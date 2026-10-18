# A01 RMA supplier confirmation adapter

Status: implementation for coordinator review; not deployed or merged by this author.

## Scope and ownership

This change extends the existing RMA credit-note API to the shared canonical supplier-credit service. It is based on the A01 foundation `f8e4dcf8151a41794d36e40faf99aa1f9b8077f8` plus main/A08 `3f267389846fbe2e54eaf89d74ee0435883eafc0`. The coordinator integrates it with the remaining A01/A03 adapters and current main. No POS, UI, audit checkout, original checkout, live database, deployment or supplier-bill mutation is part of this change. No existing RMA credit-entry frontend caller was found; no screen was invented.

Author owns `api/routers/vendor_rma.py`, `api/services/rma_supplier_credit.py`, narrow writer guards in `api/services/vendor_rma.py`, and RMA tests. Changes to the shared confirmation service are limited to kind dispatch, positive-only parsing with an explicit zero parser opt-in, and retaining a failed worker's fenced token for recovery. The canonical service owner integrates its other reservation/evidence changes independently.

## API contract

`POST /api/v1/vendor-rma/{rma_id}/credit-preview` accepts optional `confirmation_id` and `source_selections`. It delegates to the shared original-bill preview, with the RMA adapted in memory as `return_kind=RMA`. It provides the source keys, choices, caps, pending reasons and existing confirmation history used by the shared actual-note contract.

`POST /api/v1/vendor-rma/{rma_id}/credit-note` now requires the supplier's actual `credit_note_number`, `credit_note_date` (YYYY-MM-DD), `received_amount`, and UUID `idempotency_key`. It accepts `source_selections`, `allocations` (source key and taxable/CGST/SGST/IGST amounts), notes, and the existing approval request/token fields. Number-and-amount-only clients receive a clear validation error for the missing actual date and retry identity. Reuse the same UUID and immutable note identity on retries.

Both routes retain Accounts/Admin/Superadmin handler gates and shop masking. The integration owner adds the dedicated `vendor-rma:confirm-credit` capability, parent `vendor-rma:write`, policy rows and central RBAC matrix. Until that integration, this isolated branch is not a standalone release candidate.

Positive confirmations use the shared canonical writer. Only its immutable `POSTED` event is a supplier-credit accounting fact. Pending original bill/tax evidence cannot change RMA received money. There are no shadow `vendor_returns` documents, invented note dates or tax values. Original vendor bills remain unchanged. The RMA summary applies each posted confirmation once, preserving older received totals and an already closed lifecycle on retry.

## Approval and recovery

The existing received-amount/variance thresholds, priced-before-credit rule, second-person rule, request action, shop, RMA and amount binding remain. A durable binding in `rma_supplier_credit_approvals` reserves an approval request for the immutable note UUID, date, normalized number, amount, recorder and current RMA expectation/received balance before consuming it. A consumed approval can recover the same request after a lost response; it cannot authorize a new note, actor or changed monetary snapshot. Approval tokens are not copied into this binding.

RMA posting takes an exact-document preimage fence. Pricing, reject, close and legacy credit writes cannot cross it. Known rejected posting releases its own token; if release fails, retry may replace only the prior token recorded on that same canonical event. There is no TTL unlock or uncertain-event deletion. Canonical posted-note recovery repairs the summary idempotently. Close additionally matches the exact expected, received and variance values it read, so a completed concurrent partial credit cannot cause a stale write-off.

## Zero-credit boundary

The owner explicitly retained the operational zero-credit acknowledgement and subsequent write-off workflow. A zero request still requires the actual acknowledgement date/number, UUID, existing pricing and approval/variance checks. It is stored atomically only in that RMA's `credit_notes`, identified by `operational_acknowledgement_id`, and may move DISPATCHED to CREDIT_RECEIVED without increasing received money. It creates no canonical event, bill reservation, AP/GST credit or positive confirmation and cannot release a billed-rejection payment hold. Nonempty credit allocations are refused. Repeated identical acknowledgements return the original stored entry, including after a lost response or closure. Acknowledgements cannot be changed into positive credit by reusing the UUID.

The canonical writer always calls the parser with its default positive-only rule; the zero opt-in is used only by the RMA adapter's separate operational path. Existing legacy note-number duplicates remain refused.

## Validation and integration notes

- First focused canonical/RMA run: 47 passed. Zero/retry extension: 55 passed.
- Final nine-module related suite: 444 passed, 5 warnings, 123.40 seconds. Modules: RMA adapter, N4 RMA, supplier money recorded/masked, return prices, canonical confirmation, provenance, evidence and readers.
- Final exact-source RMA module: 39 passed, 4 warnings, 29.33 seconds, after the final idempotent response correction.
- Pinned CI Pylint 3.0.3, errors/fatal only: passed, 10.00/10.
- Sanitized backend import smoke: 1,315 routes.
- Forced frontend TypeScript build and Vite production build: passed; Vite built 2,997 modules in 59.83 seconds. Existing CSS comment optimization warning remains; no frontend source changes.

All Python checks use the external sanitized loopback-only runner and in-memory Mongo fixtures. No live services are used. Logs are under the coordinator's external `fixes-2026-10-18` directory.

Legacy RMA tests now supply actual date/UUID and explicit original receipt/bill fixtures; they retain role, shop, second-person, amount and lifecycle assertions. RMA fixtures use mongomock where the older small fake did not implement the new canonical CAS operators. The two-note approval test now credits 60,000 plus the remaining original 40,000 against a 100,000 original bill, instead of asserting an unsupported 120,000 credit. New tests independently cover pending evidence, unmodified original bills, no shadow returns, cross-kind UUID rejection, zero exclusion, lost approval/summary replies, changed approval retries, both zero/positive race orders and stale close/write-off prevention.

Shared test-file changes are RMA-only in `test_supplier_money_recorded_by_accounts.py`, `test_supplier_money_masked_for_managers.py` and `test_return_prices_are_accounts_input.py`; the canonical integration owner separately updates VendorReturns/RTV fixtures and assertions. The test-only `rma_credit_fixtures.py` supplies one proven original zero-tax bill for single-line RMA legacy tests; it is not application logic.
