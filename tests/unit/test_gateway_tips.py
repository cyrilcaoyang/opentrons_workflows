"""Tip lifecycle folded into the gateway: setup registration, pick validation,
sample marking on aspirate/dispense, empty on drop, /status surfacing, 412s."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from opentrons_server.gateway.api import create_app
from opentrons_server.gateway.deck import DeckDeclarationStore
from opentrons_server.gateway.models import (
    LiquidMoveRequest,
    TipRequest,
    WellLocation,
    WellSample,
)
from opentrons_server.gateway.plate_state import PlateStateStore
from opentrons_server.gateway.service import OT2Service, OT2ServiceState
from opentrons_server.gateway.tip_state import TipStateStore, TipUnavailable

RECIPE = {
    "labware": [
        {"nickname": "plate_D", "loadname": "corning_96_wellplate_360ul_flat", "location": "1", "ot_default": True},
        {"nickname": "tips_300", "loadname": "opentrons_96_tiprack_300ul", "location": "4", "ot_default": True},
    ],
    "instruments": [
        {"nickname": "p300", "instrument_name": "p300_single_gen2", "mount": "left", "ot_default": True}
    ],
    "modules": [],
}

# Both mounts loaded, as on ot2_complexation: a single-channel p300 and an
# 8-channel p20. `channels` is declared explicitly here — the robot's own
# GET /instruments report is the other source (see the by-mount test).
MULTI_RECIPE = {
    "labware": [
        {"nickname": "plate_D", "loadname": "corning_96_wellplate_360ul_flat", "location": "1", "ot_default": True},
        {"nickname": "tips_300", "loadname": "opentrons_96_tiprack_300ul", "location": "4", "ot_default": True},
        {"nickname": "tips_20", "loadname": "opentrons_96_tiprack_20ul", "location": "5", "ot_default": True},
    ],
    "instruments": [
        {"nickname": "p300", "instrument_name": "p300_single_gen2", "mount": "left", "channels": 1},
        {"nickname": "p20", "instrument_name": "p20_multi_gen2", "mount": "right", "channels": 8},
    ],
    "modules": [],
}


def _recipe_without_channels() -> dict:
    """``MULTI_RECIPE`` with the explicit counts stripped, leaving only mounts."""

    return {
        **MULTI_RECIPE,
        "instruments": [
            {k: v for k, v in inst.items() if k != "channels"}
            for inst in MULTI_RECIPE["instruments"]
        ],
    }


@pytest.fixture
def service(tmp_path):
    svc = OT2Service(
        dry_run=False,
        plates=PlateStateStore(state_path=tmp_path / "plate.json"),
        decks=DeckDeclarationStore(state_path=tmp_path / "deck.json"),
        tips=TipStateStore(state_path=tmp_path / "tips.json"),
    )
    control = Mock()
    svc.control = control
    svc.refresh_snapshot = Mock(return_value={})
    svc.state = OT2ServiceState.READY
    return svc


def _pick(service, well=None, sample_id=None, force=False, pipette="p300", rack="tips_300"):
    service.pick_up_tip(
        TipRequest(
            pipette=pipette,
            labware_nickname=rack,
            position=well,
            sample_id=sample_id,
            force=force,
        )
    )


def _move(service, kind, labware, well, pipette="p300"):
    request = LiquidMoveRequest(
        pipette=pipette,
        volume_ul=50,
        location=WellLocation(labware_nickname=labware, position=well),
    )
    getattr(service, kind)(request)


def test_setup_registers_tipracks_only(service):
    service.setup_protocol(RECIPE)

    # A rack is identified by the deck slot it sits in: tips_300 is on slot 4.
    assert service.tips.has_rack("4")
    assert not service.tips.has_rack("1")  # slot 1 holds the plate


def test_setup_registration_survives_used_tips(service):
    service.setup_protocol(RECIPE)
    service.tips.set_status("4", "A1", "sample_X")

    service.setup_protocol(RECIPE)  # e.g. re-setup after a gateway restart
    assert service.tips.status("4", "A1") == "sample_X"


def test_pick_aspirate_dispense_drop_lifecycle(service):
    service.setup_protocol(RECIPE)

    _pick(service)  # auto-pick -> A1 (column-major first well)
    mounted = service._mounted_tips["p300"]
    assert (mounted["rack"], mounted["well"]) == ("4", "A1")
    service.control.get_location_from_labware.assert_called_with("tips_300", "A1")

    _move(service, "aspirate", "reservoir", "A1")
    assert service.tips.status("4", "A1") == "reservoir_A1"

    _move(service, "dispense", "plate_D", "B2")
    assert service.tips.status("4", "A1") == "plate_D_B2"

    service.drop_tip(TipRequest(pipette="p300"))
    assert service.tips.status("4", "A1") == "empty"
    assert "p300" not in service._mounted_tips

    # Next auto-pick skips the emptied well.
    _pick(service)
    assert service._mounted_tips["p300"]["well"] == "B1"


def test_pick_refuses_cross_sample_reuse(service):
    service.setup_protocol(RECIPE)
    service.tips.set_status("4", "A1", "sample_X")

    with pytest.raises(TipUnavailable):
        _pick(service, well="A1", sample_id="sample_Y")
    # Same sample or force is allowed.
    _pick(service, well="A1", sample_id="sample_X")
    _pick(service, well="A1", force=True)


def test_sample_id_resolves_from_loaded_plate(service):
    service.setup_protocol(RECIPE)
    service.load_plate(
        plate_id="plate_D",
        model="corning_96_wellplate_360ul_flat",
        wells=[WellSample(well="B2", sample_id="caffeine-001")],
    )

    _pick(service)
    _move(service, "dispense", "plate_D", "B2")
    assert service.tips.status("4", "A1") == "caffeine-001"


def test_touching_the_rack_itself_does_not_mark(service):
    service.setup_protocol(RECIPE)
    _pick(service)
    _move(service, "aspirate", "tips_300", "A1")
    assert service.tips.status("4", "A1") == "new"


def test_untracked_rack_behaves_as_before(service):
    # No setup -> nothing registered; explicit position passes straight through.
    service.pick_up_tip(
        TipRequest(pipette="p300", labware_nickname="tips_300", position="C5")
    )
    service.control.get_location_from_labware.assert_called_with("tips_300", "C5")
    assert service._mounted_tips == {}


# ---- multi-channel pipettes -------------------------------------------------
#
# An 8-channel head sent to a row-A well removes the whole column, so tracking
# only the addressed well under-counts by seven. These pin the service side of
# that: binding the channel count, consuming the covered set, and refusing the
# picks a column-wide head cannot make.

COLUMN_1 = [f"{row}1" for row in "ABCDEFGH"]


def _column(number: int) -> list:
    return [f"{row}{number}" for row in "ABCDEFGH"]


def _statuses(service, wells, rack="5"):
    return {service.tips.status(rack, well) for well in wells}


def test_setup_binds_channel_count_from_the_recipe(service):
    service.setup_protocol(MULTI_RECIPE)

    assert service._pipette_channels == {"p300": 1, "p20": 8}
    assert service._channels_for("p300") == 1
    assert service._channels_for("p20") == 8


def test_setup_binds_channel_count_from_the_robot_probe_by_mount(service):
    # No `channels` in the recipe: the count comes from GET /instruments, joined
    # to the nickname by the mount the recipe declares.
    service._last_probe = {
        "instruments": [
            {"mount": "left", "model": "p300_single_gen2", "channels": 1},
            {"mount": "right", "model": "p20_multi_gen2", "channels": 8},
        ]
    }
    service.setup_protocol(_recipe_without_channels())

    assert service._pipette_channels == {"p300": 1, "p20": 8}


def test_unbound_pipette_falls_back_to_single_channel(service):
    # Neither an explicit count nor a reachable probe: behave exactly as before
    # multi-channel tracking existed rather than refusing picks outright.
    service.setup_protocol(_recipe_without_channels())

    assert service._channels_for("p20") == 1
    _pick(service, well="A1", pipette="p20", rack="tips_20")
    assert service._mounted_tips["p20"]["wells"] == ["A1"]


def test_multichannel_pick_and_drop_empties_the_whole_column(service):
    """The 2026-08-04 ot2_complexation run: p300 at A1 (1ch) + p20 at A1 (8ch)."""

    service.setup_protocol(MULTI_RECIPE)

    _pick(service, well="A1")
    _pick(service, well="A1", pipette="p20", rack="tips_20")
    assert service._mounted_tips["p20"]["wells"] == COLUMN_1
    assert service._mounted_tips["p20"]["channels"] == 8

    service.drop_tip(TipRequest(pipette="p300"))
    service.drop_tip(TipRequest(pipette="p20"))

    summary = service.tips.summary()
    # 9 tips left the deck, and the rack now says so: 88, not the 95 the
    # addressed-well-only tracker reported.
    assert summary["5"]["available"] == 88
    assert sorted(summary["5"]["tips"]) == COLUMN_1
    assert _statuses(service, COLUMN_1) == {"empty"}
    # The single-channel rack is unaffected.
    assert summary["4"]["available"] == 95
    assert summary["4"]["tips"] == {"A1": "empty"}


def test_multichannel_auto_pick_advances_by_column(service):
    service.setup_protocol(MULTI_RECIPE)

    _pick(service, pipette="p20", rack="tips_20")
    assert service._mounted_tips["p20"]["well"] == "A1"
    service.drop_tip(TipRequest(pipette="p20"))

    # A2, never B1 — B1's tip is already on channel two, so a B1 start would
    # descend on seven empty holes.
    _pick(service, pipette="p20", rack="tips_20")
    assert service._mounted_tips["p20"]["well"] == "A2"
    assert service._mounted_tips["p20"]["wells"] == _column(2)


def test_multichannel_sample_marking_stamps_the_whole_column(service):
    service.setup_protocol(MULTI_RECIPE)
    _pick(service, well="A1", pipette="p20", rack="tips_20")

    _move(service, "aspirate", "reservoir", "A1", pipette="p20")
    assert _statuses(service, COLUMN_1) == {"reservoir_A1"}

    # Pick/drop round-trips: the column goes empty, not back to a mixed state.
    service.drop_tip(TipRequest(pipette="p20"))
    assert _statuses(service, COLUMN_1) == {"empty"}


def test_multichannel_pick_refuses_a_partial_column(service):
    service.setup_protocol(MULTI_RECIPE)
    service.tips.set_status("5", "E1", "empty")

    with pytest.raises(TipUnavailable) as exc:
        _pick(service, well="A1", pipette="p20", rack="tips_20")
    body = exc.value.body
    assert body["blocking_well"] == "E1"
    assert body["well"] == "A1"
    assert body["channels"] == 8
    assert body["covered_wells"] == COLUMN_1
    assert "E1" in body["detail"]
    # Nothing moved and nothing was recorded.
    assert "p20" not in service._mounted_tips

    # A single-channel pipette can still take what is left of that column.
    _pick(service, well="A1")
    assert service._mounted_tips["p300"]["wells"] == ["A1"]


def test_multichannel_pick_refuses_a_non_row_a_address(service):
    service.setup_protocol(MULTI_RECIPE)

    with pytest.raises(TipUnavailable) as exc:
        _pick(service, well="B1", pipette="p20", rack="tips_20")
    assert exc.value.body["well"] == "B1"
    assert exc.value.body["channels"] == 8
    assert "row A" in exc.value.body["detail"]
    assert "p20" not in service._mounted_tips

    # ... while any row is fine for a single-channel head.
    _pick(service, well="B1")
    assert service._mounted_tips["p300"]["wells"] == ["B1"]


def test_multichannel_exhaustion_refuses_once_every_column_is_broken(service):
    service.setup_protocol(MULTI_RECIPE)
    for column in range(1, 13):
        service.tips.set_status("5", f"H{column}", "empty")

    with pytest.raises(TipUnavailable) as exc:
        _pick(service, pipette="p20", rack="tips_20")  # auto-pick
    assert exc.value.body["channels"] == 8
    assert exc.value.body["well"] is None

    # 84 tips remain, all reachable by a single-channel head.
    assert service.tips.next_available("5", channels=1) == "A1"


def test_status_surfaces_tip_state(service):
    service.setup_protocol(RECIPE)
    _pick(service)
    _move(service, "aspirate", "reservoir", "A1")

    details = service.get_status().details
    assert details["tip_racks"]["4"]["available"] == 95
    assert details["tip_racks"]["4"]["tips"] == {"A1": "reservoir_A1"}
    assert details["mounted_tips"]["p300"]["well"] == "A1"


def test_status_surfaces_pipette_channels(service):
    """An unbound 8-channel head silently tracked as 1 is the whole bug, so the
    live binding is published for diagnosis."""

    service.setup_protocol(MULTI_RECIPE)
    _pick(service, well="A1", pipette="p20", rack="tips_20")

    details = service.get_status().details
    assert details["pipette_channels"] == {"p300": 1, "p20": 8}
    assert details["mounted_tips"]["p20"]["wells"] == COLUMN_1


def test_allowed_actions_include_tips_reset(service):
    assert "tips.reset" in service.allowed_actions()


def test_api_pick_up_tip_412_and_tips_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("OT2_TIP_STATE_PATH", str(tmp_path / "tips.json"))
    monkeypatch.setenv("OT2_PLATE_STATE_PATH", str(tmp_path / "plate.json"))
    monkeypatch.setenv("OT2_DECK_STATE_PATH", str(tmp_path / "deck.json"))
    app = create_app(dry_run=True, enforce_claims=False)
    client = TestClient(app)
    service = app.state.service
    # A protocol setup is what maps the nickname the caller sends to the slot
    # the tracker keys by — exercise that resolution, not a pre-seeded rack.
    service.setup_protocol(RECIPE)
    service.tips.set_status("4", "A1", "sample_X")

    response = client.post(
        "/control/pick-up-tip",
        json={
            "pipette": "p300",
            "labware_nickname": "tips_300",
            "position": "A1",
            "sample_id": "sample_Y",
        },
    )
    assert response.status_code == 412
    body = response.json()
    assert body["rack"] == "4"
    assert body["tip_status"] == "sample_X"
    # Precondition refusals never mutate last_error (STATUS_SPEC §6.3).
    assert client.get("/status").json()["last_error"] is None

    reset = client.post("/control/tips/reset", json={"slot": "4"})
    assert reset.status_code == 200
    assert reset.json()["tips"]["A1"] == "new"


def test_api_multichannel_partial_column_412_body(tmp_path, monkeypatch):
    """The 412 body is what a caller branches on, so the multi-channel fields
    (STATUS_SPEC §6.1: distinguishable by shape) have to survive the API layer."""

    monkeypatch.setenv("OT2_TIP_STATE_PATH", str(tmp_path / "tips.json"))
    monkeypatch.setenv("OT2_PLATE_STATE_PATH", str(tmp_path / "plate.json"))
    monkeypatch.setenv("OT2_DECK_STATE_PATH", str(tmp_path / "deck.json"))
    app = create_app(dry_run=True, enforce_claims=False)
    client = TestClient(app)
    service = app.state.service
    service.setup_protocol(MULTI_RECIPE)
    service.tips.set_status("5", "E1", "empty")

    response = client.post(
        "/control/pick-up-tip",
        json={"pipette": "p20", "labware_nickname": "tips_20", "position": "A1"},
    )
    assert response.status_code == 412
    body = response.json()
    assert body["blocking_well"] == "E1"
    assert body["covered_wells"] == [f"{row}1" for row in "ABCDEFGH"]
    assert body["channels"] == 8
    # A precondition refusal never mutates last_error (STATUS_SPEC §6.3).
    assert client.get("/status").json()["last_error"] is None


# ---- slot keying -----------------------------------------------------------
#
# A tip rack's identity is the deck slot it sits in. It carries no sample and
# no history worth naming, and what an operator points at — and refills — is
# "the rack in slot 4".


def test_declaring_a_tiprack_starts_tracking_it(service):
    """The reported bug: three racks declared on the deck, none of them in the
    panel, because registration only ever happened from a /control/setup
    recipe. A declaration is exactly the fact the tracker needs."""

    service.declare_deck(
        {
            "7": "opentrons_96_filtertiprack_10ul",
            "9": "opentrons_96_tiprack_300ul",
            "1": "corning_96_wellplate_360ul_flat",
        }
    )

    assert sorted(service.tips.racks()) == ["7", "9"]  # the plate is not a rack
    assert service.tips.summary()["7"]["available"] == 96


def test_tracking_survives_a_restart_without_a_setup(service, tmp_path):
    """Slot keying is what makes this work: the declared deck is persisted
    while session_recipe is in-memory and lost on every restart."""

    service.declare_deck({"7": "opentrons_96_filtertiprack_10ul"})
    service.tips.set_status("7", "A1", "empty")

    reborn = OT2Service(
        dry_run=False,
        plates=PlateStateStore(state_path=service.plates.state_path),
        decks=DeckDeclarationStore(state_path=service.decks.state_path),
        tips=TipStateStore(state_path=service.tips.state_path),
    )

    assert reborn.tips.has_rack("7")
    assert reborn.tips.status("7", "A1") == "empty"


def test_a_protocol_call_still_addresses_labware_by_nickname(service):
    # The robot is driven by nickname; only the tracker uses slots. Both must
    # hold at once, or the pick lands on the wrong labware.
    service.setup_protocol(RECIPE)

    _pick(service, well="A1")

    service.control.get_location_from_labware.assert_called_with("tips_300", "A1")
    assert service._mounted_tips["p300"]["rack"] == "4"


def test_refill_accepts_the_slot_or_the_legacy_nickname(service):
    service.setup_protocol(RECIPE)
    service.tips.set_status("4", "A1", "empty")

    service.reset_tip_rack("4")
    assert service.tips.status("4", "A1") == "new"

    service.tips.set_status("4", "B1", "empty")
    service.reset_tip_rack("tips_300")  # resolved through the session recipe
    assert service.tips.status("4", "B1") == "new"


def test_a_rack_cannot_be_registered_under_a_non_slot_key(service):
    # The loader drops non-slot keys, so accepting one on write would be a
    # silent write-then-lose across the next restart.
    with pytest.raises(ValueError, match="keyed by deck slot"):
        service.tips.register_rack("tips_300")


def test_legacy_nickname_keyed_racks_are_dropped_on_load(tmp_path):
    """Ghost entries from before slot keying: they tracked no deck slot, so
    nothing could join them — they showed in the panel while the racks actually
    on the deck showed nothing."""

    import json

    path = tmp_path / "tips.json"
    path.write_text(
        json.dumps(
            {
                "racks": {
                    "tips_20": {
                        "nickname": "tips_20",
                        "tips": {"A1": "new"},
                        "registered_at": "2026-08-05T00:00:00+00:00",
                    },
                    "4": {
                        "nickname": "4",
                        "tips": {"A1": "empty"},
                        "registered_at": "2026-08-05T00:00:00+00:00",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    store = TipStateStore(state_path=path)

    assert sorted(store.racks()) == ["4"]
    assert store.status("4", "A1") == "empty"  # the real one is untouched
