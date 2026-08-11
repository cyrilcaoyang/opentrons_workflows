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
  AssistantHealth,
  AssistantMessage,
  AssistantReply,
  ClaimResponse,
  DeviceDeck,
  EquipmentStatus,
  LabwareSummary,
  Plan,
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
  // Join without ever producing a leading "//": the browser would read that
  // as a scheme-relative URL (e.g. "//status" -> http://status/). apiBase
  // always ends with "/" and path always starts with "/", so trim one.
  return `${apiBase.replace(/\/+$/, "")}${path}`;
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

/** One full Opentrons definition, for the inspector's side elevation. Throws
 *  `ApiError(404)` when the load_name is unknown or `opentrons-shared-data`
 *  isn't installed — callers treat both as "no elevation available". */
export function getLabwareDefinition(loadName: string): Promise<unknown> {
  return fetchJson<unknown>(`/labware/${encodeURIComponent(loadName)}`);
}

// ---------------------------------------------------------------------------
// Claim lifecycle
// ---------------------------------------------------------------------------

/** `takeover` supersedes a claim held by the *same owner* — this operator's
 *  other tab, or the one they reloaded. A different owner (an agent, the
 *  dashboard) still comes back 409. Gateway-local, not STATUS_SPEC §5. */
export function postClaim(
  owner: string,
  sessionId: string,
  ttlS = 60,
  takeover = false,
): Promise<ClaimResponse> {
  return fetchJson<ClaimResponse>("/control/claim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ owner, session_id: sessionId, ttl_s: ttlS, takeover }),
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

/** Acknowledge a failed command (or an unknown outcome) after inspecting the
 *  robot — clears `last_error` and returns the gateway to ready. Sent with NO
 *  body: the endpoint's optional body is a snapshot override, and an empty
 *  object would clobber the cached snapshot rather than mean "no snapshot". */
export const postReconcile = (token: string | null) =>
  controlPost("reconcile", undefined, token);

export const postSetLights = (token: string | null, on: boolean) =>
  controlPost("lights", { on }, token);

/** Mark every tip in the rack on `slot` fresh again — the operator asserting a
 *  physical refill. Never inferred: the gateway cannot observe new tips being
 *  put in, and guessing wrong claims tips that aren't there. Addressed by slot
 *  because that is a tip rack's identity. */
export const postTipsReset = (token: string | null, slot: string) =>
  controlPost("tips/reset", { slot }, token);

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

// ---------------------------------------------------------------------------
// Agent-proposed plans
//
// `listPlans` is a read. The other three are the human gate: all claim-token
// gated server-side, which is exactly why they live in the operator UI and
// have no agent-reachable equivalent.
// ---------------------------------------------------------------------------

export function listPlans(): Promise<Plan[]> {
  return fetchJson<Plan[]>("/plans");
}

/** One plan — used by the chat bubble to show, read-only, the steps it just
 *  proposed, so the operator sees what was drafted without leaving the chat. */
export function getPlan(planId: string): Promise<Plan> {
  return fetchJson<Plan>(`/plans/${encodeURIComponent(planId)}`);
}

/** Approve one exact step list. `stepHash` must be the digest the operator was
 *  shown — the gateway refuses (409) if the plan changed since it rendered,
 *  which is what makes this a review rather than a rubber stamp. */
export function approvePlan(
  planId: string,
  stepHash: string,
  token: string | null,
): Promise<Plan> {
  return fetchJson<Plan>(`/plans/${encodeURIComponent(planId)}/approve`, {
    method: "POST",
    headers: withToken(token),
    body: JSON.stringify({ step_hash: stepHash }),
  });
}

export function executePlan(planId: string, token: string | null): Promise<Plan> {
  return fetchJson<Plan>(`/plans/${encodeURIComponent(planId)}/execute`, {
    method: "POST",
    headers: withToken(token),
  });
}

export function abortPlan(planId: string, token: string | null): Promise<Plan> {
  return fetchJson<Plan>(`/plans/${encodeURIComponent(planId)}/abort`, {
    method: "POST",
    headers: withToken(token),
  });
}

/** Dismiss a settled (failed / executed / aborted) plan — removes it from the
 *  list entirely. Abort cannot clear a failed plan: it is already terminal. */
export function deletePlan(planId: string, token: string | null): Promise<void> {
  return fetchJson<void>(`/plans/${encodeURIComponent(planId)}`, {
    method: "DELETE",
    headers: withToken(token),
  });
}

// ---------------------------------------------------------------------------
// Optional chat assistant
// ---------------------------------------------------------------------------

/** Open (no claim, no identity) so the UI can decide whether to render the
 *  bubble before anyone logs in. Returns only a boolean and a reason. */
export function getAssistantHealth(): Promise<AssistantHealth> {
  return fetchJson<AssistantHealth>("/assistant/health");
}

/** One turn. Claim-gated server-side: a proposal is only useful to whoever
 *  holds the device, and it keeps a passer-by from spending the API budget. */
export function assistantChat(
  messages: AssistantMessage[],
  token: string | null,
): Promise<AssistantReply> {
  return fetchJson<AssistantReply>("/assistant/chat", {
    method: "POST",
    headers: withToken(token),
    // `planId` is a UI-side annotation; the gateway's schema rejects extras.
    body: JSON.stringify({
      messages: messages.map((m) => ({ role: m.role, content: m.content })),
    }),
  });
}
