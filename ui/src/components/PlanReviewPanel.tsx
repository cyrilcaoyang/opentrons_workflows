import { useCallback, useEffect, useState } from "react";

import { ApiError, abortPlan, approvePlan, executePlan, listPlans } from "../lib/api";
import type { ClaimState } from "../lib/use-claim";
import type { Plan, StepOutcome } from "../lib/types";

/**
 * Review and run agent-proposed plans.
 *
 * This panel is the human half of the gate in `gateway/plans.py`. An agent
 * (Hermes) can draft a plan and revise it; it can do nothing else. Approving
 * and running both require the claim token, which lives in this browser, so
 * every path that moves the robot passes through these buttons.
 *
 * Two things here are load-bearing rather than cosmetic:
 *
 *  - **Approve sends back the hash that was rendered.** If the plan changed
 *    between the operator reading it and clicking, the gateway returns 409 and
 *    the approval is refused. That is what makes this a review and not a
 *    rubber stamp, so the button must never re-fetch and approve the *current*
 *    hash — it approves the one on screen.
 *  - **Approve and Run stay two clicks.** Merging them would lose the record
 *    of what was reviewed, and would make the last thing before a pipette
 *    moves a single click on a screen the operator may not have read.
 */

const STEP_TONE: Record<StepOutcome, string> = {
  pending: "text-ink-muted dark:text-slate-400",
  ok: "text-emerald-700 dark:text-emerald-400",
  failed: "text-rose-700 dark:text-rose-400",
  skipped: "text-amber-700 dark:text-amber-500",
};

const STATUS_TONE: Record<string, string> = {
  draft: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  approved: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  executing: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  executed: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  failed: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  aborted: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-300",
};

function summarizeArgs(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (entries.length === 0) return "";
  return entries
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join("  ");
}

export function PlanReviewPanel({ claim }: { claim: ClaimState }) {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setPlans(await listPlans());
    } catch {
      // A failed poll is not worth a banner — the next tick usually recovers,
      // and the status panel already reports an unreachable gateway.
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  const run = useCallback(
    async (planId: string, fn: () => Promise<Plan>) => {
      setBusyId(planId);
      setError(null);
      try {
        const updated = await fn();
        setPlans((prev) => prev.map((p) => (p.plan_id === planId ? updated : p)));
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          // The most important error to explain rather than just show: the plan
          // moved under the operator, so what they read is not what would run.
          setError(`${err.message} — the plan changed; re-read it before approving.`);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
        void refresh();
      } finally {
        setBusyId(null);
      }
    },
    [refresh],
  );

  const open = plans.filter((p) => p.status !== "executed" && p.status !== "aborted");
  if (open.length === 0) return null;

  return (
    <section className="rounded-lg border border-ink-line bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
      <header className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-ink dark:text-slate-100">
          Proposed plans
        </h2>
        <p className="text-xs text-ink-muted dark:text-slate-400">
          Proposed by an agent. Nothing runs until you approve it here.
        </p>
      </header>

      {error && (
        <p
          role="alert"
          className="mb-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200"
        >
          {error}
        </p>
      )}

      {!claim.held && (
        <p className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200">
          Take control of the device to review and run a plan.
        </p>
      )}

      <ul className="flex flex-col gap-3">
        {open.map((plan) => {
          const busy = busyId === plan.plan_id;
          const risky = plan.non_idempotent_actions.length > 0;
          return (
            <li
              key={plan.plan_id}
              // Stable id so the chat bubble's "Review & approve ↑" can scroll
              // straight to the plan it just proposed; the target style briefly
              // rings it (see styles.css :target rule) so a full panel doesn't
              // leave the operator hunting.
              id={`plan-${plan.plan_id}`}
              className="scroll-mt-4 rounded-md border border-ink-line p-3 target:ring-2 target:ring-sky-400 dark:border-slate-700"
            >
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span
                  className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                    STATUS_TONE[plan.status] ?? STATUS_TONE.draft
                  }`}
                >
                  {plan.status}
                </span>
                <span className="text-xs text-ink-muted dark:text-slate-400">
                  from <span className="font-mono">{plan.created_by}</span>
                </span>
                <span
                  className="ml-auto font-mono text-[11px] text-ink-muted dark:text-slate-500"
                  title="Digest of the exact step list you are approving"
                >
                  {plan.step_hash.slice(0, 12)}
                </span>
              </div>

              <ol className="mb-2 flex flex-col gap-1">
                {plan.steps.map((step, i) => {
                  const result = plan.results[i];
                  return (
                    <li key={i} className="flex gap-2 font-mono text-xs">
                      <span className="w-4 shrink-0 text-ink-muted dark:text-slate-500">
                        {i + 1}
                      </span>
                      <span className={STEP_TONE[result?.outcome ?? "pending"]}>
                        {step.action}
                        {plan.non_idempotent_actions.includes(step.action) && (
                          <span
                            title="Not safely repeatable — a transport loss mid-step leaves the outcome unknown"
                            className="ml-1 text-amber-600 dark:text-amber-500"
                          >
                            ⚠
                          </span>
                        )}
                        <span className="ml-2 font-normal text-ink-muted dark:text-slate-400">
                          {summarizeArgs(step.args)}
                        </span>
                        {result?.message && (
                          <span className="ml-2 font-normal text-rose-700 dark:text-rose-400">
                            {result.message}
                          </span>
                        )}
                      </span>
                    </li>
                  );
                })}
              </ol>

              {risky && plan.status === "draft" && (
                <p className="mb-2 text-[11px] text-amber-700 dark:text-amber-500">
                  ⚠ Contains {plan.non_idempotent_actions.join(", ")} — if the link
                  drops mid-step, whether it happened cannot be determined.
                </p>
              )}

              {plan.halt_reason && (
                <p className="mb-2 text-xs text-rose-700 dark:text-rose-400">
                  Halted: {plan.halt_reason}
                </p>
              )}

              {!plan.executable && plan.blocked_reason && plan.status !== "draft" && (
                <p className="mb-2 text-xs text-ink-muted dark:text-slate-400">
                  {plan.blocked_reason}
                </p>
              )}

              <div className="flex flex-wrap gap-2">
                {plan.status === "draft" && (
                  <button
                    type="button"
                    disabled={!claim.held || busy}
                    // Approves the hash rendered above, not whatever the server
                    // holds now. A mismatch is a 409 the operator must re-read.
                    onClick={() =>
                      run(plan.plan_id, () =>
                        approvePlan(plan.plan_id, plan.step_hash, claim.token),
                      )
                    }
                    className="rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
                  >
                    Approve these {plan.steps.length} steps
                  </button>
                )}

                {plan.status === "approved" && (
                  <button
                    type="button"
                    disabled={!claim.held || busy || !plan.executable}
                    onClick={() =>
                      run(plan.plan_id, () => executePlan(plan.plan_id, claim.token))
                    }
                    className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
                  >
                    {busy ? "Running…" : "Run"}
                  </button>
                )}

                <button
                  type="button"
                  disabled={!claim.held || busy}
                  onClick={() => run(plan.plan_id, () => abortPlan(plan.plan_id, claim.token))}
                  className="rounded-md border border-ink-line px-3 py-1.5 text-xs font-medium text-ink dark:border-slate-600 dark:text-slate-200"
                >
                  Discard
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
