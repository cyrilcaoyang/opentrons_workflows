"""Tip lifecycle folded into the gateway: setup registration, pick validation,
sample marking on aspirate/dispense, empty on drop, /status surfacing, 412s."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from opentrons_server.gateway.api import create_app
from opentrons_server.gateway.deck import DeckDeclarationStore
from opentrons_server.gateway.models import (
    LiquidMoveRequest,
    TipRequest,
    WellLocation,
    WellSample,
)
from opentrons_server.gateway.plate_state import PlateStateStore
from opentrons_server.gateway.service import OT2Service, OT2ServiceState
from opentrons_server.gateway.tip_state import TipStateStore, TipUnavailable

RECIPE = {
    "labware": [
        {"nickname": "plate_D", "loadname": "corning_96_wellplate_360ul_flat", "location": "1", "ot_default": True},
        {"nickname": "tips_300", "loadname": "opentrons_96_tiprack_300ul", "location": "4", "ot_default": True},
    ],
    "instruments": [
        {"nickname": "p300", "instrument_name": "p300_single_gen2", "mount": "left", "ot_default": True}
    ],
    "modules": [],
}


@pytest.fixture
def service(tmp_path):
    svc = OT2Service(
        dry_run=False,
        plates=PlateStateStore(state_path=tmp_path / "plate.json"),
        decks=DeckDeclarationStore(state_path=tmp_path / "deck.json"),
        tips=TipStateStore(state_path=tmp_path / "tips.json"),
    )
    control = Mock()
    svc.control = control
    svc.refresh_snapshot = Mock(return_value={})
    svc.state = OT2ServiceState.READY
    return svc


def _pick(service, well=None, sample_id=None, force=False):
    service.pick_up_tip(
        TipRequest(
            pipette="p300",
            labware_nickname="tips_300",
            position=well,
            sample_id=sample_id,
            force=force,
        )
    )


def _move(service, kind, labware, well):
    request = LiquidMoveRequest(
        pipette="p300",
        volume_ul=50,
        location=WellLocation(labware_nickname=labware, position=well),
    )
    getattr(service, kind)(request)


def test_setup_registers_tipracks_only(service):
    service.setup_protocol(RECIPE)

    assert service.tips.has_rack("tips_300")
    assert not service.tips.has_rack("plate_D")


def test_setup_registration_survives_used_tips(service):
    service.setup_protocol(RECIPE)
    service.tips.set_status("tips_300", "A1", "sample_X")

    service.setup_protocol(RECIPE)  # e.g. re-setup after a gateway restart
    assert service.tips.status("tips_300", "A1") == "sample_X"


def test_pick_aspirate_dispense_drop_lifecycle(service):
    service.setup_protocol(RECIPE)

    _pick(service)  # auto-pick -> A1 (column-major first well)
    mounted = service._mounted_tips["p300"]
    assert (mounted["rack"], mounted["well"]) == ("tips_300", "A1")
    service.control.get_location_from_labware.assert_called_with("tips_300", "A1")

    _move(service, "aspirate", "reservoir", "A1")
    assert service.tips.status("tips_300", "A1") == "reservoir_A1"

    _move(service, "dispense", "plate_D", "B2")
    assert service.tips.status("tips_300", "A1") == "plate_D_B2"

    service.drop_tip(TipRequest(pipette="p300"))
    assert service.tips.status("tips_300", "A1") == "empty"
    assert "p300" not in service._mounted_tips

    # Next auto-pick skips the emptied well.
    _pick(service)
    assert service._mounted_tips["p300"]["well"] == "B1"


def test_pick_refuses_cross_sample_reuse(service):
    service.setup_protocol(RECIPE)
    service.tips.set_status("tips_300", "A1", "sample_X")

    with pytest.raises(TipUnavailable):
        _pick(service, well="A1", sample_id="sample_Y")
    # Same sample or force is allowed.
    _pick(service, well="A1", sample_id="sample_X")
    _pick(service, well="A1", force=True)


def test_sample_id_resolves_from_loaded_plate(service):
    service.setup_protocol(RECIPE)
    service.load_plate(
        plate_id="plate_D",
        model="corning_96_wellplate_360ul_flat",
        wells=[WellSample(well="B2", sample_id="caffeine-001")],
    )

    _pick(service)
    _move(service, "dispense", "plate_D", "B2")
    assert service.tips.status("tips_300", "A1") == "caffeine-001"


def test_touching_the_rack_itself_does_not_mark(service):
    service.setup_protocol(RECIPE)
    _pick(service)
    _move(service, "aspirate", "tips_300", "A1")
    assert service.tips.status("tips_300", "A1") == "new"


def test_untracked_rack_behaves_as_before(service):
    # No setup -> nothing registered; explicit position passes straight through.
    service.pick_up_tip(
        TipRequest(pipette="p300", labware_nickname="tips_300", position="C5")
    )
    service.control.get_location_from_labware.assert_called_with("tips_300", "C5")
    assert service._mounted_tips == {}


def test_status_surfaces_tip_state(service):
    service.setup_protocol(RECIPE)
    _pick(service)
    _move(service, "aspirate", "reservoir", "A1")

    details = service.get_status().details
    assert details["tip_racks"]["tips_300"]["available"] == 95
    assert details["tip_racks"]["tips_300"]["tips"] == {"A1": "reservoir_A1"}
    assert details["mounted_tips"]["p300"]["well"] == "A1"


def test_allowed_actions_include_tips_reset(service):
    assert "tips.reset" in service.allowed_actions()


def test_api_pick_up_tip_412_and_tips_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("OT2_TIP_STATE_PATH", str(tmp_path / "tips.json"))
    monkeypatch.setenv("OT2_PLATE_STATE_PATH", str(tmp_path / "plate.json"))
    monkeypatch.setenv("OT2_DECK_STATE_PATH", str(tmp_path / "deck.json"))
    app = create_app(dry_run=True, enforce_claims=False)
    client = TestClient(app)
    service = app.state.service
    service.tips.reset_rack("tips_300")
    service.tips.set_status("tips_300", "A1", "sample_X")

    response = client.post(
        "/control/pick-up-tip",
        json={
            "pipette": "p300",
            "labware_nickname": "tips_300",
            "position": "A1",
            "sample_id": "sample_Y",
        },
    )
    assert response.status_code == 412
    body = response.json()
    assert body["rack"] == "tips_300"
    assert body["tip_status"] == "sample_X"
    # Precondition refusals never mutate last_error (STATUS_SPEC §6.3).
    assert client.get("/status").json()["last_error"] is None

    reset = client.post("/control/tips/reset", json={"nickname": "tips_300"})
    assert reset.status_code == 200
    assert reset.json()["tips"]["A1"] == "new"
