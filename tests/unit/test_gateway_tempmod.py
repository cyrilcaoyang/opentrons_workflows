"""Temperature-module control: set-target (no wait) and deactivate.

The assistant can only propose what PLAN_ACTIONS lists, and the operator
panel can only POST what /control exposes. Neither existed for the temp
module — /status already showed current vs target, but nothing could change
them. These tests pin the new surface: session auto-load from a declared
slot, recipe-nickname pass-through, range validation, and the HTTP start
(not wait) path.
"""

import socket
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from opentrons_server.gateway.api import create_app
from opentrons_server.gateway.deck import DeckDeclarationStore
from opentrons_server.gateway.models import TempmodDeactivateRequest, TempmodSetRequest
from opentrons_server.gateway.plans import PLAN_ACTIONS, PlanStep, PlanStore
from opentrons_server.gateway.plate_state import PlateStateStore
from opentrons_server.gateway.service import OT2Service, OT2ServiceState
from opentrons_server.gateway.tip_state import TipStateStore


def _stores(tmp_path):
    return dict(
        decks=DeckDeclarationStore(state_path=tmp_path / "deck.json"),
        plates=PlateStateStore(state_path=tmp_path / "plate.json"),
        tips=TipStateStore(state_path=tmp_path / "tips.json"),
    )


def _ready_service(tmp_path):
    service = OT2Service(dry_run=False, **_stores(tmp_path))
    control = Mock()
    service.control = control
    service.state = OT2ServiceState.READY
    return service, control


def test_tempmod_actions_are_plannable():
    assert "tempmod.set" in PLAN_ACTIONS
    assert "tempmod.deactivate" in PLAN_ACTIONS


def test_celsius_is_validated_at_proposal_time():
    with pytest.raises(ValidationError):
        TempmodSetRequest(celsius=0)
    with pytest.raises(ValidationError):
        TempmodSetRequest(celsius=100)
    with pytest.raises(ValidationError):
        TempmodSetRequest(celsius=4, extra="nope")  # type: ignore[call-arg]
    ok = TempmodSetRequest(celsius=4, module="7")
    assert ok.celsius == 4.0
    assert ok.module == "7"


def test_set_loads_a_declared_module_and_starts_without_waiting(tmp_path):
    service, control = _ready_service(tmp_path)
    service.declare_deck({"7": "temperature_module"})

    service.set_tempmod_temperature(TempmodSetRequest(celsius=4, module="7"))

    control.load_module.assert_called_once_with(
        {
            "nickname": "slot_7",
            "module_name": "temperatureModuleV2",
            "location": "7",
        }
    )
    control.tempmod_start_set_temperature.assert_called_once_with("slot_7", 4.0)
    control.tempmod_set_temperature.assert_not_called()
    assert service.state == OT2ServiceState.READY


def test_set_omits_module_when_exactly_one_is_declared(tmp_path):
    service, control = _ready_service(tmp_path)
    service.declare_deck({"7": "temperature_module"})

    service.set_tempmod_temperature(TempmodSetRequest(celsius=37))

    control.tempmod_start_set_temperature.assert_called_once_with("slot_7", 37.0)


def test_set_uses_a_recipe_nickname_without_reloading(tmp_path):
    service, control = _ready_service(tmp_path)
    service.session_recipe = {
        "labware": [],
        "instruments": [],
        "modules": [
            {
                "nickname": "tm",
                "module_name": "temperatureModuleV2",
                "location": "7",
            }
        ],
    }

    service.set_tempmod_temperature(TempmodSetRequest(celsius=4, module="tm"))

    control.load_module.assert_not_called()
    control.tempmod_start_set_temperature.assert_called_once_with("tm", 4.0)


def test_set_refuses_an_empty_deck(tmp_path):
    service, _ = _ready_service(tmp_path)
    with pytest.raises(RuntimeError, match="no temperature module"):
        service.set_tempmod_temperature(TempmodSetRequest(celsius=4))


def test_deactivate_uses_the_same_session_nickname(tmp_path):
    service, control = _ready_service(tmp_path)
    service.declare_deck({"7": "temperature_module"})

    service.set_tempmod_temperature(TempmodSetRequest(celsius=4, module="7"))
    service.deactivate_tempmod(TempmodDeactivateRequest(module="7"))

    control.tempmod_deactivate.assert_called_once_with("slot_7")
    # Second call must not load the module again.
    assert control.load_module.call_count == 1


def test_transport_loss_on_set_is_idempotent_error(tmp_path):
    service, control = _ready_service(tmp_path)
    service.declare_deck({"7": "temperature_module"})
    control.tempmod_start_set_temperature.side_effect = socket.timeout("lost")

    with pytest.raises(socket.timeout):
        service.set_tempmod_temperature(TempmodSetRequest(celsius=4, module="7"))

    assert service.state == OT2ServiceState.ERROR
    assert service.last_error is not None
    assert service.last_error.code == "command_transport_failed"


def test_ready_allowed_actions_advertise_tempmod(tmp_path):
    service, _ = _ready_service(tmp_path)
    assert "tempmod.set" in service.allowed_actions()
    assert "tempmod.deactivate" in service.allowed_actions()


def test_plan_proposal_accepts_tempmod_set():
    store = PlanStore()
    plan = store.create(
        [PlanStep(action="tempmod.set", args={"celsius": 4, "module": "7"})],
        created_by="assistant",
    )
    assert plan.status == "draft"
    assert plan.steps[0].action == "tempmod.set"


def test_endpoints_dry_run_and_validation():
    client = TestClient(create_app(dry_run=True, enforce_claims=False, ui=False))

    ok = client.post("/control/tempmod/set", json={"celsius": 4, "module": "7"})
    assert ok.status_code == 200
    assert ok.json()["state"] == "dry_run"

    cold = client.post("/control/tempmod/set", json={"celsius": 0})
    assert cold.status_code == 422

    off = client.post("/control/tempmod/deactivate", json={})
    assert off.status_code == 200

    catalog = client.get("/plans/actions").json()
    names = {row["action"] for row in catalog["actions"]}
    assert {"tempmod.set", "tempmod.deactivate"} <= names
