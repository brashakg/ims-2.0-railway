# Feature #22: Split-Tender Matrix with Ledger Routing (Advanced POS)
META: effort=M days=5 risk=MED roi=4 quickwin=no deps=none phase=2

## Existing overlap
IMS already has a **fully-functional split-tender system** — this feature is largely built. Key existing capabilities:

- Multi-tender per order: `backend/api/routers/orders.py` `add_payment()` (~line 2386) records multiple `PaymentEntry` docs against one order. Tenders supported: CASH, UPI, CARD, BANK_TRANSFER, EMI, CREDIT, GIFT_VOUCHER, LOYALTY.
- Over-tender guard: cash non-CREDIT tenders cannot exceed `grand_total` (orders.py ~2386+).
- CREDIT tender: pay-later promise, excluded from `amount_paid`, sticky audit flag.
- Atomic gift-voucher debit: `vouchers.redeem_voucher_atomic()` (vouchers.py:170) — guarded `find_one_and_update`, no double-spend.
- Loyalty tender: `LOYALTY` tender type exists; debit via `/loyalty/redeem` before order confirm; atomic `try_debit()` in `loyalty_repository.py:101`.
- Store-credit tender: `try_debit_store_credit()` in `customers.py:110` — guarded debit on `credit_note_ledger`.
- POS Zustand store (`frontend/src/stores/posStore.ts:146-149`): split-tender state already tracked (`tenders[]`, `totalPaid`, `changeDue`).
- **Genuine gap**: ledger routing — there is no mapping of tender type → accounting sub-ledger. Cash lands in general `amount_paid`; no distinct GL bucket per tender type. Also, "Family Loyalty Points Wallet" (pooled across family members) is explicitly **not implemented** (loyalty is per-customer only).

## Reuse (extend, don't rebuild)
- `backend/api/routers/orders.py` — extend `add_payment()` to stamp `ledger_account` on each `PaymentEntry`
- `backend/database/repositories/order_repository.py` — extend `add_payment()` to persist `ledger_account` field
- `backend/api/routers/loyalty.py` — add family-pool query endpoint; reuse `try_debit()` atomicity pattern
- `backend/database/repositories/loyalty_repository.py` — extend `LoyaltyAccount` for family-pool aggregation
- `backend/api/routers/finance.py` — extend cash-inflow aggregation (`_split_output_tax`, `cash_flow_forecast`) to group by `ledger_account`
- `frontend/src/stores/posStore.ts` — add `ledger_account` field to each tender entry; add family-pool loyalty balance fetch
- `frontend/src/pages/pos/POSPage.tsx` + payment step component — extend tender picker to show ledger routing label and family pool balance

## Data model
**Extend existing `PaymentEntry` sub-doc on `orders` collection** (no new collection needed):
```
PaymentEntry (existing fields retained):
  + ledger_account: str  # e.g. "CASH_IN_HAND", "CARD_PG", "UPI_PG", "LOYALTY_WALLET", "STORE_CREDIT", "GIFT_VOUCHER"
  + family_pool_used: bool  # true when loyalty points drawn from family pool, not individual
  + pool_member_customer_id: str | None  # which family member's points were actually debited
```

**New singleton collection: `tender_ledger_map`** (admin-configurable routing table):
```
{
  "_id": "tender_ledger_map",
  "mappings": [
    { "tender_type": "CASH",         "ledger_account": "CASH_IN_HAND",    "tally_ledger": "Cash" },
    { "tender_type": "UPI",          "ledger_account": "UPI_PG",          "tally_ledger": "UPI Receipts" },
    { "tender_type": "CARD",         "ledger_account": "CARD_PG",         "tally_ledger": "Card Receipts" },
    { "tender_type": "BANK_TRANSFER","ledger_account": "BANK_TRANSFER",   "tally_ledger": "Bank Account" },
    { "tender_type": "EMI",          "ledger_account": "EMI_RECEIVABLE",  "tally_ledger": "EMI Debtors" },
    { "tender_type": "CREDIT",       "ledger_account": "AR_TRADE",        "tally_ledger": "Sundry Debtors" },
    { "tender_type": "GIFT_VOUCHER", "ledger_account": "GIFT_VOUCHER_LIA","tally_ledger": "Gift Voucher Liability" },
    { "tender_type": "LOYALTY",      "ledger_account": "LOYALTY_WALLET",  "tally_ledger": "Loyalty Points Liability" },
    { "tender_type": "STORE_CREDIT", "ledger_account": "STORE_CREDIT_LIA","tally_ledger": "Store Credit Liability" }
  ]
}
```

**Extend `loyalty_accounts` collection** (family-pool support):
```
+ family_pool_enabled: bool   # whether this account participates in family pool
+ head_customer_id: str | None  # the "head" account whose pool all members share
```

**New collection: `family_loyalty_pools`** (only if owner enables family pooling):
```
{
  pool_id: str,
  head_customer_id: str,       # primary account (usually parent/account holder)
  member_customer_ids: [str],  # includes head
  combined_balance: int,       # denormalized sum; authoritative via atomic update
  updated_at: datetime
}
```

## Backend

**`GET /api/v1/settings/tender-ledger-map`** (ADMIN/SUPERADMIN) — read current `tender_ledger_map` singleton.

**`PUT /api/v1/settings/tender-ledger-map`** (SUPERADMIN only) — update Tally ledger name mappings; validate all 9 tender types present. Writes audit log.

**Extend `POST /api/v1/orders/{order_id}/payments`** (`add_payment()` in orders.py) — on receiving a `PaymentEntry`, look up `tender_ledger_map` (cached in-process, TTL 60s) and stamp `ledger_account` before DB write. No change to caller contract; new field is additive.

**`GET /api/v1/loyalty/family-pool/{customer_id}`** (SALES_CASHIER / SALES_STAFF / STORE_MANAGER / ADMIN / SUPERADMIN) — return family pool balance for a customer: checks if `family_loyalty_pools` doc exists for this customer_id (as head or member), returns `pool_id`, `combined_balance`, `member_summary[]` (name + individual balance). Falls back to individual account if no pool exists. No PII beyond names of family members already linked on `customers.patients[]`.

**`POST /api/v1/loyalty/family-pool/{customer_id}/debit`** (internal, called by orders.py `add_payment()` for LOYALTY tender) — atomic `find_one_and_update` on `family_loyalty_pools` with `$inc: {combined_balance: -amount}`, filter requires `combined_balance >= amount`. Returns `deducted_from: "pool" | "individual"`, `pool_member_debited`. Mirrors `try_debit()` pattern from `loyalty_repository.py:101` exactly — no read-modify-write.

**Extend `backend/api/services/payroll_exports.py` / Tally JV builder** (`nexus_providers.py` NEXUS agent) — when building Tally sales voucher XML, use `ledger_account` → `tally_ledger` from stored `PaymentEntry.ledger_account` (already on the order doc after this change). Removes the current hardcoded "Cash" ledger for all tenders. Zero change to XML schema; only the `<LEDGERNAME>` values change per tender.

**Extend `GET /api/v1/finance/cash-flow`** (finance.py) — group inflows by `ledger_account` in the aggregation pipeline (`$group` by `payments.ledger_account`). Returns `inflows_by_ledger: { "CASH_IN_HAND": ₹X, "UPI_PG": ₹Y, ... }` alongside existing `total_inflow`. Backward-compatible (total_inflow unchanged).

## Frontend

**POS Payment Step — extend tender picker** (`frontend/src/pages/pos/POSPage.tsx` + payment component):
- Each tender tile shows its ledger routing label in small muted text below the tender name (e.g., "UPI → UPI Receipts"). Restrained — gray-500, small font, not prominent.
- LOYALTY tender tile: fetch family pool balance via `GET /loyalty/family-pool/{customer_id}` on customer selection. Show "Family Wallet: ₹X pts" if pool exists, else individual balance. Single toggle "Use Family Wallet" (checkbox, default off). Checking it routes the LOYALTY tender through the pool debit endpoint.
- After all tenders entered, show a read-only "Ledger routing" summary line in the review step: `Cash → Cash In Hand | UPI ₹2,000 → UPI PG | Loyalty 500 pts → Loyalty Wallet`. Collapsed by default; expand on tap. Light neutral styling — no colour coding beyond semantic (green = settled, amber = pending CREDIT).

**Settings → Finance → Tender Ledger Map** (new tab under existing Settings page, `frontend/src/pages/settings/SettingsPage.tsx`):
- Simple table: Tender Type | Ledger Account (IMS) | Tally Ledger Name (editable text field).
- Save button (SUPERADMIN only). Read-only for ADMIN. Hidden from all other roles.
- Neutral table styling; no colours; consistent with existing Settings panel design.

**Finance Dashboard — Cash Inflow by Tender** (`frontend/src/pages/finance/FinanceDashboard.tsx`):
- Extend the existing "Cash Inflow" card to show a breakdown bar (horizontal stacked, monochrome — different shades of gray for each tender bucket). CASH / UPI / CARD / OTHER. No rainbow colours; accent only on the selected segment.

## Business rules

- **Tender total must equal grand total**: enforced by existing `add_payment()` over-tender guard. Not relaxed by this feature.
- **LOYALTY tender cap**: existing `max_redeem_pct` from `loyalty_settings` applies regardless of pool vs individual. Pool debit honours the same cap — cap is checked against order total before debit call.
- **Family pool debit atomicity**: the pool's `combined_balance` is decremented atomically (guarded filter); if insufficient, fall back to individual balance silently, or surface as "insufficient pool balance" with option to split across pool + another tender.
- **Ledger map is immutable at order-time**: once an order's `PaymentEntry` is written with `ledger_account`, that value never changes even if admin later edits the map. This preserves accounting integrity.
- **Tally ledger names**: must match exact Tally master ledger names configured in the customer's Tally company file. Mismatch causes Tally import to fail silently (Tally behaviour). SUPERADMIN is responsible for alignment.
- **No ledger routing for DRAFT orders**: `ledger_account` stamped only when `add_payment()` is called (CONFIRMED or later). DRAFT orders have no payment entries.
- **Audit**: every `add_payment()` call already writes to `audit_logs`; extend the detail dict to include `ledger_account`.
- **Family pool PII**: only customer_ids and combined balance stored in `family_loyalty_pools`. Member names fetched on-demand from `customers` collection (not denormalized in pool doc).

## RBAC

| Role | Tender Ledger Map (Settings) | Family Pool Balance (POS) | Family Pool Debit | Cash Inflow by Ledger (Finance) |
|---|---|---|---|---|
| SUPERADMIN | Read + Write | Yes | Via order flow | Yes |
| ADMIN | Read only | Yes | Via order flow | Yes |
| ACCOUNTANT | Read only | No | No | Yes |
| STORE_MANAGER | No | Yes | Via order flow | Own store only |
| SALES_CASHIER | No | Yes (POS only) | Via order flow | No |
| SALES_STAFF | No | Yes (POS only) | Via order flow | No |
| All others | No | No | No | No |

## Integrations

- **Tally**: primary beneficiary — NEXUS agent's nightly Tally JV export reads `ledger_account` from `PaymentEntry` and maps to `tally_ledger`. No change to XML schema; only ledger names change. Test export against sandbox Tally company before going live.
- **Jarvis / ORACLE**: no change needed. ORACLE's sales anomaly detection reads `grand_total`; ledger breakdown is additive metadata.
- **MSG91 / Shopify / Razorpay / Shiprocket**: none — this is POS-internal accounting routing.

## Risk notes

- **POS revenue-critical**: any change to `add_payment()` (orders.py ~2386) touches live billing. The ledger stamp is additive (new field, no validation change) — lowest-risk modification. Still ship behind `FEATURE_LEDGER_ROUTING=1` env flag; reads old data gracefully (missing `ledger_account` → fallback to tender_type as label).
- **Family loyalty pool**: the pooling logic is the highest-risk new piece. It introduces a new `family_loyalty_pools` collection and a new atomic debit path. Must be behind a separate `FEATURE_FAMILY_LOYALTY_POOL=1` flag, defaulting off. The per-customer loyalty system must remain the default; pool is opt-in per customer account (owner enables per family).
- **Tally ledger name accuracy**: if a Tally ledger name in the map doesn't match the Tally company master exactly, the nightly JV will import with a wrong ledger (Tally auto-creates unknown ledgers as "Sundry Debtors"). Do a dry-run export and validate in Tally sandbox before enabling in production.
- **Backward compatibility**: existing orders without `ledger_account` on PaymentEntry must not break finance aggregations. Use `$ifNull: ["$payments.ledger_account", "$payments.method"]` in aggregation pipelines.

## Recommendation

**Build in two sub-phases**: Phase A (ledger routing on PaymentEntry + Tally fix) is a 2-day low-risk win that immediately fixes the Tally export (currently all tenders land as "Cash" in Tally — accounting gap). Phase B (family loyalty pool) is a separate 3-day effort, off by default, enabled only after owner confirms family-pooling policy. Do not block Phase A on Phase B.

## Owner decisions

- Q: Should loyalty points be poolable across family members (e.g., parent's points pay for child's glasses)? | Why: Determines whether to build `family_loyalty_pools` collection + pool debit flow, or simply skip and use individual balances only. | Options: (a) Individual only — skip family pool entirely, use existing per-customer loyalty; (b) Family pool opt-in — head account can enable pooling for their linked family members; (c) Family pool always-on — all customers linked under same mobile/account share one pool
- Q: What are the exact Tally ledger names in your Tally company file for each payment mode (Cash, UPI, Card, Bank Transfer, EMI, Store Credit, Gift Voucher, Loyalty)? | Why: The `tender_ledger_map` Tally names must match your Tally master exactly or the JV import will silently mis-post. | Options: Share your Tally ledger master list — Claude will pre-fill the defaults in the settings screen
- Q: Should the Finance Dashboard show ledger-level cash breakdown to STORE_MANAGER, or keep it ADMIN and above only? | Why: Store managers seeing UPI vs Cash split is useful for shift reconciliation, but some owners prefer to restrict financial detail to admin level. | Options: (a) ADMIN and above only; (b) STORE_MANAGER sees own-store breakdown; (c) All POS roles see their own shift's breakdown