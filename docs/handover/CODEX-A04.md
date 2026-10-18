# A04: finance GST summary by registration

Status: draft for independent review; not merged or deployed by this change.

Base: `eab9fd62df9fea26a31087763e56a2d2d686badb` (`origin/main` when this branch was created).
Branch: `codex/a04-gst-registration-summary`.

## Owner approval and visible behavior

The owner approved calculating the existing finance GST summary separately for each GSTIN and showing a clear error when registration data is missing or ambiguous, until Accounts corrects it. Successful screens, response keys, role restrictions and staff steps stay the same. The only new presentation is the approved error in the existing GST tab; no new screen, control or workflow was added.

Example: Maharashtra output tax of Rs 500 and Jharkhand input credit of Rs 500 previously produced cash payable of zero. The summary now reports Rs 500 cash payable and Rs 500 credit carried forward. Two shops belonging to the same verified registration still combine within the caller's existing visibility scope.

## Implementation

- `backend/api/routers/finance/gst.py` groups the existing sales, eligible supplier-bill credit and reverse-charge tax by verified entity/GSTIN, calls the existing `net_cash_due` calculation for each group, then totals cash payable. The existing eligible-bill, period, held-online-order, tax-head and reverse-charge rules remain in place.
- `backend/api/services/gst_registration.py` uses the established shop GSTIN resolver and the existing legacy-bill receiving-evidence rule. A valid stamped receiving GSTIN held by its entity remains authoritative. Unstamped bills use unanimous verified shop/receipt ownership, or the entity's sole registration when there is no receiving evidence. A legacy bill without an entity can infer it only from unanimous receiving evidence. Missing, foreign, conflicting or ambiguous registrations produce HTTP 409, rather than a guessed calculation.
- `backend/api/routers/reports/gst_itc.py` calls the extracted legacy-bill helper. Its existing allocation query, return policy and report output remain unchanged.
- Source/registration read failures produce HTTP 503. The state lookup helpers in `finance/_shared.py` have an opt-in strict mode used only by this summary; other callers retain their existing behavior.
- `frontend/src/pages/finance/FinanceDashboard.tsx` renders the API error message and clears the unsuccessful summary instead of hiding the error. Existing summary fields and the successful panel are unchanged.

Admin/Superadmin retain their existing all-shop scope. Accounts retain their own-shop scope; sharing a GSTIN does not grant visibility to another shop. A no-shop login still gets an empty scoped summary with readable data. No customer, order, bill or master data is written or migrated.

## Regression evidence and verification

The first 20 new A04 cases were run against the unchanged implementation: **13 failed and 7 passed**. Failures included cross-registration set-off, ambiguous receiving ownership and source-read failures. Those failures were fixed in production code. The new suite also covers same-registration shops, reverse charge, booked registration precedence, legacy receipt/sole-registration inference, accountant scope, date handling and unchanged response keys.

Existing fixtures in four test modules were given their missing shop/entity/GSTIN ownership so they model verified registrations; their money and period assertions were preserved. The prior no-database zero-skeleton assertion was changed to the explicitly approved 503 error expectation.

Final local verification:

- **394 backend tests passed** (51 existing warnings, 72.07 seconds). The command below includes the new registration cases; existing source/date/role/scope/tax-head/RCM tests; A10 identity tests; receipt-without-PO behavior; GST exports and transfer reporting; and the online held-sale exclusion integration.
- **24 frontend tests passed** across `GstInputTaxAccounts.test.tsx` and `GSTPanelReverseCharge.test.tsx`, including the approved refusal for Account/Admin/Superadmin. Command: `vitest run src/pages/finance/__tests__/GstInputTaxAccounts.test.tsx src/pages/finance/__tests__/GSTPanelReverseCharge.test.tsx --pool=threads --maxWorkers=1`. The first default worker-pool attempt timed out at startup under concurrent load; the thread-pool run passed without changing tests or timeout limits.
- Forced TypeScript build and Vite production build passed on the modified frontend. Vite reported the existing CSS comment-token warning.
- CI-pinned Pylint **3.0.3**, errors/fatal only with the repository's Pydantic exclusions, passed for all four changed production Python files.
- Backend import smoke passed: **1,314 routes**. `git diff --check` passed.

Backend test command (run through the sanitized runner described below):

```text
python -m pytest backend/tests/test_gst_summary_registration.py backend/tests/test_gst_summary_itc_source.py backend/tests/test_gst_summary_split.py backend/tests/test_gst_tab_one_shop_rule.py backend/tests/test_gst_no_shop_login.py backend/tests/test_gst_input_accounts_only.py backend/tests/test_finance_aggregation_correctness.py backend/tests/test_finance_ap_scope_round2.py backend/tests/test_purchase_reverse_charge.py backend/tests/test_tax_itc_tds.py backend/tests/test_ist_typed_range_windows.py backend/tests/test_gst_itc_vendor_bills.py backend/tests/test_gstr3b_registration_identity.py backend/tests/test_online_fulfillment_route.py::test_every_gst_view_leaves_out_what_the_returns_leave_out backend/tests/test_receipt_without_po.py backend/tests/test_gstn_export.py backend/tests/test_transfer_gst_reporting.py -q --disable-warnings --tb=short
```

Verification uses an external sanitized runner which rebuilds the environment with `ENVIRONMENT=test`, a test JWT key, loopback-only MongoDB (`127.0.0.1:27099`), disabled external writes and mocked/in-memory test services. No production credentials or live data are used. Frontend verification uses the bundled Node 24.19.0 runtime and the existing dependency installation.

## Separate defect retained for follow-up

The **GST Reconciliation by Entity** table in the same tab is not corrected by A04. It is a separate endpoint and calculation:

- `backend/api/routers/finance/gst_crosscheck.py:116-129` reads `purchase_orders` for input tax, rather than booked supplier bills.
- `backend/api/routers/finance/_shared.py:339-400` groups by legal entity and subtracts aggregate input credit, including `net_payable = collected - input_credit`; it does not calculate cash set-off per GSTIN.
- `frontend/src/pages/finance/FinanceDashboard.tsx:593-613` still renders that separate reconciliation response.

Consequently that table can disagree with the corrected summary cards. Its source selection and entity-level calculation remain an open defect, explicitly excluded from this narrow approved change. This PR must not be described as correcting the entire GST tab or certifying a return.

## Limits and handover

This is a calculation/data-scoping correction using existing application rules, not tax-law certification or a review of actual filings. No live GST filing, production database operation, POS change or deployment validation was performed. Existing missing or ambiguous master data can now block the summary until Accounts resolves it; this is the owner-approved outcome. Review this exact PR head independently and require the repository's checks before any merge.
