/**
 * Wire types for the gateway's STATUS_SPEC v1.1 surface, plus the
 * snapshot wrapper the ported dashboard components expect.
 *
 * `GatewaySnapshot` mirrors the shape of the ac-organic-lab dashboard's
 * `EquipmentSnapshot` closely enough that components ported from there
 * (DeckPanel, ControlPanel, …) run unchanged: the gateway's `/status`
 * envelope is the `status` field, and the fetch bookkeeping
 * (`fetch_error`, `latency_ms`, `fetched_at`) is synthesized client-side
 * by `useGatewayStatus` instead of by the dashboard aggregator.
 */

export type EquipmentState =
  | "ready"
  | "busy"
  | "requires_init"
  | "degraded"
  | "dry_run"
  | "error"
  | "e_stop"
  | "unknown";

export interface ComponentStatus {
  connected: boolean;
  state: string;
  message?: string | null;
  last_event_at?: string | null;
}

export interface MetricValue {
  value: number | string | boolean;
  unit?: string | null;
  timestamp?: string | null;
}

export interface ErrorInfo {
  code?: string | null;
  message: string;
  severity: "info" | "warning" | "error" | "critical";
  timestamp: string;
}

export interface EquipmentStatus {
  protocol_version: string;
  equipment_id: string;
  equipment_name: string;
  equipment_kind: string;
  equipment_version?: string | null;
  host?: string | null;
  equipment_status: EquipmentState;
  message?: string | null;
  required_actions?: string[];
  device_time?: string;
  uptime_seconds?: number | null;
  components?: Record<string, ComponentStatus>;
  metrics?: Record<string, MetricValue>;
  last_error?: ErrorInfo | null;
  allowed_actions?: string[];
  details?: Record<string, unknown>;
}

/** Client-side transport failure (the browser could not reach the gateway).
 *  Shape-compatible with the dashboard's aggregator `fetch_error`. */
export interface FetchError {
  kind: string;
  http_status?: number | null;
}

export interface GatewaySnapshot {
  id: string;
  name: string;
  kind: string;
  status: EquipmentStatus;
  fetch_error: FetchError | null;
  latency_ms: number | null;
  fetched_at: string;
}

// ---------------------------------------------------------------------------
// Deck / modules (details.snapshot.deck, details.robot.modules)
// ---------------------------------------------------------------------------

/** One slot of the gateway's normalized deck (details.snapshot.deck.slots). */
export interface DeviceDeckSlot {
  labware: {
    kind: string;
    load_name: string;
    display_name?: string | null;
    is_tiprack?: boolean;
    rows?: number | null;
    columns?: number | null;
    plate_id?: string | null;
  } | null;
  module: {
    module_name: string;
    status?: string | null;
    serial_number?: string | null;
  } | null;
  slot_state: "empty" | "declared" | "occupied" | "in_use" | "mismatch";
  source: "run" | "repl" | "declared" | "empty";
  declared?: { kind: string; load_name: string } | null;
}

export interface DeviceDeck {
  source: "run" | "repl" | "declared" | "empty";
  slots: Record<string, DeviceDeckSlot>;
  timestamp?: string;
}

/** One attached hardware module from `details.robot.modules` — live telemetry
 *  straight off the robot. Distinct from the deck's declared module, which is
 *  operator intent; the panel pairs the two by serial or module family. */
export interface RobotModule {
  model: string;
  type: string;
  serial?: string | null;
  id?: string | null;
  status?: string | null;
  current_temperature?: number | null;
  target_temperature?: number | null;
}

// ---------------------------------------------------------------------------
// Claims (STATUS_SPEC v1.1)
// ---------------------------------------------------------------------------

export interface ClaimedBy {
  session_id: string;
  owner: string;
  expires_at: string;
}

export interface ClaimResponse {
  claim_token: string;
  heartbeat_interval_s: number;
  expires_at: string;
}

// ---------------------------------------------------------------------------
// Labware catalog (GET /labware)
// ---------------------------------------------------------------------------

export interface LabwareSummary {
  load_name: string;
  display_name: string;
  display_category?: string;
  is_tiprack?: boolean;
  rows?: number;
  columns?: number;
  well_count?: number;
  well_volume_ul?: number | null;
  version?: number;
  namespace?: string;
  source?: string;
}
