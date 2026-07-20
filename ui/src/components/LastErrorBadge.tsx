import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * Standardized `last_error` surface: a small rose message icon next to the
 * status pill whenever the device reports a `last_error`. Click to pop the
 * detail box; click away / Escape / scroll hides it. Portal-rendered so
 * overflow never clips it. Ported from the dashboard.
 */

export interface LastErrorParts {
  code: string | null;
  recovery: string;
  raw: string;
}

type ErrorLike =
  | { code?: string | null; message?: string | null; severity?: string | null }
  | null
  | undefined;

export type LastErrorInterpret = (error: ErrorLike) => LastErrorParts | null;

function defaultInterpret(error: ErrorLike): LastErrorParts | null {
  const raw = (error?.message ?? "").trim();
  if (!raw) return null;
  return { code: error?.code ?? null, recovery: "", raw };
}

export function LastErrorBadge({
  error,
  interpret,
}: {
  error: ErrorLike;
  interpret?: LastErrorInterpret;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  const parts = error ? (interpret ?? defaultInterpret)(error) : null;

  useEffect(() => {
    if (!open) return;
    const place = () => {
      const r = btnRef.current?.getBoundingClientRect();
      if (r) setPos({ top: r.bottom + 4, right: window.innerWidth - r.right });
    };
    place();
    const close = () => setOpen(false);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!parts) return null;

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="Device fault details"
        title={open ? "Hide fault" : (parts.code ?? "Device fault")}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-rose-300 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-300"
      >
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      </button>
      {open &&
        pos &&
        createPortal(
          <>
            <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden />
            <div
              role="status"
              style={{ position: "fixed", top: pos.top, right: pos.right }}
              className="z-50 max-h-64 w-64 overflow-y-auto rounded-md border border-rose-300 bg-rose-50 px-2.5 py-2 text-left text-[11px] leading-snug text-rose-900 shadow-lg dark:border-rose-700 dark:bg-rose-950 dark:text-rose-100"
            >
              {parts.code && <div className="mb-1 font-mono font-semibold">{parts.code}</div>}
              {parts.recovery ? (
                <>
                  {parts.recovery} <span className="opacity-75">{parts.raw}</span>
                </>
              ) : (
                parts.raw
              )}
            </div>
          </>,
          document.body,
        )}
    </>
  );
}
