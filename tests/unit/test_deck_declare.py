"""Phase 2 tests: operator-declared layout wired into the service + endpoint.

Covers the declared source in get_status (declared / mismatch states), the
setup-recipe overlay, `deck.declare` in allowed_actions, and the
`/control/deck/declare` endpoint (claim-gated, legacy compat, clear, validation).
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opentrons_server.gateway.api import create_app
from opentrons_server.gateway.deck import DeckDeclarationStore
from opentrons_server.gateway.models import DeckState, EquipmentStatus
from opentrons_server.gateway.service import OT2Service, OT2ServiceState

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _decks(tmp_path):
    return DeckDeclarationStore(state_path=tmp_path / "deck.json")


def _repl_deck():
    return json.loads((_FIXTURES / "repl_get_all_states.json").read_text())


# ---------------------------------------------------------------------------
# Declared source in get_status
# ---------------------------------------------------------------------------


def test_declared_layout_surfaces_on_status(tmp_path):
    service = OT2Service(dry_run=True, decks=_decks(tmp_path))
    service.declare_deck({"2": "corning_96_wellplate_360ul_flat"})

    deck = service.get_status().details["snapshot"]["deck"]
    assert deck["source"] == "declared"
    assert deck["slots"]["2"]["slot_state"] == "declared"
    assert deck["slots"]["2"]["labware"]["kind"] == "96-well"


def test_setup_recipe_overlays_standalone_declaration(tmp_path):
    service = OT2Service(dry_run=True, decks=_decks(tmp_path))
    service.declare_deck({"2": "nest_12_reservoir_15ml"})   # standalone intent
    service.setup_protocol(
        {"labware": [{"nickname": "D", "loadname": "corning_96_wellplate_360ul_flat", "location": "2"}]}
    )
    deck = service.get_status().details["snapshot"]["deck"]
    # The realized setup recipe wins the slot.
    assert deck["slots"]["2"]["labware"]["kind"] == "96-well"


def test_declared_vs_observed_mismatch(tmp_path):
    service = OT2Service(dry_run=False, decks=_decks(tmp_path))
    service.state = OT2ServiceState.READY
    service.last_snapshot = _repl_deck()                 # slot 2 observed as 96-well
    service.declare_deck({"2": "corning_24_wellplate_3.4ml_flat"})  # declared 24-well

    slot2 = service.get_status().details["snapshot"]["deck"]["slots"]["2"]
    assert slot2["slot_state"] == "mismatch"
    assert slot2["labware"]["kind"] == "96-well"   # observed wins the labware field
    assert slot2["declared"]["kind"] == "24-well"  # losing intent surfaced


def test_declared_confirmed_by_observation_is_occupied_not_mismatch(tmp_path):
    service = OT2Service(dry_run=False, decks=_decks(tmp_path))
    service.state = OT2ServiceState.READY
    service.last_snapshot = _repl_deck()
    service.declare_deck({"2": "corning_96_wellplate_360ul_flat"})  # agrees with observed

    slot2 = service.get_status().details["snapshot"]["deck"]["slots"]["2"]
    assert slot2["slot_state"] == "occupied"
    assert slot2["declared"] is None


# ---------------------------------------------------------------------------
# allowed_actions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state", [OT2ServiceState.REQUIRES_INIT, OT2ServiceState.READY, OT2ServiceState.DRY_RUN]
)
def test_deck_declare_advertised_in_normal_states(tmp_path, state):
    service = OT2Service(dry_run=(state == OT2ServiceState.DRY_RUN), decks=_decks(tmp_path))
    service.state = state
    assert "deck.declare" in service.get_status().allowed_actions


def test_deck_declare_not_advertised_in_external_control(tmp_path):
    service = OT2Service(dry_run=False, decks=_decks(tmp_path))
    service.state = OT2ServiceState.EXTERNAL_CONTROL
    assert service.get_status().allowed_actions == []


# ---------------------------------------------------------------------------
# /control/deck/declare endpoint
# ---------------------------------------------------------------------------


def _client(tmp_path, **kwargs):
    app = create_app(dry_run=True, auto_reconnect=False, **kwargs)
    app.state.service.decks = _decks(tmp_path)
    return app, TestClient(app)


def test_declare_endpoint_dry_run_roundtrip(tmp_path):
    _app, client = _client(tmp_path, enforce_claims=False)
    resp = client.post(
        "/control/deck/declare",
        json={"slots": {"2": "corning_96_wellplate_360ul_flat", "12": "waste"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    DeckState.model_validate(body)
    assert body["source"] == "declared"
    assert body["slots"]["2"]["labware"]["kind"] == "96-well"
    assert body["slots"]["12"]["labware"]["kind"] == "trash"

    # Reflected on /status.
    st = client.get("/status").json()
    assert st["details"]["snapshot"]["deck"]["slots"]["2"]["slot_state"] == "declared"


def test_declare_endpoint_accepts_object_and_legacy_kind(tmp_path):
    _app, client = _client(tmp_path, enforce_claims=False)
    resp = client.post(
        "/control/deck/declare",
        json={"slots": {"1": {"load_name": "opentrons_96_tiprack_300ul"}, "10": "24-well"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["slots"]["1"]["labware"]["kind"] == "tiprack"
    assert body["slots"]["10"]["labware"]["kind"] == "24-well"


def test_declare_endpoint_uses_attached_definition_for_unknown_load_name(tmp_path):
    """A load_name the regex classifier can't parse (no recognizable category
    token, e.g. a dashboard custom upload) still resolves real geometry once
    the caller attaches the full definition, instead of ``kind: "unknown"``
    with null rows/columns."""
    _app, client = _client(tmp_path, enforce_claims=False)
    definition = {
        "parameters": {"loadName": "plate_a", "isTiprack": False},
        "metadata": {"displayName": "Plate A", "displayCategory": "wellPlate"},
        "ordering": [["A1", "B1"], ["A2", "B2"], ["A3", "B3"]],
    }
    resp = client.post(
        "/control/deck/declare",
        json={"slots": {"2": {"load_name": "plate_a", "definition": definition}}},
    )
    assert resp.status_code == 200
    labware = resp.json()["slots"]["2"]["labware"]
    assert labware["kind"] == "well_plate"
    assert labware["rows"] == 2
    assert labware["columns"] == 3
    assert labware["display_name"] == "Plate A"


def test_declare_endpoint_clear_via_delete(tmp_path):
    _app, client = _client(tmp_path, enforce_claims=False)
    client.post("/control/deck/declare", json={"slots": {"2": "96-well"}})
    resp = client.delete("/control/deck/declare")
    assert resp.status_code == 200
    assert resp.json()["slots"]["2"]["slot_state"] == "empty"


def test_declare_endpoint_invalid_slot_returns_422(tmp_path):
    _app, client = _client(tmp_path, enforce_claims=False)
    resp = client.post("/control/deck/declare", json={"slots": {"13": "96-well"}})
    assert resp.status_code == 422


def test_declare_endpoint_is_claim_gated(tmp_path):
    _app, client = _client(tmp_path, enforce_claims=True)
    # No token -> 423 Locked, same as every other /control/* write.
    assert client.post(
        "/control/deck/declare", json={"slots": {"2": "96-well"}}
    ).status_code == 423

    token = client.post(
        "/control/claim", json={"owner": "test", "session_id": "s1"}
    ).json()["claim_token"]
    ok = client.post(
        "/control/deck/declare",
        json={"slots": {"2": "96-well"}},
        headers={"X-Claim-Token": token},
    )
    assert ok.status_code == 200


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, slot2_state",
    [("status_deck_declared.json", "declared"), ("status_deck_mismatch.json", "mismatch")],
)
def test_declared_fixtures_match_contract(name, slot2_state):
    payload = json.loads((_FIXTURES / name).read_text())
    status = EquipmentStatus(**payload)
    deck = DeckState.model_validate(status.details["snapshot"]["deck"])
    assert deck.slots["2"].slot_state == slot2_state
    if slot2_state == "mismatch":
        assert deck.slots["2"].declared.kind == "24-well"
        assert deck.slots["2"].labware.kind == "96-well"
