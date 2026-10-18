# A01: supplier-confirmed credit recognition

## Current delivery state

Isolated evidence/preview, canonical writer and shared read foundations. No
application route, AP/GST report or accounting export has been switched yet;
A01 remains open.
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

134 in-memory foundation cases pass: evidence/preview, canonical writer, shared
AP/ITC readers and the 62 A03 resolver cases. They cover missing/malformed original registration, mismatched tax
headers, blocked credit, RCM, exact original-unit choices, shop access, ambiguous
origins, unknown expected amounts, partial-history preload, lost-response
recovery, changed source fingerprints and read failures. Canonical writer cases
also exercise a competing worker winning the bill CAS, a unit or receipt reused
through a different bill, successive partial notes, immutable posted payloads,
cross-UUID supplier-note claims, closed periods, lost reservation/write replies,
and fencing an old writer before reserved capacity can be reused. The read
adapters feed the real AP ledger and ITC register; later-period reversals remain
negative when that month has no new input credit. Pinned Pylint 3.0.3 errors/fatal
checks pass. No live services,
database, POS or financial operations are used.

## Remaining work and limits

`supplier_credit_confirmation.py` records the actual note pending or atomically
POSTED in `supplier_credit_confirmations`. Bill guards cap original line
quantities/value/tax; receipt guards also prevent the same goods being credited
through another bill. A return reserves its physical quantity once across partial
notes. Uncertain operations retain their event token and capacity; an identical
UUID resumes them. A known rejection fences POST permission before a future
writer may reclaim capacity. No elapsed timeout frees uncertain money. Supplier
note identity claims prevent duplicate posting after reopening a lost-response
form with a new UUID; unknown registration uses a conservative supplier-wide
claim until evidence becomes available. Unclaimed attempts are omitted from the
confirmation history.

`supplier_credit_reads.py` adapts POSTED facts to existing bill-allocated AP rows
and original-head input-credit reversals in the supplier-note period. Manual and
scheme AP adjustments remain existing rows; internal requests never become
accounting facts. These adapters are not yet connected to report consumers.

API role/scope checks and RBAC rows, existing return writer fence enforcement,
all AP/GST report consumers, printable-request labels and the Tally gate must
still be integrated and tested. The application-level
adapter regressions remain uncommitted until the A08-safe adapter is ready.
The current preview is read-only and does not reserve money; POST rechecks
evidence and cumulative caps. Existing status/pricing writers must respect the
confirmation fence before these new services are exposed as routes. The
independent Cash Flow debit-note form currently omits supplier-note identity
and defaults to RETURN_CN. Its bypass needs a separately approved narrow form
change or explicit deferral; no change to that existing form/route is made by
these foundations. This is software/data-scoping work, not tax-filing
certification. A12 is separate aging/report correction, with no bill-allocation UI.
