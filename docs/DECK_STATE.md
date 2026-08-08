# OT-2 Deck / Labware State — feature documentation

**Status:** shipped and deployed. Phases 0–2 landed in this repo (commit
`ede0a85`); the dashboard side (tile reading the device deck, `deck.declare`
SkillDef) shipped in `ac-organic-lab`. Both gateways (`ot2_hte` :8020,
`ot2_complexation` :8021) deployed 2026-07-10 and verified live; the old
dashboard stopgap layouts were migrated into the gateways' declared stores.
HTTP-transport deck parity (including idle-persistence across restart) was
hardware-validated 2026-07-14 — see
[`HTTP_DRIVE_VALIDATION.md`](HTTP_DRIVE_VALIDATION.md). Tip-rack registration
was rekeyed onto the deck slot 2026-08-07 (`4a7faf3`) and extended to gateway
boot (`6c46e57`); both gateways redeployed and verified live the same day.

> This document replaces the retired **`DECK_STATE_PLAN.md`** (the phased
> implementation plan, removed 2026-07-19 once its tasks completed; full
> history in git). It keeps the plan's durable final-state material: the
> model, the merge semantics, and the contract with the dashboard and
> workflow repos.

**What it is:** a normalized deck/labware state layer in the gateway
(`gateway/deck.py` + models in `gateway/models.py`) that unifies the
previously-orphaned state stores and publishes `details.snapshot.deck` on
`/status` — the single deck picture the dashboard tile and the full-page OT-2
control interface render.

---

## The model

### Labware kind enum

Derived from the Opentrons `load_name` (which is highly structured:
`<brand>_<count>_<category>_<...>`), never hand-set:

```python
LabwareKind = Literal[
    "96-well", "384-well", "48-well", "24-well", "12-well", "6-well",
    "well_plate",   # a wellPlate whose grid isn't one of the named sizes
    "tiprack", "reservoir", "tuberack", "trash", "adapter", "unknown",
]
```

`classify_labware(load_name, ...)` returns `(kind, rows, columns)`; the
12-count ambiguity (12-reservoir = 1×12 vs 12-wellplate = 3×4) is resolved by
the category token. The dashboard renders generically off `rows`/`columns`;
`kind` only drives label/icon/greying.

### Slot / deck models

```python
class SlotLabware(BaseModel):
    kind: LabwareKind
    load_name: str
    display_name: Optional[str] = None
    is_tiprack: bool = False
    rows: Optional[int] = None
    columns: Optional[int] = None
    # present only for the tracked plate (from PlateStateStore); None otherwise
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
    declared: Optional[SlotLabware] = None   # populated only on mismatch

class DeckState(BaseModel):
    source: DeckSource            # highest-precedence source contributing any slot
    slots: Dict[str, DeckSlot]    # "1".."12", always all 12 keys present
    timestamp: datetime
```

## Sources and merge semantics

`build_deck(...)` is a pure function (no hardware, no I/O) merging four
sources with precedence **run > repl > declared > empty**:

- **run** — the robot-server run's loaded labware (the active external run
  probe on SSH; the gateway's own never-played run on the HTTP transport —
  see [`HTTP_TRANSPORT.md`](HTTP_TRANSPORT.md)). TTL-cached; never fetched
  from the `/status` handler.
- **repl** — the SSH REPL snapshot (`state_readers.get_all_states`), present
  only while the gateway owns a REPL session. Empty on the HTTP transport.
- **declared** — operator/recipe intent: the persisted
  `DeckDeclarationStore` (`OT2_DECK_STATE_PATH`) merged with the
  `session_recipe` from `/control/setup`.
- **empty** — the fallback.

**Only `declared` survives a restart.** `run` and `repl` are session-scoped —
the run probe's cache and the REPL snapshot both die with the process — so a
deck that was *only ever observed* comes back `empty` after a service restart
and stays that way until the next protocol loads labware. Declaring is what
makes a layout durable, which matters most on a robot whose deck is set up by
hand rather than by a `/control/setup` recipe. Observed live 2026-08-07:
restarting `ot2_hte` blanked a deck that had only ever been observed, while
`ot2_complexation`'s declared deck came back intact. Note the direction of the
precedence — declaring can never override what a run observes, so declaring a
hand-set deck is safe even while protocols come and go.

### Slot-state decision table

Let `observed = run[s] or repl[s]` (run wins), `declared = declared[s]`,
`busy` = service BUSY or an active run:

| observed | declared | kinds agree? | `slot_state` | `source` |
|---|---|---|---|---|
| — | — | — | `empty` | `empty` |
| — | ✓ | — | `declared` | `declared` |
| ✓ | — | — | `in_use` if busy else `occupied` | run/repl |
| ✓ | ✓ | yes | `in_use` if busy else `occupied` | run/repl |
| ✓ | ✓ | **no** | `mismatch` (`labware`=observed, `declared`=the losing intent) | run/repl |

This declared-vs-observed reconciliation is the contract the workflow repos
asked for: declaring records *intent* (pure metadata, no hardware); the robot
observation wins on conflict, and the disagreement is flagged per slot rather
than silently resolved.

## Store unification (no parallel structures)

- **`PlateStateStore` is the single well-truth store.** `build_deck` reads it
  and attaches `plate_id` + `wells` onto the slot the tracked plate occupies
  (resolved by matching `plate_id` against `session_recipe` nicknames →
  location; an unmatched plate stays "unplaced" and is still emitted in
  `details.loaded_plate`).
- **`details.loaded_plate` is retained verbatim** for back-compat (the
  solubility workflow reads it directly) but is now derived from the same
  store the deck slot reads — deprecated in favour of the deck slots once
  consumers migrate.
- **`last_snapshot`** stays as the internal raw snapshot cache feeding the
  `repl` source (SSH) or the run-derived shape (HTTP — see the snapshot-shape
  section of [`HTTP_SSH_PARITY.md`](HTTP_SSH_PARITY.md)).

### Tip-rack registration follows the deck

`TipStateStore` is keyed by **deck slot**, not by a recipe nickname: a tip rack
carries no sample and no history worth naming, and what an operator points at —
and refills — is "the rack in slot 4" (`4a7faf3`). Registration happens at
three points, all non-destructive (a slot already tracked keeps its used-tip
statuses):

| When | Why |
|---|---|
| `/control/setup` | the recipe places tip racks |
| `/control/deck/declare` | declaring a rack *is* the fact the tracker needs — before this, a declared rack was invisible to the tracker |
| gateway boot (`OT2Service.__init__`) | the persisted deck outlives the process; without it a rack declared in an earlier session came back untracked (`6c46e57`) |

**Registration asserts a *full* rack.** `_fresh_rack` marks every well `new`,
so a count that came from registration alone is an assumption — "a rack
appeared on this slot, presume it is untouched" — not an observation. Three
things corroborate a count, in descending strength: pick/drop events (evidence
the gateway itself recorded), an operator `POST /control/tips/reset` (a human
asserting a physical refill or swap), and nothing at all. Treat a rack with no
history and no reset as unverified, however confident its `96/96` looks; the
device cannot see tips and will never correct itself. Registration is
deliberately non-destructive for the same reason — re-declaring or restarting
must never silently "refill" a rack that has been used.

Boot registration reads `_build_deck_state`, which is cache-only — no HTTP, no
REPL — so it cannot block or slow startup. It follows the *declared* deck by
construction, so a robot with no declared layout registers nothing at boot and
falls back to whatever the tip store itself persisted. That asymmetry is worth
knowing: the tip store and the deck can disagree after a restart, with the
store asserting racks the deck no longer shows.

The store and the deck are joined on the slot, which is why the join always
resolves — `labware.nickname` is `null` on every slot until a setup runs, and
keying on it was why a tracked rack could still render as untracked.

## Declared-layout endpoints

Control-class, claim-gated, audited when proxied through the dashboard's
`/control/*` passthrough:

- `POST /control/deck/declare` — body `{"slots": {"2": "<load_name>" |
  {"load_name"|"kind": ...} | null}}`; accepts full load-names (preferred) or
  the legacy kind-string shape; `null`/omitted clears a slot, empty `{}`
  clears all. POST (not PUT) so it flows through the dashboard passthrough
  and matches the SDK skill-catalog method set.
- `DELETE /control/deck/declare` — clear all.
- `deck.declare` is advertised in `allowed_actions` in every state except
  `EXTERNAL_CONTROL` (pure metadata, like `lights.set`).

**Hard constraint kept:** `GET /status` stays side-effect-free and fast —
`build_deck` runs inline over cached inputs only; no HTTP or REPL read ever
happens in the status path (enforced by test).

## Cross-repo state

- **Dashboard** (`ac-organic-lab`): `LiquidHandlerTile` and the full-page
  OT-2 control interface render `details.snapshot.deck` (declared and
  observed shown separately, mismatches badged ≠). The tile keeps a
  read-only legacy fallback to the old dashboard store for gateways that
  don't publish a deck yet; final deletion of the `api/app/deck.py` stopgap
  is tracked in that repo (`EQUIP_STATUS.md` §11). **That legacy store is
  stale and must not be used to seed a declaration** — for `ot2_hte` it still
  claims slot 7 is a 24-well plate, where the robot reports a 1000 µL tip
  rack.

**Deck view conventions.** `DeckPanel` exists in two copies — this repo's
`/ui` and the dashboard's tile — kept deliberately in step, so a change to one
is ported to the other (`6c46e57` here, `6b95e86` / `a35ad41` there):

- slot number in the cell's top-left corner, as bare text — a pill background
  there reads as an overlay covering A1;
- a `declared` slot carries a solid orange border, named **once** by a legend
  above the deck rather than by a badge on every slot (on a typical deck most
  slots are declared);
- `busy` / `≠` badges in the cell's top-right corner, in both variants;
- tip-rack wells tinted green (fresh) / amber (touched) / grey. Grey covers
  both an emptied well *and* an untracked rack: "no tip" and "no idea" are
  both "do not count on it", and the tooltip carries the distinction. Green
  therefore means "known available", so a rack with no green is either spent
  or unregistered — both worth a closer look.
- The declared outline and legend are `page`-variant only. On the compact
  tile nearly every slot is declared, so outlining them all would say nothing
  while shouting.
- **Workflow repos** consume `details.loaded_plate` (unchanged contract) and
  gain the per-slot deck + mismatch flags.
- **xArm** never reports placements — the OT-2 owns its own loaded truth;
  plate handoffs are recorded by the gateway per the handoff spec in
  [`HTTP_TRANSPORT.md`](HTTP_TRANSPORT.md).

## Implementation map

| Piece | Where |
|---|---|
| Pure normalizers + `build_deck` | `gateway/deck.py` |
| Models (`SlotLabware`, `DeckSlot`, `DeckState`, …) | `gateway/models.py` |
| Declaration store (JSON-atomic, corrupt-tolerant) | `gateway/deck.py::DeckDeclarationStore` |
| Wiring (`_build_deck_state`, TTL run probe, endpoints) | `gateway/service.py`, `gateway/api.py` |
| Tests (decision table, normalizers, store, side-effect-free status) | `tests/unit/test_deck.py`, `test_deck_declare.py`, `test_deck_status.py` |
| Fixtures | `tests/fixtures/status_deck_*.json`, `repl_get_all_states.json`, `robot_run_labware.json` |
