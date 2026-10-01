# Cloud brief: Audit blocker C1 (#1171) - round 5

Repo: brashakg/ims-2.0-railway (IMS 2.0, a retail OS for an Indian optical chain: FastAPI + MongoDB backend in backend/, React 19 + Vite + TypeScript frontend in frontend/). Branch: `fix/off-catalogue-items-release` (draft PR #1171).

Start: `git fetch origin && git checkout fix/off-catalogue-items-release && git merge origin/main` (resolve any conflict, then re-run the checks). Read the PR body and its latest comments first (`gh pr view 1171 --comments`).

## Background and owner rulings
Audit C1/C2/C3 and owner rulings: goods ordered through 'Not in the catalogue?' arrive held; the catalogue manager(s) BY NAME get one task per receipt (2026-09-30), escalating to the shop's store manager after a day; finishing the product releases the held units through ONE release function; nothing says 'On shelf' for 0 units; an off-catalogue line matching an existing product (active or draft) never creates a second product; receiving is managers only. ROOT RULE (decided 2026-10-01): a unit's ORIGIN (which receipt and line it was received on) is recorded once and never rewritten by a transfer - every count of 'units already received from this receipt/line' (order cap, repeat guard, void check, voidable flag) reads that origin, not the unit's current source/location.

## Task
Round 5. Fix the two open problems below at the root (a draft that is on an open purchase order or a held receipt can be discarded today; after a discard the item cannot be re-ordered). Decide: refuse the delete/discard of a draft while any open (not cancelled, not fully received) PO line or any receipt names it, with a clear message; the identity check that blocks a typed PO line must ignore deleted/discarded drafts, or offer the discarded draft back. Then re-check every rule in the background against the change.

## Open problems found by the last review (fix every one, or show with evidence that it is wrong)
1. MEDIUM - a discarded draft that is still on an open order is not handled. The new delete guard at backend/api/routers/catalog.py:2743-2758 only refuses the delete while held_receipts(spine) is non-empty. It never checks for a sent PO that has not been received yet. Repro (probe on HEAD 80f482e, StrictDB world): raise_po([new_product BOSS_TYPED x2]) and send it; ADMIN delete_catalog_product(twin) succeeds; then receive_everything(po). Wrong results: (1) The receipt goes PARTIALLY_ACCEPTED holding 2 units, and the cataloguer gets the task 'Finish 1 item(s) held on receipt RCPT/BV-DHN-02/26-27/0001'. The task text (grn_accept.py:998, link /catalog/review at :1001) says 'Finish them from Catalogue > Needs review (they are at the top)', but the draft is NOT in Needs review: the discard cleared needs_review (catalog.py:2763), so _needs_review_list has no such SKU. The store manager, who could void the receipt, is told nothing. (2) Opening the product editor directly and finishing it (PUT /products/{id}) runs release_held_receipts (products.py:3593-3598). That mints 2 AVAILABLE units at BV-DHN-02 for a product whose spine stays is_active=False (deleted), and the receipt turns ACCEPTED. The units are sellable-looking stock of a deleted product. Fix direction: refuse the delete while an open PO line names the draft, or route a receipt for a discarded draft to the store manager to void.

2. LOW-MEDIUM (introduced by this branch's discard + C2 rules) - after a discard, the item can never be re-ordered by typing it. Repro: order_and_receive(BOSS x2); void the receipt; ADMIN deletes the draft; the manager types the same Boss 1700 C2 52 on a new PO. The PO is refused with 409 ALREADY_IN_CATALOGUE 'Use the existing product instead of typing it in', naming the deleted draft (is_active False, provisional False, catalog_status DRAFT). That check is purchase_orders.py:115 via product_master.identity_conflict, which matches deleted rows. Picking that product instead is refused at send with 400 PO_LINES_INCOMPLETE (missing offer_price), and the draft is in no queue. So the discarded item is stuck behind its own corpse until a cataloguer finds the deleted row by id, finishes it and switches it back on.

## Review passes (run after the fix, one after another)
- **stock-release**: Held units are released exactly once, only when their product is complete, at the right shop, with a movement record - including after some of the receipt's units were SOLD or TRANSFERRED to another shop (the order cap, the per-line repeat guard and the void check must still count them); a finished draft never mints a second copy of another already-received line; a held receipt whose units exist anywhere cannot be voided; two receipts of the same draft, a merged or discarded draft, and a partly accepted receipt are handled. Run mutants with a timeout.
- **roles-tasks**: ONE door tells the cataloguer an item blocks a receipt or a bill (the named-person path) - the older 'Ask for cataloguing' button and the booking-blocked auto task go through it or are removed; every such task is visible to the catalogue managers it names and never only to people who cannot open the catalogue; one task per person (dedupe key per person pinned by a test); none-found fails loud (pinned); receiving stays managers-only; rbac rows equal code gates.
- **one-rule-ui**: One identity match decides 'already exists' (PO line and Add product); one release function; the catalogue drawer and every chip read the REAL product state (an ordered draft is never 'POS-ready' and offers no Order stock/Clone until finished); the Needs-review ordered-draft row, its badge and editor kind are pinned by a frontend test; a PO create that fails validation leaves no orphan draft behind; tsc and touched vitest green on a 1024x768 tablet.

## Hard rules
- Never touch POS files: frontend/src/pages/pos/**, frontend/src/components/pos/**, frontend/src/stores/posStore.ts, frontend/src/routes/posRoutes.tsx, backend/api/routers/orders/** (unless this brief names an owner-approved POS change).
- No production access of any kind: no Railway, no database URLs, no Shopify calls, no secrets. Network only for git/GitHub and the npm/pip registries.
- No emojis in Python files. Light theme only (no `dark:` classes). Touch only what this task needs.
- A new or changed API route needs its rbac_policy row equal to its code gate. A new screen needs a row in e2e/fixtures/routes.ts.
- Every fix gets a test that FAILS when the fix is reverted - prove it (break the code, watch the test fail, restore). Run mutants with a timeout.
- Count EVERY problem a review pass lists, whatever its severity. Pre-existing problems you did not cause go in a 'Follow-ups' list instead.

## Checks (all must pass before you push)
- Backend: `python -m venv .venv && .venv/bin/pip install -q -r backend/requirements.txt -r backend/requirements-dev.txt`
- Smoke: `cd backend && JWT_SECRET_KEY=test ENVIRONMENT=test ../.venv/bin/python -c "from api.main import app;print(len(app.routes))"`
- Tests: `cd backend && JWT_SECRET_KEY=test ENVIRONMENT=test ../.venv/bin/python -m pytest -q <every test file that imports a module you touched, plus tests/test_rbac_policy.py tests/test_rbac_access_matrix.py tests/test_policy_registry_guard.py>`
- Lint: `cd backend && ../.venv/bin/pylint api/ --disable=all --enable=E,F --extension-pkg-allow-list=pydantic --disable=no-name-in-module,no-member`
- Frontend (if touched): `cd frontend && npm ci && node_modules/.bin/tsc -b --force && node_modules/.bin/vite build && node_modules/.bin/vitest run <touched test files>` (node_modules/.bin, not npx)

## How to work
1. Do the fix/build below.
2. Then run the REVIEW PASSES below one after another, each as an independent adversarial reviewer (a separate subagent if you can; otherwise a fresh, skeptical re-read). Each pass tries to break the change and lists every concrete problem (file:line, input, wrong result).
3. Fix everything the passes list, re-run the checks, and repeat the passes - up to 3 rounds, or until a full round lists nothing.

## Deliver
- Small conventional commits, each ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Push to the branch named above.
- Update PR #1171: add a "Round 5" section to its body.
- Post ONE comment on the PR whose first line is `CLOUD ROUND DONE: CLEAN <head sha>` or `CLOUD ROUND DONE: OPEN (n) <head sha>`, then: what you fixed (problem -> fix -> commit -> revert proof), any problem still open, the follow-ups, and every check with pass counts.
- Keep the PR a DRAFT. Do NOT merge, do not mark it ready, never use --admin. Another Claude session reviews and merges it.

