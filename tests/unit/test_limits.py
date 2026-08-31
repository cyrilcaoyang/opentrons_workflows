"""Layer-1 hardware limits (INTERLOCKS.md layer 1, STATUS_SPEC §9 v1.0).

Two tiers, tested as two things: the **static** bounds live in the request
models, so they must show up in the JSON schema an agent reads (that is the
whole point — a limit nobody can see is one an agent will violate); the
**live** bounds depend on the attached pipette and are refused pre-motion with
a structured 412.
"""

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from opentrons_server.gateway.deck import DeckDeclarationStore
from opentrons_server.gateway.limits import (
    MAX_PIPETTE_VOLUME_UL,
    MAX_WELL_OFFSET_MM,
    MAX_X_MM,
    MAX_Y_MM,
    MAX_Z_MM,
    OutOfEnvelope,
    check_volume,
)
from opentrons_server.gateway.models import (
    CoordinateLocation,
    LiquidMoveRequest,
    MoveToRequest,
    WellLocation,
)
from opentrons_server.gateway.plans import PLAN_ACTIONS
from opentrons_server.gateway.plate_state import PlateStateStore
from opentrons_server.gateway.service import OT2Service, OT2ServiceState
from opentrons_server.gateway.tip_state import TipStateStore

RECIPE = {
    "labware": [
        {"nickname": "plate_D", "loadname": "corning_96_wellplate_360ul_flat", "location": "1", "ot_default": True},
    ],
    "instruments": [
        {
            "nickname": "p300",
            "instrument_name": "p300_single_gen2",
            "mount": "left",
            "min_volume": 20.0,
            "max_volume": 300.0,
        }
    ],
    "modules": [],
}


@pytest.fixture
def service(tmp_path):
    svc = OT2Service(
        dry_run=False,
        plates=PlateStateStore(state_path=tmp_path / "plate.json"),
        decks=DeckDeclarationStore(state_path=tmp_path / "deck.json"),
        tips=TipStateStore(state_path=tmp_path / "tips.json"),
    )
    svc.control = Mock()
    svc.refresh_snapshot = Mock(return_value={})
    svc.state = OT2ServiceState.READY
    return svc


def _liquid(volume_ul: float, pipette: str = "p300") -> LiquidMoveRequest:
    return LiquidMoveRequest(
        pipette=pipette,
        volume_ul=volume_ul,
        location=WellLocation(labware_nickname="plate_D", position="A1"),
    )


# ---------------------------------------------------------------------------
# Static tier — in the schema, therefore visible
# ---------------------------------------------------------------------------


def test_the_reported_out_of_range_move_is_refused_before_motion():
    """A move 200 mm above a well used to validate here and fail at the robot,
    mid-run, with a tip on the head. Real protocols use single-digit offsets."""

    with pytest.raises(ValidationError):
        WellLocation(labware_nickname="plate_D", position="A1", top=200.0)

    WellLocation(labware_nickname="plate_D", position="A1", top=5.0)  # normal


def test_absolute_coordinates_are_bounded_by_the_gantry():
    for bad in ({"x": -1, "y": 0, "z": 0}, {"x": MAX_X_MM + 1, "y": 0, "z": 0},
                {"x": 0, "y": MAX_Y_MM + 1, "z": 0}, {"x": 0, "y": 0, "z": MAX_Z_MM + 1}):
        with pytest.raises(ValidationError):
            CoordinateLocation(**bad)

    CoordinateLocation(x=100.0, y=100.0, z=50.0)


def test_volume_is_bounded_in_both_directions():
    with pytest.raises(ValidationError):
        _liquid(-50.0)            # negative reached the robot untouched before
    with pytest.raises(ValidationError):
        _liquid(0.0)              # a no-op that still moves the head
    with pytest.raises(ValidationError):
        _liquid(MAX_PIPETTE_VOLUME_UL + 1)

    assert _liquid(50.0).volume_ul == 50.0


def test_the_limits_are_visible_to_an_agent():
    """`list_actions` hands the model `model_json_schema()`. A bound that is not
    in that schema is one the model has no way to respect — this is the `--help`
    surface, and it is generated from the enforcement rather than written
    alongside it, so the two cannot drift."""

    coords = PLAN_ACTIONS["move_to"].model.model_json_schema()["$defs"]["CoordinateLocation"]
    for axis, ceiling in (("x", MAX_X_MM), ("y", MAX_Y_MM), ("z", MAX_Z_MM)):
        assert coords["properties"][axis]["maximum"] == ceiling
        assert coords["properties"][axis]["minimum"] == 0.0
        assert coords["properties"][axis]["description"]

    volume = PLAN_ACTIONS["aspirate"].model.model_json_schema()["properties"]["volume_ul"]
    assert volume["maximum"] == MAX_PIPETTE_VOLUME_UL
    assert volume["exclusiveMinimum"] == 0.0
    assert "details.pipette_volumes" in volume["description"]

    # An optional field renders as anyOf[number, null], so the bound sits in the
    # numeric branch. Asserted in that shape deliberately: it is how a model has
    # to read it, and a naive top-level lookup finds nothing.
    well = PLAN_ACTIONS["aspirate"].model.model_json_schema()["$defs"]["WellLocation"]
    for field in ("top", "bottom"):
        prop = well["properties"][field]
        numeric = next(b for b in prop["anyOf"] if b.get("type") == "number")
        assert numeric["maximum"] == MAX_WELL_OFFSET_MM
        assert numeric["minimum"] == -MAX_WELL_OFFSET_MM
        assert prop["description"]


def test_move_to_still_accepts_a_normal_request():
    MoveToRequest(pipette="p300", coordinates=CoordinateLocation(x=10.0, y=10.0, z=10.0))
    MoveToRequest(
        pipette="p300",
        location=WellLocation(labware_nickname="plate_D", position="A1", top=2.0),
    )


# ---------------------------------------------------------------------------
# Live tier — depends on what is attached, so refused with 412
# ---------------------------------------------------------------------------


def test_check_volume_refuses_over_and_under_the_pipette():
    with pytest.raises(OutOfEnvelope) as over:
        check_volume("p300", 500.0, (20.0, 300.0))
    assert over.value.body["max_ul"] == 300.0
    assert over.value.body["requested_ul"] == 500.0

    with pytest.raises(OutOfEnvelope) as under:
        check_volume("p300", 5.0, (20.0, 300.0))
    assert "cannot meter this accurately" in under.value.body["detail"]

    check_volume("p300", 200.0, (20.0, 300.0))  # in band


def test_unknown_limits_pass_rather_than_block_the_lab():
    """Same reasoning as `_channels_for`'s fallback to 1: refusing every
    aspirate because an instrument probe is unreachable is a worse failure than
    the one this guards against. The binding is published so it stays
    diagnosable."""

    check_volume("p300", 999.0, None)


def test_service_refuses_an_over_capacity_aspirate_without_moving(service):
    service.setup_protocol(RECIPE)

    with pytest.raises(OutOfEnvelope):
        service.aspirate(_liquid(300.1))
    service.control.aspirate.assert_not_called()

    service.aspirate(_liquid(250.0))
    service.control.aspirate.assert_called_once()


def test_status_publishes_the_live_envelope(service):
    """So an agent can size a transfer before proposing it, rather than learning
    the limit from a refusal."""

    service.setup_protocol(RECIPE)
    volumes = service.get_status().details["pipette_volumes"]
    assert volumes["p300"] == {"min_ul": 20.0, "max_ul": 300.0}


# ---------------------------------------------------------------------------
# The declared-deck flow: no recipe, pipettes addressed by mount
# ---------------------------------------------------------------------------


def _probe(svc) -> None:
    """Stand in for the robot's GET /instruments, as probe_robot caches it."""

    svc._last_probe = {
        "reachable": True,
        "instruments": [
            {"mount": "left", "name": "p300_single_gen2", "channels": 1,
             "min_volume": 20.0, "max_volume": 300.0},
            {"mount": "right", "name": "p20_multi_gen2", "channels": 8,
             "min_volume": 1.0, "max_volume": 20.0},
        ],
    }


def test_volume_guard_works_without_a_setup(service):
    """A declared-deck robot has no recipe to bind from, so the guard has to
    reach the probe or it never engages at all.

    This is how ot2_complexation actually runs — `_channels_for` had the
    mount-addressed fallback from the start and `_volume_limits_for` did not, so
    the guard shipped dead on the one deployment that needed it.
    """

    _probe(service)
    assert service.session_recipe["instruments"] == []   # no setup has run

    assert service._volume_limits_for("left") == (20.0, 300.0)
    assert service._volume_limits_for("right") == (1.0, 20.0)

    with pytest.raises(OutOfEnvelope) as exc:
        check_volume("right", 50.0, service._volume_limits_for("right"))
    assert exc.value.body["max_ul"] == 20.0

    # An unknown mount is still unknown — it does not borrow another head's.
    assert service._volume_limits_for("p300") is None


def test_status_publishes_mount_addressed_limits(service):
    """Reporting less than is enforced is the same class of lie as reporting
    more: the preflight read "unbound" while the guard was live off the probe."""

    _probe(service)
    volumes = service.get_status().details["pipette_volumes"]
    assert volumes["left"] == {"min_ul": 20.0, "max_ul": 300.0}
    assert volumes["right"] == {"min_ul": 1.0, "max_ul": 20.0}
