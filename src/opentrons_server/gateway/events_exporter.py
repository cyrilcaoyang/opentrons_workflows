"""Best-effort event push to the lab dashboard's history DB.

The device-side exporter described in ac-organic-lab's `OBSERVABILITY.md`:
device services push domain events via ``POST /api/ingest/events`` rather than
writing the history DB directly (ARCHITECTURE decision #9 — one writer, one
host). Ported from the xArm's exporter, which established the pattern.

**What this is for.** The aggregator already polls ``/status`` and records
coarse state, so this deliberately carries only what a poll *cannot* see:

- ``control_action`` — one row per ``/control/*`` command with its outcome and
  the claim holder who caused it. Operator writes made in this gateway's own
  UI go straight to the device, so they never produce the audit row the
  dashboard's passthrough writes (ARCHITECTURE decision #1). Now they do.
- ``tip_pickup`` / ``tip_drop`` / ``tips_reset`` — tip lifecycle, which lives
  only as *current* state in ``ot2_tip_state.json``. The file says which tips
  are gone; these rows say when they went and for which sample, so tip usage
  is answerable per run instead of per glance. An 8-channel pick carries the
  whole covered column, which is the accounting that was wrong before the
  multi-channel fix.
- ``startup`` / ``shutdown`` / ``error`` — session edges.

Both are sub-poll-interval events: a command that starts and finishes between
two 60 s polls is not undercounted by the aggregator, it is invisible to it —
the same reasoning STATUS_SPEC §2.3.1 gives for ``cycles_total``.

Design constraints, in order:

1. **Never block or break the control path.** ``emit()`` only enqueues onto a
   bounded in-memory queue; a daemon thread does the HTTP POST. Full queue or
   unreachable dashboard → the event is dropped and the robot keeps working.
   The aggregator's poll remains the coarse backstop, so a dropped row
   degrades timing fidelity, not truth.
2. **Stdlib only.** ``urllib``, not a new HTTP dependency.
3. **Disabled unless configured.** Without ``OT2_INGEST_URL`` the exporter is
   a no-op, so dev checkouts, unit tests and dry-run gateways emit nothing —
   a simulated run must never enter the lab's history as real work.

Configuration (environment):

- ``OT2_INGEST_URL`` — full ingest endpoint, e.g.
  ``http://sdl2-server-gaia.tail6a1dd7.ts.net:8001/api/ingest/events``.
  Unset/empty disables the exporter.
- ``OT2_INGEST_DEVICE_ID`` — ``device_id`` stamped on every record. Defaults
  to ``OT2_EQUIPMENT_ID`` so a two-gateway host attributes each robot
  correctly without a second variable to keep in sync.

Wire format matches the dashboard's ``IngestEventsRequest``:
``{device_id, records: [...]}`` where each record carries ``timestamp`` (UTC
ISO-8601), ``event``, optional ``from_state`` / ``to_state`` / ``message``,
and an ``extra`` dict the ingest handler folds into the persisted payload
verbatim. ``owner``, ``well`` and ``duration_s`` are keys that handler already
recognises, so they are used here rather than near-synonyms.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_ID = "ot2"

# Log at most the first failure of a streak, then every Nth, so an unreachable
# dashboard doesn't flood the service log.
_FAILURE_LOG_EVERY = 50

_SENTINEL = object()


def _utc_now_iso() -> str:
    """UTC ISO-8601 with a Z suffix, per STATUS_SPEC timestamp rules."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EventsExporter:
    """Queue-and-forward exporter for ``POST /api/ingest/events``.

    ``transport`` is injectable for tests: a callable taking the payload dict
    and raising on delivery failure. The default POSTs with ``urllib`` and
    treats any non-2xx / socket error as a failure — the batch is dropped,
    never retried, because history fidelity is best-effort by contract and a
    retry queue is one more thing to go wrong on the control path.
    """

    def __init__(
        self,
        ingest_url: Optional[str],
        device_id: str = DEFAULT_DEVICE_ID,
        *,
        timeout_s: float = 3.0,
        queue_size: int = 256,
        batch_max: int = 32,
        transport: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.ingest_url = (ingest_url or "").strip() or None
        self.device_id = device_id
        self._timeout_s = timeout_s
        self._batch_max = batch_max
        self._transport = transport or self._default_transport
        self._consecutive_failures = 0
        self._dropped = 0
        self._queue: Optional[queue.Queue] = None
        self._thread: Optional[threading.Thread] = None
        if self.ingest_url:
            self._queue = queue.Queue(maxsize=queue_size)
            self._thread = threading.Thread(
                target=self._worker, name="ot2-events-exporter", daemon=True
            )
            self._thread.start()

    @classmethod
    def from_env(cls, environ: Optional[dict] = None) -> "EventsExporter":
        env = os.environ if environ is None else environ
        url = (env.get("OT2_INGEST_URL") or "").strip()
        device_id = (
            (env.get("OT2_INGEST_DEVICE_ID") or "").strip()
            or (env.get("OT2_EQUIPMENT_ID") or "").strip()
            or DEFAULT_DEVICE_ID
        )
        return cls(url or None, device_id)

    @property
    def enabled(self) -> bool:
        return self._queue is not None

    def emit(
        self,
        event: str,
        *,
        from_state: Optional[str] = None,
        to_state: Optional[str] = None,
        message: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        """Enqueue one record. Returns False when disabled or dropped.

        Never raises and never blocks: callers are control paths that must not
        stall on observability.
        """

        if self._queue is None:
            return False
        record: dict[str, Any] = {"timestamp": _utc_now_iso(), "event": event}
        if from_state is not None:
            record["from_state"] = from_state
        if to_state is not None:
            record["to_state"] = to_state
        if message is not None:
            record["message"] = message
        cleaned = {k: v for k, v in extra.items() if v is not None}
        if cleaned:
            record["extra"] = cleaned
        try:
            self._queue.put_nowait(record)
            return True
        except queue.Full:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % _FAILURE_LOG_EVERY == 0:
                logger.warning("[events] queue full — dropped %d event(s) so far", self._dropped)
            return False

    def close(self, timeout_s: float = 5.0) -> None:
        """Best-effort flush + stop. Safe on a disabled exporter."""

        if self._queue is None or self._thread is None:
            return
        try:
            self._queue.put_nowait(_SENTINEL)
        except queue.Full:
            pass
        self._thread.join(timeout=timeout_s)

    # ---- worker side ----------------------------------------------------

    def _worker(self) -> None:
        assert self._queue is not None
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                return
            batch = [item]
            while len(batch) < self._batch_max:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is _SENTINEL:
                    self._send(batch)
                    return
                batch.append(nxt)
            self._send(batch)

    def _send(self, records: list) -> None:
        payload = {"device_id": self.device_id, "records": records}
        try:
            self._transport(payload)
        except Exception as exc:  # noqa: BLE001 — best-effort by contract
            self._consecutive_failures += 1
            if (
                self._consecutive_failures == 1
                or self._consecutive_failures % _FAILURE_LOG_EVERY == 0
            ):
                logger.warning(
                    "[events] ingest POST failed (%d in a row), dropping %d record(s): %s",
                    self._consecutive_failures,
                    len(records),
                    exc,
                )
        else:
            if self._consecutive_failures:
                logger.info(
                    "[events] ingest recovered after %d failure(s)", self._consecutive_failures
                )
            self._consecutive_failures = 0

    def _default_transport(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.ingest_url,  # type: ignore[arg-type]  # None ⇒ no worker runs
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout_s) as resp:
            if not 200 <= resp.status < 300:
                raise RuntimeError(f"ingest returned HTTP {resp.status}")


__all__ = ["EventsExporter", "DEFAULT_DEVICE_ID"]
