import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, postClaim, postHeartbeat, postRelease, releaseOnUnload } from "./api";
import type { ClaimedBy } from "./types";

/**
 * Browser-side cooperative-claim manager (STATUS_SPEC v1.1 §5), the UI's
 * equivalent of the SDK's `ClaimManager`: acquire on demand, heartbeat in the
 * background more often than `heartbeat_interval_s`, release on toggle /
 * unmount / tab close (best-effort keepalive).
 *
 * The claim is what unlocks the controls: buttons are disabled until the
 * operator takes control, mirroring how the dashboard's per-request claim or
 * a workflow's long-lived claim gates every other writer. A heartbeat 401/404
 * means the claim was lost (expiry or gateway restart) — controls re-lock and
 * the reason is surfaced.
 */
export interface ClaimState {
  held: boolean;
  token: string | null;
  sessionId: string | null;
  expiresAt: string | null;
  pending: boolean;
  /** Why the claim was refused or lost (409 conflict, heartbeat loss). */
  error: string | null;
  /** The competing holder on a 409, when the gateway reported one. */
  conflict: ClaimedBy | null;
}

const IDLE: ClaimState = {
  held: false,
  token: null,
  sessionId: null,
  expiresAt: null,
  pending: false,
  error: null,
  conflict: null,
};

export function useClaim(owner: string) {
  const [state, setState] = useState<ClaimState>(IDLE);
  const heartbeatTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const tokenRef = useRef<string | null>(null);

  const stopHeartbeat = useCallback(() => {
    if (heartbeatTimer.current) {
      clearInterval(heartbeatTimer.current);
      heartbeatTimer.current = null;
    }
  }, []);

  const acquire = useCallback(async () => {
    setState((s) => ({ ...s, pending: true, error: null, conflict: null }));
    const sessionId = `ui-${crypto.randomUUID()}`;
    try {
      const resp = await postClaim(owner, sessionId, 60);
      tokenRef.current = resp.claim_token;
      setState({
        held: true,
        token: resp.claim_token,
        sessionId,
        expiresAt: resp.expires_at,
        pending: false,
        error: null,
        conflict: null,
      });
      stopHeartbeat();
      // Spec: heartbeat MORE OFTEN than heartbeat_interval_s.
      const everyMs = Math.max(1000, resp.heartbeat_interval_s * 1000 * 0.5);
      heartbeatTimer.current = setInterval(async () => {
        const token = tokenRef.current;
        if (!token) return;
        try {
          const hb = await postHeartbeat(token);
          if (hb?.expires_at) {
            setState((s) => (s.held ? { ...s, expiresAt: hb.expires_at } : s));
          }
        } catch (e: unknown) {
          // 401/404 = claim lost (expired or gateway restarted).
          if (e instanceof ApiError && (e.status === 401 || e.status === 404)) {
            tokenRef.current = null;
            stopHeartbeat();
            setState({ ...IDLE, error: "Claim lost — the gateway expired or forgot it. Take control again." });
          }
          // Transient network failures: keep trying until the TTL decides.
        }
      }, everyMs);
    } catch (e: unknown) {
      let message = e instanceof Error ? e.message : String(e);
      let conflict: ClaimedBy | null = null;
      if (e instanceof ApiError && (e.status === 409 || e.status === 423)) {
        const body = (e.body ?? {}) as { detail?: string; claimed_by?: ClaimedBy | null };
        conflict = body.claimed_by ?? null;
        message = conflict
          ? `Device is controlled by ${conflict.owner} (until ${conflict.expires_at}).`
          : body.detail ?? message;
      }
      setState({ ...IDLE, error: message, conflict });
    }
  }, [owner, stopHeartbeat]);

  const release = useCallback(async () => {
    const token = tokenRef.current;
    tokenRef.current = null;
    stopHeartbeat();
    setState(IDLE);
    if (token) {
      try {
        await postRelease(token);
      } catch {
        /* release is idempotent and best-effort */
      }
    }
  }, [stopHeartbeat]);

  // Release on unmount and on tab close (keepalive fetch).
  useEffect(() => {
    const onUnload = () => {
      if (tokenRef.current) releaseOnUnload(tokenRef.current);
    };
    window.addEventListener("pagehide", onUnload);
    return () => {
      window.removeEventListener("pagehide", onUnload);
      stopHeartbeat();
      if (tokenRef.current) releaseOnUnload(tokenRef.current);
    };
  }, [stopHeartbeat]);

  return { ...state, acquire, release };
}
