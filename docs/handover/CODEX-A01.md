# A01: supplier-confirmed credit recognition

## Delivery state

Backend application adapters are connected in this integration checkpoint. A03/A01 will ship in one coordinated draft PR after the approved frontend and parallel RMA adapter are integrated, final tests pass and an independent reviewer checks the exact head. This is not a merge or deployment claim. A12 aging remains separate.

## Owner decisions implemented

Accounts records the supplier's actual credit-note number, date, items and amounts. Only its immutable POSTED event reduces AP and reverses GST that the original bill claimed. Partial actual notes post their actual amounts; one note stays wholly pending until its complete amount has verified allocations. Unknown evidence stays pending with a reason.

Approved staff-facing exceptions are the existing Vendor Returns Issue Credit form, a Cash Flow credit-type choice that directs returned goods to that form, and an extension of the existing RMA credit-note endpoint with actual date/bill evidence while retaining maker-checker. Other adjustments remain SCHEME_CN, DISCOUNT_CN and QUALITY_CN. New manual RETURN_CN/default requests return 422; existing historical manual ledger rows are preserved without a data migration.

The owner also approved payment of a verifiably accepted-only bill after its rejected goods have a confirmed, uncancelled A08 custody/return disposition. Here confirmed means the existing A08 draft-to-created choice has been made; no accounting credit is inferred from that operational request. If rejected units were billed, payment requires a fully confirmed actual supplier credit against that bill. Missing quantities, header-only invoices, conflicting ownership, multiple bills or unreadable records keep payment on hold. Explicit supplier advances have no bill to apply this hold to.

## Evidence and posting

`return_provenance.py` identifies original receipt units and booked invoice lines, never the newest receipt or current master tax. `supplier_credit_evidence.py` verifies stamped recipient registration, original date/number, claimed supplier tax identity, original tax totals and agreement between the existing GST-return and register eligibility rules. Inconsistent legacy dates or flags stay pending. Current vendor/shop data cannot silently repair historical tax.

Rejected custody by itself remains unbilled. A canonical credit for rejected goods requires one unique bill and product line, linked only to that receipt, whose quantity proves all accepted and rejected units were billed. Partial/consolidated/ambiguous evidence stays pending. This does not change the A08 internal request path.

`supplier_credit_confirmation.py` uses unique note identity claims, token/version-fenced events, per-bill caps and per-receipt physical reservations. Original quantity, value and each tax head cannot be reused across competing returns. Partial notes reserve the physical return once. Only one PREPARING-to-POSTED transition exposes the financial fact; no timeout releases an uncertain reservation.

Review regressions cover a PREPARING retry after a different-origin partial note posts, positive actual amount enforcement, lost cleanup recovery using only the same event's prior fenced token, lost POSTED responses, cross-UUID duplicate notes and reopening with the exact original submitted payload. RMA and Vendor Return histories and physical owners have separate kinds. There are no shadow financial Vendor Return documents for RMAs.

## Application adapters

- `vendor_returns.py`: Accounts-only preview/confirmation, original-shop access, price/status CAS fences, masked responses; old status-only credit shortcut cannot generate a supplier note.
- `return_request_document.py`, `rtv_debit_note.py`, `rtv_debit_notes.py`: original-bill internal request, explicit unverified-tax description, canonical-only Tally export. Multiple actual notes export distinct UUID vouchers at their actual dates/amounts. RCM supplier amounts exclude self-paid tax; blocked input tax has no reversal.
- `supplier_credit_reads.py`: one POSTED source feeds bill-allocated AP, negative ITC register rows and original GST heads in the actual note period.
- Finance Cash Flow, supplier ledger/AP, invoice settlement stamps and Purchases report use the same canonical credit rows. GST summary, ITC register/reconciliation and GSTR-3B/portal output consume the same confirmed original heads. A later-month reversal is not zero-clamped when that month has no purchases. Each GST registration remains isolated. Canonical read failure produces an error rather than a partial figure.
- Dedicated confirmation capabilities preserve Accounts-only access and existing parent/module denies. New input-tax totals remain masked for other roles. RBAC matrix regeneration follows the RMA route integration.

The exact frontend API contract is saved externally in `fixes-2026-10-18/A01-CREDIT-CONFIRMATION-API-CONTRACT.md`. PREPARING history exposes `retry_unchanged` plus an exact `retry_payload`; normal pending evidence remains editable with immutable note identity.

## Verification and limits

Before the final review refinements: 446 related tests passed, covering original-bill issue, supplier ledger/roles/scopes, GST summary/register/portal and canonical writer/readers. A subsequent 107 focused cases passed after recovery and real AP reader probes. The final exact integrated run and lint/smoke results will be recorded before PR review. Tests use the external sanitized runner, in-memory data and explicit test environment; no live database, financial operation, POS or deployment was used.

No historical data repair or tax filing has been performed. Original evidence gaps are visible pending records, not automatically repaired invoices. Unknown write outcomes keep their fences until same-request recovery; unsupported contradictory legacy records may need technical reconciliation. Existing manual historical credits are not retrospectively reclassified. This is software and data-scoping work, not tax-filing certification.
