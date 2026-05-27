# Branch B': Lens Catalog Rebuild — Specification

**Status:** DRAFT (awaiting owner sign-off on the design questions in §8 before B'1 implementation starts)
**Created:** 2026-05-27
**Source:** Council deliberation Cycle 2, joint finding #6 (Stock-Integrity) + #9 (Stock-Integrity × Frontend-UX)
**Targets:** ~1 week, split into three sub-PRs (B'1 / B'2 / B'3)
**Depends on:** Branch B (PR #267) — merged

---

## 1. Problem

The Power Grid (`/inventory/power-grid`) is data-model-wrong for the way an Indian optician actually keeps lens stock. After Branch B (PR #267) fixed the silent `stock` vs `stock_units` collection split-brain, the grid CAN now read real on-hand data — but the underlying schema makes it useless for the owner's actual question:

> "Do I have any **-2.00 SPH / -1.00 CYL** with **anti-blue + green coat** at **Bokaro**?"

The current model can't answer that because:

- **Lens products carry only `sph`, `cyl`, `brand`, `model`** (verified: `backend/api/routers/inventory.py:1490-1495`). There are NO first-class columns for `coating` (anti-blue / green / blue / dual), `index` (1.5 / 1.56 / 1.6 / 1.67 / 1.74), `material` (CR-39 / poly / MR-8 / MR-174 / Trivex), or `lens_type` (single-vision / bifocal / progressive).
- **Lens stock is modelled as serialized units** (one Mongo doc per physical piece in `stock_units`). That works for frames and CL boxes — opticians don't serialize stock lenses, they count per (SPH, CYL) cell in a tray.
- **The grid aggregates only by (sph, cyl)** — so 25 SKUs of the same power (5 coatings × 5 indices) collapse into one cell with no way to differentiate.
- A modelling workaround (one product per (brand, series, coating, index, SPH, CYL) combo) would create **~240,000 product docs** for a typical 10-brand catalog. Mongo would survive it; the UX wouldn't.

The owner's stated stocking reality (from the 2026-05-27 session):

> "Mix of Both [stock + per-Rx ordered] plus various Brand and options like anti blue light, green coat, blue coat, dual coat etc."

---

## 2. Target data model

Two new collections sitting alongside (not replacing) `products` and `stock_units`. Frames + CL continue to use the serialized model; lenses get their own typed catalog and quantity-tracked stock.

### 2.1 `lens_catalog` — one row per "lens line"

```python
{
    lens_line_id: str,         # primary key, slug e.g. "essilor-crizal-forte-uv-1p6-sv"
    brand: str,                # "Essilor" / "Zeiss" / "Kodak" / "Nikon" / ...
    series: str,               # "Crizal Forte UV" / "DriveSafe" / "Pure Reflex" / ...

    # Typed lens dimensions (the council's HIGH finding #6)
    index: float,              # 1.50 | 1.56 | 1.60 | 1.67 | 1.74
    material: str,             # "CR39" | "POLY" | "MR8" | "MR174" | "TRIVEX" | "GLASS" | "OTHER"
    lens_type: str,            # "SV" (single-vision) | "BIFOCAL" | "PROGRESSIVE" | "OFFICE" | "READING"
    coating: list[str],        # ["ANTI_BLUE", "GREEN_COAT", "DUAL_COAT", "HC", "AR", "PHOTOCHROMIC", "POLARIZED", "UV"]
                               # ARRAY to support combos ("dual coat" = anti-blue + green)

    # Power range this LINE supports (used to validate stock entries)
    sph_range: {"min": -8.0, "max": 6.0, "step": 0.25},
    cyl_range: {"min": -4.0, "max": 0.0, "step": 0.25},
    has_add: bool,             # bifocal / progressive
    add_range: {"min": 0.75, "max": 3.50, "step": 0.25} | None,

    # Pricing (lens lines are often power-banded — same brand, different price by power)
    mrp: float,                # default MRP
    cost_price: float,         # default cost
    mrp_table: list[{          # optional power-banded pricing
        sph_min: float, sph_max: float, cyl_min: float, cyl_max: float,
        mrp: float, cost_price: float
    }] | None,

    # GST identity
    gst_rate: float = 5.0,     # corrective optical = 5% per HSN 9001 (per gst_rates.py)
    hsn_code: str = "9001",

    # Lifecycle
    is_active: bool = True,
    notes: str | None,
    created_at, updated_at, created_by
}
```

Indexes: `(brand, series, index, material, lens_type)` compound (for the filter strip); `is_active` standalone.

### 2.2 `lens_stock_lines` — one row per (lens_line × store × power cell)

```python
{
    line_stock_id: str,        # primary key (auto, ObjectId)
    lens_line_id: str,         # FK -> lens_catalog
    store_id: str,             # store-scoped (geo-fenced)

    # Power cell (the (sph, cyl[, add]) tuple)
    sph: float,
    cyl: float,                # 0 for spherical-only
    add: float | None,         # bifocal/progressive only; None for SV

    # Quantity (NOT serialized — lenses are tray-counted)
    on_hand: int,              # current stock
    reserved: int = 0,         # held by open orders / workshop WIP
    reorder_point: int = 0,    # gap planner threshold
    safety_stock: int = 0,     # buffer above reorder_point

    # Audit
    last_counted_at: datetime | None,
    last_counted_by: str | None,
    last_movement_at: datetime
}
```

Indexes: `(lens_line_id, store_id, sph, cyl, add)` UNIQUE (one row per cell per store); `(store_id, on_hand)` for store-level low-stock queries.

### 2.3 Why this is right

- **Filters become trivial**: `lens_catalog.find({brand: "Essilor", coating: "ANTI_BLUE", index: 1.6, lens_type: "SV"})` returns the lens lines, then `lens_stock_lines.aggregate([{$match: {lens_line_id: {$in: [...]}, store_id: "BV-BOK-01"}}, {$group: {_id: {sph, cyl}, total: {$sum: "$on_hand"}}}])` builds the grid.
- **Per-power qty entry**: the owner pastes/types qtys into a 57×17 grid per (lens_line, store) — one matrix per "Essilor Crizal Forte 1.6 SV" per Bokaro store.
- **Reorder/gap planner**: one Mongo query `lens_stock_lines.find({store_id, $expr: {$lt: ["$on_hand", "$reorder_point"]}})`.
- **Catalog cardinality stays sane**: ~50-100 lens lines per brand × 5-10 brands ≈ 500-1000 `lens_catalog` docs (not 240,000 product docs).
- **Stock cardinality**: ~970 cells per (lens_line × store) at full SPH×CYL coverage, but realistically owner stocks only the middle 30-40% of the matrix per line ≈ ~300 cells × 1000 lines × 6 stores ≈ 1.8M `lens_stock_lines` docs at full saturation. Mongo handles it.

---

## 3. API surface

### 3.1 lens_catalog (CRUD + search)

| Method | Path | Purpose | Roles |
|---|---|---|---|
| GET | `/api/v1/lens-catalog?brand=X&coating=Y&index=1.6&lens_type=SV&active=true&q=...&limit=50` | list/search (filter strip backing) | all |
| GET | `/api/v1/lens-catalog/{lens_line_id}` | detail | all |
| POST | `/api/v1/lens-catalog` | create | SUPERADMIN / ADMIN / CATALOG_MANAGER |
| PATCH | `/api/v1/lens-catalog/{lens_line_id}` | update (metadata, pricing, ranges) | SUPERADMIN / ADMIN / CATALOG_MANAGER |
| DELETE | `/api/v1/lens-catalog/{lens_line_id}` | soft-delete (set is_active=false) | SUPERADMIN / ADMIN |
| GET | `/api/v1/lens-catalog/meta/options` | dropdown values (brand list, coating enum, etc.) | all |

### 3.2 lens_stock_lines (per-power qty)

| Method | Path | Purpose | Roles |
|---|---|---|---|
| GET | `/api/v1/lens-stock/{lens_line_id}?store_id=...` | full power matrix for one lens line at one store | store-scoped read |
| POST | `/api/v1/lens-stock/{lens_line_id}/bulk` | paste/upload qty matrix (CSV or 2D JSON) | CATALOG_MANAGER / STORE_MANAGER / ADMIN / SUPERADMIN |
| PATCH | `/api/v1/lens-stock/{line_stock_id}` | adjust a single cell (±delta or absolute) | CATALOG_MANAGER / STORE_MANAGER / ADMIN / SUPERADMIN |
| POST | `/api/v1/lens-stock/{lens_line_id}/sell` | decrement on_hand for a cell (called from POS) | system / internal |
| POST | `/api/v1/lens-stock/{lens_line_id}/restock` | increment on_hand for a cell (called from GRN accept) | system / internal |
| GET | `/api/v1/lens-stock/audit/{line_stock_id}` | adjustment history | SUPERADMIN / ADMIN / STORE_MANAGER |

Every write emits an audit row to a `lens_stock_audit` collection: `{line_stock_id, prior_qty, new_qty, delta, source, source_id, by_user, at}`. Mirrors the `stock_audit` rows from Branch B.

### 3.3 power-grid v2

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/inventory/lenses/power-grid?brand=&coating=&index=&material=&lens_type=&store_id=` | SPH × CYL on-hand grid, filtered |
| GET | `/api/v1/inventory/lenses/power-grid/cell?sph=X&cyl=Y&[filters]&store_id=` | drill-in: list of (lens_line, on_hand, reorder_point) entries in that cell |
| GET | `/api/v1/inventory/lenses/gap-planner?store_id=` | cells where `on_hand < reorder_point`, sorted by velocity (joins to recent order history) |

The OLD power-grid endpoint at the same path stays during B'2; B'3 retires the products-based path and the new lens_catalog-based path becomes the only one.

---

## 4. Frontend redesign

### `frontend/src/pages/inventory/PowerGridPage.tsx`

**Above the grid — filter strip** (replaces the lone store dropdown):
```
[ Brand ▼ ]  [ Coating ▼ multi ]  [ Index ▼ ]  [ Lens type ▼ ]  [ Material ▼ ]  [ Store ▼ ]  [ Refresh ]
```
Default: no filters; show all. Empty filter = treat as wildcard.

**Grid cell — clickable** (today it's `title=` tooltip only):
- Click a cell → opens a side drawer (reuse `VendorLedgerDrawer` pattern from CashFlow page)
- Drawer lists every `lens_line` with stock in that cell: SKU, lens line name, on_hand, reserved, reorder_point, last_counted_at
- Per-row actions: **Adjust qty** (inline +/− with optional reason) · **View history** (open `/lens-stock/audit/{id}` log)

**New "Manage stock" mode toggle** (CATALOG_MANAGER / STORE_MANAGER / ADMIN / SUPERADMIN):
- Pick a `lens_line` from a search dropdown
- Replaces the heatmap with editable inputs per cell (57×17 grid)
- Supports **paste a CSV/matrix from Excel** — the owner's primary workflow
- "Save matrix" button posts to `/lens-stock/{lens_line_id}/bulk`

**New "Gap planner" tab**:
- List of cells where `on_hand < reorder_point`, grouped by lens_line
- Each row: lens line, store, (sph, cyl[, add]), on_hand vs reorder_point, last 30-day velocity
- Actions: **Create PO** (pre-fills purchase router) · **Mark counted** (sets last_counted_at=now)

---

## 5. Migration from existing products

**Strategy:** keep the existing products with `category in lens_cats` untouched as historical data. The new `lens_catalog` + `lens_stock_lines` becomes the parallel typed source going forward. No destructive migration.

**Migration script** (optional, owner-triggered):

```bash
"C:/Users/avina/IMS 2.0 CLAUDE COWORK/ims-2.0-railway/.venv/Scripts/python.exe" \
  -m backend.scripts.migrate_lens_products_to_catalog --dry-run
```

For each existing lens product (category ∈ {LS, OPTICAL_LENS, LENS, ...}) with `sph` and `cyl` set:
1. Look up or create a `lens_catalog` row (group by `brand` + `model` → infer `series`; default `index=1.5`, `material="CR39"`, `lens_type="SV"`, `coating=[]`)
2. Read serialized `stock_units` count for that product per store
3. Create a `lens_stock_lines` row per (store, sph, cyl) with `on_hand` = unit count

The owner reviews + corrects the typed dimensions (coating, index, material) in the catalog UI after the dry-run. Then re-run without `--dry-run`.

**Existing products stay in the catalog** for backwards compatibility with orders that reference them. POS lens-sale path migrates in B'3 (point at `lens_stock_lines` instead of `stock_units`).

---

## 6. Sub-PR breakdown

| Sub-PR | Scope | Time | Files | Tests |
|---|---|---|---|---|
| **B'1** | `lens_catalog` + `lens_stock_lines` schemas (schemas.py), indexes, validators, CRUD routers, meta/options endpoint. No FE changes. No power-grid changes. | ~2 days | new `backend/api/routers/lens_catalog.py` + `lens_stock.py`; schemas.py; tests. | ~25-30 |
| **B'2** | power-grid v2 endpoint, drill-in endpoint, gap-planner endpoint, FE filter strip + drill-in side drawer. Old endpoint stays as fallback (same URL serves both based on a feature flag or query param). | ~2 days | new endpoints; new `frontend/src/pages/inventory/PowerGridPage.tsx` filter strip + drawer; service module. | ~20 |
| **B'3** | FE Manage-stock mode (editable matrix + paste support), bulk endpoint, gap-planner tab, migration script, POS sell-path migration, retire old power-grid endpoint. | ~2 days | FE rewrite; migration script; orders.py lens-sell hook. | ~15 |

Each sub-PR opens, runs CI, merges independently. B'2 depends on B'1 schemas. B'3 depends on B'2 endpoints.

---

## 7. Test plan (per sub-PR)

- Schema validators: enum boundaries (index must be in {1.5, 1.56, 1.6, 1.67, 1.74}), coating array non-empty if non-clear, sph_range valid
- CRUD endpoint role gates (try as CASHIER → 403)
- Store-scoping (STORE_MANAGER for Bokaro cannot read Pune `lens_stock_lines`)
- Idempotent stock-add / stock-sell (same source_id twice → no double effect, mirrors restock_applied guard from PR #265)
- Power-grid v2 returns same shape as v1 (so the FE can switch seamlessly)
- Drill-in returns the right lens_lines per cell
- Gap-planner ranks by velocity correctly
- Cross-layer integration test against mongo:7.0 (the fixture pattern from Branch B's `test_stock_integration.py`)

---

## 8. OPEN DESIGN QUESTIONS — need owner sign-off before B'1 starts

### Q1 — Coating model
- **Option A (Recommended): `coating: list[str]` array.** Owner stocks combos: "dual coat" = `["ANTI_BLUE", "GREEN_COAT"]`. Filters use `$in` / `$all`. One lens_catalog row per (brand, series, index, material, lens_type) regardless of coating count.
- **Option B:** Single `coating: str` enum with "DUAL_COAT" as its own code. Simpler but loses combinatorial generality — what if next year there's a "triple coat"?
- **Option C:** Separate lens_catalog row per coating combo. Cardinality explosion.

### Q2 — `ADD` axis for bifocal / progressive
- **Option A (Recommended):** Add ADD as a 3rd axis on `lens_stock_lines` (`sph`, `cyl`, `add`). The Power Grid for bifocal/progressive lines renders a separate matrix per `add` value (or a 3D selector). For SV lens lines, `add` is `None`.
- **Option B:** Don't store ADD; pretend bifocals are SV. Wrong — bifocal stock IS distinct by ADD.

### Q3 — Migration of existing products
- **Option A:** Owner-triggered dry-run script (recommended). Owner reviews the inferred dimensions, corrects, then commits. Safe.
- **Option B:** Auto-migrate in B'3 deployment. Faster but the inferred coating/index/material would be wrong for almost everything (no source data to infer from).
- **Option C:** No migration. Owner re-enters everything in the new UI. Most accurate but most labour.

### Q4 — POS lens-sell path
- **Option A (Recommended):** Order-create endpoint calls `/lens-stock/{lens_line_id}/sell` for each lens line on the order. Mirrors Branch B's `mark_sold` pattern.
- **Option B:** Workshop dispatch path (when the lens is actually cut + fitted). More accurate to physical stock movement, but delays the on-hand decrement.
- **Option C:** Both — soft-reserve at POS, hard-commit at workshop. Most accurate but most complex.

### Q5 — Coating vocabulary
What coating codes does the owner actually stock? Initial proposal based on the session input:
- `ANTI_BLUE` (anti blue light)
- `GREEN_COAT`
- `BLUE_COAT`
- `DUAL_COAT` (when stocked as a combo SKU rather than tracked as an array — keep for backward compat)
- `HC` (hard coat)
- `AR` (anti-reflective)
- `PHOTOCHROMIC` (transitions)
- `POLARIZED`
- `UV` (UV-only)
- `MIRROR` (mirror coatings)

Does this cover everything? Anything missing or to remove?

### Q6 — Indexes and materials
**Indexes** (Indian optical market): `1.50`, `1.56`, `1.60`, `1.67`, `1.74`. Add `1.53` (Trivex)?
**Materials**: `CR39`, `POLY`, `MR8`, `MR174`, `TRIVEX`, `GLASS`. Anything to add / remove?

---

## 9. Acceptance criteria

- `/inventory/power-grid?brand=Essilor&coating=ANTI_BLUE&store_id=BV-BOK-01` shows ONLY Essilor anti-blue cells with real quantities at Bokaro.
- Owner can paste a 57×17 SPH × CYL matrix from Excel into the Manage-stock UI and see all cells populate in under 2 seconds.
- Clicking a populated cell opens a drawer listing the underlying lens_lines + on_hand + reorder_point + last-counted-at.
- Gap planner lists every cell with `on_hand < reorder_point` for the current store, sorted by 30-day sales velocity.
- POS sale of a (lens_line, sph, cyl, add, store) decrements `lens_stock_lines.on_hand` by 1 atomically; the audit row records the order_id.
- A re-run of the migration script with the same input is idempotent (no duplicate lens_catalog rows).
- Existing `/inventory/power-grid` URL keeps working through B'2; only retired in B'3 once feature parity is verified live.

---

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Cardinality blow-up on `lens_stock_lines` | Indexed correctly; sparse — owner won't fill the full 57×17 matrix for every line × store. Confirmed bounded ~1.8M docs at full saturation. |
| Migration script gets dimensions wrong | Dry-run mode + owner review before commit; coating/material default to "OTHER" + flag for review. |
| POS lens-sell path mismatch (sold at POS but lens delivered later) | Use `reserved` field at POS, decrement `on_hand` at workshop dispatch. Tracks reality of optical workflow. |
| Owner doesn't actually use the "Manage stock" paste UI | Keep `PATCH /lens-stock/{id}` per-cell endpoint for staff who want to adjust one cell at a time. Both paths supported. |
| Old products + new lens_catalog drift over time | The migration is one-way (products → lens_catalog), and B'3 retires the products-based power-grid path. Old products linger only as historical FK targets for past orders. |

---

## 11. Out of scope for B' (parking lot)

- **Contact-lens grid**: separate spec; CL keeps the serialized `stock_units` + batch/expiry model from #75. The CL power × base-curve grid stays as-is.
- **Lens fitting / cutting workflow**: workshop module already covers this; B' only changes the stock decrement path.
- **Frame catalog**: out of scope; frames stay on the existing `products` + serialized `stock_units` model.
- **Vendor procurement integration**: a future Branch links `lens_stock_lines.reorder_point` to auto-PO via TASKMASTER. Not in B'.

---

## 12. Acceptance + sign-off

Before B'1 starts, please confirm:
- [ ] Q1: `coating: list[str]` array (Option A)
- [ ] Q2: ADD as a 3rd axis on lens_stock_lines (Option A)
- [ ] Q3: Owner-triggered dry-run migration script (Option A)
- [ ] Q4: POS order-create calls `/lens-stock/.../sell` (Option A)
- [ ] Q5: Coating vocabulary covers your reality (or add/remove codes)
- [ ] Q6: Index + material enums (or add/remove)

Once these are signed off, the spec is frozen and B'1 implementation can start (background agent, ~2 days).
