"""The control-plane identity gate (``OT2_REQUIRE_LOGIN``).

Claims are cooperative coordination, not authentication (STATUS_SPEC §5) — the
owner is a string the caller invents. Without this gate anyone who can reach
the port takes a claim as anybody and drives the hardware; with it, a claim
requires a verified principal and ``details.claimed_by.owner`` becomes
trustworthy even on a direct call.

Deliberately no external auth service: an edge-injected header (any reverse
proxy can produce it) or a static API key. That is what makes the gate usable
by someone who deploys this gateway outside our lab.
"""

from fastapi.testclient import TestClient

from opentrons_server.gateway.api import _parse_api_keys, create_app

SECRET = "edge-secret"
CLAIM = {"owner": "whoever-i-say", "session_id": "s1", "ttl_s": 30}


def _client(**kwargs):
    return TestClient(create_app(dry_run=True, **kwargs))


# ---------------------------------------------------------------------------
# Default posture: unchanged
# ---------------------------------------------------------------------------


def test_off_by_default_so_existing_deployments_are_unchanged():
    client = _client()
    resp = client.post("/control/claim", json=CLAIM)
    assert resp.status_code == 200
    # The caller's own owner string stands when nothing verified it.
    assert client.get("/status").json()["details"]["claimed_by"]["owner"] == "whoever-i-say"
    assert client.get("/status").json()["details"]["control_auth"] == "claim_only"


def test_status_publishes_the_posture():
    # An operator can see which gate a gateway runs without reading its env.
    assert _client().get("/status").json()["details"]["control_auth"] == "claim_only"
    assert (
        _client(enforce_claims=False).get("/status").json()["details"]["control_auth"] == "open"
    )
    gated = _client(require_login=True, edge_secret=SECRET)
    assert gated.get("/status").json()["details"]["control_auth"] == "identity"


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


def test_claim_without_a_credential_is_401():
    client = _client(require_login=True, edge_secret=SECRET)

    resp = client.post("/control/claim", json=CLAIM)

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "login_required"
    # Nothing was granted. (details.claimed_by is omitted, not null, when no
    # claim is held — STATUS_SPEC §5 allows either.)
    assert client.get("/status").json()["details"].get("claimed_by") is None


def test_edge_identity_is_accepted_and_overrides_the_claimed_owner():
    client = _client(require_login=True, edge_secret=SECRET)

    resp = client.post(
        "/control/claim",
        json=CLAIM,
        headers={"X-Edge-Key": SECRET, "X-Auth-User": "ada@lab"},
    )

    assert resp.status_code == 200
    # The verified principal wins over the body — this is what makes the audit
    # trail (and details.claimed_by) mean something.
    assert client.get("/status").json()["details"]["claimed_by"]["owner"] == "ada@lab"


def test_an_identity_header_without_the_edge_secret_is_worthless():
    # The device is directly reachable, so X-Auth-User alone must never be
    # trusted — otherwise anyone forges any identity with one header.
    client = _client(require_login=True, edge_secret=SECRET)

    for headers in (
        {"X-Auth-User": "mallory@lab"},
        {"X-Edge-Key": "wrong", "X-Auth-User": "mallory@lab"},
    ):
        assert client.post("/control/claim", json=CLAIM, headers=headers).status_code == 401


def test_the_dashboard_passthrough_header_name_is_accepted():
    """The passthrough sends X-Edge-Auth, not X-Edge-Key.

    It reaches devices on their tailnet base_url rather than through Caddy
    (api/app/control.py::_device_auth_headers, matching the xArm's spelling),
    so without this alias a login-gated gateway would refuse the dashboard
    while the framed panel — which does go through the edge — kept working.
    """
    client = _client(require_login=True, edge_secret=SECRET)

    resp = client.post(
        "/control/claim",
        json=CLAIM,
        headers={"X-Edge-Auth": SECRET, "X-Auth-User": "ada@lab"},
    )

    assert resp.status_code == 200
    assert client.get("/status").json()["details"]["claimed_by"]["owner"] == "ada@lab"
    # ... and a wrong secret under that name is still worthless.
    assert (
        client.post(
            "/control/claim",
            json={"owner": "x", "session_id": "s9", "ttl_s": 30},
            headers={"X-Edge-Auth": "wrong", "X-Auth-User": "mallory@lab"},
        ).status_code
        == 401
    )


def test_api_key_identifies_a_machine_principal_by_name():
    client = _client(require_login=True, api_keys={"solubility-workflow": "k-123"})

    resp = client.post("/control/claim", json=CLAIM, headers={"X-Api-Key": "k-123"})

    assert resp.status_code == 200
    # Named, and never the key itself.
    owner = client.get("/status").json()["details"]["claimed_by"]["owner"]
    assert owner == "api:solubility-workflow"
    assert "k-123" not in owner


def test_a_wrong_api_key_is_refused():
    client = _client(require_login=True, api_keys={"wf": "k-123"})
    assert client.post("/control/claim", json=CLAIM, headers={"X-Api-Key": "nope"}).status_code == 401


def test_identity_still_labels_the_owner_when_the_gate_is_off():
    # Attribution is useful even when it isn't mandatory.
    client = _client(edge_secret=SECRET)  # require_login not set

    client.post(
        "/control/claim", json=CLAIM, headers={"X-Edge-Key": SECRET, "X-Auth-User": "ada@lab"}
    )

    assert client.get("/status").json()["details"]["claimed_by"]["owner"] == "ada@lab"


def test_login_is_not_bypassed_when_claims_are_disabled():
    # The claim gate normally carries the identity requirement (a token can
    # only be had by an authenticated caller). With claims off it must be
    # enforced directly on the control endpoints, or the flag does nothing.
    client = _client(require_login=True, enforce_claims=False, edge_secret=SECRET)

    assert client.post("/control/home", json={}).status_code == 401
    ok = client.post("/control/home", json={}, headers={"X-Edge-Key": SECRET, "X-Auth-User": "ada@lab"})
    assert ok.status_code == 200


def test_read_surfaces_stay_open_for_the_aggregator():
    # The gate is for the control plane. /status must keep answering or the
    # dashboard marks the device unreachable.
    client = _client(require_login=True, edge_secret=SECRET)
    for path in ("/", "/health", "/status"):
        assert client.get(path).status_code == 200


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_requiring_login_with_no_way_in_is_refused_at_startup():
    # Fail-closed with no credential configured is a bricked device, not a
    # secure one — so say so at boot rather than at 2am.
    import pytest

    with pytest.raises(RuntimeError, match="OT2_REQUIRE_LOGIN"):
        create_app(dry_run=True, require_login=True, ui=False)


def test_api_key_parsing():
    assert _parse_api_keys("wf:k1,agent:k2") == {"wf": "k1", "agent": "k2"}
    assert _parse_api_keys("  wf : k1 ") == {"wf": "k1"}
    assert _parse_api_keys("bare-key") == {"unnamed": "bare-key"}
    assert _parse_api_keys("") == {}
    assert _parse_api_keys(None) == {}


# ---------------------------------------------------------------------------
# Propose-only principals (OT2_PROPOSER_KEYS)
#
# The approval gate rests entirely on the claim: approving and running a plan
# require nothing but a valid claim token. So a credential that can claim can
# approve its own proposal, and the human review is decorative. Agents get a
# credential that cannot claim.
# ---------------------------------------------------------------------------


def _proposer_client():
    return TestClient(
        create_app(
            dry_run=True,
            enforce_claims=True,
            ui=False,
            require_login=True,
            api_keys={"workflow": "k-full"},
            proposer_keys={"agent": "k-propose"},
        )
    )


def test_a_propose_only_key_cannot_claim():
    """The load-bearing refusal. Without it, handing an agent a key so it can
    draft also hands it approve-and-run."""
    resp = _proposer_client().post(
        "/control/claim",
        json={"owner": "agent", "session_id": "s1", "ttl_s": 30.0},
        headers={"X-Api-Key": "k-propose"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "propose_only_principal"


def test_a_propose_only_key_can_still_draft_a_plan():
    """It has to remain useful — drafting is the whole point of the credential."""
    resp = _proposer_client().post(
        "/plans",
        json={"steps": [{"action": "lights.set", "args": {"on": True}}]},
        headers={"X-Api-Key": "k-propose"},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "draft"


def test_a_full_api_key_may_still_claim():
    """Workflow drivers (lab-skills, execute_plan) legitimately hold a claim;
    this restriction must not break the SDK path."""
    resp = _proposer_client().post(
        "/control/claim",
        json={"owner": "workflow", "session_id": "s1", "ttl_s": 30.0},
        headers={"X-Api-Key": "k-full"},
    )
    assert resp.status_code == 200
    assert resp.json()["claim_token"]


def test_a_propose_only_key_cannot_reach_approve_or_execute():
    """Belt and braces: even the routes themselves refuse it, because it can
    never obtain the token they require."""
    client = _proposer_client()
    plan = client.post(
        "/plans",
        json={"steps": [{"action": "lights.set", "args": {"on": True}}]},
        headers={"X-Api-Key": "k-propose"},
    ).json()
    for path in ("approve", "execute", "abort"):
        body = {"step_hash": plan["step_hash"]} if path == "approve" else None
        resp = client.post(
            f"/plans/{plan['plan_id']}/{path}", json=body, headers={"X-Api-Key": "k-propose"}
        )
        assert resp.status_code == 423, f"{path} -> {resp.status_code}"


def test_full_and_propose_only_principals_are_named_distinctly_in_audit():
    """`details.claimed_by.owner` must name the principal, so a later audit can
    tell a workflow's action from a person's."""
    client = _proposer_client()
    client.post(
        "/control/claim",
        json={"owner": "ignored", "session_id": "s1", "ttl_s": 30.0},
        headers={"X-Api-Key": "k-full"},
    )
    assert client.get("/status").json()["details"]["claimed_by"]["owner"] == "api:workflow"
