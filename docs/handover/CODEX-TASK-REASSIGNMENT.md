# Codex handover: task reassignment recipient access

Date: 2026-10-18. Branch: `codex/task-reassignment-access`.
Base: `eab9fd62df9fea26a31087763e56a2d2d686badb` (fetched main).
Delivery: draft PR for independent review; no merge or deployment in this phase.

## Problem and change

A manager could send `assigned_to` through PATCH, PUT or POST `/tasks/{id}/reassign`
with a nonexistent, inactive or foreign-shop user ID. All three routes persisted
that ID without looking up the recipient. Named task ownership itself grants
visibility, so a foreign-shop user could then read the task.

`backend/api/routers/tasks.py` now calls the existing `_named_target` validator
before any edit/reassignment write. The PUT alias shares the PATCH handler.
The same rule already protected named escalation: the recipient must exist,
be active and have access to the task's shop. Existing Admin/Superadmin reach,
manager authority, task visibility, terminal-state protection, successful
reassignment history and response shapes remain as before.

No screen, staff step, POS code or role registry changed. A request rejected for
its recipient also writes none of the other fields supplied in that request.
Unavailable staff records return the existing validator's 503 response.

## Regression evidence

- Before the production change, all nine invalid-recipient cases failed:
  nonexistent/inactive/foreign-shop recipients through each of PATCH, PUT and
  POST reassignment returned 200 instead of 400.
- After the fix, 48 focused HTTP checks passed against real task/user
  repositories over `mongomock`. They cover the nine bypasses, no writes on
  rejection, foreign-recipient visibility, same-shop/area/HQ controls,
  unattributed-task HQ rules, missing staff records, manager-only authority and
  invisible tasks.
- Two existing success tests now supply a real-shaped active same-shop
  recipient fixture; their assertions were retained.
- Related task/access suite: 363 passed, 2 skipped (the optional real-Mongo
  checks; the isolated local port is intentionally unavailable).
- Frontend forced TypeScript build and Vite production build: passed. Vite
  emitted the pre-existing CSS-comment optimization warning; no frontend
  source was changed.
- CI-pinned Pylint 3.0.3, errors/fatal checks on `tasks.py`: passed. The shared
  runtime's Pylint 4.0.5 also passed before the pinned check.
- Backend smoke import: 1,314 routes. `git diff --check`: passed.

Local checks use the external `fixes-2026-10-18/run_checks.py` launcher with an
allowlisted test environment, a short-timeout unavailable localhost Mongo port,
dark dispatch and Shopify writes disabled. No live database or service was used.
The focused tests supply in-memory repositories; the shared pytest fixture may
start the application in that isolated test environment.

Evidence logs are external to Git in the owner's `fixes-2026-10-18` folder:
`task-reassignment-red.log`, `task-reassignment-focused.log`,
`task-reassignment-related.log`, `task-reassignment-pinned-lint.log`,
`task-reassignment-smoke.log`, and `task-reassignment-frontend.log`.

## Limits

This prevents new invalid named reassignments; it does not repair existing task
assignments. It reuses the established recipient validator rather than changing
how legacy user roles or shop reach are normalized. Recipient lookup and task
write remain separate operations, as on the existing named-escalation path;
this change does not add a cross-collection transaction. Production behavior
has not been exercised. The original audit checkout was not modified.
