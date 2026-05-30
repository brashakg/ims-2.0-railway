# QA Investigation — 2026-05-30

Live-stack QA fleet run against a real backend (`localhost:8000`) + real Mongo, authed as `admin` (SUPERADMIN), HTTP-only (agents read-only on source). Three waves: customer-search fix verification, data-creation (customers/patients/Rx/uploads), and orders/POS stress (50 orders → breaking point).

Money math was independently re-derived and is **correct** (inclusive GST: `taxable + tax == grand_total` on 48/48 orders; split tenders reconcile to 0; cart discount applied before GST). Bugs below are correctness/validation/control gaps, not arithmetic errors.

Legend: ✅ fixed & shipped · 🟡 fix in progress · ⏳ queued · ❓ needs user decision (revenue-critical / policy)

---

## Wave A — Customer search (regression check)
- ✅ **Patient-name search** — `GET /customers?search=<family member>` now returns the parent account for child/spouse/parent (5/5). Shipped as **#362** (merged). Verified by the QA agent.

## Wave B — Data creation (customers / patients / Rx / uploads)

| ID | Sev | Bug | Location | Status |
|----|-----|-----|----------|--------|
| B-1 | P1 | **Silent data corruption** — nested `patients[]` on customer-create overwrites caller's `relation` to `"Self"/"Other"` by name match (e.g. "Spouse","Son" → "Other"). Dedicated `POST /customers/{id}/patients` preserves it correctly. | `customers.py:240` | 🟡 agent `claude/fix-qa-data-bugs` |
| B-2 | P2 | **Cannot back-date prescriptions** — `PrescriptionCreate` has no date field; service always stamps `utcnow()`. POSTed `prescription_date`/`test_date`/`created_at` → 201 but silently dropped. Blocks the "Rx on different days" requirement. | `prescriptions.py` create | ⏳ after #363 (conflicts) |
| B-3 | P2 | **`upload-bill` false success** — returns 200 `{persisted:false}` when storage down; sibling `POST /handoffs` correctly returns 503. Inconsistent fail-soft. | `expenses.py` upload-bill | 🟡 agent `claude/fix-qa-data-bugs` |
| B-4 | P3 | `customer_type:"B2X"` (invalid enum) accepted 201. | `customers.py` CustomerCreate | 🟡 agent |
| B-5 | P3 | Malformed GSTIN (`"NOTAGSTIN"`) accepted; malformed email accepted; future DOB accepted. | `customers.py` CustomerCreate/PatientCreate | 🟡 agent (format-when-provided) |
| B-6 | P3 | Mobile starting with 0 accepted (`^\d{10}$` doesn't enforce 6-9 lead); B2B created with no GSTIN. | `customers.py` | ❓ policy (deferred — leading-digit, require-GSTIN-for-B2B) |
| B-7 | P3 | `upload-bill` size cap not enforced before storage (couldn't fully confirm — storage down). | `expenses.py` | ⏳ verify when storage configured |

Positives: Rx validation excellent (SPH/CYL/ADD range + 0.25 grid + AXIS 1-180, precise 422s); `GET /prescriptions/family/{id}` clean; CL modality enum-validated; multipart type allowlist enforced.

## Wave C — Orders / POS stress (48 valid orders + breaking-point probes)

| ID | Sev | Bug | Location | Status |
|----|-----|-----|----------|--------|
| C-1 | P1 | **Seeded catalog products cannot be ordered** — `/catalog/products` serves from collection `catalog_products`, but order-create validates `product_id` against `products` (empty). Only virtual ids (`custom-`,`lens-`) + `walkin-` customers bypass. In a catalog_products-only deploy, POS is fully blocked. | `orders.py` `_resolve_product_doc` ~614,807; `catalog.py:1015` | ❓ revenue-critical |
| C-2 | P1 | **GST undercharged** — rate resolved from `category` only (`cat = category or item_type`); unknown/typo'd category falls back to `DEFAULT_GST_RATE=5%`. A WATCH/SUNGLASS with a wrong category string bills 5% instead of 18%. | `orders.py:103`; `gst_rates.py` | ❓ revenue-critical |
| C-3 | P1 | **`unit_price × quantity = Infinity → HTTP 500`** — no upper bound; float overflow breaks JSON. Also `qty 1e9` / `price 1e12` accepted (201) with no sanity cap. | `orders.py` OrderCreate/create | ⏳ safe validation fix |
| C-4 | P2 | **100% discount → ₹0 order accepted, no approver captured** — no zero-total guard, `discount_approved_by` null. ₹50k item free, no audit trail. | `orders.py` discount path | ❓ control/audit |
| C-5 | P2 | **No idempotency on order-create** — 10-12 identical concurrent POSTs → 10-12 distinct orders. POS double-click / retry duplicates live invoices. | `orders.py` create | ❓ needs design |
| C-6 | P2 | **Invoice JSON omits CGST/SGST/IGST split + GSTIN + place-of-supply** — `GET /orders/{id}/invoice` returns only totals; intra vs inter-state never distinguished server-side (split presumably only in FE receipt). | `orders.py` invoice | ❓ compliance |
| C-7 | P3 | `delivery_priority:"TELEPORT"` accepted (no enum; only NORMAL/EXPRESS/URGENT meaningful). | `orders.py` OrderCreate | ⏳ safe validation fix |
| C-8 | P3 | `delivery_date:"2020-01-01"` (past) accepted (no future check). | `orders.py` OrderCreate | ⏳ safe validation fix |
| C-9 | P3 | **CREDIT over-tender wrongly blocked** — router blocks `amount > balance_due` for ALL methods incl. CREDIT, but `OrderRepository.add_payment` documents CREDIT as exempt (pay-later). Router vs repo inconsistency. | `orders.py:1605` vs `order_repository.py:142` | ❓ payment-flow |

Correctly enforced (verified, no bug): empty cart 400; qty 0/neg 422; discount >100% 422; emi_months>24 422; cart cap 15 items 400; over-tender (cash) blocked single + cumulative; lifecycle transitions enforced; UNPAID-delivery blocked; first-payment auto-confirm; PAID/PARTIAL/CREDIT status correct.

Note (FE/DB hygiene, not a server break): XSS/script/10k-char/unicode/null-byte in `notes`/`product_name` stored verbatim — no server-side injection observed, but no sanitization/length cap.

---

## Triage summary
- **Shipping now (non-POS, low risk):** B-1, B-3, B-4, B-5 (agent cluster `claude/fix-qa-data-bugs`).
- **After #363 merges:** B-2 (back-date Rx — conflicts with prescription PR).
- **Safe orders validation (additive 422s, no valid-order behavior change):** C-3, C-7, C-8.
- **Needs user decision (revenue/tax/control/compliance — "ask before touching POS"):** C-1, C-2, C-4, C-5, C-6, C-9; plus policy B-6.
