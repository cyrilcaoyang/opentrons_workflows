"""Run-engine HTTP client for driving an OT-2 over the robot-server API.

This is the HTTP replacement for the SSH REPL transport (``OT2Control`` /
``SSHClient``). It speaks the Opentrons robot-server run engine on ``:31950``:
create a run, post commands one at a time, register custom labware, read run
state. See ``docs/HTTP_DRIVE_PLAN.md`` for the full migration design.

Driving model (confirmed against Opentrons v8.7.0 source):

- We create a run with ``POST /runs`` and **never issue ``play``**. Every command
  is posted with ``intent="setup"`` and executes immediately while the run stays
  in the SETUP phase. Issuing ``play`` would move the run to RUNNING and cause the
  engine to reject all further setup commands (``SetupCommandNotAllowedError``).
- ``waitUntilComplete=true`` blocks the POST until the command reaches a terminal
  status; a terminal ``failed`` status is surfaced as :class:`CommandFailed`.
- ``manualMoveWithPause`` is deliberately unsupported (it pauses the run out of
  SETUP and cannot be resumed without ``play``); plate moves use
  ``manualMoveWithoutPause`` — see :class:`RunEngineCommands`.

Every request/response uses the ``{"data": {...}}`` envelope and carries the
``Opentrons-Version`` header.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

# Off-deck location literal (confirmed: bare JSON string, not an object).
OFF_DECK = "offDeck"

_DEFAULT_OPENTRONS_VERSION = os.getenv("OT2_OPENTRONS_VERSION", "3")
# Wall-clock ceiling for a single blocking command, mirrors OT2_SSH_COMMAND_TIMEOUT.
_DEFAULT_COMMAND_TIMEOUT_S = float(os.getenv("OT2_HTTP_COMMAND_TIMEOUT", "120"))
# Timeout for non-command control-plane calls (create run, get run, register def).
_DEFAULT_REQUEST_TIMEOUT_S = float(os.getenv("OT2_HTTP_TIMEOUT", "10"))


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RunEngineError(Exception):
    """Base for run-engine client failures."""


class RunEngineUnreachable(RunEngineError, OSError):
    """Transport-level failure reaching the robot-server (timeout / refused).

    Subclasses ``OSError`` so the gateway's existing transport-loss handling in
    ``OT2Service._run_action`` (which catches ``OSError``) treats a dropped HTTP
    call the same way it treats a dropped SSH call — i.e. a non-idempotent action
    lands in ``UNKNOWN_OUTCOME`` rather than a plain error.
    """


class RunEngineHTTPError(RunEngineError):
    """The robot-server returned a non-2xx response to a control-plane call."""

    def __init__(self, status_code: int, detail: str, *, path: str) -> None:
        self.status_code = status_code
        self.detail = detail
        self.path = path
        super().__init__(f"{status_code} from {path}: {detail}")


class CommandFailed(RunEngineError):
    """A posted command reached a terminal ``failed`` status.

    ``error_type`` / ``detail`` / ``error_code`` are extracted from the command's
    ``error`` object when present, so callers can branch on the failure kind
    instead of string-matching.
    """

    def __init__(self, command: Dict[str, Any]) -> None:
        self.command = command
        error = command.get("error") or {}
        self.error_type: Optional[str] = error.get("errorType")
        self.error_code: Optional[str] = error.get("errorCode")
        self.detail: str = error.get("detail") or "command failed"
        super().__init__(
            f"{command.get('commandType', 'command')} failed: {self.detail}"
            + (f" ({self.error_type})" if self.error_type else "")
        )


# ---------------------------------------------------------------------------
# Command builders — encode the confirmed v8.7.0 param schemas in one place.
# Each returns a (commandType, params) pair ready for RunEngineClient.execute().
# ---------------------------------------------------------------------------


Command = Tuple[str, Dict[str, Any]]
SlotName = Union[str, int]
# A LoadableLabwareLocation: a deck slot object, the off-deck literal, or a
# module/on-labware object the caller supplies directly.
Location = Union[str, Dict[str, Any]]


def deck_slot(slot: SlotName) -> Dict[str, str]:
    """A DeckSlotLocation for an OT-2 slot (``"1"``..``"12"``)."""
    return {"slotName": str(slot)}


def _well_location(
    origin: Optional[str],
    offset: Optional[Dict[str, float]],
    *,
    volume_offset: Optional[Union[float, str]] = None,
) -> Optional[Dict[str, Any]]:
    if origin is None and offset is None and volume_offset is None:
        return None
    loc: Dict[str, Any] = {}
    if origin is not None:
        loc["origin"] = origin
    if offset is not None:
        loc["offset"] = offset
    if volume_offset is not None:
        loc["volumeOffset"] = volume_offset
    return loc


class RunEngineCommands:
    """Builders for the run-engine commands the gateway needs.

    ``origin`` values: ``top`` | ``bottom`` | ``center`` | ``meniscus`` for
    aspirate/dispense; ``top`` | ``bottom`` | ``center`` for pick-up-tip;
    ``top`` | ``bottom`` | ``center`` | ``default`` for drop-tip. ``offset`` is
    ``{"x","y","z"}`` in mm. ``flow_rate`` is µL/s; ``volume`` is µL.
    """

    @staticmethod
    def load_pipette(
        pipette_name: str,
        mount: str,
        *,
        pipette_id: Optional[str] = None,
    ) -> Command:
        params: Dict[str, Any] = {"pipetteName": pipette_name, "mount": mount}
        if pipette_id is not None:
            params["pipetteId"] = pipette_id
        return "loadPipette", params

    @staticmethod
    def load_labware(
        load_name: str,
        namespace: str,
        version: int,
        location: Location,
        *,
        labware_id: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Command:
        params: Dict[str, Any] = {
            "location": location,
            "loadName": load_name,
            "namespace": namespace,
            "version": int(version),
        }
        if labware_id is not None:
            params["labwareId"] = labware_id
        if display_name is not None:
            params["displayName"] = display_name
        return "loadLabware", params

    @staticmethod
    def load_module(
        model: str,
        location: Dict[str, Any],
        *,
        module_id: Optional[str] = None,
    ) -> Command:
        params: Dict[str, Any] = {"model": model, "location": location}
        if module_id is not None:
            params["moduleId"] = module_id
        return "loadModule", params

    @staticmethod
    def pick_up_tip(
        pipette_id: str,
        labware_id: str,
        well_name: str,
        *,
        origin: Optional[str] = None,
        offset: Optional[Dict[str, float]] = None,
    ) -> Command:
        params: Dict[str, Any] = {
            "pipetteId": pipette_id,
            "labwareId": labware_id,
            "wellName": well_name,
        }
        well = _well_location(origin, offset)
        if well is not None:
            params["wellLocation"] = well
        return "pickUpTip", params

    @staticmethod
    def aspirate(
        pipette_id: str,
        labware_id: str,
        well_name: str,
        volume: float,
        flow_rate: float,
        *,
        origin: str = "bottom",
        offset: Optional[Dict[str, float]] = None,
    ) -> Command:
        params: Dict[str, Any] = {
            "pipetteId": pipette_id,
            "labwareId": labware_id,
            "wellName": well_name,
            "volume": float(volume),
            "flowRate": float(flow_rate),
            "wellLocation": _well_location(origin, offset or {"x": 0, "y": 0, "z": 0}),
        }
        return "aspirate", params

    @staticmethod
    def dispense(
        pipette_id: str,
        labware_id: str,
        well_name: str,
        volume: float,
        flow_rate: float,
        *,
        origin: str = "bottom",
        offset: Optional[Dict[str, float]] = None,
        push_out: Optional[float] = None,
    ) -> Command:
        params: Dict[str, Any] = {
            "pipetteId": pipette_id,
            "labwareId": labware_id,
            "wellName": well_name,
            "volume": float(volume),
            "flowRate": float(flow_rate),
            "wellLocation": _well_location(origin, offset or {"x": 0, "y": 0, "z": 0}),
        }
        if push_out is not None:
            params["pushOut"] = float(push_out)
        return "dispense", params

    @staticmethod
    def drop_tip(
        pipette_id: str,
        labware_id: str,
        well_name: str,
        *,
        origin: Optional[str] = None,
        offset: Optional[Dict[str, float]] = None,
        home_after: Optional[bool] = None,
    ) -> Command:
        params: Dict[str, Any] = {
            "pipetteId": pipette_id,
            "labwareId": labware_id,
            "wellName": well_name,
        }
        well = _well_location(origin, offset)
        if well is not None:
            params["wellLocation"] = well
        if home_after is not None:
            params["homeAfter"] = home_after
        return "dropTip", params

    @staticmethod
    def drop_tip_in_place(
        pipette_id: str,
        *,
        home_after: Optional[bool] = None,
    ) -> Command:
        params: Dict[str, Any] = {"pipetteId": pipette_id}
        if home_after is not None:
            params["homeAfter"] = home_after
        return "dropTipInPlace", params

    @staticmethod
    def blow_out(
        pipette_id: str,
        flow_rate: float,
        labware_id: str,
        well_name: str,
        *,
        origin: str = "top",
        offset: Optional[Dict[str, float]] = None,
    ) -> Command:
        params: Dict[str, Any] = {
            "pipetteId": pipette_id,
            "flowRate": float(flow_rate),
            "labwareId": labware_id,
            "wellName": well_name,
            "wellLocation": _well_location(origin, offset or {"x": 0, "y": 0, "z": 0}),
        }
        return "blowout", params

    @staticmethod
    def move_labware(
        labware_id: str,
        new_location: Location,
        *,
        strategy: str = "manualMoveWithoutPause",
    ) -> Command:
        """Move a previously-loaded labware.

        ``strategy`` is fixed to ``manualMoveWithoutPause`` by default and that is
        the only value this gateway supports on an OT-2: it records the location
        change and succeeds immediately without leaving the SETUP phase.
        ``manualMoveWithPause`` and ``usingGripper`` are rejected (the former
        breaks the never-played model; the latter is Flex-only).
        """
        if strategy != "manualMoveWithoutPause":
            raise ValueError(
                "OT-2 gateway only supports moveLabware strategy "
                f"'manualMoveWithoutPause'; got {strategy!r} "
                "(manualMoveWithPause breaks the setup-only run; usingGripper is Flex-only)"
            )
        return "moveLabware", {
            "labwareId": labware_id,
            "newLocation": new_location,
            "strategy": strategy,
        }

    @staticmethod
    def home(axes: Optional[List[str]] = None) -> Command:
        params: Dict[str, Any] = {}
        if axes is not None:
            params["axes"] = axes
        return "home", params


# ---------------------------------------------------------------------------
# Transport client
# ---------------------------------------------------------------------------


class RunEngineClient:
    """Thin client over the robot-server run engine.

    Holds one run id (created via :meth:`create_run`). Not thread-safe: the
    gateway serialises control actions through its own state machine.
    """

    def __init__(
        self,
        base_url: str,
        *,
        session: Optional[requests.Session] = None,
        opentrons_version: str = _DEFAULT_OPENTRONS_VERSION,
        command_timeout_s: float = _DEFAULT_COMMAND_TIMEOUT_S,
        request_timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._owns_session = session is None
        self._headers = {"Opentrons-Version": opentrons_version}
        self.command_timeout_s = command_timeout_s
        self.request_timeout_s = request_timeout_s
        self.run_id: Optional[str] = None

    # -- lifecycle ---------------------------------------------------------

    def create_run(self) -> str:
        """Create an empty (protocol-less) run and remember its id."""
        data = self._request(
            "POST", "/runs", json_body={"data": {}}, timeout=self.request_timeout_s
        )
        run_id = data.get("id")
        if not run_id:
            raise RunEngineError(f"run creation returned no id: {data!r}")
        self.run_id = run_id
        return run_id

    def stop_run(self) -> None:
        """Best-effort ``stop`` action; safe to call without a run."""
        if self.run_id is None:
            return
        try:
            self._request(
                "POST",
                f"/runs/{self.run_id}/actions",
                json_body={"data": {"actionType": "stop"}},
                timeout=self.request_timeout_s,
            )
        except RunEngineError:
            pass  # shutdown is best-effort; the run id is discarded regardless

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    # -- commands ----------------------------------------------------------

    def execute(
        self,
        command: Command,
        *,
        intent: str = "setup",
        wait: bool = True,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Post one command and (by default) block until it finishes.

        Returns the command resource. Raises :class:`CommandFailed` if the command
        reached a terminal ``failed`` status.
        """
        if self.run_id is None:
            raise RunEngineError("no active run; call create_run() first")
        command_type, params = command

        params_q: Dict[str, Any] = {}
        read_timeout = self.request_timeout_s
        if wait:
            params_q["waitUntilComplete"] = "true"
            effective_ms = (
                timeout_ms if timeout_ms is not None else int(self.command_timeout_s * 1000)
            )
            params_q["timeout"] = effective_ms
            # Keep the socket read timeout comfortably above the server-side wait
            # so requests does not abort before the run engine returns.
            read_timeout = effective_ms / 1000.0 + 5.0

        body = {"data": {"commandType": command_type, "params": params, "intent": intent}}
        result = self._request(
            "POST",
            f"/runs/{self.run_id}/commands",
            json_body=body,
            params=params_q,
            timeout=read_timeout,
        )
        if result.get("status") == "failed":
            raise CommandFailed(result)
        return result

    def get_command(self, command_id: str) -> Dict[str, Any]:
        if self.run_id is None:
            raise RunEngineError("no active run")
        return self._request(
            "GET",
            f"/runs/{self.run_id}/commands/{command_id}",
            timeout=self.request_timeout_s,
        )

    # -- run state / labware ----------------------------------------------

    def get_run(self) -> Dict[str, Any]:
        if self.run_id is None:
            raise RunEngineError("no active run")
        return self._request("GET", f"/runs/{self.run_id}", timeout=self.request_timeout_s)

    def add_labware_definition(self, definition: Dict[str, Any]) -> str:
        """Register a custom labware definition on the run; return its uri.

        Idempotent server-side: re-registering the same namespace/loadName/version
        overwrites and returns the same uri.
        """
        if self.run_id is None:
            raise RunEngineError("no active run; call create_run() first")
        data = self._request(
            "POST",
            f"/runs/{self.run_id}/labware_definitions",
            json_body={"data": definition},
            timeout=self.request_timeout_s,
        )
        uri = data.get("definitionUri")
        if not uri:
            raise RunEngineError(f"labware definition registration returned no uri: {data!r}")
        return uri

    # -- internals ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: float,
    ) -> Dict[str, Any]:
        """Issue one request, unwrap the ``{"data": ...}`` envelope.

        Maps transport failures to :class:`RunEngineUnreachable` and non-2xx to
        :class:`RunEngineHTTPError`.
        """
        url = self.base_url + path
        try:
            response = self._session.request(
                method,
                url,
                json=json_body,
                params=params or None,
                headers=self._headers,
                timeout=timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise RunEngineUnreachable(f"{method} {path}: {exc}") from exc
        except requests.RequestException as exc:
            raise RunEngineError(f"{method} {path}: {exc}") from exc

        if response.status_code >= 400:
            raise RunEngineHTTPError(response.status_code, _error_detail(response), path=path)

        try:
            payload = response.json()
        except ValueError as exc:
            raise RunEngineError(f"{method} {path}: non-JSON response") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        return data if data is not None else {}


def _error_detail(response: requests.Response) -> str:
    """Pull a human-readable detail out of an Opentrons error response body."""
    try:
        body = response.json()
    except ValueError:
        return (response.text or "").strip()[:500] or "<empty body>"
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                return first.get("detail") or first.get("title") or str(first)
        if "detail" in body:
            return str(body["detail"])
    return str(body)[:500]
