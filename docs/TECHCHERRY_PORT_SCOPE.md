# TechCherry → IMS 2.0 Port — Scope Doc

**Status:** Draft for review — no code yet. Awaiting sign-off on contracts and UI placement before any branch is cut.
**Source:** [techcherry-daily-excel plugin skills](C:/Users/avina/AppData/Roaming/claude/local-agent-mode-sessions/4c953781-9b8a-40b0-993b-af7356f27735/e36f8456-8fd4-49f0-b318-74e85ba733e6/rpm/plugin_01ULbtgHRaz4g2LVNKaRUh4s/skills) — `techcherry-dashboard/SKILL.md` and `whatsapp-outreach/SKILL.md`.
**Target:** IMS 2.0 — `/reports` page (analytics) + `/customers/campaigns` (WhatsApp outreach).

---

## 1. Executive summary

TechCherry's two skills compute optical-retail BI off a legacy POS's `.xls` exports and generate personalized WhatsApp outreach Excel files. IMS 2.0 already owns the underlying data natively (orders / customers / products in MongoDB), so we port the **logic and outputs**, not the XLS-ingest pipeline.

Two surfaces are touched:

1. **`/reports` page** — add 4 net-new analytics dimensions + 3 enhancements that mirror TechCherry's most differentiated sections. The other 7 TechCherry sections already exist in some form on `/reports`.
2. **`/customers/campaigns` area** — add a new "Outreach Campaigns" sub-page that produces TechCherry-style segmented WhatsApp messages, dispatched through the existing **MEGAPHONE agent + MSG91** plumbing rather than as Excel exports.

Total scope: **~7.5 dev-days across 5 phases (R1, R2, R3, M1, M2)**, each merged as its own PR.

---

## 2. Decisions log

These are the questions already answered. Anything added later goes here too.

| # | Question | Decision |
|---|---|---|
| 1 | Source of truth for report data | **IMS 2.0 MongoDB only.** No XLS importer. Reports compute live off existing collections. |
| 2 | Report depth / which TechCherry sections to port | **Start with what's MISSING from current `/reports`.** Gap-fill approach, not full duplication. |
| 3 | WhatsApp outreach delivery | **WATI** (locked but DEFERRED). M1+M2 phases are on hold until owner is ready. Existing MEGAPHONE/MSG91 plumbing left untouched in the meantime. |
| 6 | DLT template registration | Not yet done. Required for WATI live dispatch when M1 resumes. |
| 7 | M1/M2 timing | Both deferred to later. Reports phases (R1-R3) can proceed independently. |
| 4 | Customer segments to support | **All four — one-time, lapsed (12m+), Rx-overdue (12-24m), CL-reorder (3-6m).** |
| 5 | Lens data shape | **Structured fields on order line items (via product join).** No regex parsing — backend joins `orders.items` → `products` via `product_id`, pulls `category` / `brand` / `attributes`. |

Open questions are flagged inline in the relevant phase sections below with **❓**.

---

## 3. Gap analysis — what's missing vs TechCherry

| # | TechCherry section | IMS today | Gap |
|---|---|---|---|
| 1 | KPI Overview | `/reports/sales/summary` + `/dashboard` | ⚠️ Missing repeat-vs-one-timer split |
| 2 | Revenue Trends | `/sales/daily` + `/sales/comparison` + `/sales/growth` | ✅ Covered |
| 3 | Staff Performance | `/staff/ranking` + `/sales/by-salesperson` | ⚠️ Missing FY split |
| 4 | **Footfall Audit** (hidden sales) | — | ❌ **NEW** |
| 5 | Product Segments | `/sales/by-category` + `/profit/by-category` | ✅ Covered |
| 6 | **Price Band Analysis** (₹<1K → ₹1.5L+) | — | ❌ **NEW** |
| 7 | **Lens Deep Dive** (brand / type / coating / index) | — | ❌ **NEW** |
| 8 | Frames & Sunglasses | `/inventory/brand-sellthrough` | ⚠️ Need brand-revenue pivot |
| 9 | Demographics (community) | `/customers/acquisition` | ⚠️ Acquisition trend yes, community no — deprioritized (community data not in `customers` schema) |
| 10 | Discount Analysis | `/discount/analysis` | ✅ Covered |
| 11 | **Seasonality** (DOW × MOY heatmap) | — | ❌ **NEW** |
| 12 | **Purchase Recommendations** | — | ❌ **NEW** |
| 13 | **Growth Blueprint** (consultant synthesis) | — | ❌ **NEW** — JARVIS-narrated |

**Net-new for R1 (single PR):** Footfall Audit + Price Band Analysis + Lens Deep Dive + Seasonality.
**Net-new for R2:** Purchase Recommendations.
**Net-new for R3:** Growth Blueprint (LLM-narrated).
**Enhancements (folded into R1):** repeat-vs-one-timer on KPI, FY split on staff perf, brand-revenue pivot for frames/sunglasses.

---

## 4. Architecture

Existing patterns we follow:

- **Backend:** `backend/api/routers/reports.py` — add new `@router.get` handlers. Pure read-only Mongo aggregations. RBAC via `_get_user_role()` (STORE_MANAGER+ only for most; ACCOUNTANT also).
- **Frontend:** `frontend/src/pages/reports/ReportsPage.tsx` — tabbed page. We add tabs OR new report cards under existing tabs (Sales / Inventory / Customers).
- **Marketing:** `frontend/src/pages/customers/CampaignManager.tsx` exists — we add a new sibling page `OutreachCampaigns.tsx` rather than overloading the existing campaign UI.
- **WhatsApp dispatch:** `backend/agents/providers.py::send_whatsapp(phone, message, template_id)` exists with `DISPATCH_MODE=off|test|live` safety gate. We reuse it as-is.

No new collections needed. Outreach history is logged to a new `outreach_log` collection (one doc per send for audit + delivery tracking).

---

## 5. Phase R1 — Reports gap-fill (≈3 dev-days, 1 PR)

### R1.1 Footfall Audit

**Insight:** Cross-reference walkout entries with orders to find hidden sales — invoices created without a corresponding walkout entry. Surfaces under-reported foot traffic.

**Endpoint:** `GET /api/v1/reports/walkouts/footfall-audit`

Query params:
- `store_id` — store filter (defaults to user's active store)
- `year`, `month` — period (defaults to current month)

Response shape:
```json
{
  "store_id": "BV-BOK-01",
  "period": "2026-05",
  "months": [
    {
      "month": "2026-05",
      "walkins_total": 240,
      "walkouts_total": 73,
      "walkouts_converted": 28,
      "orders_total": 156,
      "hidden_sales": 128,
      "hidden_sales_pct": 0.82,
      "staff_reported_conversion_pct": 0.32,
      "true_conversion_pct": 0.65
    }
  ],
  "rolling_12m": { /* same shape, aggregated */ }
}
```

Computation:
- `walkins_total` = sum of `walk_in_counters.total` for the (store, month)
- `walkouts_total` / `walkouts_converted` = grouped counts on `walkouts` collection
- `orders_total` = count of `orders` where `status != "draft"` in the period
- `hidden_sales` = `orders_total - walkouts_converted` (sales without a walkout entry)
- `staff_reported_conversion_pct` = `walkouts_converted / walkins_total`
- `true_conversion_pct` = `orders_total / walkins_total`

UI placement: `/reports?tab=customers` — new card "Footfall Audit" below the acquisition chart. Monthly comparison table + colored bar chart showing the gap between staff-reported and true conversion.

Mockup:
```
┌─ Footfall Audit ──────────────────────────────────────────┐
│ Hidden sales = orders where staff didn't log the walk-in. │
│                                                             │
│ Month     Walk-ins  Walkouts  Converted  Orders  Hidden    │
│ May 26      240       73        28        156      128     │
│ Apr 26      218       54        19        142      123     │
│ Mar 26      201       48        21        128      107     │
│                                                             │
│ Reported conversion: 32% • True conversion: 65%             │
│ Hidden: 82% of all sales bypass the walk-in log             │
└─────────────────────────────────────────────────────────────┘
```

---

### R1.2 Price Band Analysis

**Insight:** Segment invoices by net amount into 11 bands. Track customer movement between bands across financial years — premiumization signal.

**Endpoint:** `GET /api/v1/reports/sales/price-bands`

Query params: `store_id`, `fy_count` (default 3 — last 3 FYs), `year`/`month` for monthly trend.

Bands (₹):
```
Below 1K, 1K-2.5K, 2.5K-5K, 5K-10K, 10K-15K,
15K-20K, 20K-30K, 30K-50K, 50K-75K, 75K-1.5L, 1.5L+
```

Response shape:
```json
{
  "bands": ["<1K", "1K-2.5K", ..., "1.5L+"],
  "by_fy": [
    {
      "fy": "FY24-25",
      "invoices_by_band": [42, 88, 134, ..., 3],
      "revenue_by_band": [29800, 165200, ..., 480000],
      "atv_by_band": [710, 1877, ..., 160000]
    }
  ],
  "monthly_trend_by_band": {
    "5K-10K": [{"month": "2025-04", "revenue": 245000, "invoices": 31}, ...]
  },
  "movement_summary": {
    "premiumized_pct": 0.18,
    "downgraded_pct": 0.06,
    "stable_pct": 0.76
  }
}
```

Computation:
- For each order: `bucket = band(order.grand_total - order.tax_total)`
- Group by `(fy, bucket)`
- Movement: for repeat customers, compare their last-FY avg invoice band to current-FY avg

UI placement: `/reports?tab=sales` — new card "Price Band Analysis" with stacked bar chart (bands × FY) + a heat-map table showing month × band revenue.

Mockup:
```
┌─ Price Band Analysis ─────────────────────────────────────┐
│  Band         FY24-25 invoices │ FY25-26 invoices │ Δ     │
│  <1K          42                │ 28               │ -33%  │
│  1K-2.5K      88                │ 79               │ -10%  │
│  5K-10K       134               │ 178              │ +33%  │
│  10K-15K      67                │ 89               │ +33%  │
│  15K-20K      23                │ 41               │ +78%  │
│  ...                                                       │
│                                                            │
│  Premiumization: 18% of repeat customers moved up a band.  │
│  Stable: 76% • Downgraded: 6%                              │
└────────────────────────────────────────────────────────────┘
```

---

### R1.3 Lens Deep Dive

**Insight:** Lens revenue is the most profitable line. Break down by brand, type, coating, refractive index. Highlight upsell gaps.

**Endpoint:** `GET /api/v1/reports/sales/lens-deep-dive`

Query params: `store_id`, `year`/`month` for period.

**Data path:** order items where `item_type IN ("LENS", "CONTACT_LENS")` → `$lookup` into `products` → pull `brand`, `category`, and `attributes` (where coating/index/lens_type are stored per category-specific JSON).

❓ **Open question:** the `product.attributes` field is a generic `object` per schema. Need to confirm with you what keys you've actually populated on lens products (e.g. `attributes.coating`, `attributes.refractive_index`, `attributes.lens_type`). If the structure isn't consistent, R1.3 ships with the fields that ARE present and reports parse-rate on the missing ones.

Response shape:
```json
{
  "period": "2026-05",
  "totals": {
    "lens_units": 287,
    "lens_revenue": 18400000,
    "atv": 64111
  },
  "by_brand": [
    {"brand": "Carl Zeiss", "units": 124, "revenue": 9800000, "share": 0.53},
    {"brand": "Essilor", "units": 78, "revenue": 4200000, "share": 0.23},
    ...
  ],
  "by_type": [
    {"type": "Progressive", "units": 89, "revenue": 6200000},
    {"type": "Single Vision", "units": 167, "revenue": 8400000},
    {"type": "Blue Light", "units": 31, "revenue": 3800000}
  ],
  "by_coating": [...],
  "by_refractive_index": [
    {"index": "1.50", "units": 142},
    {"index": "1.60", "units": 89},
    {"index": "1.67", "units": 41},
    {"index": "1.74", "units": 15}
  ],
  "parse_rate": 0.94
}
```

UI placement: `/reports?tab=sales` — new card "Lens Deep Dive" with 4 mini-charts (brand pie, type bar, coating donut, index bar) + a top-brands table.

---

### R1.4 Seasonality

**Insight:** When in the week and when in the year do customers actually buy?

**Endpoint:** `GET /api/v1/reports/sales/seasonality`

Query params: `store_id`, `years_back` (default 2).

Response shape:
```json
{
  "day_of_week": [
    {"dow": "Mon", "invoices": 320, "revenue": 1820000, "atv": 5687},
    {"dow": "Tue", "invoices": 298, "revenue": 1690000, "atv": 5671},
    ...
  ],
  "month_of_year": [
    {"month": "Jan", "invoices": 480, "revenue": 2480000},
    ...
  ],
  "peak_dow": "Sat",
  "trough_dow": "Tue",
  "peak_month": "Oct",
  "trough_month": "Aug",
  "peak_dow_lift_pct": 0.42
}
```

Computation: straight aggregation on `orders.created_at`'s day-of-week and month strftime.

UI placement: `/reports?tab=sales` — new card "Seasonality" with two side-by-side bar charts (DOW, MOY).

Mockup:
```
┌─ Seasonality ─────────────────────────────────────────────┐
│  Day of week (last 24 months)                              │
│  Sat ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ ₹24.8L                  │
│  Sun ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓     ₹19.2L                  │
│  Fri ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓         ₹16.1L                  │
│  Mon ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓             ₹13.4L                  │
│  ...                                                        │
│                                                             │
│  Month of year                                              │
│  Oct ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓   ₹38.2L  (festive peak)   │
│  Dec ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓     ₹34.5L                  │
│  ...                                                        │
└─────────────────────────────────────────────────────────────┘
```

---

### R1 deliverables checklist

- [ ] `backend/api/routers/reports.py` — 4 new endpoints
- [ ] `backend/tests/test_reports_techcherry.py` — 12+ unit tests (3-4 per endpoint)
- [ ] `frontend/src/services/api/reports.ts` — 4 new client methods
- [ ] `frontend/src/pages/reports/sections/FootfallAuditCard.tsx` — new
- [ ] `frontend/src/pages/reports/sections/PriceBandCard.tsx` — new
- [ ] `frontend/src/pages/reports/sections/LensDeepDiveCard.tsx` — new
- [ ] `frontend/src/pages/reports/sections/SeasonalityCard.tsx` — new
- [ ] `frontend/src/pages/reports/ReportsPage.tsx` — wire cards into existing tabs
- [ ] Enhancement: KPI overview shows repeat-vs-one-timer split
- [ ] Enhancement: staff performance has FY toggle
- [ ] Enhancement: frames/sunglasses by-brand pivot

---

## 6. Phase R2 — Purchase Recommendations (≈1 day, 1 PR)

**Endpoint:** `GET /api/v1/reports/purchase/recommendations`

Logic: combine product velocity (units sold last 90d) with current stock and reorder point. Surface "buy more of X" suggestions per category, ranked by gap × margin.

Response:
```json
{
  "recommendations": [
    {
      "category": "FRAME",
      "brand": "Ray-Ban",
      "velocity_90d": 24,
      "current_stock": 6,
      "reorder_point": 12,
      "suggested_order_qty": 18,
      "estimated_revenue_impact": 145000,
      "confidence": "HIGH",
      "reason": "Velocity 24/90d, stock 6, reorder at 12. Margin ₹3200/unit."
    },
    ...
  ]
}
```

UI placement: `/reports?tab=inventory` — new card "Purchase Recommendations" with ranked table.

---

## 7. Phase R3 — Growth Blueprint via JARVIS (≈0.5 day, 1 PR)

**Endpoint:** `GET /api/v1/reports/blueprint`

Calls JARVIS's `complete()` with a structured prompt containing the outputs of all the R1+R2 endpoints. JARVIS returns a 12-section consultant-style narrative.

UI placement: new dedicated page `/reports/blueprint` accessed from the Reports tab. Renders the LLM markdown response with print-friendly styling.

Sections (mirrors TechCherry's "Deep Analysis & Growth Blueprint"):
1. Where the business stands today
2. Revenue trajectory — ATV-driven or footfall-driven?
3. Premiumization proof — price band movement
4. Staff honest assessment
5. Footfall integrity — hidden sales quantified
6. Lens business as profit engine
7. Discount discipline
8. Growth levers (ranked by ₹ impact)
9. Revenue projections (conservative 3-year)
10. Quick wins (zero-cost)
11. Top 10 actions
12. Competitive positioning

Generated on-demand. Optionally cached per-(store, month) since regeneration costs Claude/Ollama tokens.

---

## 8. Phase M1 — Outreach backend (≈2 days, 1 PR)

### Service module

`backend/api/services/outreach.py` — pure-Python functions:

```python
def identify_segment_customers(
    db, store_id, segment: Literal["one_time", "lapsed", "rx_overdue", "cl_reorder"],
    limit: int = 100,
) -> list[dict]: ...

def score_comeback_confidence(customer: dict) -> dict:
    # Returns {score: 10-95, rating: HIGH|MEDIUM|LOW|VERY_LOW, reasons: [...]}
    ...

def classify_purchase_type(items: list[dict]) -> str:
    # Returns "spectacles" | "lens" | "sunglasses" | "frame" | "contact_lens" | ...
    ...

def parse_name(full_name: str) -> dict:
    # Returns {first: "Rahul", gender: "M"|"F"|"U", honorific: "Sir"|"Ma'am"|"Dr."}
    # Indian name database from TechCherry skill (500+ names)
    ...

def render_message(customer: dict, template_id: str, vars: dict) -> str:
    # Picks template based on purchase_type + season + months_since_visit
    # Substitutes {first_name}, {brand}, {months}, {honorific}
    ...
```

### Endpoints

```
GET  /api/v1/marketing/outreach/segments
       → list of segments with counts and refresh timestamps

GET  /api/v1/marketing/outreach/segments/{segment_id}/customers
       → ranked customer list with scoring, suggested messages, classification

POST /api/v1/marketing/outreach/dispatch
       Body: { segment_id, customer_ids: [...], template_id?, send_at? }
       → Schedules MEGAPHONE dispatches. Returns batch_id + per-customer status.

GET  /api/v1/marketing/outreach/history
       → outreach_log entries: who got what message when, delivery status
```

### Templates

Templates live in code (`backend/api/services/outreach_templates.py`) as a registry, keyed by `(purchase_type, months_since_visit_bucket, brand_class)`. Each template has:
- `id` (DLT template ID for compliance — needs MSG91 registration)
- `name` (human-readable)
- `body_template` (with `{var}` placeholders)
- `vars_required` (e.g. `["first_name", "honorific", "months", "brand"]`)
- `cta_type` (`"eye_check"`, `"adjustment"`, `"refill_reminder"`, none)

❓ **Open question:** DLT-template registration is a regulatory requirement for transactional WhatsApp in India. Each outreach template must be pre-approved by MSG91 with the user's business credentials. Until templates are registered, dispatch falls back to `DISPATCH_MODE=test` (sends only to `TEST_PHONE` env var). Acceptable?

### `outreach_log` collection schema

```json
{
  "_id": "OL-2026-05-A1B2C3",
  "store_id": "BV-BOK-01",
  "segment": "one_time",
  "customer_id": "CUST-9f352422",
  "template_id": "ims_eyecheck_rx_v1",
  "rendered_message": "Namaste Rahul Sir, ...",
  "dispatched_at": "2026-05-21T10:32:00Z",
  "dispatched_by": "user-superadmin",
  "msg91_message_id": "abc123",
  "delivery_status": "queued | sent | delivered | failed",
  "delivery_status_updated_at": "...",
  "customer_replied": false,
  "customer_visited_after": null,    // backfilled by MEGAPHONE on next order from this customer
  "comeback_score_at_send": 75
}
```

### Tests

10+ unit tests in `backend/tests/test_outreach.py`:
- Segment SQL correctness (one-time, lapsed boundaries)
- Confidence scoring math
- Name parser edge cases (Dr., initials, single names, company names)
- Template rendering with missing vars
- DLT compliance check (rejects send without registered template)
- Dispatch mode safety gate

---

## 9. Phase M2 — Outreach frontend (≈1.5 days, 1 PR)

### New page: `frontend/src/pages/customers/OutreachCampaigns.tsx`

Layout:
```
┌─ Outreach Campaigns ──────────────────────────────────────┐
│  ┌────────────────┐ ┌────────────────┐ ┌────────────────┐ │
│  │ One-time       │ │ Lapsed (12m+)  │ │ Rx Overdue     │ │
│  │ 487 customers  │ │ 312 customers  │ │ 156 customers  │ │
│  │ Avg LTV ₹8,400 │ │ Avg LTV ₹14,200│ │ HIGH urgency   │ │
│  │ [Review →]     │ │ [Review →]     │ │ [Review →]     │ │
│  └────────────────┘ └────────────────┘ └────────────────┘ │
│  ┌────────────────┐                                       │
│  │ CL Reorder     │                                       │
│  │ 78 customers   │                                       │
│  │ Most urgent: 23│                                       │
│  │ [Review →]     │                                       │
│  └────────────────┘                                       │
└────────────────────────────────────────────────────────────┘
```

Click a segment card → drills into a customer list page:

```
┌─ One-time Buyers (487 customers) ─────────────────────────┐
│  Template: [Eye check + Ray-Ban welcome ▾]                 │
│  Filter: Confidence ≥ HIGH  •  Last visit: 3-12m ago       │
│  Select all (showing 60 ranked by confidence)              │
│                                                             │
│  [✓] Rahul Sharma · 98765xxxxx · HIGH 87% · 5m ago · Ray-Ban│
│       Preview: "Namaste Rahul Sir, hope you're enjoying    │
│        the Ray-Ban sunglasses..." [Edit]                   │
│                                                             │
│  [✓] Priya Gupta · 87654xxxxx · HIGH 82% · 8m ago · Zeiss  │
│       Preview: "Hello Priya Ma'am, ..." [Edit]              │
│  ...                                                        │
│                                                             │
│  [Send to 47 selected via MEGAPHONE]  [Export Excel]       │
└─────────────────────────────────────────────────────────────┘
```

### Components

```
frontend/src/pages/customers/
├── OutreachCampaigns.tsx              (segment grid + entry point)
├── OutreachSegmentDetail.tsx          (drill-down customer list + send)
├── components/
│   ├── SegmentCard.tsx                (one-time, lapsed, etc.)
│   ├── OutreachCustomerRow.tsx        (per-customer row with preview)
│   ├── MessagePreviewModal.tsx        (edit template per-customer)
│   └── DispatchConfirmModal.tsx       (count, DLT confirm, send)

frontend/src/services/api/marketing.ts
├── listOutreachSegments()
├── listSegmentCustomers(segment_id, filters)
├── dispatchOutreach(segment_id, customer_ids, template_id, send_at?)
└── getOutreachHistory()
```

### Routing

`App.tsx` — add 2 routes:
- `/customers/outreach` → `OutreachCampaigns`
- `/customers/outreach/:segment` → `OutreachSegmentDetail`

### Discoverability

Add a button "Outreach" in the existing `CampaignManager.tsx` page header that links to `/customers/outreach`. Avoids confusion between "Campaigns" (manual broadcasts) and "Outreach" (segment-driven retention).

---

## 10. Risks and assumptions

| Risk | Mitigation |
|---|---|
| `product.attributes` doesn't have consistent keys for coating/index/type | R1.3 ships with `parse_rate` metric. Surfaces data-quality issue to user. Add Catalog field validation in a follow-up. |
| Walkout → order linkage is implicit (mobile match), not enforced | Footfall Audit treats `hidden_sales = orders - walkouts_converted` as approximate. Surface the math openly. |
| MSG91 DLT template registration is owner's responsibility | Frontend shows clear "Pending DLT approval" badge per template until backend marks them approved. Dispatch falls back to test mode. |
| WhatsApp dispatch costs (₹0.30-₹1 per send via MSG91) | Per-segment send button shows estimated cost. Daily cap enforced server-side. |
| LLM cost for Growth Blueprint (Phase R3) | Cache per-month. Regeneration explicit + costed. |
| Mongo aggregation pipeline performance on 100K+ orders | Add indexes on `(store_id, created_at)` and `(store_id, customer_id)`. Each new endpoint gets a `.explain()` profile before merge. |
| Segment "lapsed" boundary at 12m could miss seasonal buyers (sunglass only in summer) | Lapsed window is per-segment: 12m for spectacles/lens, 18m for sunglass-only. Encoded in `identify_segment_customers()`. |

---

## 11. Out of scope

- Web search for current lens MRP (TechCherry skill does this for revenue estimation — IMS has its own `mrp` field on products, so we use that).
- Demographics / community segmentation (data not in IMS customer schema; would need a customer-attributes extension).
- Footfall register backfill from POS — beyond walkout module's responsibility.
- Multi-language WhatsApp templates (English + Hinglish only in M1; Marathi/Hindi via DLT registration is a separate effort).
- A/B testing of outreach templates (defer to Phase M3 if needed).
- Outreach response handling (reply detection, conversation continuation) — defer.
- Excel export of outreach lists — implemented in M2 as a secondary feature, just a CSV/XLSX download of the rendered table.

---

## 12. Estimate summary

| Phase | Scope | Days |
|---|---|---|
| **R1** | Footfall Audit + Price Bands + Lens Deep Dive + Seasonality | **3** |
| **R2** | Purchase Recommendations | **1** |
| **R3** | Growth Blueprint (JARVIS-narrated) | **0.5** |
| **M1** | Outreach service + endpoints + tests | **2** |
| **M2** | Outreach frontend (segment grid + drill-down + dispatch UX) | **1.5** |
| **Total** | 5 PRs | **8 dev-days** |

Each phase merges independently. R1-R3 unblock the new dashboards. M1-M2 light up the WhatsApp engine.

---

## 13. Recommended starting order

1. **R1** first — biggest single chunk of new analytics value. Single PR, ~3 days.
2. **M1+M2** next as a pair — direct revenue impact from reactivation campaigns.
3. **R2** then — fits as inventory-side companion to R1.
4. **R3** last — needs R1+R2 outputs to ground its synthesis.

---

## 14. Sign-off

Before any branch is cut:

- [ ] Owner confirms endpoint contracts (section 5-9)
- [ ] Owner confirms UI placement (existing tabs vs new pages)
- [ ] Owner answers ❓ DLT template registration question (section 8)
- [ ] Owner answers ❓ `product.attributes` lens key consistency question (section 5.3)
- [ ] Owner picks starting phase
