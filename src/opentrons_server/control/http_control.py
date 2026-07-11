"""OT2Control-compatible adapter backed by the run-engine HTTP client.

``OT2HttpControl`` mirrors the subset of :class:`OT2Control`'s method surface that
``gateway/service.py`` actually calls, so wiring it into the service is close to a
constructor swap. It translates the REPL's stateful, immediate-execution model into
run-engine commands via :class:`RunEngineClient`.

STATUS: NOT wired into ``service.py`` yet, and NOT exercised against a real robot.
See ``docs/HTTP_DRIVE_PLAN.md``. Known semantic gaps vs. the SSH REPL (each flagged
inline and in the plan doc):

- **Flow rates.** ``aspirate``/``dispense``/``blow_out`` take no flow rate in the
  ``OT2Control`` surface, but the run engine *requires* ``flowRate``. This adapter
  injects a configurable default (constructor args). Until ``LiquidMoveRequest``
  grows a ``flow_rate`` field these defaults are a stopgap, not per-call truth.
- **Explicit tips.** The run engine's ``pickUpTip`` needs an explicit labware+well;
  there is no implicit "next tip" tracking like the protocol API. ``pick_up_tip``
  therefore requires a pending location (set via ``get_location_from_labware``) and
  raises otherwise.
- **Drop location.** ``drop_tip`` uses the pending location if set, else falls back
  to ``dropTipInPlace`` — which drops where the pipette is, NOT in the trash. A
  faithful drop-to-trash needs the trash labware id (future work).
- **Default labware namespace/version.** Built-in labware is assumed
  ``namespace="opentrons"``, ``version=1``; labware needing another version must
  carry ``namespace``/``version`` keys in its config.
- **Object ids** are client-supplied and equal to the caller's nickname, so nicknames
  must stay unique within a run.
- **Modules-before-labware.** Setup order mirrors ``OT2Control`` (labware, then
  instruments, then modules); labware loaded onto a module would need the module
  first — flagged, not handled.
- No ``invoke`` / geometry readouts; deck snapshots come from :meth:`run_snapshot`.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .http_run import OFF_DECK, RunEngineClient, RunEngineCommands, deck_slot

_DEFAULT_ASPIRATE_FLOW = float(os.getenv("OT2_HTTP_ASPIRATE_FLOW_UL_S", "150"))
_DEFAULT_DISPENSE_FLOW = float(os.getenv("OT2_HTTP_DISPENSE_FLOW_UL_S", "300"))
_DEFAULT_BLOWOUT_FLOW = float(os.getenv("OT2_HTTP_BLOWOUT_FLOW_UL_S", "100"))

_OFF_DECK_ALIASES = {"OFF_DECK", "offDeck", "off_deck", OFF_DECK}


class OT2HttpControl:
    """Run-engine-backed drop-in for the ``OT2Control`` methods the gateway uses."""

    def __init__(
        self,
        client: RunEngineClient,
        *,
        aspirate_flow_rate: float = _DEFAULT_ASPIRATE_FLOW,
        dispense_flow_rate: float = _DEFAULT_DISPENSE_FLOW,
        blow_out_flow_rate: float = _DEFAULT_BLOWOUT_FLOW,
    ) -> None:
        self.client = client
        self.aspirate_flow_rate = aspirate_flow_rate
        self.dispense_flow_rate = dispense_flow_rate
        self.blow_out_flow_rate = blow_out_flow_rate
        # nickname -> engine id (client-supplied == nickname; kept explicit so a
        # future switch to engine-assigned ids is a one-place change).
        self._labware_ids: Dict[str, str] = {}
        self._pipette_ids: Dict[str, str] = {}
        # Stateful "current location" set by get_location_from_labware, consumed by
        # the next aspirate/dispense/pick_up_tip/blow_out — mirrors the REPL model.
        self._pending: Optional[Dict[str, Any]] = None

    # -- session lifecycle -------------------------------------------------

    def initialize_protocol(self, simulation: bool = False) -> None:
        """Create the run. ``simulation`` is decided by the target robot-server,
        not here, so the flag is accepted for signature parity and ignored."""
        self.client.create_run()

    def shutdown(self) -> None:
        self.client.stop_run()
        self.client.close()

    def close_session(self) -> None:
        self.shutdown()

    # -- setup / loading ---------------------------------------------------

    def setup_protocol(
        self,
        *,
        labware: Optional[List[Dict[str, Any]]] = None,
        instruments: Optional[List[Dict[str, Any]]] = None,
        modules: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        for labware_config in labware or []:
            self.load_labware(labware_config)
        for instrument_config in instruments or []:
            self.load_instrument(instrument_config)
        for module_config in modules or []:
            self.load_module(module_config)

    def load_labware(self, labware: Dict[str, Any]) -> str:
        nickname = labware["nickname"]
        location = self._location(labware["location"])
        if labware["ot_default"]:
            load_name = labware["loadname"]
            namespace = labware.get("namespace", "opentrons")
            version = int(labware.get("version", 1))
        else:
            definition = labware["config"]
            self.client.add_labware_definition(definition)
            params = definition["parameters"]
            load_name = params["loadName"]
            namespace = definition["namespace"]
            version = int(definition["version"])
        self.client.execute(
            RunEngineCommands.load_labware(
                load_name, namespace, version, location, labware_id=nickname
            )
        )
        self._labware_ids[nickname] = nickname
        return nickname

    def load_instrument(self, instrument: Dict[str, Any]) -> str:
        if not instrument.get("ot_default", True):
            raise NotImplementedError("custom instrument not implemented")
        nickname = instrument["nickname"]
        self.client.execute(
            RunEngineCommands.load_pipette(
                instrument["instrument_name"], instrument["mount"], pipette_id=nickname
            )
        )
        self._pipette_ids[nickname] = nickname
        return nickname

    def load_module(self, module: Dict[str, Any]) -> str:
        nickname = module["nickname"]
        self.client.execute(
            RunEngineCommands.load_module(
                module["module_name"], self._location(module["location"]), module_id=nickname
            )
        )
        # NOTE: OT2Control also loads an adapter onto the module; the run-engine
        # loadModule has no adapter concept here — flagged, not handled.
        return nickname

    # -- location tracking -------------------------------------------------

    def get_location_from_labware(
        self,
        labware_nickname: str,
        position: str,
        top: float = 0,
        bottom: float = 0,
        center: float = 0,
    ) -> None:
        """Stash the pending well location, mirroring OT2Control's precedence
        (top, then bottom, then center, else the well top)."""
        if top:
            origin, z = "top", top
        elif bottom:
            origin, z = "bottom", bottom
        elif center:
            origin, z = "center", 0.0
        else:
            origin, z = "top", 0.0
        self._pending = {
            "labware_id": self._labware_id(labware_nickname),
            "well_name": position,
            "origin": origin,
            "offset": {"x": 0, "y": 0, "z": z},
        }

    # -- liquid handling ---------------------------------------------------

    def aspirate(self, pip_name: str, volume: float) -> None:
        loc = self._take_pending("aspirate")
        self.client.execute(
            RunEngineCommands.aspirate(
                self._pipette_id(pip_name),
                loc["labware_id"],
                loc["well_name"],
                volume,
                self.aspirate_flow_rate,
                origin=loc["origin"],
                offset=loc["offset"],
            )
        )

    def dispense(self, pip_name: str, volume: float, push_out: Optional[float] = None) -> None:
        loc = self._take_pending("dispense")
        self.client.execute(
            RunEngineCommands.dispense(
                self._pipette_id(pip_name),
                loc["labware_id"],
                loc["well_name"],
                volume,
                self.dispense_flow_rate,
                origin=loc["origin"],
                offset=loc["offset"],
                push_out=push_out,
            )
        )

    def blow_out(self, pip_name: str) -> None:
        loc = self._take_pending("blow_out")
        self.client.execute(
            RunEngineCommands.blow_out(
                self._pipette_id(pip_name),
                self.blow_out_flow_rate,
                loc["labware_id"],
                loc["well_name"],
                origin=loc["origin"],
                offset=loc["offset"],
            )
        )

    # -- tips --------------------------------------------------------------

    def pick_up_tip(self, pip_name: str) -> None:
        if self._pending is None:
            raise RuntimeError(
                "pick_up_tip over HTTP needs an explicit tip location; call "
                "get_location_from_labware(tiprack, well) first (the run engine has "
                "no implicit next-tip tracking)"
            )
        loc = self._take_pending("pick_up_tip")
        self.client.execute(
            RunEngineCommands.pick_up_tip(
                self._pipette_id(pip_name),
                loc["labware_id"],
                loc["well_name"],
                origin=loc["origin"],
                offset=loc["offset"],
            )
        )

    def drop_tip(self, pip_name: str) -> None:
        pipette_id = self._pipette_id(pip_name)
        if self._pending is not None:
            loc = self._take_pending("drop_tip")
            self.client.execute(
                RunEngineCommands.drop_tip(
                    pipette_id,
                    loc["labware_id"],
                    loc["well_name"],
                    origin=loc["origin"],
                    offset=loc["offset"],
                )
            )
        else:
            # Fallback: drop where the pipette is (NOT the trash). Flagged.
            self.client.execute(RunEngineCommands.drop_tip_in_place(pipette_id))

    # -- deck / robot ------------------------------------------------------

    def move_labware(self, labware_nickname: str, new_location: str) -> None:
        """Move a loaded labware. Always ``manualMoveWithoutPause`` on the OT-2 —
        unlike OT2Control, which (incorrectly for an OT-2) uses the gripper."""
        self.client.execute(
            RunEngineCommands.move_labware(
                self._labware_id(labware_nickname), self._location(new_location)
            )
        )

    def home(self) -> None:
        self.client.execute(RunEngineCommands.home())

    def pause(self) -> None:
        """No-op: in the never-played setup model each command already blocks to
        completion, so there is no run queue to pause (see HTTP_DRIVE_PLAN.md)."""

    def resume(self) -> None:
        """No-op counterpart to :meth:`pause`."""

    def run_snapshot(self) -> Dict[str, Any]:
        """Raw run resource for deck-state parsing (replaces the REPL snapshot)."""
        return self.client.get_run()

    # -- internals ---------------------------------------------------------

    def _location(self, new_location: Any) -> Any:
        if isinstance(new_location, dict):
            return new_location  # caller-supplied module / on-labware location
        if new_location in _OFF_DECK_ALIASES:
            return OFF_DECK
        return deck_slot(new_location)

    def _labware_id(self, nickname: str) -> str:
        try:
            return self._labware_ids[nickname]
        except KeyError:
            raise RuntimeError(f"labware {nickname!r} is not loaded in this run") from None

    def _pipette_id(self, nickname: str) -> str:
        try:
            return self._pipette_ids[nickname]
        except KeyError:
            raise RuntimeError(f"pipette {nickname!r} is not loaded in this run") from None

    def _take_pending(self, action: str) -> Dict[str, Any]:
        if self._pending is None:
            raise RuntimeError(f"{action} needs a location; call get_location_from_labware first")
        pending = self._pending
        self._pending = None
        return pending
