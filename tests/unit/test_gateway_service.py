import socket
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from opentrons_workflows.gateway.api import create_app
from opentrons_workflows.gateway.models import LiquidMoveRequest, WellLocation
from opentrons_workflows.gateway.service import OT2Service, OT2ServiceState, UnknownOutcomeError


def test_dry_run_status_matches_equipment_contract():
    service = OT2Service(dry_run=True)

    status = service.get_status()

    assert status.equipment_id == "ot2"
    assert status.equipment_kind == "liquid_handler"
    assert status.equipment_status == "dry_run"
    assert "startup" in status.allowed_actions
    assert status.details["service_state"] == "dry_run"


def test_gateway_exposes_spec_endpoints_in_dry_run():
    client = TestClient(create_app(dry_run=True, enforce_claims=False))

    assert client.get("/").json()["equipment_id"] == "ot2"
    assert client.get("/health").json() == {"status": "healthy"}
    status = client.get("/status").json()
    assert status["equipment_status"] == "dry_run"
    assert status["equipment_kind"] == "liquid_handler"


def test_non_idempotent_transport_loss_sets_unknown_outcome():
    service = OT2Service(dry_run=False)
    control = Mock()
    control.get_location_from_labware.return_value = None
    control.aspirate.side_effect = socket.timeout("lost during aspirate")
    service.control = control
    service.state = OT2ServiceState.READY

    request = LiquidMoveRequest(
        pipette="p300",
        volume_ul=50,
        location=WellLocation(labware_nickname="plate", position="A1"),
    )

    with pytest.raises(UnknownOutcomeError):
        service.aspirate(request)

    assert service.state == OT2ServiceState.UNKNOWN_OUTCOME
    assert service.last_error is not None
    assert service.last_error.code == "aspirate_unknown_outcome"
    assert service.get_status().equipment_status == "unknown"


def test_reconcile_clears_unknown_outcome():
    service = OT2Service(dry_run=False)
    service.state = OT2ServiceState.UNKNOWN_OUTCOME

    service.reconcile({"deck": {"slots": {}}})

    assert service.state == OT2ServiceState.READY
    assert service.last_error is None
    assert service.last_snapshot == {"deck": {"slots": {}}}
