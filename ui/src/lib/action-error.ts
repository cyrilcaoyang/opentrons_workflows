import { ApiError } from "./api";

/**
 * Shared model for the inline "action-error" surface: a device refusal — a
 * 412 precondition, a 423 claim conflict, a 409 device-state conflict —
 * rendered back to the operator instead of being swallowed. Ported from the
 * ac-organic-lab dashboard (`web/src/lib/action-error.ts`).
 */
export interface ActionError {
  status: number;
  message: string;
  kind: "precondition" | "claim" | "state" | "other";
}

export type Parse412 = (
  body: Record<string, unknown>,
  ctx: { action?: string; retryAfterS: number | null },
) => string | null;

export function interpretActionError(
  e: unknown,
  opts: { action?: string; parse412?: Parse412 } = {},
): ActionError {
  if (!(e instanceof ApiError)) {
    const message = e instanceof Error ? e.message : String(e);
    return { status: 0, message, kind: "other" };
  }
  const body = (e.body ?? {}) as Record<string, unknown>;
  const detail = typeof body.detail === "string" ? body.detail : undefined;

  if (e.status === 412) {
    const custom = opts.parse412?.(body, {
      action: opts.action,
      retryAfterS: e.retryAfterS,
    });
    return {
      status: 412,
      message: custom ?? detail ?? "Device precondition not met.",
      kind: "precondition",
    };
  }

  if (e.status === 423) {
    const claimedBy = body.claimed_by as { owner?: string } | undefined;
    const owner = claimedBy?.owner;
    return {
      status: 423,
      message: owner
        ? `Device claim is held by ${owner}. Take control first (or wait for the holder to release).`
        : detail ?? "Device is claimed by another caller — take control first.",
      kind: "claim",
    };
  }

  if (e.status === 409) {
    const msg = detail ?? "Action rejected.";
    const hint = /init|startup|connect/i.test(msg) ? " Connect the gateway session first." : "";
    return { status: 409, message: msg + hint, kind: "state" };
  }

  return { status: e.status, message: detail ?? e.message, kind: "other" };
}
