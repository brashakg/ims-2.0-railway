# Codex handover: A07 customer-return visibility

Date: 2026-10-18

Status: implemented and locally checked on `codex/a07-return-visibility`.
The owner subsequently authorized Codex review and merge. PR #1208 awaits
fresh CI and the coordinating agent's final merge checks. Not merged or
deployed. Base: `169b501f211930fff8b5fac5875dece4d4560aa8`.

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
- `backend/tests/test_return_visibility_a07.py`: 18 regression/control cases
  against an in-memory collection, including roles, assigned shops, explicit
  filters, no-shop accounts, counts/pagination, and indistinguishable IDs.
- `backend/tests/test_idor_orders_returns_tasks.py`: the existing foreign
  return GET test now expects 404 and compares its body with a missing ID.
  Existing return write and same-shop/admin tests remain unchanged.
- `backend/tests/test_no_shop_login_outside_purchase.py`: the existing area
  manager control now checks the explicit 403 response for a foreign shop,
  and checks that its default list still contains its own shop.
- `docs/handover/CODEX-A07.md`: this handover.

## Verification

- Initial related suites: **84 passed, 39 warnings**, 64.14 seconds:
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
verification gate and PR workflow.

### Final-review correction: null and blank shop membership

The independent merge review found that an area manager's legacy membership
list could include null or blank entries. Passing null into Mongo's `$in`
matches records with no shop attribution; sorting mixed null/string values
also raised an error. An in-memory combined A06/A07 HTTP probe confirmed the
leak for `[None]`, blank-shop visibility for `[""]`, and a 500 for `[None, ""]`.

The area-manager list now retains only nonempty string shop IDs before
building `$in`, using the same validity test already used by the shared
shop-access helpers. Valid IDs are kept verbatim. No shared authorization
helper, other endpoint, return write, frontend or workflow was changed.
Four new cases failed before the fix and cover null, blank, mixed invalid,
and mixed valid/invalid membership against missing/null/blank return shops.

An older no-shop control searched the response text for a shop ID, so the
403 error text accidentally satisfied its old any-shop expectation. It now
asserts the 403 status/body and verifies the area manager's own-shop list.

After the correction, the four original related suites plus
`test_no_shop_login_outside_purchase.py` report **115 passed, 55 warnings**
in 56.77 seconds. Pylint **3.0.3**, installed separately from the shared venv,
passes with the repository's CI error/fatal flags on the changed module.

The corrected return module was then loaded only in memory alongside exact
A06 head `35e0359104c279dfa76bd620a35adfe0ffe7abc7`. **48 HTTP requests passed**
with one authority read each, covering the same old admin token after role/shop
reassignment, all four malformed membership shapes, foreign/missing/blank/null
return IDs, and the existing authenticated-only contract after all roles are
removed. A disabled-account request was additionally rejected with 401.
Compile checks and the full application import passed (**1,320 routes**).
Neither the integration checkout nor the audit checkout was modified.

## Limits

This is a read-access correction, not a repair of historical return records
with missing shop attribution. Those records remain available to admins.
The separate A06 live-authority correction makes later role/shop revocations
take effect on existing sessions; A07 itself applies the shared access rule
to the caller context it receives.
