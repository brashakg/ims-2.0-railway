# A06: current permissions for existing sessions

Draft for Claude to review and merge. Base main is
`169b501f211930fff8b5fac5875dece4d4560aa8`; branch
`codex/a06-live-authority`, worktree `C:/ims-codex-a06`.

## Result

An existing signed access token now uses the account's current roles and shop
membership on its next protected request. Route dependencies, the RBAC
middleware and the investor write guard share one projected account lookup per
request. The result is not cached across requests. Session identity and token
expiry are unchanged. Removed active shops are cleared or reassigned using the
existing refresh-claims rule, including null or empty membership lists.

An account with every role removed cannot bypass a middleware role restriction.
Authenticated-only routes keep their existing contract. A disabled account is
rejected even when the older status cache still contains an allow result.
Changing between ADMIN and INVESTOR applies to existing sessions in both
directions. No frontend, POS, staff steps or screen layouts changed.

## Files and review

- `backend/api/routers/auth.py`: current authority resolver, request reuse,
  route dependency integration and null shop-list handling.
- `backend/api/middleware/rbac_enforcement.py`: current authority at the policy
  gate; distinguish invalid credentials from a valid account with no roles.
- `backend/api/main.py`: investor write gate uses the same current authority.
- `backend/tests/test_live_authority_a06.py`: 12 regression/control cases.
- Existing auth and RBAC tests now expect fresh permission reads and denial at
  middleware-only role gates after all roles are removed.

Independent source/test review found no blocker. No live database was inspected.

## Validation

- Related auth, account hardening, RBAC and refresh rotation suite: **166 passed**.
- After adding the inverse INVESTOR-to-ADMIN control, final focused suite:
  **12 passed**. This is 167 distinct cases across these two runs.
- The same final focused tests against the original three application modules
  loaded in memory from `git show 169b501f`: **11 failed, one passed**. The
  unchanged missing-account/outage behavior is the passing control.
- Errors-only Pylint passed for all three application files.
- Backend import smoke loaded **1,320 routes**; `git diff --check` passed.
- No frontend files changed. The identical frontend source passed forced
  TypeScript checking and Vite build in the parallel A02 checkout.

Checks used explicit test-only settings and a loopback Mongo address with short
timeouts; regression tests use isolated in-memory users. No production calls or
business transactions were made. The pinned audit checkout was not modified.

## Limits and integration

The pre-existing fail-soft policy is preserved: absent user records or an
unavailable user repository retain signed token authority. This PR does not
claim revocation during a database outage. It adds one projected Mongo read per
authenticated request; no production latency/load test was performed.

`api/main.py` is also touched by product-import retirement, in separate sections.
Claude should verify the combined result after merging both drafts. This PR is
not merged or production-verified.
