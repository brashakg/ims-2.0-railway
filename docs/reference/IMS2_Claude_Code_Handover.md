# IMS 2.0 — Claude Code Handover Prompt

Copy everything below the line into a fresh Claude Code session run from `C:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway`.

---

## Context

I'm Avinash, solo dev and CEO of **Better Vision / WizOpt** (optical retail chains in India). I'm building **IMS 2.0**, a Retail Operating System to replace our Excel-based store operations. It covers POS, Inventory, Clinical/Optometry, HR/Payroll, Finance, CRM, Marketing, Vendor Management, Task Management, and an AI layer called Jarvis.

**Stack:** React 19 + TypeScript + Vite (Vercel) / Python FastAPI + MongoDB (Railway). 31 backend routers, 387+ endpoints, 65+ frontend pages.

**Repo:** `C:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway` — remote is `github.com/brashakg/ims-2.0-railway`, working branch `main`.

## Recent work (done in Cowork session, already pushed to `origin/main`)

| Commit | What |
|---|---|
| `e19be44` | April 2026 deep audit — 47 fixes across security, POS, Jarvis mocks, prescription validation |
| `e3d74d0` | ReferralTracker leaderboard border color on non-podium rows |
| `dddc325` | Backend CI fixes — `crm.py` undefined `db`, `jarvis.py` relative imports + staticmethod + unbound var, `vendors.py` wrong method signatures, `main.py` nosec, corrected bandit comment in workflow |

Frontend CI is green. Vercel deployed. Railway deployed at `ims-20-railway-production.up.railway.app`. Backend CI was red before `dddc325` — please verify it's green now via `gh run list --workflow=backend-ci.yml --limit 3`.

## What's pending

**1. Verify backend CI on `dddc325` is green.** If anything still fails, the likely suspects are residual pylint E/F in routers I didn't touch (there are 31 routers — I only audited the 3 that were flagged). Fix and push.

**2. Feature-by-feature frontend testing, in this order.** This is the main task. Test via the running app (localhost or Vercel preview), clicking through every screen and exercising real flows, not just reading code. Log bugs as you find them.

   1. **POS** — new sale, add items, discounts, GST, split payment, invoice print, lens fitting workflow, customer lookup.
   2. **Inventory** — receive stock via GRN, stock adjustment, barcode print, multi-store transfer, low-stock alerts, stock count/cycle.
   3. **Eye Testing / Clinical** — prescription capture, patient history, Rx renewal flagging, optometrist assignment.
   4. **CRM** — Customer 360, lifecycle phase, RFM segmentation, churn-risk list, loyalty points, interactions log. *Note:* these endpoints were fundamentally broken before `dddc325` (undefined `db`). The `_CRMDataAdapter` in `crm.py` now routes them to real repos but returns empty arrays where backing collections don't exist (interactions, prescriptions-as-array). Confirm the UI degrades gracefully.
   5. HR / Payroll, Finance, Marketing, Vendor, Task Management, Jarvis — in that order, unless something earlier reveals an issue worth deep-diving.

   Report bugs in this format per screen: `Screen → Action → Expected → Actual → Severity (P0/P1/P2)`.

## Known landmines worth flagging

- **Jarvis `execute_command` endpoints** were returning mock responses per the April 2026 audit. Audit claims these were fixed — verify before relying on Jarvis in tests.
- **`crm.py` prescriptions and interactions** are read from the customer document (fallback) because no dedicated repos are wired up. Creating/reading interactions should work; reading prescriptions depends on schema. If a real `PrescriptionRepository.find_by_customer` exists and is wired via `get_prescription_repository()`, that path will be preferred.
- **`vendors.py` GRN stock creation** now calls `stock_repo.create()` in a loop, one stock unit per accepted qty — matches `inventory.py`'s pattern but may not match what the original `add_stock(...)` was supposed to do. Verify a GRN flow creates the expected stock records.
- **Stale `.git/index.lock`** recurs occasionally (seems to be a Windows-side crash artifact). If `git` says another process is running and none is, `Remove-Item .git\index.lock` clears it.
- **`CLAUDE.md` global** points at `C:\Users\avina\Documents\GitHub\ims-2.0-railway` — that path does not exist on this machine. Actual repo is under `C:\Users\avina\IMS 2.0 CLAUDE COWORK\`. Update CLAUDE.md if you touch it.

## Reference docs in the repo

- `IMS2_Comprehensive_Test_Report_April2026.md` — full audit report (47 issues, priorities).
- `backend/TESTING_STRATEGY.md`, `backend/OPTIMIZATION_STRATEGY.md` — architectural notes.
- `.github/workflows/backend-ci.yml` — what CI runs (pylint E,F; bandit `-ll`; pytest with MongoDB service).

## Working preferences

- Make commits small and scoped. Push often.
- When you hit a non-obvious decision, explain the why briefly, then act.
- Don't paper over bugs with `# noqa` / `# nosec` unless the suppression is genuinely correct (main.py:531 is, for example).
- If a router has a structural bug (e.g., the undefined `db` in `crm.py`), flag it separately from the CI fix — those are two different problems.

Start by verifying backend CI on `dddc325` is green, then proceed to POS testing.
