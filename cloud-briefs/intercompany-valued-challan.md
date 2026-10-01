# Cloud brief: Inter-company valued delivery challan (audit F51, owner ruling D13) - BUILD

Repo: brashakg/ims-2.0-railway (IMS 2.0, a retail OS for an Indian optical chain: FastAPI + MongoDB backend in backend/, React 19 + Vite + TypeScript frontend in frontend/). Branch: `feat/intercompany-valued-challan`.

Start: `git fetch origin && git checkout feat/intercompany-valued-challan && git merge origin/main` (resolve any conflict, then re-run the checks). 

## Background and owner rulings
Audit row F51 and owner ruling D13 (2026-09-29): a stock transfer between shops of DIFFERENT companies/GSTINs (e.g. Dhanbad to Bokaro) prints a DELIVERY CHALLAN with value at cost, HSN, both GSTINs (from the one shop-GSTIN rule) and the SAME number as the transfer - replacing the Rs 0 challan - until the CA confirms otherwise (keep the rule in one place so it can later become a tax invoice). Same-company transfers keep today's challan. Valued at the units' own cost; the challan never appears on GSTR-1 as a sale.

Other open drafts touch nearby code: Open drafts touching nearby code (keep edits there minimal; if one has MERGED, build on main): feat/add-product-owner-rulings (reorder -1 = not set helper in api/services/reorder_policy.py), fix/stock-screens-audit #1169 (low stock uses the product level), feat/purchase-order-edit-cancel-send #1165 (receiving managers only), fix/purchase-bill-matches-form #1167 (one shop-GSTIN rule), fix/counter-roles-no-purchase-reads #1161 (cost_mask for counter roles), fix/off-catalogue-items-release (C1). Never touch POS files.

## Task
The reproduce phase is done and pushed (commit 18ff3ca): backend/tests/test_intercompany_valued_challan.py has 15 strict-xfail tests plus 3 guards that pin the contract. BUILD the fix so every xfail passes (remove each xfail marker as it turns green) and the guards keep passing. The reproducer's notes below explain the flow on main and the open questions; where they offer a choice, take the safe one and say which in the PR body.

## Open problems found by the last review (fix every one, or show with evidence that it is wrong)
The F51/D13 reproduce phase is done. The worktree is C:/ims-challan on branch feat/intercompany-valued-challan, made from origin/main 6068802. Commit 18ff3ca is pushed and adds one file, backend/tests/test_intercompany_valued_challan.py. No production code changed.

FLOW TRACED ON MAIN
1. Create: frontend StockTransferModal.tsx:236 sends no cost, and services/api/inventory.ts:73 turns that into unit_cost 0. Backend transfers.py:1104 then saves total_value = client unit_cost x qty = Rs 0. The lines keep only the client's fields: no HSN, no cost.
2. Ship: transfers.py:1376 calls _apply_ship_stock_move (:524). It claims the actual AVAILABLE units into shipped_stock_ids but never reads their unit_cost/cost_price. Those are stamped on each unit at goods receipt (grn_accept.py:319) or opening stock (opening_stock.py:93).
3. Ship history: transfers.py:1440 writes f"Shipped via {courier_name}" while courier_name is None. That is where "carrier reads None" comes from.
4. Print: print_documents.py:176.
   - Number is _challan_number("TRF", id), giving DC/TRF/<last 8 of the id> (:222).
   - HSN is read from the line, which never has one (:210).
   - Quantity is quantity_requested, not what shipped (:211).
   - Serial is serial_number or the line notes (:215).
   - No consignee GSTIN or address is passed (:219-232).
   - No GSTIN is required on either side (:198).
   - render_delivery_challan (print_render.py:397) has no value column at all.
5. Complete: transfers.py:1713 books the FIN-3 mirror bill (_book_mirror_purchase). Its gate at :2247 already treats "different company OR different state" as crossing a registration. It reads the same Rs 0 line cost (:2167), so it books taxable 0. reports/gstr1.py:300-326 then puts that bill on the sender's GSTR-1 as a B2B deemed supply.

TESTS: 15 xfail(strict=True) plus 3 guards. Result: 3 passed, 15 xfailed. I ran them with --runxfail and every one fails at its intended assertion, not in setup.
- F51 xfails:
  - the challan number is the TRF number
  - each line carries the product's HSN
  - each unit is valued at its own cost of 1850, and the 2000 product average never appears
  - the stored total_value and line unit_cost come from the units' own cost
  - a request for 3 with 2 on the shelf is valued at 3700, not 5550
  - both GSTINs and the consignee address are printed
  - the shipped unit barcodes are printed
  - shipping with no carrier never reads "None"
  - a challan printed before ship is refused (4xx) or valued
  - a challan is refused when either side has no GSTIN (parametrised)
- D13 xfails:
  - the challan is valued exactly when the GSTINs differ. Bokaro (other company) and Pune (same company, other state) are xfail; same-GSTIN Dhanbad is a passing guard
  - any mirror bill carries the same value as the challan
- D7 xfail: SALES_STAFF reading a costed transfer through GET /transfers and /transfers/{id} sees no cost. Today both return unit_cost and total_value unmasked.
- Guards (pass now, must keep passing):
  - printing the challan writes nothing anywhere, so it is never a GSTR-1 row
  - a counter role's challan carries no cost
  - the same-GSTIN challan stays unvalued
  I checked the guards catch a leak with a scratch probe (not committed).

The harness is mongomock plus the real StockRepository, with the Shopify write-back stubbed. GSTINs are synthetic, checksum-valid ones.

CHECKS
- The new file plus test_delivery_challan, test_transfer_mirror_purchase, test_idor_transfers, test_rbac_access_matrix/enforcement/policy and test_policy_registry_guard/engine_e2: 621 passed, 15 xfailed.
- Smoke import: 1307 routes.
- pylint E/F: the only message is E0401 "Unable to import 'redis'" in api/services/cache.py. That is the local venv missing redis and nothing in this branch; CI installs it.

FOR THE BUILD / OWNER
1. Money tension (owner/CA): D13 says the paper is a valued challan, "NOT a tax invoice". But the existing FIN-3 mirror bill already reports the same move on the sender's GSTR-1 as a B2B invoice TRF/<number>, and on the receiver's GSTR-3B as input credit. Valuing the lines will turn that bill from Rs 0 into real GST figures. I did not touch it. The mirror-bill test only requires that any bill agrees with the challan value, so it holds whether the bill stays or goes.
2. "Different GSTINs" interpretation: I read same company across states (e.g. Dhanbad to Pune) as crossing, the same gate as the mirror bill. That is the [ST-PUN-1] xfail. Flip it if the owner meant companies only.
3. One rule: the consignor/consignee GSTIN should come from org_validation.shop_gstin, which is in draft #1167 (not merged). #1167 also rewrites transfers.py's mirror-bill GSTIN helpers into _shop_gst, and #1163 changes LegalHeader's GSTIN. The build should share one "crosses a registration" predicate between the mirror bill and the challan, building on #1167 once it merges or with a small edit to keep the merge clean.
4. Counter roles can print transfer challans today (_CHALLAN_ROLES includes SALES_STAFF and SALES_CASHIER). Under D7 a valued challan must either be refused for them (recommended) or printed without figures. Transfer reads must also mask cost via cost_mask (#1161 adds a "product" context for managers).
5. Not pinned, left for the build to decide:
   - units of mixed cost on one line (unit_cost as an average vs per-unit rows)
   - what happens when no unit carries a cost (product cost_price fallback?)
   - numbering, HSN and barcodes for same-company challans ("keep today's challan")
   - the consignee's legal name
6. The existing test_delivery_challan asserts "DC/TRF/" in the challan body (that fixture has no transfer_number) and will need updating when the numbering changes.

## Review passes (run after the fix, one after another)
- **one-rule**: Exactly one implementation of every rule this change adds or uses (grep backend and frontend for a second copy); no route masks cost or decides roles on its own list; money figures come from the existing single sources.
- **correctness**: Drive every owner-ruled behaviour and the audit rows with test inputs, including edge cases (two shops, two companies/GSTINs, zero and missing values, a unit already sold or transferred, concurrent requests); nothing raises; no data is lost.
- **roles-screens**: Every new or changed route has its rbac_policy row equal to its code gate; counter roles see no cost/supplier/money; new screens have an e2e/fixtures/routes.ts row and read correctly on a landscape 1024x768 tablet; tsc/vite/touched vitest green.

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
- No PR exists yet: open a DRAFT pull request against main titled `feat(inventory): transfers between two of our companies carry a valued delivery challan with both GSTINs (audit F51, owner ruling D13)`, body = the owner ruling, what was built, the rules, the checks, the follow-ups, ending with the line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Post ONE comment on the PR whose first line is `CLOUD ROUND DONE: CLEAN <head sha>` or `CLOUD ROUND DONE: OPEN (n) <head sha>`, then: what you fixed (problem -> fix -> commit -> revert proof), any problem still open, the follow-ups, and every check with pass counts.
- Keep the PR a DRAFT. Do NOT merge, do not mark it ready, never use --admin. Another Claude session reviews and merges it.

