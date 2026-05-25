import socket
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from opentrons_server.gateway.api import create_app
from opentrons_server.gateway.models import LiquidMoveRequest, WellLocation
from opentrons_server.gateway.service import OT2Service, OT2ServiceState, UnknownOutcomeError


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


# ---------------------------------------------------------------------------
# /control/startup credential precedence: env-var default vs request body
# ---------------------------------------------------------------------------


def _service_with_fake_control(monkeypatch, **kwargs):
    """Build a service whose OT2Control() doesn't try to open SSH; capture
    the kwargs passed to OT2Control() so tests can assert what credentials
    actually reached the transport layer."""
    captured: dict = {}

    def fake_ot2control(**ot2_kwargs):
        captured.update(ot2_kwargs)
        return Mock()

    monkeypatch.setattr(
        "opentrons_server.gateway.service.OT2Control", fake_ot2control
    )
    service = OT2Service(dry_run=False, **kwargs)
    return service, captured


def test_empty_password_in_body_preserves_env_default(monkeypatch):
    """Regression: password='' in the request body must NOT clear the
    env-var-supplied default. The whole point of putting OT2_SSH_PASSWORD
    on the gateway host is that workflows can call /control/startup
    without sending the passphrase themselves."""
    service, captured = _service_with_fake_control(
        monkeypatch, host_alias="env-host", password="env-secret"
    )
    service.startup(host_alias="env-host", password="", simulation=True)
    assert service.password == "env-secret"
    assert captured["password"] == "env-secret"


def test_missing_password_in_body_preserves_env_default(monkeypatch):
    """password=None (field absent) also falls through to the env default."""
    service, captured = _service_with_fake_control(
        monkeypatch, host_alias="env-host", password="env-secret"
    )
    service.startup(host_alias="env-host", password=None, simulation=True)
    assert service.password == "env-secret"
    assert captured["password"] == "env-secret"


def test_truthy_password_in_body_overrides_env_default(monkeypatch):
    """Explicit non-empty password still overrides (the debug / local-dev
    path; production workflow code should not exercise this)."""
    service, captured = _service_with_fake_control(
        monkeypatch, host_alias="env-host", password="env-secret"
    )
    service.startup(host_alias="env-host", password="override", simulation=True)
    assert service.password == "override"
    assert captured["password"] == "override"


def test_empty_host_alias_in_body_preserves_env_default(monkeypatch):
    """host_alias was already correctly truthy-checked (line 87 of service.py);
    this test pins the behaviour so a future refactor can't regress it."""
    service, captured = _service_with_fake_control(
        monkeypatch, host_alias="env-host", password="env-secret"
    )
    service.startup(host_alias="", password=None, simulation=True)
    assert service.host_alias == "env-host"
    assert captured["host_alias"] == "env-host"
