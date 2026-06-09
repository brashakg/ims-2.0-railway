# IMS 2.0 Roadmap — EXECUTION BOARD

> Single source of truth for who-builds-what. **Read order each loop:** `STATUS.md` →
> `PROTOCOL.md` → `DECISIONS.md` → `CORRECTIONS.md` → the item's packet in `features/`.
> **Precedence:** DECISIONS > CORRECTIONS > ENGINES/packets. Go-live is controlled by `STATUS.md`.
> Item IDs: `E*`=engine, `PM`/`SC`=foundation, `#NN`=roadmap feature, `N*`=Excel net-new.

Legend: **BACKLOG** (not ready) · **TODO** (packet ready + corrections folded, claimable) · **IN BUILD** · **IN TEST** · **DONE** · **BLOCKED**

> ⚠️ Every TODO item below has passed the adversarial hardening pass. **Read its `CORRECTIONS.md` entry before building** — some engine clauses are DO-NOT-BUILD.

---

## ▶ TODO — Phase 0 (build session: take the top claimable item)

_**Phase 0 COMPLETE** — all 8 merged (E1, E2, #35, #21, #34, #40, **E6** — test session accepted the §6/T5 voucher fix, rebased + self-merged). **Phase-1 ENGINE LAYER COMPLETE** — E3, E4, SC, PM all merged + E4fe + F24. Now building Phase-1 features autonomously (whole-roadmap, dependency-order, self-merge on green+adversarial-clean)._

## 🔨 IN BUILD

| # | Name | Branch | Notes |
|---|---|---|---|
| #17 | Maker-checker journal entries | `feat/F17-maker-checker-je` | JE requires a different-user checker via the merged E4 before posting. Money feature; no POS. Adversarial-verify before PR. |
| #46 | Configurable reminders (E6 config UI) | [#605](https://github.com/brashakg/ims-2.0-railway/pull/605) | 22bd643 | **PASS -- 2026-06-09 (test session).** 19 tests (test_f46_configurable_reminders.py, T1-T11) + required CI green (test 3.10/3.11, test-and-build, security); e2e non-required. **Operator config-UI over the EXISTING E6 rail (no backend fork):** the reminders router calls reminder_rail.evaluate_rule (the one gate/send engine); consent DELEGATES to the canonical marketing.is_opted_out (3-signal flag+ledger, NOT reimplemented); every send rides notification_service.send_notification which writes a PENDING row + only dispatches under DISPATCH_MODE=live -- **send-dark, #565-compliant (nothing leaves on a fresh deploy).** Verified intents: cross-rule freq-cap 3/customer/30d (OTP excluded); consent-gate wins / is_transactional bypass; quiet-hours DEFER not drop (scheduled_for next 09:00 IST); E2 store>global template override; voucher-gate mints EXACTLY ONCE + same-day idempotent; FU-due-today channel mapping; family-wallet OTP atomic single-doc find_one_and_update; DISPATCH=off -> honest PENDING rows + zero provider calls; preview strictly read-only; no double-send after MEGAPHONE legacy-scan removal; RBAC cross-store/GLOBAL blocked + coverage-lock passes. FE RemindersSettings wired to real /reminders/rules CRUD+preview+run-now endpoints (not hollow). Smoke OK (1081 routes); 0 emoji. |
