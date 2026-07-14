import json
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from opentrons_server.gateway.api import create_app
from opentrons_server.gateway.models import EquipmentStatus, LiquidMoveRequest, WellLocation
from opentrons_server.gateway.service import OT2Service, OT2ServiceState, UnknownOutcomeError

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


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


# ---------------------------------------------------------------------------
# Deck-light toggle: /control/lights endpoint + /status components wiring
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _ready_service_with_control() -> OT2Service:
    service = OT2Service(dry_run=False)
    control = Mock()
    control.client.hostname = "ot2.local"
    service.control = control
    service.state = OT2ServiceState.READY
    return service


def test_dry_run_lights_default_off_and_toggle():
    service = OT2Service(dry_run=True)

    status = service.get_status()
    assert status.components["lights"].connected is True
    assert status.components["lights"].state == "off"
    assert "lights.set" in status.allowed_actions

    assert service.set_lights(True) is True
    assert service.get_lights() is True
    assert service.get_status().components["lights"].state == "on"


def test_lights_endpoint_dry_run_toggles_and_reflects_in_status():
    client = TestClient(create_app(dry_run=True, enforce_claims=False))

    resp = client.post("/control/lights", json={"on": True})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    status = client.get("/status").json()
    assert status["components"]["lights"]["state"] == "on"
    assert "lights.set" in status["allowed_actions"]


def test_lights_component_reads_robot_http(monkeypatch):
    service = _ready_service_with_control()
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return _FakeResponse({"on": True})

    monkeypatch.setattr("opentrons_server.gateway.service.requests.get", fake_get)

    status = service.get_status()

    assert captured["url"] == "http://ot2.local:31950/robot/lights"
    assert captured["headers"]["Opentrons-Version"] == "3"
    assert status.components["lights"].connected is True
    assert status.components["lights"].state == "on"
    # Convenience control: available even though it isn't tied to ready state.
    assert "lights.set" in status.allowed_actions


def test_lights_unreachable_reported_as_unknown(monkeypatch):
    service = _ready_service_with_control()

    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("opentrons_server.gateway.service.requests.get", boom)

    status = service.get_status()

    assert status.components["lights"].connected is False
    assert status.components["lights"].state == "unknown"
    assert "lights.set" not in status.allowed_actions


def test_set_lights_posts_to_robot_http(monkeypatch):
    service = _ready_service_with_control()
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return _FakeResponse({"on": kwargs.get("json", {}).get("on")})

    monkeypatch.setattr("opentrons_server.gateway.service.requests.post", fake_post)

    assert service.set_lights(True) is True
    assert captured["url"] == "http://ot2.local:31950/robot/lights"
    assert captured["json"] == {"on": True}
    assert captured["headers"]["Opentrons-Version"] == "3"


def test_lights_endpoint_is_claim_gated_and_proxies(monkeypatch):
    app = create_app(dry_run=False, enforce_claims=True, auto_reconnect=False)
    service = app.state.service
    control = Mock()
    control.client.hostname = "ot2.local"
    service.control = control
    service.state = OT2ServiceState.READY

    def fake_post(url, **kwargs):
        return _FakeResponse({"on": kwargs.get("json", {}).get("on")})

    monkeypatch.setattr("opentrons_server.gateway.service.requests.post", fake_post)
    client = TestClient(app)

    # No claim token -> 423 Locked, same as the other /control/* actions.
    assert client.post("/control/lights", json={"on": True}).status_code == 423

    token = client.post(
        "/control/claim", json={"owner": "test", "session_id": "s1"}
    ).json()["claim_token"]
    resp = client.post(
        "/control/lights", json={"on": True}, headers={"X-Claim-Token": token}
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "Deck lights on"


def test_lights_endpoint_409_when_not_initialized():
    # No session and no OT2_HTTP_BASE_URL override -> set_lights raises, 409.
    client = TestClient(create_app(dry_run=False, enforce_claims=False, auto_reconnect=False))
    assert client.post("/control/lights", json={"on": True}).status_code == 409


def test_status_lights_on_fixture_matches_contract():
    payload = json.loads((_FIXTURES / "status_lights_on.json").read_text())
    status = EquipmentStatus(**payload)
    assert status.components["lights"].state == "on"
    assert status.components["lights"].connected is True
    assert "lights.set" in status.allowed_actions


# ---------------------------------------------------------------------------
# Guarded auto-reconnect on process start (probe + re-derive state)
# ---------------------------------------------------------------------------


def _fake_probe_get(*, health=None, run_active=False, instruments=None, modules=None, fail=False):
    """Build a fake requests.get that dispatches by robot-server endpoint."""

    default_health = {
        "api_version": "8.7.0",
        "fw_version": "v1.1.0",
        "robot_model": "OT-2 Standard",
        "name": "ot2cytation",
    }
    default_instruments = [
        {
            "mount": "left",
            "instrumentModel": "p300_multi_v2.0",
            "instrumentName": "p300_multi_gen2",
            "data": {"channels": 8},
        }
    ]

    def fake_get(url, **kwargs):
        if fail:
            raise RuntimeError("connection refused")
        if url.endswith("/health"):
            return _FakeResponse(health or default_health)
        if url.endswith("/runs"):
            status = "running" if run_active else "succeeded"
            return _FakeResponse({"data": [{"current": run_active, "status": status}]})
        if url.endswith("/instruments"):
            data = default_instruments if instruments is None else instruments
            return _FakeResponse({"data": data})
        if url.endswith("/modules"):
            return _FakeResponse({"data": modules or []})
        return _FakeResponse({})

    return fake_get


def test_probe_surfaces_attached_modules(monkeypatch):
    mods = [
        {
            "moduleModel": "temperatureModuleV2",
            "moduleType": "temperatureModuleType",
            "serialNumber": "TMV2123",
            "id": "mod-1",
            "data": {"status": "idle"},
        }
    ]
    monkeypatch.setattr(
        "opentrons_server.gateway.service.requests.get", _fake_probe_get(modules=mods)
    )
    service = OT2Service(dry_run=False, host_alias="192.168.0.9")

    probe = service.probe_robot()
    assert probe["modules"] == [
        {
            "model": "temperatureModuleV2",
            "type": "temperatureModuleType",
            "serial": "TMV2123",
            "id": "mod-1",
            "status": "idle",
        }
    ]
    # flows through to /status as details.robot.modules
    service._last_probe = probe
    status = service.get_status()
    assert status.details["robot"]["modules"][0]["model"] == "temperatureModuleV2"


def test_probe_no_modules_is_empty_list(monkeypatch):
    monkeypatch.setattr(
        "opentrons_server.gateway.service.requests.get", _fake_probe_get()
    )
    probe = OT2Service(dry_run=False, host_alias="192.168.0.9").probe_robot()
    assert probe["modules"] == []


def test_boot_reconnect_idle_establishes_session(monkeypatch):
    captured: dict = {}

    def fake_ot2control(**kwargs):
        captured.update(kwargs)
        return Mock()

    monkeypatch.setattr("opentrons_server.gateway.service.OT2Control", fake_ot2control)
    monkeypatch.setattr("opentrons_server.gateway.service.requests.get", _fake_probe_get())

    service = OT2Service(dry_run=False, host_alias="192.168.0.9", password="pw")
    service.boot_reconnect()

    assert service.state == OT2ServiceState.READY
    assert service.equipment_version == "8.7.0"
    status = service.get_status()
    assert status.equipment_status == "ready"
    assert status.equipment_version == "8.7.0"
    assert status.components["pipette_left"].state == "p300_multi_gen2"
    assert status.details["robot"]["robot_name"] == "ot2cytation"


def test_boot_reconnect_active_run_stands_off(monkeypatch):
    def must_not_connect(**kwargs):
        raise AssertionError("must not open a session while a run is active")

    monkeypatch.setattr("opentrons_server.gateway.service.OT2Control", must_not_connect)
    monkeypatch.setattr(
        "opentrons_server.gateway.service.requests.get", _fake_probe_get(run_active=True)
    )

    service = OT2Service(dry_run=False, host_alias="192.168.0.9")
    service.boot_reconnect()

    assert service.state == OT2ServiceState.EXTERNAL_CONTROL
    assert service.control is None
    status = service.get_status()
    assert status.equipment_status == "busy"
    assert status.allowed_actions == []
    assert "external" in status.message.lower()


def test_boot_reconnect_unreachable_stays_requires_init(monkeypatch):
    monkeypatch.setattr(
        "opentrons_server.gateway.service.requests.get", _fake_probe_get(fail=True)
    )

    service = OT2Service(dry_run=False, host_alias="192.168.0.9")
    service.boot_reconnect()

    assert service.state == OT2ServiceState.REQUIRES_INIT
    assert service.control is None
    assert "unreachable" in service.get_status().message.lower()


def test_boot_reconnect_is_noop_in_dry_run(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("dry-run must not probe the robot")

    monkeypatch.setattr("opentrons_server.gateway.service.requests.get", boom)
    service = OT2Service(dry_run=True)
    service.boot_reconnect()
    assert service.state == OT2ServiceState.DRY_RUN
