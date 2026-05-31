"""Tests for the OT-2 plate-state layer and the live-snapshot parser.

Covers:
- PlateStateStore load/unload/update + atomic persistence + reload-on-restart
- OT2Service._parse_remote_snapshot (the live-snapshot REPL parsing fix)
- /control/plate/* and /control/well/update endpoints (claim-gated, dry-run)
- details.loaded_plate surfaced on /status
"""

import json

import pytest
from fastapi.testclient import TestClient

from opentrons_server.gateway.api import create_app
from opentrons_server.gateway.models import WellSample
from opentrons_server.gateway.plate_state import PlateStateStore, well_ids_96
from opentrons_server.gateway.service import OT2Service


# ---------------------------------------------------------------------------
# PlateStateStore
# ---------------------------------------------------------------------------


def _store(tmp_path):
    return PlateStateStore(state_path=tmp_path / "state.json")


def test_load_defaults_to_96_empty_wells(tmp_path):
    store = _store(tmp_path)
    plate = store.load_plate(plate_id="P1", model="corning_96_wellplate_360ul_flat")

    assert plate.plate_id == "P1"
    assert len(plate.wells) == 96
    assert [w.well for w in plate.wells] == well_ids_96()
    assert all(w.volume_ul is None and w.sample_id is None for w in plate.wells)


def test_update_well_mutates_and_persists(tmp_path):
    store = _store(tmp_path)
    store.load_plate(plate_id="P1", model="custom_96")

    updated = store.update_well("A1", sample_id="caffeine", volume_ul=200.0)
    assert updated.sample_id == "caffeine"
    assert updated.volume_ul == 200.0

    # A fresh store over the same file recovers the mutation (restart survival).
    reborn = PlateStateStore(state_path=store.state_path)
    a1 = next(w for w in reborn.get().wells if w.well == "A1")
    assert a1.sample_id == "caffeine"
    assert a1.volume_ul == 200.0


def test_unload_clears_plate(tmp_path):
    store = _store(tmp_path)
    store.load_plate(plate_id="P1", model="custom_96")
    previous = store.unload_plate()

    assert previous.plate_id == "P1"
    assert store.get() is None
    assert PlateStateStore(state_path=store.state_path).get() is None


def test_update_well_without_plate_raises_lookup(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(LookupError):
        store.update_well("A1", volume_ul=10.0)


def test_update_unknown_well_raises_lookup(tmp_path):
    store = _store(tmp_path)
    store.load_plate(plate_id="P1", model="custom_96")
    with pytest.raises(LookupError):
        store.update_well("Z9", volume_ul=10.0)


def test_clear_flags_null_out_fields(tmp_path):
    store = _store(tmp_path)
    store.load_plate(plate_id="P1", model="custom_96")
    store.update_well("A1", sample_id="x", notes="n")

    cleared = store.update_well("A1", clear_sample_id=True, clear_notes=True)
    assert cleared.sample_id is None
    assert cleared.notes is None


def test_load_with_explicit_wells_validates_grid(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.load_plate(
            plate_id="P1", model="custom_96", wells=[WellSample(well="A99")]
        )


def test_empty_model_rejected(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.load_plate(plate_id="P1", model="")


def test_corrupt_state_file_is_ignored(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = PlateStateStore(state_path=path)  # must not raise
    assert store.get() is None


# ---------------------------------------------------------------------------
# Live-snapshot parsing fix (_parse_remote_snapshot)
# ---------------------------------------------------------------------------


def test_parse_remote_snapshot_extracts_json_from_repl_transcript():
    payload = {"deck": {"slots": {"1": None}}, "pipettes": {}, "labwares": {}, "modules": {}}
    # Simulate the SSH-REPL transcript: echoed command, printed JSON, prompt.
    transcript = (
        "import json; print(json.dumps(get_all_states(protocol), default=str))\r\n"
        + json.dumps(payload)
        + "\r\n>>> "
    )
    parsed = OT2Service._parse_remote_snapshot(transcript)
    assert parsed == payload
    assert "raw" not in parsed


def test_parse_remote_snapshot_falls_back_on_garbage():
    parsed = OT2Service._parse_remote_snapshot(">>> no json here >>>")
    assert "raw" in parsed
    assert "note" in parsed


def test_parse_remote_snapshot_handles_unbalanced_braces():
    parsed = OT2Service._parse_remote_snapshot("garbage { not json")
    assert "raw" in parsed


# ---------------------------------------------------------------------------
# Endpoints (dry-run + claim gating) and /status surfacing
# ---------------------------------------------------------------------------


def test_status_exposes_loaded_plate_none_by_default(tmp_path):
    service = OT2Service(dry_run=True, plates=_store(tmp_path))
    status = service.get_status()
    assert status.details["loaded_plate"] is None
    assert "plate.load" in status.allowed_actions
    assert "well.update" in status.allowed_actions


def test_plate_endpoints_roundtrip_in_dry_run(tmp_path):
    app = create_app(dry_run=True, enforce_claims=False)
    app.state.service.plates = _store(tmp_path)
    client = TestClient(app)

    loaded = client.post(
        "/control/plate/load", json={"plate_id": "P1", "model": "custom_96"}
    )
    assert loaded.status_code == 200
    assert loaded.json()["plate_id"] == "P1"
    assert len(loaded.json()["wells"]) == 96

    upd = client.post(
        "/control/well/update", json={"well": "A1", "sample_id": "x", "volume_ul": 50.0}
    )
    assert upd.status_code == 200
    assert upd.json()["volume_ul"] == 50.0

    # Surfaced on /status.
    status = client.get("/status").json()
    a1 = next(w for w in status["details"]["loaded_plate"]["wells"] if w["well"] == "A1")
    assert a1["sample_id"] == "x"

    # Unload clears it.
    assert client.post("/control/plate/unload").json()["plate_id"] == "P1"
    assert client.get("/status").json()["details"]["loaded_plate"] is None


def test_well_update_without_plate_returns_409(tmp_path):
    app = create_app(dry_run=True, enforce_claims=False)
    app.state.service.plates = _store(tmp_path)
    client = TestClient(app)
    resp = client.post("/control/well/update", json={"well": "A1", "volume_ul": 10.0})
    assert resp.status_code == 409


def test_plate_endpoints_are_claim_gated(tmp_path):
    app = create_app(dry_run=True, enforce_claims=True, auto_reconnect=False)
    app.state.service.plates = _store(tmp_path)
    client = TestClient(app)

    # No token -> 423 Locked, same as every other /control/* write.
    assert client.post(
        "/control/plate/load", json={"plate_id": "P1", "model": "custom_96"}
    ).status_code == 423

    token = client.post(
        "/control/claim", json={"owner": "test", "session_id": "s1"}
    ).json()["claim_token"]
    ok = client.post(
        "/control/plate/load",
        json={"plate_id": "P1", "model": "custom_96"},
        headers={"X-Claim-Token": token},
    )
    assert ok.status_code == 200
