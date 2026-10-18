# Codex handover: A07 customer-return visibility

Date: 2026-10-18

Status: implemented and locally checked on `codex/a07-return-visibility`,
awaiting the coordinating agent's review and draft PR for Claude. Not merged
or deployed. Base: `169b501f211930fff8b5fac5875dece4d4560aa8`.

## Ownership and scope

The owner supplied Claude's update that no jobs were active and these audit
findings were unclaimed. Codex used the isolated `C:/ims-codex-a07` checkout.
The primary Claude checkout and `C:/ims-final-audit` were not changed.

This correction only changes GET visibility for customer returns. No return
creation, refund calculation, exchange, restock, POS write, frontend screen,
or workflow was changed.

## Problem and result

The return list treated AREA_MANAGER as able to see every shop. The return
detail also answered 403 for an existing return in another shop but 404 for
a missing ID, disclosing whether a guessed record existed. A record without
shop attribution could fall through to the caller's active shop.

The two read endpoints now use the existing shared shop-access helpers:

- `GET /returns`: ADMIN and SUPERADMIN retain all-shop access; an area manager
  gets only assigned shops by default; other roles retain their active-shop
  default. An explicit shop filter must be within the caller's permitted
  shops. Counts and pagination use the same filtered query.
- `GET /returns/{id}`: a store-scoped caller receives the same 404 and
  `Return not found` body for a foreign-shop, missing, or unattributed return.
  Admins retain access to unattributed records for investigation.
- A non-admin with no shop gets an empty list and cannot name another shop
  to widen access.

## Files changed

- `backend/api/routers/returns.py`: shared scope helpers for the list and
  shared existence-hiding helper for the detail.
- `backend/tests/test_return_visibility_a07.py`: 14 regression/control cases
  against an in-memory collection, including roles, assigned shops, explicit
  filters, no-shop accounts, counts/pagination, and indistinguishable IDs.
- `backend/tests/test_idor_orders_returns_tasks.py`: the existing foreign
  return GET test now expects 404 and compares its body with a missing ID.
  Existing return write and same-shop/admin tests remain unchanged.
- `docs/handover/CODEX-A07.md`: this handover.

## Verification

- Related suites: **84 passed, 39 warnings**, 64.14 seconds:
  `test_return_visibility_a07.py`, `test_returns.py`,
  `test_returns_exchange_closed.py`, and `test_idor_orders_returns_tasks.py`.
- Baseline counterexamples: **8 failed, 6 passed**, 13.66 seconds. The original
  `returns.py` from the base commit was loaded in memory before running the
  14 new cases. Failures reproduce the area-manager list leak, unchecked
  explicit foreign-shop filters, and distinguishable or unattributed return
  reads. Six control cases remain passing. No checkout files were replaced.
- Errors-only Pylint for `backend/api/routers/returns.py`: passed.
- Compile checks for the changed application/test files and application
  import smoke: passed; **1,320 routes** registered.
- `git diff --check`: passed.

Checks use the external sanitized `run_checks.py` helper with an explicit
test-only child environment, loopback Mongo on `127.0.0.1:27099`, outbound
dispatch dark, and Shopify writes disabled. The new tests use mongomock and
override the global app-startup fixture. Related tests retain their existing
test fixtures. No production data or services were used.

No frontend source changed. The coordinating agent owns the final frontend
verification gate and draft-PR workflow.

## Limits

This is a read-access correction, not a repair of historical return records
with missing shop attribution. Those records remain available to admins.
The separate A06 live-authority correction makes later role/shop revocations
take effect on existing sessions; A07 itself applies the shared access rule
to the caller context it receives.
