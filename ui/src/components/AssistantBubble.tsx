import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, assistantChat, getAssistantHealth } from "../lib/api";
import type { ClaimState } from "../lib/use-claim";
import type { AssistantMessage } from "../lib/types";

/**
 * Optional chat popup for simple operations on THIS OT-2.
 *
 * Renders nothing at all unless the gateway reports an assistant is
 * configured, so a deployment without an API key looks exactly as it did
 * before — the point being that installing this package alone still gives you
 * a complete operator surface, chat included, with no dashboard or agent
 * harness required.
 *
 * The assistant can only propose. Anything it drafts shows up in the Proposed
 * plans panel as a draft for the operator to authorize and run, so this box
 * has no path to the hardware that the panel does not already gate.
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
  const [thread, setThread] = useState<AssistantMessage[]>(loadThread);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

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
      setThread((t) => [
        ...t,
        {
          role: "assistant" as const,
          content:
            res.reply ||
            (res.plan_id ? "Proposed a plan — review it in Proposed plans above." : "…"),
          planId: res.plan_id ?? undefined,
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
    <section className="fixed bottom-4 right-4 z-40 flex h-[28rem] w-[22rem] max-w-[calc(100vw-2rem)] flex-col rounded-xl border border-slate-200 bg-surface-raised shadow-xl dark:border-slate-700 dark:bg-slate-900">
      <header className="flex items-center justify-between border-b border-slate-100 px-3 py-2 dark:border-slate-800">
        <div className="flex flex-col">
          <span className="text-xs font-semibold text-ink dark:text-slate-100">Assistant</span>
          <span className="text-[10px] text-ink-subtle dark:text-slate-500">
            Proposes only — you authorize and run
          </span>
        </div>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded px-1.5 text-sm text-ink-subtle hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
          aria-label="Close the assistant"
        >
          ×
        </button>
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
                <span className="mt-1 block text-[10px] font-medium text-emerald-700 dark:text-emerald-400">
                  Draft created — approve it in Proposed plans.
                </span>
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
