"""Unit tests for the JSON-backed tip lifecycle store."""

import pytest

from opentrons_server.gateway.tip_state import (
    EMPTY,
    TipStateStore,
    TipUnavailable,
    covered_well_span,
    tip_well_order_96,
)


@pytest.fixture
def store(tmp_path):
    return TipStateStore(state_path=tmp_path / "tips.json")


def test_well_order_is_column_major():
    order = tip_well_order_96()
    assert order[:9] == ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1", "A2"]
    assert len(order) == 96
    assert len(set(order)) == 96


def test_register_creates_fresh_rack_and_persists(tmp_path):
    # A rack is keyed by the deck slot it sits in, not by a recipe nickname.
    store = TipStateStore(state_path=tmp_path / "tips.json")
    rack = store.register_rack("4")

    assert rack.tips["A1"] == "new"
    assert len(rack.tips) == 96

    reloaded = TipStateStore(state_path=tmp_path / "tips.json")
    assert reloaded.has_rack("4")
    assert reloaded.status("4", "A1") == "new"


def test_register_is_non_destructive_but_reset_renews(store):
    store.register_rack("4")
    store.set_status("4", "A1", "plate_A1")

    store.register_rack("4")
    assert store.status("4", "A1") == "plate_A1"

    store.reset_rack("4")
    assert store.status("4", "A1") == "new"


def test_validate_pick_contamination_guard(store):
    store.register_rack("4")

    # Fresh tip: anyone may pick.
    assert store.validate_pick("4", "A1") is None

    # Sample-touched tip: same sample ok, other sample refused, force overrides.
    store.set_status("4", "A1", "sample_X")
    assert store.validate_pick("4", "A1", sample_id="sample_X") == "sample_X"
    with pytest.raises(TipUnavailable) as exc:
        store.validate_pick("4", "A1", sample_id="sample_Y")
    assert exc.value.body["tip_status"] == "sample_X"
    assert exc.value.body["well"] == "A1"
    assert store.validate_pick("4", "A1", force=True) == "sample_X"

    # Empty well: force cannot conjure a tip.
    store.set_status("4", "A1", EMPTY)
    with pytest.raises(TipUnavailable):
        store.validate_pick("4", "A1", force=True)


def test_next_available_skips_used_and_matches_sample(store):
    store.register_rack("4")
    store.set_status("4", "A1", EMPTY)
    store.set_status("4", "B1", "sample_X")

    assert store.next_available("4") == "C1"
    # Same-sample reuse is preferred in scan order.
    assert store.next_available("4", sample_id="sample_X") == "B1"


def test_next_available_exhausted_raises(store):
    store.register_rack("3", wells=["A1", "B1"])
    store.set_status("3", "A1", EMPTY)
    store.set_status("3", "B1", "other")

    with pytest.raises(TipUnavailable) as exc:
        store.next_available("3", sample_id="mine")
    assert exc.value.body["rack"] == "3"


def test_unknown_rack_and_well_raise(store):
    with pytest.raises(LookupError):
        store.status("nope", "A1")
    store.register_rack("4")
    with pytest.raises(ValueError):
        store.set_status("4", "Z99", "x")


# ---- multi-channel coverage -------------------------------------------------


def test_covered_well_span_geometry():
    assert covered_well_span("A1", 1) == ["A1"]
    assert covered_well_span("C7", 1) == ["C7"]  # 1 channel: any row is fine
    assert covered_well_span("A1", 8) == ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"]
    assert covered_well_span("A12", 8) == [f"{r}12" for r in "ABCDEFGH"]
    assert covered_well_span("A3", 4) == ["A3", "B3", "C3", "D3"]

    with pytest.raises(ValueError):
        covered_well_span("E1", 8)  # would run off the bottom of the rack
    with pytest.raises(ValueError):
        covered_well_span("A1", 0)
    with pytest.raises(ValueError):
        covered_well_span("nope", 1)


def test_covered_wells_refuses_non_row_a_multichannel(store):
    store.register_rack("5")

    assert store.covered_wells("5", "B1", channels=1) == ["B1"]
    with pytest.raises(TipUnavailable) as exc:
        store.covered_wells("5", "B1", channels=8)
    assert exc.value.body["well"] == "B1"
    assert exc.value.body["channels"] == 8
    # No blocking well: the address itself is wrong, no column was inspected.
    assert exc.value.body["covered_wells"] is None
    assert "blocking_well" not in exc.value.body


def test_covered_wells_rejects_a_span_the_rack_cannot_hold(store):
    store.register_rack("3", wells=["A1", "B1"])
    with pytest.raises(ValueError):
        store.covered_wells("3", "A1", channels=8)


def test_validate_pick_requires_the_whole_column(store):
    store.register_rack("5")
    assert store.validate_pick("5", "A1", channels=8) is None

    # One consumed well in the column makes the whole column unpickable, and the
    # refusal names it — a partial column is not pickable by an 8-channel head.
    store.set_status("5", "C1", EMPTY)
    with pytest.raises(TipUnavailable) as exc:
        store.validate_pick("5", "A1", channels=8)
    body = exc.value.body
    assert body["blocking_well"] == "C1"
    assert body["well"] == "A1"
    assert body["tip_status"] == EMPTY
    assert body["channels"] == 8
    assert body["covered_wells"] == [f"{r}1" for r in "ABCDEFGH"]
    assert "C1" in body["detail"]

    # Single-channel picks in that column are unaffected.
    assert store.validate_pick("5", "A1", channels=1) is None


def test_validate_pick_multichannel_contamination_and_force(store):
    store.register_rack("5")
    store.set_status("5", "D1", "sample_X")

    with pytest.raises(TipUnavailable) as exc:
        store.validate_pick("5", "A1", channels=8, sample_id="sample_Y")
    assert exc.value.body["blocking_well"] == "D1"
    assert exc.value.body["tip_status"] == "sample_X"

    # Same-sample reuse and force clear it, exactly as for one channel.
    assert store.validate_pick("5", "A1", channels=8, sample_id="sample_X") is None
    assert store.validate_pick("5", "A1", channels=8, force=True) is None
    # The return value is the *addressed* well's prior status, not the column's.
    store.set_status("5", "A1", "sample_X")
    assert (
        store.validate_pick("5", "A1", channels=8, sample_id="sample_X")
        == "sample_X"
    )


def test_next_available_multichannel_steps_by_column(store):
    store.register_rack("5")
    assert store.next_available("5", channels=8) == "A1"

    # A single consumed tip retires the whole column for an 8-channel head: the
    # next start is A2, never B1 (which would put 7 channels over empty holes).
    store.set_statuses("5", [f"{r}1" for r in "ABCDEFGH"], EMPTY)
    assert store.next_available("5", channels=8) == "A2"
    store.set_status("5", "H2", "sample_X")
    assert store.next_available("5", channels=8) == "A3"
    # ... while a single-channel head happily takes what is left of column 2.
    assert store.next_available("5", channels=1) == "A2"
    # Same-sample reuse still counts as pickable for the whole span.
    assert store.next_available("5", channels=8, sample_id="sample_X") == "A2"


def test_next_available_multichannel_exhausted_raises(store):
    store.register_rack("5")
    for column in range(1, 13):
        store.set_status("5", f"H{column}", EMPTY)

    with pytest.raises(TipUnavailable) as exc:
        store.next_available("5", channels=8)
    assert exc.value.body["channels"] == 8
    assert exc.value.body["rack"] == "5"
    # Every column still has 7 fresh tips for a single-channel pipette.
    assert store.next_available("5", channels=1) == "A1"


def test_next_available_single_channel_body_is_unchanged(store):
    store.register_rack("3", wells=["A1"])
    store.set_status("3", "A1", EMPTY)

    with pytest.raises(TipUnavailable) as exc:
        store.next_available("3")
    assert "channels" not in exc.value.body


def test_set_statuses_validates_before_mutating(store):
    store.register_rack("3", wells=["A1", "B1"])
    with pytest.raises(ValueError):
        store.set_statuses("3", ["A1", "Z9"], EMPTY)
    assert store.status("3", "A1") == "new"  # nothing was written

    store.set_statuses("3", ["A1", "B1"], EMPTY)
    assert store.summary()["3"]["empty"] == 2


def test_summary_counts(store):
    store.register_rack("3", wells=["A1", "B1", "C1"])
    store.set_status("3", "A1", EMPTY)
    store.set_status("3", "B1", "sample_X")

    summary = store.summary()["3"]
    assert summary["total"] == 3
    assert summary["available"] == 1
    assert summary["empty"] == 1
    assert summary["touched"] == 1
    assert summary["tips"] == {"A1": "empty", "B1": "sample_X"}
