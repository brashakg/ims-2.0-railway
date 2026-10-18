# A03: original supplier-bill provenance

## Delivery state

Foundation only at this stage: the new resolver and its isolated tests are ready
for review. The existing debit-note issue path has not yet been switched to this
resolver, so **A03 is not yet fixed in the application**. Integration waits for
the overlapping A08 return-draft change to merge. A01 confirmed-credit posting
will consume the same evidence after A03 integration.

The owner has approved recording the supplier's actual credit-note number/date,
credited items and amounts through the existing Issue Credit action. A unique,
traceable original bill can be filled automatically; Accounts selects an
ambiguous origin. Only the confirmed partial amount posts; untraceable amounts
remain pending. These are A01 changes, not implemented by this foundation.

## Evidence and behavior

The prior issue engine selected one receipt per product, or the latest receipt
when no units were linked, and could infer claimed input tax from today's vendor
registration without a booked bill. `return_provenance.resolve_return_provenance`
instead resolves every linked unit's immutable GRN, including after transfer,
and matches a booked original bill and line. Unlinked goods require a unique
accepted origin or an explicit selection. An unbilled accepted receipt remains
an ambiguity candidate; pending and void receipts cannot establish a purchase.

The resolver returns original bill/line identity and a fingerprint, supplier and
recipient registration snapshots, original quantity/value/tax heads, linked
stock identities, and proportional caps. Multiple receipt chunks sharing a bill
line share one cap; rounding cannot duplicate the same paise. Blocked input tax
has no reversal cap; reverse-charge tax is separate from the supplier payable.

Duplicate product lines and multiple bill lines require exact unit selections
when physical units are linked. Selections cannot cross the recorded supplier,
replace a linked origin, reuse a unit, exceed accepted/billed quantities, or use
unrelated bills. Missing or inconsistent stored amounts are pending errors.
Header-only bills do not prove billed item quantities, even for a one-product
receipt; they require an item breakup before financial allocation. Rejected
custody alone does not prove a billed purchase. Database failures propagate.

## Files and verification

- `backend/api/services/return_provenance.py`: read-only shared resolver; no
  routing, application startup, writes, or current-master tax enrichment.
- `backend/tests/test_return_provenance.py`: in-memory Mongo tests for original
  versus recent receipts, mixed sources/heads, selections/caps, transfer lineage,
  unbooked/blocked/RCM bills, changed master data, malformed stored amounts,
  header-only ambiguity, read failures and absence of writes.

Run with the external sanitized `run_checks.py` using the owned test checkout:

```text
python run_checks.py C:/ims-codex-a10 <external-log> -m pytest backend/tests/test_return_provenance.py -q --disable-warnings --tb=short
```

All 50 foundation cases passed in 6.80 seconds. Pinned Pylint 3.0.3 with the
repository errors/fatal gate passed. No live database, POS,
deployment, or financial posting was used. No frontend source changed.

## Required integration work and limitations

Callers must authorize the return and original bills before showing evidence.
These snapshots are not reservations. A01's writer must atomically enforce
cumulative quantities, money and tax caps across confirmations and returns,
check fingerprints, obey period locks, and supply idempotency. This helper does
not yet validate the original registration against entity records or provide an
API to repair a historical bill. Missing original tax provenance must not be
replaced with today's tax rule.

The existing printable debit note assumes one supplier/recipient GSTIN pair.
Its adapter must preserve original tax heads and reject incompatible original
registration pairs instead of putting mixed tax origins under today's header.
Application-level regressions must reproduce the old issue-engine failure
before this finding can be called fixed. A01 must keep the internal debit-note
document separate from the supplier-confirmed accounting event. This is a
software/data-scoping review, not tax-filing certification.
