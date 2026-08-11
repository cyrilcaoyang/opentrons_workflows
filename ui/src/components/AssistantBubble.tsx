import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  abortPlan,
  approvePlan,
  assistantChat,
  deletePlan,
  executePlan,
  getAssistantHealth,
  getPlan,
} from "../lib/api";
import type { ClaimState } from "../lib/use-claim";
import type { AssistantMessage, Plan, PlanStep } from "../lib/types";

/**
 * Optional chat popup for simple operations on THIS OT-2.
 *
 * Renders nothing at all unless the gateway reports an assistant is
 * configured, so a deployment without an API key looks exactly as it did
 * before — the point being that installing this package alone still gives you
 * a complete operator surface, chat included, with no dashboard or agent
 * harness required.
 *
 * The assistant can only propose. What it drafts renders here as a plan card
 * the operator can approve and run **in the chat** — the same claim-gated
 * calls the Proposed-plans panel makes, with the same two review properties
 * preserved:
 *
 *  - **Approve sends the hash of the steps this card is showing.** The card
 *    renders from the live plan (re-fetched, not the proposal-time preview),
 *    so what is approved is what is on screen; a plan revised elsewhere gets
 *    a 409 and a re-read, exactly as in the panel.
 *  - **Approve and Run stay two clicks**, for the same reason as the panel.
 *
 * The panel remains the overview surface (plans from other agents, history
 * of settled plans); this card is the fast path for what THIS chat proposed.
 */

const STORAGE_KEY = "ot2-assistant-thread";
const MAX_KEPT = 20;

function loadThread(): AssistantMessage[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as AssistantMessage[]) : [];
  } catch {
    return [];
  }
}

export function AssistantBubble({ claim }: { claim: ClaimState }) {
  const [available, setAvailable] = useState(false);
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [thread, setThread] = useState<AssistantMessage[]>(loadThread);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Live plan state per plan id — what the cards render and approve from.
  // "gone" = deleted/dismissed (or died with a gateway restart). Not
  // persisted: statuses are re-fetched when the bubble opens, so a stale
  // sessionStorage copy can never be what gets approved.
  const [planStates, setPlanStates] = useState<Record<string, Plan | "gone">>({});
  const [planBusy, setPlanBusy] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  const refreshPlan = useCallback(async (planId: string) => {
    try {
      const plan = await getPlan(planId);
      setPlanStates((s) => ({ ...s, [planId]: plan }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setPlanStates((s) => ({ ...s, [planId]: "gone" }));
      }
      /* other failures: keep whatever we had; the card degrades read-only */
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    // Re-sync every card when the bubble opens: approvals expire, plans get
    // revised or dismissed from the panel, and the gateway may have restarted.
    const ids = new Set(thread.map((m) => m.planId).filter(Boolean) as string[]);
    ids.forEach((id) => void refreshPlan(id));
    // Deliberately not keyed on `thread`: send() stores the fresh plan itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, refreshPlan]);

  const runPlanAction = useCallback(
    async (planId: string, fn: () => Promise<Plan>) => {
      setPlanBusy(planId);
      setError(null);
      try {
        const updated = await fn();
        setPlanStates((s) => ({ ...s, [planId]: updated }));
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          setError(`${err.message} — the plan changed; re-read it before approving.`);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
        void refreshPlan(planId);
      } finally {
        setPlanBusy(null);
      }
    },
    [refreshPlan],
  );

  const dismissPlan = useCallback(
    async (planId: string) => {
      setPlanBusy(planId);
      setError(null);
      try {
        await deletePlan(planId, claim.token);
        setPlanStates((s) => ({ ...s, [planId]: "gone" }));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        void refreshPlan(planId);
      } finally {
        setPlanBusy(null);
      }
    },
    [claim.token, refreshPlan],
  );

  useEffect(() => {
    // One probe on mount. If the gateway has no assistant this component then
    // costs nothing for the rest of the session.
    void getAssistantHealth()
      .then((h) => setAvailable(h.configured))
      .catch(() => setAvailable(false));
  }, []);

  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(thread.slice(-MAX_KEPT)));
    } catch {
      /* private mode / quota — the thread just won't survive a reload */
    }
    endRef.current?.scrollIntoView({ block: "end" });
  }, [thread]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || pending) return;
    const next = [...thread, { role: "user" as const, content: text }];
    setThread(next);
    setDraft("");
    setPending(true);
    setError(null);
    try {
      const res = await assistantChat(next.slice(-MAX_KEPT), claim.token);
      // Fetch the steps of any plan it drafted so the operator sees what was
      // proposed *in the chat*, not just "go look elsewhere". Best-effort: the
      // panel is still the source of truth, so a failed fetch just omits the
      // inline preview rather than failing the turn.
      let steps: PlanStep[] | undefined;
      if (res.plan_id) {
        try {
          const plan = await getPlan(res.plan_id);
          steps = plan.steps;
          // Seed the live card immediately — approvable without a reopen.
          setPlanStates((s) => ({ ...s, [plan.plan_id]: plan }));
        } catch {
          /* preview is a nicety; the card degrades read-only without it */
        }
      }
      setThread((t) => [
        ...t,
        {
          role: "assistant" as const,
          content: res.reply || (res.plan_id ? "Proposed a plan for your review." : "…"),
          planId: res.plan_id ?? undefined,
          steps,
        },
      ]);
    } catch (err) {
      // The turn failed, so no assistant message is appended — showing an
      // empty bubble would read as the assistant having said nothing rather
      // than as the request never landing.
      setError(
        err instanceof ApiError && err.status === 423
          ? "Take control of the device to use the assistant."
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setPending(false);
    }
  }, [draft, pending, thread, claim.token]);

  if (!available) return null;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Ask about this OT-2"
        className="fixed bottom-4 right-4 z-40 flex h-11 w-11 items-center justify-center rounded-full bg-sky-600 text-lg text-white shadow-lg hover:bg-sky-700"
      >
        <span aria-hidden>💬</span>
        <span className="sr-only">Open the assistant</span>
      </button>
    );
  }

  return (
    <section
      className={[
        "fixed bottom-4 right-4 z-40 flex max-w-[calc(100vw-2rem)] flex-col rounded-xl border border-slate-200 bg-surface-raised shadow-xl dark:border-slate-700 dark:bg-slate-900",
        // Two sizes rather than a drag-resize: anchored bottom-right, a CSS
        // resize handle would grow the panel off-screen. Default is wide
        // enough for a plan preview; expanded is for reading longer replies.
        expanded
          ? "h-[min(46rem,calc(100vh-2rem))] w-[44rem]"
          : "h-[32rem] w-[32rem] max-h-[calc(100vh-2rem)]",
      ].join(" ")}
    >
      <header className="flex items-center justify-between border-b border-slate-100 px-3 py-2 dark:border-slate-800">
        <div className="flex flex-col">
          <span className="text-xs font-semibold text-ink dark:text-slate-100">Assistant</span>
          <span className="text-[10px] text-ink-subtle dark:text-slate-500">
            Proposes only — you approve and run
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="rounded px-1.5 text-sm text-ink-subtle hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
            aria-label={expanded ? "Shrink the assistant window" : "Enlarge the assistant window"}
            title={expanded ? "Shrink" : "Enlarge"}
          >
            {expanded ? "⤡" : "⤢"}
          </button>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="rounded px-1.5 text-sm text-ink-subtle hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
            aria-label="Close the assistant"
          >
            ×
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-3 py-2">
        {thread.length === 0 && (
          <p className="text-xs text-ink-subtle dark:text-slate-500">
            Ask about this robot&apos;s state, or describe a simple operation and I&apos;ll
            propose it for your approval.
          </p>
        )}
        <ul className="flex flex-col gap-2">
          {thread.map((m, i) => (
            <li
              key={i}
              className={
                m.role === "user"
                  ? "self-end rounded-lg bg-sky-600 px-2.5 py-1.5 text-xs text-white"
                  : "self-start rounded-lg bg-slate-100 px-2.5 py-1.5 text-xs text-ink dark:bg-slate-800 dark:text-slate-200"
              }
            >
              <span className="whitespace-pre-wrap">{m.content}</span>
              {m.planId && (
                <ChatPlanCard
                  planId={m.planId}
                  live={planStates[m.planId]}
                  previewSteps={m.steps}
                  busy={planBusy === m.planId}
                  claimHeld={claim.held}
                  onApprove={(hash) =>
                    void runPlanAction(m.planId!, () =>
                      approvePlan(m.planId!, hash, claim.token),
                    )
                  }
                  onRun={() =>
                    void runPlanAction(m.planId!, () => executePlan(m.planId!, claim.token))
                  }
                  onDiscard={() =>
                    void runPlanAction(m.planId!, () => abortPlan(m.planId!, claim.token))
                  }
                  onDismiss={() => void dismissPlan(m.planId!)}
                />
              )}
            </li>
          ))}
        </ul>
        {pending && (
          <p className="mt-2 text-[11px] text-ink-subtle dark:text-slate-500">Thinking…</p>
        )}
        {error && (
          <p role="alert" className="mt-2 text-[11px] text-rose-700 dark:text-rose-400">
            {error}
          </p>
        )}
        <div ref={endRef} />
      </div>

      <form
        className="flex gap-1.5 border-t border-slate-100 p-2 dark:border-slate-800"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={pending}
          placeholder={claim.held ? "Ask or describe a step…" : "Take control first…"}
          className="min-w-0 flex-1 rounded-md border border-slate-200 px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
        />
        <button
          type="submit"
          disabled={pending || !draft.trim()}
          className="rounded-md bg-sky-600 px-2.5 py-1 text-xs font-medium text-white disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </section>
  );
}

const CARD_STATUS_TONE: Record<string, string> = {
  draft: "bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200",
  approved: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  executing: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  executed: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  failed: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  aborted: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-300",
};

const CARD_STEP_TONE: Record<string, string> = {
  pending: "text-ink dark:text-slate-200",
  ok: "text-emerald-700 dark:text-emerald-400",
  failed: "text-rose-700 dark:text-rose-400",
  skipped: "text-amber-700 dark:text-amber-500",
};

function stepLine(s: PlanStep): string {
  const args = Object.entries(s.args)
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" ");
  return args ? `${s.action}  ${args}` : s.action;
}

/**
 * The in-chat plan card: review, approve, and run without leaving the chat.
 *
 * Renders from the LIVE plan (`live`), never from the proposal-time preview,
 * so the hash sent by Approve is the hash of exactly what is displayed — the
 * same review property the panel enforces. Without live state (fetch failed,
 * gateway restarted) the card degrades to the read-only preview.
 */
function ChatPlanCard({
  planId,
  live,
  previewSteps,
  busy,
  claimHeld,
  onApprove,
  onRun,
  onDiscard,
  onDismiss,
}: {
  planId: string;
  live: Plan | "gone" | undefined;
  previewSteps?: PlanStep[];
  busy: boolean;
  claimHeld: boolean;
  onApprove: (stepHash: string) => void;
  onRun: () => void;
  onDiscard: () => void;
  onDismiss: () => void;
}) {
  if (live === "gone") {
    return (
      <p className="mt-1.5 text-[10px] italic text-ink-subtle dark:text-slate-500">
        Plan dismissed.
      </p>
    );
  }

  const jump = (
    <button
      type="button"
      onClick={() => {
        const el = document.getElementById(`plan-${planId}`);
        if (el) {
          // Re-trigger :target even if we're already there, so the ring
          // flashes on a repeat click.
          window.location.hash = "";
          window.location.hash = `plan-${planId}`;
          el.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }}
      className="text-[10px] text-ink-subtle underline decoration-dotted hover:text-ink dark:text-slate-500 dark:hover:text-slate-300"
    >
      view in panel
    </button>
  );

  if (live === undefined) {
    // No live state: show what was proposed, but never an Approve button —
    // approving requires the hash of a plan we can actually see fresh.
    return (
      <div className="mt-1.5 rounded border border-slate-200 bg-surface-raised p-1.5 dark:border-slate-700 dark:bg-slate-900">
        {previewSteps && previewSteps.length > 0 && (
          <ol className="mb-1 flex flex-col gap-0.5">
            {previewSteps.map((s, si) => (
              <li key={si} className="font-mono text-[11px] text-ink dark:text-slate-200">
                {si + 1}. {stepLine(s)}
              </li>
            ))}
          </ol>
        )}
        {jump}
      </div>
    );
  }

  const actionButton = "rounded px-2 py-0.5 text-[11px] font-medium text-white disabled:opacity-40";
  const quietButton =
    "rounded border border-slate-300 px-2 py-0.5 text-[11px] font-medium text-ink dark:border-slate-600 dark:text-slate-200 disabled:opacity-40";

  return (
    <div className="mt-1.5 rounded border border-slate-200 bg-surface-raised p-1.5 dark:border-slate-700 dark:bg-slate-900">
      <div className="mb-1 flex items-center gap-1.5">
        <span
          className={`rounded px-1 py-px text-[10px] font-medium ${
            CARD_STATUS_TONE[live.status] ?? CARD_STATUS_TONE.draft
          }`}
        >
          {live.status}
        </span>
        <span className="ml-auto">{jump}</span>
      </div>
      <ol className="mb-1 flex flex-col gap-0.5">
        {live.steps.map((s, si) => {
          const outcome = live.results[si]?.outcome ?? "pending";
          return (
            <li
              key={si}
              className={`font-mono text-[11px] ${CARD_STEP_TONE[outcome] ?? CARD_STEP_TONE.pending}`}
            >
              {si + 1}. {stepLine(s)}
              {live.results[si]?.message && (
                <span className="ml-1 font-sans text-rose-700 dark:text-rose-400">
                  {live.results[si].message}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      {live.status === "draft" && live.non_idempotent_actions.length > 0 && (
        <p className="mb-1 text-[10px] text-amber-700 dark:text-amber-500">
          ⚠ {live.non_idempotent_actions.join(", ")} cannot be safely repeated if the
          link drops mid-step.
        </p>
      )}
      {live.halt_reason && (
        <p className="mb-1 text-[10px] text-rose-700 dark:text-rose-400">
          Halted: {live.halt_reason}
        </p>
      )}
      {live.status === "approved" && !live.executable && live.blocked_reason && (
        <p className="mb-1 text-[10px] text-ink-subtle dark:text-slate-500">
          {live.blocked_reason}
        </p>
      )}
      {!claimHeld && live.status === "draft" && (
        <p className="mb-1 text-[10px] text-amber-700 dark:text-amber-500">
          Take control of the device to approve.
        </p>
      )}
      <div className="flex flex-wrap gap-1.5">
        {live.status === "draft" && (
          <button
            type="button"
            disabled={!claimHeld || busy}
            // Approves the hash of the steps rendered above — never a
            // re-fetch-and-approve, so a plan that moved gets a 409.
            onClick={() => onApprove(live.step_hash)}
            className={`${actionButton} bg-sky-600`}
          >
            Approve these {live.steps.length} steps
          </button>
        )}
        {live.status === "approved" && (
          <button
            type="button"
            disabled={!claimHeld || busy || !live.executable}
            onClick={onRun}
            className={`${actionButton} bg-emerald-600`}
          >
            {busy ? "Running…" : "Run"}
          </button>
        )}
        {(live.status === "draft" || live.status === "approved") && (
          <button type="button" disabled={!claimHeld || busy} onClick={onDiscard} className={quietButton}>
            Discard
          </button>
        )}
        {(live.status === "failed" || live.status === "executed" || live.status === "aborted") && (
          <button type="button" disabled={!claimHeld || busy} onClick={onDismiss} className={quietButton}>
            Dismiss
          </button>
        )}
      </div>
    </div>
  );
}
