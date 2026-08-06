import { useEffect, useMemo, useState } from "react";

import {
  deleteDeckDeclare,
  getLabwareList,
  postDeckDeclare,
  postHome,
  postPause,
  postResume,
  postSetLights,
  postTipsReset,
  postShutdown,
  postStartup,
} from "../lib/api";
import { useActionErrorState } from "../lib/use-action-error";
import type { useClaim } from "../lib/use-claim";
import {
  buildSlotView,
  claimedByFromStatus,
  declaredMapFromDeck,
  deviceDeckFromStatus,
  mountedTipsFromStatus,
  nextDeclaration,
  pairModuleSlots,
  pipetteLabel,
  robotInfoFromStatus,
  robotModulesFromStatus,
  tipRacksFromStatus,
} from "../lib/ot2-deck";
import { catalogEntryFromLabware, OT2_CATALOG, type CatalogEntry } from "../lib/ot2-catalog";
import type { GatewaySnapshot } from "../lib/types";

import { ActionErrorBadge } from "./ActionErrorBadge";
import { DeckPanel, ModuleReadout } from "./DeckPanel";
import { PlateInspector } from "./PlateInspector";
import { DeclarePicker } from "./DeclarePicker";
import { FetchErrorBand } from "./FetchErrorBand";
import { LastErrorBadge } from "./LastErrorBadge";
import { StalenessIndicator } from "./StalenessIndicator";
import { StatusPill } from "./StatusPill";
import { TileButton } from "./TileButton";

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-subtle dark:text-slate-400">
        {title}
      </h3>
      {children}
    </section>
  );
}

function KV({ k, v, mono }: { k: string; v: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="text-ink-subtle dark:text-slate-500">{k}</span>
      <span
        className={[
          "min-w-0 truncate text-right text-ink dark:text-slate-200",
          mono ? "font-mono" : "",
        ].join(" ")}
      >
        {v}
      </span>
    </div>
  );
}

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full ${
        ok ? "bg-emerald-400" : "bg-slate-400 dark:bg-slate-500"
      }`}
      aria-hidden
    />
  );
}

// ---------------------------------------------------------------------------
// The full-page OT-2 interface (ported from the dashboard's Ot2ControlPanel;
// the CONTROL_PASSWORD lock is replaced by the cooperative-claim gate).
// ---------------------------------------------------------------------------

export function ControlPanel({
  snapshot,
  refetch,
  claim,
}: {
  snapshot: GatewaySnapshot;
  refetch: () => void;
  claim: ReturnType<typeof useClaim>;
}) {
  const { status } = snapshot;
  const { actionError, setActionError, reportError } = useActionErrorState();
  const [selectedSlot, setSelectedSlot] = useState<number | null>(null);
  const [declaring, setDeclaring] = useState(false);
  const [pending, setPending] = useState(false);
  // Which rack is awaiting a refill confirmation (nickname), if any.
  const [refillConfirm, setRefillConfirm] = useState<string | null>(null);

  // Controls unlock when this browser session holds the device claim.
  const locked = !claim.held;
  const token = claim.token;

  // Standard Opentrons definitions from the gateway's /labware (empty when
  // opentrons-shared-data isn't installed there). Fetched once — the catalog
  // is immutable for the gateway process lifetime.
  const [labwareEntries, setLabwareEntries] = useState<CatalogEntry[]>([]);
  useEffect(() => {
    let cancelled = false;
    const authored = new Set(OT2_CATALOG.map((e) => e.declare));
    getLabwareList()
      .then((r) => {
        if (cancelled) return;
        setLabwareEntries(
          r.definitions
            .filter((d) => !authored.has(d.load_name))
            .map(catalogEntryFromLabware),
        );
      })
      .catch(() => {
        /* endpoint absent or shared-data not installed — authored catalog only */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const deviceDeck = deviceDeckFromStatus(status);
  const robotModules = robotModulesFromStatus(status);
  const moduleSlots = pairModuleSlots(deviceDeck, robotModules);
  const declaredMap = useMemo(
    () => (deviceDeck ? declaredMapFromDeck(deviceDeck) : {}),
    [deviceDeck],
  );
  const tipRacks = tipRacksFromStatus(status);
  const mountedTips = mountedTipsFromStatus(status);
  const claimedBy = claimedByFromStatus(status);
  const robot = robotInfoFromStatus(status);
  const claimedByMe = claimedBy != null && claimedBy.session_id === claim.sessionId;

  const components = status.components ?? {};
  const pipLeft = components["pipette_left"];
  const pipRight = components["pipette_right"];
  const ssh = components["ssh"];
  const protocol = components["protocol"];

  const lightsRaw = components["lights"]?.state;
  const lightsOn = lightsRaw === "on";
  const lightsKnown = lightsRaw === "on" || lightsRaw === "off";
  // Gateway session state for the CONNECTED toggle: anything but
  // requires_init / unknown counts as connected (ready/busy while up).
  const deviceOn =
    status.equipment_status !== "requires_init" && status.equipment_status !== "unknown";

  const selectedView = selectedSlot != null ? buildSlotView(selectedSlot, deviceDeck, {}) : null;
  const selectedDeclare = selectedSlot != null ? (declaredMap[String(selectedSlot)] ?? null) : null;

  const mismatchSlots = deviceDeck
    ? Object.entries(deviceDeck.slots)
        .filter(([, s]) => s.slot_state === "mismatch")
        .map(([slot]) => Number(slot))
        .sort((a, b) => a - b)
    : [];

  function declare(entry: CatalogEntry | null) {
    if (locked || selectedSlot == null || declaring) return;
    setActionError(null);
    setDeclaring(true);
    // Full-layout replace: re-send every currently-declared slot (exact
    // load_names preserved by declaredMapFromDeck) with this slot updated.
    const next = nextDeclaration(declaredMap, selectedSlot, entry?.declare ?? null);
    postDeckDeclare(token, next)
      .then(() => refetch())
      .catch((e: unknown) => reportError(e, "deck.declare"))
      .finally(() => setDeclaring(false));
  }

  function runControl(name: string, fn: () => Promise<unknown>) {
    if (locked || pending) return;
    setActionError(null);
    setPending(true);
    fn()
      .then(() => refetch())
      .catch((e: unknown) => reportError(e, name))
      .finally(() => setPending(false));
  }

  function refillRack(nickname: string) {
    setRefillConfirm(null);
    runControl("tips.reset", () => postTipsReset(token, nickname));
  }

  function clearAll() {
    if (locked || declaring) return;
    setActionError(null);
    setDeclaring(true);
    deleteDeckDeclare(token)
      .then(() => refetch())
      .catch((e: unknown) => reportError(e, "deck.declare"))
      .finally(() => setDeclaring(false));
  }

  const controlHint = locked ? "Take control first (claim the device)" : undefined;

  return (
    <div className="flex flex-col gap-4">
      {/* Header strip */}
      <header className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-lg font-semibold text-ink dark:text-slate-100">
            {snapshot.name}
          </h2>
          <p className="truncate text-xs text-ink-subtle dark:text-slate-500">
            <span className="uppercase">{snapshot.kind}</span> ·{" "}
            <span className="font-mono">{snapshot.id}</span>
            {robot?.robot_name && (
              <>
                {" "}
                · robot <span className="font-mono">{robot.robot_name}</span>
              </>
            )}
            {robot?.api_version && (
              <>
                {" "}
                · API <span className="font-mono">{robot.api_version}</span>
              </>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <ActionErrorBadge error={actionError} />
          <LastErrorBadge error={status.last_error} />
          <TileButton
            onClick={() => (claim.held ? void claim.release() : void claim.acquire())}
            disabled={claim.pending}
            variant={claim.held ? "primary" : "default"}
            title={
              claim.held
                ? "You hold the device claim — click to release control"
                : "Acquire the cooperative claim (STATUS_SPEC v1.1) to unlock the controls"
            }
          >
            {claim.held ? "RELEASE CONTROL" : "TAKE CONTROL"}
          </TileButton>
          <StatusPill state={status.equipment_status} />
        </div>
      </header>

      {claim.error && (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-4 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
          {claim.error}
        </p>
      )}

      {/* Session controls. The toggle connects/disconnects the GATEWAY control
          session (NOT robot power); PAUSE pauses a running protocol (not an
          e-stop). */}
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <TileButton
          onClick={() =>
            deviceOn
              ? runControl("shutdown", () => postShutdown(token))
              : runControl("startup", () => postStartup(token))
          }
          disabled={locked || pending}
          variant={deviceOn ? "primary" : "default"}
          title={
            controlHint ??
            (deviceOn
              ? "Gateway session connected — click to disconnect (does NOT power off the robot)"
              : "Click to connect & initialize the gateway session")
          }
        >
          <span
            className={[
              "mr-1 inline-block h-2 w-2 rounded-full",
              deviceOn ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]" : "bg-slate-400",
            ].join(" ")}
            aria-hidden
          />
          {deviceOn ? "CONNECTED" : "DISCONNECTED"}
        </TileButton>
        <TileButton
          onClick={() => runControl("home", () => postHome(token))}
          disabled={locked || pending}
          title={controlHint ?? "Home the gantry (requires a connected session)"}
        >
          HOME
        </TileButton>
        <TileButton
          onClick={() => runControl("pause", () => postPause(token))}
          disabled={locked || pending}
          variant="danger"
          title={
            controlHint ??
            "Pause a running protocol — not an emergency stop (use the robot's physical e-stop); does not disconnect"
          }
        >
          PAUSE
        </TileButton>
        <TileButton
          onClick={() => runControl("resume", () => postResume(token))}
          disabled={locked || pending}
          title={controlHint ?? "Resume a paused protocol"}
        >
          RESUME
        </TileButton>
        <TileButton
          onClick={() => runControl("lights.set", () => postSetLights(token, !lightsOn))}
          disabled={locked || pending}
          title={
            controlHint ??
            (lightsKnown
              ? lightsOn
                ? "Lights on — click to turn off"
                : "Lights off — click to turn on"
              : "Lights state not reported — click to turn on")
          }
        >
          <span
            className={[
              "mr-1.5 inline-block h-2.5 w-2.5 rounded-full",
              lightsOn
                ? "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.7)]"
                : "bg-slate-900 dark:bg-black",
            ].join(" ")}
            aria-hidden
          />
          Light
        </TileButton>
      </div>

      {claimedBy && !claimedByMe && (
        <p className="rounded-md border border-sky-200 bg-sky-50 px-4 py-2 text-xs text-sky-900 dark:border-sky-900/50 dark:bg-sky-950/30 dark:text-sky-200">
          Controlled by <span className="font-semibold">{claimedBy.owner}</span>
          {claimedBy.expires_at && (
            <>
              {" "}
              — claim expires <span className="font-mono">{claimedBy.expires_at}</span>
            </>
          )}
          . Control writes will be refused (423) while the claim is held.
        </p>
      )}

      {mismatchSlots.length > 0 && (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-4 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
          Declared intent disagrees with the observed deck at slot
          {mismatchSlots.length > 1 ? "s" : ""} {mismatchSlots.join(", ")} — click the flagged slot
          for details.
        </p>
      )}

      {snapshot.fetch_error && <FetchErrorBand error={snapshot.fetch_error} />}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        {/* Left column: deck + declare */}
        <div className="flex flex-col gap-4">
          <Section title="Deck — declared intent vs observed hardware">
            <DeckPanel
              deviceDeck={deviceDeck}
              robotModules={robotModules}
              selectedSlot={selectedSlot}
              onSelectSlot={setSelectedSlot}
              variant="page"
              tipRacks={tipRacks}
            />
            {!deviceDeck && (
              <p className="mt-2 text-xs text-ink-subtle dark:text-slate-500">
                This gateway doesn&apos;t publish a normalized deck on /status yet — deck view
                unavailable.
              </p>
            )}
          </Section>

          {selectedView && selectedSlot != null && (
            <Section title={`Slot ${selectedSlot} detail`}>
              <div className="flex flex-col gap-1">
                <KV k="State" v={selectedView.state} />
                {selectedView.moduleName && <KV k="Module" v={selectedView.moduleName} />}
                {selectedView.label && <KV k="Labware" v={selectedView.label} />}
                {selectedView.loadName && (
                  <KV k="Load name (observed)" v={selectedView.loadName} mono />
                )}
                {selectedDeclare && <KV k="Declared as" v={selectedDeclare} mono />}
                {selectedView.state === "mismatch" && selectedView.declared && (
                  <p className="mt-1 rounded bg-amber-50 px-2 py-1 text-[11px] text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                    Mismatch: declared{" "}
                    <span className="font-mono">
                      {selectedView.declared.load_name || selectedView.declared.kind}
                    </span>{" "}
                    but observed{" "}
                    <span className="font-mono">
                      {selectedView.loadName || selectedView.kind || "?"}
                    </span>
                    .
                  </p>
                )}
              </div>
            </Section>
          )}

          <Section title="Declare deck intent">
            <DeclarePicker
              selectedSlot={selectedSlot}
              currentDeclare={selectedDeclare}
              locked={locked}
              onDeclare={declare}
              customEntries={labwareEntries}
            />
            <div className="mt-2 border-t border-slate-100 pt-2 dark:border-slate-800">
              <button
                type="button"
                disabled={locked || declaring || Object.keys(declaredMap).length === 0}
                onClick={clearAll}
                className="rounded-md border border-rose-300 px-2 py-1 text-xs text-rose-700 hover:border-rose-400 disabled:cursor-not-allowed disabled:opacity-50 dark:border-rose-900 dark:text-rose-300"
                title="Clears every operator-declared slot (observed hardware is unaffected)"
              >
                Clear all declared intent
              </button>
            </div>
          </Section>
        </div>

        {/* Right column: selected plate / robot / pipettes / modules / tips / claim */}
        <div className="flex flex-col gap-4">
          <Section title="Selected plate">
            <PlateInspector
              slot={selectedSlot}
              view={selectedView}
              tipRacks={tipRacks}
              mountedTips={mountedTips}
            />
          </Section>

          <Section title="Robot">
            <div className="flex flex-col gap-1">
              <KV k="Robot" v={robot?.robot_name ?? "—"} mono />
              <KV k="API version" v={robot?.api_version ?? "—"} mono />
              <KV
                k="Run active"
                v={robot?.run_active == null ? "—" : robot.run_active ? "yes" : "no"}
              />
              <div className="mt-1 flex items-center gap-3">
                <span className="flex items-center gap-1.5 text-xs text-ink-subtle dark:text-slate-400">
                  <Dot ok={ssh?.state === "connected" || ssh?.state === "ready"} /> SSH{" "}
                  <span className="font-mono">{ssh?.state ?? "—"}</span>
                </span>
                <span className="flex items-center gap-1.5 text-xs text-ink-subtle dark:text-slate-400">
                  <Dot ok={protocol?.state === "connected" || protocol?.state === "ready"} />{" "}
                  Protocol <span className="font-mono">{protocol?.state ?? "—"}</span>
                </span>
              </div>
            </div>
          </Section>

          <Section title="Pipettes">
            <div className="flex flex-col gap-1">
              <KV k="Left mount" v={pipetteLabel(pipLeft?.state)} />
              <KV k="Right mount" v={pipetteLabel(pipRight?.state)} />
            </div>
          </Section>

          <Section title="Modules (live telemetry)">
            {moduleSlots.size === 0 && robotModules.length === 0 ? (
              <p className="text-xs text-ink-subtle dark:text-slate-500">
                No modules on the deck or attached.
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {Array.from(moduleSlots.entries())
                  .sort(([a], [b]) => a - b)
                  .map(([slot, m]) => (
                    <li
                      key={slot}
                      className="flex items-center justify-between gap-2 rounded-md border border-slate-200 px-2 py-1.5 dark:border-slate-800"
                    >
                      <span className="min-w-0 truncate text-xs text-ink dark:text-slate-200">
                        <span className="font-semibold">Slot {slot}</span> · {m.name}
                      </span>
                      <ModuleReadout live={m.live} compact />
                    </li>
                  ))}
              </ul>
            )}
          </Section>

          <Section title="Tip racks">
            {tipRacks.length === 0 ? (
              <p className="text-xs text-ink-subtle dark:text-slate-500">
                No tracked tip racks (register via <span className="font-mono">tips.reset</span> or
                a protocol setup).
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {tipRacks.map((r) => (
                  <li
                    key={r.nickname}
                    className="rounded-md border border-slate-200 px-2 py-1.5 dark:border-slate-800"
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="min-w-0 truncate font-mono text-xs text-ink dark:text-slate-200">
                        {r.nickname}
                      </span>
                      <span className="shrink-0 text-xs tabular-nums text-ink-subtle dark:text-slate-400">
                        {r.available}/{r.total} available
                      </span>
                    </div>
                    {(r.empty > 0 || r.touched > 0) && (
                      <p className="mt-0.5 text-[10px] text-ink-subtle dark:text-slate-500">
                        {r.empty} used · {r.touched} touched
                      </p>
                    )}
                    {/* Refill is always an explicit operator act: the gateway
                        cannot see new tips going in, and a wrong "full" sends
                        the head onto bare holes. Hence the confirm step. */}
                    {r.available < r.total && (
                      <div className="mt-1.5">
                        {refillConfirm === r.nickname ? (
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] text-ink-subtle dark:text-slate-400">
                              All {r.total} tips physically present?
                            </span>
                            <button
                              type="button"
                              disabled={locked || pending}
                              onClick={() => refillRack(r.nickname)}
                              className="rounded border border-sky-500 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700 hover:bg-sky-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-sky-500 dark:text-sky-300 dark:hover:bg-sky-950/40"
                            >
                              Yes, refilled
                            </button>
                            <button
                              type="button"
                              onClick={() => setRefillConfirm(null)}
                              className="text-[10px] text-ink-subtle underline dark:text-slate-400"
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <button
                            type="button"
                            disabled={locked || pending}
                            onClick={() => setRefillConfirm(r.nickname)}
                            title={controlHint ?? "Mark every tip in this rack fresh again"}
                            className="rounded border border-slate-300 px-1.5 py-0.5 text-[10px] text-ink-subtle hover:border-slate-400 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:text-slate-400"
                          >
                            Mark refilled
                          </button>
                        )}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="Mounted tips">
            {mountedTips.length === 0 ? (
              <p className="text-xs text-ink-subtle dark:text-slate-500">No tip currently mounted.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {mountedTips.map((t) => (
                  <li key={t.pipette} className="text-xs text-ink dark:text-slate-200">
                    <span className="font-semibold">{t.pipette}</span>:{" "}
                    <span className="font-mono">
                      {t.rack ?? "?"} {t.well ?? ""}
                    </span>
                    {t.last_sample && (
                      <span className="text-ink-subtle dark:text-slate-400">
                        {" "}
                        · last sample <span className="font-mono">{t.last_sample}</span>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="Claim">
            {claimedBy ? (
              <div className="flex flex-col gap-1">
                <KV k="Holder" v={claimedByMe ? `${claimedBy.owner} (you)` : claimedBy.owner} mono />
                <KV k="Session" v={claimedBy.session_id || "—"} mono />
                <KV k="Expires" v={claimedBy.expires_at || "—"} mono />
              </div>
            ) : (
              <p className="text-xs text-ink-subtle dark:text-slate-500">
                No claim held — click <span className="font-semibold">Take control</span> to
                acquire one and unlock the controls.
              </p>
            )}
          </Section>
        </div>
      </div>

      {/* Footer strip */}
      <footer className="flex items-end justify-between gap-2 border-t border-slate-100 pt-2 text-xs text-ink-subtle dark:border-slate-800 dark:text-slate-400">
        <div className="min-w-0 flex-1 space-y-0.5">
          {status.message && (
            <div className="truncate" title={status.message}>
              {status.message}
            </div>
          )}
          {(status.required_actions?.length ?? 0) > 0 && (
            <div className="truncate">
              <span className="font-semibold text-amber-700 dark:text-amber-400">
                Action needed:
              </span>{" "}
              <span className="font-mono">{status.required_actions?.join(", ")}</span>
            </div>
          )}
          <a
            href="/docs"
            className="text-sky-700 underline-offset-2 hover:underline dark:text-sky-400"
          >
            API docs (Swagger) ↗
          </a>
        </div>
        <div className="flex shrink-0 items-center gap-2 tabular-nums">
          {snapshot.latency_ms != null && <span>{snapshot.latency_ms} ms</span>}
          <StalenessIndicator fetchedAt={snapshot.fetched_at} />
        </div>
      </footer>
    </div>
  );
}
