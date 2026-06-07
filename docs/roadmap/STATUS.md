# IMS 2.0 Enterprise Roadmap — STATUS

## ✅ BOARD LIVE — work may begin (Phase 0)

All 27 owner decisions are locked (`DECISIONS.md`), the 8 shared-engine contracts are
written (`ENGINES.md`), and the whole plan passed an adversarial hardening pass whose
fixes are applied (`CORRECTIONS.md` + folded into the Phase-0 packets). The board is open.

**Read order every loop:** this file → `PROTOCOL.md` → `DECISIONS.md` → `CORRECTIONS.md`
→ the item's packet in `features/`. **Precedence: DECISIONS > CORRECTIONS > ENGINES/packets.**

### Build session
- Take the **top claimable** item in `EXECUTION_BOARD.md` → TODO (Phase 0: **E1, E2, #35, #40, #34, #21, E6**).
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
