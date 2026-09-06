"""STATUS_SPEC v1.2 conformance: `activity`, `activity_since`, `cycles_total`.

The point of v1.2 is that health and activity are *independent* answers
(§2.3). These tests pin the three things a reader depends on: the invariant
table between `equipment_status` and `activity`, that a span is timestamped at
the instant it changes rather than at poll time, and that a poller which sleeps
through a command can still account for it via `metrics["cycles_total"]`.
"""

import json
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from opentrons_server.gateway.models import PROTOCOL_VERSION, EquipmentStatus
from opentrons_server.gateway.service import (
    _RUN_STARTING_ACTIONS,
    OT2Service,
    OT2ServiceState,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# §2.3's invariant table, as it applies to the states this gateway can reach.
# `error` / `dry_run` / `unknown` accept any activity and are checked separately.
_INVARIANTS = {
    "busy": "running",
    "ready": "idle",
    "requires_init": "idle",
}


def _ready_service() -> OT2Service:
    service = OT2Service(dry_run=False)
    service.control = Mock()
    service.state = OT2ServiceState.READY
    return service


def test_protocol_version_is_1_2_on_both_surfaces():
    service = OT2Service(dry_run=True)

    assert PROTOCOL_VERSION == "1.2"
    assert service.get_status().protocol_version == "1.2"


@pytest.mark.parametrize("state", list(OT2ServiceState))
def test_activity_satisfies_the_invariant_table(state):
    """No reachable service state may produce a busy/idle or ready/running pair."""

    service = OT2Service(dry_run=False)
    service.state = state

    status = service.get_status()
    required = _INVARIANTS.get(status.equipment_status)

    if required is not None:
        assert status.activity == required, (
            f"{state.value} -> {status.equipment_status} + {status.activity} "
            f"violates the §2.3 invariant table"
        )


def test_unknown_outcome_reports_unknown_activity():
    """Transport died mid-command: whether the robot is still moving is
    exactly what we cannot determine, so `unknown` is the honest answer."""

    service = OT2Service(dry_run=False)
    service.state = OT2ServiceState.UNKNOWN_OUTCOME

    status = service.get_status()

    assert status.equipment_status == "unknown"
    assert status.activity == "unknown"


def test_external_run_is_running_not_merely_busy():
    service = OT2Service(dry_run=False)
    service.state = OT2ServiceState.EXTERNAL_CONTROL

    status = service.get_status()

    assert status.equipment_status == "busy"
    assert status.activity == "running"


def test_paused_is_degraded_and_idle():
    """A paused protocol is not performing its primary operation. `degraded`
    accepts either activity (§2.3), and nothing is moving."""

    service = OT2Service(dry_run=False)
    service.state = OT2ServiceState.PAUSED

    status = service.get_status()

    assert status.equipment_status == "degraded"
    assert status.activity == "idle"


def test_activity_since_marks_the_span_start_not_the_poll():
    service = OT2Service(dry_run=False)

    first = service.get_status()
    second = service.get_status()

    assert first.activity == second.activity == "idle"
    # Same unchanged span: polling must not re-stamp it.
    assert first.activity_since == second.activity_since
    assert first.activity_since is not None


def test_activity_span_restamps_on_transition():
    service = _ready_service()
    idle_since = service.get_status().activity_since

    # Windows' wall clock ticks at ~15.6 ms; sleep past a tick so "re-stamped"
    # is distinguishable from "same instant".
    time.sleep(0.05)
    service.state = OT2ServiceState.BUSY
    running = service.get_status()

    assert running.activity == "running"
    assert running.activity_since is not None
    assert running.activity_since > idle_since


def test_command_edges_stamp_the_span_exactly():
    """`_run_action` brackets the in-flight command, so the span starts when
    the command starts — not when the next poll happens to observe it."""

    service = _ready_service()
    observed = {}

    def _command():
        # Mid-command: this is what a concurrent /status poll would see.
        status = service.get_status()
        observed["equipment_status"] = status.equipment_status
        observed["activity"] = status.activity
        observed["allowed_actions"] = status.allowed_actions

    service._run_action("home", _command, idempotent=True)

    assert observed["equipment_status"] == "busy"
    assert observed["activity"] == "running"
    # §2.3: no second concurrent command may be advertised while one runs.
    assert not _RUN_STARTING_ACTIONS.intersection(observed["allowed_actions"])
    # And the span closes when the command does.
    assert service.get_status().activity == "idle"


def test_cycles_total_counts_completed_commands():
    """A protocol command is far shorter than the 60 s aggregator poll, so the
    counter — not a sampled activity series — is what makes work accountable
    (§2.3.1)."""

    service = _ready_service()

    assert service.get_status().metrics["cycles_total"].value == 0

    service._run_action("home", lambda: None, idempotent=True)
    service._run_action("home", lambda: None, idempotent=True)

    metric = service.get_status().metrics["cycles_total"]
    assert metric.value == 2
    assert metric.unit == "count"


def test_cycles_total_does_not_count_a_failed_command():
    service = _ready_service()

    def _boom():
        raise RuntimeError("robot said no")

    with pytest.raises(RuntimeError):
        service._run_action("home", _boom, idempotent=True)

    assert service.get_status().metrics["cycles_total"].value == 0


def test_dry_run_reports_its_own_idle_activity():
    """A simulated device reports reality; readers exclude it from utilization
    (Appendix B.1) rather than the device faking a state."""

    service = OT2Service(dry_run=True)

    status = service.get_status()

    assert status.equipment_status == "dry_run"
    assert status.activity == "idle"


@pytest.mark.parametrize(
    "name",
    [
        "status_requires_init.json",
        "status_dry_run.json",
        "status_lights_on.json",
        "status_deck_declared.json",
        "status_deck_occupied.json",
        "status_deck_mismatch.json",
        "status_deck_in_use.json",
        "status_robot_unreachable.json",
    ],
)
def test_fixtures_are_v1_2_shaped(name):
    payload = json.loads((_FIXTURES / name).read_text())
    status = EquipmentStatus(**payload)

    assert status.protocol_version == "1.2"
    assert status.activity_since is not None
    assert status.metrics["cycles_total"].unit == "count"

    required = _INVARIANTS.get(status.equipment_status)
    if required is not None:
        assert status.activity == required
    if status.activity == "running":
        assert not _RUN_STARTING_ACTIONS.intersection(status.allowed_actions)


def test_fixtures_cover_both_healthy_activities():
    """§9's v1.2 checklist: at least one healthy+running and one healthy+idle
    snapshot. (`degraded` + `running` is unreachable for this device — its only
    degraded state is a paused protocol, which is idle by definition.)"""

    seen = set()
    for path in _FIXTURES.glob("status_*.json"):
        status = EquipmentStatus(**json.loads(path.read_text()))
        seen.add((status.equipment_status, status.activity))

    assert ("busy", "running") in seen
    assert ("ready", "idle") in seen


def test_allowed_actions_method_matches_what_status_publishes():
    """STATUS_SPEC §6.2: one helper feeds both surfaces.

    The convenience actions (`lights.set`, `deck.declare`) were once appended
    by the /status builder alone, so `service.allowed_actions()` returned a
    narrower list than the device advertised on the wire — the endpoints
    honored them, the published list named them, and every in-process caller
    was told they were unavailable. Any gate that consults the method (the
    plan executor's pre-step re-check) would have refused a step the device
    would happily have run.
    """
    for dry_run in (True, False):
        service = OT2Service(dry_run=dry_run)
        assert sorted(service.get_status().allowed_actions) == sorted(
            service.allowed_actions()
        )
