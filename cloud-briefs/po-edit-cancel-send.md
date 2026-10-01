# Cloud brief: Purchase-order edit / cancel / send (#1165) - round 7

Repo: brashakg/ims-2.0-railway (IMS 2.0, a retail OS for an Indian optical chain: FastAPI + MongoDB backend in backend/, React 19 + Vite + TypeScript frontend in frontend/). Branch: `feat/purchase-order-edit-cancel-send` (draft PR #1165).

Start: `git fetch origin && git checkout feat/purchase-order-edit-cancel-send && git merge origin/main` (resolve any conflict, then re-run the checks). Read the PR body and its latest comments first (`gh pr view 1165 --comments`).

## Background and owner rulings
Owner rulings 2026-09-28/29: edit a DRAFT purchase order; cancel a Draft or Sent order (or a line) WITH A REASON; no approval step - the button says Send to vendor; the catalogue manager raises drafts for the manager; RECEIVING IS MANAGERS ONLY (store/area manager, admin, superadmin - NOT the accountant); on-order counts SENT orders only. Every change leaves an audit row with who, what and (for cancels) why.

## Task
Round 7 on the purchase-order PR. Fix the three open problems at the root: pin 'a refused edit creates no provisional product' with a parametrized test using a real product repository and a typed-in line on SENT/ACKNOWLEDGED/RECEIVED/CANCELLED orders; close the concurrent-edit leak by validating every typed-in line through normalise_door_payload BEFORE creating any product and creating products only after the compare-and-set write succeeds (do the same in create_po so a refused line 2 never leaves line 1's product behind); update docs/reference/RBAC_MATRIX.md for the receiving rows (no ACCOUNTANT), the PO create row (CATALOG_MANAGER) and the two new routes. If one is wrong, say so with evidence. Revert-proof each fix; run mutants with a timeout. git fetch origin && git merge origin/main first if main moved. Re-run every check; commit; push.

## Open problems found by the last review (fix every one, or show with evidence that it is wrong)
1. LOW, test gap shown by a surviving mutant. Rule: a refused edit creates no provisional product. Code: backend/api/routers/vendors/po_detail.py:612 checks DRAFT before price_po_lines at :648 runs the product door (purchase_orders.py:429-457). The source, vendor-404 and date refusals at :620-644 also run first. The current code is correct, but no test pins that order. Mutant (private copy): move the DRAFT check to just after `new_items = computed["items"]` (:651). test_po_edit_cancel_send.py, test_purchase_lifecycle.py and test_po_store_boundary.py still pass (108 passed); no other test file calls update_po. Breaking input under the mutant: a PUT on a SENT order with one typed-in line {category FRAME, brand Vogue, model VO5286, colour W44, size 52, mrp 5000}. It returns 400, but a provisional Vogue is left on the spine with its product.created audit row. Why no test sees it: test_edit_refused_once_not_a_draft (test_po_edit_cancel_send.py:219-231) uses _wire, which sets get_product_repository to None (:157), and its body has no typed-in line. Fix: one parametrized test. My probe (scratchpad/pfv_rules_r6_probe_test.py) passes 4/4 on HEAD 55ae575 and fails on the mutant.

2. LOW, known and documented. Rule: a refused edit creates no provisional product. update_po makes typed-in products in price_po_lines (purchase_orders.py:429-457) before the compare-and-set write in _write_change (po_detail.py:552). Breaking input: two managers edit the same DRAFT at once, and the one who loses adds a typed-in line. Or a colleague sends the draft while a multi-line edit is still running create_via_door, which makes several DB round trips per line. The loser gets 409 and the order is unchanged. Wrong result: an inactive, stockless provisional DRAFT product stays on the spine with a product.created audit row, and only a retry with the same lines reuses it. The ponytail comment at purchase_orders.py:413-418 documents this. The builder's claim that it is limited to 'the request's own milliseconds' understates the window: it grows with the number of typed-in lines.

3. LOW (docs drift, introduced by this branch): docs/reference/RBAC_MATRIX.md was not updated, although the rbac_policy package docstring says to update it when rows change. PRs #1110 and #1155 did update it. It is now wrong in three ways. Lines 1116, 1118 and 1119 still say ACCOUNTANT may POST /api/v1/vendors/grn, POST /grn/{grn_id}/accept and POST /grn/{grn_id}/escalate. Line 1121 (POST /vendors/purchase-orders) leaves out CATALOG_MANAGER. The new rows PUT /vendors/purchase-orders/{po_id} and POST /vendors/purchase-orders/{po_id}/items/{line_index}/cancel are missing. Wrong result: anyone who answers 'can the accountant receive goods?' from the matrix gets 'yes', the opposite of the 2026-09-28 ruling and of the live gate. Nothing enforces this document, so no runtime effect.

## Review passes (run after the fix, one after another)
- **rules**: Edit only on Draft (Sent, Acknowledged, Received, Cancelled refused); cancel needs a real reason on Draft AND Sent; a line cancel audit row records the reason; a refused edit leaves NO side effect (no product cost change, no provisional product created) and every cost change it does make is audited; a cancel cannot race a receipt into an order shown Cancelled with stock on the shelf; a cancelled line stops counting as due everywhere.
- **roles-routes**: Receiving (create and accept goods receipts, express receive) is managers only - the accountant is refused at the server and the blocked page names who can receive; every route code gate equals its rbac_policy row; counter roles cannot edit or cancel; tsc/vite/vitest green.

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
- Update PR #1165: add a "Round 7" section to its body.
- Post ONE comment on the PR whose first line is `CLOUD ROUND DONE: CLEAN <head sha>` or `CLOUD ROUND DONE: OPEN (n) <head sha>`, then: what you fixed (problem -> fix -> commit -> revert proof), any problem still open, the follow-ups, and every check with pass counts.
- Keep the PR a DRAFT. Do NOT merge, do not mark it ready, never use --admin. Another Claude session reviews and merges it.

