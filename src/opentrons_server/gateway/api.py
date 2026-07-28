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
from .labware import standard_summaries
from .models import (
    ClaimRejection,
    ClaimRequest,
    ClaimResponse,
    CommandResponse,
    DeckDeclareRequest,
    DeckState,
    EquipmentStatus,
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


def create_app(
    *,
    dry_run: Optional[bool] = None,
    enforce_claims: bool = True,
    auto_reconnect: Optional[bool] = None,
    ui: Optional[bool] = None,
    trust_local_ui: Optional[bool] = None,
    edge_secret: Optional[str] = None,
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
    if ui and trust_local_ui:
        logger.warning(
            "OT2_TRUST_LOCAL_UI=true: operator UI is served without the auth "
            "edge (dev bypass — set OT2_TRUST_LOCAL_UI=false in production)"
        )
    # Compact summary surfaced at /status details.ui_mode.
    ui_mode = "off" if not ui else ("open" if trust_local_ui else "edge")

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
        version="1.1.0",
        description="AC-compatible REST gateway for an Opentrons OT-2 liquid handler.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _from_edge(request: Request) -> bool:
        """True iff the request provably came through the auth edge: it
        carries X-Edge-Key matching the configured shared secret. Never true
        when no secret is configured, so identity headers on direct requests
        are always ignored."""
        if not edge_secret:
            return False
        supplied = request.headers.get("X-Edge-Key")
        return supplied is not None and hmac.compare_digest(supplied, edge_secret)

    if ui and not trust_local_ui:

        @app.middleware("http")
        async def _edge_gate(request: Request, call_next: Any) -> Any:
            path = request.url.path
            gated = path == "/ui" or path.startswith("/ui/") or path == "/labware"
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

    def require_claim(x_claim_token: Optional[str] = Header(default=None, alias="X-Claim-Token")) -> None:
        if not enforce_claims:
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
        return snapshot

    @app.get("/labware", tags=["ui"])
    def labware() -> dict[str, Any]:
        """Read-only catalog of official Opentrons labware definitions
        (grid summaries), for the UI's deck-declare picker. Empty when
        ``opentrons-shared-data`` is not installed."""
        return {"definitions": list(standard_summaries())}

    @app.post(
        "/control/claim",
        response_model=ClaimResponse,
        responses={409: {"model": ClaimRejection}},
        tags=["claim"],
    )
    def claim(request: ClaimRequest, http_request: Request) -> ClaimResponse:
        # Edge-asserted identity: when the request provably came through the
        # auth edge, the logged-in user overrides the body's owner so
        # details.claimed_by names a person, not a UI constant.
        if _from_edge(http_request):
            edge_user = http_request.headers.get("X-Auth-User")
            if edge_user:
                request = request.model_copy(update={"owner": edge_user})
        try:
            return service.claims.acquire(request)
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
