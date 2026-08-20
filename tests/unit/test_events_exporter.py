"""The device-side history exporter: wire format, failure isolation, and the
service hooks that produce control-action and tip-lifecycle rows.

These rows exist because the aggregator's 60 s poll cannot see them — a
command that starts and finishes between two polls is invisible to it, and a
write made in the gateway's own UI never passes through the dashboard's
audited passthrough at all.
"""

from unittest.mock import Mock

import pytest

from opentrons_server.gateway.deck import DeckDeclarationStore
from opentrons_server.gateway.events_exporter import EventsExporter
from opentrons_server.gateway.models import TipRequest
from opentrons_server.gateway.plate_state import PlateStateStore
from opentrons_server.gateway.service import OT2Service, OT2ServiceState
from opentrons_server.gateway.tip_state import TipStateStore


class RecordingExporter(EventsExporter):
    """Synchronous stand-in: no queue, no thread, no HTTP — just a list."""

    def __init__(self) -> None:
        super().__init__(None)  # disabled: never starts the worker
        self.records: list[dict] = []

    def emit(self, event, **kwargs):  # type: ignore[override]
        record = {"event": event}
        record.update({k: v for k, v in kwargs.items() if v is not None})
        self.records.append(record)
        return True

    def of(self, event: str) -> list[dict]:
        return [r for r in self.records if r["event"] == event]

    def action(self, name: str) -> list[dict]:
        """control_action rows for one action. `setup_protocol` is itself an
        audited action, so tests filter rather than index blindly."""
        return [r for r in self.of("control_action") if r.get("action") == name]


# ---------------------------------------------------------------------------
# The exporter itself
# ---------------------------------------------------------------------------


def test_disabled_without_a_url():
    exporter = EventsExporter(None)
    assert exporter.enabled is False
    # Emitting is a no-op that never raises — dev checkouts and tests stay silent.
    assert exporter.emit("control_action", action="home") is False
    exporter.close()  # safe on a disabled exporter


def test_from_env_falls_back_to_the_equipment_id():
    # One variable fewer to keep in sync on a two-gateway host.
    exporter = EventsExporter.from_env({"OT2_EQUIPMENT_ID": "ot2_hte"})
    assert exporter.device_id == "ot2_hte"
    assert exporter.enabled is False  # no URL ⇒ still disabled

    explicit = EventsExporter.from_env(
        {"OT2_EQUIPMENT_ID": "ot2_hte", "OT2_INGEST_DEVICE_ID": "override"}
    )
    assert explicit.device_id == "override"


def test_posts_the_dashboards_wire_shape():
    sent: list[dict] = []
    exporter = EventsExporter(
        "http://dash/api/ingest/events", device_id="ot2_hte", transport=sent.append
    )
    try:
        exporter.emit("control_action", action="home", outcome="ok", owner="ada")
        exporter.emit("tip_pickup", rack="tips_20", wells=["A1", "B1"])
    finally:
        exporter.close()

    records = [r for payload in sent for r in payload["records"]]
    assert all(p["device_id"] == "ot2_hte" for p in sent)
    assert [r["event"] for r in records] == ["control_action", "tip_pickup"]
    first = records[0]
    assert first["extra"] == {"action": "home", "outcome": "ok", "owner": "ada"}
    assert first["timestamp"].endswith("Z")  # STATUS_SPEC timestamp rule


def test_a_dead_dashboard_never_breaks_the_caller():
    def explode(_payload):
        raise ConnectionError("dashboard down")

    exporter = EventsExporter("http://dash/x", transport=explode)
    try:
        # The contract: emit() returns True (queued) and nothing propagates.
        assert exporter.emit("control_action", action="home") is True
    finally:
        exporter.close()


def test_a_full_queue_drops_rather_than_blocks():
    # A blocked control path is worse than a missing history row.
    exporter = EventsExporter("http://dash/x", queue_size=1, transport=lambda _p: None)
    exporter._thread = None  # stop the worker draining, so the queue can fill
    accepted = [exporter.emit("control_action", action=f"a{i}") for i in range(5)]
    assert accepted[0] is True
    assert False in accepted


# ---------------------------------------------------------------------------
# Service hooks
# ---------------------------------------------------------------------------


RECIPE = {
    "labware": [
        {"nickname": "tips_20", "loadname": "opentrons_96_tiprack_20ul", "location": "5"},
    ],
    "instruments": [
        {"nickname": "p20", "instrument_name": "p20_multi_gen2", "mount": "right", "channels": 8},
    ],
    "modules": [],
}


@pytest.fixture
def service(tmp_path):
    events = RecordingExporter()
    svc = OT2Service(
        dry_run=False,
        plates=PlateStateStore(state_path=tmp_path / "plate.json"),
        decks=DeckDeclarationStore(state_path=tmp_path / "deck.json"),
        tips=TipStateStore(state_path=tmp_path / "tips.json"),
        events=events,
    )
    svc.control = Mock()
    svc.refresh_snapshot = Mock(return_value={})
    svc.state = OT2ServiceState.READY
    svc.setup_protocol(RECIPE)
    return svc


def test_every_control_action_produces_one_audit_row(service):
    service.home()

    rows = service.events.action("home")
    assert len(rows) == 1
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["duration_s"] >= 0
    # `source` distinguishes this from the dashboard passthrough's own row for
    # the same click; the message follows the passthrough's convention.
    assert rows[0]["source"] == "device"
    assert rows[0]["message"] == "unclaimed home → ok"
    # setup_protocol is a control action too, and was audited by the fixture.
    assert service.events.action("setup")


def test_a_failed_action_is_audited_with_its_outcome(service):
    service.control.home.side_effect = RuntimeError("gantry stuck")

    with pytest.raises(RuntimeError):
        service.home()

    row = service.events.action("home")[0]
    assert row["outcome"] == "failed"
    assert "gantry stuck" in row["message"]
    assert row["source"] == "device"


def test_the_audit_row_names_the_claim_holder(service):
    from opentrons_server.gateway.models import ClaimRequest

    service.claims.acquire(ClaimRequest(owner="ada@lab", session_id="s1", ttl_s=30))
    service.home()

    assert service.events.action("home")[0]["owner"] == "ada@lab"


def test_a_multichannel_pick_records_the_whole_covered_column(service):
    service.pick_up_tip(TipRequest(pipette="p20", labware_nickname="tips_20", position="A1"))

    row = service.events.of("tip_pickup")[0]
    assert row["rack"] == "5"  # the slot, not a nickname
    assert row["well"] == "A1"
    assert row["wells"] == [f"{r}1" for r in "ABCDEFGH"]  # 8 tips, not 1
    assert row["channels"] == 8

    service.drop_tip(TipRequest(pipette="p20"))
    drop = service.events.of("tip_drop")[0]
    assert drop["wells"] == [f"{r}1" for r in "ABCDEFGH"]


def test_a_refill_records_what_it_discarded(service):
    service.tips.set_statuses("5", [f"{r}1" for r in "ABCDEFGH"], "empty")

    service.reset_tip_rack("5")

    row = service.events.of("tips_reset")[0]
    # The counts the reset threw away — otherwise history shows a full rack
    # with no record that 8 tips were ever consumed.
    assert row["available_before"] == 88
    assert row["empty_before"] == 8
    assert row["total"] == 96


def test_a_partial_correction_records_what_it_changed(service):
    service.mark_tips("5", status="empty", columns=[1, 2])

    row = service.events.of("tips_marked")[0]
    assert row["status"] == "empty"
    assert row["columns"] == [1, 2]
    assert len(row["wells"]) == 16
    # The counts before the assertion, so history shows what it overrode.
    assert row["available_before"] == 96


def test_session_edges_are_recorded(service):
    service.shutdown()

    row = service.events.of("shutdown")[0]
    assert row["from_state"] == "ready"
    assert row["to_state"] == "requires_init"


def test_dry_run_never_enters_lab_history(tmp_path):
    events = RecordingExporter()
    svc = OT2Service(
        dry_run=True,
        plates=PlateStateStore(state_path=tmp_path / "plate.json"),
        decks=DeckDeclarationStore(state_path=tmp_path / "deck.json"),
        tips=TipStateStore(state_path=tmp_path / "tips.json"),
        events=events,
    )
    svc.control = Mock()
    svc.setup_protocol(RECIPE)

    svc.home()
    svc.reset_tip_rack("5")
    svc.shutdown()

    assert events.records == []
