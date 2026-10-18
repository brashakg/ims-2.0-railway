# A01: supplier-confirmed credit recognition

## Current delivery state

Isolated evidence/preview foundation only. No application route, posting writer,
AP/GST reader or accounting export has been switched yet; A01 remains open.
A03 and A01 deliver together in one coordinated draft PR after A08 merges.
Root implements the owner-approved form on the existing Issue Credit action.

The owner approved posting only when Accounts records the supplier's actual
credit note, with the original bill's claimed GST only. Partial supplier notes
post only their confirmed amount. Missing evidence stays pending. The approved
form records number/date, credited items/amounts, and explicit original-bill
selection when the physical origin is ambiguous. This is the sole A01 workflow
exception; other screens/steps remain unchanged.

## Evidence boundary

`supplier_credit_evidence.py` uses the A03 original-bill resolver, verifies the
stored recipient GSTIN against its legal entity, and requires original bill
number/date. Claimed tax also requires a valid stored supplier GSTIN (except
reverse charge) and original line sums matching the stored tax headers used by
the GST readers. Missing or inconsistent legacy identity remains pending with a
precise reason; current vendor records never repair historical tax silently.
Blocked input tax retains its commercial supplier amount without an ITC reversal.
RCM self-paid tax is excluded from the supplier commercial total.

The preview differentiates requested value from original caps, supports explicit
receipt/bill choices and physical-unit labels, restricts original bills to the
caller's shop access, and reloads pending or POSTED confirmations after a lost
response. Pending/preparing events do not reduce credit figures. Database read
failures propagate instead of becoming zero figures or missing-evidence answers.

The agreed API contract is recorded externally in
`fixes-2026-10-18/A01-CREDIT-CONFIRMATION-API-CONTRACT.md`. Confirmation UUID equals
the submitted idempotency key. Number/date/amount are immutable for an existing
pending note; posted events are wholly immutable. The writer must also deduplicate
actual supplier note identity across UUIDs and reserve original quantities and
values before a single canonical POSTED event is visible to AP/GST/Tally.

## Validation so far

32 new in-memory preview/evidence cases and all 62 A03 resolver cases pass (94
total). They cover missing/malformed original registration, mismatched tax
headers, blocked credit, RCM, exact original-unit choices, shop access, ambiguous
origins, unknown expected amounts, partial-history preload, lost-response
recovery, changed source fingerprints and read failures. No live services,
database, POS or financial operations are used.

## Remaining work and limits

Canonical posting, concurrency reservations, note deduplication, API role/scope
checks and RBAC rows, period locks, all AP/GST readers, printable-request labels
and the Tally gate must still be integrated and tested. The application-level
adapter regressions remain uncommitted until the A08-safe adapter is ready.
The current preview is read-only and does not reserve money; POST must recheck
evidence and cumulative caps. This is software/data-scoping work, not tax-filing
certification. A12 is separate aging/report correction, with no bill-allocation UI.
