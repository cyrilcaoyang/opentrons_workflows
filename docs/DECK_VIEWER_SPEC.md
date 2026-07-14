# Read-only Deck / Plate Viewer — Spec

**Repo:** `opentrons-server` (device gateway) · **Status:** spec, not built.
**Goal:** an **optional, opt-in, read-only** local web page served by the gateway
that visualizes the OT-2 deck + loaded plate from its own `/status`. For bench work
and standalone / OT-2-only workflows (e.g. the complexation dispense test) and for
debugging when the central dashboard is down.

## Scope

**In:** a single self-contained HTML page that polls this gateway's `/status` and
renders the 12-slot deck, per-slot labware (with mini well-grids), modules, and the
tracked plate's well metadata; a small status header.

**Out (non-goals):**
- **No control.** The page never calls `/control/*`. It is view-only. (A future
  claim-gated, dry-run-default control surface would be a *separate* opt-in flag —
  see §7. Do not fold it in here.)
- **Not the production service.** Off by default; the `ot2-gateway` NSSM service does
  not enable it. Mirrors the xArm lesson (prod runs the api surface, not `web`).
- **No auth of its own.** Read-only over data `/status` already exposes; Tailscale
  ACLs gate access, exactly as STATUS_SPEC §11 prescribes for device repos. Because it
  is read-only it does **not** reopen the control-surface exposure the ROADMAP tracks.
- No build step, no npm, no framework — plain inline HTML/CSS/JS.

## 1. Opt-in & deployment

Gate on a single env flag, read in `create_app()` alongside the existing `OT2_*`
flags (`gateway/api.py`):

```python
web_viewer = os.environ.get("OT2_WEB_VIEWER", "false").lower() in {"1", "true", "yes"}
...
if web_viewer:
    _mount_viewer(app, service)   # adds GET /viewer only
```

- **Default off** → the production `ot2-gateway` service (which sets no such var)
  returns **404** on `/viewer`. Verified by test.
- **Enable for a bench run:** `OT2_WEB_VIEWER=true uv run uvicorn opentrons_server.gateway.api:app --port 8020`, then open `http://<gateway-host>:8020/viewer`.
- No separate process/CLI — one flag gating one route on the same app.

## 2. Endpoint

| Method | Path | Returns | When |
|---|---|---|---|
| GET | `/viewer` | `HTMLResponse` — the self-contained page | only if `OT2_WEB_VIEWER` on |

The page is a Python string constant served via `fastapi.responses.HTMLResponse`
(inline CSS + JS, no static-files mount, no assets). The browser then polls the
**existing** `/status` endpoint client-side — the viewer adds **no new server state
and no new data endpoint**. `/status` is already `Access-Control-Allow-Origin: *`
(`create_app` CORS), so the fetch works even cross-origin.

## 3. Data source — all from `/status`

Everything renders from the existing envelope; no backend changes:

- **`equipment_status`, `message`, `equipment_name`** → header.
- **`details.robot`** → `robot_name`, `api_version`, `reachable`.
- **`components.pipette_left/right`** → mounts + pipette names.
- **`details.claimed_by`** → "controlled by `<owner>`" banner when held (so a bench
  viewer shows when automation owns the device — read-only awareness).
- **`details.snapshot.deck`** → the 12-slot grid (the shape the dashboard's
  `LiquidHandlerTile` already consumes):
  ```
  deck = { source: "run"|"declared"|"repl"|"empty",
           slots: { "1".."12": {
             slot_state: "empty"|"in_use"|"declared"|"mismatch",
             source, labware: { kind, load_name, display_name, is_tiprack,
                                rows, columns, plate_id } | null,
             module: { module_name, ... } | null,
             declared: {...} | null } } }
  ```
- **`details.loaded_plate`** → `{ plate_id, model, wells: [{ well, sample_id,
  volume_ul, notes }] }` — overlaid on its slot's mini-grid (well tooltips).

## 4. Rendering

### Deck layout (match the dashboard exactly)

OT-2 deck = 12 numbered slots, **3 columns × 4 rows**, slot 1 bottom-left, slot 3
bottom-right, slot 12 top-right. Render **top row first** so the screen matches the
physical deck (same convention as `LiquidHandlerTile`):

```
┌──────┬──────┬──────┐
│  10  │  11  │  12  │   ← trash usually at 12
├──────┼──────┼──────┤
│   7  │   8  │   9  │
├──────┼──────┼──────┤
│   4  │   5  │   6  │
├──────┼──────┼──────┤
│   1  │   2  │   3  │
└──────┴──────┴──────┘
```

### Per-slot cell

- **Empty** → muted outline, slot number only.
- **Labware** → load-name/display-name label + a **mini well-grid** sized `rows × columns`
  (reuse the dashboard `MiniPlate` idea: a CSS grid of dots). Tipracks styled distinctly
  (`is_tiprack`); trash (`kind == "trash"`) styled as a bin.
- **Module** → module name badge (once §5 of the modules work lands / `module` present).
- **`slot_state` cues:** `in_use` solid, `declared` dashed outline, **`mismatch`** flagged
  red with `declared` vs observed in the tooltip (surfaces the same conflict the dashboard does).
- **Wells with liquid** (from `loaded_plate`) → filled dot + tooltip `well · sample_id · volume_ul`.

### Header strip

`equipment_name` · state pill (color by `equipment_status`) · `robot_name api_version` ·
transport hint (`deck.source == "run"` ⇒ HTTP, else SSH) · `claimed_by.owner` banner if held ·
last-updated ticker.

### Refresh

Client-side `fetch('/status')` every **2–3 s** (config const), diff-free full re-render
(the grid is tiny). Pause polling when the tab is hidden. Show a "stale / unreachable"
badge if a fetch fails (mirrors the aggregator's `fetch_error` semantics) but keep the
last-known deck on screen.

## 5. Security posture (restating, because it's the crux)

- **Read-only.** The page's JS only ever GETs `/status`. No `/control/*`, no claim, no writes.
- **No new exposure.** It surfaces only what `/status` already serves openly on the tailnet.
- **Off in production.** The `ot2-gateway` service never sets `OT2_WEB_VIEWER`.
- Because it cannot actuate, it stays *out* of the control-surface-exposure problem
  (ROADMAP) and does not touch the dashboard's audit / `ac_auth` model.

## 6. Tests

- `GET /viewer` → **200 + `text/html`** when `create_app` built with the flag on
  (construct via `create_app` with env patched); **404** when off.
- The returned HTML **contains no `/control` string** (guard against accidental control
  wiring) and references `/status`.
- Optional: a tiny JSON→cells unit for the slot-ordering / mismatch mapping if that logic
  is factored into a testable helper (kept in JS; or mirror it in a Python helper if we
  want server-side coverage).

## 7. Explicitly deferred — a controlled bench-control mode

If we later want the viewer to *drive* OT-2-only workflows on the bench, it is a
**separate** opt-in (`OT2_WEB_CONTROL`, default off, and only honored when
`OT2_WEB_VIEWER` is also on) with hard rails: acquire a **claim** first, default every
action to **dry-run/simulation**, require an explicit "run on hardware" confirmation,
and a visible "outside the audit trail" notice. Real production control still goes
through the dashboard (audited + `ac_auth`) or a claimed SDK session. Not part of this spec.

## 8. Effort

Small and self-contained: ~1 HTML string constant + a flag-gated route + `_mount_viewer`
helper + 2 route tests. No new dependencies, no data-model or `/status` changes, no
central-repo coordination. It intentionally re-uses the deck shape and slot convention
the dashboard already renders, so the two stay visually consistent (bench mirror, not a
competing UI).

## See also

- `docs/DECK_STATE_PLAN.md` — the normalized `snapshot.deck` shape this renders.
- `ac-organic-lab` `web/src/components/LiquidHandlerTile.tsx` — the dashboard tile whose
  layout/`MiniPlate` convention this mirrors.
- STATUS_SPEC §11 (auth = Tailscale) · §4.9 (`/status` is current state, not a catalog).
- ROADMAP "Control-surface exposure" — why control stays out of a no-auth device page.
