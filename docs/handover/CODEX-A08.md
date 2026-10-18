# A08 — rejected receipt custody and draft handoff

Owner approval: complete the handoff on the existing pages, including a small
draft-status and credit/replacement choice. This is an approved workflow
exception. A09 now permits Accounts pricing later; unknown cost is not zero.
A01 supplier accounting posting remains separate. No live data, POS code or
historical receipt backfill is included.

Base: `eab9fd62df9fea26a31087763e56a2d2d686badb` (remote main when the isolated
branch `codex/a08-rejected-receipt-handoff` was created). Implementation is in
`C:/ims-codex-a02`; the old A02 branch remains intact. The original and final
audit checkouts were not changed.

## Behavior

Posting a receipt with received 3 / accepted 2 / rejected 1 creates two
AVAILABLE accepted units and one QUARANTINED custody unit. Accepted counters,
normal labels, product cost, bill quantities and sellable stock remain based
on the two accepted pieces. Rejected units have immutable receipt/line
provenance, the receiver/reason/time, their own barcode and quarantine label
metadata. They appear in the existing quarantine queue and link to their
return draft.

Each rejected GRN line creates its own deterministic supplier-return draft,
so repeated products and mixed credit/replacement choices remain separate.
The receiver chooses `rejection_disposition` per line. Older pending receipts
without that choice stay explicitly unconfirmed; there is no silent default.
On the existing Vendor Returns page, a manager confirms credit or replacement
through the existing status route. Accounts alone can enter prices. Drafts
can remain unpriced and cannot issue a note; confirmed returns continue
through the existing approval/shipping/pricing/issue actions.

A cancelled receipt-generated return can reopen as the SAME draft from this
page. Its identifier, physical links, prices, chosen type and history stay;
fresh confirmation is required before issue. Ordinary manual returns retain
their existing transitions.

The draft is stored on `vendor_returns` with `status=draft`,
`debit_note_draft_status=DRAFT`, a stable draft identifier, and exact source
references. It is NOT a record in either posted-note collection. It has no
issued serial, ledger/GST entry, Tally export or payment-hold release. A
successful existing formal issue records the issued link on the return; a
retry reuses the same issued note. This does not repair A01's existing split
between formal documents and AP accounting.

## Safety and recovery

- The existing GRN acceptance claim and heartbeat cover rejected custody and
  the required draft write before terminal receipt status. Failures leave the
  receipt retryable. Deterministic Mongo primary keys independently prevent
  duplicate rejected units and duplicate drafts after a crash or lost reply.
- Rejected units carry `receipt_disposition=REJECTED` and negative per-line
  ordinals. Accepted ordinals remain nonnegative. The existing GRN unique
  index therefore has disjoint accepted/rejected identities.
- Accepted-origin counts and last-paid cost exclude only the new marker,
  never all QUARANTINED units: an accepted piece quarantined later still
  counts as an accepted purchase, even after transfer. Void/escalation safety
  counts include rejected physical custody so these pieces cannot be orphaned.
- Generic lift-quarantine refuses receipt-rejected pieces. It cannot bypass
  receiving/catalogue/cancelled-order checks to make them sellable. Existing
  quarantine correction for accepted goods keeps A02's compare-and-set rules.
- A receipt-rejected unit already linked to its automatic draft cannot be
  relinked through a duplicate manual vendor return. Ordinary manual returns
  retain their existing behavior.
- Non-Accounts readers see only allowed operational draft/origin metadata;
  prices, totals and note amounts remain masked.
- Draft confirmation uses the existing status compare-and-set. Of two
  conflicting choices, only the first can change `draft` to `created`; the
  other receives 409 and cannot replace the saved choice. The chosen type is
  recorded in status history alongside the actor and time.
- Cancellation, reopening and repricing share an atomic fence with formal
  note issue. The issue claim reads a fresh return document, preventing a
  stale price/type snapshot. An authoritative posted-note lookup fails closed
  when unreadable; lagging return metadata cannot make a real note disappear.
- A known initial validation failure releases its own issue claim so Accounts
  can correct the input. A handled uncertain write becomes `RETRY_ISSUE`:
  only issue retry is permitted, and even a later validation failure preserves
  the price/cancellation fence. A persisted note repairs the return link on
  retry without allocating a second note.
- A lost claim reply before the issue engine runs releases only that call's
  claim and preserves any earlier uncertainty. The claim compares the saved
  uncertainty value so a stale request cannot clear a newer issue fence.
- `quarantined_units` in acceptance results is the total rejected quantity
  whose custody/draft handoff is confirmed for this receipt, including prior
  attempts. `units_added` and `stock_ids` retain their existing accepted-stock
  meanings and are not inflated with rejected pieces.

Existing queue display limitation: its `rtv_vendor_id` field holds a return ID,
while the old vendor-name lookup interprets it as a vendor ID. A rejected row
can therefore show its return identifier in the supplier column. Its View
return action uses that identifier correctly. Broader name-resolution cleanup
is outside A08.

Explicit recovery limit accepted during coordination: a process that dies
after persisting `ISSUING` but before persisting a note leaves the return locked.
Without a real note to recover, that case requires technical reconciliation
after proving the old issuer stopped. There is deliberately no automatic
timeout takeover: a delayed old worker must never issue after a new worker
cancels or changes its prices. Ordinary handled errors remain retryable as
described above. This is not advertised as automatic recovery from process death.

## Verification status

- Final combined backend regression run is in progress on the source preceding
  the final lost-claim-reply correction. Its union covers
  receipt acceptance/claims, void/recovery, no-PO purchases, catalogue holds,
  cancelled orders, last cost, quarantine, return roles/statuses, formal debit
  notes and the shared on-hand/label rule. Earlier related run: 386 passed.
- Recovery and corrected legacy fixture run: **152 passed**, including the
  then-existing **31 A08 cases** (19 custody/handoff plus 12 issue/cancel/reopen cases).
  Legacy last-cost/cancel tests now provide required in-memory draft storage;
  all existing accepted-count and monetary assertions remain, with explicit
  rejected-quarantine counts added. No production behavior was weakened to
  accommodate their old database-unavailable fixtures.
- Final source focused run: **34 passed** (19 custody/handoff plus 15 recovery
  cases), including lost claim replies with and without prior uncertainty and
  a stale uncertainty race. Pinned CI lint was rerun after this correction and
  passed at **10.00/10**. The final broad result will be recorded on the PR and
  external execution handover, without changing source just to restart CI.
- Independent reviewer also ran the 31 A08 cases and two additional late-CAS
  race probes: **all passed**, with no source blocker found. Exact committed
  head sign-off will follow the final commit.
- Pinned CI **Pylint 3.0.3**, errors/fatal only, all 12 changed application
  modules: **passed, 10.00/10**. Backend import smoke: **1,314 routes**.
- Frontend: forced `tsc -b --force` and `vite build` **passed**. Existing CSS
  comment parsing warnings remain in unchanged styles. Initial related tests
  found a legacy undefined-return-type display regression; that was corrected
  without changing the old fallback. Related first run: 85 passed/1 failed;
  corrected follow-up: **28 passed** across the affected return/cockpit suites,
  including the new draft test. Other initially passing suites were unchanged.
  Reopen-draft UI follow-up: **8 passed**. Final forced TypeScript and Vite build
  after that follow-up also passed.
- Tests run through the external sanitized runner with only loopback MongoDB
  and in-memory mocks, synthetic test credentials and external writes disabled.
  Logs are under `C:/Users/avina/OneDrive/Desktop/IMS 2-0 ChatGpt/fixes-2026-10-18/`:
  `a08-combined-final.log`, `a08-recovery-focused.log`, `a08-recovery-lint.log`,
  `a08-final-smoke.log`, `a08-final-frontend-build.log`,
  `a08-frontend-tests.log`, `a08-frontend-followup.log`,
  `a08-recovery-frontend.log`, `a08-independent-recovery.log`,
  `a08-independent-late-cas.log`, `a08-claim-reply-final.log`,
  `a08-claim-reply-lint.log`.

Independent review and PR CI remain pending. The PR is to remain a draft until
those checks and the coordinator's review complete. No merge or deployment was
performed for A08.
