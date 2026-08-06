"""FastAPI app for an AC-compatible OT-2 gateway."""

from __future__ import annotations

import hmac
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from .claims import ClaimConflict, UnknownClaim
from .deck import DeckDeclarationStore
from .labware import standard_definition, standard_summaries
from .models import (
    ClaimRejection,
    ClaimResponse,
    CommandResponse,
    DeckDeclareRequest,
    DeckState,
    EquipmentStatus,
    GatewayClaimRequest,
    HealthResponse,
    LightsRequest,
    LiquidMoveRequest,
    LoadedPlate,
    MoveLabwareRequest,
    MoveToRequest,
    PlateLoadRequest,
    ProbeResponse,
    PROTOCOL_VERSION,
    ProtocolSetupRequest,
    StartupRequest,
    TipRackState,
    TipRequest,
    TipsResetRequest,
    WellSample,
    WellUpdateRequest,
)
from .plate_state import PlateStateStore
from .service import OT2Service, UnknownOutcomeError
from .tip_state import TipStateStore, TipUnavailable


UI_DIST_DIR = Path(__file__).resolve().parent.parent / "ui_dist"

logger = logging.getLogger("opentrons_server.gateway")


def _configure_logging() -> None:
    """Route this package's own loggers to stderr.

    Under uvicorn nothing configures application loggers, so Python falls back
    to ``logging.lastResort`` — WARNING and above only. Every INFO breadcrumb
    from the SSH/REPL boot path (notably ``SSH connection established … in
    SHELL mode``) was therefore dropped, which made a slow-but-healthy
    ``connecting`` boot indistinguishable from a hang in the service logs.

    Level is ``OT2_LOG_LEVEL`` (default INFO). Attaches to the package root so
    ``opentrons_server.*`` module loggers all inherit it, and is idempotent —
    ``create_app()`` runs more than once under tests.
    """

    pkg = logging.getLogger("opentrons_server")
    level = os.getenv("OT2_LOG_LEVEL", "INFO").upper()
    pkg.setLevel(getattr(logging, level, logging.INFO))
    if any(getattr(h, "_ot2_gateway_handler", False) for h in pkg.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    handler._ot2_gateway_handler = True  # type: ignore[attr-defined]
    pkg.addHandler(handler)


class SPAStaticFiles(StaticFiles):
    """StaticFiles that serves index.html for unknown non-file paths, so the
    single-page UI survives a hard refresh on any sub-path."""

    async def get_response(self, path: str, scope: Any) -> Any:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


class ClaimHTTPError(Exception):
    def __init__(self, status_code: int, payload: dict[str, Any], headers: Optional[dict[str, str]] = None) -> None:
        super().__init__(payload.get("detail", "claim error"))
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}


def _parse_api_keys(raw: Optional[str]) -> dict[str, str]:
    """Parse ``OT2_API_KEYS`` into ``{principal_name: key}``.

    Accepts ``name:key`` pairs, comma-separated, so an audit row can say *which*
    machine principal acted (``api:solubility-workflow``) rather than just "an
    API key". A bare ``key`` with no name is accepted and reported as
    ``api:unnamed`` — convenient for a quick deployment, worse for the audit
    trail.
    """

    out: dict[str, str] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, key = entry.partition(":")
        if sep and key.strip():
            out[name.strip() or "unnamed"] = key.strip()
        else:
            out["unnamed"] = entry
    return out


def create_app(
    *,
    dry_run: Optional[bool] = None,
    enforce_claims: bool = True,
    auto_reconnect: Optional[bool] = None,
    ui: Optional[bool] = None,
    trust_local_ui: Optional[bool] = None,
    edge_secret: Optional[str] = None,
    require_login: Optional[bool] = None,
    api_keys: Optional[dict[str, str]] = None,
) -> FastAPI:
    _configure_logging()
    if dry_run is None:
        dry_run = os.environ.get("OT2_DRY_RUN", "false").lower() in {"1", "true", "yes"}
    if auto_reconnect is None:
        auto_reconnect = os.environ.get("OT2_AUTO_RECONNECT", "true").lower() in {"1", "true", "yes"}
    if ui is None:
        ui = os.environ.get("OT2_UI", "true").lower() not in {"0", "false", "no", "off"}

    # Open/close switch for the operator UI's auth bypass (an §6.5-style
    # override flag — exists for dev, never for production):
    #   OT2_TRUST_LOCAL_UI=true  — "blind trust": /ui and /labware are served
    #       to anyone who can reach the port; no identity is trusted. Default
    #       for a bare checkout so dev just works; logs loudly at startup.
    #   OT2_TRUST_LOCAL_UI=false — edge-only: /ui and /labware answer only
    #       requests forwarded by the auth edge (X-Edge-Key must match
    #       OT2_EDGE_SECRET, which becomes required); the edge-asserted
    #       X-Auth-User is stamped into claim owners. Direct hits get 404.
    # OT2_UI=off unmounts the UI entirely (headless gateway).
    if trust_local_ui is None:
        trust_local_ui = os.environ.get("OT2_TRUST_LOCAL_UI", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if edge_secret is None:
        edge_secret = os.environ.get("OT2_EDGE_SECRET") or None
    if ui and not trust_local_ui and not edge_secret:
        raise RuntimeError("OT2_TRUST_LOCAL_UI=false requires OT2_EDGE_SECRET to be set")

    # Control-plane identity gate (OT2_REQUIRE_LOGIN, default off so existing
    # and dev deployments are unchanged). Claims are cooperative, NOT
    # authentication — STATUS_SPEC §5 — so without this anyone who can reach
    # the port acquires a claim under any owner they care to type and drives
    # the hardware. Enforced at claim acquisition, the single chokepoint every
    # motion endpoint already sits behind.
    if require_login is None:
        require_login = os.environ.get("OT2_REQUIRE_LOGIN", "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if api_keys is None:
        api_keys = _parse_api_keys(os.environ.get("OT2_API_KEYS"))
    if require_login and not edge_secret and not api_keys:
        # Fail-closed with no way in is a bricked device, not a secure one.
        raise RuntimeError(
            "OT2_REQUIRE_LOGIN=true requires OT2_EDGE_SECRET (for edge-injected "
            "identity) and/or OT2_API_KEYS (for machine principals)"
        )
    if require_login:
        logger.info(
            "OT2_REQUIRE_LOGIN=true: /control/claim requires a verified identity "
            "(edge header%s)",
            " or API key" if api_keys else "",
        )
    if ui and trust_local_ui:
        logger.warning(
            "OT2_TRUST_LOCAL_UI=true: operator UI is served without the auth "
            "edge (dev bypass — set OT2_TRUST_LOCAL_UI=false in production)"
        )
    # Compact summary surfaced at /status details.ui_mode.
    ui_mode = "off" if not ui else ("open" if trust_local_ui else "edge")
    # ... and its control-plane sibling, details.control_auth: "identity" when
    # a verified principal is required to claim, "claim_only" when a claim
    # token is the only gate (cooperative, not authentication), "open" when
    # even that is off. Published so an operator can see which posture a
    # gateway is actually running without reading its service env.
    control_auth = (
        "identity" if require_login else ("claim_only" if enforce_claims else "open")
    )

    service = OT2Service(
        equipment_id=os.environ.get("OT2_EQUIPMENT_ID", "ot2"),
        equipment_name=os.environ.get("OT2_EQUIPMENT_NAME", "Opentrons OT-2"),
        host_alias=os.environ.get("OT2_HOST_ALIAS"),
        password=os.environ.get("OT2_SSH_PASSWORD", ""),
        dry_run=dry_run,
        plates=PlateStateStore(
            state_path=os.environ.get("OT2_PLATE_STATE_PATH", "./ot2_state.json")
        ),
        decks=DeckDeclarationStore(
            state_path=os.environ.get("OT2_DECK_STATE_PATH", "./ot2_deck_state.json")
        ),
        tips=TipStateStore(
            state_path=os.environ.get("OT2_TIP_STATE_PATH", "./ot2_tip_state.json")
        ),
    )

    app = FastAPI(
        title="Opentrons OT-2 Gateway",
        # Tracks the STATUS_SPEC revision this gateway speaks.
        version="1.2.0",
        description=(
            "AC-compatible REST gateway for an Opentrons OT-2 liquid handler. "
            "Conforms to lab status spec v1.2: this device's primary operation "
            "(what `activity` reports) is a protocol command in flight on the "
            "robot, and `metrics.cycles_total` counts the commands completed "
            "since the gateway started."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _principal_for_api_key(supplied: str) -> Optional[str]:
        """Match ``X-Api-Key`` against the configured keys, in constant time.

        Every entry is compared even after a hit, so the response time does not
        reveal which key matched (or how far down the list it was).
        """

        matched: Optional[str] = None
        for name, key in api_keys.items():
            if hmac.compare_digest(supplied, key):
                matched = name
        return matched

    def _resolve_identity(request: Request) -> Optional[str]:
        """The verified principal behind a control request, or ``None``.

        Two credentials, in order, and **no external auth service is
        contacted** — that is deliberate, so this gate is usable by anyone who
        deploys the gateway, not only by this lab:

        1. **Edge-injected identity** — ``X-Auth-User``, trusted only when the
           request also carries a matching ``X-Edge-Key`` (:func:`_from_edge`).
           Any reverse proxy that authenticates a human and sets two headers
           works: our Caddy edge, oauth2-proxy, Authelia, nginx auth_request.
        2. **Static API key** — ``X-Api-Key`` against ``OT2_API_KEYS``, for
           machine principals (workflows, the SDK, agents) that have no browser
           session and no proxy in front of them.

        The returned string is what lands in ``details.claimed_by.owner`` and
        in the audit rows the events exporter writes, so it must name a real
        principal: an edge identity is the person's own name, an API key
        resolves to ``api:<name>`` and never to the key itself.
        """

        if _from_edge(request):
            edge_user = (request.headers.get("X-Auth-User") or "").strip()
            if edge_user:
                return edge_user
        supplied = request.headers.get("X-Api-Key")
        if supplied:
            name = _principal_for_api_key(supplied)
            if name:
                return f"api:{name}"
        return None

    def _require_identity(request: Request) -> Optional[str]:
        """Resolve the principal, refusing the request when login is required.

        Fails closed: with ``OT2_REQUIRE_LOGIN`` on, a request carrying no
        recognised credential is refused outright rather than falling back to
        the caller-supplied owner — which is a string the caller invents.
        """

        identity = _resolve_identity(request)
        if require_login and not identity:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "login_required",
                    "hint": (
                        "This gateway requires a verified identity. Sign in through "
                        "the auth edge, or send X-Api-Key."
                    ),
                },
            )
        return identity

    def _from_edge(request: Request) -> bool:
        """True iff the request provably came through a trusted front: it
        carries the shared secret. Never true when no secret is configured, so
        identity headers on direct requests are always ignored.

        Two accepted header names for the same secret. ``X-Edge-Key`` is what
        this gateway's Caddy block sets. ``X-Edge-Auth`` is the name the
        dashboard's control passthrough already sends
        (``api/app/control.py::_device_auth_headers``, matching the xArm's
        ``XARM_EDGE_SHARED_SECRET``) — the passthrough reaches devices on their
        tailnet ``base_url`` rather than through the edge, so without this
        alias a login-gated gateway would refuse the dashboard while the
        framed panel (which does go through Caddy) kept working. Same
        constant-time comparison either way; only the spelling differs.
        """
        if not edge_secret:
            return False
        for header in ("X-Edge-Key", "X-Edge-Auth"):
            supplied = request.headers.get(header)
            if supplied is not None and hmac.compare_digest(supplied, edge_secret):
                return True
        return False

    if ui and not trust_local_ui:

        @app.middleware("http")
        async def _edge_gate(request: Request, call_next: Any) -> Any:
            path = request.url.path
            gated = (
                path == "/ui"
                or path.startswith("/ui/")
                or path == "/labware"
                or path.startswith("/labware/")
            )
            if gated and not _from_edge(request):
                # 404 (not 401/403): the UI surface simply does not exist for
                # anyone who is not the edge.
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            return await call_next(request)

    @app.exception_handler(ClaimHTTPError)
    async def claim_error_handler(request: Any, exc: ClaimHTTPError) -> JSONResponse:  # noqa: ARG001
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.payload,
            headers=exc.headers,
        )

    def require_claim(
        request: Request,
        x_claim_token: Optional[str] = Header(default=None, alias="X-Claim-Token"),
    ) -> None:
        if not enforce_claims:
            # Claims off, but login on: the claim gate is normally what carries
            # the identity requirement (a token can only be obtained by an
            # authenticated caller), so with claims disabled it has to be
            # checked here or OT2_REQUIRE_LOGIN would be silently bypassed.
            if require_login:
                _require_identity(request)
            return
        if service.claims.validate(x_claim_token):
            return
        current = service.claims.current()
        raise ClaimHTTPError(
            status_code=423,
            payload={
                "detail": "missing or invalid X-Claim-Token; POST /control/claim first",
                "claimed_by": current.model_dump(mode="json") if current else None,
                "retry_after_s": None,
            },
        )

    @app.get("/", response_model=ProbeResponse, tags=["spec"])
    def probe() -> ProbeResponse:
        return ProbeResponse(
            equipment_id=service.equipment_id,
            equipment_name=service.equipment_name,
            protocol_version=PROTOCOL_VERSION,
        )

    @app.get("/health", response_model=HealthResponse, tags=["spec"])
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/status", response_model=EquipmentStatus, tags=["spec"])
    def status() -> EquipmentStatus:
        snapshot = service.get_status()
        snapshot.details["ui_mode"] = ui_mode
        snapshot.details["control_auth"] = control_auth
        return snapshot

    @app.get("/labware", tags=["ui"])
    def labware() -> dict[str, Any]:
        """Read-only catalog of official Opentrons labware definitions
        (grid summaries), for the UI's deck-declare picker. Empty when
        ``opentrons-shared-data`` is not installed."""
        return {"definitions": list(standard_summaries())}

    @app.get("/labware/{load_name}", tags=["ui"])
    def labware_definition(load_name: str) -> dict[str, Any]:
        """One full Opentrons definition, for the UI's side-view cross-section.

        Summaries carry only the grid; the elevation needs real geometry. 404
        covers both "no such load_name" and "``opentrons-shared-data`` is not
        installed" — the UI treats them the same way, by omitting the side view.
        """
        defn = standard_definition(load_name)
        if defn is None:
            raise HTTPException(status_code=404, detail=f"No labware definition {load_name!r}")
        return defn

    @app.post(
        "/control/claim",
        response_model=ClaimResponse,
        responses={409: {"model": ClaimRejection}},
        tags=["claim"],
    )
    def claim(request: GatewayClaimRequest, http_request: Request) -> ClaimResponse:
        # A verified identity always OVERRIDES the body's owner, so
        # details.claimed_by (and every audit row keyed off it) names a real
        # principal rather than a string the caller typed. With
        # OT2_REQUIRE_LOGIN on, the absence of one is a 401 instead.
        identity = _require_identity(http_request)
        if identity:
            request = request.model_copy(update={"owner": identity})
        held = service.claims.current()
        try:
            response = service.claims.acquire(request, takeover=request.takeover)
        except ClaimConflict as exc:
            rejection = ClaimRejection(
                detail=str(exc),
                claimed_by=exc.claimed_by,
                retry_after_s=exc.retry_after_s,
            )
            raise ClaimHTTPError(
                status_code=409,
                payload=rejection.model_dump(mode="json"),
                headers={"Retry-After": str(int(exc.retry_after_s + 1))},
            )
        # Audit the one case where a grant cost somebody else their claim.
        if held is not None and held.session_id != request.session_id:
            logger.warning(
                "claim taken over by owner %r (session %s) from session %s",
                request.owner,
                request.session_id,
                held.session_id,
            )
        return response

    @app.post("/control/heartbeat", response_model=ClaimResponse, tags=["claim"])
    def heartbeat(x_claim_token: Optional[str] = Header(default=None, alias="X-Claim-Token")) -> ClaimResponse:
        try:
            return service.claims.heartbeat(x_claim_token)
        except UnknownClaim:
            raise HTTPException(status_code=401, detail="claim token is unknown or expired")

    @app.post("/control/release", status_code=204, tags=["claim"])
    def release(x_claim_token: Optional[str] = Header(default=None, alias="X-Claim-Token")) -> Response:
        service.claims.release(x_claim_token)
        return Response(status_code=204)

    @app.post("/control/startup", response_model=CommandResponse, tags=["control"])
    def startup(request: StartupRequest, _claim: None = Depends(require_claim)) -> CommandResponse:
        try:
            service.startup(
                host_alias=request.host_alias,
                password=request.password,
                simulation=request.simulation,
            )
            return CommandResponse(message="OT-2 initialized", state=service.state.value)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @app.post("/control/shutdown", response_model=CommandResponse, tags=["control"])
    def shutdown(_claim: None = Depends(require_claim)) -> CommandResponse:
        service.shutdown()
        return CommandResponse(message="OT-2 shutdown", state=service.state.value)

    @app.post("/control/setup", response_model=CommandResponse, tags=["control"])
    def setup(request: ProtocolSetupRequest, _claim: None = Depends(require_claim)) -> CommandResponse:
        try:
            service.setup_protocol(request.model_dump())
            return CommandResponse(message="Protocol setup complete", state=service.state.value)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/control/home", response_model=CommandResponse, tags=["control"])
    def home(_claim: None = Depends(require_claim)) -> CommandResponse:
        try:
            service.home()
            return CommandResponse(message="OT-2 homed", state=service.state.value)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/control/pause", response_model=CommandResponse, tags=["control"])
    def pause(_claim: None = Depends(require_claim)) -> CommandResponse:
        service.pause()
        return CommandResponse(message="OT-2 paused", state=service.state.value)

    @app.post("/control/resume", response_model=CommandResponse, tags=["control"])
    def resume(_claim: None = Depends(require_claim)) -> CommandResponse:
        service.resume()
        return CommandResponse(message="OT-2 resumed", state=service.state.value)

    @app.post("/control/move-to", response_model=CommandResponse, tags=["control"])
    def move_to(request: MoveToRequest, _claim: None = Depends(require_claim)) -> CommandResponse:
        """Move a pipette to a well or absolute deck coordinates (no liquid).

        Body: ``{"pipette": str, "location": WellLocation}`` or
        ``{"pipette": str, "coordinates": {"x","y","z"}}`` (exactly one),
        plus optional ``speed`` (mm/s), ``force_direct``, ``minimum_z_height``.
        Idempotent — a transport loss mid-move records an error, not
        ``unknown_outcome``, and the move can simply be re-issued.
        """
        try:
            service.move_to(request)
            return CommandResponse(message="Move complete", state=service.state.value)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/control/pick-up-tip", response_model=CommandResponse, tags=["control"])
    def pick_up_tip(request: TipRequest, _claim: None = Depends(require_claim)) -> CommandResponse:
        return _run_non_idempotent(lambda: service.pick_up_tip(request), "Tip picked up")

    @app.post("/control/drop-tip", response_model=CommandResponse, tags=["control"])
    def drop_tip(request: TipRequest, _claim: None = Depends(require_claim)) -> CommandResponse:
        return _run_non_idempotent(lambda: service.drop_tip(request), "Tip dropped")

    @app.post("/control/aspirate", response_model=CommandResponse, tags=["control"])
    def aspirate(request: LiquidMoveRequest, _claim: None = Depends(require_claim)) -> CommandResponse:
        return _run_non_idempotent(lambda: service.aspirate(request), "Aspirate complete")

    @app.post("/control/dispense", response_model=CommandResponse, tags=["control"])
    def dispense(request: LiquidMoveRequest, _claim: None = Depends(require_claim)) -> CommandResponse:
        return _run_non_idempotent(lambda: service.dispense(request), "Dispense complete")

    @app.post("/control/move-labware", response_model=CommandResponse, tags=["control"])
    def move_labware(request: MoveLabwareRequest, _claim: None = Depends(require_claim)) -> CommandResponse:
        return _run_non_idempotent(lambda: service.move_labware(request), "Labware moved")

    @app.post("/control/plate/load", response_model=LoadedPlate, tags=["control"])
    def plate_load(request: PlateLoadRequest, _claim: None = Depends(require_claim)) -> LoadedPlate:
        try:
            return service.load_plate(
                plate_id=request.plate_id,
                model=request.model,
                wells=request.wells,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/control/plate/unload", response_model=Optional[LoadedPlate], tags=["control"])
    def plate_unload(_claim: None = Depends(require_claim)) -> Optional[LoadedPlate]:
        return service.unload_plate()

    @app.post("/control/well/update", response_model=WellSample, tags=["control"])
    def well_update(request: WellUpdateRequest, _claim: None = Depends(require_claim)) -> WellSample:
        try:
            return service.update_well(
                request.well,
                sample_id=request.sample_id,
                volume_ul=request.volume_ul,
                notes=request.notes,
                clear_sample_id=request.clear_sample_id,
                clear_notes=request.clear_notes,
            )
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/control/deck/declare", response_model=DeckState, tags=["control"])
    def deck_declare(request: DeckDeclareRequest, _claim: None = Depends(require_claim)) -> DeckState:
        """Set the operator/recipe-declared deck layout (retires the dashboard stopgap).

        Body ``{"slots": {"2": "<load_name>" | {"load_name"|"kind": ...} | null}}``;
        an empty ``slots`` map clears the declaration. Returns the resulting merged
        deck so the caller sees the effect (declared + any observed sources).

        POST (not PUT) so it flows through the dashboard's ``/control/*`` passthrough
        (which mirrors POST/GET/DELETE) and matches the SDK skill-catalog method set.
        """

        try:
            service.declare_deck(request.slots)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return service._build_deck_state()

    @app.delete("/control/deck/declare", response_model=DeckState, tags=["control"])
    def deck_declare_clear(_claim: None = Depends(require_claim)) -> DeckState:
        service.clear_deck()
        return service._build_deck_state()

    @app.post("/control/tips/reset", response_model=TipRackState, tags=["control"])
    def tips_reset(request: TipsResetRequest, _claim: None = Depends(require_claim)) -> TipRackState:
        """(Re)register a tip rack with every tip fresh — a physical rack swap.

        Racks named in ``/control/setup`` labware register automatically (keeping
        used-tip statuses across restarts); this endpoint is for swapping in a
        fresh rack or tracking one loaded out-of-band. Metadata only — no robot
        motion — so, like ``plate.*``, it works in any state including dry-run.
        """

        try:
            return service.reset_tip_rack(request.nickname, wells=request.wells)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/control/lights", response_model=CommandResponse, tags=["control"])
    def lights(request: LightsRequest, _claim: None = Depends(require_claim)) -> CommandResponse:
        try:
            on = service.set_lights(request.on)
        except RuntimeError as exc:
            # No session yet — same shape as the other "not initialized" refusals.
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception as exc:
            # Upstream robot HTTP API failed/unreachable.
            raise HTTPException(status_code=502, detail=f"robot lights request failed: {exc}")
        return CommandResponse(
            message=f"Deck lights {'on' if on else 'off'}", state=service.state.value
        )

    @app.post("/control/reconcile", response_model=CommandResponse, tags=["control"])
    def reconcile(snapshot: Optional[dict[str, Any]] = None, _claim: None = Depends(require_claim)) -> CommandResponse:
        service.reconcile(snapshot)
        return CommandResponse(message="State reconciled", state=service.state.value)

    def _run_non_idempotent(func: Any, success_message: str) -> CommandResponse:
        try:
            func()
            return CommandResponse(message=success_message, state=service.state.value)
        except TipUnavailable as exc:
            # Precondition refusal (STATUS_SPEC §6.1): structured body, top-level
            # fields — never wrapped in {"detail": ...} — and no last_error.
            raise ClaimHTTPError(status_code=412, payload=exc.body)
        except UnknownOutcomeError as exc:
            raise HTTPException(status_code=409, detail=f"unknown outcome: {exc}")
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    # Optional gateway-served operator UI (prebuilt SPA under ui_dist/,
    # committed by `npm run build` in ui/). Off when OT2_UI is falsy or the
    # assets were never built — the gateway is then byte-for-byte headless.
    # With OT2_TRUST_LOCAL_UI=false the mount exists but the middleware above
    # 404s any request that did not come through the auth edge.
    if ui and (UI_DIST_DIR / "index.html").is_file():
        app.mount("/ui", SPAStaticFiles(directory=UI_DIST_DIR, html=True), name="ui")

    app.state.service = service

    # Guarded self-heal on process start: probe the robot over HTTP and, only
    # when it's reachable AND idle, re-establish the REPL session in the
    # background so a restart returns to `ready` without blocking liveness or
    # seizing hardware from an active run. Skipped in dry-run.
    if auto_reconnect and not service.dry_run:
        threading.Thread(
            target=service.boot_reconnect, name="ot2-boot-reconnect", daemon=True
        ).start()
        # Keep the external-run deck / busy flag fresh between boots without the
        # /status handler ever issuing HTTP (Phase 1 left this stale after boot).
        threading.Thread(
            target=service.run_background_refresh, name="ot2-run-refresh", daemon=True
        ).start()

    return app


app = create_app()
