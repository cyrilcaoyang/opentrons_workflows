/**
 * Typed fetch layer for the gateway's own REST surface. All paths are
 * prefixed with `apiBase` so they resolve correctly both when the SPA is
 * served directly by the gateway at /ui (apiBase = "/") AND when it's served
 * through the Caddy edge at /ot2/{hte,complexation}/ui/ (apiBase =
 * "/ot2/{instance}/"). Same-origin, no CORS concern in either case.
 *
 * `apiBase` is derived from the page URL: strip the trailing "ui/" segment
 * (the SPA's canonical path under both direct and edge access) to recover the
 * server-root prefix. Examples:
 *   http://host/ui/               -> apiBase = "/"
 *   http://host/ot2/hte/ui/        -> apiBase = "/ot2/hte/"
 *   http://host/ot2/complexation/ui/ -> apiBase = "/ot2/complexation/"
 * Dev (vite, root URL)            -> apiBase = "/"
 *
 * Control calls attach the cooperative-claim token (`X-Claim-Token`) when the
 * caller holds one — see `use-claim.ts`. Refusals surface as `ApiError` with
 * the parsed body, so `action-error.ts` can branch on 412/423/409 shapes.
 */

import type {
  ClaimResponse,
  DeviceDeck,
  EquipmentStatus,
  LabwareSummary,
} from "./types";

/** Server-root prefix for API calls, derived from the SPA's own URL. */
const apiBase: string = (() => {
  const { pathname } = window.location;
  // The SPA lives at .../ui/ (canonical, trailing slash) or .../ui (no slash,
  // before a redirect normalizes it). Strip the "ui" segment to get the prefix.
  const idx = pathname.lastIndexOf("/ui");
  if (idx === -1) return "/";
  return pathname.slice(0, idx + 1) || "/";
})();

function apiUrl(path: string): string {
  return `${apiBase}${path}`.replace(/\/{2,}/g, (m, off) => (off === 0 ? m : "/"));
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  readonly path: string;
  readonly retryAfterS: number | null;

  constructor(
    status: number,
    statusText: string,
    body: unknown,
    path: string,
    retryAfterS: number | null,
  ) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : undefined;
    super(detail ?? `${status} ${statusText} from ${path}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.path = path;
    this.retryAfterS = retryAfterS;
  }
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(path), init);
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* non-JSON body */
    }
    const retryAfter = res.headers.get("Retry-After");
    throw new ApiError(
      res.status,
      res.statusText,
      body,
      path,
      retryAfter != null ? Number(retryAfter) : null,
    );
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function withToken(token: string | null): HeadersInit {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["X-Claim-Token"] = token;
  return headers;
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export function getStatus(): Promise<EquipmentStatus> {
  return fetchJson<EquipmentStatus>("/status");
}

export function getLabwareList(): Promise<{ definitions: LabwareSummary[] }> {
  return fetchJson<{ definitions: LabwareSummary[] }>("/labware");
}

// ---------------------------------------------------------------------------
// Claim lifecycle
// ---------------------------------------------------------------------------

export function postClaim(
  owner: string,
  sessionId: string,
  ttlS = 60,
): Promise<ClaimResponse> {
  return fetchJson<ClaimResponse>("/control/claim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ owner, session_id: sessionId, ttl_s: ttlS }),
  });
}

export function postHeartbeat(token: string): Promise<ClaimResponse | undefined> {
  return fetchJson<ClaimResponse | undefined>("/control/heartbeat", {
    method: "POST",
    headers: { "X-Claim-Token": token },
  });
}

export function postRelease(token: string): Promise<void> {
  return fetchJson<void>("/control/release", {
    method: "POST",
    headers: { "X-Claim-Token": token },
  });
}

/** Best-effort release on tab close: fetch with keepalive so the request
 *  survives page teardown (sendBeacon cannot carry the token header). */
export function releaseOnUnload(token: string): void {
  void fetch(apiUrl("/control/release"), {
    method: "POST",
    headers: { "X-Claim-Token": token },
    keepalive: true,
  }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Controls (claim token attached when held)
// ---------------------------------------------------------------------------

function controlPost<TResp>(
  action: string,
  body: unknown,
  token: string | null,
): Promise<TResp> {
  return fetchJson<TResp>(`/control/${action}`, {
    method: "POST",
    headers: withToken(token),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const postStartup = (token: string | null, simulation = false) =>
  controlPost("startup", { simulation }, token);

export const postShutdown = (token: string | null) =>
  controlPost("shutdown", {}, token);

export const postPause = (token: string | null) => controlPost("pause", {}, token);

export const postResume = (token: string | null) => controlPost("resume", {}, token);

export const postHome = (token: string | null) => controlPost("home", {}, token);

export const postSetLights = (token: string | null, on: boolean) =>
  controlPost("lights", { on }, token);

/** Full-layout declared-deck replace. Values are load_names, module keys, or
 *  legacy kind strings; an empty map clears the declaration. */
export const postDeckDeclare = (
  token: string | null,
  slots: Record<string, string | null>,
): Promise<DeviceDeck> => controlPost<DeviceDeck>("deck/declare", { slots }, token);

export function deleteDeckDeclare(token: string | null): Promise<DeviceDeck> {
  return fetchJson<DeviceDeck>("/control/deck/declare", {
    method: "DELETE",
    headers: withToken(token),
  });
}
