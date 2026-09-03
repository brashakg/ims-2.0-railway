# IMS 2.0 — live plan status

Updated **2026-09-03**, after the PR stack was rebased onto main. This file is the single place to see what is done, what
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

**Found while verifying, not fixed:** `find_overdue` binds a datetime against
`expected_delivery`, which is stored as a string, so `/orders/overdue/list` has
been returning nothing in production.

---

## 2. Wave 2 page splits — tabs become addresses

Owner's order: POS → Clinical → Inventory → Reports → Tasks → Customers → HR.
Sequence adjusted with reasons (Tasks needed decisions, HR was smallest).

| Module | What was wrong | Status |
|---|---|---|
| **Reports** | 5 sections in one 1,345-line page; 16 data calls before any click; GST returns were pop-ups | **MERGED #1086** |
| **HR** | 7 tabs, one URL; salary screens open to 5 roles against admin-only endpoints | **PR #1088** (in CI) |
| **Tasks** | Two rival pages with opposite permissions; fabricated SOPs; 50-task blindness | **DONE** |
| **Customers** | 10 finished screens in no menu; no address for a customer profile | **DONE** |
| **Clinical** | 5 hidden tabs, two rival prescription doors | **DONE** — weaker Rx door deleted |
| **Inventory** | 19 sections in one file, 3 in the menu | **DONE** — 17 real pages |
| **Catalog** | Mostly split already; needs the review queue + photo work | **TODO** — designed |

---

## 3. Security — found while doing the above

| Finding | Severity | Status |
|---|---|---|
| Churn-risk list returned 500 **complete customer records** to any signed-in user | High | **DONE** |
| RFM report published company-wide average customer value to any signed-in user | Medium | **DONE** |
| **13 of 19 customer doors trusted an id as authority — 4 moved money** | High | **MERGED #1089** |
| Login limiter locked out a **whole shop** on five typos across three people | High | **DONE** |
| Two IP readers, one spoofable and one returning a constant | Medium | **DONE** |
| Salary screens reachable by 3 roles the endpoints refuse | Medium | **PR #1088** (in CI) |

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
| **Catalog** — review queue + the photo work | designed |
| **Staff sign-in gate** — approved devices (WebAuthn passkeys); ADMIN/SUPERADMIN never gated | **BUILT, shipped OFF** — owner arms it |
| Three screens each re-map the store's staff list their own way (`NewTaskModal`, `SalespersonPicker`, the new `useStoreStaff`) | small follow-up |

## 4c. Found and NOT fixed — recorded so they are not lost

| Finding | Why it is still open |
|---|---|
| `find_overdue` binds a datetime against `expected_delivery`, stored as a string, so **`/orders/overdue/list` returns nothing in production** | Found while verifying the delivery work; fixing it properly means settling that field's storage shape first |
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

**Retiring the legacy till** (owner: "retire old pos") - **WIP**, salvage
inventory first. Nothing is deleted until it is proven replaced.

## 5. Waiting on the owner

| Question | Why it matters |
|---|---|
| **Commission Leaderboard** shows per-staff revenue and commission in rupees to 5 roles | Money beside named staff; the salary ruling was deliberately strict |
| **Apply-for-leave has no screen** — nobody in the company can request leave | The whole approve/reject chain is built and running |
| **Thermal or A4** receipts at the counter? | Decides whether a missing thermal receipt blocks the POS switchover |
| Is *"every bill needs a customer"* deliberate? | The new till is built as if it is |
| **`app.uniparallel.com` move** | Passkeys bind to the web address; the device gate must not be enrolled before a domain change |

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
