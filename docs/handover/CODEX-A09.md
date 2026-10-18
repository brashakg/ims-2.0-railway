# A09 - preserve unknown purchase costs

Owner decision: goods may be received before Accounts enters their price.
Missing cost is distinct from zero. No free-goods workflow, receiving gate,
staff screen or POS change is authorized or included here.

Working branch: `codex/a09-unknown-cost` in `C:/ims-codex-a11`, initially based
on remote main `50d5247a384280fdb22ad259d83abbd3ac88c89c`, then fast-forwarded
to merged A08 main `3f267389846fbe2e54eaf89d74ee0435883eafc0`. The earlier A11 branch
is preserved. Original and final audit checkouts and live data are untouched.

## Behavior and evidence

Previously, 10 old units with no known cost plus a bill for 10 new units at
100 produced a moving average of 50. The old missing cost became numeric
zero; the product fallback then made unknown units appear fully costed.

Moving-average calculations now retain an unknown result when a positive
quantity has unresolved cost. Explicit numeric zero remains distinct in the
pure calculation; existing receipt/invoice guards still refuse inferred free
goods. Per-product state separates known and unknown prior layers using the
same own-cost and legacy product-fallback rule as stock valuation. Read errors
stop the fail-soft valuation update instead of pretending inventory is empty.
An existing valid master average remains the prior average: original receipt
unit prices must not undo an earlier invoice true-up. A sequential regression
keeps receipt 100 / bill 120 followed by receipt and bill 100 at average 110.

Before any invoice changes a product price, unresolved existing units receive
an internal pending-cost marker. A later product price cannot cost those units
by fallback. A real recorded unit cost still wins over the marker. All shops
and unit statuses are covered so reservation, transfer or a later return does
not erase the unknown state. Marker failures prevent the dependent valuation
write; the already recorded supplier payable remains intact.

The approved receiving hunk stamps that marker directly on newly
accepted units when the receipt/PO supplied no price. Quantity-first acceptance
and catalogue checks are unchanged. The marker applies only to accepted-unit
minting; rejected custody remains excluded from invoice cost resolution.

A later supplier bill can resolve missing costs only when real booked purchase
invoices for that supplier and receipt cover the complete accepted and minted
quantity at one unique price. Immutable GRN identity is authoritative; legacy
source/unique receipt-number fallback is disjoint. Transferred pieces remain
linked, and rejected receipt custody is excluded. Partial bills, conflicting
prices, missing physical units or unproven legacy provenance remain unresolved
instead of receiving an arbitrary allocation. Already priced own layers are
not rewritten by this missing-cost correction.

Newly invoice-priced units keep supplier base price in existing `unit_cost` /
`cost_price` fields and actual inventory value in existing `landed_cost`.
Stock valuation uses that landed figure only for `PURCHASE_INVOICE` layers.
Thus a no-ITC walk-in bill at base 100 plus 5% GST values its unit at 105 while
the shared last-paid supplier quote remains 100. Immediate receipt prices and
older unit conventions remain unchanged. No new monetary field is exposed.
The pending flags and internal catalogue-only marker are registered with the
shared Accounts-only cost mask.

An incomplete moving average preserves known old prices and writes a null
`moving_avg_cost` with pending status; the audit records unresolved quantities.
When a valid bill supplies the catalogue's missing cost, its proven price still
finishes a cost-only draft through `catalogue_write_done`. An internal
`cost_price_is_catalogue_only` marker prevents this price from being treated as
a complete prior average or as evidence for unpriced stock. Later complete
pricing uses known layer costs and clears the marker. Existing valid prior
averages keep their established behavior. This preserves catalogue completion
without making old unknown units look costed.
FIFO and walk-in latest-price product rules retain their arithmetic, while
their writes also preserve unknown unit layers. Stock ledger/report totals
continue to use the existing uncosted-unit count and known-cost-only average.

## Deliberate boundaries

- Unmarked historical units with a positive existing product price retain the
  established fallback. Their old absence of an own price does not prove they
  were unpriced, so no guessed historical backfill is performed.
- Partial or conflicting bills and bulk-DC allocations without proven single
  receipt/unit pricing remain pending. There is no arbitrary assignment of a
  partial invoice to physical barcodes.
- Existing fail-soft post-booking valuation remains: a valuation-storage error
  does not roll back the supplier bill. Unknown layers remain visible for
  reconciliation; this change does not claim a general replay architecture.
- A01 AP posting, A03 source-bill/tax notes, A12 advances/aging and broader
  valuation-policy redesign are separate work. No financial deployment or
  live database verification is claimed.

## Verification status

- Initial new/focused purchase matching: 52 passed, then 64 passed after
  base-versus-landed, last-paid and raw-stock masking controls.
- Related run: **364 passed**, 50 warnings, 78.18 seconds. Included the new
  regression module, purchase matching, stock valuation, purchase invoices,
  no-PO receiving and last-paid suites.
- Pinned CI Pylint 3.0.3, errors/fatal only: **10.00/10** at that checkpoint.
- The real receiving regression fails on the pre-marker source exactly at
  the missing pending marker, while receipt creation/acceptance still succeed.
- After integrating merged A08 and the accepted-unit marker: **402 passed**,
  50 warnings, 64.69 seconds across the related purchasing/value/receiving and
  A08 custody/recovery suites. Pinned lint passed; smoke loaded **1314 routes**;
  forced TypeScript build and Vite production build passed.
- Independent review caught three introduced regressions before submission:
  preserving an earlier known invoice average, supplier base versus landed
  value, and completing cost-only catalogue drafts. All were corrected with
  discriminating controls. The final focused module passed **46 tests**, 5
  warnings, 37.39 seconds. Four independent synthetic probes also passed,
  including subsequent mixed-layer resolution to the correct known average.
- Final pinned Pylint 3.0.3 errors/fatal check passed (**10.00/10**), and the
  final backend smoke again loaded **1314 routes**.
- Final expanded related run (including existing catalogue completion suites),
  independent exact-head review and hosted CI are
  pending at this commit. The coordinator will record final results in the PR
  and external execution handover before considering a merge.

Sanitized logs and independent counterexamples are in
`C:/Users/avina/OneDrive/Desktop/IMS 2-0 ChatGpt/fixes-2026-10-18/`, including
`a09-independent-baseline.log`, `a09-first-focused.log`,
`a09-second-focused.log`, `a09-related-first.log`, `a09-ci-lint-first.log`,
`a09-receive-baseline.log`, `a09-related-final.log`, `a09-catalogue-fixed.log`,
`a09-related-complete.log`, and `a09-frontend-build-final.log`. Synthetic
credentials, loopback-only MongoDB and in-memory mocks were used; external
writes were disabled.
