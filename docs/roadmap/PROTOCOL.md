# IMS 2.0 Roadmap — EXECUTION PROTOCOL

> How the **build session** and the **test session** operate. Read this every loop. The
> orchestrator session owns this file, `DECISIONS.md`, `ENGINES.md`, `EXECUTION_BOARD.md`,
> and the per-feature packets under `features/`. **`DECISIONS.md` is law.**
> Do not start until `STATUS.md` says **BOARD LIVE**.

---

## 0. The three sessions

- **Orchestrator** (not you): writes packets, owns the board, sequences work, flips POS flags, resolves conflicts. Does not write feature code.
- **Build session:** implements one packet at a time from the board's `TODO` column.
- **Test session:** validates items in `IN TEST` against the packet's acceptance tests; passes them to `DONE` or bounces them back to `TODO` with findings.

You coordinate ONLY through this repo. You never see each other's chat. The board is the single source of truth.

---

## 1. Board lifecycle (`EXECUTION_BOARD.md`)

Columns: **BACKLOG → TODO → IN BUILD → IN TEST → DONE** (plus **BLOCKED**).

- The orchestrator moves items `BACKLOG → TODO` (only when their packet exists and dependencies are met).
- **Build session:** take the **top** item in `TODO`, set it to `IN BUILD`, write your branch name + start time in its row, and commit. Build it per its packet. When done + your own checks pass, open a PR and move it to `IN TEST` with the PR link.
- **Test session:** take the top item in `IN TEST`, validate against the packet's **Acceptance tests**. If all pass and CI is green → merge (auto-merge per §3) and move to `DONE`. If any fail → move back to `TODO`, append a dated **Findings** note (what failed + repro), and (for a flagged feature) leave the flag OFF.
- Never have two sessions editing the same item. Claim by editing its row first, then commit immediately so the other session sees the claim.

---

## 2. Branching (no collisions)

- One feature = one branch: `feat/F<NN>-<slug>` (build) ; test session pushes fixes only as review comments or follow-up commits on the same branch.
- **Never** commit feature code directly to `main`. Never force-push a shared branch.
- If you pull and see the board changed under you, re-read the board before acting (the other session may have claimed your item).
- Keep branches small and scoped to one packet. Rebase on `main` before opening the PR.

---

## 3. Merge + CI

- A PR may **auto-merge** once: (a) the test session marked its acceptance tests PASS, and (b) CI is green (`pylint`, `tsc -b`, `vite build`, backend smoke, related pytest).
- No owner sign-off per merge. Squash-merge, delete branch.
- Vercel free-tier deploy rate-limit can show UNSTABLE while all GitHub Actions are green — that is NOT a failure; judge on GitHub Actions.

---

## 4. POS / money safety (hard)

- Any change touching POS/checkout, payments, refunds, pricing, or accounting ships behind an **off-by-default feature flag** (via the E2 settings engine, e.g. `PROMO_ENGINE_ENABLED`, `REFUND_GATE_ENABLED`).
- The feature merges **dark** (flag off). The **orchestrator** flips the flag on per store after the test session validates on a staging cart. Build/test sessions do NOT flip POS flags and do NOT ask the owner.
- `claude/fix-money-integrity` must be merged + verified before ANY cash/refund/promo POS work (Phase 2+).

---

## 5. INTENT-FIDELITY gate (hard — the whole point)

An existing router/table/page is a **starting line, not done**. Every packet has:
- **Current behavior** — what the code does today.
- **Intended behavior** — the full feature intent (from the spec / Excel reference).
- **Delta to build** — the exact work to instill the intent.

The test session's **acceptance tests assert the *intended* behavior** (e.g. "a 95-day-idle SKU at Store A that is selling at Store B produces a transfer suggestion"), NOT mere existence ("endpoint returns 200"). A half-wired shell **fails** QA and bounces back. Reuse = graft the intent onto the foundation; never ship a hollow shell.

---

## 6. Shared engines (build once, never fork)

- The shared engines are specified in `ENGINES.md` (E1 money-guard, E2 settings-matrix, E3 item-event, E4 approval/PIN, E5 tender-routing, E6 reminder-rail) + foundations (Unified Product Master, Scorecard/Slab).
- Features MUST call the engines (`get_policy()`, money-guard, approval engine, reminder rail, tender router) — **never** reimplement balance mutation, settings, approvals, or messaging inline. Reviewer rejects in-feature reimplementation (this is what caused the legacy `AGENT_REGISTRY` / `pricing_caps` divergent-copy bugs).
- Any balance change uses the guarded `find_one_and_update` pattern (`vouchers.redeem_voucher_atomic`). Never read-modify-write money.

---

## 7. House rules (from DECISIONS.md)

- No emojis in Python (Windows cp1252 crash). ASCII tags like `[AGENTS]`.
- Theme light-only, restrained/executive (neutral + single accent; colour only for semantic meaning).
- Indian context: IST timezone, financial year Apr–Mar, GSTIN/PAN, paisa-exact rounding, GST 5% optical incl. contact lens / 18% sun-watch-acc.
- Every legacy Excel colour-flag (green=done, red=problem, amber=pending, yellow=header) → an explicit status enum field. Never replicate cell colour.
- Settings are store-scoped: **global → entity → store** override (E2).
- Approvals: per-approver PIN, 60-min validity (E4).

---

## 8. Verify before opening a PR (build session)

```
# frontend
cd frontend && npx tsc -b && npx vite build
# backend smoke
JWT_SECRET_KEY=test ENVIRONMENT=test .venv/Scripts/python.exe -c "import sys;sys.path.insert(0,'backend');from api.main import app;print(len(app.routes))"
# tests for the touched area
.venv/Scripts/python.exe -m pytest backend/tests/<relevant> -q
```
Add tests for the intent (not just the plumbing). Update `graphify update .` if you changed structure.

---

## 9. Bug bounce format (test session → board)

When bouncing an item to `TODO`, append under its board row:
```
Findings <date>: <what intended behavior failed> | repro: <steps> | file:line if known
```
Keep the original packet; do not rewrite it. The build session fixes and re-submits to `IN TEST`.

---

## 10. Escalate to orchestrator (BLOCKED)

Move an item to `BLOCKED` (with a one-line reason) if: it needs an owner decision/data not in `DECISIONS.md` §6, it depends on an unbuilt engine, or two features conflict. The orchestrator unblocks.

---

## 11. CORRECTIONS are binding (read before building)

- **Precedence of truth:** `DECISIONS.md` > `CORRECTIONS.md` > `ENGINES.md` / `engines/*` / packets. If a packet or engine contract conflicts with `CORRECTIONS.md`, **CORRECTIONS wins** — the engine contracts were adversarially hardened and some clauses are deliberately overridden there.
- **Before you build any item:** read its entry in `CORRECTIONS.md`. Several engine clauses are marked **DO-NOT-BUILD** (e.g. E1 unified `money_accounts` SoR / cross-collection dual-write — impossible on this standalone Mongo; E3 unit-level hash-chain). Building the literal contract there = data corruption.
- **Packet-before-TODO gate (orchestrator-enforced, but verify):** an item only appears in `TODO` once its `CORRECTIONS.md` items are folded into its packet. If you find a `TODO` item whose packet still contradicts `CORRECTIONS.md`, move it to `BLOCKED` and note it — do not build the contradiction.
- **No money corruption rule:** any balance/ledger change is single-document `find_one_and_update` only. There are **no multi-document transactions** in this deployment (standalone Mongo). Never write a cross-collection "atomic" update.
- **Single-source-of-truth fold gate (2026-06-08, from `GAP_ANALYSIS_2026-06-08.md` §2 — the durable fix for recurring packet-vs-CORRECTIONS drift):** an item enters `TODO` ONLY when its packet is the **SINGLE consistent source** for that item — internally and against `CORRECTIONS.md` — with **zero live pointer to an un-folded engine contract**. Before flipping a row to TODO the orchestrator MUST, in the packet:
  1. **Grep the packet against its own banner** and DELETE every body occurrence (Delta steps, Data-model blocks, indexes, acceptance tests, DoD lines) that still orders a thing the banner cancels/renames. *A banner that cancels X over a body that builds X is a BLOCK condition, not a TODO.*
  2. **Fold the CORRECTIONS entry into the body** (don't just cite it) — replace dead names with the locked ones: `append_audit_entry` → `AuditRepository.create`; `resolve_setting`/`resolve_settings` → `get_policy`/`get_policies`; `money_accounts` SoR → per-type single-doc collection (R1); `1.4@14%` → `1.1@14%`; `QUARANTINE` → the canonical `QUARANTINED` **free string** (no enum).
  3. **Quarantine cross-phase consumer tests** — any acceptance test asserting a *later-phase* consumer's behavior is relabeled `DEFERRED — cross-phase, NOT gating this item` and **struck from the merge-gating DoD checklist**. An engine's DoD gates only on the engine's own tests.
  4. **Kill the dangling engine pointer** — each `engines/*.md` carries a `SUPERSEDED-BY-CORRECTIONS — DO NOT BUILD FROM THIS FILE` banner (or is deleted and `ENGINES.md` repointed at `features/*.md`). **No build session opens an `engines/*.md` that still contains dead text.**
  *Rationale: banners alone were ignored within the same file; the lower-precedence layer must physically stop containing the wrong instruction so there is no half for an agent to pick.*
