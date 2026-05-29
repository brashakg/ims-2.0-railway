# IMS 2.0 — Deep QA Investigation (2026-05-29)

Living report. Autonomous full-app investigation: versions, full test suite, security,
endpoint/role coverage, a fleet of data-creation + analysis agents, Vercel prod, UI/UX.

> Status legend: ✅ verified · 🔧 fixed this session · 🚩 bug found · ⏳ in progress

---

## 0. Method & infrastructure

- **MongoDB 8.0.15** installed locally (docker daemon unavailable) on `127.0.0.1:27017`.
  Railway prod runs **8.3** (rapid-release); 8.0 LTS is functionally identical for this
  app's CRUD/aggregation usage. `pymongo 4.17` supports both.
- **Backend running** via uvicorn on `127.0.0.1:8000` against that Mongo, seeded
  (6 stores, 13 users), `RUN_AGENT_SCHEDULER=false` (also live-tests the new scheduler
  gate), `DISPATCH_MODE=off`, `PORTAL_OTP_DEBUG=true`.
- **Test fleet**: background agents hitting the shared backend, each creating 50+ entries
  and reporting bugs with repro. No browser-automation tool exists in this container, so
  UI/UX is a static-code audit + (separately) Vercel runtime logs; browser-only checks are
  handed off as a per-role checklist.

## 1. Version audit (requested)

| Component | Local | Prod target | Note |
|---|---|---|---|
| Python | 3.11.15 | 3.12 | ⚠️ minor: local test env is 3.11, prod/CLAUDE.md 3.12 |
| MongoDB | 8.0.15 | 8.3 | functionally compatible |
| FastAPI / pydantic / pymongo | 0.136.1 / 2.13.4 / 4.17.0 | pinned == installed | ✅ clean |
| uvicorn | 0.46.0 | unpinned | ⚠️ pin it in requirements.txt |
| React / Vite / TS / Tailwind | 19.2 / 7.3 / 5.9 / 4.1 | — | ✅ modern |

All backend pins match installed (clean install). Two minor deltas above.

## 2. Full backend test suite ✅

**2,140 / 2,140 passing** against real MongoDB 8.0.15 (after the two fixes below).
The suite was 2,139/2,140 on arrival — the single failure was a broken test file, not a
product bug.

## 3. Security 🚩→🔧 (CRITICAL, fixed)

**Unauthenticated DB-seed / account-takeover** — `POST /api/v1/admin/seed-database`
fell back to a **hardcoded secret `"bv-seed-2026"`** (in a public repo) when `SEED_SECRET`
was unset. `?secret=bv-seed-2026&force=users` would drop + re-seed the users collection
with known seed passwords → full takeover. **Fixed** (commit `143327a`): endpoint disabled
unless `SEED_SECRET` is explicitly set on the server; constant-time compare. Verified live:
old secret now returns **HTTP 403**.

> ⚠️ **ACTION REQUIRED ON RAILWAY:** set `SEED_SECRET` on the backend service so existing
> seed/deploy scripts keep working (they must now pass the real value).

## 4. Endpoint authorization audit ✅

Introspected all **829 API routes** for auth dependencies. 33 lacked an obvious auth dep;
categorized:
- **Correctly public:** `/auth/login`, `/auth/refresh`, `/health`, `/webhooks/*`
  (signature-auth), the customer `/portal/*` + `/vendor-portal/*` (token/OTP by design).
- **False positives:** the bare module roots (`GET /inventory`, `/jarvis`, `/clinical`, …)
  are harmless stubs (`{"module":...,"status":"active"}`); the real data endpoints under
  them require `get_current_user`. Confirmed `/jarvis/status` etc. are `require_superadmin`.
- **One real hole:** `seed-database` (§3, fixed).
- ✅ **Jarvis SUPERADMIN-only** rule holds on the real endpoints.

## 5. Test repair 🔧

`test_marketing_referrals.py` was missing its entire header (lost in #340) → `NameError`.
Restored the standard TestClient + fake-DB + signed-JWT header (commit `b60b234`).

## 6. Session feature work (context)

Branch `claude/intelligent-noether-5QGMa` also carries this session's 5 features + doc
rebuild + 2 QA fixes, all verified (full suite green, tsc 0, vite build 0):
bug-fixes (DND→IST, scheduler lock), cheap-UI, HR attendance, AI proposals, customer portal.

---

## 7. Test-agent fleet — executed

15 agents run against the shared live backend (methodology saved as the
`app-test-fleet` skill — full 56-agent catalog there). Data created:
**53 products, 76 prescriptions, 50 orders, 153 stock units, 50 workshop jobs**,
plus expenses/customers/queue (some wiped mid-run — see note). Every domain
workflow was exercised end-to-end; the bugs are in §9.

Wave 1: Catalog · Orders/POS · Inventory · Clinical/Rx · Workshop · AI/Jarvis ·
UI/UX-Colour-Font · Web-research. Wave 2: role-matrix ×6 (all 12 roles) +
Finance.

> ⚠️ **Fleet-coordination lesson:** one agent's `seed-database?force=` call wiped
> products/customers/orders mid-run, and another's destructive cleanup deleted
> shared docs. Agents on a shared DB must NOT force-seed or `delete_many`. Future
> runs: give each agent its own DB/namespace. (Baseline stores+users survived.)

## 8. Vercel production ✅

- Latest **production deploy `READY`** (#343, `d7a99ac`); live site serves the SPA
  (`200`, theme-color `#B42318` = BV red). Static SPA → no Vercel runtime logs.
- My branch builds **green** on Vercel after the `marketing_consent` fix (earlier
  `b66ef74` was `ERROR` — confirms the `tsc -b` build was genuinely red).
- 🔭 **Parallel work on `main` duplicated 2 of my fixes** (#341 broken-marketing-test,
  #342 marketing_consent) — my branch forked an older `main`, so a future merge will
  see duplicates (harmless, same intent).

## 9. Consolidated findings (prioritized)

🔧 = fixed & pushed this session · 📋 = documented, fix pending

### 🚨 CRITICAL
1. 🔧 **Unauthenticated DB-seed** (`main.py`, hardcoded `bv-seed-2026`) — fixed `143327a`.
2. 📋 **Systemic missing authorization (the #1 finding).** Many endpoints gate only on
   `Depends(get_current_user)` — no `require_roles` / `validate_store_access`. The
   primitives exist (inventory/finance use them; INVESTOR block + audit immutability
   work) but orders/stores/reports don't apply them. Concretely:
   - **Cross-store read+write on `/stores/{id}`** (`stores.py:347,361,395,455`): a CASHIER
     reads another store's revenue/staff-PII and **`PUT`s** its name + geo-fence. *No
     role gate, no scope check.*
   - **Cross-store leak on `/orders`** (`orders.py:411-560,1290`): Area/Store Manager &
     cashier read any store's orders by passing `?store_id=`. Mirror inventory's
     `validate_store_access`.
3. 📋 **Discount-cap bypass (revenue):** (a) order-level `cart_discount_percent`
   (`orders.py:1055`) and (b) `POST /orders/{id}/items` (`orders.py:1363`) are never
   cap-checked — any role applied 80-90% off. (c) 🔧 `STORE_MANAGER` was in the
   `is_admin` cap-bypass (`orders.py:864`) — fixed this session.
4. 📋 **Catalog back-door** (`catalog.py POST /products`): `offer_price > mrp` NOT blocked
   (canonical `/products` blocks it); GST/caps not enforced. Two divergent write surfaces.

### 🔴 HIGH
5. 📋 **No role gate on `POST /orders` /customers /expenses /tasks** — ACCOUNTANT,
   OPTOMETRIST, CATALOG_MANAGER, WORKSHOP_STAFF, CASHIER all created POS orders.
6. 📋 **Self-approval** of expenses & advances (`expenses.py:852,1184`) — §7 violation
   (HR week-off swap *does* guard this; expenses doesn't).
7. 🔧 **Rx 0.25-step unenforced** — `+1.30`/`+0.10` accepted. Fixed `6f3c302`.
8. 📋 **Inventory `quantity` field never set** by `add_stock` → low-stock, sell-through,
   overstock, non-moving, stock-count-scan all read 0 (one root cause, 5 endpoints).
9. 📋 **Stock transfers move no actual stock** (`transfers.py` ship/receive) — §5 violation;
   a "completed" transfer leaves both stores' on-hand wrong.
10. 📋 **No e-invoicing / IRN generation** — if either entity's turnover ≥ ₹5 cr, B2B
    invoices legally need IRN+QR. Only settings fields exist (no IRP/GSP). Compliance.

### 🟠 MEDIUM
11. 🔧 Jarvis root `/jarvis` ungated → fixed `6f3c302`.
12. 🔧 `agents.py` Collection-truthiness → 3 observability endpoints empty → fixed `6f3c302`.
13. 📋 Jarvis activity feed: ISO-string vs BSON-datetime query → under-reports (`agents.py:807+`).
14. 📋 `/workshop/overdue` always 0 — string-vs-datetime (`workshop_repository.py:46`).
15. 📋 `/inventory/expiring` always 0 — string-vs-datetime (`product_repository.py:106`).
16. 🔧 MEGAPHONE didn't filter `marketing_consent` (Rx/birthday scans) → fixed `6f3c302`.
17. 🔧 `/validate` false-flagged `add:"0"` Rx → fixed `6f3c302`.
18. 🔧 Inline CYL `±10` vs canonical `±6` → reconciled `6f3c302`.
19. 📋 Reports-admin (`valuation`, `staff/ranking`, `sales/by-salesperson`) reachable by
    lvl-6 + no store scope (`reports.py:345,408,522,1168`).
20. 📋 `UserCreate.discount_cap` defaults to **10** for ALL roles (`users.py:34`) — a
    cashier created via the API silently gets 10% discount power.
21. 🔧 `StatusBadge` `archived` dark-on-dark → fixed `afe9080`.
22. 📋 Dead nav `/catalog/inventory` → 404 after product save (3 files: AddProductPage:187,
    QuickAddPage:236, RapidGridPage:429).
23. 📋 Light-red-on-light figures escape the CSS override (WorkshopPage:1045/1080,
    ReportsPage:1180); dead dark-mode subsystem (491 `dark:` + unused ThemeToggle).

### ✅ Verified WORKING (no action)
GST **inclusive** math (mixed 5%/18% correct to the paisa) · partial-payment/EMI/credit ·
order lifecycle · per-item discount cap · Rx range validation (all out-of-range rejected) ·
workshop pipeline + QC pass/fail/rework + KPIs · **AI change-proposal §8 loop (all paths:
reversible auto-exec + audit, advisory stays advisory, reject)** · 8/8 agent roster + toggles
+ Claude fail-soft · **INVESTOR write-block (26/26 blocked)** · **audit-log immutability** ·
inventory counts/shrinkage/FEFO/power-grid · **GST 2.0 rates correct & current** ·
clinician-set Rx validity + separate CL Rx model · full backend suite **2139/2140**.

### Recommended next step
The CRITICAL #2/#3/#5 cluster shares one root cause — **apply `require_roles` +
`validate_store_access` to the orders, stores, and reports routers** (mirroring the
inventory/finance routers that already do it), and cap `cart_discount_percent` /
add-item discount like the per-item path. It's a coherent ~1-day change touching
revenue-critical POS, so it warrants its own focused, fully-tested PR rather than a
rushed edit. Exact file:line locations are above.

## 10. Fixes shipped this session (12)
`143327a` seed-secret · `b60b234` marketing test · `6f3c302` (Rx step, CYL, validate-ADD,
MEGAPHONE consent ×2, Jarvis gate, agents truthiness ×3) · `afe9080` StatusBadge ·
+ STORE_MANAGER cap-bypass (this commit). All verified against the full suite + the
live running backend.
