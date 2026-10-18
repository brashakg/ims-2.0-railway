# Codex handover: A11 last-paid price must come from accepted goods

Date: 2026-10-18. Claude remains the main agent.

This branch fixes audit finding A11 only. It is prepared for independent review;
this document does not confirm a merge or deployment. Do not duplicate this fix
on another branch.

## Location and scope

- Worktree: `C:/ims-codex-a11`.
- Branch: `codex/a11-last-paid`.
- Base: `169b501f211930fff8b5fac5875dece4d4560aa8`.
- Production file: `backend/api/routers/vendors/cockpit.py`.
- Tests: `backend/tests/test_last_cost.py` and
  `backend/tests/test_what_we_pay_accounts_only.py`.

The owner cleared unclaimed audit findings for Codex, and subsequently asked for
no visual or workflow changes while staff are working. This correction changes
no screens, labels, routes, permissions, or staff steps. No products are removed.
Only the source of the existing automatically filled price changes.

## Corrected behavior

The shared `last_paid_prices` helper used to fall back to a sent purchase-order
quotation when no accepted receipt supplied a price. For example, catalogue
cost 1,000 and an unreceived quotation 1,200 made both the last-paid display and
a new server-priced PO use 1,200.

The helper now returns only a price supported by an accepted receipt, ranked by
that receipt's acceptance date. If no such price exists, the display has no
last-paid caption and the existing server/form catalogue-cost fallback remains
in use. With neither a supported receipt price nor catalogue cost, the existing
unpriced behavior remains.

The sent-order fallback and its obsolete rejected-delivery bookkeeping were
removed. The helper no longer requires a PO repository to read independently
supported receipt history. The shared `_receipt_price_paid` implementation was
left intact, including its unique booked Accounts-price fallback for units that
arrived unpriced, and its refusal to infer a price from conflicting invoices.

Accepted-receipt filtering, unit-origin checks, zero/unpriced exclusion,
cross-store visibility, manager cost masking, and the catalogue fallback remain
unchanged. Existing recorded POs, receipt costs, invoices, and stock records are
not rewritten by this patch.

## Verification

- Before the production change, both new A11 probes failed: the last-paid
  response contained the unreceived 1,200 quote, and server pricing returned
  1,200 instead of the catalogue cost 1,000.
- Focused last-cost suite after the fix: **39 passed**.
- Updated prior quote-based tests now exercise actual accepted receipts, dates,
  short shipments, rejected deliveries, and quote-only histories. Added checks
  cover accepted zero/missing prices, receipt history without a PO repository,
  and a unique Accounts price recorded after receipt.
- The manager PO integration test now seeds unreceived quotes for products with
  and without catalogue costs, then checks the saved PO prices and hidden cost
  response. Existing shared-caller tests cover forecast drafts, CL drafts, and
  adding manager/accountant PO lines.
- Errors-only pylint for the changed production file: passed.
- Backend import smoke: **1,320 routes loaded**.
- Final combined regression run: **472 passed** (202.57 seconds), including all
  39 last-cost cases, the full Accounts-only pricing suite, four receipt-cost
  reconciliation cases, and two manager/accountant PO-line pricing cases.
- `git diff --check`: passed.

All checks ran in the root virtual environment with inherited credentials
excluded by rebuilding the child environment from system path variables plus
explicit test settings: `ENVIRONMENT=test`, `JWT_SECRET_KEY=test`,
`MONGODB_URL=mongodb://127.0.0.1:27099/ims_codex_a11` with short connection
timeouts, `DISPATCH_MODE=dark`, and `IMS_SHOPIFY_WRITES=false`. The unavailable
local Mongo address fell back to the application's mock startup database;
business test fixtures used isolated in-memory databases/repositories. No live
services or transactions were exercised.

## Remaining limits

- This does not retroactively correct prices previously copied from quotations.
- Accepted receipt history is still queried without a limit so a busy vendor's
  newer unrelated receipts cannot hide a product's real history. Large-history
  performance was not changed or load-tested.
- Existing read-failure behavior remains fail-soft; no new outage policy was
  introduced.
- No production verification, merge, deployment, catalogue optimization, or
  product removal is included.
