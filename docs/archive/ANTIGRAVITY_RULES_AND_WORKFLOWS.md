# IMS 2.0 — Rules, Workflows & Prompts to seed in Google Antigravity

> Companion to `ANTIGRAVITY_HANDOFF.md`. Drop the **Rules** block in as an always-on Antigravity
> rule/memory; save the **Workflows** as reusable Antigravity workflows; keep the **Prompts** handy.
> These are the distilled, hard-won patterns that made the IMS 2.0 work fast *and* safe.

---

## 1. ALWAYS-ON RULES (make this a persistent Antigravity rule/memory)

```
IMS 2.0 — operating rules (load every run):
- The owner (Avinash) is NOT a developer. You own ALL technical execution. Explain in plain
  English; give exact dashboard steps (which tile/button/value); never expect him to read a diff
  or use a terminal beyond a copy-paste one-liner.
- VERIFY before EVERY PR, no exceptions:
    backend:  JWT_SECRET_KEY=test ENVIRONMENT=test .venv/Scripts/python.exe -c "import sys;sys.path.insert(0,'backend');from api.main import app;print(len(app.routes))"   (expect ~920, 0 errors)
              + pytest the touched area + keep test_rbac_policy green (0 uncatalogued routes)
    frontend: cd frontend && npx tsc --noEmit && npx vite build
  Merge --squash ONLY when the required GitHub checks are green. (A red "Vercel" check on a
  backend-only PR is the free-tier deploy rate-limit, NOT a failure.)
- Every NEW backend route MUST be catalogued in backend/api/services/rbac_policy.py, or
  test_rbac_policy's "no uncatalogued routes" test fails.
- NO emojis / non-ASCII in Python files (Windows cp1252 crashes print/logger). Theme is LIGHT-ONLY.
- docs/SYSTEM_INTENT.md is SUPREME: Control over Convenience, Audit Everything, Fail Loudly.
  NEVER show fabricated/hardcoded numbers — bind to real data or show an honest "—"/empty state.
- ASK before changing POS / orders.py / GST / pricing / loyalty / payments (revenue-critical).
  When you do, reuse the canonical pricing_caps resolver + the gst_rates / hsn_gst_master master;
  pricing is GST-INCLUSIVE; place_of_supply drives IGST (inter-state) vs CGST+SGST (intra-state).
- NEVER print or commit secret VALUES. Print env-var KEYS only. Use the Railway CLI
  (railway run --service <svc> <cmd>) for prod ops; the Railway MCP is unauthorized.
- New FRONTEND api service: import it DIRECTLY from its module
  (import { xApi } from '../../services/api/x'), NOT via services/api/index.ts (TS2614 quirk).
- Do NOT run two agents building the SAME feature or editing the same files (rbac_policy.py,
  main.py, App.tsx) — you get duplicate/colliding PRs. Take non-colliding lanes; rebase before merge.
- Commit + push at the END of each phase (small, conventional commits).
```

---

## 2. SAVE THESE AS ANTIGRAVITY WORKFLOWS

### Workflow A — "Seam/Stub Audit → Fix" (this is the loop currently producing #460–462)
> The highest-ROI loop now that the app is feature-complete: find FE↔BE integration defects and fix them.

1. **Fan out** read-only audit agents over module/page groups (finance, purchase/inventory,
   clinical/HR, CRM/marketing, POS, online-store).
2. Each flags, with file:line + severity (BROKEN > STUB > PARTIAL > MINOR): hardcoded/mock data;
   "coming soon"/disabled buttons; `useState([])` with no API call; a FE payload whose fields don't
   match the backend pydantic schema; a call to an endpoint that doesn't exist (404/405); an enum/
   status the FE doesn't handle (crash risk).
3. A **lead** agent de-dupes + ranks into one defect list.
4. **Fix each** additively + fail-soft, reusing existing services; add a pytest for any backend/money
   path; run the full verify gate; open a PR per coherent batch; merge on green.
5. Loop until the audit comes back clean.

### Workflow B — "Council" (for any design / money / architecture decision)
> Use before building anything with accounting/GST/schema impact (it's how Purchase Invoice was scoped).

1. Spawn **3–5 independent expert lenses** in parallel, each VERIFYING in the actual code:
   e.g. domain/procurement · GST-compliance · accounting/AP · software-architecture · UX.
   Each returns: EXISTS (file:line) / GAP / RECOMMEND.
2. A **chair** synthesizes ONE decision: VERDICT + WHAT-TO-REUSE + THE-GAP + a phased BUILD PLAN,
   tagging each phase **SHIP-NOW** (prod-safe) / **OWNER-SIGN-OFF** (needs Avinash/accountant) /
   **DEFER** (needs external input).
3. Present the verdict; build the SHIP-NOW phase; hold the rest for the owner.

### Workflow C — "Adversarial verify" (for risky findings)
> Before acting on a non-obvious bug/claim, spawn 2–3 skeptics each prompted to REFUTE it; proceed
> only if a majority can't. Stops plausible-but-wrong fixes.

---

## 3. PROMPTS TO KEEP HANDY

**Find the next defects (audit):**
```
Audit <module/page group> for FE↔BE seam + stub defects (read-only). Flag, with file:line + a
severity (BROKEN/STUB/PARTIAL/MINOR) + the one-line fix: hardcoded/mock data; "coming soon"/all-
disabled buttons; useState([]) with no API call; a FE request whose fields don't match the backend
pydantic model; a call to a route that doesn't exist; a status/enum the FE doesn't handle (crash).
Confirm each against the actual backend router before reporting. Do not edit.
```

**Money/POS pre-merge self-review (run BEFORE merging any orders/POS/GST/pricing/loyalty PR):**
```
Self-review this diff before merge. List each as PASS/FAIL with the line:
1) No GST or price math changed unintentionally (amounts/HSN/rates).
2) Any discount cap uses the canonical pricing_caps resolver (not a local table).
3) Money tenders reconcile: amount_paid + balance_due stay correct; idempotency preserved; no
   double-charge / double-credit path.
4) place_of_supply / IGST-vs-CGST+SGST classification still correct.
5) A pytest exercises the money path.
6) The owner authorized this POS/revenue change.
If any FAIL, fix before merging.
```

**Live-verify a fix (tests alone are not enough for a seam fix):**
```
Load the app (https://ims-2-0-railway.vercel.app, admin/admin123) — or a local stack — navigate to
<flow>, perform <action>, and confirm <expected outcome> with a screenshot and a clean console.
A green tsc/pytest is necessary but NOT sufficient for an integration/seam fix; prove the user-facing
flow works.
```

---

## 4. MEMORY TO ADD (hard-won gotchas, some not in the repo docs)

```
- Railway mongodb-volume is capped at 500MB (redis/postgres are 5GB). Mongo needs 500MB FREE to
  build any index -> ensure_indexes silently fails for ALL collections. RESIZE IS DASHBOARD-ONLY
  (no API/CLI). Owner must grow it to 5GB; then a restart auto-builds the indexes. THIS IS THE
  KEYSTONE BLOCKER + the prerequisite for the BVI Shopify cutover.
- ANTHROPIC_API_KEY is SET on the backend service (Catalog Autopilot AI source + ORACLE/JARVIS work).
- The audit hash-chain (audit_chain._json_default) must serialize datetimes IDENTICALLY across the
  Mongo write->read round-trip: truncate microseconds->ms AND normalize tz-aware->naive-UTC. A drift
  causes a false /audit/verify "tamper" alarm. DB-backed audit tests SKIP without a local mongod.
- No local mongod in the Claude harness -> many backend tests SKIP locally; CI runs mongo:7.0. If
  Antigravity can run Docker, stand up mongo:7.0 locally to unlock live end-to-end tests.
- Prod Mongo ops: railway run --service MongoDB bash -c 'mongosh "$MONGO_PUBLIC_URL" ...' (or pipe a
  python script with the repo-root venv). Never surface the connection string.
- A SECOND agent/session may be editing the same repo concurrently -> expect additive conflicts on
  rbac_policy.py / main.py / App.tsx; resolve by keeping BOTH blocks; rebase before merge.
- BVI Online Store is BUILD-COMPLETE (Phases 1-5+3b); only the OWNER-GATED cutover (flip
  IMS_SHOPIFY_WRITES=1 + DISPATCH_MODE=live after the volume + a quiet window) + decommission remain.
```

---

*If you only do one thing: paste Section 1 as an always-on rule, and run Workflow A (Seam Audit → Fix)
until clean. That's 80% of the value. — handoff from the Claude Code sessions, with appreciation.*
