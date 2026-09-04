/**
 * Per-well state for the selected deck slot — the model both inspector views
 * render.
 *
 * Two different truths arrive keyed by well id and are unified here:
 *
 * - **Tip racks** get their state from `details.tip_racks[nickname].tips`
 *   (the gateway's `TipStateStore`). That map lists only *non-fresh* wells, so
 *   absence means fresh — never "unknown".
 * - **Plates** get theirs from the slot labware's own `wells`, the
 *   orchestrator-tracked samples the deck folds onto the slot.
 *
 * The join key is the slot's `nickname` (stamped by `build_deck` so it survives
 * run/REPL precedence), not `display_name`, which the robot overwrites with its
 * own label.
 *
 * A rack the gateway has never registered is **`untracked`**, deliberately
 * distinct from "full": nothing has claimed those tips, but nothing has
 * confirmed them either, and drawing 96 fresh tips for a rack the tracker has
 * never seen is the lie this state exists to avoid.
 */

import type { LabwareGeometry } from "./labware-geometry";
import type { MountedTip, TipRackSummary } from "./ot2-deck";
import type { WellSample } from "./types";

export type WellKind =
  /** Tip rack: a fresh, never-used tip. */
  | "fresh"
  /** Tip rack: the tip touched a sample and is still in the rack. */
  | "touched"
  /** Tip rack: used and dropped — the well is an empty hole. */
  | "empty"
  /** Tip rack: the tip is on a pipette right now — a hole, but one that has a
   *  tip which may come back to it. */
  | "mounted"
  /** Plate: a tracked sample sits in this well. */
  | "sample"
  /** Plate: no sample recorded for this well. */
  | "vacant"
  /** Nothing is known about this well (an unregistered rack). */
  | "unknown";

export const TIP_KINDS: readonly WellKind[] = [
  "fresh",
  "touched",
  "mounted",
  "empty",
] as const;
export const PLATE_KINDS: readonly WellKind[] = ["sample", "vacant"] as const;

export interface WellCell {
  well: string;
  /** 0-based, from the definition's ordering: row 0 = A, column 0 = 1. */
  row: number;
  column: number;
  kind: WellKind;
  /** Sample id (plate or touched tip) or raw status string, when there is one. */
  detail?: string;
  volumeUl?: number | null;
  /** True when a pipette currently holds the tip taken from this well. */
  mounted?: boolean;
}

export interface PlateWellModel {
  contents: "tiprack" | "plate";
  /** False when the gateway holds no record for this labware at all. */
  tracked: boolean;
  cells: WellCell[];
  /** Well ids in column-major order, row 0 first within each column. */
  byWell: Record<string, WellCell>;
  counts: Partial<Record<WellKind, number>>;
  total: number;
  rows: number;
  columns: number;
}

const ROW_LETTERS = "ABCDEFGHIJKLMNOP";

/** Statuses TipStateStore treats as "fresh" (mirrors `_FRESH_STATUSES`). */
const FRESH_STATUSES = new Set(["", "new", "unused", "clean", "available"]);

/**
 * Well ids in column-major order. Prefers the definition's own `ordering` (it
 * is authoritative, and correct for non-96 layouts); falls back to synthesising
 * `<row letter><column>` from a rows × columns grid when no definition is
 * available, which is what the deck's own `rows`/`columns` give us.
 */
export function wellOrder(
  geometry: LabwareGeometry | null,
  rows: number,
  columns: number,
): { well: string; row: number; column: number }[] {
  if (geometry) {
    const out: { well: string; row: number; column: number }[] = [];
    geometry.ordering.forEach((col, columnIndex) => {
      col.forEach((well, rowIndex) => out.push({ well, row: rowIndex, column: columnIndex }));
    });
    return out;
  }
  const out: { well: string; row: number; column: number }[] = [];
  for (let c = 0; c < columns; c++) {
    for (let r = 0; r < rows; r++) {
      out.push({ well: `${ROW_LETTERS[r] ?? "?"}${c + 1}`, row: r, column: c });
    }
  }
  return out;
}

function tipKind(status: string | undefined): { kind: WellKind; detail?: string } {
  if (status == null) return { kind: "fresh" }; // absent from the map == fresh
  const normalized = status.trim().toLowerCase();
  if (FRESH_STATUSES.has(normalized)) return { kind: "fresh" };
  if (normalized === "empty") return { kind: "empty" };
  if (normalized === "on_pipette") return { kind: "mounted" };
  return { kind: "touched", detail: status };
}

export interface BuildWellModelArgs {
  /** True when the slot holds a tip rack (deck `is_tiprack` / kind). */
  isTiprack: boolean;
  rows: number;
  columns: number;
  geometry: LabwareGeometry | null;
  /** The tracker's summary for this rack, or null when it holds none. */
  tipRack: TipRackSummary | null;
  /** Orchestrator-tracked plate samples folded onto this slot. */
  samples: WellSample[] | null;
  /** Live mounted tips, used to ring the wells whose tips are on a head. */
  mountedTips?: MountedTip[];
  /** This slot's deck slot key — the join key for a mounted tip's `rack`. */
  slot?: string | number | null;
  /** This slot's nickname, accepted as a secondary match for a mount recorded
   *  by a setup recipe rather than by slot. */
  nickname?: string | null;
}

/**
 * Build the per-well model for one slot. Never throws: an unreadable or absent
 * source degrades to `unknown` cells with `tracked: false` rather than
 * inventing state.
 */
export function buildWellModel({
  isTiprack,
  rows,
  columns,
  geometry,
  tipRack,
  samples,
  mountedTips = [],
  slot,
  nickname,
}: BuildWellModelArgs): PlateWellModel {
  const order = wellOrder(geometry, rows, columns);
  const contents: "tiprack" | "plate" = isTiprack ? "tiprack" : "plate";
  const tracked = isTiprack ? tipRack != null : samples != null;

  // Wells whose tips are on a pipette right now. `wells` is the covered span
  // (a whole column for a multi-channel head); older gateways send only `well`.
  //
  // Matched by SLOT, because a mount's `rack` is the tip store's slot key and a
  // declared deck has no nickname at all — matching on nickname alone left the
  // comparison against `null`, which drew every mounted tip on every slot (so
  // one stale 8-tip mount showed A5-H5 "on a head" on every rack and plate).
  // A slot we cannot identify gets no rings: the mounted-tip panel still
  // reports the mount, and inventing an owner is what caused the bug.
  const owners = new Set(
    [slot == null ? null : String(slot), nickname].filter(
      (id): id is string => id != null && id !== "",
    ),
  );
  const mountedWells = new Set<string>();
  for (const tip of mountedTips) {
    if (tip.rack == null || !owners.has(tip.rack)) continue;
    for (const well of tip.wells ?? (tip.well ? [tip.well] : [])) mountedWells.add(well);
  }

  const sampleByWell = new Map<string, WellSample>();
  for (const s of samples ?? []) {
    if (s && typeof s.well === "string") sampleByWell.set(s.well, s);
  }

  const cells: WellCell[] = order.map(({ well, row, column }) => {
    let kind: WellKind;
    let detail: string | undefined;
    let volumeUl: number | null | undefined;

    if (!tracked) {
      kind = "unknown";
    } else if (isTiprack) {
      ({ kind, detail } = tipKind(tipRack?.tips[well]));
    } else {
      const sample = sampleByWell.get(well);
      kind = sample ? "sample" : "vacant";
      detail = sample?.sample_id ?? undefined;
      volumeUl = sample?.volume_ul ?? null;
    }
    return {
      well,
      row,
      column,
      kind,
      detail,
      volumeUl,
      mounted: mountedWells.has(well) || kind === "mounted" || undefined,
    };
  });

  const counts: Partial<Record<WellKind, number>> = {};
  for (const cell of cells) counts[cell.kind] = (counts[cell.kind] ?? 0) + 1;

  const byWell: Record<string, WellCell> = {};
  for (const cell of cells) byWell[cell.well] = cell;

  return {
    contents,
    tracked,
    cells,
    byWell,
    counts,
    total: cells.length,
    rows: geometry?.rows ?? rows,
    columns: geometry?.columns ?? columns,
  };
}

