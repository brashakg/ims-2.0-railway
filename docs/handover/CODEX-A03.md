# A03: original supplier-bill provenance

## Delivery state

The issue engine now uses the original receipt/bill resolver after the A08
merge. It prints an internal RETURN REQUEST, using original stored tax evidence
where verified, or clearly unverified tax for accepted-unbilled/rejected custody.
It never guesses the newest receipt or today's catalogue rate. A01 confirmation
uses the same strict original-bill evidence for financial posting. This branch
includes the coordinated frontend/RMA delivery and independently reviewed
accounting adapters; it is not a merged or deployed fix. See CODEX-A01.md for
the integrated test results and exact reviewed application head.

A03 and A01 will be reviewed as separate scoped commits in one coordinated
draft PR. Merge only when the approved supplier-confirmation path and its
accounting-export gate are both usable; do not deploy an intermediate export
block without a completion action.

The owner has approved recording the supplier's actual credit-note number/date,
credited items and amounts through the existing Issue Credit action. A unique,
traceable original bill can be filled automatically; Accounts selects an
ambiguous origin. Only the confirmed partial amount posts; untraceable amounts
remain pending. See CODEX-A01.md for the connected posting/read adapters.

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

`resolve_request_provenance` is a distinct operational resolver: exact A08
rejected custody or a unique accepted-but-unbilled origin can support an
internal request. The result supplies no invented billed quantity, claimed GST
or financial amount. For rejected custody it checks the exact receipt line,
rejected quantity, supplier/shop and linked physical-unit identities. A matching
accepted-goods bill cannot be borrowed to tax those rejected pieces. Ambiguous
accepted origins, bad links and read failures remain errors.

The adapter retains the Accounts-entered known goods value with GST
explicitly unverified, rather than labelling it zero-rated or adding current
catalogue GST. The internal print explains that the supplier credit is not
posted. A08 issue/retry/cancel/reprice fences stay intact. The accounting Tally
export requires the canonical confirmed POSTED event for ordinary traceable
notes as well as unbilled requests; the prior exporter debited the vendor
and credited Input GST immediately. A01's export uses confirmed allocations,
not the earlier requested amount.

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

At the earlier foundation checkpoint, all 62 cases passed in 10.76 seconds. Pinned Pylint 3.0.3 with the
repository errors/fatal gate passed. No live database, POS,
deployment, or financial posting was used. That foundation phase changed no frontend source;
the approved confirmation UI is now integrated as documented in CODEX-A01-FRONTEND.md.

## Integration and limitations

Callers must authorize the return and original bills before showing evidence.
These snapshots are not reservations. A01's connected writer enforces
cumulative quantities, money and tax caps across confirmations and returns,
checks fingerprints, obeys period locks, and supplies idempotency. A01 validates
the original registration against entity records. Neither helper provides an
API to repair a historical bill. Missing original tax provenance must not be
replaced with today's tax rule.

The existing printable debit note assumes one supplier/recipient GSTIN pair.
Its adapter preserves original tax heads and rejects incompatible original
registration pairs instead of putting mixed tax origins under today's header.
Eight application-level issue regressions cover original versus newer receipts,
master-tax changes, missing bill evidence, exact original invoice reference and
the explicit unbooked internal request. A01 keeps the internal request
separate from the supplier-confirmed accounting event. See its handover for
the coordinated verification and remaining hosted CI/merge gates. This is a
software/data-scoping review, not tax-filing certification.
