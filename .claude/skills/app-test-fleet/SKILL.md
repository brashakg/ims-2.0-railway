---
name: app-test-fleet
description: >-
  Run a deep, full-application QA pass on IMS 2.0 (or any FastAPI+React+Mongo app)
  by standing up a real local stack and launching a large fleet (50+) of parallel
  test agents — data-creation, user/role-by-role access-matrix, business-rule,
  AI-subsystem, and frontend-analysis agents — that each create 50-100 real entries
  and report bugs with repro. Use when the user asks to "test the whole app",
  "screen-to-screen / user-by-user / role-by-role", "create many test agents",
  "verify every endpoint/feature", or "stress/QA the app end to end".
---

# App Test Fleet — full-application QA methodology

A repeatable way to verify an entire app works, by *using* it at scale through a
fleet of parallel sub-agents, then fixing what they find. Born from the IMS 2.0
2026-05-29 deep-QA session (found ~25 real bugs incl. a cross-store data leak,
missing RBAC on POS/finance writes, a hardcoded seed secret, and a class of
string-vs-BSON-datetime query bugs).

## Core principles

1. **Test against a REAL stack, not mocks.** Stand up the real DB + backend and
   drive the real HTTP API. In-process TestClient is a fallback only — and beware
   apps that silently fall back to an in-memory mock DB when the real connection
   isn't initialised (verify `database: connected` first).
2. **Every agent must produce a bug list with exact repro** (method, path, payload,
   response). Counts of "entries created" prove the workflow runs; the *bugs* are
   the deliverable.
3. **Agents are read-only on source.** They create data via the API and report.
   The orchestrator fixes bugs in a controlled way (parallel code edits = merge
   chaos). Honor the project's sensitive-area rules (e.g. "ask before POS").
4. **Run in managed waves, not 50-at-once.** 50 simultaneous agents thrash one
   backend and bury the signal. ~8-12 per wave, sharing one seeded backend.
5. **Verify before commit.** Run the full test suite after each fix batch; a fix
   that breaks a topically-unrelated test usually means shared-DB pollution, not a
   regression — recheck against a clean DB.

## Phase 1 — Infrastructure

```bash
# Mongo (match prod major; docker if daemon up, else the binary tarball)
curl -fsSL https://fastdl.mongodb.org/linux/mongodb-linux-x86_64-ubuntu2404-8.0.15.tgz | tar xz -C /tmp
/tmp/mongodb-*/bin/mongod --dbpath /tmp/mongodata --port 27017 --fork --logpath /tmp/mongod.log

# Backend, against that Mongo, with agents/dispatch OFF so the fleet isn't confounded
cd <repo>
JWT_SECRET_KEY=ims-qa-secret SEED_SECRET=qa-seed-2026 MONGO_HOST=127.0.0.1 \
  RUN_AGENT_SCHEDULER=false DISPATCH_MODE=off \
  python -m uvicorn api.main:app --app-dir backend --host 127.0.0.1 --port 8000 &

# Seed baseline + sanity (health must say database:connected)
curl -fsS localhost:8000/api/v1/health
curl -fsS -X POST "localhost:8000/api/v1/admin/seed-database?secret=qa-seed-2026"
curl -fsS -X POST localhost:8000/api/v1/auth/login -d '{"username":"admin","password":"admin123"}'
```

Each agent authenticates as `admin/admin123` (full access) for data creation, and
**mints role JWTs** (`JWT_SECRET_KEY=ims-qa-secret`, HS256, claims `user_id, roles[],
store_ids[], active_store_id, exp`) for access-matrix tests.

## Phase 2 — The agent fleet (50+ catalog)

Spawn as **background** agents (no worktree — they share the live backend + DB,
read-only on code). Group by category; each prompt carries the Phase-1 connection
preamble + a tight mission + the "report bugs with repro" contract.

### A. Data-creation / workflow agents (create 50-100 entries each) — 20
| # | Agent | Function |
|---|---|---|
| 1 | Catalog/Products | 50+ products across every category; test MRP≥offer, HSN→GST, discount caps |
| 2 | Orders/POS | 50 orders of all types/payment-modes; GST-inclusive math, partial pay, lifecycle, discount caps |
| 3 | Inventory | stock-add, daily counts+variance, transfers, power-grid, FEFO, shrinkage |
| 4 | Clinical/Rx | 50+ prescriptions incl. boundary SPH/CYL/AXIS/ADD + 0.25-step; eye-test queue; redo |
| 5 | Workshop | 50 jobs through assign→QC(pass/fail)→rework→lens-status→notify→deliver; KPIs |
| 6 | Customers/CRM | 50+ customers (B2C/B2B), 360 view, RFM, churn-risk, follow-ups |
| 7 | Finance | expenses (caps, duplicate-bill), advances, P&L, cash-flow, AR/AP aging, period-lock |
| 8 | Payroll | salary config, payroll run (EPF/ESI/PT/TDS/LWP), payslips, Tally JV, PF ECR |
| 9 | Attendance | shifts, late-mark, week-off swap, geo check-in, LWP report |
| 10 | Tasks/SOP | auto-generate, SOP checklists, escalation, fake-closure/silence integrity |
| 11 | Returns/Credit-notes | returns, restock, store-credit ledger, exchange |
| 12 | Loyalty/Points | earn/redeem/ledger/expire, tiers, daily points, leaderboard |
| 13 | Vouchers/Gift-cards | create/redeem/cancel, expiry |
| 14 | Marketing/Campaigns | bulk-send + consent gate, referrals, NPS, Rx-expiry alerts |
| 15 | Follow-ups | due-today, auto-generate, complete |
| 16 | Purchase/Vendors | vendor master, PO→GRN→serialized stock, partial receipt, vendor returns |
| 17 | Transfers | inter-store transfer lifecycle + actual stock movement (§5 barcode rules) |
| 18 | Prescriptions-deep | CL Rx (base-curve/diameter/modality), validity, progression, family records |
| 19 | Walkouts/Footfall | walk-in/walkout capture, recovery, footfall audit |
| 20 | Handoffs/Notifications | shift handoffs, notification logs, snooze |

### B. User-by-user / role-by-role access-matrix agents — 12
One per canonical role: mint that role's token, probe a matrix of endpoints across
all modules, assert allowed-vs-forbidden against the role spec, report over- and
under-permission.
| # | Agent | Function |
|---|---|---|
| 21 | SUPERADMIN | full access incl. AI; audit-log immutability check |
| 22 | ADMIN | full ops + user mgmt; **must be denied all /jarvis/*** |
| 23 | AREA_MANAGER | multi-store scope (no cross-store leak), 25% cap |
| 24 | STORE_MANAGER | single-store, 20% cap |
| 25 | ACCOUNTANT | finance/GST only — no POS/inventory writes |
| 26 | CATALOG_MANAGER | catalog/pricing only — no POS |
| 27 | OPTOMETRIST | Rx capture only — no finance/POS |
| 28 | SALES_CASHIER | POS + payments, 10% cap |
| 29 | SALES_STAFF | POS + search, 10% cap |
| 30 | CASHIER | payment only — no order create/discount |
| 31 | WORKSHOP_STAFF | job-status only — no finance/POS/CRM writes |
| 32 | INVESTOR | read-only everywhere — every write must 403 |

### C. Business-rule / compliance agents — 8
| # | Agent | Function |
|---|---|---|
| 33 | Pricing-caps | role × category × brand discount caps; per-item AND cart-level |
| 34 | GST-math | 5%/18% by HSN, inclusive vs exclusive, CGST/SGST vs IGST, GSTR-1/3B |
| 35 | Rx-validation | full SPH/CYL/AXIS/ADD range + 0.25-step boundary sweep |
| 36 | Geo-fence | roles 4-7 must be within store radius to log in / check in |
| 37 | Approval-chain | requester ≠ approver (§7); approver must outrank |
| 38 | Audit-immutability | no delete/update path for audit_logs, even for SUPERADMIN |
| 39 | Partial-payment | advance→balance→PAID; balance_due correctness; over-tender block |
| 40 | MRP-offer constraint | offer_price > mrp blocked on EVERY write surface |

### D. AI / Jarvis subsystem agents — 4
| # | Agent | Function |
|---|---|---|
| 41 | Agent-roster | 8/8 agents present, toggles persist, diagnostic clean |
| 42 | Change-proposals | §8 loop: suggest→approve→auto-exec(reversible only)→audit; reject |
| 43 | ORACLE/analysis | anomaly scan, forecast, fail-soft when ANTHROPIC_API_KEY unset |
| 44 | MEGAPHONE | DND IST window, transactional bypass, marketing-consent gate |

### E. Frontend / UX analysis agents (static, read code) — 8
| # | Agent | Function |
|---|---|---|
| 45 | UI-consistency | button/card/input systems, layout, design-token drift |
| 46 | UX-flows | navigation, dead links (routes not in App.tsx), dead buttons, "coming soon" stubs |
| 47 | Colour-palette | residual dark tokens that escape the CSS override; brand (bv-red not bv-gold) |
| 48 | Typography | font usage/sizes/hierarchy vs tokens |
| 49 | Accessibility | WCAG AA contrast, form labels, alt text, heading order |
| 50 | Responsive | mobile breakpoints, overflow, safe-area |
| 51 | States | loading/empty/error coverage; console.logs |
| 52 | Bundle/perf | chunk sizes, lazy-loading, render-blocking |

### F. Cross-cutting / research agents — 4
| # | Agent | Function |
|---|---|---|
| 53 | Web-research | best-practice & compliance comparison vs industry (cited) |
| 54 | Feature-completeness | cross-check the feature-status doc against live endpoints |
| 55 | Security-sweep | hardcoded secrets, unauthenticated endpoints, injection surface |
| 56 | Data-integrity | string-vs-BSON-datetime queries, missing fields breaking aggregations |

## Phase 3 — Collect, fix, report

- As agents report, append findings to a living `docs/reference/QA_INVESTIGATION_<date>.md`.
- Triage bugs: CRITICAL (security/data-loss) → HIGH (business-rule/revenue) → MED → polish.
- Fix the clear, contained ones; verify with the full suite; commit in scoped batches.
- Document the bigger ones (e.g. e-invoicing, schema migrations) as a prioritized backlog.

## Gotchas learned (IMS 2.0)

- **Rate limiter**: ~120 req/min per IP (bypassed only under `ENVIRONMENT=test`). Pace
  agents or vary `X-Forwarded-For`; a real bulk import would hit this ceiling too.
- **Mock-DB fallback**: importing the app without `db.connect()` serves an in-memory
  mock — agents must confirm `database: connected` or they test a throwaway DB.
- **Shared-DB pollution**: many tests aren't isolated; a fleet writing to the suite's
  DB can make unrelated tests fail. Recheck regressions on a clean DB.
- **String-vs-datetime**: a recurring bug class — fields stored as ISO strings but
  queried with `datetime` objects (or vice-versa) silently match nothing. Audit any
  `$lt/$gte` on date fields.
- **Divergent write surfaces**: the same entity exposed by two routers (e.g.
  `/catalog/products` vs `/products`) where only one enforces the business rules — the
  other is an unguarded back door. Test every surface.
