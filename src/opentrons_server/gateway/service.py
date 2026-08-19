"""Runtime service and state machine for the OT-2 gateway."""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import socket
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

import paramiko
import requests

from ..control import OT2Control, OT2HttpControl, RunEngineClient, RunEngineError
from ..control import state_readers as _state_readers
from ..version import __version__ as GATEWAY_VERSION
from .claims import ClaimManager
from .deck import (
    SLOTS,
    DeckDeclarationStore,
    build_deck,
    make_slot_labware,
    normalize_repl_slots,
    normalize_run_slots,
)
from .events_exporter import EventsExporter
from .models import (
    EQUIPMENT_KIND,
    ERROR_CODES,
    PROTOCOL_VERSION,
    Activity,
    ComponentStatus,
    EquipmentStatus,
    ErrorCode,
    ErrorInfo,
    LoadedPlate,
    MetricValue,
    SlotLabware,
    SlotModule,
    WellSample,
)
from .plate_state import PlateStateStore
from .tip_state import EMPTY, TipStateStore, TipUnavailable

# Snapshot is run on the OT-2's Python REPL in two invokes. The OT-2 runs
# only the official Opentrons SDK — `opentrons_server` is NOT installed
# there by design — so we ship the reader source over the wire and exec()
# it as a single string literal. state_readers has zero imports from
# opentrons_server, so the source is self-contained against the Opentrons
# SDK + stdlib.
#
# Two invokes (not one multi-statement send) because the SSH REPL reader
# breaks on the FIRST `>>>` prompt; sending the exec + print together would
# capture only the exec's empty output. The exec defines `get_all_states`
# in the REPL's module globals so the second invoke can call it.
# (Blank lines inside function bodies break a naïve paste of the source
# directly into the REPL — interactive mode treats blank as end-of-compound
# — so we route the source through compile()/exec() to bypass that quirk.)
logger = logging.getLogger(__name__)

_REMOTE_SNAPSHOT_DEFS = f"exec({inspect.getsource(_state_readers)!r})"
_REMOTE_SNAPSHOT_CALL = "import json; print(json.dumps(get_all_states(protocol), default=str))"

# Deck (rail) lights are driven through the robot's own Opentrons HTTP API
# (GET/POST /robot/lights), not the SSH REPL: it is a stateless, side-effect
# -free read on the GET path, so a direct HTTP call avoids contending with
# the shared REPL session that snapshot reads and protocol commands use. The
# robot host is the same one SSH already reaches; the HTTP server listens on
# port 31950 and requires an Opentrons-Version header. Both are overridable
# for non-standard deployments.
_OT2_HTTP_PORT = os.getenv("OT2_HTTP_PORT", "31950")
_OT2_HTTP_TIMEOUT = float(os.getenv("OT2_HTTP_TIMEOUT", "2.0"))
_OPENTRONS_HTTP_HEADERS = {"Opentrons-Version": "3"}

# Minimum spacing between robot-server run-labware probes. The probe is best-
# effort and NEVER runs inside the /status handler (which stays side-effect-free
# and uses only the cached `_last_run_labware`); it is refreshed on boot and on
# startup. The TTL guards against hammering the robot if refresh points cluster.
_OT2_DECK_PROBE_TTL = float(os.getenv("OT2_DECK_PROBE_TTL", "3.0"))

# How often the optional background thread refreshes the external-run probe so an
# EXTERNAL_CONTROL deck stays fresh without the (side-effect-free) /status handler
# ever issuing HTTP. Only runs when auto_reconnect is on and not in dry-run.
_OT2_RUN_REFRESH_INTERVAL = float(os.getenv("OT2_RUN_REFRESH_INTERVAL", "5.0"))

# Minimum spacing between *automatic* startup attempts when self-healing from a
# boot-time stand-off. A REPL + protocol init takes ~2 minutes, so retrying at
# the refresh cadence would keep a struggling robot permanently occupied and
# bury the real failure in a restart loop.
_OT2_SELF_HEAL_INTERVAL = float(os.getenv("OT2_SELF_HEAL_INTERVAL", "60.0"))


# How deep to descend when dropping a tip back INTO a tracked rack well
# (relocation / return): the tip end is brought to this height above the well
# bottom before release, so the tip slides into the hole and seats instead of
# being released at the well top and landing crooked. ~1 cm above where a
# pickup bottoms out, per bench feedback 2026-08-12; override per-deployment
# if a rack geometry needs it.
_TIP_RESEAT_BOTTOM_MM = float(os.getenv("OT2_TIP_RESEAT_BOTTOM_MM", "10"))

# References a drop_tip caller may use for the OT-2 fixed trash. The trash is
# the *default* drop target and needs no addressing; these route to it rather
# than being resolved as labware (slot 12 holds no loadable labware — it IS
# the trash). "12" is what the assistant naturally proposes.
_TRASH_ALIASES = frozenset({"12", "trash", "fixedTrash", "fixed_trash", "default_trash", "waste"})

# Actions that drive a protocol command on the robot — this device's primary
# operation. Withheld from `allowed_actions` while one is already in flight
# (STATUS_SPEC §2.3). Everything else on the surface is either abort/stop
# class (`pause`), lifecycle (`startup` / `shutdown`), or pure bookkeeping
# (plate / tip / deck tracking), none of which start a second run.
_RUN_STARTING_ACTIONS = frozenset(
    {
        "setup",
        "home",
        "move_to",
        "pick_up_tip",
        "aspirate",
        "dispense",
        "drop_tip",
        "move_labware",
        "resume",
        "tempmod.set",
        "tempmod.deactivate",
    }
)


# Run statuses that mean the robot-server has finished with a run.
_RUN_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "stopped"})


def _module_family(name: Optional[str]) -> Optional[str]:
    """Classify a module label the same way the UI does (ot2-deck.ts)."""

    t = (name or "").lower().replace("-", " ")
    if "temperature" in t:
        return "temperature"
    if "magnetic" in t:
        return "magnetic"
    if "heater" in t and "shaker" in t:
        return "heater_shaker"
    if "thermocycler" in t:
        return "thermocycler"
    return None


def _tempmod_engine_name(label: str) -> str:
    """Map a human/declared module_name to the engine's loadModule model."""

    t = label.lower()
    if "v1" in t or "gen1" in t:
        return "temperatureModuleV1"
    return "temperatureModuleV2"


def _run_counts_as_active(run: Dict[str, Any]) -> bool:
    """Is this robot-server run evidence that something is driving the robot?

    Only the *current* run can be, and only while it is not terminal — but
    "not terminal" is not sufficient. A run in ``idle`` that has never been
    started (``startedAt`` null) has executed no command at all: it is a
    container the robot-server opened, not work in progress.

    Excluding it matters because **this gateway opens exactly such a run
    itself** on ``OT2_TRANSPORT=http`` — the run-engine transport keeps a run
    open between commands (docs/HTTP_TRANSPORT.md). Counting it made the
    gateway read its *own* open run as somebody else's session, so every
    restart landed in the ``EXTERNAL_CONTROL`` stand-off ("Robot has an active
    run (external / official app)") and reported ``busy``. Worse, the escape
    from that stand-off (:meth:`OT2Service._maybe_resume_from_external_control`)
    waits for ``run_active`` to go false — a condition the gateway's own
    leftover run prevented, so it could sit ``busy`` until someone forced
    ``/control/startup``. Observed live on ot2_complexation, 2026-08-05.

    This mirrors the rule :meth:`OT2Service._observed_activity` already states:
    an open run is not evidence of motion. A genuinely external session still
    counts the moment it starts (``startedAt`` set, or any non-idle status).
    """

    if not run.get("current"):
        return False
    status = run.get("status")
    if status in _RUN_TERMINAL_STATUSES:
        return False
    return not (status == "idle" and not run.get("startedAt"))


class OT2ServiceState(str, Enum):
    REQUIRES_INIT = "requires_init"
    CONNECTING = "connecting"
    READY = "ready"
    BUSY = "busy"
    PAUSED = "paused"
    DRY_RUN = "dry_run"
    ERROR = "error"
    UNKNOWN_OUTCOME = "unknown_outcome"
    # Robot reachable over HTTP but a run is active outside this gateway
    # (e.g. started in the official Opentrons app). We deliberately do NOT
    # take the REPL control plane in this state — see boot_reconnect().
    EXTERNAL_CONTROL = "external_control"


class UnknownOutcomeError(RuntimeError):
    """Raised when transport failed during a non-idempotent operation."""


class OT2Service:
    """Owns one OT-2 connection and exposes safe gateway operations."""

    non_idempotent_actions = {
        "aspirate",
        "dispense",
        "pick_up_tip",
        "drop_tip",
        "move_labware",
    }

    def __init__(
        self,
        *,
        equipment_id: str = "ot2",
        equipment_name: str = "Opentrons OT-2",
        host_alias: Optional[str] = None,
        password: str = "",
        dry_run: bool = False,
        simulation: bool = False,
        plates: Optional[PlateStateStore] = None,
        decks: Optional[DeckDeclarationStore] = None,
        tips: Optional[TipStateStore] = None,
        events: Optional[EventsExporter] = None,
        transport: Optional[str] = None,
        http_run_state_path: Optional[str] = None,
    ) -> None:
        self.equipment_id = equipment_id
        self.equipment_name = equipment_name
        self.host_alias = host_alias
        self.password = password
        self.dry_run = dry_run
        self.simulation = simulation
        # Control-plane transport: "ssh" (default, the SSH REPL) or "http" (the
        # robot-server run engine, docs/HTTP_TRANSPORT.md). Opt-in and fully
        # reversible: unset OT2_TRANSPORT (or pass transport="ssh") to restore the
        # SSH path with zero behaviour change. HTTP is not yet robot-validated.
        self.transport = (transport or os.getenv("OT2_TRANSPORT") or "ssh").lower()
        # Orchestrator-owned plate/well tracking, persisted across restarts.
        # Mirrors the Cytation contract so a plate round-trips across devices.
        self.plates = (
            plates if plates is not None else PlateStateStore(state_path="./ot2_state.json")
        )
        # Operator/recipe-declared deck layout, persisted across restarts. The
        # source of truth that retires the dashboard's deck_layouts.json stopgap.
        self.decks = (
            decks if decks is not None else DeckDeclarationStore(state_path="./ot2_deck_state.json")
        )
        # Per-tiprack tip lifecycle (fresh / sample-touched / empty), persisted
        # across restarts. Racks register on /control/setup; picks are validated
        # against it; aspirate/dispense/drop mutate it. Transport-agnostic: the
        # hooks live here, above OT2Control / OT2HttpControl.
        self.tips = (
            tips if tips is not None else TipStateStore(state_path="./ot2_tip_state.json")
        )
        # pipette -> {rack, well, wells, channels, last_sample, origin_status} for
        # the currently mounted (tracked) tips. `well` is the addressed well and
        # `wells` every well the head emptied — the same list for a 1-channel
        # pipette, a whole column for an 8-channel one. In-memory session state,
        # like session_recipe.
        self._mounted_tips: Dict[str, Dict[str, Any]] = {}
        # pipette nickname -> channel count, bound at /control/setup from the
        # robot's own instrument report (see _bind_pipette_channels). Drives how
        # many tip wells a pick consumes; unknown pipettes default to 1.
        self._pipette_channels: Dict[str, int] = {}
        self._refresh_stop = threading.Event()
        self.started_at = time.monotonic()
        self.state = OT2ServiceState.DRY_RUN if dry_run else OT2ServiceState.REQUIRES_INIT
        # Either an OT2Control (SSH) or an OT2HttpControl (run engine); both expose
        # the same method surface the service calls.
        self.control: Optional[Any] = None
        self.claims = ClaimManager()
        # Best-effort push of control actions + tip lifecycle to the dashboard's
        # history DB. A no-op unless OT2_INGEST_URL is set, and never emitted in
        # dry run — a simulation must not enter the lab's history as real work.
        self.events = events if events is not None else EventsExporter.from_env()
        self.last_error: Optional[ErrorInfo] = None
        self._dry_run_lights_on = False
        # Cached deck-light state. Refreshed off the request path (background
        # refresh loop, startup) so /status never issues a blocking HTTP read
        # to the robot — see _refresh_lights / _lights_component. None => the
        # state is not yet known (reported as "unknown"). In dry-run there is
        # no background loop, so seed it from the simulated in-memory state and
        # let set_lights keep it current.
        self._last_lights: Optional[bool] = self._dry_run_lights_on if dry_run else None
        # THIS service's version, not the robot's. `equipment_version` names
        # the software answering /status; the robot's own software version is
        # a property of the attached hardware and lives in
        # `details.robot.api_version` (with fw/system alongside it).
        self.equipment_version: Optional[str] = GATEWAY_VERSION
        self._last_probe: Dict[str, Any] = {}
        # Cached labware of an active *external* robot-server run (EXTERNAL_CONTROL).
        # None while the gateway owns the REPL (deck then comes from last_snapshot).
        self._last_run_labware: Optional[Dict[str, Any]] = None
        self._last_run_labware_at: float = 0.0
        self._status_note: Optional[str] = None
        self._boot_started = False
        # Set by an operator POST /control/shutdown. Suppresses the background
        # self-heal so "shut it down" stays shut down: without it the refresh
        # loop would re-take the REPL seconds later and make the endpoint a
        # no-op. Cleared by an explicit POST /control/startup.
        self._operator_shutdown = False
        self._last_self_heal_at = 0.0
        # Activity span tracking (STATUS_SPEC v1.2 §2.3). `_activity` is the
        # last observed value and `_activity_since` the instant it last
        # changed — the start of the CURRENT span, not of the process or of
        # the enclosing request. `_run_action` stamps the exact edges of a
        # gateway-driven command; `get_status` only reconciles.
        self._activity: Activity = "unknown"
        self._activity_since: Optional[datetime] = None
        # Reserved monotonic counter (§2.3.1). A protocol command is typically
        # far shorter than the aggregator's 60 s poll, so a sampled `activity`
        # series would miss whole commands outright; the poll-to-poll delta of
        # this counter is what makes OT-2 utilization accountable. Counts
        # commands this process completed — it resets on restart, by contract.
        self._cycles_total = 0
        self.last_snapshot: Dict[str, Any] = self._empty_snapshot()
        self.session_recipe: Dict[str, Any] = {
            "labware": [],
            "instruments": [],
            "modules": [],
        }
        # Session auto-provisioning for declared decks (no /control/setup):
        # slot -> the nickname this session loaded the declared labware under,
        # mount -> the nickname the attached pipette was loaded under, and
        # slot/nickname -> the nickname a declared module was loaded under.
        # All describe the CURRENT control session only, so they reset with it
        # (startup / shutdown).
        self._session_labware: Dict[str, str] = {}
        self._session_pipettes: Dict[str, str] = {}
        self._session_modules: Dict[str, str] = {}
        # Stamp the opening span so `activity_since` is a real instant from the
        # first poll on, rather than "unknown until someone asks".
        self._sync_activity()
        # Pick tip racks back up from the persisted deck. Registration otherwise
        # only happens when a deck is declared or a setup runs, so a restart
        # left an already-declared rack untracked until the operator re-declared
        # it — the panel would show no racks at all for a deck full of them.
        # Safe here: `_build_deck_state` is cache-only (no HTTP, no REPL), and
        # `register_rack` never overwrites a rack the store just loaded.
        self.register_tiprack_slots()

    def startup(
        self,
        *,
        host_alias: Optional[str] = None,
        password: Optional[str] = None,
        simulation: Optional[bool] = None,
    ) -> None:
        """Connect and initialize the remote protocol session."""

        # An explicit startup is the operator asking for the session back, so
        # it re-arms the background self-heal.
        self._operator_shutdown = False

        if self.dry_run:
            self.state = OT2ServiceState.DRY_RUN
            self.last_error = None
            return

        self.state = OT2ServiceState.CONNECTING
        self.host_alias = host_alias or self.host_alias
        # Truthy check: an empty-string `password` in the request body
        # is treated as "no opinion" and falls through to the env-var
        # default set at __init__. This keeps device secrets in the
        # gateway's service config and out of workflow repos.
        if password:
            self.password = password
        if simulation is not None:
            self.simulation = simulation

        try:
            if self.transport == "http":
                self.control = self._connect_http()
            else:
                self.control = OT2Control(
                    host_alias=self.host_alias,
                    password=self.password,
                    simulation=self.simulation,
                )
            # A new control session starts empty: anything the previous session
            # auto-loaded from the declared deck is gone with it.
            self._session_labware = {}
            self._session_pipettes = {}
            self._session_modules = {}
            self.state = OT2ServiceState.READY
            self.last_error = None
            self._status_note = None
            self._emit_session_event("startup", to_state=self.state.value)
            self.refresh_snapshot()
            self._refresh_identity()
        except Exception as exc:
            self._set_error("startup_failed", str(exc), severity="error")
            self._emit_session_event("error", message=str(exc), code="startup_failed")
            raise

    def _connect_http(self) -> OT2HttpControl:
        """Build the run-engine control backend and create the (unplayed) run.

        Reuses ``_probe_base_url()`` (``OT2_HTTP_BASE_URL`` or the configured host)
        so the same address the boot probe already uses drives control.
        """
        base_url = self._probe_base_url()
        if not base_url:
            raise RuntimeError(
                "http transport requires OT2_HTTP_BASE_URL or a configured host_alias"
            )
        control = OT2HttpControl(RunEngineClient(base_url))
        control.initialize_protocol(simulation=self.simulation)
        # The OT-2's fixed trash is always physically present. Register it as
        # soon as the run exists so a bare drop_tip routes there even in a
        # setup-less (declared-deck) session; setup_protocol's own registration
        # guard then sees it and does not load a second one. Best-effort: an
        # engine that refuses (exotic deck configuration) must not cost the
        # whole session — the per-drop fallback (drop in place) remains, and
        # the warning names what was lost. First deploy of this call took the
        # gateway down at startup (AreaNotInDeckConfigurationError); never
        # let a convenience default do that again.
        try:
            control.load_trash_bin()
        except RunEngineError as exc:
            logger.warning(
                "fixed-trash registration failed (%s); a bare drop_tip will "
                "drop in place until a trash is registered",
                exc,
            )
        return control

    def shutdown(self) -> None:
        """Close the robot session and return to requires-init.

        Latches ``_operator_shutdown`` so the background self-heal does not
        immediately undo it — the gateway stays down until someone starts it.
        """

        self._operator_shutdown = True
        if self.control is not None:
            try:
                self.control.shutdown()
            finally:
                self.control = None
        self._session_labware = {}
        self._session_pipettes = {}
        self._session_modules = {}
        self.claims.force_clear()
        previous = self.state
        self.state = OT2ServiceState.DRY_RUN if self.dry_run else OT2ServiceState.REQUIRES_INIT
        self._emit_session_event(
            "shutdown", from_state=previous.value, to_state=self.state.value
        )

    def setup_protocol(self, setup: Dict[str, Any]) -> None:
        self.session_recipe = {
            "labware": list(setup.get("labware", [])),
            "instruments": list(setup.get("instruments", [])),
            "modules": list(setup.get("modules", [])),
        }

        def _setup() -> None:
            if self.control is None:
                raise RuntimeError("OT-2 is not initialized")
            self.control.setup_protocol(**self.session_recipe)

        self._run_action("setup", _setup, idempotent=True)
        self.register_tiprack_slots()
        self._bind_pipette_channels()

    def register_tiprack_slots(self) -> None:
        """Track every deck slot that holds a tip rack, keyed by the slot.

        **The slot is a tip rack's identity.** A rack carries no sample and no
        history worth naming — what an operator points at is "the rack in slot
        4", and that is also what they refill. Keying by slot rather than by a
        recipe nickname has three consequences worth stating:

        * It works from *any* deck source. Racks used to register only from a
          ``/control/setup`` recipe, so a rack the operator declared on the
          deck was invisible to the tracker — three racks on the deck, two
          unrelated ghosts in the panel.
        * It survives a restart. ``session_recipe`` is in-memory and is lost on
          every restart; the declared deck is persisted, so slot-keyed racks
          come back with it.
        * The UI join becomes trivial and always resolves — no dependency on
          ``labware.nickname``, which is null on every slot until a setup runs.

        Non-destructive: a slot already tracked keeps its partially-used
        statuses. ``/control/tips/reset`` is the explicit "a fresh rack was
        physically swapped in".
        """

        deck = self._build_deck_state()
        for slot, deck_slot in deck.slots.items():
            labware = deck_slot.labware
            if labware is not None and labware.is_tiprack:
                self.tips.register_rack(str(slot))

    def _tiprack_slot(self, ref: Optional[str]) -> Optional[str]:
        """Resolve a labware reference to the slot key the tracker uses.

        Protocol calls address labware by nickname (``labware_nickname`` on
        ``/control/pick-up-tip``), but the tracker is keyed by slot, so a
        nickname is resolved through the session recipe. A caller may also pass
        the slot itself — which is what the operator UI does, since a declared
        deck has no nicknames at all.

        Returns ``None`` when the reference names nothing the tracker holds, so
        callers fall through to their untracked path exactly as before.
        """

        if not ref:
            return None
        ref = str(ref)
        if self.tips.has_rack(ref):
            return ref  # already a slot key
        slot = self._nickname_to_slot().get(ref)
        if slot and self.tips.has_rack(str(slot)):
            return str(slot)
        return None

    def _auto_select_tiprack(
        self, *, pipette: str, sample_id: Optional[str], channels: int
    ) -> tuple[str, str]:
        """Choose a rack for a ``pick_up_tip`` that named no rack at all.

        Returns ``(nickname, slot)`` — the recipe nickname the control session
        addresses the rack by, and the slot key the tracker uses. Scans tracked
        racks in slot order and takes the first that (a) is addressable in the
        current session (a declared-only rack is tracked but not loaded in the
        run/REPL, so the transport cannot reach it), (b) holds tips this
        pipette can physically take (a p300 sent onto a 20 µL rack picks the
        wrong tip silently — worse than a refusal), and (c) has a tip this head
        can pick. Raises :class:`TipUnavailable` — a §6.1 precondition refusal,
        never an operational error — when no rack qualifies, naming why each
        candidate was passed over so the refusal is actionable.
        """

        slot_to_nickname = {
            slot: nick for nick, slot in self._nickname_to_slot().items()
        }
        declared = self._declared_slots()
        # "1".."11" must order numerically, not lexically ("10" < "2").
        slots = sorted(self.tips.racks(), key=lambda s: (len(s), s))
        reasons: List[str] = []
        for slot in slots:
            addressed = slot_to_nickname.get(slot)
            if addressed is None:
                # No recipe nickname — a declared rack is still addressable:
                # the slot reference is loaded into the session at execution
                # time (_resolve_session_labware).
                if getattr(declared.get(slot), "load_name", None):
                    addressed = slot
                else:
                    reasons.append(
                        f"slot {slot}: tracked but neither loaded in the "
                        "control session nor declared"
                    )
                    continue
            if not self._rack_fits_pipette(addressed, pipette):
                reasons.append(
                    f"slot {slot}: tip size does not fit pipette {pipette!r}"
                )
                continue
            try:
                self.tips.next_available(slot, sample_id=sample_id, channels=channels)
            except TipUnavailable as exc:
                reasons.append(f"slot {slot}: {exc.body.get('detail')}")
                continue
            return addressed, slot
        raise TipUnavailable(
            {
                "detail": (
                    "No tip rack can serve this pick"
                    + (f" for sample {sample_id!r}" if sample_id else "")
                    + (
                        f" ({'; '.join(reasons)})"
                        if reasons
                        else " (no tip racks are tracked; run /control/setup "
                        "or declare a tip rack slot)"
                    )
                ),
                "rack": None,
                "well": None,
                "tip_status": None,
                "requested_sample_id": sample_id,
                "channels": channels,
                "retry_after_s": None,
            }
        )

    def _rack_fits_pipette(self, rack_ref: str, pipette: str) -> bool:
        """Whether a rack's tips physically fit the pipette, when derivable.

        Both volumes are parsed from names the gateway already holds: the rack
        loadname (``..._300ul``) from the recipe or the declared deck, the
        instrument name (``p300_single_gen2``) from the recipe or — for a
        mount-addressed pipette — the robot's own instrument probe. Opentrons'
        GEN2 compatibility matrix — p20:{10,20}, p300:{200,300}, p1000:{1000}
        — is exactly ``pipette/2 <= tip <= pipette``, so the rule is encoded
        as that inequality rather than a table that would need editing for
        every new pipette. Unparseable names (custom labware, unknown
        instruments) return True: auto-selection must not silently exclude
        gear it cannot classify, and a caller who needs precision names the
        rack explicitly.
        """

        load_name = next(
            (
                lw.get("loadname")
                for lw in self.session_recipe.get("labware", []) or []
                if lw.get("nickname") == rack_ref
            ),
            None,
        )
        if load_name is None and rack_ref in SLOTS:
            load_name = getattr(self._declared_slots().get(rack_ref), "load_name", None)
        instrument_name = next(
            (
                inst.get("instrument_name")
                for inst in self.session_recipe.get("instruments", []) or []
                if inst.get("nickname") == pipette
            ),
            None,
        )
        if instrument_name is None:
            mount = str(pipette).strip().lower()
            instrument_name = next(
                (
                    inst.get("name")
                    for inst in self._last_probe.get("instruments", []) or []
                    if str(inst.get("mount", "")).strip().lower() == mount
                ),
                None,
            )
        pip_match = re.match(r"p(\d+)", str(instrument_name or ""))
        tip_match = re.search(r"_(\d+)ul", str(load_name or ""))
        if pip_match is None or tip_match is None:
            return True
        pip_ul, tip_ul = int(pip_match.group(1)), int(tip_match.group(1))
        return pip_ul / 2 <= tip_ul <= pip_ul

    # ---- session auto-provisioning (declared decks, no /control/setup) ----
    #
    # A setup recipe loads labware and pipettes into the control session and
    # names them. A *declared* deck does neither — its only names are deck
    # slots, and the operator flow (declare in the panel, then run an agent
    # plan) never calls /control/setup at all. These resolvers make the two
    # deck sources equivalent at the point of use: a slot or mount reference
    # is loaded into the session on first use, from what the operator declared
    # and what the robot reports attached. Both MUST be called inside a
    # `_run_action` closure — loading is a session command like any other.

    def _ensure_session_pipette(self, pipette: str) -> str:
        """Resolve ``pipette`` to a name the control session can address.

        Recipe nicknames pass through (setup loaded them). A mount name
        ("left" / "right") loads the attached instrument — from the robot's
        own ``GET /instruments`` probe — into the session on first use, and
        binds its channel count so multi-channel tip tracking stays honest.
        Anything else passes through for the transport to refuse honestly.
        """

        ref = str(pipette).strip()
        if any(
            inst.get("nickname") == ref
            for inst in self.session_recipe.get("instruments", []) or []
        ):
            return ref
        mount = ref.lower()
        if mount in self._session_pipettes:
            return self._session_pipettes[mount]
        if mount not in {"left", "right"}:
            return ref
        attached = next(
            (
                inst
                for inst in self._last_probe.get("instruments", []) or []
                if str(inst.get("mount", "")).strip().lower() == mount
            ),
            None,
        )
        if attached is None or not attached.get("name"):
            raise RuntimeError(
                f"no pipette known on mount {mount!r} — the robot probe "
                "reports none attached (or has not run); check the instrument "
                "or load one explicitly via /control/setup"
            )
        self._require_control().load_instrument(
            {
                "ot_default": True,
                "nickname": mount,
                "instrument_name": attached["name"],
                "mount": mount,
            }
        )
        self._session_pipettes[mount] = mount
        try:
            channels = int(attached.get("channels"))
        except (TypeError, ValueError):
            channels = 0
        if channels >= 1:
            self._pipette_channels[mount] = channels
        return mount

    def _resolve_session_labware(self, ref: str) -> str:
        """Resolve a labware reference to a session nickname, loading declared
        labware on demand.

        Recipe nicknames pass through. A deck-slot reference — the only name
        a declared deck has — is loaded into the session from the operator's
        declaration on first use, under ``slot_<n>`` (a valid identifier, so
        the SSH REPL path works too). Anything else passes through for the
        transport to refuse honestly.
        """

        name = str(ref).strip()
        if name in self._nickname_to_slot():
            return name
        if name in self._session_labware:
            return self._session_labware[name]
        if name not in SLOTS:
            return name
        declared = self._declared_slots().get(name)
        load_name = getattr(declared, "load_name", None)
        if not load_name:
            raise RuntimeError(
                f"slot {name} is not loaded in the control session and "
                "nothing is declared there — declare the slot in the panel "
                "or load it via /control/setup"
            )
        nickname = f"slot_{name}"
        definition = getattr(declared, "definition", None)
        if isinstance(definition, dict) and definition:
            # Custom labware the gateway has no local copy of: ship the full
            # schema-2 definition so the robot loads it by value instead of
            # looking it up in the opentrons namespace (which 404s).
            self._require_control().load_labware(
                {
                    "ot_default": False,
                    "nickname": nickname,
                    "config": definition,
                    "location": name,
                }
            )
        else:
            self._require_control().load_labware(
                {
                    "ot_default": True,
                    "nickname": nickname,
                    "loadname": load_name,
                    "location": name,
                }
            )
        self._session_labware[name] = nickname
        return nickname

    def _ensure_session_module(
        self, ref: Optional[str], *, family: str = "temperature"
    ) -> str:
        """Resolve a module reference to a control-session nickname.

        Recipe nicknames pass through (setup already loaded them). A deck-slot
        reference — the only name a declared deck has — is loaded into the
        session on first use, under ``slot_<n>``. ``ref`` may be omitted when
        exactly one module of ``family`` is declared or in the setup recipe.
        Must be called inside a ``_run_action`` closure.
        """

        name = (ref or "").strip() or None
        if name:
            for mod in self.session_recipe.get("modules") or []:
                nick = mod.get("nickname")
                loc = str(mod.get("location") or "")
                if nick and name in {str(nick), loc}:
                    return str(nick)
            if name in self._session_modules:
                return self._session_modules[name]

        slot = self._resolve_module_slot(name, family=family)
        if slot in self._session_modules:
            return self._session_modules[slot]
        nickname = f"slot_{slot}"
        self._require_control().load_module(
            {
                "nickname": nickname,
                "module_name": self._tempmod_load_name_for_slot(slot),
                "location": slot,
            }
        )
        self._session_modules[slot] = nickname
        if name:
            self._session_modules[name] = nickname
        return nickname

    def _resolve_module_slot(self, ref: Optional[str], *, family: str) -> str:
        candidates = self._module_slots(family)
        if ref:
            if ref in SLOTS:
                declared = self._declared_slots().get(ref)
                declared_name = getattr(declared, "module_name", None)
                if declared_name and _module_family(declared_name) not in {family, None}:
                    raise RuntimeError(
                        f"slot {ref} holds {declared_name}, not a {family} module"
                    )
                if ref in candidates or self._probe_has_family(family):
                    return ref
                raise RuntimeError(
                    f"no {family} module at slot {ref} — declare it on the deck "
                    "or load it via /control/setup"
                )
            raise RuntimeError(
                f"no {family} module named {ref!r}; use a recipe nickname or a deck slot"
            )
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise RuntimeError(
                f"no {family} module on the deck — declare it or load it via /control/setup"
            )
        raise RuntimeError(
            f"multiple {family} modules ({sorted(candidates)}); pass module as the slot"
        )

    def _module_slots(self, family: str) -> List[str]:
        slots: List[str] = []
        for slot, item in self._declared_slots().items():
            if isinstance(item, SlotModule) and _module_family(item.module_name) == family:
                slots.append(str(slot))
        for mod in self.session_recipe.get("modules") or []:
            loc = str(mod.get("location") or "")
            if (
                loc in SLOTS
                and loc not in slots
                and _module_family(str(mod.get("module_name") or "")) == family
            ):
                slots.append(loc)
        return slots

    def _probe_has_family(self, family: str) -> bool:
        for mod in self._last_probe.get("modules") or []:
            label = str(mod.get("type") or mod.get("model") or "")
            if _module_family(label) == family:
                return True
        return False

    def _tempmod_load_name_for_slot(self, slot: str) -> str:
        for mod in self._last_probe.get("modules") or []:
            model = mod.get("model")
            label = str(mod.get("type") or model or "")
            if model and _module_family(label) == "temperature":
                return str(model)
        declared = self._declared_slots().get(slot)
        if isinstance(declared, SlotModule):
            return _tempmod_engine_name(declared.module_name)
        return "temperatureModuleV2"

    def _bind_pipette_channels(self) -> None:
        """Bind each recipe pipette nickname to its channel count.

        The count comes from the robot's own ``GET /instruments`` (mount ->
        channels, cached on ``_last_probe``), joined to the nickname by the mount
        the recipe declares — the recipe is the only place both are known. An
        explicit ``channels`` on the instrument entry wins, which is what makes
        dry-run and simulation testable without a robot.

        Never inferred from the model name: ``p20_multi_gen2`` happening to
        contain ``multi`` is a naming convention, not a fact about the hardware,
        and getting it wrong here mis-tracks a whole column.
        """

        by_mount = {
            str(inst.get("mount")).strip().lower(): inst.get("channels")
            for inst in (self._last_probe.get("instruments") or [])
            if inst.get("mount")
        }
        for inst in self.session_recipe.get("instruments") or []:
            nickname = inst.get("nickname")
            if not nickname:
                continue
            channels = inst.get("channels")
            if channels is None:
                channels = by_mount.get(str(inst.get("mount") or "").strip().lower())
            try:
                count = int(channels)
            except (TypeError, ValueError):
                continue  # unknown -> stay unbound; _channels_for falls back to 1
            if count >= 1:
                self._pipette_channels[str(nickname)] = count

    def _channels_for(self, pipette: str) -> int:
        """Channel count for a pipette nickname; 1 when it cannot be determined.

        Falls back to 1 deliberately: an unbound pipette then behaves exactly as
        it did before multi-channel tracking existed, rather than refusing picks
        on a robot whose instrument probe is unreachable. The live binding is
        published as ``details.pipette_channels`` so a silent 1 is diagnosable.
        """

        channels = self._pipette_channels.get(pipette)
        if channels is None and self._last_probe:
            # The probe may have landed after setup (boot order, a reconnect).
            self._bind_pipette_channels()
            channels = self._pipette_channels.get(pipette)
        if channels is None:
            # Mount-addressed pipette (declared-deck flow, no recipe): the
            # probe knows the attached head directly.
            mount = str(pipette).strip().lower()
            probed = next(
                (
                    inst.get("channels")
                    for inst in self._last_probe.get("instruments", []) or []
                    if str(inst.get("mount", "")).strip().lower() == mount
                ),
                None,
            )
            try:
                if int(probed) >= 1:
                    channels = int(probed)
            except (TypeError, ValueError):
                pass
        return channels or 1

    @staticmethod
    def _labware_is_tiprack(lw: Dict[str, Any]) -> bool:
        config = lw.get("config") or {}
        if isinstance(config, dict):
            params = config.get("parameters") or {}
            if isinstance(params, dict) and params.get("isTiprack"):
                return True
        loadname = str(lw.get("loadname") or lw.get("load_name") or "").lower()
        return "tiprack" in loadname

    def home(self) -> None:
        self._run_action("home", lambda: self._require_control().home(), idempotent=True)

    def pause(self) -> None:
        self._run_action("pause", lambda: self._require_control().pause(), idempotent=True)
        self.state = OT2ServiceState.PAUSED

    def resume(self) -> None:
        self._run_action("resume", lambda: self._require_control().resume(), idempotent=True)
        self.state = OT2ServiceState.READY

    def set_location_from_well(self, request: Any) -> None:
        self._require_control().get_location_from_labware(
            self._resolve_session_labware(request.location.labware_nickname),
            request.location.position,
            top=request.location.top or 0,
            bottom=request.location.bottom or 0,
            center=1 if request.location.center else 0,
        )

    def move_to(self, request: Any) -> None:
        """Move a pipette to a well or to absolute deck coordinates (no liquid).

        Idempotent: re-issuing the same move is safe (no tip/liquid state at
        risk), so a transport loss mid-move records an error rather than
        ``unknown_outcome`` — same policy as ``home``.
        """

        def _move_to() -> None:
            pip = self._ensure_session_pipette(request.pipette)
            if request.location is not None:
                self.set_location_from_well(request)
            else:
                coords = request.coordinates
                self._require_control().get_location_absolute(coords.x, coords.y, coords.z)
            self._require_control().move_to_pip(
                pip,
                speed=request.speed,
                # False -> None keeps the SSH invoke minimal (kwargs formatter
                # skips None); the protocol-API default is False anyway.
                force_direct=request.force_direct or None,
                minimum_z_height=request.minimum_z_height,
            )

        self._run_action("move_to", _move_to, idempotent=True)

    def aspirate(self, request: Any) -> None:
        flow_rate = getattr(request, "flow_rate", None)

        def _aspirate() -> None:
            pip = self._ensure_session_pipette(request.pipette)
            self.set_location_from_well(request)
            self._require_control().aspirate(pip, request.volume_ul, flow_rate=flow_rate)

        self._run_action("aspirate", _aspirate, idempotent=False)
        self._mark_tip_used(
            request.pipette, request.location.labware_nickname, request.location.position
        )

    def dispense(self, request: Any) -> None:
        flow_rate = getattr(request, "flow_rate", None)

        def _dispense() -> None:
            pip = self._ensure_session_pipette(request.pipette)
            self.set_location_from_well(request)
            self._require_control().dispense(pip, request.volume_ul, flow_rate=flow_rate)

        self._run_action("dispense", _dispense, idempotent=False)
        self._mark_tip_used(
            request.pipette, request.location.labware_nickname, request.location.position
        )

    def pick_up_tip(self, request: Any) -> None:
        # The tracker is keyed by slot; protocol calls name labware by nickname.
        nickname = request.labware_nickname
        well = request.position
        sample_id = getattr(request, "sample_id", None)
        force = bool(getattr(request, "force", False))
        # An N-channel head takes N wells per pick, so every tracking decision
        # below is made over the covered set, not the addressed well alone.
        channels = self._channels_for(request.pipette)
        if nickname:
            rack = self._tiprack_slot(nickname)
            if rack is None and not well:
                # Refuse BEFORE any hardware addressing. Without tracking there
                # is no next-tip answer, and letting the transport discover
                # that mid-action turns a plain precondition into an ERROR
                # state (the HTTP run engine has no implicit next-tip either).
                raise TipUnavailable(
                    {
                        "detail": (
                            f"{nickname!r} is not a tracked tip rack, so the "
                            "gateway cannot choose the next tip from it; pass "
                            "an explicit position, or load the rack via "
                            "/control/setup (or declare its slot) so it is "
                            "tracked"
                        ),
                        "rack": nickname,
                        "well": None,
                        "tip_status": None,
                        "requested_sample_id": sample_id,
                        "retry_after_s": None,
                    }
                )
        else:
            # No rack named at all: the gateway owns tip tracking, so it — not
            # the caller — answers "which rack, which tip". Deterministic scan
            # over the tracked racks; raises TipUnavailable when none can serve.
            nickname, rack = self._auto_select_tiprack(
                pipette=request.pipette, sample_id=sample_id, channels=channels
            )
        tracked = rack is not None

        # Contamination guard + auto-pick, both only for tracked racks. Raises
        # TipUnavailable (HTTP 412 at the API layer) before any hardware motion.
        if tracked and not well:
            well = self.tips.next_available(rack, sample_id=sample_id, channels=channels)
        prior_status: Optional[str] = None
        covered: List[str] = []
        if tracked and well:
            prior_status = self.tips.validate_pick(
                rack, well, sample_id=sample_id, force=force, channels=channels
            )
            covered = self.tips.covered_wells(rack, well, channels=channels)

        def _pick_up_tip() -> None:
            # The robot is addressed by session names; slot / mount references
            # (declared-deck flow) are loaded into the session on first use.
            # `rack` is the tracker's slot key and means nothing to the
            # protocol API.
            pip = self._ensure_session_pipette(request.pipette)
            if nickname and well:
                self._require_control().get_location_from_labware(
                    self._resolve_session_labware(nickname), well
                )
            self._require_control().pick_up_tip(pip)

        self._run_action("pick_up_tip", _pick_up_tip, idempotent=False)
        if tracked and well:
            self._mounted_tips[request.pipette] = {
                "rack": rack,
                "well": well,
                "wells": covered,
                "channels": channels,
                "last_sample": sample_id or prior_status,
                "origin_status": prior_status,
            }
            self._emit_tip_event(
                "tip_pickup",
                rack,
                pipette=request.pipette,
                well=well,
                wells=covered or None,
                channels=channels,
                sample_id=sample_id,
            )

    def drop_tip(self, request: Any) -> None:
        nickname = request.labware_nickname
        position = request.position
        if nickname and not position and str(nickname).strip() in _TRASH_ALIASES:
            # Naming the trash without a well is the same as naming nothing —
            # it is already the default target, and it is not resolvable
            # labware. A full nickname+position pair always passes through:
            # a session may genuinely hold labware nicknamed "trash".
            nickname = None
        elif bool(nickname) != bool(position):
            # Refuse a half-specified location loudly (pre-motion, no
            # last_error): silently ignoring it is how a "drop in slot 12"
            # once became a drop wherever the head happened to be.
            raise ValueError(
                "an explicit drop location needs both labware_nickname and "
                "position; omit both to drop into the fixed trash"
            )

        # An explicit drop into a TRACKED rack is a tip relocation, not a
        # disposal, and gets the same pre-motion discipline as a pick: the
        # destination wells (all of them, for a multi-channel head) must not
        # already hold tips — dropping onto a seated tip is a crash. The
        # head's own origin wells are exempt: they are physically empty while
        # the tips ride the head (that exemption is what makes "return to
        # where it came from" legal).
        mounted = self._mounted_tips.get(request.pipette)
        dest_rack = self._tiprack_slot(nickname) if nickname and position else None
        dest_wells: List[str] = []
        if dest_rack is not None:
            channels = (mounted or {}).get("channels") or self._channels_for(request.pipette)
            # Raises for an unmappable address (unknown well; a multi-channel
            # head not at row A) — refuse rather than move untracked.
            dest_wells = self.tips.covered_wells(dest_rack, position, channels=channels)
            vacated = (
                set(mounted.get("wells") or [])
                if mounted is not None and mounted.get("rack") == dest_rack
                else set()
            )
            occupied = [
                w
                for w in dest_wells
                if w not in vacated
                and self.tips.status(dest_rack, w).strip().lower() != EMPTY
            ]
            if occupied:
                raise TipUnavailable(
                    {
                        "detail": (
                            f"Cannot drop into rack {dest_rack} at {position}: "
                            f"well(s) {', '.join(occupied)} already hold tips"
                        ),
                        "rack": dest_rack,
                        "well": position,
                        "tip_status": self.tips.status(dest_rack, occupied[0]),
                        "requested_sample_id": None,
                        "retry_after_s": None,
                    }
                )

        def _drop_tip() -> None:
            # Optional explicit drop location (e.g. a well to return a tip
            # to). When omitted, both transports route to the OT-2 fixed
            # trash: SSH implicitly, HTTP via the trash registered when the
            # session was created (trash labware on servers that model it as
            # labware, the fixedTrash addressable area otherwise). Mirrors
            # pick_up_tip.
            pip = self._ensure_session_pipette(request.pipette)
            if nickname and position:
                # Into a tracked rack, descend so the tip seats in the hole
                # (releasing at the well top lands it crooked); into any other
                # labware keep the default well-top release.
                depth = {"bottom": _TIP_RESEAT_BOTTOM_MM} if dest_rack is not None else {}
                self._require_control().get_location_from_labware(
                    self._resolve_session_labware(nickname), position, **depth
                )
            self._require_control().drop_tip(pip)

        self._run_action("drop_tip", _drop_tip, idempotent=False)
        mounted = self._mounted_tips.pop(request.pipette, None)
        # Every well the head emptied goes back to "empty", not just the
        # addressed one — otherwise a multi-channel column stays partly "new"
        # and the next auto-pick sends the head onto holes.
        self._mark_mounted_wells(mounted, EMPTY)
        if dest_rack is not None and dest_wells:
            # Complete the relocation: the destination wells now hold the
            # head's tips, carrying their history — the sample they last
            # touched, "new" for tips that never touched liquid (so a
            # relocated fresh tip stays available), "unknown" for a head
            # whose tips were never tracked (occupied, but never offered to
            # an auto-pick). Ordered after the origin marking so returning a
            # tip to its own well nets out as that well holding a tip.
            carried = "unknown" if mounted is None else (mounted.get("last_sample") or "new")
            self.tips.set_statuses(dest_rack, dest_wells, carried)
        if mounted is not None or dest_rack is not None:
            self._emit_tip_event(
                "tip_drop",
                (mounted or {}).get("rack") or dest_rack,
                pipette=request.pipette,
                well=(mounted or {}).get("well"),
                wells=(mounted or {}).get("wells") or None,
                channels=(mounted or {}).get("channels"),
                sample_id=(mounted or {}).get("last_sample"),
                to_rack=dest_rack,
                to_wells=dest_wells or None,
            )

    def _mark_mounted_wells(
        self, mounted: Optional[Dict[str, Any]], status: str
    ) -> None:
        """Stamp ``status`` on every rack well the mounted head's tips came from."""

        if mounted is None or not self.tips.has_rack(mounted["rack"]):
            return
        wells = mounted.get("wells") or [mounted["well"]]
        self.tips.set_statuses(mounted["rack"], list(wells), status)

    def _mark_tip_used(self, pipette: str, labware_nickname: str, position: str) -> None:
        """Record what the mounted tip touched, after a successful liquid step.

        Touching a tracked tiprack is not a sample contact; anything else stamps
        the tip's origin well with a sample id — the tracked plate's real
        ``sample_id`` when the target well has one, else ``<labware>_<well>``.
        """

        mounted = self._mounted_tips.get(pipette)
        if mounted is None or self._tiprack_slot(labware_nickname) is not None:
            return
        sample = self._resolve_sample_id(labware_nickname, position)
        mounted["last_sample"] = sample
        self._mark_mounted_wells(mounted, sample)

    def _resolve_sample_id(self, labware_nickname: str, position: str) -> str:
        plate = self.plates.get()
        if plate is not None and plate.plate_id == labware_nickname:
            for w in plate.wells:
                if w.well == position and w.sample_id:
                    return w.sample_id
        return f"{labware_nickname}_{position}"

    def reset_tip_rack(self, slot: str, *, wells: Optional[list[str]] = None):
        """(Re)register a rack with all tips fresh — a physical rack swap.

        Audited: this is an operator asserting a physical fact the gateway
        cannot observe, and it discards the record of every tip consumed so
        far. The previous counts go on the event so the history still shows
        what was thrown away.
        """

        slot = self._tiprack_slot(slot) or str(slot)
        before = self.tips.summary().get(slot) if self.tips.has_rack(slot) else None
        result = self.tips.reset_rack(slot, wells=wells)
        self._emit_tip_event(
            "tips_reset",
            slot,
            available_before=None if before is None else before.get("available"),
            empty_before=None if before is None else before.get("empty"),
            touched_before=None if before is None else before.get("touched"),
            total=len(result.tips),
        )
        return result

    def move_labware(self, request: Any) -> None:
        self._run_action(
            "move_labware",
            lambda: self._require_control().move_labware(
                self._resolve_session_labware(request.labware_nickname),
                request.new_location,
            ),
            idempotent=False,
        )

    # ---- plate / well tracking (orchestrator-owned bookkeeping) --------
    #
    # These mutate persisted metadata only; they do not drive the robot, so
    # they bypass the BUSY state machine in _run_action and work in any state
    # (including dry-run). Claim enforcement still applies at the API layer.

    def load_plate(
        self,
        *,
        plate_id: str,
        model: str,
        wells: Optional[list[WellSample]] = None,
    ) -> LoadedPlate:
        return self.plates.load_plate(plate_id=plate_id, model=model, wells=wells)

    def unload_plate(self) -> Optional[LoadedPlate]:
        return self.plates.unload_plate()

    def update_well(self, well: str, **kwargs: Any) -> WellSample:
        return self.plates.update_well(well, **kwargs)

    def _robot_lights_url(self) -> Optional[str]:
        """Resolve the robot's /robot/lights HTTP endpoint, or None if unknown.

        Prefers an explicit ``OT2_HTTP_BASE_URL`` override; otherwise reuses
        the hostname the SSH transport already resolved. Returns None when no
        session is connected so callers can report the lights as unreachable
        without attempting a doomed request.
        """

        base = os.getenv("OT2_HTTP_BASE_URL")
        if base:
            return base.rstrip("/") + "/robot/lights"
        if self.control is None:
            return None
        host = getattr(self.control.client, "hostname", None)
        if not host:
            return None
        return f"http://{host}:{_OT2_HTTP_PORT}/robot/lights"

    def get_lights(self) -> Optional[bool]:
        """Return the deck-light state, or None when it cannot be determined.

        Side-effect-free: a GET against the robot's HTTP API. In dry-run mode
        the in-memory simulated state is returned.
        """

        if self.dry_run:
            return self._dry_run_lights_on
        url = self._robot_lights_url()
        if url is None:
            return None
        response = requests.get(url, headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT)
        response.raise_for_status()
        return bool(response.json().get("on"))

    def set_lights(self, on: bool) -> bool:
        """Set the deck lights via the robot's HTTP API; return the new state."""

        if self.dry_run:
            self._dry_run_lights_on = on
            self._last_lights = on
            return on
        url = self._robot_lights_url()
        if url is None:
            raise RuntimeError("OT-2 is not initialized; POST /control/startup first")
        response = requests.post(
            url, json={"on": on}, headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
        )
        response.raise_for_status()
        result = bool(response.json().get("on", on))
        # Reflect the new state immediately so the next /status doesn't lag a
        # background-refresh interval behind an operator toggle.
        self._last_lights = result
        return result

    def set_tempmod_temperature(self, request: Any) -> None:
        """Set the temperature-module target; return without waiting for the ramp."""

        def _set() -> None:
            nick = self._ensure_session_module(getattr(request, "module", None))
            control = self._require_control()
            start = getattr(control, "tempmod_start_set_temperature", None)
            if start is not None:
                start(nick, request.celsius)
            else:
                control.tempmod_set_temperature(nick, request.celsius)

        self._run_action("tempmod.set", _set, idempotent=True)

    def deactivate_tempmod(self, request: Any) -> None:
        def _off() -> None:
            nick = self._ensure_session_module(getattr(request, "module", None))
            self._require_control().tempmod_deactivate(nick)

        self._run_action("tempmod.deactivate", _off, idempotent=True)

    def _refresh_lights(self) -> None:
        """Refresh the cached deck-light state from a best-effort read.

        Runs only *off* the request path — the background refresh loop and
        ``startup`` — so ``/status`` itself never issues the blocking HTTP read
        that would otherwise stall a poll (and, under contention, drop the
        socket before replying). Never raises; an unreachable robot resets the
        cache to ``None`` so the component honestly reports ``unknown``.
        """

        try:
            self._last_lights = self.get_lights()
        except Exception:
            self._last_lights = None

    def _lights_component(self) -> ComponentStatus:
        """Build the ``lights`` component from the cached state (no I/O).

        Reads only ``self._last_lights`` (maintained by ``_refresh_lights`` and
        ``set_lights``), so ``/status`` stays side-effect-free and always
        returns 200. ``None`` (state not yet known / robot unreachable at the
        last refresh) is reported as ``unknown``/disconnected.
        """

        on = self._last_lights
        if on is None:
            return ComponentStatus(connected=False, state="unknown")
        return ComponentStatus(connected=True, state="on" if on else "off")

    def _probe_base_url(self) -> Optional[str]:
        """Resolve the robot's HTTP API base, usable *before* a session exists.

        Unlike the lights path, this falls back to the configured host alias so
        the boot probe can run while ``self.control`` is still None.
        """

        base = os.getenv("OT2_HTTP_BASE_URL")
        if base:
            return base.rstrip("/")
        host = getattr(self.control.client, "hostname", None) if self.control is not None else None
        host = host or self.host_alias
        if not host:
            return None
        return f"http://{host}:{_OT2_HTTP_PORT}"

    def probe_robot(self) -> Dict[str, Any]:
        """Read-only probe of the robot-server HTTP API. Never raises.

        Derives reachability, identity (api/fw version, model, name), whether a
        run is active outside this gateway, and the attached instruments and
        modules — all durable, session-independent state the SSH/REPL plane
        cannot give us. Surfaces on ``/status`` as ``details.robot`` (see
        ``get_status``), so the dashboard can show/constrain module setup from
        what is physically attached.
        """

        result: Dict[str, Any] = {
            "reachable": False,
            "run_active": False,
            "api_version": None,
            "fw_version": None,
            "robot_model": None,
            "robot_name": None,
            "instruments": [],
            "modules": [],
        }
        base = self._probe_base_url()
        if base is None:
            return result
        try:
            resp = requests.get(
                base + "/health", headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
            )
            resp.raise_for_status()
            health = resp.json()
        except Exception:
            return result  # unreachable
        result["reachable"] = True
        result["api_version"] = health.get("api_version")
        result["fw_version"] = health.get("fw_version")
        result["robot_model"] = health.get("robot_model")
        result["robot_name"] = health.get("name")
        # Best-effort extras: a failure here must not flip reachability.
        try:
            runs = requests.get(
                base + "/runs", headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
            ).json()
            result["run_active"] = any(
                _run_counts_as_active(r) for r in runs.get("data", []) or []
            )
        except Exception:
            pass
        try:
            instruments = requests.get(
                base + "/instruments", headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
            ).json()
            result["instruments"] = [
                {
                    "mount": d.get("mount"),
                    "model": d.get("instrumentModel"),
                    "name": d.get("instrumentName"),
                    "channels": (d.get("data") or {}).get("channels"),
                }
                for d in instruments.get("data", []) or []
            ]
        except Exception:
            pass
        try:
            modules = requests.get(
                base + "/modules", headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
            ).json()
            result["modules"] = [
                {
                    "model": m.get("moduleModel"),
                    "type": m.get("moduleType"),
                    "serial": m.get("serialNumber"),
                    "id": m.get("id"),
                    # Live per-module telemetry from the robot-server — available
                    # whenever the module is powered, independent of any run, so
                    # the dashboard can show the reading before/after an experiment.
                    "status": (m.get("data") or {}).get("status"),
                    "current_temperature": (m.get("data") or {}).get("currentTemperature"),
                    "target_temperature": (m.get("data") or {}).get("targetTemperature"),
                }
                for m in modules.get("data", []) or []
            ]
        except Exception:
            pass
        return result

    def probe_run_labware(self) -> Optional[Dict[str, Any]]:
        """Read the active robot-server run's loaded labware. Never raises.

        Returns ``{"labware": [...], "modules": [...]}`` for the current run, or
        ``None`` when there is no active run or the robot is unreachable. This is
        the ``run`` source for the deck merge and is session-independent (it works
        even when the gateway holds no REPL session — e.g. a run started in the
        official Opentrons app). It is deliberately NOT called from ``get_status``.
        """

        base = self._probe_base_url()
        if base is None:
            return None
        try:
            runs = requests.get(
                base + "/runs", headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
            ).json()
        except Exception:
            return None
        run_id = None
        for r in runs.get("data", []) or []:
            if r.get("current") and r.get("status") not in {"succeeded", "failed", "stopped"}:
                run_id = r.get("id")
                break
        if not run_id:
            return None
        try:
            run = requests.get(
                base + f"/runs/{run_id}",
                headers=_OPENTRONS_HTTP_HEADERS,
                timeout=_OT2_HTTP_TIMEOUT,
            ).json()
        except Exception:
            return None
        data = run.get("data") or {}
        return {"labware": data.get("labware") or [], "modules": data.get("modules") or []}

    def _refresh_run_labware(self, *, force: bool = False) -> None:
        """Refresh the cached run-labware, TTL-guarded. Best-effort; never raises."""

        if (
            not force
            and self._last_run_labware_at
            and (time.monotonic() - self._last_run_labware_at) < _OT2_DECK_PROBE_TTL
        ):
            return
        self._last_run_labware = self.probe_run_labware()
        self._last_run_labware_at = time.monotonic()

    def _refresh_identity(self) -> None:
        """Update cached probe/version from a best-effort HTTP read."""

        probe = self.probe_robot()
        if probe.get("reachable"):
            self._last_probe = probe
            # The boot note ("Robot unreachable at …") outlives the condition
            # it describes: it is set once at boot and nothing cleared it, so
            # /status went on reporting a robot as unreachable long after it
            # came back — the single most misleading thing on the tile. The
            # only note reachable in `requires_init` is that one.
            if self.state == OT2ServiceState.REQUIRES_INIT:
                self._status_note = None
        self._refresh_run_labware(force=True)
        # Fold the deck-light read into the same off-request-path refresh so
        # /status can serve it from cache instead of blocking on robot HTTP.
        self._refresh_lights()
        # Self-heal from either boot-time stand-off: a robot that was busy with
        # an external run, or one that was simply not there yet.
        self._maybe_resume_from_external_control(probe)
        self._maybe_resume_from_unreachable_boot(probe)

    def _maybe_resume_from_external_control(self, probe: Dict[str, Any]) -> None:
        """Reclaim the REPL control plane once an external run has finished.

        ``EXTERNAL_CONTROL`` is a boot-time stand-off: when the gateway starts
        while the robot already has a run we must not seize (see
        ``boot_reconnect``), it defers. Nothing else transitioned out of it, so
        before this the gateway reported ``busy`` until a manual restart even
        after the external run completed. The background refresh watches the
        live probe and, once the robot is reachable AND no longer running,
        (re)establishes our own session so the gateway self-heals to ``ready``.
        Mirrors the idle branch of ``boot_reconnect``. Best-effort; never raises.

        ``probe`` must be a freshly-read ``probe_robot()`` result — not the
        possibly-stale ``_last_probe`` (which is only updated while reachable) —
        so a robot that has gone unreachable never triggers a reclaim.
        """

        if self.dry_run or self.state != OT2ServiceState.EXTERNAL_CONTROL:
            return
        if not probe.get("reachable") or probe.get("run_active"):
            return
        # Reachable and idle: safe to take the REPL control plane.
        self._status_note = None
        try:
            self.startup()
        except Exception:  # pragma: no cover - startup records its own error/state
            # startup() already recorded last_error and flipped to ERROR.
            pass

    def _maybe_resume_from_unreachable_boot(self, probe: Dict[str, Any]) -> None:
        """(Re)establish the session once a robot absent at boot comes back.

        The sibling of :meth:`_maybe_resume_from_external_control`, for the
        other boot-time stand-off. ``boot_reconnect`` leaves an unreachable
        robot in ``requires_init`` and, until this existed, nothing ever
        retried: a gateway that started while its OT-2 was off stayed down
        until an operator noticed the tile and POSTed ``/control/startup`` —
        which is exactly how both gateways spent a weekend idle.

        The guards are what make this safe to run unattended:

        * ``_operator_shutdown`` — a deliberate ``/control/shutdown`` is never
          undone, or the endpoint would be a no-op.
        * ``requires_init``, or a **failed startup** (``error`` with no live
          session and ``last_error.code == "startup_failed"``) — the same
          boot-retry the shaker and plateloc shipped after the 2026-07-31
          USB race left both in ``requires_init`` for two days. The error
          stays surfaced on ``/status`` until a retry succeeds (§6.4 then
          clears it); what never loops is a *mid-session* operational error,
          where a session exists and a human should adjudicate. (First hit
          live 2026-08-12: one 10 s read-timeout on ``POST /runs`` during
          boot left the gateway in ``error`` until a manual restart.)
        * ``run_active`` — a robot busy with an outside run is deferred to,
          the same way ``boot_reconnect`` does, which hands it to the
          external-control self-heal once that run ends.
        * ``_OT2_SELF_HEAL_INTERVAL`` — bounds retries against a robot that is
          reachable but cannot complete a protocol init.

        Best-effort; never raises. ``probe`` must be a freshly-read
        ``probe_robot()`` result, not the cached ``_last_probe``.
        """

        if self.dry_run or self._operator_shutdown or not self._boot_started:
            return
        failed_startup = (
            self.state == OT2ServiceState.ERROR
            and self.control is None
            and self.last_error is not None
            and self.last_error.code == "startup_failed"
        )
        if self.state != OT2ServiceState.REQUIRES_INIT and not failed_startup:
            return
        if not probe.get("reachable"):
            return

        if probe.get("run_active"):
            # Same stand-off boot_reconnect would have taken had the robot been
            # reachable then; _maybe_resume_from_external_control takes it from here.
            self.state = OT2ServiceState.EXTERNAL_CONTROL
            self._status_note = (
                "Robot has an active run (external / official app); gateway is standing off"
            )
            logger.info("self-heal: %s", self._status_note)
            return

        now = time.monotonic()
        if self._last_self_heal_at and (now - self._last_self_heal_at) < _OT2_SELF_HEAL_INTERVAL:
            return
        self._last_self_heal_at = now

        logger.info(
            "self-heal: robot reachable and idle after %s; "
            "starting %s session + protocol init (this can take several minutes)",
            "a failed startup" if failed_startup else "an unreachable boot",
            self.transport,
        )
        try:
            self.startup()
        except Exception:  # pragma: no cover - startup records its own error/state
            # startup() already recorded last_error and flipped to ERROR; the
            # self-heal interval above spaces the next attempt.
            pass

    def boot_reconnect(self) -> None:
        """Guarded one-shot reconnect at process start.

        Probe the robot's HTTP API and only (re)establish the SSH/REPL protocol
        context when the robot is reachable AND idle, so a service restart
        self-heals to ``ready`` without ever seizing the hardware from an active
        run (e.g. one started in the official Opentrons app). The REPL plane and
        the robot-server run engine are mutually exclusive — this is the guard
        that keeps them from colliding.
        """

        if self.dry_run or self._boot_started:
            return
        self._boot_started = True

        probe = self.probe_robot()
        self._last_probe = probe
        # Capture any active external run's labware so the deck reflects it even
        # while the gateway stands off (EXTERNAL_CONTROL). Cheap, best-effort.
        self._refresh_run_labware(force=True)

        if not probe.get("reachable"):
            self._status_note = (
                f"Robot unreachable at {self._probe_base_url() or 'unknown host'}; awaiting startup"
            )
            logger.warning("boot_reconnect: %s", self._status_note)
            return
        if probe.get("run_active"):
            self.state = OT2ServiceState.EXTERNAL_CONTROL
            self._status_note = (
                "Robot has an active run (external / official app); gateway is standing off"
            )
            logger.info("boot_reconnect: %s", self._status_note)
            return

        # Reachable and idle: safe to take the REPL control plane.
        self._status_note = None
        # Logged at both ends: the SSH session plus protocol-API init routinely
        # takes minutes, and with no log line the gateway looked hung rather
        # than working. The elapsed time also gives a baseline to compare
        # against when a boot really does wedge.
        logger.info(
            "boot_reconnect: robot reachable and idle; starting %s session + protocol init "
            "(this can take several minutes)",
            self.transport,
        )
        started = time.monotonic()
        try:
            self.startup()
        except Exception as exc:
            # startup() already recorded last_error and flipped to ERROR.
            logger.error(
                "boot_reconnect: startup failed after %.1fs: %s",
                time.monotonic() - started,
                exc,
            )
        else:
            logger.info(
                "boot_reconnect: ready after %.1fs", time.monotonic() - started
            )

    def refresh_snapshot(self) -> Dict[str, Any]:
        """Refresh cached state from the remote session when possible."""

        if self.dry_run:
            self.last_snapshot = self._dry_run_snapshot()
            return self.last_snapshot

        if self.control is None:
            self.last_snapshot = self._empty_snapshot()
            return self.last_snapshot

        if self.transport == "http":
            return self._refresh_snapshot_http()

        try:
            # The reader functions need to execute where the protocol object
            # lives: inside the robot-side Python interpreter. We send the
            # reader source over the wire rather than importing it, because
            # the OT-2 only runs the Opentrons SDK — see _REMOTE_SNAPSHOT_*.
            self.control.invoke(_REMOTE_SNAPSHOT_DEFS)
            output = self.control.invoke(_REMOTE_SNAPSHOT_CALL)
            self.last_snapshot = self._parse_remote_snapshot(output)
        except Exception as exc:
            self._set_error("snapshot_failed", str(exc), severity="warning")
        return self.last_snapshot

    def _refresh_snapshot_http(self) -> Dict[str, Any]:
        """Deck snapshot from the run engine (GET /runs/{id}).

        The run resource carries the loaded labware/modules under the same
        top-level keys the external-run probe returns, so we feed them into
        ``_last_run_labware`` — the ``run`` source of ``_build_deck_state`` — and
        the deck tile gets full parity through the existing ``build_deck``
        precedence (run > repl > declared) with no bespoke parser. There is no
        REPL deck in HTTP mode, so ``last_snapshot`` carries no ``deck`` key and
        the repl source stays empty. Never crashes the side-effect-free path.

        Container-shape parity with the SSH snapshot: ``pipettes`` is a dict
        keyed by mount and ``labwares``/``modules`` dicts keyed by deck slot
        (falling back to the engine id for off-deck/keyless entries), matching
        ``state_readers.get_all_states``. The *values* remain raw run-engine
        entries — the per-item schemas differ per transport by design (see
        HTTP_SSH_PARITY.md). ``run_id`` is HTTP-only.
        """
        try:
            run = self.control.run_snapshot()
            labware = run.get("labware") or []
            modules = run.get("modules") or []
            pipettes = run.get("pipettes") or []
            # Same {labware, modules} shape probe_run_labware yields; drives the
            # `run` deck source via normalize_run_slots.
            self._last_run_labware = {"labware": labware, "modules": modules}
            self._last_run_labware_at = time.monotonic()
            self.last_snapshot = {
                "run_id": run.get("id"),
                "pipettes": {
                    (entry.get("mount") or entry.get("id") or f"pipette_{i}"): entry
                    for i, entry in enumerate(pipettes)
                },
                "labwares": self._key_run_entries_by_slot(labware),
                "modules": self._key_run_entries_by_slot(modules),
            }
        except Exception as exc:
            self._set_error("snapshot_failed", str(exc), severity="warning")
        return self.last_snapshot

    @staticmethod
    def _key_run_entries_by_slot(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Key run-engine labware/module entries by deck slot, mirroring the SSH
        snapshot's slot-keyed dicts. Off-deck / slotless entries (and slot
        collisions, e.g. mid-move) fall back to the engine id so nothing is
        silently dropped."""
        keyed: Dict[str, Any] = {}
        for i, entry in enumerate(entries):
            location = entry.get("location")
            key = None
            if isinstance(location, dict) and location.get("slotName"):
                key = str(location["slotName"])
            if key is None or key in keyed:
                key = str(entry.get("id") or f"entry_{i}")
            keyed[key] = entry
        return keyed

    @staticmethod
    def _parse_remote_snapshot(output: str) -> Dict[str, Any]:
        """Extract the structured snapshot dict from the REPL transcript.

        ``output`` is the raw SSH-REPL transcript captured by the transport:
        the echoed ``json.dumps(...)`` command, the printed JSON object, and a
        trailing ``>>>`` prompt. The reader prints a single ``json.dumps``
        line, so the JSON object spans from the first ``{`` to the last ``}``
        — neither the echoed command nor the prompt contains a brace. Slice
        that span and parse it so ``details.snapshot`` carries the same
        ``{deck, pipettes, labwares, modules}`` shape the dry-run path returns.

        Falls back to ``{"raw": output, "note": ...}`` on any parse failure so
        a garbled read never crashes the side-effect-free ``/status`` handler.
        """

        start = output.find("{")
        end = output.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(output[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        return {"raw": output, "note": "remote JSON could not be parsed from REPL output"}

    def get_status(self) -> EquipmentStatus:
        """Return a side-effect-free AC equipment status envelope."""

        now = datetime.now(timezone.utc)
        status = self._equipment_state()
        # Health (§2.2) and activity (§2.3) are answered independently; this
        # only reconciles the span for transitions no command edge stamped
        # (a boot stand-off, a self-heal, an operator reconcile).
        activity = self._sync_activity()
        lights = self._lights_component()
        # One source of truth (§6.2): `allowed_actions()` already folds in the
        # convenience actions, so the wire and every in-process caller agree.
        actions = self.allowed_actions()
        raw = self.last_snapshot if isinstance(self.last_snapshot, dict) else {}
        # `snapshot.deck` is the normalized, provenance-tagged DeckState (built from
        # cached inputs only — see _build_deck_state); pipettes/labwares/modules stay
        # as the raw REPL read for now.
        details: Dict[str, Any] = {
            "service_state": self.state.value,
            "dry_run": self.dry_run,
            "simulation": self.simulation,
            "snapshot": {
                "deck": self._build_deck_state().model_dump(mode="json"),
                "pipettes": raw.get("pipettes", {}),
                "labwares": raw.get("labwares", {}),
                "modules": raw.get("modules", {}),
            },
            "session_recipe": self.session_recipe,
        }
        loaded_plate = self.plates.get()
        details["loaded_plate"] = loaded_plate.model_dump(mode="json") if loaded_plate else None
        details["tip_racks"] = self.tips.summary()
        details["mounted_tips"] = {
            pip: dict(info) for pip, info in self._mounted_tips.items()
        }
        # How many tip wells each pipette consumes per pick. Published because an
        # unbound pipette falls back to 1, and a silent 1 on an 8-channel head is
        # exactly the mis-tracking this exists to prevent.
        details["pipette_channels"] = dict(self._pipette_channels)
        if self._last_probe:
            details["robot"] = self._last_probe
        claimed_by = self.claims.current()
        if claimed_by is not None:
            details["claimed_by"] = claimed_by.model_dump(mode="json")

        return EquipmentStatus(
            # Explicit: the shared model defaults to "1.0" (the honest reading
            # of a device that does not state a version), so every envelope
            # names the version this gateway actually speaks.
            protocol_version=PROTOCOL_VERSION,
            equipment_id=self.equipment_id,
            equipment_name=self.equipment_name,
            equipment_kind=EQUIPMENT_KIND,
            equipment_version=self.equipment_version,
            # STATUS_SPEC: local hostname only (never an IP — the registry
            # owns network identity). Names the machine THIS gateway runs on;
            # the operator UI shows it so two panels are tellable apart.
            host=socket.gethostname(),
            equipment_status=status,
            activity=activity,
            activity_since=self._activity_since,
            message=self._message(),
            required_actions=self._required_actions(),
            allowed_actions=actions,
            device_time=now,
            uptime_seconds=time.monotonic() - self.started_at,
            components=self._components(lights),
            metrics={"cycles_total": MetricValue(value=self._cycles_total, unit="count")},
            last_error=self.last_error,
            details=details,
        )

    def _build_deck_state(self):
        """Merge the cached deck sources into a normalized DeckState.

        Side-effect-free: reads only cached fields (`last_snapshot`,
        `_last_run_labware`, the plate store, `session_recipe`) — no HTTP, no REPL.
        `declared` is wired in Phase 2. Precedence run > repl > declared > empty.
        """

        run_active = bool(self._last_probe.get("run_active")) if self._last_probe else False
        busy = self.state in {OT2ServiceState.BUSY, OT2ServiceState.EXTERNAL_CONTROL} or run_active
        repl = normalize_repl_slots((self.last_snapshot or {}).get("deck"))
        run = normalize_run_slots(self._last_run_labware) if self._last_run_labware else None
        return build_deck(
            run=run,
            repl=repl,
            declared=self._declared_slots(),
            loaded_plate=self.plates.get(),
            nickname_to_slot=self._nickname_to_slot(),
            busy=busy,
            now=datetime.now(timezone.utc),
        )

    def _declared_slots(self) -> Dict[str, Union[SlotLabware, SlotModule]]:
        """Merge the standalone operator declaration with the realized setup recipe.

        Two declared sub-sources: the persisted :class:`DeckDeclarationStore` (the
        stopgap replacement — set when there is no session) and ``session_recipe``
        (what ``/control/setup`` actually loaded). The setup recipe overlays the
        standalone declaration per slot, since it reflects what the gateway loaded.
        Declared entries may be labware or a sticky module (temperature module, …).
        """

        declared: Dict[str, Union[SlotLabware, SlotModule]] = dict(self.decks.get())
        for lw in self.session_recipe.get("labware", []) or []:
            loadname = lw.get("loadname") or lw.get("load_name")
            location = lw.get("location")
            if loadname and location is not None and str(location) in SLOTS:
                declared[str(location)] = make_slot_labware(
                    loadname, display_name=lw.get("nickname")
                )
        return declared

    def declare_deck(self, mapping: Dict[str, Any]):
        """Replace the operator-declared layout. Raises ValueError on a bad slot.

        Declaring a tip rack also starts tracking it: the operator saying "slot
        4 holds a tip rack" is exactly the fact the tracker needs, and requiring
        a separate /control/setup for it was why a declared rack never appeared
        in the panel.
        """

        result = self.decks.declare(mapping)
        self.register_tiprack_slots()
        return result

    def clear_deck(self) -> None:
        self.decks.clear()

    def run_background_refresh(self) -> None:
        """Daemon loop: periodically refresh the external-run probe.

        Keeps an EXTERNAL_CONTROL deck (and the run-active busy flag) fresh between
        boots without the status handler ever issuing HTTP. Best-effort; a failed
        probe is swallowed and retried next tick.
        """

        while not self._refresh_stop.wait(_OT2_RUN_REFRESH_INTERVAL):
            try:
                self._refresh_identity()
            except Exception:  # pragma: no cover - best-effort background loop
                pass

    def _nickname_to_slot(self) -> Dict[str, str]:
        """Map labware nicknames to deck slots from the current setup recipe.

        organic-solubility sends the plate nickname (e.g. "D") as the gateway
        `plate_id`, and `/control/setup` maps that nickname to a slot; this join
        is what places the tracked plate's wells on the right slot.
        """

        out: Dict[str, str] = {}
        for lw in self.session_recipe.get("labware", []) or []:
            nickname = lw.get("nickname")
            location = lw.get("location")
            if nickname is not None and location is not None:
                out[str(nickname)] = str(location)
        return out

    def allowed_actions(self) -> list[str]:
        """What this gateway would honor right now (§6.2 / §2.3).

        The state table below is the primary gate; the activity gate after it
        is a belt-and-braces guarantee that no protocol command is ever
        advertised while one is in flight, however the state table evolves.
        """

        actions = [a for a in self._allowed_for_state() if not self._blocked_by_activity(a)]
        # The two convenience actions used to be appended by the /status builder
        # instead of here, so this method returned a NARROWER list than the
        # device advertised on the wire — `lights.set` and `deck.declare` were
        # honored by their endpoints and published in `allowed_actions`, but
        # absent from every in-process caller's view. STATUS_SPEC §6.2 asks for
        # one helper feeding both surfaces; this is that helper.
        #
        # Lights are a convenience control, not gated on equipment_status, so
        # advertise lights.set whenever the robot answered the lights read.
        # `_lights_component` is cache-only, so /status stays side-effect-free.
        if self._lights_component().connected and "lights.set" not in actions:
            actions.append("lights.set")
        # Declaring the deck layout is pure metadata (no hardware), so it is a
        # convenience action available in every state except EXTERNAL_CONTROL,
        # where the gateway advertises nothing while an external app owns the robot.
        if self.state != OT2ServiceState.EXTERNAL_CONTROL and "deck.declare" not in actions:
            actions.append("deck.declare")
        return actions

    def _blocked_by_activity(self, action: str) -> bool:
        """§2.3: while ``activity == "running"``, omit anything that would
        start or enqueue a *second* concurrent command. Abort/stop-class
        actions (``pause``) and pure bookkeeping stay available."""

        return action in _RUN_STARTING_ACTIONS and self._observed_activity() == "running"

    def _allowed_for_state(self) -> list[str]:
        if self.state == OT2ServiceState.REQUIRES_INIT:
            return ["startup"]
        if self.state == OT2ServiceState.ERROR:
            # §2.2 keeps run-starting actions (setup, pick_up_tip, aspirate,
            # dispense, move_labware) withheld while the fault is active — but
            # withholding *recovery* too used to strand exactly the operator
            # who needs it (a mounted tip after a failed step had no advertised
            # way to home or drop; the plateloc fleet hit the same trap).
            # Recovery needs a live control session; without one, startup is
            # genuinely the only way forward.
            if self.control is None:
                return ["startup"]
            return [
                "startup",
                "shutdown",
                "home",
                "move_to",
                "drop_tip",
                "plate.load",
                "plate.unload",
                "well.update",
                "tips.reset",
                "tempmod.set",
                "tempmod.deactivate",
            ]
        if self.state == OT2ServiceState.DRY_RUN:
            return [
                "startup",
                "shutdown",
                "home",
                "setup",
                "plate.load",
                "plate.unload",
                "well.update",
                "tips.reset",
            ]
        if self.state == OT2ServiceState.PAUSED:
            return ["resume", "shutdown"]
        if self.state == OT2ServiceState.READY:
            return [
                "shutdown",
                "home",
                "setup",
                "pause",
                "move_to",
                "pick_up_tip",
                "aspirate",
                "dispense",
                "drop_tip",
                "move_labware",
                "plate.load",
                "plate.unload",
                "well.update",
                "tips.reset",
                "tempmod.set",
                "tempmod.deactivate",
            ]
        if self.state == OT2ServiceState.BUSY:
            return ["pause"]
        if self.state == OT2ServiceState.EXTERNAL_CONTROL:
            # Robot is driven from outside this gateway; we issue nothing.
            return []
        return []

    def reconcile(self, snapshot: Optional[Dict[str, Any]] = None) -> None:
        """Acknowledge an unknown outcome — or a failed command — after manual
        or external inspection.

        For ERROR the acknowledgement requires a live control session: the
        operator is saying "the fault is dealt with, the session is usable",
        which cannot be true of a session that does not exist (a failed
        startup stays ERROR until a startup succeeds).
        """

        if snapshot is not None:
            self.last_snapshot = snapshot
        if self.state == OT2ServiceState.UNKNOWN_OUTCOME or (
            self.state == OT2ServiceState.ERROR and self.control is not None
        ):
            self.last_error = None
            self.state = OT2ServiceState.READY

    # ---- history export (best-effort; see events_exporter.py) --------------

    def _claim_owner(self) -> Optional[str]:
        """Who holds the claim right now — the actor for an audit row.

        The edge stamps the signed-in person into the claim owner, so on a
        gated deployment this names a human rather than a UI constant.
        """

        current = self.claims.current()
        return current.owner if current else None

    def _emit_control_action(
        self,
        action: str,
        outcome: str,
        started: Optional[float] = None,
        message: Optional[str] = None,
    ) -> None:
        """One audit row per ``/control/*`` command.

        This is the row the dashboard's passthrough writes for operator clicks
        it proxies (ARCHITECTURE decision #1) — but a write made in this
        gateway's own UI never passes through the dashboard, so without this it
        left no trace. Emitting device-side covers both paths, and is the only
        way to cover an SDK/workflow call as well.

        **A dashboard-proxied action therefore produces two rows**: the
        passthrough's, recording the HTTP hop it made, and this one, recording
        what the hardware actually did. They are distinguished by ``source``
        (``device`` here; the passthrough's rows carry ``method`` /
        ``status_code`` and no ``source``), and the device row is the
        authoritative one for outcome and duration — the proxy only ever sees
        its own request. Count one or the other, not both.

        Message follows the passthrough's convention so the two read alike in
        one series. Never emitted in dry run: a simulation must not enter lab
        history.
        """

        if self.dry_run:
            return
        owner = self._claim_owner()
        summary = f"{owner or 'unclaimed'} {action} → {outcome}"
        self.events.emit(
            "control_action",
            message=f"{summary}: {message}" if message else summary,
            action=action,
            outcome=outcome,
            owner=owner,
            source="device",
            duration_s=None if started is None else round(time.monotonic() - started, 3),
        )

    def _emit_session_event(
        self,
        event: str,
        *,
        from_state: Optional[str] = None,
        to_state: Optional[str] = None,
        message: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """Session edges (startup / shutdown / error).

        The aggregator's poll sees state eventually; these carry the instant
        and the reason, and survive a transition that opens and closes inside
        one poll window.
        """

        if self.dry_run:
            return
        self.events.emit(
            event,
            from_state=from_state,
            to_state=to_state,
            message=message,
            owner=self._claim_owner(),
            transport=self.transport,
            **extra,
        )

    def _emit_tip_event(self, event: str, rack: Optional[str], **extra: Any) -> None:
        """Tip lifecycle, which otherwise exists only as *current* state in
        ``ot2_tip_state.json``. The file answers "which tips are gone"; these
        rows answer "when, and for which sample" — per-run tip accounting the
        60 s poll cannot reconstruct."""

        if self.dry_run or not rack:
            return
        self.events.emit(event, rack=rack, owner=self._claim_owner(), **extra)

    def _run_action(self, name: str, func: Callable[[], None], *, idempotent: bool) -> None:
        if self.dry_run:
            self.state = OT2ServiceState.DRY_RUN
            self.last_error = None
            return
        if self.state in {OT2ServiceState.BUSY, OT2ServiceState.UNKNOWN_OUTCOME}:
            raise RuntimeError(f"OT-2 is not ready for {name}; current state is {self.state.value}")

        previous_state = self.state
        self.state = OT2ServiceState.BUSY
        self._sync_activity()  # exact span start (§2.3): the command is in flight
        started = time.monotonic()
        try:
            func()
            self.state = OT2ServiceState.READY
            self.last_error = None
            self._cycles_total += 1
            self._emit_control_action(name, "ok", started)
            self.refresh_snapshot()
        except (socket.timeout, paramiko.SSHException, OSError) as exc:
            if idempotent:
                self._set_error(
                    "command_transport_failed", f"{name}: {exc}", severity="error"
                )
                self._emit_control_action(name, "transport_failed", started, str(exc))
            else:
                self.state = OT2ServiceState.UNKNOWN_OUTCOME
                self._set_error(
                    "command_unknown_outcome", f"{name}: {exc}", severity="critical"
                )
                self._emit_control_action(name, "unknown_outcome", started, str(exc))
                raise UnknownOutcomeError(str(exc)) from exc
            raise
        except Exception as exc:
            self._set_error("command_failed", f"{name}: {exc}", severity="error")
            self._emit_control_action(name, "failed", started, str(exc))
            raise
        finally:
            if self.state == OT2ServiceState.BUSY:
                self.state = previous_state
            # Exact span end, whatever the outcome — including the
            # UNKNOWN_OUTCOME path, where "still running?" is genuinely
            # unanswerable until an operator reconciles.
            self._sync_activity()

    def _require_control(self) -> OT2Control:
        if self.control is None:
            raise RuntimeError("OT-2 is not initialized")
        return self.control

    def _set_error(self, code: ErrorCode, message: str, *, severity: str) -> None:
        # The single mutation site for `last_error`, and the only place the
        # taxonomy is enforced (STATUS_SPEC best practice #6 asks for exactly
        # that). Raising rather than coercing is deliberate: a code outside the
        # set is a bug in this file, and a silently-substituted "unknown" would
        # ship it to every dashboard as a real device error.
        if code not in ERROR_CODES:
            raise ValueError(
                f"last_error.code {code!r} is outside the taxonomy; "
                f"add it to models.ErrorCode or reuse one of {sorted(ERROR_CODES)}"
            )
        self.last_error = ErrorInfo(
            code=code,
            message=message,
            severity=severity,  # type: ignore[arg-type]
            timestamp=datetime.now(timezone.utc),
        )
        if severity in {"error", "critical"} and self.state not in {
            OT2ServiceState.UNKNOWN_OUTCOME,
            OT2ServiceState.DRY_RUN,
        }:
            self.state = OT2ServiceState.ERROR

    def _equipment_state(self) -> str:
        if self.state == OT2ServiceState.READY:
            return "ready"
        if self.state in {
            OT2ServiceState.BUSY,
            OT2ServiceState.EXTERNAL_CONTROL,
        }:
            return "busy"
        # CONNECTING is "service up, hardware not initialized yet" — STATUS_SPEC
        # §2.2's requires_init, not busy. `busy` means an operation is running
        # (§2.3's invariant table pairs it with activity: running), so reporting
        # it here made a slow-but-healthy boot indistinguishable from real work
        # and hid the fact that nothing was driving the robot. The REPL protocol
        # init legitimately takes minutes on an OT-2. required_actions stays
        # empty (startup is already in flight — see _required_actions).
        if self.state in {OT2ServiceState.REQUIRES_INIT, OT2ServiceState.CONNECTING}:
            return "requires_init"
        if self.state == OT2ServiceState.DRY_RUN:
            return "dry_run"
        if self.state == OT2ServiceState.PAUSED:
            return "degraded"
        if self.state == OT2ServiceState.UNKNOWN_OUTCOME:
            return "unknown"
        return "error"

    def _observed_activity(self) -> Activity:
        """Is the robot performing its primary operation right now? (§2.3)

        Primary operation for this liquid handler is **a protocol command in
        flight on the robot** — a motion, a liquid transfer, a tip or labware
        move, or the setup that loads them. Two observations, neither read off
        ``equipment_status``:

        * ``BUSY`` brackets exactly one in-flight command (``_run_action``
          sets it around the blocking call to the control plane).
        * ``EXTERNAL_CONTROL`` is entered from the robot-server's own run list
          (``probe_robot``'s ``run_active``) and left only once a fresh probe
          says that run has finished — so it is a live observation of a run
          the gateway deliberately did not seize.

        ``run_active`` is deliberately NOT consulted on its own: the HTTP
        transport keeps a run open between commands (docs/HTTP_TRANSPORT.md),
        so an open run is not evidence of motion. The two control planes are
        mutually exclusive, so an external run cannot start underneath a
        ``READY`` gateway — the ``EXTERNAL_CONTROL`` branch is the only case
        where an outside run is observable.

        ``UNKNOWN_OUTCOME`` is the honest ``unknown``: transport died during a
        non-idempotent command, so whether the robot is still moving is
        exactly what we do not know until an operator reconciles.
        """

        if self.state in {OT2ServiceState.BUSY, OT2ServiceState.EXTERNAL_CONTROL}:
            return "running"
        if self.state == OT2ServiceState.UNKNOWN_OUTCOME:
            return "unknown"
        # Dry run included: the simulation performs no operation between
        # commands, and reporting its real (idle) activity is what keeps a
        # simulated device exercisable end-to-end. Readers exclude simulated
        # devices from utilization; devices do not self-censor.
        return "idle"

    def _note_activity(self, activity: Activity) -> None:
        """Record an observed activity, stamping ``activity_since`` only when
        the value actually changes (§2.3: the start of the CURRENT span)."""

        if activity != self._activity:
            self._activity = activity
            self._activity_since = datetime.now(timezone.utc)

    def _sync_activity(self) -> Activity:
        """Reconcile the tracked span with what is observed right now."""

        self._note_activity(self._observed_activity())
        return self._activity

    def _message(self) -> str:
        if self.last_error is not None:
            return self.last_error.message
        if self.state == OT2ServiceState.EXTERNAL_CONTROL:
            return self._status_note or "Robot under external control; gateway is standing off"
        if self.state == OT2ServiceState.REQUIRES_INIT:
            return self._status_note or "Awaiting startup"
        if self.state == OT2ServiceState.CONNECTING:
            return (
                "Connecting: SSH session + protocol-API init on the robot. "
                "This legitimately takes minutes on an OT-2; no action needed."
            )
        if self.state == OT2ServiceState.DRY_RUN:
            return "Dry-run mode - no hardware connected"
        if self.state == OT2ServiceState.UNKNOWN_OUTCOME:
            return "Transport failed during a non-idempotent operation; reconcile before continuing"
        return f"OT-2 service state: {self.state.value}"

    def _required_actions(self) -> list[str]:
        if self.state == OT2ServiceState.REQUIRES_INIT:
            return ["startup"]
        if self.state == OT2ServiceState.UNKNOWN_OUTCOME:
            return ["manual_reconcile"]
        if self.state == OT2ServiceState.ERROR:
            # With a live session the way out is acknowledging the failed
            # command (or any successful recovery action, which auto-clears
            # per §6.4); without one, only a fresh startup can help.
            return ["reconcile"] if self.control is not None else ["startup"]
        return []

    def _control_liveness(self) -> tuple[bool, str]:
        """``(connected, transport)`` for the control backend, observed where
        the transport allows it.

        This used to be ``self.control is not None`` — true whenever a control
        *object* existed, which is not the same claim. A dropped SSH session
        leaves the object in place, so ``/status`` went on reporting a live
        session until something tried to use it and failed. AGENTS.md §1: never
        report a state the gateway has not observed.

        What can honestly be observed differs by transport:

        - **ssh** — paramiko knows whether the socket is up, and asking costs
          no I/O (:meth:`SSHClient.is_alive`).
        - **http** — the run engine holds no persistent session, so there is
          nothing to be "connected" to. The robot answering the last probe is
          the closest available evidence, and it is a real observation.
        """
        if self.dry_run:
            return True, "dry_run"
        if self.control is None:
            return False, "disconnected"
        if self.transport == "http":
            return bool(self._last_probe.get("reachable")), "http"
        client = getattr(self.control, "client", None)
        if hasattr(client, "is_alive"):
            return bool(client.is_alive()), "ssh"
        # A control backend that cannot be asked. Report the session as up (it
        # was established) rather than inventing a "down" nobody observed.
        return True, "ssh"

    def _components(self, lights: ComponentStatus) -> Dict[str, ComponentStatus]:
        connected, transport = self._control_liveness()
        if self.dry_run:
            transport_note = "dry run (no robot connection)"
        elif transport == "http":
            transport_note = "control via HTTP run engine (no SSH session)"
        else:
            transport_note = "control via SSH REPL"

        # An SSH session exists only when SSH is the transport. Both gateways
        # currently run OT2_TRANSPORT=http, where this key was reporting
        # `connected` with no SSH session anywhere — a component named after a
        # protocol the device was not speaking. It is now scoped to what it
        # literally names; `control` below carries the transport actually in
        # use. The key stays for compatibility (STATUS_SPEC #14 forbids
        # renaming a published component).
        ssh_up = connected and transport in {"ssh", "dry_run"}
        components: Dict[str, ComponentStatus] = {
            "control": ComponentStatus(
                connected=connected,
                # Names the backend rather than restating connectivity, which
                # `connected` already carries. Small closed enum:
                # ssh | http | dry_run | disconnected.
                state=transport,
                message=transport_note,
            ),
            "ssh": ComponentStatus(
                connected=ssh_up,
                state="connected" if ssh_up else "disconnected",
                message=transport_note,
            ),
            "protocol": ComponentStatus(
                connected=self.state
                in {
                    OT2ServiceState.READY,
                    OT2ServiceState.BUSY,
                    OT2ServiceState.PAUSED,
                    OT2ServiceState.DRY_RUN,
                },
                state=self.state.value,
            ),
            "lights": lights,
        }
        # Attached pipettes, surfaced from the (session-independent) HTTP probe.
        for instrument in self._last_probe.get("instruments", []) or []:
            mount = instrument.get("mount")
            if not mount:
                continue
            components[f"pipette_{mount}"] = ComponentStatus(
                connected=True,
                state=instrument.get("name") or instrument.get("model") or "attached",
            )
        return components

    def _empty_snapshot(self) -> Dict[str, Any]:
        return {
            "deck": {"slots": {}, "occupied_slots": 0, "empty_slots": 12},
            "pipettes": {},
            "labwares": {},
            "modules": {},
        }

    def _dry_run_snapshot(self) -> Dict[str, Any]:
        return {
            "deck": {
                "slots": {str(i): None for i in range(1, 13)},
                "occupied_slots": 0,
                "empty_slots": 12,
            },
            "pipettes": {},
            "labwares": {},
            "modules": {},
        }
