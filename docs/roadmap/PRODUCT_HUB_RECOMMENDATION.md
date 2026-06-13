# Product Hub + Catalog Consolidation — Council Recommendation (AWAITING OWNER APPROVAL)

> **Status: AWAITING OWNER APPROVAL — nothing below is built yet.**
> Owner directive (2026-06-12, verbatim): *"inventory catalog shopify and ims stock needs to have one
> single screen from where once catalog is done, product purchase can be done"* — council authorized.
>
> Two design councils ran (2026-06-12), each = recon → 3 independent designs → 3 adversarial judges →
> chair synthesis, every claim grounded in file:line against the live repo:
> 1. **Product-hub council** (the SCREEN) → winner **"Buy Desk"** (3/3 judges).
> 2. **Catalog-execution council** (the 5 ENTRY PATHS; ran with all 9 locked owner answers) →
>    winner **"One Door, Three Feeders"** (2/3 judges, highest aggregate).
>
> This doc reconciles both into ONE plan. The 9 locked owner answers are in the project memory
> (`owner_directive_product_hub.md`) and repeated inline where they bind.

---

## 1. What the owner gets (plain English)

**One screen — "Buy Desk" — as the Catalog landing page.** One table, per store, every product:

| Column | Meaning |
|---|---|
| Catalog status | READY, or the exact missing fields named ("missing: colour_code, cost price") |
| Online store | Honest Shopify state: Not listed / Staged / Live / **Push-locked** (push is still dark today) |
| Stock | My store + chain total + per-store split |
| On order | Open PO qty (incl. drafts, hover shows which PO) |
| Buy signal | Suggested order qty from real 30-day sales speed, MINUS what's already on order (no double-ordering) |
| Action | **"Purchase" unlocks the moment catalog is complete** → one click → DRAFT vendor PO (grouped by vendor) |

**One door to add products** (inside Catalog) with three speeds, replacing today's five paths:
- **Single** (Quick Add — also the editor for finishing half-done drafts)
- **Grid** (spreadsheet bulk: type, vendor price-list import with remembered column mapping, clone-one-frame-across-20-colours)
- **Autopilot** (AI drafts every field; a person ALWAYS reviews and saves — AI never publishes alone)

**Retired:** the 6-step guided wizard (duplicate of Quick Add), the broken Inventory-page CSV importer,
and the legacy `/catalog/products` API write-door that wrote into a side collection nothing reads.

**Receiving finishes the cataloguing (the hero flow):** when stock physically arrives at GRN, a draft
missing only its cost price goes ACTIVE automatically using the PO's cost — and anything truly unknown
at the dock gets a "Catalog now" form right in the receiving screen. The rest of the truck is never
held up. POs can be drafted with incomplete products but cannot be **SENT** until every line is complete.

**Hard guarantees (server-enforced, not UI suggestions):**
- **Duplicate hard-block** — entering an existing product (same SKU / barcode / brand+model+colour identity)
  is refused with a link to the existing row: add stock or a variant instead. Zero duplicate rows.
- **Shopify push-locks** — brands/collections restricted in Settings (only Superadmin/Admin edit the list)
  can NEVER be pushed, enforced inside the push function itself, before any other gate. Fail-closed.
- **Purchase is always a DRAFT PO** — reviewed and sent from the existing Purchase module. Never auto-sent.
- POS, the dark Shopify push gates, and the existing purchase flow keep their current behaviour.

---

## 2. The 9 locked owner answers (constraints — already baked into both specs)

1. Entry-path consolidation: council decides, **owner approves before build** (this doc is that approval gate).
2. Who catalogs: **CATALOG_MANAGER / ADMIN / SUPERADMIN** only. No approval step — complete = immediately purchasable.
3. **Catalog-done rule** (unlocks purchase): per-category required fields **+ MRP + offer + COST price + HSN/GST**. Images NOT required.
4. Shopify is a **separate explicit step** (per-product "list online") + **brand/collection push-locks** in Settings (default-allow; SUPERADMIN/ADMIN edit; server-enforced).
5. Autopilot: **always human review**.
6. Bulk must nail: **GRN-arrival cataloguing**, **vendor price-list import**, **season clone-and-vary**.
7. Purchase raisers: **STORE_MANAGER (own store) / AREA_MANAGER / CATALOG_MANAGER / ADMIN / SUPERADMIN — DRAFT only.**
8. **Desktop-mostly** (mobile read-only).
9. Duplicates: **hard-block + show existing**. Placement: **inside Catalog as the landing page** (Inventory stays stock-ops).

### Reconciliation corrections applied (council drift vs locked answers)
- The hub council drafted before answers 7/10 were locked. Corrected here: Buy Desk lives at **/catalog**
  (not under Inventory), and **ACCOUNTANT is NOT a purchase-raiser** (it keeps bills/AP — flagged in §5).
- Naming merge: the write-side stamp (`compute_catalog_status` → `catalog_status: ACTIVE|DRAFT` + `done_gaps`)
  and the read-side view (`catalog_readiness` → `complete/missing/blockers/purchasable`, with the legacy
  flat-field overlay so old rows don't read falsely incomplete) are ONE rule in ONE module
  (`backend/api/services/product_master.py`); `purchasable = complete AND is_active`. No FE copy of the rule.

---

## 3. Real defects the councils found while grounding (fixed by this plan, worth knowing regardless)

1. **GRN mints orphan stock** — `vendors.py accept_grn` (~:1586) creates sellable-looking `stock_units`
   for ANY non-empty product_id with zero check it exists on the product spine; the manual PO form even
   fabricates `new-${Date.now()}` ids (`PurchaseOrderForm.tsx:91`). Orphans render as "Unknown Product",
   are unsellable at POS, and break valuation. → Phase 2 closes this (validate at PO + backfill-then-gate at GRN).
2. **Legacy spine bypass** — `POST/PUT /catalog/products*` writes into `catalog_products` only (invisible
   to POS/inventory/finance, divergent SKU dialect); `admin_catalog.py:997` still points bulk imports at it.
   → Phase 6 410s it (the known owner-gated step-10).
3. **No server-side brand push-lock exists** — push is dark today, so latent; the lock must land BEFORE
   the Shopify gates ever flip. → Phase 5.
4. **Duplicate guard is check-then-write** — `BaseRepository.create` swallows DuplicateKeyError into a 500,
   and the prod unique indexes (sku/barcode/product_id) were BLOCKED by dirty data (5 dup SKUs, 612 empty
   barcodes, 10,805 null product_ids — `prod_data_blockers`). → Phase 1 cleans + race-safe 409.

---

## 4. Delivery plan (one sequence, each phase independently shippable)

| Phase | What lands | Est |
|---|---|---|
| **0** | Catalog-done chokepoint (`compute_catalog_status` + `catalog_readiness`, cost_price required, `as_draft`, atomic DRAFT→ACTIVE promote, never-auto-demote, migration with dry-run) | 2d |
| **1** | Duplicate guard (409 + show-existing, identity_key report-only index, race-safe create) + prod data cleanup (5 dup SKUs owner-reviewed, 612 `""` barcodes, null product_ids) | 2.5d |
| **2** | **GRN/PO hero**: PO typeahead (no fabricated ids), DRAFT-on-DRAFT-PO + all-ACTIVE SENT gate, GRN backfill-then-gate (cost from PO → auto-ACTIVE → mint), unresolved_lines + PARTIALLY_ACCEPTED, "Catalog now" modal at receiving, vendor SKU-alias flywheel | 3d |
| **3** | Vendor price-list import (per-vendor remembered column mapping, drafts pool, matched rows = opt-in cost updates) + bulk draft completion + Inventory importer deleted (deep-link) | 3d |
| **4** | Clone-and-vary (colours × sizes), Autopilot send-approved-to-grid (prefill-only, human saves) | 1.5d |
| **5** | Shopify push-locks (E2 key `ecom.shopify_push_locks`, enforced first-statement inside push_product/collection/image, fail-CLOSED) + per-product "list online" stage endpoint + Settings card | 1.5d |
| **6** | Retirements (guided wizard, legacy catalog write-door → 410, Inventory importer), identity unique-index flip, full-suite hardening, owner walkthrough | 1.5d |
| **BD** | **Buy Desk screen** at /catalog (rows endpoint: readiness + ecom state + stock + open-PO netting + burn-rate suggestion; multi-select → DRAFT POs by vendor via the EXISTING create_po, Idempotency-Key, 409 [Open existing] recovery) | 7d |

**Sequencing:** Phase 0 first (the shared chokepoint) → **Buy Desk** (the owner's visible win, read-mostly + DRAFT-PO-only)
→ Phases 1-6. Money/POS-adjacent phases get the standing adversarial-verify pass before merge.
Total ≈ 22 focused days; every phase ships behind the house rules (no POS change, no live Shopify write,
no auto-SENT PO, no emoji in Python, single-doc atomic writes).

---

## 5. Owner round-1 answers (2026-06-12) — AMENDMENTS now binding on the build

The owner reviewed the §6 sign-off list and chose **"Hold — questions first"** on the overall
go/no-go (build does NOT start until he gives an explicit "approved"), but locked four spec
amendments. These OVERRIDE the council drafts wherever they conflict:

1. **Import formats = Excel + CSV + PDF.** PDFs are the *common* case ("we mostly get pdfs"), not an
   edge. Phase 3 ingestion: text-PDF table extraction (pdfplumber-class) + an AI-extraction fallback
   (Claude, the existing Jarvis key) for scanned/messy sheets; every extracted row lands in the grid as
   an editable DRAFT for human review — never direct-commit. One review UI for all three formats.
2. **Fuzzy vendor-SKU matching is core, not optional.** Owner example: IMS `RB 3025 001/21` vs vendor
   `0RB3025001/21`. Import classifies each row into **MATCHED** (exact / known alias), **SUGGESTED**
   (normalized fuzzy: case + whitespace + slash strip, leading-zero-prefix strip, similarity threshold —
   staff one-click confirm), or **NEW** (draft). Every confirmation writes to `vendor_sku_aliases` (the
   flywheel: the next import + GRN receiving auto-resolve from it).
3. **ACCOUNTANT KEEPS PO-raise.** Reverses the catalog-council's literal reading of answer 7 — `_PO_RAISE_ROLES`
   INCLUDES ACCOUNTANT alongside SM (own store) / AM / CM / ADMIN / SUPERADMIN.
4. **Duplicate SKUs → RE-SKU (suffix the newer), not merge.** The 5 prod dup rows are treated as genuinely
   distinct products; the unique index is unblocked by re-SKUing, zero merge risk.

**Confirmed (ticked) from the §6 list:** legacy `/catalog/products` write-door → 410 (step-10 flip) ✓;
hide DRAFT rows from POS search (`pm.pos_exclude_drafts`) ✓.

## 6. Open items still needing OWNER sign-off

1. **Approve this plan overall to START THE BUILD** (the §1 experience + §4 sequence). **← currently HELD.**
2. ~~Dup-SKU merge vs re-SKU~~ — DECIDED: **re-SKU** (§5.4).
3. ~~ACCOUNTANT loses PO-raise~~ — DECIDED: **ACCOUNTANT keeps it** (§5.3).
4. ~~Legacy `/catalog/products` 410~~ — CONFIRMED ✓.
5. ~~`pm.pos_exclude_drafts`~~ — CONFIRMED ✓.
6. ~~XLSX vs CSV-only~~ — DECIDED: **Excel + CSV + PDF + fuzzy matching** (§5.1–5.2).
7. Push-locks prevent NEW pushes; they do not retro-unpublish already-pushed items — future decision.

## 6. Source transcripts

- Product-hub council: workflow run `wf_93ac8f78` (Buy Desk spec, 3/3 judges).
- Catalog-execution council: workflow run `wf_a6a4d2c6` (One Door Three Feeders, frozen interface contracts:
  `catalog_status`, `done_gaps`, `as_draft`, `identity_key`, `ecom.listed`, `source_door`, `vendor_id`,
  URL params `?focus/?status=DRAFT/?return/?edit/?mode/?tab/?cloneFrom`, registry keys
  `ecom.shopify_push_locks`, `pm.po_sent_active_gate`, `pm.pos_exclude_drafts`).
- Both transcript dirs under the session's `subagents/workflows/` directory; the frozen contracts above are
  the build-binding interface either way.
