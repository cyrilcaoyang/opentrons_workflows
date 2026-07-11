"""Wiring tests for the opt-in HTTP (run-engine) transport in OT2Service.

The default transport is SSH and is covered elsewhere; these tests exercise only
the ``transport="http"`` branch, with a fake RunEngineClient so no network or
robot is touched.
"""

import pytest

from opentrons_server.control.http_control import OT2HttpControl
from opentrons_server.gateway.service import OT2Service, OT2ServiceState


class FakeClient:
    def __init__(self, base_url=None):
        self.base_url = base_url
        self.commands = []
        self.created = False
        self.stopped = False
        self.closed = False
        self.run_id = None

    def create_run(self):
        self.created = True
        self.run_id = "run-1"
        return "run-1"

    def execute(self, command, **kwargs):
        self.commands.append(command)
        return {"status": "succeeded"}

    def add_labware_definition(self, definition):
        return "custom/plate/1"

    def get_run(self):
        return {"id": "run-1", "labware": []}

    def stop_run(self):
        self.stopped = True

    def close(self):
        self.closed = True


@pytest.fixture
def fake_http(monkeypatch):
    fake = FakeClient()
    monkeypatch.setenv("OT2_HTTP_BASE_URL", "http://robot:31950")
    monkeypatch.setattr("opentrons_server.gateway.service.RunEngineClient", lambda base_url: fake)
    # keep _refresh_identity hermetic (it would otherwise hit the network)
    monkeypatch.setattr(OT2Service, "probe_robot", lambda self: {"reachable": False})
    return fake


def test_default_transport_is_ssh():
    assert OT2Service(dry_run=True).transport == "ssh"


def test_http_startup_uses_http_control_and_creates_run(fake_http):
    service = OT2Service(dry_run=False, transport="http")

    service.startup()

    assert service.state == OT2ServiceState.READY
    assert isinstance(service.control, OT2HttpControl)
    assert fake_http.created is True
    # snapshot came from the run engine, not a REPL invoke
    assert service.last_snapshot.get("run_id") == "run-1"


def test_http_transport_requires_a_base_url(monkeypatch):
    monkeypatch.delenv("OT2_HTTP_BASE_URL", raising=False)
    monkeypatch.setattr(OT2Service, "probe_robot", lambda self: {"reachable": False})
    service = OT2Service(dry_run=False, transport="http", host_alias=None)

    with pytest.raises(Exception):
        service.startup()
    assert service.state == OT2ServiceState.ERROR


def test_http_aspirate_flows_through_to_a_command(fake_http):
    service = OT2Service(dry_run=False, transport="http")
    service.startup()
    service.state = OT2ServiceState.READY

    # load a pipette + labware, then aspirate through the normal service surface
    service.control.load_instrument(
        {
            "ot_default": True,
            "nickname": "p300",
            "instrument_name": "p300_single_gen2",
            "mount": "right",
        }
    )
    service.control.load_labware(
        {
            "ot_default": True,
            "nickname": "plate",
            "loadname": "corning_96_wellplate_360ul_flat",
            "location": "2",
        }
    )

    from opentrons_server.gateway.models import LiquidMoveRequest, WellLocation

    service.aspirate(
        LiquidMoveRequest(
            pipette="p300",
            volume_ul=50,
            location=WellLocation(labware_nickname="plate", position="A1", bottom=2),
        )
    )

    command_types = [c[0] for c in fake_http.commands]
    assert "aspirate" in command_types
    aspirate = next(c for c in fake_http.commands if c[0] == "aspirate")
    assert aspirate[1]["labwareId"] == "plate"
    assert aspirate[1]["wellName"] == "A1"


def test_http_shutdown_stops_and_closes(fake_http):
    service = OT2Service(dry_run=False, transport="http")
    service.startup()

    service.shutdown()

    assert fake_http.stopped is True
    assert fake_http.closed is True
    assert service.control is None
