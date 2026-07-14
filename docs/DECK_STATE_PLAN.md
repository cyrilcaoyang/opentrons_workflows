# OT-2 Deck / Labware State Model — Implementation Plan

**Status:** IMPLEMENTED and deployed. Phases 0–2 shipped in this repo
(commit `ede0a85`); Phase 3 (dashboard tile, `deck.declare` SkillDef,
docs) shipped in `ac-organic-lab`. Both OT-2 gateways (`ot2` :8020,
`ot2_complexation` :8021) deployed 2026-07-10 on sdl2-pc-03 and verified
live; the stopgap layouts were migrated into the gateways' declared
stores. Remaining: delete the dashboard's `api/app/deck.py` stopgap after
a soak period (tracked in `ac-organic-lab` EQUIP_STATUS §11). This
document is kept as the design record. **HTTP-transport deck parity
validated live 2026-07-14** (`/status…deck.source == "run"` matched
`GET /runs/{id}` after setup on `ot2cytation`); idle-persistence-across-restart
still to confirm — see `HTTP_DRIVE_VALIDATION.md`.
**Scope:** add a normalized deck/labware state layer to the OT-2 gateway
(`opentrons-server`) that (a) unifies the two orphan state stores, (b) publishes
`details.snapshot.deck` on `/status`, and (c) supports operator-declared layout
so the dashboard's `api/app/deck.py` → `deck_layouts.json` stopgap can be retired.

This plan is the deliverable of the *research + design* step. It is written to be
reviewed and then executed phase-by-phase.

---

## 0. What exists today (verified against the code)

Two disconnected state layers, plus a session-independent HTTP probe:

| Layer | Where | Shape | Populated when |
|---|---|---|---|
| **Service state machine** (leave alone) | `service.py::OT2ServiceState` | `requires_init/connecting/ready/busy/paused/dry_run/error/unknown_outcome/external_control` → mapped to STATUS_SPEC enum by `_equipment_state()` | always |
| **REPL snapshot** | `service.last_snapshot`, filled by `control/state_readers.get_all_states()` over SSH | `{deck:{slots, occupied_slots, empty_slots}, pipettes, labwares, modules}`; `deck.slots["1".."12"] → {type, name, load_name, is_tiprack, uri} \| None` | **only while the gateway owns a REPL session**; `{}`-ish otherwise |
| **Plate/well store** | `gateway/plate_state.py::PlateStateStore` → `details.loaded_plate` | `{plate_id, model, loaded_at, wells:[{well, sample_id, volume_ul, notes}]}`, single plate, JSON-persisted | orchestrator-driven, survives restarts |
| **HTTP probe** | `service.probe_robot()` (robot-server `:31950`) | health / run_active / instruments; **does not read the deck** | on boot + after control actions, cached in `_last_probe` |

Problems: the REPL snapshot and the plate store both describe "what's on the deck"
but are unnormalized, disconnected, and have no per-slot lifecycle. The REPL deck is
empty whenever no session is held (i.e. an idle OT-2 shows a blank deck).

### Cross-repo constraints (from the four sibling-repo surveys)

- **Dashboard (`ac-organic-lab`) — the retire target.** `api/app/deck.py` holds a
  stopgap `DeckLayout{slots: dict[str,str]}` with kind enum `{96-well, 24-well, waste}`,
  persisted to `data/deck_layouts.json` (currently has `ot2` and `ot2_complexation`).
  Its own docstring + `EQUIP_STATUS.md` §11 open-work state the migration target
  explicitly: *"retire the stopgap once opentrons-server publishes real deck contents
  on `/status` (`details.snapshot.deck.slots`); the tile should read that and push
  assignments through a `plate.load`-style skill."* The frontend `LiquidHandlerTile.tsx`
  renders slots on a 3-col×4-row grid (slot 1 bottom-left, 12 top-right), and its
  `MiniPlate` component takes **rows/columns** to draw any grid generically. Writes are
  control-class: claim-gated, `operator+`, audited as `control_action`/`deck.set`.
- **organic-solubility (the consumer) — contract to keep stable.** Consumes exactly
  `GET /status → details.loaded_plate = {plate_id, wells:[{well, volume_ul, sample_id, notes}]}`
  and calls `/control/plate/{load,unload}`, `/control/well/update`, `/control/setup`.
  `plate_id` sent to the gateway is a **nickname** (`"D"`), not a barcode. It has a
  first-class **declared-layout** concept (`PlateDef.starts_at = "ot2/slot_2"`, an
  operator precondition) that is *not* reconciled against `/control/setup` anywhere —
  the surveys flag "per-slot occupancy + labware kind + declared-vs-observed
  reconciliation" as the missing piece (`docs/integration_ot2.md` §7/§8.5). It does
  **not** consume any per-slot deck snapshot today.
- **xarm-translocation — no plate state.** Pure motion controller. Its motion-graph
  nodes name OT-2 slots (`opentrons_2/4/6_{high,low}_{empty,grip_120,press}`), only
  slots **2/4/6** are arm-reachable. It never tells the OT-2 "I placed plate X in
  slot N" — **the OT-2 must own its own loaded truth.** Its only physical-handoff
  signal is `/status.details.gripper.object_detected` + `error_code 12 = slipped`.
- **PyLabRobot (design reference, not a dependency).** Borrow: `category` string →
  promote to a real `kind` enum; `Resource.model` → `load_name`; `num_items_x/num_items_y`
  → columns/rows; `OTDeck`'s 1-indexed `{1..12}` slot dict; the **structure vs. state**
  serialization split. Skip: the full recursive Resource/Coordinate tree and live
  tip/volume trackers (overkill for a status gateway). Crucially: the **Opentrons
  labware definition already carries everything we need** — `parameters.loadName`,
  `metadata.displayCategory`, `metadata.displayName`, and `ordering` (grid) — so the
  gateway needs no PyLabRobot dependency.

---

## 1. Open scope question — RESOLVED (recommendation)

> Does the model support **declared-layout** from day one, or start **observed-only**?

**Recommendation: declared-layout is in scope from day one — but built *after* the
observed sources (it is Phase 2, not Phase 0).**

Reasons:
1. **It's required to hit the stated goal.** Retiring the dashboard stopgap is
   impossible without the gateway accepting operator intent — the stopgap is *purely*
   a declared layout.
2. **Observed-only regresses the idle deck.** An idle OT-2 has no labware from either
   the robot-server (labware is a run concept) or the REPL (no session). Observed-only
   would render a blank deck before startup — worse than today's stopgap.
3. **The value-add lives in the merge.** `declared` vs `observed` → `mismatch` flagging
   is exactly the reconciliation gap organic-solubility documents.
4. **The gateway already half-holds it.** `service.session_recipe`
   (`{labware:[{nickname, loadname, location}], ...}` from `/control/setup`) is a
   declared layout we can normalize for free.

So the model *shape* supports declared from the start; the *wiring* is sequenced
observed-first so each phase is independently shippable and testable.

---

## 2. The model (`gateway/deck.py` + additions to `models.py`)

### 2.1 Normalized kind enum

Derived, never hand-set. Supersets the tile's tiny `{96-well, 24-well, waste}` enum.

```python
LabwareKind = Literal[
    "96-well", "384-well", "48-well", "24-well", "12-well", "6-well",  # well plates by grid
    "well_plate",   # a wellPlate whose grid isn't one of the named sizes
    "tiprack",
    "reservoir",    # troughs
    "tuberack",
    "trash",
    "adapter",
    "unknown",
]
```

The dashboard tile keeps rendering generically off `rows`/`columns` (its `MiniPlate`
already takes them); `kind` only drives label/icon/greying (waste bar vs. tip dots vs.
plate wells). Extending `LABWARE_TYPES` in the tile to the richer enum is a coordinated
follow-up in `ac-organic-lab` (Phase 3), *not* a blocker here.

### 2.2 Slot / deck models (Pydantic, in `models.py`)

```python
class SlotLabware(BaseModel):
    kind: LabwareKind
    load_name: str
    display_name: Optional[str] = None
    is_tiprack: bool = False
    rows: Optional[int] = None
    columns: Optional[int] = None
    # present only for the tracked plate (unified from PlateStateStore); None otherwise
    plate_id: Optional[str] = None
    wells: Optional[List[WellSample]] = None

class SlotModule(BaseModel):
    module_name: str
    status: Optional[str] = None
    serial_number: Optional[str] = None

SlotState = Literal["empty", "declared", "occupied", "in_use", "mismatch"]
DeckSource = Literal["run", "repl", "declared", "empty"]

class DeckSlot(BaseModel):
    labware: Optional[SlotLabware] = None
    module: Optional[SlotModule] = None
    slot_state: SlotState = "empty"
    source: DeckSource = "empty"
    declared: Optional[SlotLabware] = None   # populated only on mismatch (the losing side)

class DeckState(BaseModel):
    source: DeckSource                        # highest-precedence source contributing any slot
    slots: Dict[str, DeckSlot]                # "1".."12", always all 12 keys present
    timestamp: datetime
```

`details.snapshot` becomes `{deck: <DeckState>, pipettes, labwares, modules}` — the
normalized `deck` replaces the loose one; `pipettes/labwares/modules` stay raw (from
`last_snapshot`) for now.

### 2.3 Pure normalizers (no hardware, no I/O — the heart of Phase 0)

```python
def classify_labware(load_name: str, is_tiprack: bool | None = None,
                     display_category: str | None = None) -> tuple[LabwareKind, int|None, int|None]:
    """(kind, rows, columns) from an Opentrons load_name (+ optional hints).

    load_name is highly structured: '<brand>_<count>_<category>_<...>'
      corning_96_wellplate_360ul_flat      -> ("96-well", 8, 12)
      opentrons_96_tiprack_300ul           -> ("tiprack", 8, 12)
      nest_12_reservoir_15ml               -> ("reservoir", 1, 12)
      opentrons_24_tuberack_nest_1.5ml...  -> ("tuberack", 4, 6)
      opentrons_1_trash_1100ml_fixed       -> ("trash", None, None)
    Falls back to display_category / is_tiprack, then ('unknown', None, None).
    """

STANDARD_GRIDS = {96:(8,12), 384:(16,24), 48:(6,8), 24:(4,6), 12:(1,12)/(3,4)?, 6:(2,3), 1:(1,1)}
# NOTE for review: 12-count is ambiguous (12-reservoir=1x12 vs 12-wellplate=3x4).
# Resolve by category token: reservoir -> 1xN, wellplate -> standard plate grid.

def normalize_repl_slots(repl_deck: dict) -> dict[str, SlotLabware|SlotModule]:
    """Map get_deck_state() output (slots -> {type, load_name, is_tiprack, name}) to
    normalized SlotLabware / SlotModule. Enrich grid from load_name; if the enriched
    REPL reader later provides rows/columns, prefer those."""

def normalize_run_slots(run_doc: dict) -> dict[str, SlotLabware]:
    """Map an active robot-server run's loaded labware (loadLabware commands /
    run 'labware' summary: {loadName, location.slotName, displayName}) to SlotLabware."""

def build_deck(*, run: dict|None, repl: dict|None, declared: dict|None,
               loaded_plate: LoadedPlate|None, nickname_to_slot: dict[str,str],
               busy: bool, now: datetime) -> DeckState:
    """Merge sources with precedence run > repl > declared > empty; attach the tracked
    plate's wells to its slot; flag declared-vs-observed mismatches. Pure function."""
```

### 2.4 Slot-state decision table (what `build_deck` computes per slot)

Let `observed = run[s] or repl[s]` (run wins), `declared = declared[s]`, `busy` = service
BUSY or an active run.

| observed | declared | kinds agree? | `slot_state` | `source` | notes |
|---|---|---|---|---|---|
| — | — | — | `empty` | `empty` | `labware=null` |
| — | ✓ | — | `declared` | `declared` | intent only; not yet on the robot |
| ✓ | — | — | `in_use` if `busy` else `occupied` | run/repl | |
| ✓ | ✓ | yes | `in_use` if `busy` else `occupied` | run/repl | declared confirmed |
| ✓ | ✓ | **no** | `mismatch` | run/repl | `labware`=observed, `declared`=the conflicting intent |

Deck-level `source` = the highest-precedence source that contributed any slot
(run → repl → declared → empty).

### 2.5 Unifying the two orphan stores (no parallel structures)

- **PlateStateStore stays the single well-truth store.** `build_deck` *reads* it and
  attaches `plate_id`+`wells` onto the slot the plate occupies. The slot is resolved by
  matching `loaded_plate.plate_id` against `session_recipe.labware[*].nickname` →
  `location`. (organic-solubility sends `plate_id="D"` and `/control/setup` maps `"D"`→
  slot `"2"`.) If no match, the plate is "unplaced" — still emitted in
  `details.loaded_plate`, just not attached to a slot. **Documented heuristic.**
- **`details.loaded_plate` is retained verbatim** for back-compat (organic-solubility
  reads it directly). It is now *derived from the same store* the deck slot reads, so
  there is no hand-maintained duplication. Mark it deprecated-in-favour-of-deck-slots
  in the README; remove only after the workflow + dashboard migrate.
- **`last_snapshot` stays** as the internal raw REPL cache; it feeds the `repl` source.

---

## 3. Wiring into the running service (`service.py`, `api.py`)

**Hard constraint: `/status` stays side-effect-free and fast.** `build_deck` runs inline
in `get_status()` but *only over cached inputs*. No HTTP/REPL read happens in the status
path.

- Add `self.decks = DeckDeclarationStore(state_path=OT2_DECK_STATE_PATH)` (JSON-atomic,
  mirrors `PlateStateStore`: load-on-init, persist-on-mutate, corrupt-file-tolerant).
- Add a **TTL-cached run-labware probe** `_last_run_labware` refreshed by the same
  best-effort HTTP path as `probe_robot()` (extend it to also GET the active run's
  labware), never called from `get_status`. Refresh points: `boot_reconnect`, and
  wherever `refresh_snapshot()` is already called (after control actions).
- `get_status()`: build `details.snapshot.deck = build_deck(run=self._last_run_labware,
  repl=self.last_snapshot["deck"], declared=merge(session_recipe, self.decks.get()),
  loaded_plate=self.plates.get(), nickname_to_slot=<from session_recipe>,
  busy=<state in {BUSY, EXTERNAL_CONTROL} or run_active>, now=...)`. Keep emitting
  `details.loaded_plate`.
- `allowed_actions()`: add `deck.declare` as a convenience action (available whenever
  reachable, like `lights.set` — works in `requires_init`, matching the stopgap which
  works with no session).
- New service methods `declare_deck(slots)` / `clear_deck()` delegating to `self.decks`.

### New endpoints (declared layout — control-class, claim-gated, dashboard-audited)

- `POST /control/deck/declare` — body accepts per-slot declarations, either
  `{"slots": {"2": {"load_name": "corning_96_wellplate_360ul_flat"}, ...}}` (preferred,
  full fidelity) **or** the legacy `{"slots": {"2": "96-well"}}` kind-string shape
  (drop-in compat with the dashboard picker). `null` / omitted slot clears it; empty
  `{}` clears all. Validates slot ∈ 1..12. **POST, not PUT** — so it routes through the
  dashboard's `/control/*` passthrough (POST/GET/DELETE only) and matches the SDK
  skill-catalog method set.
- `DELETE /control/deck/declare` — clear all (or reuse empty POST).
- Both claim-gated via the existing `require_claim` dependency, so when proxied through
  the dashboard's `/control/*` passthrough they inherit the `control_action` audit +
  `operator+` auth automatically — matching the stopgap's behaviour.

---

## 4. Fixtures & tests

### Fixtures (`tests/fixtures/`)
- Update `status_dry_run.json`, `status_lights_on.json`, `status_requires_init.json`
  `details.snapshot.deck` to the new normalized empty-deck shape (all 12 slots `empty`).
- Add `status_deck_declared.json` (requires_init + declared layout → `slot_state:declared`,
  deck `source:declared`).
- Add `status_deck_occupied.json` (ready + REPL-observed plate on a slot, with `wells`
  attached from the plate store → `occupied`).
- Add `status_deck_in_use.json` (busy → `in_use` on observed slots).
- Add `status_deck_mismatch.json` (declared 96-well vs observed 24-well → `mismatch`,
  `declared` sub-field populated).
- Raw-input fixtures: `repl_get_all_states.json` (a `get_deck_state()` output) and
  `robot_run_labware.json` (a robot-server run doc) to drive the normalizer tests.

### Unit tests (`tests/unit/test_deck.py`, new)
- `classify_labware` parametrized: corning_96_wellplate, opentrons_96_tiprack,
  nest_12_reservoir, opentrons_24_tuberack, opentrons_1_trash, an unknown load_name,
  and the 12-count ambiguity (reservoir vs wellplate).
- `normalize_repl_slots` / `normalize_run_slots` from the raw fixtures.
- `build_deck` — one test per row of the §2.4 decision table (empty, declared-only,
  occupied, in_use-when-busy, mismatch, run>repl precedence).
- `DeckDeclarationStore`: load/persist/clear, reload-on-restart, corrupt-file-ignored
  (mirror `test_plate_state.py`).
- Plate→slot attachment via `session_recipe` nickname→location; unplaced-plate fallback.
- `get_status` wiring: asserts `details.snapshot.deck` shape **and** that building it
  issues no `requests`/REPL calls (monkeypatch `requests.get`/`.post` to raise — proves
  side-effect-free).
- Endpoint tests (`test_deck_endpoints`): `/control/deck/declare` 423 without token,
  dry-run round-trip, legacy kind-string compat, clear.

---

## 5. Phased sequence

- **Phase 0 — model + normalizers + tests, NO wiring.** New `gateway/deck.py` (pure
  functions + `DeckDeclarationStore`) and `models.py` additions; full `test_deck.py`;
  raw-input fixtures. `build_deck` fully exercised with hand-authored dicts. No
  `service.py`/`api.py` change. *This satisfies the "build the state model first"
  mandate and is independently reviewable/mergeable.*
- **Phase 1 — observed wiring.** Feed `repl` (from existing `last_snapshot`) and a new
  TTL-cached `run` probe into `get_status().details.snapshot.deck`; attach `loaded_plate`
  wells to the slot. Update the three existing fixtures; add occupied/in_use fixtures.
  Assert side-effect-free.
- **Phase 2 — declared layout + stopgap-retire enablement.** Wire `DeckDeclarationStore`
  + `session_recipe` as the `declared` source; `/control/deck/declare` endpoint;
  mismatch detection; `deck.declare` in `allowed_actions`. Add declared/mismatch fixtures.
- **Phase 3 — cross-repo coordination (separate PRs in `ac-organic-lab`, noted here, not
  done in this repo).**
  1. Add `deck.declare` (and eventually the protocol-exec) SkillDefs to
     `skills/src/lab_skills/skill_catalog/liquid_handler.py`.
  2. Repoint `LiquidHandlerTile.tsx` to read `status.details.snapshot.deck.slots` and
     extend `LABWARE_TYPES` to the richer kind enum (render off `rows`/`columns`).
  3. Retire `api/app/deck.py` + `deck_layouts.json`; migrate its two entries (`ot2`,
     `ot2_complexation`, kind-strings) into the gateway's declared store on cutover.
  4. Update `EQUIP_STATUS.md` §11.

---

## 6. Decisions needing a nod before Phase 0

1. **Declared-layout in scope from day one (Phase 2), observed-first sequencing** — my
   recommendation above. Confirm.
2. **`kind` enum breadth** — the richer enum in §2.1 vs. staying at the tile's
   `{96-well,24-well,waste}`. I recommend the richer enum (tile renders generically off
   rows/columns anyway).
3. **`details.loaded_plate` retained (deprecated) vs. removed now** — I recommend
   retain-and-deprecate to avoid breaking organic-solubility; remove after it migrates.
4. **Declared endpoint accepts both load_name and legacy kind-string** — I recommend
   yes, for a low-friction dashboard cutover.
5. **12-count grid ambiguity** resolution by category token (§2.3) — confirm the rule.
