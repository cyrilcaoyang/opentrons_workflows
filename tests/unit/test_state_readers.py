"""Unit tests for control/state_readers.get_labware_state.

Regression guard: a non-tiprack labware's `tip_length` property RAISES
LabwareIsNotTipRackError on the robot (it is not a missing attribute, so
getattr's default does not catch it). get_labware_state must not read it for
non-tipracks — otherwise the whole SSH snapshot 500s (found live 2026-07-14 on
ot2_complexation after a plate was loaded).
"""

from opentrons_server.control.state_readers import get_labware_state


class _FakeWell:
    well_name = "A1"
    max_volume = 360.0
    depth = 10.0


class _FakeLabware:
    """Minimal stand-in whose `tip_length` raises unless it is a tiprack."""

    def __init__(self, *, is_tiprack, load_name):
        self.is_tiprack = is_tiprack
        self.load_name = load_name
        self.name = load_name
        self.parent = "1"
        self.uri = f"opentrons/{load_name}/1"

    @property
    def tip_length(self):
        if not self.is_tiprack:
            raise Exception("LabwareIsNotTipRackError: has no tip length defined")
        return 51.1

    def wells(self):
        return [_FakeWell()]

    def rows(self):
        return [[_FakeWell()]]

    def columns(self):
        return [[_FakeWell()]]


def test_non_tiprack_does_not_read_tip_length():
    # Must not raise, and tip_length is None for a plate.
    state = get_labware_state(_FakeLabware(is_tiprack=False, load_name="corning_96_wellplate_360ul_flat"))
    assert state["info"]["is_tiprack"] is False
    assert state["info"]["tip_length"] is None


def test_tiprack_reads_tip_length():
    state = get_labware_state(_FakeLabware(is_tiprack=True, load_name="opentrons_96_tiprack_300ul"))
    assert state["info"]["is_tiprack"] is True
    assert state["info"]["tip_length"] == 51.1
