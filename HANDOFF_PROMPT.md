# Paste this into the new chat to continue

---

I'm continuing work on **IMS 2.0** — retail OS for my Indian optical chains
Better Vision and WizOpt. Repo at `C:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway`.

The last session ended at **Phase 5** (commit `437f3c9`). Before you do anything:

1. Read [CLAUDE.md](ims-2.0-railway/CLAUDE.md) at repo root — the top section has a full session handover including what's shipped, env vars, and the Phase 6 menu.
2. Read [docs/reference/IMS2_Agent_Architecture.html](ims-2.0-railway/docs/reference/IMS2_Agent_Architecture.html) if you're touching agent code.
3. Read [docs/design/README.md](ims-2.0-railway/docs/design/README.md) if you're touching UI.

## What's already shipped (32 commits)

- Full design rollout across 9 module screens + all 18 interior pages (Phase 0 → 2.6)
- 8-agent Jarvis ecosystem, registered + toggleable UI (Phase 3 + 3.5)
- Real provider integrations — Claude, MSG91, PageSpeed, Shopify, Razorpay, Shiprocket, Tally (Phase 4.1 → 4.4)
- Cross-agent activity feed on Jarvis page (Phase 5)
- 5 infra fixes (CORS permanent, CI green, admin gate, Rx ranges, Jarvis canned-reply, store pill)

## Phase 6 — pick one

1. **Observability** — wire Sentry APM, OpenTelemetry, Slack webhook for CRITICAL anomalies
2. **Event bus durability** — move in-process event dispatch to MongoDB change streams or Redis (multi-worker Railway safe)
3. **Feature backlog** — ~166 deferred items in `docs/reference/IMS2_Updated_Feature_Status.md`
4. **Prod verification** — Chrome walk-through each route, log + fix bugs

**Start with option: ______**

## Working preferences (keep these)

- Commit + push at the end of every phase, not batched
- Always verify with `tsc --noEmit` + `vite build` + browser preview before commit
- No emojis in Python (Windows cp1252 crashes)
- Theme is light-only
- Ask before touching POS — revenue-critical
- Fail-soft contract for every external API call

## First move

Run `git log --oneline -5` to confirm you're on `main` at `437f3c9` or later, then open `CLAUDE.md` and confirm the session-handover block is visible before touching anything.

Then tell me which Phase 6 option you're starting with and go.
