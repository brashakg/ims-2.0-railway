# Codex handover: A02 quarantine transition race

Date: 2026-10-18. Owner: Claude remains the main agent.

Status: implementation and local verification complete; independent review and
PR delivery are owned by the coordinating Codex agent. This file is not a merge
or deployment confirmation. Do not duplicate this fix on another branch.

## Scope and starting point

- Audit finding: A02, stale quarantine/lift writes could overwrite a competing
  sale or other stock transition and later make the same sold unit available.
- Worktree: `C:/ims-codex-a02`.
- Branch: `codex/a02-quarantine-race`.
- Base: `169b501f211930fff8b5fac5875dece4d4560aa8`.
- The owner cleared this finding for Codex after supplying Claude's current
  assignment. No POS files, shared repository methods, or other audit findings
  were changed here.

## What changed

`backend/api/routers/inventory/quarantine.py` now calls the existing
`BaseRepository.update_if` for both quarantine and lift. The single Mongo write
requires the status, store, update timestamp, quarantine timestamp, quarantine
generation, and item-event sequence seen during the permission/state checks.
If a competing write changes that snapshot, the request returns HTTP 409 with
code `stock_changed` and asks the caller to refresh. Database errors still raise;
they are not reported as a concurrency conflict.

Every newly created quarantine carries a unique `quarantine_generation` marker.
An old lift request therefore cannot release a replacement quarantine even if
both cycles have identical timestamps at Mongo's millisecond precision. Existing
rows without this marker remain supported; their existing timestamps and ledger
sequence are also checked. No migration is required.

The predicate uses the actual stored status, preserving the existing eligibility
of null/missing/blank AVAILABLE rows and lowercase/padded AVAILABLE, DAMAGED, and
QUARANTINED spellings. It does not broaden the allowed business transitions.
Audit, ledger, online-stock writeback, and task-event hooks run only after a
successful conditional write. A losing request triggers none of these hooks.

## Verification

The new deterministic race tests insert a competing update immediately before
the route's actual database write. They exercise the real repository predicate
against the strict in-memory Mongo test double, rather than replacing the
conditional update with a canned success/failure result.

- Before the production change: all 10 selected race/generation tests failed
  against the base implementation. Nine stale writes incorrectly succeeded;
  the generation test found no marker.
- After the change: `backend/tests/test_quarantine_f21.py`: **37 passed**.
  This includes the 18 added cases for competing sale, shop movement, duplicate
  quarantine, stale lift, replacement cycle, ledger-sequence change, legacy
  statuses, and same-time generation separation.
- Errors-only pylint for `backend/api/routers/inventory/quarantine.py`: passed.
- Related regressions (`test_e3w_wiring.py`, `test_e3_item_event.py`, and
  `test_cost_mask_f35.py`): **75 passed**. These include the real post-write
  ledger integration and existing cost masking.
- Backend import smoke: **1,320 routes loaded**.
- `git diff --check`: passed. No frontend files changed.

Commands used the repository-root virtual environment and a rebuilt child
environment containing only system path variables plus explicit test settings:
`ENVIRONMENT=test`, `JWT_SECRET_KEY=test`,
`MONGODB_URL=mongodb://127.0.0.1:27099/ims_codex_a02`,
`DISPATCH_MODE=dark`, and `IMS_SHOPIFY_WRITES=false`. Later checks added short
connection timeouts to the same isolated URL. No production connection settings
or credentials were inherited. The local Mongo port was unavailable; application
startup fell back to its mock database. The new race tests separately mock all
online-stock and agent-dispatch hooks. No live services were exercised.

## Limits and follow-up

- These changes prevent future stale writes on these two routes; they do not
  repair stock records already made inconsistent by earlier versions.
- Real-Mongo concurrent load and production transaction checks were not run.
  The tests deterministically verify the losing interleavings and predicates.
- Existing post-write audit/event delivery remains fail-soft and separate from
  the stock update. This patch does not introduce a cross-collection transaction.
- Another metadata update may produce a conservative 409; refreshing is the
  intended recovery. No automatic retry can override a winning transition.
- All other audit findings remain outside this branch.
