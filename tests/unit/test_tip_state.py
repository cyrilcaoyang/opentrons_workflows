"""Unit tests for the JSON-backed tip lifecycle store."""

import pytest

from opentrons_server.gateway.tip_state import (
    EMPTY,
    TipStateStore,
    TipUnavailable,
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
    store = TipStateStore(state_path=tmp_path / "tips.json")
    rack = store.register_rack("tips_300")

    assert rack.tips["A1"] == "new"
    assert len(rack.tips) == 96

    reloaded = TipStateStore(state_path=tmp_path / "tips.json")
    assert reloaded.has_rack("tips_300")
    assert reloaded.status("tips_300", "A1") == "new"


def test_register_is_non_destructive_but_reset_renews(store):
    store.register_rack("tips_300")
    store.set_status("tips_300", "A1", "plate_A1")

    store.register_rack("tips_300")
    assert store.status("tips_300", "A1") == "plate_A1"

    store.reset_rack("tips_300")
    assert store.status("tips_300", "A1") == "new"


def test_validate_pick_contamination_guard(store):
    store.register_rack("tips_300")

    # Fresh tip: anyone may pick.
    assert store.validate_pick("tips_300", "A1") is None

    # Sample-touched tip: same sample ok, other sample refused, force overrides.
    store.set_status("tips_300", "A1", "sample_X")
    assert store.validate_pick("tips_300", "A1", sample_id="sample_X") == "sample_X"
    with pytest.raises(TipUnavailable) as exc:
        store.validate_pick("tips_300", "A1", sample_id="sample_Y")
    assert exc.value.body["tip_status"] == "sample_X"
    assert exc.value.body["well"] == "A1"
    assert store.validate_pick("tips_300", "A1", force=True) == "sample_X"

    # Empty well: force cannot conjure a tip.
    store.set_status("tips_300", "A1", EMPTY)
    with pytest.raises(TipUnavailable):
        store.validate_pick("tips_300", "A1", force=True)


def test_next_available_skips_used_and_matches_sample(store):
    store.register_rack("tips_300")
    store.set_status("tips_300", "A1", EMPTY)
    store.set_status("tips_300", "B1", "sample_X")

    assert store.next_available("tips_300") == "C1"
    # Same-sample reuse is preferred in scan order.
    assert store.next_available("tips_300", sample_id="sample_X") == "B1"


def test_next_available_exhausted_raises(store):
    store.register_rack("mini", wells=["A1", "B1"])
    store.set_status("mini", "A1", EMPTY)
    store.set_status("mini", "B1", "other")

    with pytest.raises(TipUnavailable) as exc:
        store.next_available("mini", sample_id="mine")
    assert exc.value.body["rack"] == "mini"


def test_unknown_rack_and_well_raise(store):
    with pytest.raises(LookupError):
        store.status("nope", "A1")
    store.register_rack("tips_300")
    with pytest.raises(ValueError):
        store.set_status("tips_300", "Z99", "x")


def test_summary_counts(store):
    store.register_rack("mini", wells=["A1", "B1", "C1"])
    store.set_status("mini", "A1", EMPTY)
    store.set_status("mini", "B1", "sample_X")

    summary = store.summary()["mini"]
    assert summary["total"] == 3
    assert summary["available"] == 1
    assert summary["empty"] == 1
    assert summary["touched"] == 1
    assert summary["tips"] == {"A1": "empty", "B1": "sample_X"}
