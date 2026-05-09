"""High-level OT-2/Flex command wrapper."""

from __future__ import annotations

from typing import Any, Dict

from ..opentrons_control import OpentronsControl as _LegacyOpentronsControl


class OT2Control(_LegacyOpentronsControl):
    """Named control wrapper for the new package layout.

    The legacy ``OpentronsControl`` class initializes a protocol session during
    construction and exposes the current robot verbs. This subclass provides
    clearer method names expected by the gateway layer without breaking the old
    API.
    """

    def initialize_protocol(self, simulation: bool = False) -> None:
        """Initialize or reinitialize the remote Opentrons protocol context."""

        self._get_protocol(simulation)

    def shutdown(self) -> None:
        """Close the active robot session without issuing extra workflow logic."""

        self._disconnect()

    def move_labware(self, labware_nickname: str, new_location: str) -> None:
        """Move labware using the robot gripper when available."""

        self.move_labware_w_gripper(labware_nickname, new_location)

    def setup_protocol(
        self,
        *,
        labware: list[Dict[str, Any]] | None = None,
        instruments: list[Dict[str, Any]] | None = None,
        modules: list[Dict[str, Any]] | None = None,
    ) -> None:
        """Load labware, instruments, and modules into the active protocol."""

        for labware_config in labware or []:
            self.load_labware(labware_config)
        for instrument_config in instruments or []:
            self.load_instrument(instrument_config)
        for module_config in modules or []:
            self.load_module(module_config)


OpentronsControl = OT2Control

__all__ = ["OT2Control", "OpentronsControl"]
