# Endpoint Parity Audit — IMS 2.0 (May 2026)

Frontend ↔ backend ↔ schema ↔ versions cross-check.

Audit run: 2026-05-08, branch `claude/ims-2.0-phase-5-ZVX98`.
Frontend services: `frontend/src/services/api/*.ts` (18 files, ~200+ calls).
Backend routers: `backend/api/routers/*.py` (35 files, 22 prefixes).
Frontend baseURL: `/api/v1` (verified `frontend/src/services/api/client.ts`).

---

## 1. Critical mismatches (production-affecting)

These cause **silent 404s in production** — the frontend reports a request error and the user sees a generic "failed to save" / "failed to load" toast.

| # | Frontend path | Frontend file | Backend reality | Severity |
|---|---|---|---|---|
| 1 | `GET /admin/products` (and POST/PUT/DELETE/{id}/bulk-import/generate-sku) — 7 calls | `services/api/products.ts:73-131`, `inventory.ts:239` | No `/admin/products` handler. Real impl is at `/catalog/products` (`routers/catalog.py:976-1423`) | **P0** Product admin UI silently broken |
| 2 | `GET /admin/categories` (and POST/PUT/DELETE/{id}) — 5 calls | `services/api/products.ts:142-177` | No `/admin/categories` writes. `/catalog/categories` (`catalog.py:907-922`) is GET-only | **P0** Category admin UI silently broken |
| 3 | `/admin/brands` + `/admin/brands/{id}/subbrands` — 7 calls | `services/api/products.ts:188-239` | `/admin/*` doesn't exist; `/catalog/brands` (`catalog.py:939`) GET-only, no subbrands sub-resource anywhere | **P0** Brand admin UI silently broken |
| 4 | `/admin/lens/{brands\|indices\|coatings\|addons\|pricing}` — 22 calls | `services/api/products.ts:256-360` | None of these paths exist on the backend. Catalog router has no lens master | **P0** Lens master config UI silently broken |

**Root cause** of items 1–4: `backend/api/routers/admin.py` is mounted at `/api/v1/admin` but contains **only** integration-config endpoints (`/admin/integrations/{shopify,razorpay,shiprocket,whatsapp,tally,sms}`). The frontend `services/api/products.ts` was written against an `/admin/*` namespace that was either renamed or never built.

**Fix options** (pick one per resource):
- **A. Re-point frontend** to `/catalog/products`, `/catalog/categories`, etc. — 1-line changes per call. Requires backfilling missing write verbs on `/catalog/categories` POST/PUT/DELETE and the entire lens-master surface.
- **B. Backfill backend** — add missing routes to `admin.py` (or new `admin_catalog.py`) so frontend doesn't change. Heavier; duplicates `catalog.py` semantics.

**Recommendation: A**, plus add missing write verbs on `/catalog/categories` and a new `/catalog/lens-{brands,indices,coatings,addons,pricing}` group. Single source of truth on the backend, smaller diff on the frontend.

---

## 2. False alarm — verified intentional

| Finding | Reality |
|---|---|
| "JARVIS router conflict — `jarvis_router` and `agents_router` both mounted at `/api/v1/jarvis`" | **Intentional.** `main.py:623, 634` mount both at the same prefix. `jarvis.py` exposes `{/, /status, /query, /command, /dashboard, /alerts, /quick-insights, /analyze, /agents/run-all, /agents/{id}/run}` — conversation/dashboard surface. `agents.py` exposes `{/agents, /agents/{id}/{status,toggle,config,run-now,logs}, /agents/{timeline,health-history,activity}}` — agent management surface. **Zero path collisions.** Phase 6.5 already removed the legacy `/agents` shadow handler in `jarvis.py:1758`. |

---

## 3. Schema spot-check — top 5 endpoints

All match. The frontend's snake_case ↔ camelCase transform in `client.ts` handles request/response field-name drift. Specific endpoints sampled:

| Endpoint | Frontend → Backend | Status |
|---|---|---|
| `POST /auth/login` | `{username, password}` → `{access_token, token_type, expires_in, user{...}}` | OK |
| `GET /orders` | params `{storeId, status, date, customerId, limit, skip}` → list with snake-case fields, transformed by client | OK |
| `GET /customers` | params `{search, page, pageSize, storeId, limit, skip}` → list | OK |
| `POST /tasks` | `{title, description?, priority?, assigned_to, due_date, type?}` → 201 task | OK |
| `GET /workshop/dashboard-kpis` | `{storeId}` → `{pending, in_progress, qc_failed, ready_for_pickup, overdue, completed_today, delivered_today, avg_turnaround_days, store_id, as_of}` | OK |

---

## 4. Version compatibility

| Component | Pinned version | Health |
|---|---|---|
| React | 19.2.0 | Current |
| React-DOM | 19.2.0 | Current |
| TypeScript | 5.9.3 | Current |
| Vite | 7.2.4 | Current |
| Tailwind CSS | 4.1.18 | Current |
| Axios | 1.13.2 | Current |
| FastAPI | 0.115.0 | Current |
| Pydantic | 2.9.0 | Current (v2) |
| Python | 3.12 (Docker) | Current — EOL Oct 2028 |
| Node.js | **NOT PINNED** | ⚠ no `.nvmrc`, no `engines` in `package.json` — pin to LTS (20.11.0+) to prevent CI surprises |

No version is behind a major; Pydantic v1 → v2 migration is complete. Only action: pin Node.

---

## 5. Top 5 actionable fixes (ROI-ranked)

1. **Restore `/admin/products/*` UI by re-pointing to `/catalog/products`** — single-file frontend change, immediate user-facing fix
2. **Add POST/PUT/DELETE to `/catalog/categories`** — backend backfill, frontend already calls it (after fix #1)
3. **Build a `/catalog/lens-master/*` surface** — biggest gap. ~12 new endpoints. Bundle into a single Phase
4. **Backfill `/catalog/brands/{id}/subbrands` sub-resource** — frontend already expects it
5. **Pin Node version** in `.nvmrc` (20.11.0) — 2-line PR, prevents future CI breakage
