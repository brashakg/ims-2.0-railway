# A13 - explicit event timestamps with compatible readers

New persisted event strings now identify their timezone. Ordinary event writers use an explicit UTC clock; established Indian-time attendance and money-ledger helpers keep their IST calendar and add `+05:30`. Existing records are not rewritten. Staff screens, roles, amounts, quantities, tax rates, document dates and steps retain their existing behavior.

## Authority and delivery sequence

The owner approved timestamp-only backend corrections, including the necessary POS event and reader changes, after the other audit findings. This implementation is isolated in `C:/ims-codex-a13`; the original checkout and `C:/ims-final-audit` remain untouched. It was prepared from A01/A03 head `68f01425dfead21798d48b3f7ed2798aa56bb837`. A01's separate test-only CI correction `869b4439e74682bd97018a4107d0fea29ef36c8b` is represented locally by dependency commit `be24d14b`; it is not an A13 application change.

A13 must be transplanted onto fresh main and released only after A01/A03 and A12 merge. Root owns review, CI and merge. A local test result does not establish deployment or live-data correctness.

## Changes and boundaries

- `api/utils/event_time.py` separates event encoding from calendar parsing. New timestamps are independent of the host timezone. Compatibility readers preserve explicit offsets and return no invented instant for malformed or unrepresentable values.
- The [writer classification](CODEX-A13-WRITER-CLASSIFICATION.md) records the direct and indirect source inventory, exclusions and additional helper paths. Direct classification covers 300 candidate expressions: 243 event expressions, 52 exclusions and five explicitly paired clock helpers. Shared helpers can serve several persisted fields; these numbers are expression counts.
- Task SLA and closure, lab dwell time, clinical conversion/event evidence and serial-sale warranty readers normalize known UTC event fields. Manual clinical and explicit warranty dates retain their existing calendar interpretation. Attendance normalizes old naive IST and new explicit representations into the same IST shift-day frame; its lateness/half-day thresholds and overnight policy are unchanged. Both manual request validators use that same frame.
- Receipt claim/heartbeat readers parse old UTC and explicit-offset stamps. The 300-second boundary, token ownership, retry thresholds and per-unit duplicate backstop remain. The atomic update compares the exact timestamp and token read, so parsing cannot authorize a stale takeover after a concurrent heartbeat. Unreadable or malformed proof remains closed to further receiving.
- Where one sample supplies BSON and an event string (order status/release, workshop/lab progression and similar paired writes), a new UTC sample supplies both. BSON remains a naive datetime and the string explicitly identifies UTC. Unrelated BSON-only writers retain their current server-UTC assumption; this is not a blanket BSON migration.
- Cash-drawer return windows include both old naive UTC and new canonical `+00:00` values at the exact inclusive endpoint. The bounded query does not claim support for arbitrary-offset historical strings. Refund arithmetic, shop scope, historical exclusions and tender legs remain intact.
- Shopify new history-import payment/refund timestamps keep their original source instant. No source identifier, refund amount, historical row, supplier credit-note date, AP date, GST period or replay key is changed.
- Workshop fitting `order_date`/`order_time` defaults and notification-ID date prefixes retain their original local-calendar convention. Only the confirmation event changes encoding. Supplied dates and measurements are preserved.

## Regression evidence

All local commands run through the sanitized external `run_checks.py`: test JWT, test environment, Mongo restricted to unavailable loopback port 27099, Redis unset, outbound notification dispatch dark and Shopify writes disabled. No production database or external financial action was used.

- `pytest backend/tests/test_event_time.py backend/tests/test_event_timestamp_readers.py backend/tests/test_grn_event_timestamp_compat.py backend/tests/test_grn_accept_atomic_claim.py -q --disable-warnings --tb=short`: **141 passed**, followed by **32 passed** in the reader module after adding two workshop-calendar controls (143 distinct cases across these four modules).
- These include UTC/IST/negative-offset representations, legacy naive values, month/year boundaries, malformed and representational-limit inputs, mixed attendance history, exact lease expiry, heartbeat/takeover races, known outages, retry stock quantities, non-UTC shared order clocks, unchanged money and status guards, and preserved workshop calendar defaults.
- Existing receipt atomic-claim fixtures now construct legacy UTC values explicitly instead of depending on the test machine's local timezone. Three outage sentinels also make the new lease-read proof unavailable after takeover; their 503, duplicate-stock bound and deliberately disabled-guard counterexample assertions remain.
- Independent reviewers reported 378 related existing tests, 268 attendance comparisons, 13 writer/report probes, eight actual persistence controls, six fitting-calendar cases and 52 root reader/POS/drawer controls. These are separate reviewer evidence; final exact-head approval remains required.
- Initial host-independent counterexamples were reproduced against the earlier reader behavior: attendance had 60 mismatches among 204 fixed comparisons; the drawer excluded both new-UTC exact-endpoint representations while four legacy/BSON controls passed. New candidate probes pass those boundaries.
- Full local backend tests, repository-configured pinned Pylint, frontend forced TypeScript/Vite build and smoke are running at this checkpoint. Their final results and any compatibility corrections must be recorded before release. Hosted full-suite and security checks remain mandatory.

## Residual limitations

No migration or repair of pre-existing ambiguous timestamps is included. Known legacy fields retain the established naive UTC or deliberate naive IST convention; this does not certify that every historical row was written on a correctly configured server. Unrelated BSON-only clock assumptions and response-only/seed timestamps remain outside this finding.

Independent financial review found a pre-existing GSTR-3B credit-note month-end microsecond gap: `_credit_note_totals` uses `$lt`/`<` against the inclusive end returned by `ist_month_window_utc`. Both old and new representations exclude that same final microsecond, while GSTR-1 uses its separate half-open window. A13 does not change that tax-window behavior; it is recorded separately for review. No tax-law certification or live financial reconciliation is claimed.
