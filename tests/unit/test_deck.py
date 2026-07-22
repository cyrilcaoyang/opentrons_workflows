"""Phase 0 tests for the normalized deck model (pure — no hardware, no I/O).

Covers:
- classify_labware: load_name -> (kind, rows, columns), incl. the 12-count ambiguity
- normalize_repl_slots / normalize_run_slots from raw fixtures
- build_deck merge: one case per row of docs/DECK_STATE.md decision table,
  precedence run > repl > declared > empty, plate-well attachment
- DeckDeclarationStore: declare/clear/reload-on-restart/corrupt-file-tolerant, legacy
  kind-string compat
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from opentrons_server.gateway.deck import (
    DeckDeclarationStore,
    build_deck,
    classify_labware,
    make_slot_labware,
    normalize_repl_slots,
    normalize_run_slots,
)
from opentrons_server.gateway.models import LoadedPlate, SlotLabware, SlotModule, WellSample

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# classify_labware
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "load_name, expected_kind, rows, cols",
    [
        ("corning_96_wellplate_360ul_flat", "96-well", 8, 12),
        ("corning_384_wellplate_112ul_flat", "384-well", 16, 24),
        ("opentrons_96_tiprack_300ul", "tiprack", 8, 12),
        ("opentrons_96_filtertiprack_200ul", "tiprack", 8, 12),
        ("nest_12_reservoir_15ml", "reservoir", 1, 12),
        ("agilent_1_reservoir_290ml", "reservoir", 1, 1),
        ("opentrons_24_tuberack_nest_1.5ml_snapcap", "tuberack", 4, 6),
        ("opentrons_1_trash_1100ml_fixed", "trash", None, None),
        ("nest_96_wellplate_2ml_deep", "96-well", 8, 12),
        ("some_weird_thing_v2", "unknown", None, None),
    ],
)
def test_classify_labware(load_name, expected_kind, rows, cols):
    kind, r, c = classify_labware(load_name)
    assert kind == expected_kind
    assert (r, c) == (rows, cols)


def test_twelve_count_ambiguity_resolved_by_category():
    # 12-reservoir is a single row of 12; a hypothetical 12-wellplate is 3x4.
    assert classify_labware("nest_12_reservoir_15ml") == ("reservoir", 1, 12)
    assert classify_labware("generic_12_wellplate_22ml") == ("12-well", 3, 4)


def test_unknown_load_name_with_is_tiprack_hint_falls_back_to_tiprack():
    kind, _, _ = classify_labware("mystery_rack", is_tiprack=True)
    assert kind == "tiprack"


def test_display_category_hint_wins_over_ambiguous_name():
    kind, _, _ = classify_labware("vendor_96_thing", display_category="wellPlate")
    assert kind == "96-well"


def test_make_slot_labware_sets_is_tiprack_and_grid():
    lw = make_slot_labware("opentrons_96_tiprack_300ul", display_name="Tips")
    assert lw.is_tiprack is True
    assert (lw.rows, lw.columns) == (8, 12)
    assert lw.display_name == "Tips"

    plate = make_slot_labware("corning_96_wellplate_360ul_flat")
    assert plate.is_tiprack is False


def test_make_slot_labware_explicit_grid_overrides_derived():
    lw = make_slot_labware("weird_labware", rows=5, columns=7)
    assert (lw.rows, lw.columns) == (5, 7)


# ---------------------------------------------------------------------------
# normalizers
# ---------------------------------------------------------------------------


def test_normalize_repl_slots_from_fixture():
    raw = json.loads((_FIXTURES / "repl_get_all_states.json").read_text())
    slots = normalize_repl_slots(raw["deck"])

    # Empty slots are omitted.
    assert set(slots) == {"1", "2", "4", "6", "7", "12"}
    assert isinstance(slots["1"], SlotLabware) and slots["1"].kind == "tiprack"
    assert slots["2"].kind == "96-well"
    assert slots["4"].kind == "reservoir"
    assert slots["6"].kind == "tuberack"
    assert slots["12"].kind == "trash"
    # Slot 7 is a module.
    assert isinstance(slots["7"], SlotModule)
    assert slots["7"].module_name == "temperature module gen2"
    assert slots["7"].status == "idle"


def test_normalize_repl_slots_handles_none_and_empty():
    assert normalize_repl_slots(None) == {}
    assert normalize_repl_slots({"slots": {}}) == {}
    assert normalize_repl_slots({"slots": {"1": None}}) == {}


def test_normalize_run_slots_from_fixture():
    raw = json.loads((_FIXTURES / "robot_run_labware.json").read_text())
    slots = normalize_run_slots(raw)

    # Off-deck labware (moduleId, no slotName) is skipped.
    assert set(slots) == {"1", "2", "12"}
    assert slots["1"].kind == "tiprack"
    assert slots["2"].kind == "96-well"
    assert slots["2"].display_name == "Reaction Plate"
    assert slots["12"].kind == "trash"


def test_normalize_run_slots_skips_offdeck_string_location():
    # After moveLabware OFF_DECK the run engine reports the bare string
    # "offDeck" (not a dict) — this 500'd /status live on 2026-07-14.
    raw = {
        "labware": [
            {"loadName": "opentrons_96_tiprack_300ul", "location": {"slotName": "1"}},
            {"loadName": "corning_96_wellplate_360ul_flat", "location": "offDeck"},
        ]
    }
    slots = normalize_run_slots(raw)
    assert set(slots) == {"1"}


# ---------------------------------------------------------------------------
# build_deck — one case per §2.4 decision-table row
# ---------------------------------------------------------------------------


def _plate(load_name="corning_96_wellplate_360ul_flat"):
    return make_slot_labware(load_name)


def test_build_deck_all_empty():
    deck = build_deck(now=_NOW)
    assert deck.source == "empty"
    assert set(deck.slots) == {str(i) for i in range(1, 13)}
    assert all(s.slot_state == "empty" and s.labware is None for s in deck.slots.values())
    assert deck.timestamp == _NOW


def test_build_deck_declared_only():
    deck = build_deck(declared={"2": _plate()}, now=_NOW)
    assert deck.source == "declared"
    assert deck.slots["2"].slot_state == "declared"
    assert deck.slots["2"].source == "declared"
    assert deck.slots["2"].labware.kind == "96-well"
    assert deck.slots["1"].slot_state == "empty"


def test_build_deck_occupied_when_idle():
    deck = build_deck(repl={"2": _plate()}, busy=False, now=_NOW)
    assert deck.source == "repl"
    assert deck.slots["2"].slot_state == "occupied"
    assert deck.slots["2"].source == "repl"


def test_build_deck_in_use_when_busy():
    deck = build_deck(repl={"2": _plate()}, busy=True, now=_NOW)
    assert deck.slots["2"].slot_state == "in_use"


def test_build_deck_confirmed_declared_is_occupied_not_mismatch():
    deck = build_deck(repl={"2": _plate()}, declared={"2": _plate()}, now=_NOW)
    assert deck.slots["2"].slot_state == "occupied"
    assert deck.slots["2"].declared is None


def test_build_deck_mismatch():
    observed = make_slot_labware("corning_96_wellplate_360ul_flat")
    declared = make_slot_labware("corning_24_wellplate_3.4ml_flat")
    deck = build_deck(repl={"2": observed}, declared={"2": declared}, now=_NOW)
    slot = deck.slots["2"]
    assert slot.slot_state == "mismatch"
    assert slot.labware.kind == "96-well"       # observed wins the labware field
    assert slot.declared.kind == "24-well"      # the losing intent is surfaced


def test_build_deck_run_wins_over_repl():
    run_lw = make_slot_labware("opentrons_96_tiprack_300ul")
    repl_lw = make_slot_labware("corning_96_wellplate_360ul_flat")
    deck = build_deck(run={"2": run_lw}, repl={"2": repl_lw}, now=_NOW)
    assert deck.source == "run"
    assert deck.slots["2"].source == "run"
    assert deck.slots["2"].labware.kind == "tiprack"


def test_build_deck_module_only_slot_is_occupied():
    deck = build_deck(repl={"7": SlotModule(module_name="temperature module gen2")}, now=_NOW)
    assert deck.slots["7"].slot_state == "occupied"
    assert deck.slots["7"].module.module_name == "temperature module gen2"
    assert deck.slots["7"].labware is None


def test_build_deck_declared_module_renders_as_declared():
    # A sticky declared module (no live source) shows as the slot's module with
    # slot_state/source "declared".
    deck = build_deck(declared={"11": SlotModule(module_name="temperature module gen2")}, now=_NOW)
    assert deck.slots["11"].slot_state == "declared"
    assert deck.slots["11"].source == "declared"
    assert deck.slots["11"].module.module_name == "temperature module gen2"
    assert deck.slots["11"].labware is None
    assert deck.source == "declared"


def test_build_deck_declared_module_yields_to_live_labware():
    # A run/REPL source at the same slot wins over the declared (sticky) module.
    deck = build_deck(
        repl={"11": _plate()},
        declared={"11": SlotModule(module_name="temperature module gen2")},
        now=_NOW,
    )
    assert deck.slots["11"].source == "repl"
    assert deck.slots["11"].labware is not None
    assert deck.slots["11"].module is None


def test_build_deck_legacy_kind_declared_agrees_with_observed_load_name():
    # Declared via a legacy kind string ("96-well"); observed via a real load_name.
    declared = SlotLabware(kind="96-well", load_name="")
    observed = make_slot_labware("corning_96_wellplate_360ul_flat")
    deck = build_deck(repl={"2": observed}, declared={"2": declared}, now=_NOW)
    assert deck.slots["2"].slot_state == "occupied"  # not a mismatch


# ---------------------------------------------------------------------------
# build_deck — plate-well attachment (unifying PlateStateStore)
# ---------------------------------------------------------------------------


def _loaded_plate(plate_id="D"):
    return LoadedPlate(
        plate_id=plate_id,
        model="corning_96_wellplate_360ul_flat",
        loaded_at=_NOW,
        wells=[WellSample(well="A1", sample_id="caffeine", volume_ul=200.0)],
    )


def test_build_deck_attaches_plate_wells_to_observed_slot():
    observed = make_slot_labware("corning_96_wellplate_360ul_flat")
    deck = build_deck(
        repl={"2": observed},
        loaded_plate=_loaded_plate("D"),
        nickname_to_slot={"D": "2"},
        now=_NOW,
    )
    slot = deck.slots["2"]
    assert slot.slot_state == "occupied"
    assert slot.labware.plate_id == "D"
    assert slot.labware.wells[0].sample_id == "caffeine"


def test_build_deck_unplaced_plate_when_no_slot_mapping():
    # Plate is loaded but nickname_to_slot doesn't resolve — no slot carries wells.
    deck = build_deck(loaded_plate=_loaded_plate("D"), nickname_to_slot={}, now=_NOW)
    assert all(
        (s.labware is None or s.labware.wells is None) for s in deck.slots.values()
    )


def test_build_deck_loaded_plate_declares_its_slot_when_unobserved():
    # No run/repl source, but the orchestrator says a plate is loaded on slot 4.
    deck = build_deck(
        loaded_plate=_loaded_plate("D"),
        nickname_to_slot={"D": "4"},
        now=_NOW,
    )
    slot = deck.slots["4"]
    assert slot.slot_state == "declared"
    assert slot.labware.plate_id == "D"
    assert slot.labware.wells[0].well == "A1"


# ---------------------------------------------------------------------------
# DeckDeclarationStore
# ---------------------------------------------------------------------------


def _store(tmp_path):
    return DeckDeclarationStore(state_path=tmp_path / "deck.json")


def test_declare_with_load_names_and_persist(tmp_path):
    store = _store(tmp_path)
    result = store.declare({"2": "corning_96_wellplate_360ul_flat", "1": {"load_name": "opentrons_96_tiprack_300ul"}})
    assert result["2"].kind == "96-well"
    assert result["1"].kind == "tiprack"

    # Reload over the same file recovers the declaration (restart survival).
    reborn = DeckDeclarationStore(state_path=store.state_path)
    assert reborn.get()["2"].kind == "96-well"
    assert reborn.get()["1"].is_tiprack is True


def test_declare_legacy_kind_strings(tmp_path):
    store = _store(tmp_path)
    result = store.declare({"2": "96-well", "10": "24-well", "12": "waste"})
    assert result["2"].kind == "96-well"
    assert (result["2"].rows, result["2"].columns) == (8, 12)
    assert result["10"].kind == "24-well"
    assert result["12"].kind == "trash"  # "waste" alias


def test_declare_module_kind_string_and_persist(tmp_path):
    store = _store(tmp_path)
    result = store.declare({"11": "temperature_module"})
    assert isinstance(result["11"], SlotModule)
    assert result["11"].module_name == "temperature module gen2"

    # Survives a restart as a module (not misparsed as labware).
    reborn = DeckDeclarationStore(state_path=store.state_path)
    revived = reborn.get()["11"]
    assert isinstance(revived, SlotModule)
    assert revived.module_name == "temperature module gen2"


def test_declare_module_dict_carries_status_and_serial(tmp_path):
    store = _store(tmp_path)
    result = store.declare(
        {"11": {"module_name": "temperatureModuleV2", "status": "idle", "serial_number": "TDV20"}}
    )
    assert isinstance(result["11"], SlotModule)
    assert (result["11"].module_name, result["11"].status, result["11"].serial_number) == (
        "temperatureModuleV2",
        "idle",
        "TDV20",
    )


def test_declare_mixed_labware_and_module(tmp_path):
    store = _store(tmp_path)
    result = store.declare({"2": "corning_96_wellplate_360ul_flat", "11": "temperature_module"})
    assert result["2"].kind == "96-well"
    assert isinstance(result["11"], SlotModule)


def test_declare_none_value_clears_that_slot(tmp_path):
    store = _store(tmp_path)
    store.declare({"2": "96-well", "3": None})
    assert set(store.get()) == {"2"}


def test_declare_empty_mapping_clears_all(tmp_path):
    store = _store(tmp_path)
    store.declare({"2": "96-well"})
    store.declare({})
    assert store.get() == {}


def test_clear(tmp_path):
    store = _store(tmp_path)
    store.declare({"2": "96-well"})
    store.clear()
    assert store.get() == {}
    assert DeckDeclarationStore(state_path=store.state_path).get() == {}


def test_declare_invalid_slot_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.declare({"13": "96-well"})


def test_declare_object_without_load_name_or_kind_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.declare({"2": {"display_name": "x"}})


def test_corrupt_state_file_is_ignored(tmp_path):
    path = tmp_path / "deck.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = DeckDeclarationStore(state_path=path)  # must not raise
    assert store.get() == {}


def test_declared_store_feeds_build_deck(tmp_path):
    store = _store(tmp_path)
    store.declare({"2": "corning_96_wellplate_360ul_flat"})
    deck = build_deck(declared=store.get(), now=_NOW)
    assert deck.slots["2"].slot_state == "declared"
    assert deck.slots["2"].labware.kind == "96-well"
