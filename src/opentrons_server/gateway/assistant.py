"""Optional in-page chat assistant for one OT-2.

The gateway is self-contained by design — it ships its own UI inside the wheel,
its own claim protocol, its own identity gate, and runs with no dashboard, no
central server and no agent harness. This module keeps that true for the chat
box: install the package alone and you get one, without installing anything
else.

**Optional, and off unless configured.** With no API key the assistant reports
itself disabled, the UI hides the bubble, and every other surface behaves
exactly as before. The ``openai`` import is deliberately soft for the same
reason: a venv without it still serves a healthy gateway.

**It cannot move the robot.** Its entire tool surface is reads plus
``propose_plan`` — the same door an agent harness comes through
(``tools/ot2_agent_mcp.py``). A proposal is a draft; approving and running it
are claim-gated clicks in the operator panel (``gateway/plans.py``). So adding
a chat box widens the safety surface by nothing at all: it is one more
proposer behind the same gate, not a new path to the hardware.

**Scoped to this robot by construction.** The tools close over *this* service
instance. There is no shell, no other device, no scheduler — the constraint is
a property of the code rather than of a prompt, which is why this is a small
purpose-built endpoint and not a general agent embedded in a device page.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .plans import PLAN_ACTIONS, PlanStep, PlanStore, StepValidationError

logger = logging.getLogger(__name__)

# OpenRouter's OpenAI-compatible endpoint. Overridable so the same code can
# point at OpenAI proper, a local vLLM, or any other compatible server —
# nothing here depends on the provider.
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# A tool-calling model by default. The assistant's whole job is choosing a verb
# from a fixed catalog and filling in a schema, so reasoning depth matters far
# less than reliable structured output. Note that Nous Hermes models on
# OpenRouter do NOT advertise tool support — pointing this at one silently
# degrades to text-only replies with no proposals.
DEFAULT_MODEL = "z-ai/glm-5.2"

DEFAULT_MAX_TOKENS = 1024
DEFAULT_TIMEOUT_S = 60.0

_SYSTEM_PROMPT = """\
You are the operator assistant for a single Opentrons OT-2 liquid handler, \
reached through its gateway. You help with simple, single-robot operations on \
THIS robot only.

What you can do:
- Read the robot's state: status, deck layout, tip racks, loaded plate.
- Propose an ordered plan of control actions for the operator to review.

What you cannot do, and must never imply otherwise:
- You cannot run anything. `propose_plan` creates a DRAFT. A human then \
reviews it in the operator panel, approves it, and runs it. Never say you \
have started, run, or completed an operation — say you have proposed it and \
that it is waiting for their approval.
- You cannot connect or disconnect the robot, pause or resume a run, or \
reconcile an unknown outcome. Those are operator-only actions; if one is \
needed, say so and let them do it.
- You do not decide chemistry. Volumes, reagents, well maps and protocol \
design come from the operator or their project's protocol. If asked to choose \
one, decline and ask what they want.

How to work:
1. Read the state first. A plan built without looking at the deck is a guess.
2. Check consumables before proposing pipetting — a rack with no fresh tips or \
an unloaded plate will fail at the first step.
3. Propose the smallest plan that does what was asked. Explain each step in one \
short line.
4. If a request is ambiguous, out of scope, or unsafe, say so plainly instead \
of proposing something approximate.
"""


class AssistantDisabled(Exception):
    """The assistant cannot run. ``reason`` is safe to show an operator."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Settings a `.env` file may supply. An allowlist, not a general loader, and
# the reason is specific to this deployment: the file lives at the repo root,
# which BOTH gateway instances share. A general loader would let it provide an
# instance-specific value — a host alias, one of the three state paths — to two
# robots at once, and shared state files corrupt each other. Everything that
# distinguishes one robot from the other stays in its NSSM service env, which
# a file can never reach.
ENV_FILE_KEYS = frozenset(
    {
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "OT2_ASSISTANT_ENABLED",
        "OT2_ASSISTANT_MODEL",
        "OT2_ASSISTANT_BASE_URL",
        "OT2_ASSISTANT_MAX_TOKENS",
        "OT2_ASSISTANT_TIMEOUT_S",
    }
)


def env_file_candidates() -> List[Path]:
    """Where a ``.env`` is looked for, in order.

    ``OT2_ENV_FILE`` wins when set. Otherwise the working directory, which is
    the repo root under NSSM (``AppDirectory``) and when running from a
    checkout — then a package-relative guess so an editable install started
    from elsewhere still finds it.
    """
    explicit = os.environ.get("OT2_ENV_FILE")
    if explicit:
        return [Path(explicit)]
    # Deduped: run from a checkout — which is how the NSSM services run, with
    # AppDirectory set to the repo — and both candidates are the same file.
    # /assistant/health publishes this list, and showing one path twice reads
    # like a bug to whoever is trying to work out where to put their key.
    seen: Dict[Path, None] = {}
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env"):
        seen.setdefault(candidate.resolve() if candidate.parent.exists() else candidate, None)
    return list(seen)


def load_env_file(path: Optional[Path] = None) -> Dict[str, str]:
    """Allowlisted settings from a ``.env``, or ``{}`` if there is none.

    Deliberately does **not** mutate ``os.environ``: the caller uses this as a
    *fallback*, so a real environment variable always wins and the precedence
    is visible at the point it matters rather than depending on import order.

    Not cached, on purpose — it is read per request, so dropping a key into the
    file takes effect on the next poll with no restart. That is the whole point
    of having it: `nssm set AppEnvironmentExtra` replaces the entire variable
    block, and getting that wrong wipes the state paths that keep two robots
    from overwriting each other's plate and tip records.

    Malformed lines are skipped rather than raising. A typo in an optional
    config file must not take the gateway down.
    """
    paths = [path] if path is not None else env_file_candidates()
    for candidate in paths:
        try:
            if not candidate.is_file():
                continue
            found: Dict[str, str] = {}
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].lstrip()
                key, sep, value = line.partition("=")
                if not sep:
                    continue
                key = key.strip()
                if key not in ENV_FILE_KEYS:
                    continue
                value = value.strip().strip('"').strip("'")
                if value:
                    found[key] = value
            return found
        except OSError as exc:  # unreadable file is not a reason to fail
            logger.warning("could not read %s: %s", candidate, exc)
    return {}


@dataclass(frozen=True)
class AssistantConfig:
    enabled: bool
    api_key: Optional[str]
    model: str
    base_url: str
    max_tokens: int
    timeout_s: float

    # Where the key came from, for /assistant/health. Answers the only question
    # an operator asks when the bubble stays hidden: "did it see my key?"
    key_source: Optional[str] = None

    @classmethod
    def from_env(cls) -> "AssistantConfig":
        from_file = load_env_file()

        def setting(key: str, default: Optional[str] = None) -> Optional[str]:
            """Environment first, then the file, then the default."""
            value = os.environ.get(key)
            if value:
                return value
            return from_file.get(key, default)

        key = setting("OPENROUTER_API_KEY") or setting("OPENAI_API_KEY")
        source: Optional[str] = None
        if key:
            source = (
                "environment"
                if (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"))
                else "file"
            )
        return cls(
            enabled=(setting("OT2_ASSISTANT_ENABLED", "true") or "true").lower()
            not in {"0", "false", "no", "off"},
            api_key=key,
            model=setting("OT2_ASSISTANT_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
            base_url=setting("OT2_ASSISTANT_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL,
            max_tokens=int(setting("OT2_ASSISTANT_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)) or 0)
            or DEFAULT_MAX_TOKENS,
            timeout_s=float(setting("OT2_ASSISTANT_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)) or 0)
            or DEFAULT_TIMEOUT_S,
            key_source=source,
        )

    def unavailable_reason(self) -> Optional[str]:
        """Why the assistant cannot run, or None when it can."""
        if not self.enabled:
            return "assistant disabled (OT2_ASSISTANT_ENABLED=0)"
        if not self.api_key:
            return "no API key configured (set OPENROUTER_API_KEY)"
        try:
            import openai  # noqa: F401
        except ImportError:
            return "the 'openai' package is not installed in this environment"
        return None


def _tool_schemas() -> List[Dict[str, Any]]:
    """The assistant's entire capability, as OpenAI-style tool definitions.

    Reads plus ``propose_plan``. There is deliberately no approve, execute or
    abort tool — those are claim-gated and the assistant holds no claim, so
    offering them would only produce refusals and tempt the model to report
    work it cannot start.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "get_status",
                "description": (
                    "Health, activity, components, and which actions the robot "
                    "will currently accept. Start here."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_deck",
                "description": "The normalized 12-slot deck: labware per slot and its provenance.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_consumables",
                "description": (
                    "Tip racks with per-tip state, the loaded plate with per-well "
                    "samples, mounted tips, and pipette channel counts."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_actions",
                "description": (
                    "The catalog a plan step may use, with each action's argument "
                    "schema. Read before proposing."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "propose_plan",
                "description": (
                    "Propose an ordered plan for the operator to review. Creates a "
                    "DRAFT — it does not run. The operator approves and runs it "
                    "in the panel."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "description": "Ordered steps drawn from list_actions.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "string"},
                                    "args": {"type": "object"},
                                },
                                "required": ["action"],
                            },
                        }
                    },
                    "required": ["steps"],
                },
            },
        },
    ]


class Assistant:
    """One conversation turn's worth of tool-calling over a single OT-2."""

    # Bounded so a confused model cannot spin against the robot's read path or
    # burn tokens indefinitely. Four is enough for read -> read -> propose with
    # one repair attempt after a validation error.
    MAX_TOOL_ROUNDS = 4

    def __init__(self, service: Any, plans: PlanStore, config: AssistantConfig) -> None:
        self._service = service
        self._plans = plans
        self._config = config

    # -- tools -------------------------------------------------------------

    def _tools(self) -> Dict[str, Callable[[Dict[str, Any]], Any]]:
        return {
            "get_status": lambda _a: self._service.get_status().model_dump(mode="json"),
            "get_deck": lambda _a: self._service.get_status()
            .details.get("snapshot", {})
            .get("deck", {}),
            "get_consumables": lambda _a: self._consumables(),
            "list_actions": lambda _a: self._actions(),
            "propose_plan": self._propose,
        }

    def _consumables(self) -> Dict[str, Any]:
        details = self._service.get_status().details
        return {
            "tip_racks": details.get("tip_racks"),
            "loaded_plate": details.get("loaded_plate"),
            "mounted_tips": details.get("mounted_tips"),
            "pipette_channels": details.get("pipette_channels"),
        }

    @staticmethod
    def _actions() -> Dict[str, Any]:
        return {
            "actions": [
                {
                    "action": name,
                    "idempotent": spec.idempotent,
                    "args_schema": spec.model.model_json_schema() if spec.model else None,
                }
                for name, spec in sorted(PLAN_ACTIONS.items())
            ]
        }

    def _propose(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Create a draft. Validation errors are returned to the model, not
        raised — its next turn can repair the step instead of the operator
        seeing a stack trace."""
        try:
            steps = [PlanStep(**s) for s in args.get("steps", [])]
            plan = self._plans.create(steps, created_by="assistant")
        except (StepValidationError, TypeError, ValueError) as exc:
            return {"error": str(exc), "hint": "fix the step and call propose_plan again"}
        return {
            "plan_id": plan.plan_id,
            "status": plan.status,
            "steps": [{"action": s.action, "args": s.args} for s in plan.steps],
            "note": "Draft created. The operator must approve and run it in the panel.",
        }

    # -- the turn ----------------------------------------------------------

    def chat(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        reason = self._config.unavailable_reason()
        if reason:
            raise AssistantDisabled(reason)

        from openai import OpenAI

        client = OpenAI(
            base_url=self._config.base_url,
            api_key=self._config.api_key,
            timeout=self._config.timeout_s,
        )
        tools = self._tools()
        convo: List[Dict[str, Any]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
        convo += [{"role": m["role"], "content": m["content"]} for m in messages]

        used: List[str] = []
        plan_id: Optional[str] = None

        for _ in range(self.MAX_TOOL_ROUNDS):
            response = client.chat.completions.create(
                model=self._config.model,
                messages=convo,
                tools=_tool_schemas(),
                max_tokens=self._config.max_tokens,
            )
            choice = response.choices[0].message
            calls = getattr(choice, "tool_calls", None) or []
            if not calls:
                return {
                    "reply": choice.content or "",
                    "tools_used": used,
                    "plan_id": plan_id,
                    "model": response.model,
                }

            convo.append(
                {
                    "role": "assistant",
                    "content": choice.content or "",
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.function.name,
                                "arguments": c.function.arguments,
                            },
                        }
                        for c in calls
                    ],
                }
            )
            for call in calls:
                name = call.function.name
                used.append(name)
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                fn = tools.get(name)
                if fn is None:
                    result: Any = {"error": f"unknown tool {name!r}"}
                else:
                    try:
                        result = fn(args)
                    except Exception as exc:  # surfaced to the model, not the operator
                        logger.warning("assistant tool %s failed: %s", name, exc)
                        result = {"error": str(exc)}
                if name == "propose_plan" and isinstance(result, dict):
                    plan_id = result.get("plan_id") or plan_id
                convo.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, default=str),
                    }
                )

        # Out of rounds with the model still calling tools. Say so rather than
        # inventing a summary of work whose outcome we did not see.
        return {
            "reply": (
                "I wasn't able to finish that within my tool-call budget. "
                "Try asking for one smaller step."
            ),
            "tools_used": used,
            "plan_id": plan_id,
            "model": self._config.model,
        }


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=8000)


class AssistantChatRequest(BaseModel):
    """One turn plus the conversation so far.

    History is re-sent by the client rather than held server-side: the gateway
    already keeps per-process state it must reason about (claims, plans, tip
    tracking), and a chat transcript is the one thing here that nothing else
    depends on. Bounded so a long session cannot grow a request without limit.
    """

    messages: List[AssistantMessage] = Field(..., min_length=1, max_length=40)
