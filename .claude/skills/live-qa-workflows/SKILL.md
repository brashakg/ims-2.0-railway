---
name: live-qa-workflows
description: >-
  Run a deep, exhaustive QA + security audit of a LIVE app using the Workflow tool —
  parallel cycles of structured-output sub-agents that drive the real API (or read the
  repo), each returning a validated findings object, consolidated into living report +
  fix-prompt docs. Includes the council pattern (understand→propose→synthesize) for
  design decisions and an adversarial re-verification pass. Use when the user says
  "deep dive", "keep testing/probing", "test the live app", "run 5 workflows in
  parallel", "audit security/edge/perf/GST/exports", "consult council", or wants an
  autonomous test-audit-log loop. Complements `app-test-fleet` (which uses a local
  stack); this one orchestrates via Workflow against prod.
---

# Live QA via parallel Workflow cycles

Born from the IMS 2.0 2026-06-05 marathon (≈20 workflows; found 2 P0 [cross-customer
medical-Rx IDOR; public forged-webhook order creation], ~17 P1, ~80 P2, ~67 P3, plus a
catalog-merge design via council). The unit of work is a **Workflow** of parallel
`agent()` calls, each returning a **StructuredOutput** findings object; the orchestrator
consolidates into two living docs and loops.

## When to use this vs `app-test-fleet`
- **This skill (live-qa-workflows):** test the deployed app via the Workflow tool;
  agents call the prod API with `python3` urllib OR read the repo (Explore). No local
  stack to stand up. Best for "audit the live app / keep going in a loop / 5 in parallel".
- **app-test-fleet:** stand up a real LOCAL stack + a 50-agent background fleet. Best
  when you can run the backend locally and want isolation from prod.

## Core method
1. **One Workflow = one test angle**, 4-6 parallel `agent()` calls, each a distinct
   sub-area, each forced to return a validated **findings schema** (see below).
2. **Run ~5 workflows per cycle.** When one finishes, consolidate it and launch the
   next angle to keep ~5 in flight (an autonomous loop). Drive the loop off
   task-completion notifications; set a `ScheduleWakeup` fallback heartbeat (~1200s) so
   it survives if a workflow hangs.
3. **Consolidate every result immediately** into the two living docs (never lose a
   finding): `LIVE_QA_<date>.md` (findings: BUG-NNN rows + positives + running P0/P1/P2/P3
   counts) and `FIX_PROMPT_<date>.md` (per-bug fix guidance with file:line + verify).
   **Keep these OUTSIDE the git repo** (workspace root) — a concurrent automation can
   switch the repo checkout and clobber untracked files.
4. **Two agent archetypes** (saved as subagents — see `.claude/agents/`):
   - `qa-api-tester` — drives the live API with one `python3` (urllib stdlib) heredoc per
     call/group, NO temp files, threads the bearer token, prints JSON; on 4xx reads the
     matching router once, fixes, retries once, records.
   - `qa-code-auditor` — read-only Explore agent; greps/reads the repo, grounds every
     finding in file:line. **Use this when the live API is rate-limited** (see Gotchas).
5. **Findings schema** every agent returns (pass as the `schema` option):
   `{ area, preflight_ok, steps:[{step,method,path,http_status,ok,id,detail}],
   business_checks:[{check,expected,actual,pass}], created_entities:[], bugs:[{severity,
   title,detail}], summary }` (required: area, summary).
6. **Safety rails in EVERY prompt:** only TEST-prefixed data (e.g. `QAJRN-`); no deletes
   of real data; no period/payroll LOCK; no PAID/irreversible; no outbound dispatch
   (keep DISPATCH off); no live integration writes. A forged-webhook / over-refund /
   privilege-escalation that SUCCEEDS is the finding — record it + clean up.

## The cycle catalog (proven angles — pick per request, ~4-6 agents each)
- **Journeys:** product lifecycle per category (PO→GRN→invoice→sell→return→resell→price-revise→2nd-store/inter-state→online); operational (store onboard→audit, staff onboard→payroll-preview→incentive→tasks, customer, cash till→day-end, AI).
- **Sub-flows:** EMI, exchange-vs-return, hold/recall, inter-store transfer, vendor-return, loyalty/voucher/store-credit double-spend concurrency.
- **Adversarial security:** JWT forge/tamper/alg-none + store-switch escalation; NoSQL injection + mass-assignment; object-level IDOR (cross-customer medical/PII, payroll); per-role WRITE matrix.
- **Edge correctness:** money rounding + discount stacking + loyalty claw-back; illegal lifecycle transitions + over-return + refund>paid; oversell/double-mint/serial races; loyalty-expiry FIFO; end-to-end financial reconciliation (orders=payments=GST=Tally=day-book) + returns-in-GSTR.
- **Temporal/webhook:** IST-vs-UTC "today"/period/FY; scheduler/SLA/expiry timing; Idempotency-Key dup; **webhook signature (forged order/payment ingestion)**.
- **Abuse/boundary:** file-upload (stored-XSS/path-traversal/size); input/unicode boundary (cp1252 crash, stored-XSS, Infinity/NaN poison); info-disclosure in errors + security headers + login enumeration; concurrency races; geo-fence fail-open + discount-cap interaction.
- **Data integrity:** order↔catalog id mismatch; duplicates; catalog hygiene (HSN/GST/category); referential integrity (orphans).
- **Web/transport:** CORS/headers/CSRF/HTTP-method; secrets in frontend bundle/source-maps; dependency/supply-chain.
- **Performance:** endpoint latency p50/p95; N+1 + index coverage; payload/pagination; bundle.
- **Exports:** Tally JV balance + XML-escape; GSTR-1/3B JSON schema + CDNR + ITC; payroll payslip/PF-ECR; **CSV formula-injection**.
- **Domain (optical):** lens transposition / spectacle→CL vertex compensation; workshop QC gate bypass; expired-CL sale + FEFO; pricing/margin (sell-below-cost, offer<MRP).
- **Finance-advanced:** double-entry GL / trial balance; GST inter-state stock-transfer (deemed supply IGST) + RCM; payroll FnF/proration; inventory valuation (FIFO/WAC/COGS); SYSTEM_INTENT conformance.
- **AI/LLM:** prompt-injection direct + indirect (via stored data); data-grounding accuracy; agent decision quality; cost/abuse.
- **Design/visual:** colour-restraint + KPI-card redesign; consistency; static a11y; data-viz/density.
- **Code re-audit + REVERIFY:** read-only re-confirm each P0/P1 at file:line (CONFIRMED vs FALSE-POSITIVE) before the fix session — invaluable for reducing false positives.
- **Coverage-fill:** enumerate routers/features not yet exercised; close gaps.

## The COUNCIL pattern (design/architecture decisions — "consult council")
A phased workflow: **Understand** (1-2 agents map the current state) → **Propose**
(3 independent architect agents, each a distinct lens — user-workflow-first /
technical-architecture-first / AI-automation-first) → **Synthesize** (1 chair adopts the
strongest, grafts the rest, corrects against the actual code). Returns a unified
recommendation + a phased migration plan + open questions for the owner. Reusable script:
`.claude/workflows/design-council.js`.

## Adversarial re-verification (do before any fix session)
A read-only code workflow that re-reads each P0/P1 code path and marks it CONFIRMED
(with file:line) or FALSE-POSITIVE/downgrade. Catches over-stated blast radius and
code-vs-live disagreements. Live evidence (a reproduced API call) outranks a code-read
when they conflict — but record both so the fixer verifies the real path.

## Consolidation convention (do this on EVERY workflow completion)
- Append a section/table to `LIVE_QA_<date>.md`; give each new bug a stable `BUG-NNN`.
- Update the running **counts line** (`N×P0 · N×P1 · N×P2 · N×P3 · N confirmed-correct`).
- Add fix guidance (root cause @ file:line + suggested fix + verify) to `FIX_PROMPT_<date>.md`.
- Capture **positives** too ("GOOD-*") so the fix session doesn't regress what works.
- Group findings by **systemic root cause** (one fix clears many) where possible.
- At the end, write a `FIX_SESSION_KICKOFF.md` — a self-contained brief for the next session.

## Gotchas learned (IMS 2.0, 2026-06-05)
- **Prod rate-limiter (~120 req/min/IP + login 5-fails/15min lockout).** Many parallel
  API-heavy workflows share ONE egress IP → they trip the limiter and stall/fail
  (agents that "completed without calling StructuredOutput" or empty output = throttled).
  **Mitigation:** prefer `qa-code-auditor` (Explore, no API) for re-runs; keep API-heavy
  workflows few; pace; the limiter is itself a finding (per-IP lockout can lock real staff).
- **Workflow agents share a global concurrency cap** — launching 9 workflows queues the
  later ones for a long time. ~5 in flight is the sweet spot.
- **StructuredOutput is mandatory** — without the `schema` option an agent may finish
  without emitting a parseable result (lost work). Always pass `schema`.
- **Cross-session pause/resume:** background workflows keep running during a pause; on
  resume, glob `…/tasks/*.output`, read the completed ones, and consolidate. `TaskStop`
  works on workflow task IDs; "no task found" means it already terminated.
- **Never let testing create harmful artifacts:** an `Infinity`-mrp test product 500s the
  whole product list (no API delete) → needed a Mongo `deleteOne`. Probe boundaries, but
  flag + clean up anything that degrades the live app.
