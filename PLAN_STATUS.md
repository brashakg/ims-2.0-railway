# IMS 2.0 — live plan status

Updated **2026-09-06**. Thirteen more squash-merged on 2026-09-06 (section 4l), and with them **all five waves of the 2026-08-30 page-split plan are on main**: **#1110** sales roles book eye tests (closes the last section-5 decision), **#1111** + **#1112** the Wave 1/2 loose ends (last `?tab=` links gone; recalls at `/customers/recalls`), **#1113-#1115** the Wave 3 file diets (QuickAdd, Jarvis, Workshop pages), **#1116-#1122** the Wave 5 backend packages (finance, vendors, reports, inventory, orders, rbac_policy, shopify_push — every one a pure move, byte-identical API, 1,313 routes throughout). On prod (read-only, 2026-09-06): the 6 live products are untracked on Shopify and on sale at quantity 0, 0 Shopify orders since the 27 Aug cut-over, all 11 webhooks point at IMS — the sync-path audit's five gaps are the next queue, website stock first (section 4b). Then three more on 2026-09-06: **#1124** the scheduled live sync + manual button + Superadmin settings, **#1125** website stock made real, **#1126** delist on retire (section 4m). The first "Push stock" press is **NOT done** — it is blocked on two owner decisions (section 5): Shopify has two locations fulfilling online orders, and IMS's ledger holds 1 unit against 49 on Shopify, so a press today would mark the whole website sold out. Then **#1128** photos and the title follow IMS onto live products, **#1129** tags ownership, **#1130** missed-order catch-up — so **all five sync-audit gaps the owner ordered are on main** (section 4m). In flight: audit item 7, a failed price push keeps the product queued (`fix/shopify-price-push-requeue`, no PR yet). The 09-05/06 wave (#1094, #1102-#1108, sections 4j-4k) is all on main. Section 5: nothing owed. This file is the single place to see what is done, what
is in flight, and what is waiting on a decision. Claude keeps it current; if it
disagrees with reality, the file is wrong and should be fixed.

Status key: **DONE** merged · **PR** open, awaiting merge · **WIP** agent working
· **BLOCKED** waiting on the owner · **TODO** not started

---

## 1. POS switchover — all five blockers closed

The new till at `/pos/new`, `/pos/counter`, `/pos/delivery` could not replace the
classic one until these were fixed. Every one deleted the classic till's copy
rather than duplicating it, so both tills now run the same code.

| # | Blocker | Status |
|---|---|---|
| 1 | New screens in no menu — reachable only by typing the address | **MERGED #1085** |
| 2 | A first-time customer could not be billed at all | **MERGED #1085** |
| 3 | Hold / recall a bill (the till auto-parks on idle — that work was unrecoverable) | **MERGED #1085** |
| 4 | Made-to-order lens could not be rung up (no barcode to scan) | **MERGED #1085** |
| 5 | Paper prescription could not be entered at the till | **MERGED #1085** |

**Two reported blockers were checked and disproved** — no code was written for
them: the workshop job is created from the *items* and is `sale_type`-agnostic;
and `is_advance_payment` never reaches the server, so part-payment already works.

### Delivery counter (owner review of the live screen, 2026-09-03)
| Item | Status |
|---|---|
| Split payment + credit-delivery options missing | **DONE** |
| Match the POS design language | **DONE** |
| Log which salesperson handed the goods over | **DONE** |
| 30-day window on pending deliveries (admin/superadmin exempt) | **DONE** |
| Search by customer name and phone, not just job card | **DONE** |

Root cause of the first three: the delivery screen hand-rolled its own
three-button payment UI instead of rendering the shared `StepPayment` the other
two tills use. That copy is deleted; reusing the real one restored split tender,
credit, vouchers, loyalty, EMI and cash denominations — and the design language —
in one move, and every guard now runs verbatim instead of in a weaker
transcription.

The search used to fetch the newest twenty orders of any status and match the
number exactly, client-side — so a customer who had lost their job card could not
be served. It now searches the delivery queue by number, name or phone; several
matches are chosen from, never guessed.

**Found while verifying, since fixed — MERGED #1093:** `find_overdue` bound a
datetime against `expected_delivery`, which is stored as an ISO string, so
`/orders/overdue/list` had been returning nothing in production. One
`iso_date_window` helper now serves the orders, workshop and prescription windows.

---

## 2. Wave 2 page splits — tabs become addresses

Owner's order: POS → Clinical → Inventory → Reports → Tasks → Customers → HR.
Sequence adjusted with reasons (Tasks needed decisions, HR was smallest).

| Module | What was wrong | Status |
|---|---|---|
| **Reports** | 5 sections in one 1,345-line page; 16 data calls before any click; GST returns were pop-ups | **MERGED #1086** |
| **HR** | 7 tabs, one URL; salary screens open to 5 roles against admin-only endpoints | **MERGED #1088** |
| **Tasks** | Two rival pages with opposite permissions; fabricated SOPs; 50-task blindness | **DONE** |
| **Customers** | 10 finished screens in no menu; no address for a customer profile | **DONE** — **found 2026-09-06 (#1111):** the "split" had only moved the route registry; recalls was still an in-page tab. **MERGED #1112**: `/customers/recalls` is a real address (same `RecallManager`, in-page branch deleted, nav row "Sales floor -> Recalls") |
| **Clinical** | 5 hidden tabs, two rival prescription doors | **DONE** — weaker Rx door deleted; the eye examination is its own page, **MERGED #1099** |
| **Inventory** | 19 sections in one file, 3 in the menu | **DONE** — 17 real pages |
| **Catalog** | Mostly split already; needs the review queue + photo work | **MERGED #1098** (review queue at `/catalog/review`, photo column, `/catalog/missing-photos`) + **#1096** (Add-a-Product) |

**Wave 1/2 loose ends closed — MERGED #1111 + #1112 (2026-09-06):** the last
`?tab=` links became real addresses (QuickAdd x2 -> `/inventory/stock?search=`,
reports -> `/inventory/stock` and `/inventory/transfers`), `pages/catalogue`
folded into `pages/catalog`, stale-doc banners on `docs/reference/*Feature_Status*`
and `IMS2_COMPLETE_FEATURE_LIST.md`. The app now has ZERO live `?tab=`
navigations — the mentions that remain are the forwarding shims in
`routes/*Routes.tsx` and comments. Waves 3 and 5 (the file diets) are in
section 4l; **all five waves of the 2026-08-30 page-split plan are on main.**

---

## 3. Security — found while doing the above

| Finding | Severity | Status |
|---|---|---|
| Churn-risk list returned 500 **complete customer records** to any signed-in user | High | **DONE** |
| RFM report published company-wide average customer value to any signed-in user | Medium | **DONE** |
| **13 of 19 customer doors trusted an id as authority — 4 moved money** | High | **MERGED #1089** |
| Login limiter locked out a **whole shop** on five typos across three people | High | **DONE** |
| Two IP readers, one spoofable and one returning a constant | Medium | **DONE** |
| Salary screens reachable by 3 roles the endpoints refuse | Medium | **MERGED #1088** |

---

## 4. Data integrity — found and fixed

| Finding | Status |
|---|---|
| Optometrist's Rx remarks discarded on save; the **patient's complaint printed in their place** | **DONE** |
| Three prescription date filters compared datetimes against strings — matched nothing, silently | **DONE** |
| One frame written five ways became **four products**; `identity_key` unique index did not exist in production | **DONE** |
| Discount cap bypassable by adding the item after saving the draft; lens lines exempt entirely | **DONE** |
| GSTR-1 and GSTR-3B disagreed on the tax head for the same refund | **DONE** |
| A refund spanning a month boundary reversed tax **twice** | **DONE** |

---

## 4b. Still to build (nothing is blocked on a decision)

| Work | State |
|---|---|
| **Catalog** — review queue + the photo work | **MERGED #1098** + **#1096** (section 4i); MEASURED + REPAIRED on prod 2026-09-05: twins with a usable photo 6 -> 70 (64 repaired, nothing queued, publish still manual); 37 never-pushed products now qualify to go online; only 7 products have no photo anywhere; **first 6 pushed LIVE 2026-09-06** (section 4k) |
| **Staff sign-in gate** — approved devices (WebAuthn passkeys); ADMIN/SUPERADMIN never gated | **BUILT, shipped OFF** — owner arms it |
| **Website stock made real** — sync-audit gap 1: the 6 live products are untracked on Shopify and on sale at quantity 0 (section 4k) | **MERGED #1125** (section 4m). The one-time "Push stock" press is NOT done — blocked on the owner (section 5) |
| **Scheduled live sync** — products already on Shopify re-push on change at 01:00 and 09:00 IST, a "Sync live products now" button, Superadmin settings (owner ruling 09-06, section 6). First publish of a never-pushed product stays a human press | **MERGED #1124** (section 4m) |
| **Sync-audit gaps 2-5**, in the owner's order: (2) delete/archive takes the product off sale; (3) photo + name changes reach live products; (4) tags; (5) missed-webhook catch-up | (2) **MERGED #1126**; (3) **MERGED #1128**; (4) **MERGED #1129**; (5) **MERGED #1130** — all five done (section 4m). Follow-ups logged: products live before #1128 carry no photo map (hands-off until adopted by a one-off script on an explicit id list); missed *status* webhooks on already-booked orders are not swept |
| Three screens each re-map the store's staff list their own way (`NewTaskModal`, `SalespersonPicker`, the new `useStoreStaff`) | small follow-up |

## 4c. Found and NOT fixed — recorded so they are not lost

| Finding | Why it is still open |
|---|---|
| `find_overdue` binds a datetime against `expected_delivery`, stored as a string, so **`/orders/overdue/list` returns nothing in production** | **MERGED #1093** — the field stays an ISO string; `iso_date_window` binds the window as strings, and the workshop and prescription windows use the same helper |
| The 30-day clamp on the customer list | Owner chose "clamp on last activity" — no last-activity field exists yet |

## 4d. The money-path audit (2026-09-03) - 20 raised, 8 killed, 12 confirmed

Five independent lenses swept POS -> payment -> delivery -> invoice; every
finding was then attacked by three separate refuters, majority required to kill.
A completeness critic then asked what all five had missed.

| Confirmed finding | Status |
|---|---|
| Refused handover charged the customer TWICE (delivery counter) | **DONE** |
| Goods left unpaid via the pickup scan, workshop status, courier booking - and a 4th, lab-routing | **DONE** - one shared gate, four doors |
| Credit note reversed under the wrong tax head; GSTR-1 and 3B disagreed | **DONE** |
| Credit note booked under the cashier's store -> one refund, two GSTINs | **DONE** |
| In-store return backed out GST at one dominant rate | **DONE** - exact per-line engine, copy deleted |
| Discount caps silently no-opped on add-item-to-order | **DONE** |
| Promo clamp read a merchandising label, not the discount tier | **DONE** |
| Loyalty points burned AND the customer still billed | **DONE** |
| Fabricated invoice number on printed paper | **DONE** - legacy invoice retired |
| Cashiers could not submit the blind day-end count | **DONE** |
| Store credit issued with no way to spend it | **WIP** |
| Cancelling an order with an advance has no door to return the cash | **TODO** |

## 4e. POS surfaces - owner review 2026-09-03

"Complete sale" on the general counter did nothing: `try/finally` with no
`catch`, so anything that threw vanished silently. **DONE**, both surfaces, and
that screen had NO tests at all before today.

An evidence-backed comparison found the counter is not a different design - it
is billing minus four capabilities, plus a legitimately re-shaped browse grid
whose card was re-typed and has drifted five ways. The cart is genuinely ONE
shared component; it only looks different because counter carts never hold
optical lines. **WIP**: all four gaps, the shared product card, 44px cart
controls, per-sale receipt choice, and customer edit at the till.

**Retiring the legacy till** (owner: "retire old pos") - **DONE**: retired in
**#1088** (`POSLayout` deleted, `/pos` redirects), salvage triage finished in
**#1095**. Nothing was deleted until it was proven replaced.

## 4f. Owner rulings 2026-09-04 - in build

| Decision | Status |
|---|---|
| Counter ALWAYS mints the tax invoice; per-sale receipt chooser dropped (no numberless receipt exists) | **WIP** |
| Store credit enabled at the delivery counter | **WIP** |
| Family-member number at create: BLOCK, with promote-to-own-account / open-existing popup | **DONE** |
| Two find-or-create implementations collapsed into one; race on the unique index returns 409 not 500 | **DONE** |
| Email-only web buyers wrote `mobile: ""` (indexed; 2nd one becomes a phantom customer) | **DONE** |
| Cashiers + sales staff: full customer edit incl. phone + GSTIN, from both tills | **DONE** |
| Counter sale marked DELIVERED at completion, cashier as handover (owner 09-04) -- through the existing /ready + /deliver doors; a HOME-DELIVERY counter bill is deliberately NOT stamped (parcel still on the packing desk) | **DONE** |
| Optical till: 'Order receipt (A4)' relabelled 'Tax invoice (A4)' -- serial mints at sale (owner 09-04) | **DONE** |
| Reverse split: adding a family member whose number is already a customer -- BLOCKED at all 3 doors; link-existing NOT built (cannot be truthful: POS Rx gate + clinical readers key on customer id); popup opens their own account | **DONE** |
| Member-on-two-accounts (child on both parents') -- BLOCK, one household account (owner 09-04) | **MERGED #1090** |
| Member rows minted in 3 places with drifting `relation` defaults and no `created_at` | **TODO** (one-rule-two-implementations) |
| Legacy till RETIRED: /pos redirects to /pos/new (query kept), POSLayout deleted, 8 legacy-vehicle tests re-pointed, a recorded-axis-0 bug caught on the new picker. Follow-ups: Playwright specs re-pointed (DONE); POSReceipt.tsx orphan deleted (**#1095**); PIXEL audit list still names /pos (follows redirect) | **DONE** |

Production check 2026-09-04 (read-only): `customers.mobile` unique index EXISTS; 779
customers; 0 duplicate top-level numbers; an early probe said 9 family members also exist as their own
customer, but it counted holders' own Self rows -- the committed report finds 0 real splits; 1 null + 1 empty mobile (one row away from an index collision).

## 4g. Found, not fixed (this wave) - recorded so they are not lost

| Finding | Where | Why open |
|---|---|---|
| Repair-portal DELIVERED is unbilled BY DESIGN | `repair_portal.py` | revisit before repairs carry charges at go-live |
| COD booking sends `sub_total = grand_total`, not `balance_due` (over-collects on a partly-paid COD) | `services/shiprocket.py` | **MERGED #1094**: COD sends `balance_due`; two adversarial rounds (39 probes kept). Round 2 fixed: `gate_credit_delivery` reads through the shared `order_balance_due` (a balance-less legacy row no longer reads Rs 0); a SIMULATED dry-run booking no longer 409-locks the order; the shipping card stops guessing a missing balance and offers "Book again anyway" on a 409; non-finite amounts refused. Left by its author: the duplicate-shipment lookup fails soft (no DB, no guard); a booking is not amended when the balance is paid at the counter afterwards; a COD REMITTANCE door is an owner decision (section 5), not built |
| lab-routing scan reply lacks a PAYMENT_DUE sentence (blocks correctly, says only the code) | `services/lab_routing.py` | cosmetic |
| `find_overdue` datetime vs string `expected_delivery` -> `/orders/overdue/list` empty in prod | `order_repository.py` | **MERGED #1093** — the string stays; one `iso_date_window` helper for the orders, workshop and prescription windows |
| Optical SALE-stage WhatsApp `ORDER_CONFIRMED` now seeded; owner must map the real approved template name before arming | `notification_templates.py` | owner paperwork (DLT) |
| Stale worktrees: inventory delivered; owner ruled REMOVE ALL 15 (msgv verified as a stale 08-27 snapshot, nothing unmerged) -- 15 removed, 0 failed | git worktrees | **DONE** |
| `POSReceipt.tsx` orphaned after the till retirement; `calculateGST`/`calculateIGST` in `constants/gst.ts` have zero callers | frontend | **MERGED #1095** (salvage look first): POSReceipt + ReceiptPreview (thermal; no live screen rendered it -- both surfaces' Receipt button is `window.print()`; owner ruled A4 + WhatsApp) + `utils/receiptFormat.ts` (client copy of `portal._describe_for_customer`, pin ported to `test_portal.py`) + both GST calculators DELETED. Guards in `ServerInvoiceOnly.test.tsx` (thermal shape, GST-extraction shape, export check); z-layer test re-pointed at WorkshopJobCardPrint. Left: PrintPage's "Thermal receipt" template card + `thermal_receipt` override key describe a document nothing renders; posStore's on-screen cart GST extraction is a live second copy of split_gst (display only) |
| Pune store's `store_id` is a UUID (`4dc49c44-...`) while every other store uses its code (`BV-DHN-02`, `WIZ-DHN-01`...) as the id; `store_code` is `BV-PUN-01` | `stores` collection (prod) | possibly related to the open "Pune 2 orphan units"; needs a look before any store-id join |
| PIXEL audit list still names `/pos` (follows the redirect) | `agents/implementations/pixel.py:107` | harmless |
| Member rows minted in 3 places, drifting `relation` defaults, no `created_at` | `customers.py` / `customer_service.py` | **MERGED #1090**: one `make_patient_row`, five sites, default `Other` (a silent `Self` would make the member the holder) |
| `GET /incentive/points/settings/eligibility` (and `/settings/effective`, a second leak) revealed per-person commission weight/bonus % | `points.py` | **MERGED #1091** |

## 4h. Closed by owner statement

| Item | Status |
|---|---|
| BV-ONLINE-01 GSTIN (was 'owner owes', hard-blocking web-order invoices) | **CLOSED 09-04** -- verified in prod: identical to the Pune store's (same entity, state 27); no write needed |

## 4i. Designed screens landed 2026-09-04 (all squash-merged to main)

| Screen | What changed | Status |
|---|---|---|
| **Add-a-Product** | The eleven designed tweaks: still-missing chips, section error counts, HSN inline error, touch targets, GST as text, chips with words, review-card real labels, Online un-muted, action-first inventory, header wrap + tile floor, tag label. Field order, section order, the 12 categories and the POSTed payload are unchanged. Found: the app-wide `.input-field` is **36px**, not the 44px the design claimed -- raising it is a separate decision (section 5) | **MERGED #1096** |
| **Store picker** `/select-store` | Names wrap, grouped by `store.brand`, online stores in their own section with live/dark from the storefront posture, "Where you were" + Enter, filter, digit picks. Presentation only -- same `setActiveStore` path | **MERGED #1097** |
| **Catalog review + photos** | Review queue at its own address `/catalog/review` (old `?segment=review` links forward); photo column + filter; `/catalog/missing-photos` work list; `has_photo` / `online` computed on the server from the push's own predicate; counts from the server. **Real bug fixed:** `_build_pim_doc` never projected the spine's `images` onto the catalog twin, so catalogued products were refused as "no photograph"; `scripts/backfill_twin_images.py` (dry-run by default) repairs existing twins. **Correction:** the earlier reading that photography is the go-live bottleneck came from this projection bug, not from a photo shortage; MEASURED + REPAIRED on prod 2026-09-05: twins with a usable photo 6 -> 70 (64 repaired, nothing queued, publish still manual); 37 never-pushed products now qualify to go online; only 7 products have no photo anywhere | **MERGED #1098** |
| **Eye examination** | Its own page at `/clinical/test/:entryId` (and `/clinical/test/amend/:testId`); the `EyeTestForm` modal is DELETED; a staff-only internal note kept off print, portal and wire. **Correction:** the split did NOT widen `/clinical/conversion` -- optometrists always had it and the backend deliberately serves them a revenue-stripped row; the gate is now pinned both ways by test | **MERGED #1099** |

## 4j. Merged 2026-09-05/06 (all squash-merged to main)

| PR | What changed | Status |
|---|---|---|
| **#1094** courier COD | Collects `balance_due`, not `grand_total` — detail in section 4g | **MERGED #1094** |
| **#1102** layout gate | `.ims-anim-page` entry animation no longer leaves a transform on the page wrapper (fill `both` -> `backwards`), so `fixed inset-0` overlays anchor to the viewport again; the e2e probe now treats closed `<details>` content as hidden. KNOWN_BROKEN shrank by two: `hr salary-config` (genuinely fixed) and `online-store new-smart-collection` (a probe artefact). The other too-wide rows are real width problems, unchanged | **MERGED #1102** |
| **#1103** top menu | Owner could not scroll the Stock & supply submenu on a tablet: `vh` sized the dropdown under the browser toolbar. Dropdowns now size to the visible viewport (`dvh`) and stay finger-scrollable (`touch-action: pan-y`) | **MERGED #1103** |
| **#1104** leaderboards | Everyone below ADMIN/SUPERADMIN sees their own row + rank only — one trim on `/incentive`, the `/payroll` commission leaderboard + summary, and the analytics-v2 staff leaderboard, which had **no gate at all** and leaked every colleague's revenue to any signed-in role. SALES_STAFF/CASHIER admitted to `/incentive/leaderboard`; NOT to `/hr/leaderboard` (the payroll mount is finance-only, pinned by test). Closes the section-5 leaderboard row | **MERGED #1104** |
| **#1105** Shopify push | `PUBLISH_SCOPE_MISSING` code + a plain message when the publish step is denied (the product create had already succeeded); `productOptions` sent on CREATE only (Shopify rejects it on update); failed pushes show the server's message on `/online-store/products` and `/online-store/shopify` | **MERGED #1105** |
| **#1106** POS customer panel | The four widget tiles (Family Rx, Dues, Offers & loyalty, My day) open a slide-over on the till (bottom sheet on phones) instead of navigating; "Bill for <member>" keeps the cart; money is read-only in the panel; one-tap "Book eye test" into today's clinical queue (reads the queue first, never adds twice); WhatsApp recall reminder queued via flow key `PRESCRIPTION_EXPIRY`; new `GET /incentive/points/my-day` (own data only). Owner decisions for all six behaviours recorded 09-06. **Open:** the server refuses "Book eye test" for SALES roles (section 5) | **MERGED #1106** |
| **#1107** catalog twin | Spine edits now mirror brand/category/attributes/tags/gtin onto the catalog twin the push reads — vendor, metafields and tags no longer go stale after an edit. **Measured first (prod, read-only):** the "Shopify did not get the descriptions" report is NOT a push fault — only 10 of 76 products have a description in IMS at all; the `ims.*` attribute metafields DO reach Shopify but need theme blocks to render | **MERGED #1107** |
| **#1108** push timeout | The shared axios 10 s timeout produced a FALSE "Network error" on a push that had succeeded. Push requests now get a sweep-sized timeout (180 s sweep, 60 s single entity) and an honest "still running on the server, do not press again" message | **MERGED #1108** |

## 4k. Storefront — what happened on prod (dated facts)

| Date | Event |
|---|---|
| 2026-09-05 | Catalog twins' `images` backfilled from the spine (dry-run measured first): twins with a usable photo **6 -> 70**; 37 never-pushed products now qualify; 7 have no photo anywhere; nothing queued |
| 2026-09-06 | Root cause of every failed LIVE push: the installed Shopify app "BV Inventory-1" had 23 scopes and NO `write_publications` / `read_publications` — the store had never been asked to accept the July app version's scopes (legacy install flow). Fixed by opening the app's own OAuth authorize URL in the owner's admin; owner pressed Update; 25 scopes verified; backend redeployed to drop its cached token |
| 2026-09-06 22:24 UTC | Owner authorised the assistant to press the Products "Push": **6 IMS products went LIVE on bettervision.in** (verified ACTIVE + published on Shopify), 4 refused for no photo — the first IMS-originated products on the storefront |
| 2026-09-06 | Sync-path audit, read-only: all 6 IMS-pushed products are `tracked=false` on Shopify and on sale at quantity 0; **0 Shopify orders** since the 27 Aug webhook cut-over; 11 webhook subscriptions, all pointing at the IMS receiver. The audit's five gaps are the next queue, in the owner's order (section 4b) |
| 2026-09-06 (after #1125 deployed) | Read-only probe before the authorised "Push stock" press: Shopify has **two** active locations that fulfil online orders (Better Vision Sector 4, Bokaro: ships nothing, 0 units; Gangadham Pune: the only shipping location, all **49** available units); 42 IMS products carry a Shopify id, 53 variants (47 tracked, all DENY). IMS's own ledger holds **1 unit** for those products (the Gucci) — the Ray-Ban Meta quantities were typed into Shopify by the connector and never entered in IMS. **Press NOT done**: it would set 46 variants to 0. Shopify's per-variant quantities were snapshotted first. Decision returned to the owner (section 5) |

## 4l. Merged 2026-09-06 — Waves 1-5 of the page-split plan closed (all squash-merged to main)

| PR | What changed | Status |
|---|---|---|
| **#1110** clinical queue | Sales roles (`SALES_STAFF`, `SALES_CASHIER`; not bare `CASHIER`) may add a customer to today's eye-test queue (`_QUEUE_ADD_ROLES` in `clinical.py`); Rx create/edit stays closed to them. Closes the section-5 "Book eye test for sales roles" decision | **MERGED #1110** |
| **#1111** Wave 1/2 loose ends | Last `?tab=` links -> real addresses; `pages/catalogue` folded into `pages/catalog`; stale-doc banners on the two reference lists. **Finding:** the Customers "split" had only moved the route registry — recalls was still an in-page tab | **MERGED #1111** |
| **#1112** recalls | `/customers/recalls` is a real address (same `RecallManager`; in-page branch deleted; campaigns redirect moved into the shim table; nav row "Sales floor -> Recalls"). Zero live `?tab=` navigations remain — Wave 2 genuinely complete | **MERGED #1112** |
| **#1113** Wave 3 | `QuickAddPage.tsx` 3,212 -> 229 lines; 12 block files under `pages/catalog/quickadd/`. Proof: unchanged tests, POSTed payloads identical key-for-key, rendered HTML byte-identical, line-multiset audit | **MERGED #1113** |
| **#1114** Wave 3 | `JarvisPage.tsx` 2,215 -> 103 lines (11 files); same proof standard | **MERGED #1114** |
| **#1115** Wave 3 | `WorkshopPage.tsx` 2,011 -> 114 lines (12 files); same proof standard. **Wave 3 complete** | **MERGED #1115** |
| **#1116** Wave 5 | `routers/finance.py` 6,604 lines -> a 20-file package | **MERGED #1116** |
| **#1117** Wave 5 | `routers/vendors.py` 6,850 -> 21 files | **MERGED #1117** |
| **#1118** Wave 5 | `routers/reports.py` 6,483 -> 17 files | **MERGED #1118** |
| **#1119** Wave 5 | `routers/inventory.py` 5,893 -> 23 files | **MERGED #1119** |
| **#1120** Wave 5 | `routers/orders.py` 6,649 -> 21 files — the POS/money door, split with the owner's approval (section 5) | **MERGED #1120** |
| **#1121** Wave 5 | `services/rbac_policy.py` 7,839 -> 22 files; 1,303 policy rows, registry identical | **MERGED #1121** |
| **#1122** Wave 5 | `services/shopify_push.py` 3,499 -> 13 files. **Wave 5 complete — all five waves done** | **MERGED #1122** |

Every Wave 5 split is a pure move: byte-identical API paths, an empty
whole-app OpenAPI schema diff (1,084 paths), **1,313 routes** before and after
(re-measured on main 2026-09-06), route tables in registration order, AST /
line-multiset equality, no test assertion edited (only the BUG-104 allow-list
paths re-pointed where a moved line sat), package-split tripwire tests
(`test_finance_package_split.py`, `test_inventory_package_split.py`,
`test_orders_package_split.py`, `test_shopify_push_package_split.py`), and
`__init__.py` files that re-export the flat surface and forward monkeypatch
writes into the sub-modules. The largest backend file is now
`routers/returns.py` at 3,754 lines.

## 4m. Merged 2026-09-06 (later) — the Shopify sync gaps (all squash-merged to main)

| PR | What | State |
|---|---|---|
| **#1124** | Scheduled live-product sync at 01:00 and 09:00 IST: re-pushes only products already on Shopify whose IMS copy changed; "Sync live products now" button on the sync page; SUPERADMIN Settings section (times, on/off, per-run cap); every run audited. First publish of a never-pushed product stays a human press | **MERGED #1124** |
| **#1125** | Website stock made real (gap 1): one pooled-quantity rule (on-hand across physical stores minus the safety buffer); online location auto-resolved (env pin -> stored -> the single online-fulfilling location; ambiguous or none -> refused with a code, never a guess); every push sets tracked + policy DENY + real quantities before publish; `POST /online-store/push/stock` + "Push stock" button; the all-pending press runs it too | **MERGED #1125** — the one-time press is blocked on the owner (section 5) |
| **#1126** | Delist on retire (gap 2): deleting or deactivating a product takes it off Shopify (product -> DRAFT, Shopify id kept, never deleted); hooked on the delete door and the three is-active doors; a failed delist shows a "Still live" chip; reactivation queues a republish | **MERGED #1126** |
| **#1128** | Photos and the title follow IMS (gap 3): a media diff on every live push — attach, then delete, then reorder — managing only media IMS itself attached (recorded per product), never a hand upload; a tombstone before every delete; the 250-media cap refused up front; a rename mirrors the title; the twice-daily sync now runs the stock pass too (a no-op until the online location is pinned) | **MERGED #1128** |
| **#1129** | Tags ownership (gap 4): one field for the tags IMS sends (the review box and the add-product door both land there; the dead top-level copy is no longer written); a push records the tags it wrote and on update adds or removes only those, so tags typed into Shopify admin survive; products with no record are adopted add-only | **MERGED #1129** |
| **#1130** | Missed-order catch-up (gap 5): the hourly Shopify pull now feeds any order IMS never received through the same mapper the webhook uses (deduped on the Shopify order id; window = the last successful pull or 48 h, whichever is older; capped per run); pulled orders land in the same inbox so the FAILED queue and Re-map see them; the sweep only books in live dispatch mode. Fact recorded: the webhook path itself has no dark-mode switch and books in every mode | **MERGED #1130** |

## 5. Waiting on the owner

| Question | Why it matters |
|---|---|
| **COD remittance door** — a courier's collection is never recorded as a payment, so a COD order stays unpaid in IMS after the customer has paid at the door | **DECIDED 2026-09-06: NOT NOW.** Courier COD orders stay unpaid in IMS until reconciled by hand; acceptable while there are no courier orders. `cod_amount` on the shipment doc is the figure a future reconcile would net against. Do not re-raise unless courier orders start |
| **36px or 44px controls** — the app-wide `.input-field` is 36px; the Add-a-Product design assumed 44px touch targets | **DECIDED 2026-09-06: LEAVE AT 36px** until staff report the tablets are fiddly. Do not re-raise |
| **"Book eye test" from the POS customer panel, for sales roles** — the one-tap booking calls `POST /clinical/queue`, which the server gates to ADMIN/STORE_MANAGER/OPTOMETRIST | **DECIDED 2026-09-06: LET SALES STAFF BOOK TOO.** Only the add-to-queue door opens to sales roles; Rx create/edit stays closed to them (control test). **MERGED #1110** |
| **Split `orders.py` in Wave 5** — the POS/money door; POS work is ask-first | **DECIDED 2026-09-06: approved.** **MERGED #1120** — pure move, byte-identical API (section 4l) |
| **Website stock: which location, and whose numbers?** — (a) Shopify lists two locations that fulfil online orders; the sync refuses to guess. Pin Gangadham Pune (where all 49 units sit and the only one that ships) or un-tick Sector 4 in Shopify admin > Settings > Locations. (b) IMS holds 1 unit for the 41 mapped products; Shopify holds 49 (typed in by the connector). Pressing "Push stock" now would mark the website sold out. Options: enter the 49 units into IMS as opening stock at Pune, then press; press now and accept sold-out until stock is entered; or leave website stock un-synced for now | **OPEN 2026-09-06.** Until answered the location stays unpinned, so neither the button nor the 01:00/09:00 sync writes any quantity |
| **One real test order on bettervision.in before live selling** — the whole web-order path (webhook → mapper → IMS order with a GST invoice → unit claim) has never run live, and #1130's catch-up should then count it once as already-in-IMS | **OPEN 2026-09-06** — an owner action, not code |
| **`app.uniparallel.com` move** | Passkeys bind to the web address; the device gate must not be enrolled before a domain change |
| **Commission Leaderboard** shows per-staff revenue and commission in rupees to 5 roles | **RULED 09-03**: ADMIN/SUPERADMIN see all; everyone else, managers included, sees own figures + rank. **MERGED #1104** — one trim (`points.self_only_rows`) on `/payroll/commission/leaderboard`, `/payroll/commission/summary`, `/analytics-v2/staff-leaderboard` (which had NO gate) and the `/incentive/points` boards; SALES_STAFF/CASHIER can now open `/incentive/leaderboard` for their own standing, still not `/hr/leaderboard` (finance-only mount) |
| **Apply-for-leave has no screen** — nobody in the company can request leave | **BUILT — MERGED #1088**: apply form on `/my-work` (open to every operational role), feeding the approve/reject chain that was already running |
| **Thermal or A4** receipts at the counter? | **RULED 09-03: A4 + WhatsApp, not thermal** — this unblocked the POS switchover; the thermal renderers went in #1095 |
| Is *"every bill needs a customer"* deliberate? | **RULED 09-03: yes** — no anonymous walk-in sale, no amount threshold; the till already assumed it |

---

## 6. Decided — recorded so they are not re-litigated

- **30-day browse horizon** applies to browsing, never to work in hand. Task
  queues and unpaid balances are exempt. Aged debt stays visible at any age.
- **Follow-ups** keep pending items visible at any age — owner weighed the
  browse risk and chose business value.
- **Team tasks**: managers and above only.
- **Fabricated SOPs**: deleted; the owner supplies the real procedures.
- **Customer list**: clamp on last *activity*, not registration date. Deferred —
  no activity field exists yet, and production has had no orders in 30 days
  because the app is not yet billing live.
- **Staff sign-in**: approved devices only, SUPERADMIN approves from his phone,
  ADMIN and SUPERADMIN never device-gated, no off-site path.
- **Catalog entry**: type in capitals, server stores the right case. Codes stay
  uppercase; a value already mixed-case is never rewritten.
- **Tasks and SOPs go to a PERSON, not a job title** (owner, 2026-09-03). A role
  on an SOP template resolves to the people holding it in that store, so
  "Cashier" becomes Sameer's task and Rupesh's task by name. Every generated
  task used to go to whoever pressed the button, and the template's assignee
  list was read by nothing.
- **POS customer panel** (owner, recorded 2026-09-06): the widget tiles open a
  slide-over on the till, never navigate; "Bill for <member>" keeps the cart;
  money is read-only in the panel; "Book eye test" is one tap onto today's
  queue; the WhatsApp reminder is the household recall (`PRESCRIPTION_EXPIRY`);
  "My day" shows the signed-in person's own figures only.
- **Products already on Shopify sync automatically** (owner, 2026-09-06): a
  scheduled run at 01:00 and 09:00 IST re-pushes every already-pushed product
  that changed, plus a "Sync live products now" button. The FIRST publish of a
  never-pushed product stays a human press. Build in flight on
  `feat/shopify-live-sync` — not merged (section 4b).
- **Sync-path audit order** (owner, 2026-09-06): website stock first, then
  delete/archive takes the product off sale, then photo + name changes reach
  live products, then tags, then missed-webhook catch-up (section 4b).
