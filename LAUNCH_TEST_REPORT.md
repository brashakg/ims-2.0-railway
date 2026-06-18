# IMS 2.0 - Pre-Launch Hardening: Living Test Report

Branch: `claude/launch-hardening-2026-06-18` - Base: `origin/main` @ `0f4585f`
(`feat(auth): idle auto-logout #752`). Date opened: 2026-06-18.

This is the living launch report. Phase 0 (read-only static/code audit) is
complete; Batch 1 of confirmed-bug fixes has landed on this branch. Later
phases (live RBAC-with-tokens, ZZ_TEST data scaffolding, POS, e2e) are tracked
in "Next phases" below.

---

## Executive Summary

**Static-health baseline is fully GREEN** on `main` @ `0f4585f`:

| Gate | Result |
|------|--------|
| Frontend `tsc -b` | 0 type errors (exit 0) |
| Frontend `vite build` | 0 errors (exit 0, ~31.7s) |
| Backend `pytest backend/tests` | 6631 passed / 0 failed / 109 skipped (exit 0) |
| Backend route count | 1242 routes (app imports cleanly) |

The known shared-mongo isolation flake did not trip. Bandit is not installed in
the repo `.venv` (MEDIUM+ security scan skipped - not a regression, just a
missing dev tool).

**Phase-0 audit totals: 22 findings - 0 P0 / 1 P1 / 7 P2 / 11 P3 / 3 P4.**
No P0. The single P1 (ORACLE PII-to-LLM leak) is fixed in Batch 1.

**Batch 1 (this branch): 4 findings fixed-pending-merge**, each with a
regression test. All other findings remain open for later batches.

---

## Phase-0 Findings Table

Status legend: `fixed-pending-merge` = fixed on this branch, awaiting PR merge;
`open` = deferred to a later batch.

| ID | Area | Sev | Title | File | Status |
|----|------|-----|-------|------|--------|
| SEC-1 | security | P1 | ORACLE sends unscrubbed customer PII (names) to Claude | `backend/agents/implementations/oracle.py`, `backend/agents/claude_client.py` | **fixed-pending-merge** (`e441d5a`) |
| BUGCLASS-1 | bug_classes | P2 | `bool(collection)` crashes POST /follow-ups/auto-generate (500) | `backend/api/routers/follow_ups.py:389,428,466` | **fixed-pending-merge** (`4618584`) |
| BUGCLASS-3 | bug_classes | P3 | `bool(collection)` silently drops DB-backed product categories | `backend/api/routers/products.py:51,58` | **fixed-pending-merge** (`4618584`) |
| RBAC-1 | rbac | P2 | Hub finance/HR widgets lack a route-level role gate | `backend/api/routers/dashboard_widgets.py` | **fixed-pending-merge** (`41112a6`) |
| RBAC-2 | rbac | P2 | Policy registry drift: analytics endpoints catalogued AUTHENTICATED, routes gate to management | `rbac_policy.py` vs `analytics.py` | open |
| RBAC-3 | rbac | P2 | Middleware not behavior-preserving for finance/hr widget routes (policy stricter than route) | `rbac_policy.py` vs `dashboard_widgets.py` | open (resolved at runtime by RBAC-1; parity assert deferred) |
| RBAC-4 | rbac | P3 | Hub-widget `_store()` helper lacks validate_store_access (cross-store read if active_store_id empty) | `dashboard_widgets.py:42-46` | open |
| RBAC-5 | rbac | P3 | RBAC drift structurally invisible (coverage-lock checks presence only; matrix excludes drifted routes) | `test_rbac_policy.py`, `test_rbac_access_matrix.py` | open |
| BUGCLASS-2 | bug_classes | P2 | Built-and-working Workshop Productivity report never wired into Reports UI | `frontend/.../WorkshopProductivityCard.tsx` | open |
| BUGCLASS-4 | bug_classes | P3 | ~33 orphaned (built-but-not-imported) frontend components - mostly superseded duplicates | `frontend/src/components/` | open (dead-code pass) |
| BUGCLASS-5 | bug_classes | P3 | README documents an aliasing/double-prefix model that no longer matches the code (doc drift) | `README.md` vs `client.ts` | open |
| BR-1 | business_rules | P3 | offer_price > mrp enforced at app layer, not a true DB constraint | `products.py`, `orders.py` | open (informational) |
| BR-2 | business_rules | P4 | Second order path resolves product spine-only, not via spine+catalog resolver | `orders.py:2312` | open (informational, fail-closed) |
| UT-01 | ui_theme | P2 | POS ReceiptPreview modal has no print isolation (modal chrome prints) | `ReceiptPreview.tsx` | open (POS - owner-gated; excluded) |
| UT-02 | ui_theme | P2 | Rupee amounts use locale-less toLocaleString() (wrong lakh/crore grouping; 24 cells PayrollDashboard) | `PayrollDashboard.tsx` | open |
| UT-03 | ui_theme | P3 | More locale-less toLocaleString() (VendorReturns, ApprovalWorkflow, ExpenseBillUpload) | various | open |
| UT-04 | ui_theme | P3 | Receipt grand total printed without digit grouping | `ReceiptPreview.tsx` | open (POS - excluded) |
| UT-05 | ui_theme | P3 | ASCII 'Rs ' prefix used instead of the rupee glyph | `CommissionLeaderboard.tsx` et al | open |
| UT-06 | ui_theme | P3 | DollarSign icon used on rupee (INR) screens | `VendorReturns.tsx` et al | open |
| UT-07 | ui_theme | P4 | GSTInvoice print relies on global chrome-hiding only (no print-area isolation) | `GSTInvoice.tsx` | open (print - excluded) |
| SEC-2 | security | P3 | .env.example does not reconcile with README env-var table / code | `.env.example`, `README.md` | open |
| SEC-3 | security | P4 | admin.py integration GET endpoints lack explicit role gate (metadata only; no secret leak) | `admin.py` | open |

---

## Batch 1 - What was fixed (file:line + how)

### SEC-1 (P1) - ORACLE PII scrub bypass - `e441d5a`
- `backend/agents/claude_client.py`: `call_claude` / `call_claude_json` now
  accept `business_data` + `scrub_level` and forward them to
  `llm_provider.complete`, which runs `scrub_pii` over `business_data` before
  the API call. Defaults to a full `"all"` scrub when `business_data` is
  present (fail-safe).
- `backend/agents/implementations/oracle.py`:
  - `_enrich_with_claude` (was ~840) now passes `business_data={"anomaly": ...}`
    with `scrub_level="customer"` instead of `json.dumps`-ing the raw anomaly
    into the prompt string.
  - `run()` (was ~926) now passes the context dict via `business_data=ctx`,
    `scrub_level="customer"`.
  - VIP-churn summaries (~351, ~385) now reference `customer_id`, never the
    raw customer name (free text is not key-scrubbed at the "customer" level).
- Test `backend/tests/test_oracle_pii_scrub.py`: intercepts
  `llm_provider._call_anthropic` to capture the exact (system,user) payload and
  asserts the customer name/phone/email never reach it (and that the scrubbed
  `[redacted]` marker IS present, proving the data flowed through scrub_pii).

### BUGCLASS-1 (P2) + BUGCLASS-3 (P3) - `bool(collection)` - `4618584`
- `backend/api/routers/follow_ups.py:389,428,466`: `if <collection>:` ->
  `if <collection> is not None:`. The endpoint was 500-ing 100% of the time
  when the DB was up; now returns 200 and generates reminders.
- `backend/api/routers/products.py:51,58`: same `is not None` fix in
  `_get_categories_from_db`; DB-backed categories now actually load (the crash
  was previously swallowed, silently returning []).
- Tests: `test_follow_ups.py::test_auto_generate_returns_200_not_bool_collection_500`
  (PyMongo-like bool-raising fake -> 200 + 1 reminder generated);
  `test_products_categories_bool.py` (categories load from collection + from
  the products-distinct fallback).

### RBAC-1 (P2) - Hub finance/HR widget route gate - `41112a6`
- `backend/api/routers/dashboard_widgets.py`: added
  `_WIDGET_FINANCE_HR_ROLES = ("ACCOUNTANT","ADMIN","AREA_MANAGER","STORE_MANAGER")`
  and changed the 5 handlers (`finance/summary-month`, `finance/gst-status`,
  `finance/pending-reconciliations`, `hr/summary-today`,
  `hr/attendance-compliance`) from `Depends(get_current_user)` to
  `Depends(require_roles(*_WIDGET_FINANCE_HR_ROLES))` (SUPERADMIN auto-passes),
  mirroring the existing `admin/owner-digest` route in the same file.
- Test `test_dashboard_widgets_rbac.py`: mounts ONLY the router (no middleware)
  and asserts floor roles (SALES_STAFF/CASHIER/WORKSHOP_STAFF/OPTOMETRIST) get
  403 while management roles + SUPERADMIN pass the gate - proving the gate is
  defense-in-depth, independent of the middleware.

**Files explicitly NOT touched (owned by other PRs):** `rbac_policy.py`,
`hr.py`, `SetupPage.tsx`, any `print/*` or POS receipt files. RBAC-2/3/5 (the
policy-registry drift + parity test work) are left for the owning PR.

---

## Test results (this branch)

- New/affected targeted run: `test_oracle_pii_scrub.py`,
  `test_dashboard_widgets_rbac.py`, `test_products_categories_bool.py`,
  `test_follow_ups.py` -> **23 passed / 0 failed**.
- Backend smoke import: app imports cleanly, **1242 routes** (matches baseline).
- Broader affected suites (agents / follow-ups / products / dashboard / rbac /
  jarvis) run green (see PR CI for the authoritative full-suite result).

---

## Credentials + data state (for later phases)

- **Creds:** the throwaway SUPERADMIN QA account `zzqa_super` works against the
  live stack. (Password intentionally NOT written here.)
- **Data state:** 1 entity, 1 store, 0 products / 0 orders / 0 customers. The
  `ZZ_TEST` data scaffolding is still pending - most live functional QA (POS,
  orders, returns, finance with real numbers) is blocked until it exists.

---

## Next phases

1. **Live RBAC-with-tokens matrix** - mint real per-role JWTs against the
   deployed stack and assert the 403/200 matrix end-to-end (covers RBAC-2/3/5
   drift detection: add allowed-value parity asserts for analytics + the
   finance/hr widgets so route gate and policy can never silently diverge).
2. **ZZ_TEST data scaffolding** - seed a disposable entity/store/users/
   products/customers/orders set (`ZZ_TEST_*` prefix) so functional QA has real
   data, then a cleanup script to remove it.
3. **POS (owner-approval-gated)** - UT-01 / UT-04 receipt print isolation +
   grand-total grouping; POS is revenue-critical, so these are held until the
   owner signs off on touching POS.
4. **End-to-end (Playwright)** - drive the live app per-module (Hub, POS,
   Orders, Returns, Finance, Reports, Clinical, Inventory, Jarvis) once ZZ_TEST
   data exists; rebase/green the open E2E nightly workflow.
5. **Remaining batches** - currency formatting (UT-02/03/05/06), orphaned-
   component cleanup (BUGCLASS-4), README doc drift (BUGCLASS-5), .env.example
   reconcile (SEC-2), admin GET role gate (SEC-3), offer>mrp DB constraint
   hardening (BR-1).
