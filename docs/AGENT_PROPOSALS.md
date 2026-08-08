# Agent-proposed plans

An agent may **propose** work on an OT-2. It may not run it. A human reviews
the proposal in the gateway UI, approves it, and runs it. This document
covers the shape of that boundary and how to wire an agent harness to it.

Implementation: `gateway/plans.py` (the gate), `tools/ot2_agent_mcp.py` (the
agent-facing MCP surface), `ui/src/components/PlanReviewPanel.tsx` (the human
half).

## The boundary

| Operation | Route | Agent | Operator (holds the claim) |
|---|---|---|---|
| propose a plan | `POST /plans` | ✅ | ✅ |
| revise its steps | `PUT /plans/{id}/steps` | ✅ | ✅ |
| read plans / catalog | `GET /plans`, `GET /plans/actions` | ✅ | ✅ |
| **approve** | `POST /plans/{id}/approve` | ❌ 423 | ✅ |
| **run** | `POST /plans/{id}/execute` | ❌ 423 | ✅ |
| discard | `POST /plans/{id}/abort` | ❌ 423 | ✅ |

Approve, run and abort require `X-Claim-Token`. The token is minted by
`POST /control/claim` and lives in the operator's browser; it is never given to
an agent. So every path that moves the robot starts with a human clicking.

This matters more than it would for a purpose-built chat panel, because a
general agent harness has a shell, cron, subagents, and a chat interface
reachable from a phone. It can draft work at 3am. It cannot start it.

## Why the gate holds

Three properties, each with a test named after it in `tests/unit/test_plans.py`:

1. **The step list is hashed and the human approves the hash.** Revising a
   plan resets it to `draft` and discards any approval — it is a different
   plan that has never been reviewed. An agent cannot get a harmless plan
   approved and then swap the steps.
2. **Approval is bound to a claim session, not just a person.** The claim only
   stays alive while the operator's page heartbeats, so "a human approved
   this" decays into "a human is still here". A different tab is a different
   session and must re-review. Approvals also expire after 10 minutes and are
   spent on use.
3. **`allowed_actions` is re-checked live before every step.** A plan approved
   against a ready robot cannot fire into one that has since faulted, paused,
   or been seized by an external run. The first refusal halts the plan and
   marks the rest `skipped` — never continue-past-error, because later steps of
   a pipetting sequence assume the earlier ones happened.

Plans live in memory and die with the process, deliberately: a claim does not
survive a restart, so an approval must not either.

### Not a "run authorization"

That term is taken. `ac-organic-lab/docs/AGENTIC_ELN_DESIGN.md` §12 defines a
**Run Authorization** as a campaign-level gate: it pins a merged commit SHA, a
protocol schema version and a compiled-package digest, revalidates inventory and
device readiness, and lands an immutable `authorization_id` in AnaliticaDB.

What this module produces is a **step approval** — one operator, holding the
claim, agreeing to one ad-hoc step list on one robot for the next ten minutes.
It covers the work a run authorization does not: bring-up, homing, a manual tip
pickup, turning the lights off. The two compose; calling both "authorization"
would conflate a durable scientific record with an ephemeral operator gesture.

### Audit

Approvals are ephemeral, but the *fact* of them is not. Two events go to the
history DB via `OT2_INGEST_URL`:

| event | when | payload |
|---|---|---|
| `plan_approved` | a human approves | `plan_id`, `step_hash`, `owner`, `session_id`, `proposed_by`, `steps`, `non_idempotent`, `expires_at` |
| `plan_executed` | the run finishes or halts | `plan_id`, `step_hash`, `status`, `owner`, `outcomes`, `halt_reason` |

The per-step `control_action` rows already say what ran; these say **who agreed
to it, and to what**. Without them that fact died with the process — recoverable
only by inference from the step rows.

Note `plan_executed` carries the *approving* owner, read before execution: the
approval is spent by `execute()`, so a naive read afterwards would leave the
completion record with no one's name on it.

## What can be planned

`GET /plans/actions` returns the catalog with each action's JSON schema — the
authoritative list. Fourteen actions: `home`, `setup`, `move_to`,
`pick_up_tip`, `aspirate`, `dispense`, `drop_tip`, `move_labware`,
`plate.load`, `plate.unload`, `well.update`, `tips.reset`, `lights.set`,
`deck.declare`.

Five control actions are deliberately **not** plannable:

| Excluded | Why |
|---|---|
| `startup` | carries an SSH credential; a bring-up step, not lab work |
| `shutdown` | lifecycle — ending a session is an operator decision |
| `pause` / `resume` | control flow over a running plan; must stay immediate, never queued behind other steps |
| `reconcile` | clears `unknown_outcome` — the state meaning "nobody knows whether that aspirate happened". A plan that could clear its own ambiguity would erase the signal a human is meant to adjudicate. |

Step arguments are validated at proposal time against the same request models
the matching `/control/*` endpoint uses, so a malformed step is refused with a
422 naming the field while it is still text.

## Wiring up Hermes

`tools/ot2_agent_mcp.py` is a **standalone** stdio MCP server. It imports only
`mcp` and `requests` — never `opentrons_server` — and talks to the gateway over
REST, so it runs in a throwaway environment and the gateway's service venv is
never touched. That is not incidental: two live robots share one checkout and
one `.venv` on `sdl2-pc-03-cytation`, and installing `mcp` there would require
stopping both services (it pulls ~10 packages including pywin32).

It exposes eight tools — four reads, `propose_plan`, `revise_plan`, and two
plan reads. There is no approve/execute/abort tool, and
`tests/unit/test_agent_mcp.py` fails if one appears.

Register the Complexation robot:

```powershell
hermes mcp add ot2-complexation `
  --command uv `
  --args "run --with mcp --with requests C:\Users\sdl2\Projects\opentrons-server\tools\ot2_agent_mcp.py --base-url http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8021"
```

**Use the gateway's tailnet address, not the Caddy edge.** The edge
(`http://100.64.254.6/ot2/complexation/…`) runs `forward_auth` and returns
**401** for these paths, so every tool would fail. The gateway port itself
answers unauthenticated on the tailnet — the documented v1 posture, where
Tailscale ACLs are the gate (STATUS_SPEC §11). Complexation is `:8021`, HTE is
`:8020`.

The server probes `GET /` at startup and exits non-zero if the gateway is
unreachable — a silent, dead MCP server looks to a model like a robot with no
state, which is the wrong impression to give it.

> **Register Complexation only.** Hands-on OT-2 work goes through
> `http://100.64.254.6/ot2/complexation/ui/`. The HTE robot (`:8020`) runs real
> campaigns and is off limits — see `AGENT_RULES.md` rule 4a.

If the gateway runs with `OT2_REQUIRE_LOGIN`, pass `--api-key` (or
`OT2_MCP_API_KEY`). It authenticates the proposer; it grants no ability to move
the robot.

## The operator's side

The **Proposed plans** panel appears at the top of the gateway UI whenever a
plan is open, and is hidden otherwise. Each plan shows its steps with
arguments, the hash being approved, and a ⚠ on any non-idempotent step
(`aspirate`, `dispense`, tip actions) — those cannot be safely repeated if the
link drops mid-step.

Take control, then **Approve these N steps**, then **Run**. Two clicks, not
one: approving records *what was reviewed*, which is what makes a later audit
meaningful, and it keeps the last action before a pipette moves from being a
single click on a screen nobody read.

If the plan changed between the panel rendering it and the click, approving
returns 409 and the panel says to re-read it. That refusal is the feature.
