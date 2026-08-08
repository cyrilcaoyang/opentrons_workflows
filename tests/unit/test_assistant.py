"""The optional in-page chat assistant.

Two things are worth pinning. First that it is genuinely optional — a gateway
with no key must behave exactly as it did before, because the repo's promise is
that installing it alone gives you a working device service. Second that it is
a *proposer*: its only write tool is propose_plan, so it comes through the same
human-authorization gate as an agent harness and cannot move the robot.

No network and no hardware — the OpenAI client is faked throughout.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from opentrons_server.gateway import assistant as assistant_mod
from opentrons_server.gateway.api import create_app
from opentrons_server.gateway.assistant import (
    Assistant,
    AssistantConfig,
    AssistantDisabled,
    _tool_schemas,
)
from opentrons_server.gateway.plans import PlanStore
from opentrons_server.gateway.service import OT2Service

CLAIM = {"owner": "ada@lab", "session_id": "s1", "ttl_s": 30.0}


@pytest.fixture(autouse=True)
def _isolate_env_file(tmp_path, monkeypatch):
    """Point the ``.env`` lookup at a path that does not exist.

    Without this, a real repo-root ``.env`` — which is the documented way to
    configure the assistant, so it exists on any machine where someone has
    turned it on — silently supplies a key to the tests that assert the
    assistant is *un*configured. They passed until the moment the feature was
    actually used, which is the worst time for a test to start lying.

    Tests that want a file set ``OT2_ENV_FILE`` themselves; a later setenv
    overrides this one.
    """
    monkeypatch.setenv("OT2_ENV_FILE", str(tmp_path / "absent.env"))


def _config(**over):
    base = dict(
        enabled=True,
        api_key="k-test",
        model="test/model",
        base_url="http://llm.invalid/v1",
        max_tokens=256,
        timeout_s=5.0,
    )
    base.update(over)
    return AssistantConfig(**base)


def _fake_openai(monkeypatch, responses):
    """Install a fake OpenAI client that replays `responses` in order."""
    calls = {"sent": []}

    def create(**kwargs):
        calls["sent"].append(kwargs)
        return responses.pop(0)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(
        assistant_mod, "OpenAI", lambda **_kw: client, raising=False
    )
    import openai

    monkeypatch.setattr(openai, "OpenAI", lambda **_kw: client)
    return calls


def _text(content):
    return SimpleNamespace(
        model="test/model",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
    )


def _tool_call(name, args, call_id="c1"):
    return SimpleNamespace(
        model="test/model",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id=call_id,
                            function=SimpleNamespace(name=name, arguments=json.dumps(args)),
                        )
                    ],
                )
            )
        ],
    )


# ---------------------------------------------------------------------------
# Optional by construction
# ---------------------------------------------------------------------------


def test_no_key_means_disabled_not_broken():
    """The repo's promise is that installing it alone gives a working gateway.
    An unconfigured assistant reports why and changes nothing else."""
    reason = _config(api_key=None).unavailable_reason()
    assert reason and "no API key" in reason


def test_kill_switch_wins_over_a_configured_key():
    reason = _config(enabled=False).unavailable_reason()
    assert reason and "disabled" in reason


def test_a_configured_assistant_reports_available():
    assert _config().unavailable_reason() is None


def test_chat_refuses_when_disabled():
    a = Assistant(OT2Service(dry_run=True), PlanStore(), _config(api_key=None))
    with pytest.raises(AssistantDisabled, match="no API key"):
        a.chat([{"role": "user", "content": "hello"}])


def test_health_endpoint_is_open_and_leaks_nothing(monkeypatch):
    """The UI needs this before login to decide whether to render the bubble,
    so it is unauthenticated — and therefore must expose only a boolean and a
    reason, never the key or the provider URL."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(create_app(dry_run=True, enforce_claims=True, ui=False))

    body = client.get("/assistant/health").json()

    assert body["configured"] is False
    assert "no API key" in body["reason"]
    assert body["model"] is None
    assert "k-" not in json.dumps(body)


# ---------------------------------------------------------------------------
# It is a proposer, not a driver
# ---------------------------------------------------------------------------


def test_no_tool_can_move_the_robot():
    """The load-bearing absence, mirroring the MCP surface. Authorizing and
    running are claim-gated clicks; offering them here would only produce
    refusals and tempt the model to claim it had started work."""
    names = {t["function"]["name"] for t in _tool_schemas()}
    assert names == {
        "get_status",
        "get_deck",
        "get_consumables",
        "list_actions",
        "propose_plan",
    }
    for forbidden in ("authorize_plan", "execute_plan", "abort_plan", "startup", "home"):
        assert forbidden not in names


def test_proposing_creates_a_draft_the_operator_must_approve(monkeypatch):
    store = PlanStore()
    a = Assistant(OT2Service(dry_run=True), store, _config())
    _fake_openai(
        monkeypatch,
        [
            _tool_call("propose_plan", {"steps": [{"action": "lights.set", "args": {"on": True}}]}),
            _text("Proposed one step. Authorize it in the panel to run it."),
        ],
    )

    result = a.chat([{"role": "user", "content": "turn the lights on"}])

    assert result["plan_id"]
    plan = store.get(result["plan_id"])
    assert plan.status == "draft"          # not authorized
    assert plan.authorization is None      # and not runnable
    assert plan.created_by == "assistant"  # attributable in the panel


def test_a_bad_proposal_is_returned_to_the_model_to_repair(monkeypatch):
    """A validation error is the model's problem, not the operator's. It comes
    back as a tool result so the next turn can fix the step, rather than
    surfacing a stack trace in the chat box."""
    store = PlanStore()
    a = Assistant(OT2Service(dry_run=True), store, _config())
    calls = _fake_openai(
        monkeypatch,
        [
            _tool_call("propose_plan", {"steps": [{"action": "nuke", "args": {}}]}),
            _text("That action does not exist on this robot."),
        ],
    )

    result = a.chat([{"role": "user", "content": "nuke it"}])

    assert result["plan_id"] is None
    assert store.list() == []
    tool_reply = [m for m in calls["sent"][-1]["messages"] if m.get("role") == "tool"][-1]
    assert "unknown action" in tool_reply["content"]


def test_reads_are_scoped_to_this_service(monkeypatch):
    """The tools close over one service instance — there is no device selector
    to get wrong, which is what makes 'this robot only' a property of the code."""
    service = OT2Service(dry_run=True)
    a = Assistant(service, PlanStore(), _config())
    _fake_openai(monkeypatch, [_tool_call("get_status", {}), _text("It is in dry run.")])

    result = a.chat([{"role": "user", "content": "what's the status"}])

    assert result["tools_used"] == ["get_status"]


def test_tool_rounds_are_bounded(monkeypatch):
    """A model that keeps calling tools must not spin against the robot's read
    path. It gets a truthful 'I didn't finish' rather than an invented summary."""
    a = Assistant(OT2Service(dry_run=True), PlanStore(), _config())
    _fake_openai(monkeypatch, [_tool_call("get_status", {}, f"c{i}") for i in range(10)])

    result = a.chat([{"role": "user", "content": "loop"}])

    assert "wasn't able to finish" in result["reply"]
    assert len(result["tools_used"]) == Assistant.MAX_TOOL_ROUNDS


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


def test_chat_requires_the_claim(monkeypatch):
    """Not because the assistant can move anything — it cannot — but a proposal
    is only useful to whoever holds the device, and this stops a passer-by
    spending the lab's API budget on a robot they do not control."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "k-test")
    client = TestClient(create_app(dry_run=True, enforce_claims=True, ui=False))

    resp = client.post("/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 423

    client.post("/control/claim", json=CLAIM)
    _fake_openai(monkeypatch, [_text("Hello.")])
    ok = client.post("/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert ok.status_code == 200
    assert ok.json()["reply"] == "Hello."


@pytest.mark.parametrize("enforce_claims", [False, True])
def test_chat_is_503_when_unconfigured(monkeypatch, enforce_claims):
    """Availability is answered before the claim gate.

    With claims on, the gate used to shadow this: an unconfigured gateway
    replied "take control of the device", advice that leads nowhere because
    taking the claim would not conjure an assistant. The caller gets the reason
    they actually hit.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(create_app(dry_run=True, enforce_claims=enforce_claims, ui=False))

    resp = client.post("/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})

    assert resp.status_code == 503
    assert "API key" in resp.json()["detail"]


def test_message_history_is_bounded():
    client = TestClient(create_app(dry_run=True, enforce_claims=False, ui=False))
    too_many = {"messages": [{"role": "user", "content": "x"} for _ in range(41)]}
    assert client.post("/assistant/chat", json=too_many).status_code == 422
    assert client.post("/assistant/chat", json={"messages": []}).status_code == 422


# ---------------------------------------------------------------------------
# .env fallback
#
# It exists so switching the assistant on needs no `nssm set
# AppEnvironmentExtra`, which replaces the ENTIRE variable block — get that
# wrong and you wipe the three state paths that stop two robots from
# overwriting each other's plate and tip records.
# ---------------------------------------------------------------------------


def _write_env(tmp_path, body: str):
    p = tmp_path / ".env"
    p.write_text(body, encoding="utf-8")
    return p


def test_missing_env_file_is_a_no_op(tmp_path):
    assert assistant_mod.load_env_file(tmp_path / "nope.env") == {}


def test_reads_an_allowlisted_key(tmp_path):
    env = _write_env(tmp_path, "OPENROUTER_API_KEY=k-from-file\n")
    assert assistant_mod.load_env_file(env) == {"OPENROUTER_API_KEY": "k-from-file"}


def test_ignores_comments_blanks_export_and_quotes(tmp_path):
    env = _write_env(
        tmp_path,
        "\n# a comment\n\nexport OPENROUTER_API_KEY=\"k-quoted\"\nnot-a-pair\n",
    )
    assert assistant_mod.load_env_file(env) == {"OPENROUTER_API_KEY": "k-quoted"}


def test_non_allowlisted_keys_are_refused(tmp_path):
    """The load-bearing restriction. This file sits at the repo root, which BOTH
    gateway instances share, so it must not be able to hand two robots the same
    host alias or the same state path — shared state files corrupt each other.
    Anything instance-specific stays in the NSSM env, which a file cannot reach.
    """
    env = _write_env(
        tmp_path,
        "OT2_HOST_ALIAS=wrong-robot\n"
        "OT2_TIP_STATE_PATH=/shared/tips.json\n"
        "OT2_EQUIPMENT_ID=ot2\n"
        "OPENROUTER_API_KEY=k-ok\n",
    )
    assert assistant_mod.load_env_file(env) == {"OPENROUTER_API_KEY": "k-ok"}


def test_environment_beats_the_file(tmp_path, monkeypatch):
    _write_env(tmp_path, "OPENROUTER_API_KEY=k-file\nOT2_ASSISTANT_MODEL=file/model\n")
    monkeypatch.setenv("OT2_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k-env")
    monkeypatch.delenv("OT2_ASSISTANT_MODEL", raising=False)

    cfg = AssistantConfig.from_env()

    assert cfg.api_key == "k-env"          # environment wins
    assert cfg.key_source == "environment"
    assert cfg.model == "file/model"       # ...per setting, not all-or-nothing


def test_the_file_alone_configures_the_assistant(tmp_path, monkeypatch):
    _write_env(tmp_path, "OPENROUTER_API_KEY=k-file\n")
    monkeypatch.setenv("OT2_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    cfg = AssistantConfig.from_env()

    assert cfg.unavailable_reason() is None
    assert cfg.key_source == "file"


def test_health_reports_where_the_key_came_from_but_never_the_key(tmp_path, monkeypatch):
    """The only question an operator asks when the bubble stays hidden after
    they dropped a key somewhere: did it see it?"""
    _write_env(tmp_path, "OPENROUTER_API_KEY=k-secret-value\n")
    monkeypatch.setenv("OT2_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(create_app(dry_run=True, enforce_claims=True, ui=False))

    body = client.get("/assistant/health").json()

    assert body["configured"] is True
    assert body["key_source"] == "file"
    assert "k-secret-value" not in json.dumps(body)


def test_a_malformed_file_does_not_take_the_gateway_down(tmp_path, monkeypatch):
    _write_env(tmp_path, "\x00\x01 garbage ===== \nOPENROUTER_API_KEY\n")
    monkeypatch.setenv("OT2_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(create_app(dry_run=True, enforce_claims=True, ui=False))

    assert client.get("/status").status_code == 200
    assert client.get("/assistant/health").json()["configured"] is False


def test_searched_paths_are_deduped(monkeypatch):
    """Run from a checkout — how the NSSM services run — and both candidates
    are the same file. /assistant/health publishes this list, and one path
    listed twice reads like a bug to whoever is hunting for where the key goes."""
    monkeypatch.delenv("OT2_ENV_FILE", raising=False)
    paths = assistant_mod.env_file_candidates()
    assert len(paths) == len(set(paths))


def test_explicit_env_file_is_the_only_candidate(monkeypatch, tmp_path):
    monkeypatch.setenv("OT2_ENV_FILE", str(tmp_path / "custom.env"))
    assert assistant_mod.env_file_candidates() == [tmp_path / "custom.env"]
