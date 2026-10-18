# A12 - supplier advances and explicit invoice settlement

Prepared in `codex/a12-unallocated-advances`, isolated checkout `C:/ims-codex-a12`,
from reviewed integration `7158e53ae1317abb74327c19bf9e3b03a45141bf`.
Release ordering remains A01/A03 first, then only these A12 commits transplanted
onto the resulting main. No deployment or live database verification is claimed.

## Owner decision and resulting behavior

Supplier advances without a bill remain allowed. They reduce the supplier's net
balance, but remain separate from settled bill payments until explicitly allocated.
For an invoice of 1,000 and an unapplied payment of 400:

| Figure | Result |
| --- | ---: |
| Unsettled invoice / aging | 1,000 |
| Unapplied credit / advances | 400 |
| Signed supplier net balance | 600 |

Allocating that same payment to the invoice yields invoice 600, unapplied 0, net
600. An overpayment allocated to invoice A stays unapplied beyond A's total; it
does not implicitly settle invoice B. Unallocated manual rebates/credits follow
the same rule. Cash plus TDS retain their existing gross settlement meaning.

## Change for Claude / the next maintainer

- `ap_engine.build_aging` no longer consumes unallocated money against oldest due
  bills. Its items, buckets, `total_outstanding`, `owed`, and legacy `net_payable`
  describe gross unsettled invoices. `advances` and `unallocated_credits` retain
  unapplied payments/credits and excess explicit allocations. Grouped `balance`
  remains the signed supplier net, including negative balances.
- An absent/blank bill ID cannot prove an allocation. The same check protects
  per-bill balance, aging's known-bill set, and `bill_as_of` including its future
  payment projection. Valid identifiers still match exactly; no data migration.
- Owner dashboard aging, overdue/due windows, forecast bill events and survival
  bill priorities retain unpaid invoices even if supplier net is zero. Dashboard
  `net_position` explicitly keeps positive supplier net balances, so one
  supplier's advance never reduces another supplier's debt.
- Purchases report keeps its ledger-derived net `owed` and totals. It retains a
  supplier row with an unsettled invoice despite zero current-month/net figures;
  next due follows the invoice. Undatable open invoices also keep their row.
- Vendor Payments summaries and supplier ledger balances remain **supplier net**;
  AP aging and dashboard invoice buckets are **gross unsettled invoices**. They
  intentionally differ when unapplied money exists. Existing response names and
  shapes are retained. Frontend changes are API comments only.
- A01's canonical POSTED credit allocations still reduce their original bill.
  PREPARING/PENDING events and an internally issued debit-note request do not.
  Canonical readers, access checks, shop/as-of rules, mirrors, stored payment
  entry, and chronological ledger arithmetic are unchanged.

No staff control, screen, payment-entry step, advance-allocation API, historical
record, POS logic, or GST posting behavior changes in A12. The existing payment
API still accepts no bill and validates a bill when supplied. This change does
not add a workflow for allocating an already-recorded advance later.

## Evidence and checks

All local Python checks use the external sanitized `run_checks.py`: test-only
JWT/environment, unavailable localhost Mongo port 27099 with short timeouts,
Redis off, dispatch dark and Shopify writes disabled. Focused fixtures are
synthetic/mongomock; no production services or application lifespan.

- Before production edits, `test_supplier_advances_a12.py`: **17 failures and 1
  passing control** on the original source. Failures showed the invoice/advance
  collapse, blank-ID false allocation and missing zero-net supplier rows.
  External log: `fixes-2026-10-18/a12-before.log`.
- After the correction: **20 final focused cases passed** (4 warnings,
  17.68 seconds, `a12-focused-final.log`), including bill-less payment entry,
  legacy shop/date rules, critical survival priorities, and POSTED versus
  unposted canonical supplier credit.
- Initial eight-module related run: **256 passed, 2 skipped** and five obsolete
  Purchases assertions requiring automatic allocation. The skipped tests need
  optional real Mongo. Those assertions now check separate gross/net/unapplied
  values while retaining their date and scope controls.
- Additional impact search found the superseded assumptions in rebate,
  money-shop, Accounts-only aging and dedicated finance tests. Their arithmetic
  expectations were updated, retaining access/isolation/net controls.
- Pinned Pylint **3.0.3**, changed production modules, CI E/F configuration:
  **passed, 10.00/10** (`a12-ci-lint.log`).
- Sanitized backend import smoke: **1,317 routes** (`a12-backend-smoke.log`).
- Forced TypeScript build and Vite build: **passed**, Vite 72 seconds
  (`a12-frontend-build.log`). Dependency lock matched the integration checkout;
  no dependency manifests changed.

The first expanded run on the older integration base also exposed five existing
direct-credit test fixtures that still defaulted to RETURN_CN. A01's final head
`1f3e037859fe60bb2573f51c46bded88ddc5535a` already changes those fixtures to the
permitted QUALITY_CN. That separate correction is not duplicated in A12; it
must be present after transplant before final PR checks.

Independent exact-head review and final post-transplant checks remain release
gates. Local tests establish synthetic behavior, not live ledger reconciliation.
