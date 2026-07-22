import { useCallback, useEffect, useRef, useState } from "react";

import { getStatus } from "./api";
import type { GatewaySnapshot } from "./types";

/**
 * Poll the gateway's own /status every `intervalMs` and wrap the envelope in
 * the snapshot shape the ported dashboard components expect. This replaces
 * the dashboard's aggregator + TanStack Query pair: `fetch_error`,
 * `latency_ms`, and `fetched_at` are synthesized from the browser's own
 * fetch instead of the aggregator's poll bookkeeping.
 *
 * On a transport failure the previous status is kept (so the deck stays
 * rendered) and `fetch_error` is set — the same behavior an operator sees on
 * the dashboard when a device stops answering.
 */
export function useGatewayStatus(intervalMs = 3000): {
  snapshot: GatewaySnapshot | null;
  isPending: boolean;
  refetch: () => void;
} {
  const [snapshot, setSnapshot] = useState<GatewaySnapshot | null>(null);
  const [isPending, setIsPending] = useState(true);
  const [tick, setTick] = useState(0);
  const inFlight = useRef(false);

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      if (inFlight.current) return;
      inFlight.current = true;
      const started = performance.now();
      try {
        const status = await getStatus();
        if (cancelled) return;
        setSnapshot({
          id: status.equipment_id,
          name: status.equipment_name,
          kind: status.equipment_kind,
          status,
          fetch_error: null,
          latency_ms: Math.round(performance.now() - started),
          fetched_at: new Date().toISOString(),
        });
      } catch (e: unknown) {
        if (cancelled) return;
        const kind = e instanceof Error ? e.message : "fetch_failed";
        setSnapshot((prev) =>
          prev
            ? { ...prev, fetch_error: { kind }, latency_ms: null }
            : null,
        );
      } finally {
        inFlight.current = false;
        if (!cancelled) setIsPending(false);
      }
    }

    void poll();
    const timer = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [intervalMs, tick]);

  return { snapshot, isPending, refetch };
}
