# IMS 2.0 Enterprise Roadmap — STATUS

## ✅ BOARD LIVE — work may begin (Phase 0)

All 27 owner decisions are locked (`DECISIONS.md`), the 8 shared-engine contracts are
written (`ENGINES.md`), and the whole plan passed an adversarial hardening pass whose
fixes are applied (`CORRECTIONS.md` + folded into the Phase-0 packets). The board is open.

**Read order every loop:** this file → `PROTOCOL.md` → `DECISIONS.md` → `CORRECTIONS.md`
→ the item's packet in `features/`. **Precedence: DECISIONS > CORRECTIONS > ENGINES/packets.**

## [!] COMMS CHANNEL DIRECTIVE (2026-06-07) -- WhatsApp BLOCKED

Meta disabled the WhatsApp Business account (healthcare/commerce policy). Owner is appealing
to recover it; do NOT block the program on it. SMS (DLT) still works as a fallback but is on
hold pending the appeal outcome.

**Rule for build + test sessions:** any feature whose value is *sending an outbound customer
message* is **DEFERRED** until a live channel returns. Build everything else. If a deferred
feature is otherwise ready, you MAY build it DARK (channel code behind `DISPATCH_MODE=off`,
no live send, send-path covered by tests) but do NOT prioritize it over non-messaging work.

- **DEFERRED (message-send dependent):** #46 reminders(send), #41 reactivation(send),
  #47 CL-reorder(send), #51 use-it-or-lose-it(send), #52 WhatsApp-invoice, #42 lookbook(send),
  #43 VIP-trigger customer-message, #45 walkout *follow-up message* (walkout LOGGING is NOT
  deferred), E6 *live send* (the rail/config/cap may still be built dark).
- **NOT affected (build normally):** all engines (E2/E3/E4/PM/SC), #35, #40, #34, #24, #50
  (in-app bell), #39 (in-app call list), #8, #2, #9, #17, #25, #26, #15, #1, #20, #14, #13,
  #18, #19, #44, #33, #16, #23, #27, #6, Base-Bank, etc. In-app / push / on-screen features
  are fully unaffected.

Family-wallet (#49) OTP: when it lands, route OTP via SMS (works today), not WhatsApp.

### Build session
- Take the **top claimable** item in `EXECUTION_BOARD.md` → TODO. Current Phase-0 reality (2026-06-08):
  - **In flight:** E1 = **DONE** (PR #563, on `main`). E2 = **IN BUILD** (`feat/E2-settings-matrix`).
  - **Claimable now** (no deps, on `main`): **#35, #40, #21** (#21 builds its E3-shim; read `features/F21.md` — quarantine status is a **free string** `QUARANTINED`, no enum).
  - **Blocked on E2 merge to `main`:** **#34, E6** — they call `get_policy`, which only exists on the E2 branch. Do not claim until E2 lands on `main`.
- **Read the item's `CORRECTIONS.md` entry + its packet banner BEFORE coding.** Several clauses are DO-NOT-BUILD (esp. E1 = Phase-A facade only; NO `money_accounts` SoR / dual-write — standalone Mongo has no transactions).
- One branch per item (`feat/<id>-<slug>`), build to the packet's **Intended behavior**, verify (PROTOCOL §8), open a PR, move the item to `IN TEST`.
- Any balance change = single-document `find_one_and_update` only. Call the engines; never reimplement (PROTOCOL §6).

### Test session
- Validate `IN TEST` items against the packet's **Acceptance tests** (which assert *intended behavior*, not mere existence — a hollow shell must FAIL).
- Pass + CI green → auto-merge, move to `DONE`. Fail → back to `TODO` with a dated Findings note (PROTOCOL §9).
- POS/money features merge **dark** (flag off); the orchestrator flips the flag after you validate on staging.

### Orchestrator (this session)
- Owns the board, writes later-phase packets (folding their CORRECTIONS before promoting BACKLOG→TODO), flips POS flags, resolves BLOCKED.
- Pre-flight in parallel: merge `claude/fix-money-integrity` + GST FY-serial; owner approves MSG91 DLT utility templates.

_Last updated by orchestrator: 2026-06-07 — Phase 0 opened after hardening (NO-GO → fixed → GO)._
