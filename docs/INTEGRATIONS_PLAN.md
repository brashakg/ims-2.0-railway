# IMS 2.0 — Integrations Layer Plan

Audience: engineering team + Avinash. Scope: third-party-vendor boundary only.
All proposals respect the existing fail-soft contract — missing env never crashes,
outbound writes return `status=SIMULATED` when not in `live` dispatch mode.

Branch: `claude/ims-2.0-phase-5-ZVX98`. Drafted 2026-05-08.

---

## 1. Inventory

Conventions:
- Connector code under `backend/agents/`
- Config CRUD: `/api/v1/admin/integrations/*` (typed, role-gated — `routers/admin.py`)
  AND `/api/v1/settings/integrations/*` (generic, what the frontend currently reads
  — `routers/settings.py`). Two surfaces over the same Mongo collection.

| # | Vendor | IMS role | Status | Connector file | Env / config | DISPATCH_MODE? | Failure mode |
|---|---|---|---|---|---|---|---|
| 1 | Anthropic Claude | ORACLE narratives, JARVIS chat | SHIPPED | `backend/agents/claude_client.py` | `ANTHROPIC_API_KEY`, `AGENT_CLAUDE_MODEL`, `AGENT_CLAUDE_MAX_TOKENS`, `AGENT_CLAUDE_TIMEOUT`, `ANTHROPIC_API_URL` | N/A (read-only) | Returns `None`; agents fall back to deterministic copy |
| 2 | MSG91 WhatsApp | Customer notifications (order ready, follow-up, Rx expiry) | SHIPPED | `backend/agents/providers.py::send_whatsapp` | `MSG91_API_KEY`, `MSG91_WHATSAPP_INTEGRATED_NUMBER`, `MSG91_WHATSAPP_NAMESPACE`, `DISPATCH_MODE`, `TEST_PHONE` | Yes — `_should_dispatch` (`providers.py:82`) | `DispatchResult(ok=True, status=SIMULATED)` when off/test, `FAILED` when key missing |
| 3 | MSG91 SMS | DLT-registered transactional fallback | SHIPPED | `backend/agents/providers.py::send_sms` | `MSG91_API_KEY`, `MSG91_SMS_TEMPLATE_ID`, `MSG91_SENDER` | Yes | Same SIMULATED contract |
| 4 | Google PageSpeed Insights | PIXEL frontend audits | SHIPPED | (Phase 4.1) | `PAGESPEED_API_KEY`, `FRONTEND_BASE_URL` | N/A (read-only) | Skipped silently if key absent |
| 5 | Sentry APM | Error tracking + per-agent-tick spans | SHIPPED | `backend/observability.py` | `SENTRY_DSN`, `SENTRY_TRACES_RATE`, `SENTRY_PROFILES_RATE`, `SENTRY_RELEASE` | N/A | No-ops on missing DSN |
| 6 | Slack | CRITICAL ORACLE anomaly webhook | SHIPPED | `backend/observability.py` | `SLACK_WEBHOOK_URL`, `SLACK_ALERT_SEVERITY` | N/A | Silent no-op |
| 7 | Redis | Cross-worker event bus + cache | SHIPPED | `backend/agents/event_bus.py`, `api/services/cache.py` | `REDIS_URL` (or `REDIS_HOST/PORT/PASSWORD/DB`), `AGENT_EVENT_CHANNEL` | N/A | In-process fallback with one startup warning |
| 8 | MongoDB Atlas | Primary DB + agent_events audit log | SHIPPED | `backend/database/connection.py` | (Atlas connection string) | N/A | Hard dependency |
| 9 | Shopify | Catalog push + order pull (website) | PARTIAL | `backend/agents/nexus_providers.py::shopify_*` | DB-stored `shop_url`, `access_token`; no env gate | Yes — gated on `dispatch_mode()=="live"` | Writes return SIMULATED, reads return `not_configured` |
| 10 | Razorpay | Payment reconciliation pull | PARTIAL | `nexus_providers.py::razorpay_list_payments` | DB-stored `key_id`, `key_secret`, `webhook_secret` | Read-only, runs in any mode | `not_configured` |
| 11 | Shiprocket | AWB tracking pull | PARTIAL | `nexus_providers.py::shiprocket_track_awb` | DB-stored `email`, `password`, `pickup_location_id` | Read-only | `not_configured` |
| 12 | Tally ERP9 | Nightly sales-voucher XML | PARTIAL | `nexus_providers.py::tally_build_day_voucher_xml` + `nexus.py::_build_tally_export` | DB-stored `server_url`, `company_name`; no HTTP push wired | **Not honored on push (gap)** | XML written to `tally_exports`; CA downloads manually |
| 13 | GST Portal | GSTIN cache refresh, returns filing | STUB | (none) | (none) | — | Listed in `INTEGRATION_SCHEDULES`; no provider client |
| 14 | Twilio | International SMS fallback | STUB | (comment only) | — | — | Documented as not wired |
| 15 | OpenTelemetry | Distributed tracing beyond Sentry | ABSENT | — | — | — | Phase 6.1 stretch |

---

## 2. Gaps and risks

**Duplicate connector surface.** Razorpay/Shopify/Shiprocket/Tally/WhatsApp config CRUD lives twice: `routers/admin.py:173-697` (typed Pydantic, per-vendor) AND `routers/settings.py:678-737` (generic `/{type}`). Frontend `IntegrationSettings.tsx` reads via `settingsApi.getIntegrations()` → `/settings/integrations`; `services/api/settings.ts:343-401` *also* exposes per-vendor methods that hit `/admin/integrations/*`. Two source-of-truth paths into the same Mongo `integrations` collection.

**`/settings/integrations/{type}/test` is a placebo.** Returns `{"status":"success"}` unconditionally (`settings.py:732-737`). The UI's "Test Connection" button is a lie.

**`GET /settings/integrations/{integration_type}` is a stub.** Returns hardcoded `{"is_configured": False}` ignoring DB (`settings.py:688-698`).

**No webhook receivers exist.** NEXUS subscribes to `webhook.received` (`registry.py:221`, `nexus.py:289`) but nothing publishes it. There is no `/api/v1/webhooks/{razorpay,shopify,shiprocket}` route. `webhook_secret` is collected and stored encrypted but **never verified** — `grep hmac` in `backend/` returns zero hits.

**No retry / circuit-breaker / rate-limit.** Every provider call is a single `httpx` request inside a try/except. Shiprocket's auth-then-track is two sequential calls per AWB on every tick — `_sync_shiprocket_outbound` iterates up to 50 orders and re-authenticates every time. A flaky vendor or 429 just records `ok=False` in `sync_runs` and waits an hour.

**Hardcoded vendor URLs.** Acceptable but Shopify Admin API version `2024-01` will rot — vendor deprecates quarterly.

**No last-sync surface.** `sync_runs` collection is written but never read. Frontend `lastSync` field (`IntegrationSettings.tsx:18`) always renders "Never synced".

**No outbound audit log.** Inbound HTTP is logged via Sentry; outbound third-party calls are not logged centrally — bad for billing disputes (MSG91), refund disputes (Razorpay).

**DISPATCH_MODE drift.** Honored by MSG91 + Shopify writes. **Not** honored by Tally export, Slack alerts (will spam #alerts on a staging deploy), Sentry (will tag staging errors as production unless `SENTRY_RELEASE` differs).

**Cosmetic regressions.** `IntegrationSettings.tsx:138, 218-220` still has `text-green-200`, `text-blue-200`, `bg-blue-50 text-blue-200` — same dark-on-light bug Phase 6.6b swept elsewhere.

**Compliance.** WhatsApp template approval state is not surfaced — operators can't see which `template_id` is approved by Meta. DLT sender ID `BVOPTL` is hardcoded as MSG91 default; for the WizOpt brand it should differ.

---

## 3. Recommended phase plan

Five small phases, ROI-ordered. Commit-and-push at the end of each.

### Phase I-1 — Single source of truth for integration config

The two-router duplication is the blocker for everything else. Pick `/api/v1/admin/integrations/*` as canonical (typed schemas, role-gated, per-vendor test endpoints). Make `settings.py` thin proxies OR redirect frontend to `/admin/integrations/*` directly. Implement the actually-working `_get_integration` and `_test_integration` (currently stubs).

- `backend/api/routers/settings.py:678-737` — replace stubs with real DB read + delegation
- `backend/api/routers/admin.py:166-697` — confirm test endpoints actually call provider clients (do not just return success)
- `frontend/src/services/api/settings.ts:343, 391` — point all vendor methods at one path
- `frontend/src/components/settings/IntegrationSettings.tsx` — fix dark tokens (lines 138, 218-220), wire `lastSync` from new endpoint
- New endpoint: `GET /api/v1/admin/integrations/sync-status` → reads `sync_runs` with `{provider, last_ok_at, last_error_at, last_error}` per integration

**Verification:**
- `tsc --noEmit` clean, `vitest` green
- `GET /settings/integrations/shopify` returns same payload as `/admin/integrations/shopify`
- "Test Connection" actually fails when keys are wrong
- "Last sync" timestamp populates from `sync_runs`

### Phase I-2 — Webhook receivers with HMAC verification

Highest-value missing piece — Razorpay payment-captured, Shopify order-created, Shiprocket status-update should push instead of poll. Framework already exists (NEXUS subscribes to `webhook.received`); only the receivers are missing.

- New router `backend/api/routers/webhooks.py` mounted at `/api/v1/webhooks/*` (open path — no auth, signature-verified)
- New helper `backend/agents/webhook_verify.py` with `verify_razorpay(body, sig, secret)`, `verify_shopify_hmac(body, hmac_header, secret)`, `verify_shiprocket(body, header, secret)` — pure, unit-testable
- Each receiver: verify HMAC → write raw envelope to new `webhook_inbox` Mongo collection → `dispatch_event("webhook.received", {integration, payload_id})` → return 200 fast
- `backend/agents/implementations/nexus.py:289-295` — extend `on_event` to read inbox row by id and dispatch to right handler
- New env: `WEBHOOK_REPLAY_WINDOW_SECONDS=300` for nonce replay protection

**Verification:**
- Posting unsigned body to `/webhooks/razorpay` returns 401
- Correctly-HMAC-signed body returns 200 within 50 ms
- 12+ unit tests in `backend/tests/test_webhooks.py` covering each vendor's signature scheme + replay rejection

### Phase I-3 — Retry, rate limit, last-sync visibility

Wrap every outbound call in a shared transport. Surface operational state to UI.

- New `backend/agents/integration_transport.py` — `httpx.AsyncClient` factory: 3-attempt exponential backoff for 5xx + 429, per-vendor token-bucket rate limit, circuit breaker that trips after 5 consecutive failures + recovers after 60 s
- Refactor `nexus_providers.py` callers; second pass on `claude_client.py` and `providers.py` (don't break MSG91)
- New collection `outbound_audit` — every external HTTP call: `{vendor, method, url, status, latency_ms, dispatch_mode, ok}`. Indexed `{vendor, ts desc}`. 30-day TTL.
- New endpoint `GET /api/v1/admin/integrations/audit?vendor=...&limit=100` (SUPERADMIN only)
- Surface badges on `IntegrationSettings.tsx`: green ("synced 2m ago"), amber ("3 errors in last hour"), red ("circuit open since 14:32")

**Verification:**
- Forcing Shopify to 429 in test does not crash NEXUS tick; `outbound_audit` shows 3 retries
- Circuit breaker opens after 5 fails; `sync_runs` records `circuit_open` reason
- Settings UI badge changes color in real time when API key removed

### Phase I-4 — Tally HTTP push + GST Portal stub → connector

Tally export currently writes XML to Mongo and stops. For tenants with Tally HTTP-Server reachable on LAN, push directly. GST Portal is regulatory; a read-only GSTIN-validation client unlocks vendor onboarding.

- `nexus_providers.py` — add `tally_push_voucher_xml(server_url, xml)` honoring `dispatch_mode()=="live"`
- New `backend/agents/gst_provider.py` — GSTIN validation (read-only first); next pass returns filing
- Config keys (DB, not env, per existing pattern): `tally.server_url`, `tally.company_name`, `gst.gstin`, `gst.api_key`
- `nexus.py::_build_tally_export` — after writing to `tally_exports`, if `tally.server_url` set + DISPATCH_MODE=live, push and record result
- `IntegrationSettings.tsx` — wire GST Portal card (currently `testable: false`) to a "Validate GSTIN" mini-form

**Verification:**
- Nightly tick at 23:00 IST writes XML row AND, when configured, posts to LAN Tally
- GSTIN validation form returns legal name + state for a real GSTIN
- Tests assert SIMULATED status when DISPATCH_MODE != live

### Phase I-5 — Compliance + observability polish

- New collection `dlt_templates` — `{vendor, channel, template_id, template_text, approved_at, brand}`. NEXUS won't dispatch a notification whose template_id isn't approved
- Per-brand DLT sender — replace hardcoded `MSG91_SENDER` with brand → sender_id map (Better Vision vs WizOpt)
- Honor DISPATCH_MODE in Slack alerts — staging deploys must not page on-call
- Confirm `SENTRY_RELEASE` set to `staging` on Railway preview environments
- Add `GET /api/v1/admin/integrations/health` aggregate — one JSON for all 12 vendors with `{configured, ready, last_ok_at, circuit_state}`. Hook a Pingdom/UptimeRobot to it.

**Verification:**
- Sending a WhatsApp via unapproved template_id → `status=FAILED, error=template_not_approved` without hitting MSG91
- Staging CRITICAL ORACLE anomaly does NOT post to #alerts
- `/admin/integrations/health` returns 12 rows, all green on prod

---

## 4. Out of scope

- **Twilio.** MSG91 covers India end-to-end. Adding Twilio = second vendor account, no ROI. Revisit only if non-India branch opens.
- **OpenTelemetry / Jaeger / Honeycomb.** Sentry already gives per-agent-tick traces. OTel adds collector + bill for tracing data we already have.
- **Redis Streams replay** for offline-worker event catchup. Pub/sub + Mongo audit log covers 95% of cases. Defer until a real outage shows the gap.
- **Direct Meta WhatsApp Cloud API.** MSG91 wraps Meta + handles template-approval queueing. Going direct = operating BSP relationship.
- **GST returns filing automation (GSTR-1, GSTR-3B).** Phase I-4 covers GSTIN validation only. Filing requires e-sign + DSC token — regulatory risk too high for now.
- **Razorpay refunds + Shopify destructive deletes.** Connectors exist as schemas (`admin.py:107`) but no endpoint exposes them. Keep that way until customer-service team explicitly asks. Refund-by-API on misconfigured deploy is a lawsuit.

---

## 5. Test strategy

The existing `DISPATCH_MODE=off|test|live` + `TEST_PHONE` discipline (`providers.py:44-98`) is the right model. Extend uniformly to every write-side integration.

**Per-mode contract:**
- `off` (default on every fresh deploy): no outbound writes anywhere. Returns `status=SIMULATED`. Reads still execute.
- `test`: writes only to a per-vendor allowlist. Today: `TEST_PHONE` for MSG91. Add: `TEST_SHOPIFY_PRODUCT_ID`, `TEST_RAZORPAY_ORDER_ID`, `TEST_SHIPROCKET_AWB`, `TEST_SLACK_CHANNEL`.
- `live`: production behavior.

**CI tests** (extend existing `test_observability.py`, `test_event_bus.py` convention):
- Each provider: `test_<vendor>_simulated.py` proving SIMULATED return when `DISPATCH_MODE=off`
- Each provider: `test_<vendor>_failed.py` proving graceful failure when env unset (no exception escapes)
- Webhook verifiers: `test_webhook_verify.py` — bad signature, replay, missing secret
- Use `respx` (httpx mock) — never hit real vendor in CI

**Pre-prod smoke** (Railway preview before promote):
- `python scripts/integration_smoke.py` (new) — for each enabled integration, runs one read-only call and prints `{vendor, ok, latency_ms}`. Exits 1 on any non-ok. Hook to Railway "deploy succeeded" check.

**Customer-safety guardrails:**
- Frontend "Test Connection" must call read-only test endpoint, never a write
- Red `DISPATCH_MODE=off` banner on `IntegrationSettings.tsx` when in non-live mode
- Outbound audit log (Phase I-3) is the post-incident forensic tool — every call attributed to user_id + dispatch_mode + ok/fail

---

## Critical files for implementation

- `backend/agents/nexus_providers.py`
- `backend/agents/providers.py`
- `backend/api/routers/admin.py`
- `backend/api/routers/settings.py`
- `frontend/src/components/settings/IntegrationSettings.tsx`
- `frontend/src/services/api/settings.ts`
