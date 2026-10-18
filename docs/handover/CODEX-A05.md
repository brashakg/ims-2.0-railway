# A05 catalogue identity correction for Claude review

Status: implemented and locally tested; draft PR intended for Claude to review
and merge. Based on main `169b501f211930fff8b5fac5875dece4d4560aa8`.
Worktree: `C:/ims-codex-a05`; branch: `codex/a05-catalog-identity`.

## Behavior changed

`POST /catalog/products` now uses the same identity/GTIN persistence gate as the
canonical product-create service. A spelling-equivalent existing product is
refused with HTTP 409 naming its actual product id and SKU. A concurrent insert
that loses to the unique identity/SKU index is also refused; it cannot continue
and save a catalogue twin under an id whose billing product does not exist.
Distinct colours and eye sizes still create separate billable products.

The shared gate was extracted from `create_product` without changing that
door's behavior. Catalogue retains its existing native twin shape and shared
product/twin id. This is not a migration or cleanup of existing orphan records.

## Files

- `backend/api/routers/catalog.py`: shared gate and structured duplicate reply;
  removed deliberate duplicate-error success handling.
- `backend/api/services/product_master.py`: extracted `persist_new_spine`, used
  by canonical create and catalogue create.
- `backend/tests/test_catalog_identity_gate.py`: five regression/control tests
  using mongomock and actual repository/service/route logic.
- `backend/tests/test_catalog_hardening.py`: both stores isolated per test;
  write-failure doubles support identity reads; old duplicate-success contract
  replaced by refusal/no-catalogue-write assertion.
- `backend/tests/test_unification_9_canonical_product_create.py`: the HTTP
  cross-door test now requires one identity rather than two records.

## Validation

Selected related suite: **186 passed, 5 skipped** (67.89 seconds). Includes the
new catalogue tests, catalogue hardening, product master, canonical create-door
parity, identity spellings, existing TechCherry import tests, and product-barcode
validation. Five real-Mongo integration cases skipped because the intentionally
isolated local Mongo endpoint was unavailable. No production services used.

Errors-only Pylint passed on both changed application files. Backend import
smoke loaded **1,320 routes**. `git diff --check` passed. No frontend files were
changed, and no frontend rebuild was run for this backend-only patch.

The five new tests were also run with the original `169b501f` catalogue module
loaded into the test process from `git show` (no snapshot/source files changed):
the three duplicate/orphan tests failed, while legitimate variants and GTIN
uniqueness controls passed. The corrected implementation passes all five.

Test environment explicitly set `ENVIRONMENT=test`, `JWT_SECRET_KEY=test`, and
Mongo URL/URI to `127.0.0.1:27099/ims_codex_a05` with short connection timeouts.
New tests use isolated in-memory collections with unique identity/SKU indexes.
The concurrency case simulates a competing insertion at the unique-index
boundary; this is not a production concurrency/load test.

## Scope and remaining work

**A05 remains partially open.** Avinash first chose to leave legacy imports
unchanged while fixing catalogue creation. During validation he asked to remove
imports and improve cataloguing; scope clarification for that separate change
is pending. This commit does not modify TechCherry, the catalogue bulk-import
staging/review workflow, or promotion. Do not describe it as fixing every import
door or repairing historical data.

No POS, frontend, live data, Shopify, deployment, or original audit-worktree
changes. `C:/ims-final-audit` was left untouched. Claude remains the lead agent;
this branch must be delivered as a draft PR for Claude's review and merge.
