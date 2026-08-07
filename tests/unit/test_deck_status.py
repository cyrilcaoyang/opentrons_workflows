"""Phase 1 tests: normalized deck wired into OT2Service.get_status().

Covers the observed sources (repl + cached run probe), plate-well attachment via
the setup recipe, the side-effect-free guarantee of the deck build, and that the
updated/new fixtures validate against the contract.
"""

import json
from pathlib import Path

import pytest

from opentrons_server.gateway.models import DeckState, EquipmentStatus, WellSample
from opentrons_server.gateway.service import OT2Service, OT2ServiceState

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_state_files(tmp_path, monkeypatch):
    """Point the default plate/deck stores at an empty tmp dir.

    ``OT2Service``'s default stores persist to ``ot2_state.json`` /
    ``ot2_deck_state.json``; both resolve a *relative* path against the repo root
    (not cwd), so a stale runtime file left there (e.g. from running the gateway
    locally) bleeds a *declared* deck into the dry-run "empty deck" assertions —
    failing only on developer machines, not in CI. Re-anchor the resolver under
    ``tmp_path`` so each test gets a clean, isolated store.
    """
    from opentrons_server.gateway import deck as deck_mod
    from opentrons_server.gateway import plate_state as plate_mod

    def _isolated(state_path):
        return tmp_path / Path(state_path).name

    monkeypatch.setattr(deck_mod, "_resolve_state_path", _isolated)
    monkeypatch.setattr(plate_mod, "_resolve_state_path", _isolated)


def _repl_deck():
    return json.loads((_FIXTURES / "repl_get_all_states.json").read_text())


def _run_doc():
    return json.loads((_FIXTURES / "robot_run_labware.json").read_text())


# ---------------------------------------------------------------------------
# get_status -> details.snapshot.deck
# ---------------------------------------------------------------------------


def test_dry_run_deck_is_normalized_and_empty():
    service = OT2Service(dry_run=True)
    deck = service.get_status().details["snapshot"]["deck"]

    assert deck["source"] == "empty"
    assert set(deck["slots"]) == {str(i) for i in range(1, 13)}
    assert all(s["slot_state"] == "empty" and s["labware"] is None for s in deck["slots"].values())
    # DeckState round-trips (shape is contract-valid).
    DeckState.model_validate(deck)


def test_ready_deck_reflects_repl_snapshot_as_occupied():
    service = OT2Service(dry_run=False)
    service.state = OT2ServiceState.READY
    service.last_snapshot = _repl_deck()

    deck = service.get_status().details["snapshot"]["deck"]
    assert deck["source"] == "repl"
    assert deck["slots"]["1"]["labware"]["kind"] == "tiprack"
    assert deck["slots"]["2"]["slot_state"] == "occupied"
    assert deck["slots"]["7"]["module"]["module_name"] == "temperature module gen2"
    assert deck["slots"]["3"]["slot_state"] == "empty"


def test_busy_deck_marks_observed_slots_in_use():
    service = OT2Service(dry_run=False)
    service.state = OT2ServiceState.BUSY
    service.last_snapshot = _repl_deck()

    deck = service.get_status().details["snapshot"]["deck"]
    assert deck["slots"]["2"]["slot_state"] == "in_use"


def test_loaded_plate_attaches_to_slot_via_setup_recipe():
    service = OT2Service(dry_run=True)
    service.setup_protocol(
        {
            "labware": [
                {"nickname": "D", "loadname": "corning_96_wellplate_360ul_flat", "location": "2"}
            ]
        }
    )
    service.load_plate(
        plate_id="D",
        model="corning_96_wellplate_360ul_flat",
        wells=[WellSample(well="A1", sample_id="caffeine", volume_ul=200.0)],
    )

    deck = service.get_status().details["snapshot"]["deck"]
    slot2 = deck["slots"]["2"]
    # Dry-run has no live observation, so the loaded plate declares its slot.
    assert slot2["slot_state"] == "declared"
    assert slot2["labware"]["plate_id"] == "D"
    assert slot2["labware"]["wells"][0]["sample_id"] == "caffeine"
    # Back-compat contract preserved alongside the new deck view.
    assert service.get_status().details["loaded_plate"]["plate_id"] == "D"


def test_external_control_uses_run_source():
    service = OT2Service(dry_run=False)
    service.state = OT2ServiceState.EXTERNAL_CONTROL
    service._last_run_labware = _run_doc()

    deck = service.get_status().details["snapshot"]["deck"]
    assert deck["source"] == "run"
    assert deck["slots"]["2"]["labware"]["kind"] == "96-well"
    # EXTERNAL_CONTROL counts as busy.
    assert deck["slots"]["2"]["slot_state"] == "in_use"


def test_run_source_wins_over_repl():
    service = OT2Service(dry_run=False)
    service.state = OT2ServiceState.READY
    service.last_snapshot = _repl_deck()          # slot 2 -> 96-well plate (repl)
    service._last_run_labware = {
        "labware": [{"loadName": "opentrons_96_tiprack_300ul", "location": {"slotName": "2"}}]
    }
    deck = service.get_status().details["snapshot"]["deck"]
    assert deck["slots"]["2"]["source"] == "run"
    assert deck["slots"]["2"]["labware"]["kind"] == "tiprack"


# ---------------------------------------------------------------------------
# Side-effect-free guarantee
# ---------------------------------------------------------------------------


def test_build_deck_state_makes_no_http_calls(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("deck build must not perform HTTP")

    monkeypatch.setattr("opentrons_server.gateway.service.requests.get", boom)
    monkeypatch.setattr("opentrons_server.gateway.service.requests.post", boom)

    service = OT2Service(dry_run=False)
    service.state = OT2ServiceState.READY
    service.last_snapshot = _repl_deck()

    deck = service._build_deck_state()   # must not raise
    assert deck.source == "repl"
    assert deck.slots["2"].slot_state == "occupied"


# ---------------------------------------------------------------------------
# run-labware probe robustness
# ---------------------------------------------------------------------------


def test_probe_run_labware_returns_none_when_unreachable(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("opentrons_server.gateway.service.requests.get", boom)
    service = OT2Service(dry_run=False, host_alias="192.168.0.9")
    assert service.probe_run_labware() is None


def test_refresh_run_labware_ttl_guard(monkeypatch):
    calls = {"n": 0}

    def counting_probe():
        calls["n"] += 1
        return {"labware": [], "modules": []}

    service = OT2Service(dry_run=False)
    monkeypatch.setattr(service, "probe_run_labware", counting_probe)

    service._refresh_run_labware(force=True)
    service._refresh_run_labware()  # within TTL -> skipped
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Fixtures validate against the contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, slot2_state",
    [("status_deck_occupied.json", "occupied"), ("status_deck_in_use.json", "in_use")],
)
def test_new_deck_fixtures_match_contract(name, slot2_state):
    payload = json.loads((_FIXTURES / name).read_text())
    status = EquipmentStatus(**payload)
    deck = DeckState.model_validate(status.details["snapshot"]["deck"])
    assert deck.slots["2"].slot_state == slot2_state
    assert deck.slots["2"].labware.plate_id == "D"
    assert deck.slots["2"].labware.wells[0].sample_id == "caffeine"


@pytest.mark.parametrize(
    "name",
    [
        "status_dry_run.json",
        "status_lights_on.json",
        "status_requires_init.json",
        "status_ready_claim_held.json",
    ],
)
def test_existing_fixtures_have_normalized_empty_deck(name):
    payload = json.loads((_FIXTURES / name).read_text())
    status = EquipmentStatus(**payload)
    deck = DeckState.model_validate(status.details["snapshot"]["deck"])
    assert deck.source == "empty"
    assert len(deck.slots) == 12
