"""Tests for POST /control/move-to (pipette motion without liquid).

Covers both target forms (well-addressed and absolute deck coordinates),
the exactly-one-target validator, the idempotent transport-loss policy
(error, not unknown_outcome), allowed_actions advertising, and the API
surface in dry-run.
"""

import socket
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from opentrons_server.gateway.api import create_app
from opentrons_server.gateway.models import (
    CoordinateLocation,
    MoveToRequest,
    WellLocation,
)
from opentrons_server.gateway.service import OT2Service, OT2ServiceState


def _ready_service():
    service = OT2Service(dry_run=False)
    control = Mock()
    service.control = control
    service.state = OT2ServiceState.READY
    return service, control


def test_move_to_well_sets_location_then_moves():
    service, control = _ready_service()
    request = MoveToRequest(
        pipette="p300",
        location=WellLocation(labware_nickname="plate", position="A1", bottom=2),
    )
    service.move_to(request)
    control.get_location_from_labware.assert_called_once_with(
        "plate", "A1", top=0, bottom=2, center=0
    )
    control.move_to_pip.assert_called_once_with(
        "p300", speed=None, force_direct=None, minimum_z_height=None
    )
    control.get_location_absolute.assert_not_called()
    assert service.state == OT2ServiceState.READY


def test_move_to_coordinates_with_motion_kwargs():
    service, control = _ready_service()
    request = MoveToRequest(
        pipette="p300",
        coordinates=CoordinateLocation(x=10, y=20, z=30),
        speed=25,
        force_direct=True,
        minimum_z_height=5,
    )
    service.move_to(request)
    control.get_location_absolute.assert_called_once_with(10.0, 20.0, 30.0)
    control.move_to_pip.assert_called_once_with(
        "p300", speed=25.0, force_direct=True, minimum_z_height=5.0
    )
    control.get_location_from_labware.assert_not_called()


def test_move_to_requires_exactly_one_target():
    with pytest.raises(ValidationError):
        MoveToRequest(pipette="p300")
    with pytest.raises(ValidationError):
        MoveToRequest(
            pipette="p300",
            location=WellLocation(labware_nickname="plate", position="A1"),
            coordinates=CoordinateLocation(x=1, y=2, z=3),
        )


def test_move_to_transport_loss_is_idempotent_error_not_unknown_outcome():
    service, control = _ready_service()
    control.move_to_pip.side_effect = socket.timeout("lost during move")
    request = MoveToRequest(
        pipette="p300", coordinates=CoordinateLocation(x=1, y=2, z=3)
    )
    with pytest.raises(socket.timeout):
        service.move_to(request)
    # Idempotent action: plain error (recoverable via startup), never the
    # manual-reconciliation unknown_outcome state — same policy as `home`.
    assert service.state == OT2ServiceState.ERROR
    assert service.state != OT2ServiceState.UNKNOWN_OUTCOME
    assert service.last_error is not None
    assert service.last_error.code == "command_transport_failed"
    assert service.last_error.message.startswith("move_to: ")


def test_ready_allowed_actions_advertise_move_to():
    service, _ = _ready_service()
    assert "move_to" in service.allowed_actions()


def test_move_to_endpoint_dry_run_and_validation():
    client = TestClient(create_app(dry_run=True, enforce_claims=False))
    ok = client.post(
        "/control/move-to",
        json={
            "pipette": "p300",
            "coordinates": {"x": 10, "y": 20, "z": 30},
        },
    )
    assert ok.status_code == 200
    assert ok.json()["state"] == "dry_run"

    both = client.post(
        "/control/move-to",
        json={
            "pipette": "p300",
            "location": {"labware_nickname": "plate", "position": "A1"},
            "coordinates": {"x": 1, "y": 2, "z": 3},
        },
    )
    assert both.status_code == 422
