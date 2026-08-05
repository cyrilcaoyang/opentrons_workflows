"""Persistent per-tiprack tip lifecycle tracking for the OT-2 gateway.

Sits beside :class:`PlateStateStore` / :class:`DeckDeclarationStore` as the third
JSON-backed state store: "which tips on which rack are fresh, used, or gone".
Ported from OT2Demo's driver-level tip tracker, but gateway-owned so it works on
both transports (SSH REPL and the run-engine HTTP path), survives restarts, and
surfaces on ``GET /status`` under ``details.tip_racks``.

Tip status vocabulary (per well):

- ``"new"`` — fresh tip, never used. Absent wells never occur: the map is full.
- ``"empty"`` — tip was dropped to trash; the well has no tip.
- any other string — a **sample id** the tip has touched (e.g. ``"D_A3"`` or an
  orchestrator sample id). A tip that touched sample X may be re-picked *for
  sample X* (or with ``force=true``), but never silently for another sample —
  that is the cross-contamination guard.

A pick is tracked per *pipette head*, not per addressed well: an N-channel
pipette sent to a row-A well removes the whole N-well column (see
:func:`covered_well_span`), so ``validate_pick`` / ``next_available`` /
``set_statuses`` all operate on the covered set. ``channels=1`` covers exactly
the addressed well, which is the single-channel behaviour this store has always
had.

``OT2Service`` drives the lifecycle: registration on ``/control/setup``,
validation + auto-pick on ``/control/pick-up-tip``, sample marking after
aspirate/dispense, ``"empty"`` on drop. Refusals raise :class:`TipUnavailable`,
which the API maps to HTTP 412 with the structured body carried on the
exception (STATUS_SPEC §6.1).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .models import TipRackState

logger = logging.getLogger(__name__)

_ROW_LETTERS = ["A", "B", "C", "D", "E", "F", "G", "H"]

# Statuses that mean "this tip is pickable by anyone".
_FRESH_STATUSES = {"", "new", "unused", "clean", "available"}

FRESH = "new"
EMPTY = "empty"


def tip_well_order_96() -> List[str]:
    """96 tip wells in column-major order (A1..H1, A2..H2, ...).

    Column-major matches how the Opentrons protocol API consumes a tiprack, so
    auto-pick walks the rack the same way an untracked run would.
    """

    return [f"{row}{col}" for col in range(1, 13) for row in _ROW_LETTERS]


def _split_well(well: str) -> tuple[str, str]:
    """Split ``"A1"`` into ``("A", "1")``; raise on any other shape."""

    text = str(well).strip()
    if len(text) < 2 or not text[1:].isdigit():
        raise ValueError(f"Well id {well!r} is not <row letter><column number>")
    return text[0].upper(), text[1:]


def covered_well_span(well: str, channels: int) -> List[str]:
    """The wells a ``channels``-wide pipette head occupies when sent to ``well``.

    A single-channel pipette covers exactly the addressed well. A multi-channel
    head spans ``channels`` wells **downward in the same column**, so an
    8-channel pick at A1 occupies A1..H1 — the physical reason a whole column
    leaves the rack on one pick, which is what the addressed well alone cannot
    express.

    Pure geometry: it knows nothing about a rack's contents or which starts are
    permitted (see :meth:`TipStateStore.covered_wells` for the row-A policy).
    """

    if channels < 1:
        raise ValueError(f"channels must be >= 1, got {channels!r}")
    row, column = _split_well(well)
    if row not in _ROW_LETTERS:
        raise ValueError(f"Well id {well!r} has an unknown row letter {row!r}")
    start = _ROW_LETTERS.index(row)
    if start + channels > len(_ROW_LETTERS):
        raise ValueError(
            f"A {channels}-channel head at {well} would run off the bottom of the rack"
        )
    return [f"{_ROW_LETTERS[start + offset]}{column}" for offset in range(channels)]


class TipUnavailable(Exception):
    """A tip pick was refused. ``body`` is the structured HTTP-412 payload."""

    def __init__(self, body: Dict[str, Any]) -> None:
        super().__init__(body.get("detail", "tip unavailable"))
        self.body = body


def _resolve_state_path(state_path: Union[str, Path]) -> Path:
    p = Path(state_path)
    if not p.is_absolute():
        # tip_state.py lives at src/opentrons_server/gateway/, so the repo root
        # is four levels up — same anchoring as PlateStateStore.
        p = (Path(__file__).resolve().parents[3] / p).resolve()
    return p


class TipStateStore:
    """Thread-safe, JSON-backed per-rack tip status map.

    ``register_rack`` is non-destructive (a re-``setup`` after a gateway restart
    must not forget which tips are already used); ``reset_rack`` is the explicit
    "a fresh rack was physically swapped in" operation.
    """

    def __init__(self, *, state_path: Union[str, Path]) -> None:
        self._path = _resolve_state_path(state_path)
        self._lock = threading.Lock()
        self._racks: Dict[str, TipRackState] = {}
        self._load_from_disk()

    @property
    def state_path(self) -> Path:
        return self._path

    # ---- rack lifecycle ------------------------------------------------

    def has_rack(self, nickname: str) -> bool:
        with self._lock:
            return nickname in self._racks

    def racks(self) -> Dict[str, TipRackState]:
        with self._lock:
            return {n: r.model_copy(deep=True) for n, r in self._racks.items()}

    def register_rack(
        self, nickname: str, *, wells: Optional[List[str]] = None
    ) -> TipRackState:
        """Start tracking ``nickname``; keep existing statuses if already known."""

        with self._lock:
            existing = self._racks.get(nickname)
            if existing is not None:
                return existing.model_copy(deep=True)
            rack = self._fresh_rack(nickname, wells)
            self._racks[nickname] = rack
            self._persist_locked()
            return rack.model_copy(deep=True)

    def reset_rack(
        self, nickname: str, *, wells: Optional[List[str]] = None
    ) -> TipRackState:
        """(Re)register ``nickname`` with every tip fresh — a physical rack swap."""

        with self._lock:
            rack = self._fresh_rack(nickname, wells)
            self._racks[nickname] = rack
            self._persist_locked()
            return rack.model_copy(deep=True)

    def remove_rack(self, nickname: str) -> None:
        with self._lock:
            if self._racks.pop(nickname, None) is not None:
                self._persist_locked()

    # ---- per-tip status ------------------------------------------------

    def status(self, nickname: str, well: str) -> str:
        with self._lock:
            rack = self._require_rack_locked(nickname)
            return self._require_well_locked(rack, well)

    def set_status(self, nickname: str, well: str, status: str) -> None:
        with self._lock:
            rack = self._require_rack_locked(nickname)
            self._require_well_locked(rack, well)
            rack.tips[well] = status
            self._persist_locked()

    def set_statuses(self, nickname: str, wells: List[str], status: str) -> None:
        """Set several wells at once, validating all of them before mutating any.

        One persist for the whole set, so a multi-channel pick or drop can never
        leave half a column recorded.
        """

        with self._lock:
            rack = self._require_rack_locked(nickname)
            for well in wells:
                self._require_well_locked(rack, well)
            for well in wells:
                rack.tips[well] = status
            self._persist_locked()

    def covered_wells(
        self,
        nickname: str,
        well: str,
        *,
        channels: int = 1,
        sample_id: Optional[str] = None,
    ) -> List[str]:
        """The wells in ``nickname`` a ``channels``-wide head takes tips from.

        ``channels=1`` is exactly ``[well]``. A multi-channel pick must be
        addressed at row A — the head spans downward from there — so a lower
        start is refused with :class:`TipUnavailable` rather than silently
        tracking the wrong wells.
        """

        with self._lock:
            rack = self._require_rack_locked(nickname)
            self._require_well_locked(rack, well)
            known = set(rack.tips)
        if channels > 1 and _split_well(well)[0] != _ROW_LETTERS[0]:
            raise TipUnavailable(
                {
                    "detail": (
                        f"A {channels}-channel pick must be addressed at row "
                        f"{_ROW_LETTERS[0]}, not {nickname} {well}"
                    ),
                    "rack": nickname,
                    "well": well,
                    "tip_status": None,
                    "requested_sample_id": sample_id,
                    "channels": channels,
                    "covered_wells": None,
                    "retry_after_s": None,
                }
            )
        covered = covered_well_span(well, channels)
        missing = [w for w in covered if w not in known]
        if missing:
            raise ValueError(
                f"Rack {nickname!r} has no well(s) {missing}, covered by a "
                f"{channels}-channel pick at {well}"
            )
        return covered

    def validate_pick(
        self,
        nickname: str,
        well: str,
        *,
        sample_id: Optional[str] = None,
        force: bool = False,
        channels: int = 1,
    ) -> Optional[str]:
        """Refuse or allow a ``channels``-wide pick addressed at ``well``.

        Returns the prior status of the **addressed** well when the pick is
        allowed (``None`` for a fresh tip); raises :class:`TipUnavailable`
        otherwise.

        Every well the head covers must be pickable. A multi-channel pick takes
        a whole column in one motion, so a partially-consumed column is not
        pickable at all — the refusal names the well that blocks it, since
        "some tip in that column is gone" is not actionable on its own.
        """

        covered = self.covered_wells(
            nickname, well, channels=channels, sample_id=sample_id
        )
        if channels == 1:
            return self._validate_one(nickname, covered[0], sample_id=sample_id, force=force)
        addressed: Optional[str] = None
        for index, covered_well in enumerate(covered):
            try:
                prior = self._validate_one(
                    nickname, covered_well, sample_id=sample_id, force=force
                )
            except TipUnavailable as exc:
                raise TipUnavailable(
                    {
                        "detail": (
                            f"Cannot pick {channels} tips at {nickname} {well}: "
                            + str(exc.body.get("detail", "tip unavailable"))
                        ),
                        "rack": nickname,
                        "well": well,
                        "tip_status": exc.body.get("tip_status"),
                        "requested_sample_id": sample_id,
                        "channels": channels,
                        "covered_wells": covered,
                        "blocking_well": covered_well,
                        "retry_after_s": None,
                    }
                ) from exc
            if index == 0:
                addressed = prior
        return addressed

    def _validate_one(
        self,
        nickname: str,
        well: str,
        *,
        sample_id: Optional[str] = None,
        force: bool = False,
    ) -> Optional[str]:
        """Single-well pickability: fresh, same-sample reuse, or ``force``.

        An ``"empty"`` well is refused even with ``force`` — force overrides the
        contamination guard, not the absence of a tip.
        """

        status = self.status(nickname, well)
        normalized = status.strip().lower()
        if normalized in _FRESH_STATUSES:
            return None
        if normalized == EMPTY:
            raise TipUnavailable(
                {
                    "detail": f"No tip at {nickname} {well}: already used and dropped",
                    "rack": nickname,
                    "well": well,
                    "tip_status": EMPTY,
                    "requested_sample_id": sample_id,
                    "retry_after_s": None,
                }
            )
        if sample_id is not None and status == sample_id:
            return status
        if force:
            return status
        raise TipUnavailable(
            {
                "detail": (
                    f"Tip at {nickname} {well} already touched {status!r}"
                    + (f", requested {sample_id!r}" if sample_id else "")
                    + "; pass force=true to override"
                ),
                "rack": nickname,
                "well": well,
                "tip_status": status,
                "requested_sample_id": sample_id,
                "retry_after_s": None,
            }
        )

    def next_available(
        self,
        nickname: str,
        *,
        sample_id: Optional[str] = None,
        start_well: Optional[str] = None,
        channels: int = 1,
    ) -> str:
        """First well a ``channels``-wide head can pick from, column-major.

        Pickable means fresh, or matching ``sample_id``. For a multi-channel head
        it means **every** well the head covers is pickable, so the scan only
        considers valid row-A starts and effectively steps by column
        (A1 -> A2 -> ...). Returning a lower row would put the trailing channels
        over wells whose tips are already on the head.
        """

        with self._lock:
            rack = self._require_rack_locked(nickname)
            wells = list(rack.tips.keys())
        if start_well is not None:
            if start_well not in wells:
                raise ValueError(f"start_well {start_well!r} is not in rack {nickname!r}")
            wells = wells[wells.index(start_well) :]
        for well in wells:
            try:
                covered = self.covered_wells(
                    nickname, well, channels=channels, sample_id=sample_id
                )
            except (TipUnavailable, ValueError):
                continue  # not a valid start for this head: wrong row, or off the rack
            if all(self._is_pickable(nickname, w, sample_id=sample_id) for w in covered):
                return well
        body: Dict[str, Any] = {
            "detail": f"No available tip in {nickname!r}"
            + (f" for a {channels}-channel pipette" if channels > 1 else "")
            + (f" for sample {sample_id!r}" if sample_id else ""),
            "rack": nickname,
            "well": None,
            "tip_status": None,
            "requested_sample_id": sample_id,
            "retry_after_s": None,
        }
        if channels > 1:
            body["channels"] = channels
        raise TipUnavailable(body)

    def _is_pickable(
        self, nickname: str, well: str, *, sample_id: Optional[str] = None
    ) -> bool:
        status = self.status(nickname, well)
        normalized = status.strip().lower()
        if normalized in _FRESH_STATUSES:
            return True
        return sample_id is not None and status == sample_id

    def summary(self) -> Dict[str, Any]:
        """Compact per-rack view for ``/status`` — full map plus counts."""

        out: Dict[str, Any] = {}
        for nickname, rack in self.racks().items():
            fresh = sum(1 for s in rack.tips.values() if s.strip().lower() in _FRESH_STATUSES)
            empty = sum(1 for s in rack.tips.values() if s.strip().lower() == EMPTY)
            out[nickname] = {
                "total": len(rack.tips),
                "available": fresh,
                "empty": empty,
                "touched": len(rack.tips) - fresh - empty,
                "tips": {w: s for w, s in rack.tips.items() if s.strip().lower() not in _FRESH_STATUSES},
                "registered_at": rack.registered_at.isoformat(),
            }
        return out

    # ---- internals ------------------------------------------------------

    def _fresh_rack(self, nickname: str, wells: Optional[List[str]]) -> TipRackState:
        well_list = wells if wells else tip_well_order_96()
        seen: set[str] = set()
        for w in well_list:
            if w in seen:
                raise ValueError(f"Duplicate well id {w!r}")
            seen.add(w)
        return TipRackState(
            nickname=nickname,
            tips={w: FRESH for w in well_list},
            registered_at=datetime.now(timezone.utc),
        )

    def _require_rack_locked(self, nickname: str) -> TipRackState:
        rack = self._racks.get(nickname)
        if rack is None:
            raise LookupError(f"Tip rack {nickname!r} is not registered")
        return rack

    @staticmethod
    def _require_well_locked(rack: TipRackState, well: str) -> str:
        if well not in rack.tips:
            raise ValueError(f"Well {well!r} is not in rack {rack.nickname!r}")
        return rack.tips[well]

    def _load_from_disk(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("tip state at %s is unreadable; ignoring", self._path)
            return
        parsed: Dict[str, TipRackState] = {}
        for nickname, rack_raw in (raw.get("racks") or {}).items():
            try:
                parsed[str(nickname)] = TipRackState.model_validate(rack_raw)
            except Exception:
                logger.exception("tip state rack %s is malformed; ignoring", nickname)
        self._racks = parsed

    def _persist_locked(self) -> None:
        body = {"racks": {n: r.model_dump(mode="json") for n, r in self._racks.items()}}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self._path)  # atomic on POSIX + Windows
        except OSError:
            logger.exception("Failed to persist tip state to %s", self._path)


__all__ = [
    "TipStateStore",
    "TipUnavailable",
    "covered_well_span",
    "tip_well_order_96",
    "FRESH",
    "EMPTY",
]
