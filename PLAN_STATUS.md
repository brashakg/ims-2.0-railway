# IMS 2.0 — live plan status

Updated **2026-09-05**. The whole 09-03/04 wave is on main: **#1088** (46 commits, six CI rounds each catching one real find), **#1090** (household guard) and **#1091** (weightings admin-only), then six more squash-merged on 2026-09-04 — **#1093** overdue list, **#1095** till-retirement orphans, **#1096** Add-a-Product, **#1097** store picker, **#1098** catalog review queue + one photo rule, **#1099** eye examination as a page (section 4i). One PR open: **#1094 (draft)**, courier COD collects the balance due — NOT merged. Two new decisions owed (section 5). This file is the single place to see what is done, what
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
| **Customers** | 10 finished screens in no menu; no address for a customer profile | **DONE** |
| **Clinical** | 5 hidden tabs, two rival prescription doors | **DONE** — weaker Rx door deleted; the eye examination is its own page, **MERGED #1099** |
| **Inventory** | 19 sections in one file, 3 in the menu | **DONE** — 17 real pages |
| **Catalog** | Mostly split already; needs the review queue + photo work | **MERGED #1098** (review queue at `/catalog/review`, photo column, `/catalog/missing-photos`) + **#1096** (Add-a-Product) |

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
| **Catalog** — review queue + the photo work | **MERGED #1098** + **#1096** (section 4i); MEASURED + REPAIRED on prod 2026-09-05: twins with a usable photo 6 -> 70 (64 repaired, nothing queued, publish still manual); 37 never-pushed products now qualify to go online; only 7 products have no photo anywhere |
| **Staff sign-in gate** — approved devices (WebAuthn passkeys); ADMIN/SUPERADMIN never gated | **BUILT, shipped OFF** — owner arms it |
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
| COD booking sends `sub_total = grand_total`, not `balance_due` (over-collects on a partly-paid COD) | `services/shiprocket.py` | **PR #1094 (draft, NOT merged)**: COD sends `balance_due`; round-1 adversarial review's 9 findings fixed (39 probes kept), round-2 review in progress. Owner decision owed: a COD remittance door — a courier's collection is never recorded as a payment, so the order stays unpaid |
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

## 5. Waiting on the owner

| Question | Why it matters |
|---|---|
| **COD remittance door** — a courier's collection is never recorded as a payment, so a COD order stays unpaid in IMS after the customer has paid at the door | Surfaced by #1094; until it exists every COD order shows a false balance |
| **36px or 44px controls** — the app-wide `.input-field` is 36px; the Add-a-Product design assumed 44px touch targets | Raising it changes every form in the app; found in #1096, deliberately not decided there |
| **`app.uniparallel.com` move** | Passkeys bind to the web address; the device gate must not be enrolled before a domain change |
| **Commission Leaderboard** shows per-staff revenue and commission in rupees to 5 roles | **RULED 09-03**: ADMIN/SUPERADMIN see all; everyone else, managers included, sees own figures + rank. **Not yet built** — `get_commission_leaderboard` still reveals names to AREA/STORE managers |
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
