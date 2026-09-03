# IMS 2.0 — live plan status

Updated **2026-09-03**. This file is the single place to see what is done, what
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
| 1 | New screens in no menu — reachable only by typing the address | **PR #1085** |
| 2 | A first-time customer could not be billed at all | **PR #1085** |
| 3 | Hold / recall a bill (the till auto-parks on idle — that work was unrecoverable) | **PR #1085** |
| 4 | Made-to-order lens could not be rung up (no barcode to scan) | **PR #1085** |
| 5 | Paper prescription could not be entered at the till | **PR #1085** |

**Two reported blockers were checked and disproved** — no code was written for
them: the workshop job is created from the *items* and is `sale_type`-agnostic;
and `is_advance_payment` never reaches the server, so part-payment already works.

### Delivery counter (owner review of the live screen, 2026-09-03)
| Item | Status |
|---|---|
| Split payment + credit-delivery options missing | **WIP** |
| Match the POS design language | **WIP** |
| Log which salesperson handed the goods over | **WIP** |
| 30-day window on pending deliveries (admin/superadmin exempt) | **WIP** |
| Search by customer name and phone, not just job card | **WIP** |

Root cause of the first three: the delivery screen hand-rolls its own three-button
payment UI instead of rendering the shared `StepPayment` the other two tills use.
Reusing it restores split tender, credit, vouchers, loyalty, EMI and cash
denominations — and the design language — in one move.

---

## 2. Wave 2 page splits — tabs become addresses

Owner's order: POS → Clinical → Inventory → Reports → Tasks → Customers → HR.
Sequence adjusted with reasons (Tasks needed decisions, HR was smallest).

| Module | What was wrong | Status |
|---|---|---|
| **Reports** | 5 sections in one 1,345-line page; 16 data calls before any click; GST returns were pop-ups | **PR #1086** |
| **HR** | 7 tabs, one URL; salary screens open to 5 roles against admin-only endpoints | **PR #1088** |
| **Tasks** | Two rival pages with opposite permissions; fabricated SOPs; 50-task blindness | **WIP** |
| **Customers** | 10 finished screens in no menu; no address for a customer profile | **TODO** |
| **Clinical** | 5 hidden tabs, two rival prescription doors | **TODO** — plan ready |
| **Inventory** | 19 sections in one file, 3 in the menu | **TODO** — designed |
| **Catalog** | Mostly split already; needs the review queue + photo work | **TODO** — designed |

---

## 3. Security — found while doing the above

| Finding | Severity | Status |
|---|---|---|
| Churn-risk list returned 500 **complete customer records** to any signed-in user | High | **DONE** |
| RFM report published company-wide average customer value to any signed-in user | Medium | **DONE** |
| **13 of 19 customer doors trusted an id as authority — 4 moved money** | High | **PR #1087** |
| Login limiter locked out a **whole shop** on five typos across three people | High | **DONE** |
| Two IP readers, one spoofable and one returning a constant | Medium | **DONE** |
| Salary screens reachable by 3 roles the endpoints refuse | Medium | **PR #1088** |

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
