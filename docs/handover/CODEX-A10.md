# Codex handover: A10 GSTR-3B registration identity

Date: 2026-10-18

Status: implemented on `codex/a10-gstr3b-identity`; awaiting the coordinating
agent's review, push, CI and merge. This document does not claim deployment.

Base: `169b501f211930fff8b5fac5875dece4d4560aa8`.

## Ownership and scope

The owner supplied Claude's update that no jobs were active and the audit
findings were unclaimed. Codex was assigned A10 in the isolated checkout
`C:/ims-codex-a10`. The primary Claude checkout and the pinned audit checkout
were not edited.

## Problem and result

GSTR-3B already placed purchase input credit using the legal entity's resolved
shop GSTIN, but its header still read the raw `stores.gstin` field. A Pune shop
with a blank or unheld raw number could therefore receive Maharashtra input
credit in its report while the report and downloaded GSTN JSON named no
registration, or named the wrong registration.

`backend/api/routers/reports/gstr3b.py` now reads the report identity from
the existing `gst_itc._itc_store_scope` helper. The report and its existing
GSTN export therefore name the registration used to place input credit.
The returned entity ID is reused by the existing unallocated-bill check.

The existing rule remains: use the shop's own number when its entity holds
it, otherwise the entity's registration for the shop's declared state.
If unresolved or the lookup fails, the header remains empty. No new export
refusal or policy decision was introduced. The existing refusal for
unallocated legacy purchase bills remains in place.

## Files changed

- `backend/api/routers/reports/gstr3b.py`: resolved identity replaces the raw
  header read; no route, tax calculation, permission or response-shape change.
- `backend/tests/test_gstr3b_registration_identity.py`: nine cases execute
  the real report and export against an isolated in-memory Mongo substitute.
- `backend/tests/test_gst_no_shop_login.py`: the existing access-test fixture
  now declares the registrations held by its entity. Its unallocated-bill
  fixture has neither a registration nor a receiving shop, so the same 409
  assertion continues to exercise a genuinely ambiguous bill. Access
  assertions were not weakened.
- `backend/tests/test_reports_sales.py`: its existing GST report fixture now
  supplies the shop's owning entity and held registration. All original tax
  amounts, response status, GSTIN and state assertions are preserved.
- `docs/handover/CODEX-A10.md`: this handover.

## Verification

- Before the source fix: seven regression cases failed and two passed.
- After the source fix: all nine passed, covering absent/blank/invalid/unheld
  raw GSTIN, own held GSTIN, two registrations with distinct input credit,
  unresolved registration, registration-read failure, and no database.
- Errors-only Pylint on the changed application module: passed.
- Backend import smoke: 1,320 routes.
- Related GST/RCM/export/transfer suites: 171 passed (12 warnings), including
  the nine new cases, in 198.22 seconds. Suites:
  `test_gstr3b_registration_identity.py`, `test_gst_itc_vendor_bills.py`,
  `test_gst_rcm_vendor_bills.py`, `test_gstn_export.py`,
  `test_gst_no_shop_login.py`, `test_purchase_reverse_charge.py`, and
  `test_transfer_gst_reporting.py`.

Test commands explicitly used `ENVIRONMENT=test`, a test signing key and
loopback-only Mongo configuration (`127.0.0.1:27099`, `ims_codex_a10`).
The first baseline invocation inherited the repository's global startup
fixture and encountered the refused loopback connection before falling back
to mock mode. The new test module now overrides that startup fixture because
its report calls use their own in-memory database. No production records or
external application actions were used.

No frontend source changed; the coordinating agent owns the final frontend
verification gate and PR workflow.

### CI follow-up: PR #1206

On head `fdb82b5a817e98ea305dddb650cc9e20d4afd528`, backend CI shard 3
(`39098703248`, job `117590353444`) reported **1 failed, 6,113 passed,
5 skipped**. The sole failure was
`TestGSTR3B.test_outward_supplies_use_grand_total` in `test_reports_sales.py`.
Its shop fixture carried a raw GSTIN but no `entity_id` or owning entity;
the corrected resolver therefore returned an empty header. Its exact tax
amount assertions already passed.

The same header failure was reproduced locally before the fixture correction
(1 failed). The fixture now records the registration on the shop's legal
entity. No application code changed, and no assertion was weakened.

After correction: **90 passed, 1 skipped, 86 warnings** in 19.84 seconds
across the entire `test_reports_sales.py`, the nine A10 regression cases,
`test_gst_one_engine.py`, `test_gst_itc_vendor_bills.py`, and
`test_gst_no_shop_login.py`. The existing skip is the one-engine test's
same-state parameter, which is covered separately. The changed test compiles
and `git diff --check` passes. Execution used the external sanitized test
runner with loopback Mongo on `127.0.0.1:27099`; the report fixtures use their
own fake or in-memory collections. A new remote CI run is still required.

## Remaining work

A04 (finance summary pooling across GST registrations) is separate and is
not fixed here. A10 changes the identity on a per-shop GSTR-3B and its export;
it does not change finance summary aggregation or certify all tax reporting.
