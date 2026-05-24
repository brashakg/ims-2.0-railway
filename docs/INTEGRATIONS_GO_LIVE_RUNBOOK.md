# IMS 2.0 - Integrations Go-Live Runbook

Operational companion to [INTEGRATIONS_PLAN.md](INTEGRATIONS_PLAN.md) (which is the
engineering gap analysis). This doc answers one question: **"Which Railway variables
do I set, in what order, to take each dormant integration live - and how do I test it
safely first?"**

Audience: Avinash (owner). Drafted from a code audit on 2026-05-24 (verified against the
running `main`, not the older plan).

> **Secrets rule:** every value below is set by **you** in the Railway dashboard
> (Variables tab) or via `railway variables`. This doc only ever names the **KEYS**.
> Never paste a secret value into chat, code, a commit, or this file.

---

## How the fail-soft + DISPATCH_MODE model works (read once)

Every integration is already coded and merged. Each one *no-ops safely* until its
credentials exist, so nothing here is a code change - it is a configuration rollout.

- **Missing credentials -> SIMULATED / FAILED, never a crash.** A fresh deploy with no
  keys will not spam customers or 500 a page.
- **`DISPATCH_MODE`** is the master safety switch for anything that *writes to the
  outside world or costs money* (WhatsApp/SMS sends, Shiprocket bookings, Shopify
  product pushes):
  - `off` (default) - log only, return `SIMULATED`. Reads still run.
  - `test` - only send to an allowlist (today: `TEST_PHONE` for MSG91). Safe UAT.
  - `live` - real sends to everyone. Production.
- **Read-only calls are NOT gated** by DISPATCH_MODE (Claude narratives, PageSpeed
  audits, Razorpay payment pulls, Shiprocket tracking) - they run as soon as creds exist.

### Gotcha 1 - a variable change needs a redeploy to take effect

`DISPATCH_MODE`, all `MSG91_*`, `SHIPROCKET_*`, and `PAGESPEED_*` are read **once at
process import** (module-level constants). Railway restarts the service automatically on
any variable change, so just **set the var and let it redeploy**. Don't expect a live
change without a restart. (Anthropic/`LLM_*` are re-read per call, so those pick up a
restart too - same outcome.)

### Gotcha 2 - two places store integration creds; only one actually works

For the **collection-based** integrations (Razorpay, Shopify, and optionally
Shiprocket), creds live in the Mongo `integrations` collection, and there are **two**
write paths into it:

| Path | Endpoint | Doc shape | Encrypted? | Providers read it? |
|---|---|---|---|---|
| **admin.py (USE THIS)** | `POST /api/v1/admin/integrations/{vendor}` | `{type:"shopify", enabled, config}` | No (plaintext) | **Yes** |
| settings.py (AVOID) | `PUT /api/v1/settings/integrations/{type}` + Settings > Integrations tab | `{integration_type:"SHOPIFY", enabled, config}` | Yes (`enc:` XOR) | **No** |

The providers query `{type: <lowercase>, enabled: true}` and read the config **as
plaintext**. The Settings > Integrations UI tab writes a differently-keyed, *encrypted*
doc the providers can't read - so **configuring there does NOT turn the integration on.**
Always use the `/api/v1/admin/integrations/*` endpoints (SUPERADMIN/ADMIN gated) for
Razorpay/Shopify/Shiprocket collection creds. (This duplication is a known gap - see
INTEGRATIONS_PLAN.md Phase I-1.)

---

## Quick reference - all Railway KEYS by integration

| Integration | Powers | Railway KEYS (env) | Collection creds | DISPATCH_MODE? |
|---|---|---|---|---|
| **Anthropic / Claude** | ORACLE narratives, JARVIS+CORTEX chat, agent copy | `ANTHROPIC_API_KEY` (req), `AGENT_CLAUDE_MODEL`, `ANTHROPIC_API_URL`, `LLM_*` (opt) | - | No (read-only) |
| **MSG91 WhatsApp** | Rx-expiry / birthday / follow-up / order / task alerts | `MSG91_API_KEY`, `MSG91_WHATSAPP_INTEGRATED_NUMBER`, `MSG91_WHATSAPP_NAMESPACE`, `DISPATCH_MODE`, `TEST_PHONE` | - | **Yes** |
| **MSG91 SMS** | DLT transactional SMS fallback | `MSG91_API_KEY`, `MSG91_SMS_TEMPLATE_ID`, `MSG91_SENDER`, `DISPATCH_MODE`, `TEST_PHONE` | - | **Yes** |
| **Shiprocket** | Book + track customer shipments (Orders page) | `SHIPROCKET_EMAIL`, `SHIPROCKET_PASSWORD`, `SHIPROCKET_PICKUP_LOCATION` (opt), `DISPATCH_MODE` | optional (for NEXUS auto-track) via admin.py | **Yes** (booking) |
| **PageSpeed** | PIXEL Lighthouse / a11y audits | `PAGESPEED_API_KEY`, `FRONTEND_BASE_URL` (opt) | - | No (read-only) |
| **Razorpay** | Payment reconciliation + webhooks (NEXUS) | (none - env not wired) | `key_id`, `key_secret`, `webhook_secret` via admin.py | Read-only |
| **Shopify** | Catalog push / order pull (NEXUS) | (none - env not wired) | `shop_url`, `access_token` via admin.py | **Yes** (push) |
| **Tally** | Nightly sales-voucher XML | (export-only; no live push wired) | - | n/a |
| **GST portal / GSP** | GSTR-1/3B e-filing | NOT WIRED - export JSON only today | - | n/a |

Optional supporting infra (already documented elsewhere, not the focus here):
`SENTRY_DSN`, `SLACK_WEBHOOK_URL`/`SLACK_ALERT_SEVERITY`, `REDIS_URL`,
`WEBHOOK_REPLAY_WINDOW_SECONDS`.

---

## Per-integration detail

### 1. Anthropic / Claude  (ORACLE, JARVIS, CORTEX, TASKMASTER, MEGAPHONE copy)

- **Code:** `backend/agents/llm_provider.py` (registry + routing), `claude_client.py`
  (thin wrapper, delegates to the registry), `implementations/cortex.py`.
- **State today:** dormant. With no key, `llm_provider.any_available()` is false and every
  agent falls back to deterministic, templated output (no crash).
- **Set on Railway:**
  - `ANTHROPIC_API_KEY` - **required**, the only key needed to turn Claude on.
  - `AGENT_CLAUDE_MODEL` - recommended `claude-haiku-4-5` (cheap per agent tick). This is
    the registry default used by ORACLE / TASKMASTER / MEGAPHONE **and** is what JARVIS chat
    falls back to. JARVIS chat also exposes an in-app model picker
    (`GET /jarvis/models` -> `llm_provider.list_models()`), so the model is chosen per query
    at runtime, not by a fixed env var.
  - Optional: `LLM_OPUS_ENABLED` / `LLM_OPUS_MODEL` (adds an Opus "premium" entry to the
    picker), `LLM_DEFAULT_MODEL` (which picker entry is default), `ANTHROPIC_API_URL` (only
    for a proxy), `LLM_TIMEOUT`, token budgets `LLM_STANDARD_*` / `LLM_PREMIUM_*`.
  - **Do NOT bother with `JARVIS_MODEL`** - despite appearing in `cortex.py` and `jarvis.py`,
    it is a dead/legacy constant that no live code path reads. The real model levers are
    `AGENT_CLAUDE_MODEL` + the in-app picker above. (Cleaning up the dead constant is a
    separate tidy-up, not a go-live step.)
- **No DISPATCH_MODE gate** - Claude is read-only; it activates the moment the key lands.
- **Test:** set the key, open `/jarvis` (SUPERADMIN), ask JARVIS a question, or trigger an
  ORACLE on-demand analysis. A real (non-templated) narrative = working. Cost is tiny on Haiku.

### 2. MSG91 WhatsApp + SMS  (MEGAPHONE)

- **Code:** `backend/agents/providers.py` (`send_whatsapp`, `send_sms`, `_should_dispatch`).
- **State today:** dormant + gated. `DISPATCH_MODE=off` -> every send returns `SIMULATED`.
- **Set on Railway (WhatsApp):**
  - `MSG91_API_KEY` - your MSG91 auth key.
  - `MSG91_WHATSAPP_INTEGRATED_NUMBER` - the WhatsApp number id from the MSG91 dashboard.
  - `MSG91_WHATSAPP_NAMESPACE` - the template namespace.
  - (template id is passed per-message from the notification templates, not an env var.)
- **Set on Railway (SMS):**
  - `MSG91_API_KEY` (same), `MSG91_SMS_TEMPLATE_ID` (DLT-approved flow id),
    `MSG91_SENDER` (DLT 6-char sender; default `BVOPTL` - **note:** for the WizOpt brand
    you may want a different registered sender).
- **Safety / DISPATCH_MODE:**
  - `DISPATCH_MODE=test` + `TEST_PHONE=<your 10-digit number>` -> only your phone receives
    anything; every other recipient is suppressed and logged as SIMULATED. **Do this first.**
  - `DISPATCH_MODE=live` -> sends to all real customers.
- **Test sequence (safe):**
  1. Set `MSG91_*` keys + `DISPATCH_MODE=test` + `TEST_PHONE=<you>`. Let it redeploy.
  2. Trigger a message - e.g. let MEGAPHONE's drain run, or fire a notification whose
     recipient is your test number.
  3. Confirm only your phone got it; check `notification_logs` for `status: SENT` and a
     `provider_message_id`.
  4. Flip `DISPATCH_MODE=live` only when the template renders correctly.
- **Watch:** WhatsApp templates must be **Meta-approved**; an unapproved `template_id` will
  be rejected by MSG91 (returns FAILED). Confirm approval in the MSG91 dashboard first.

### 3. Shiprocket  (shipping - book + track on the Orders page)

- **Code:** `backend/api/services/shiprocket.py` + `routers/shipping.py`
  (`POST /api/v1/shipping/shipments`, `GET .../track`). Separate, newer, env-driven client
  (supersedes the older `nexus_providers.shiprocket_track_awb`).
- **State today:** bookings return `SIMULATED` with a deterministic fake AWB.
- **Set on Railway:**
  - `SHIPROCKET_EMAIL`, `SHIPROCKET_PASSWORD` - your Shiprocket login (token is cached
    in-process ~9 days; auto re-auths on 401).
  - `SHIPROCKET_PICKUP_LOCATION` (optional, default `"Primary"`) - **must exactly match a
    pickup location nickname registered in your Shiprocket dashboard**, or live bookings fail.
  - `DISPATCH_MODE=live` - required for a *real* booking. (Tracking is read-only and works
    in any mode once creds exist.)
  - Optional tuning: `SHIPROCKET_TIMEOUT`, `SHIPROCKET_TOKEN_TTL`.
- **Important nuance - two consumers, different cred sources:**
  - Manual **book/track from the Orders page** uses the **env vars** above. This is the
    main path.
  - The **NEXUS hourly auto-tracking sweep** (`nexus_providers.shiprocket_track_awb`) reads
    **only the `integrations` collection**, not env. If you want background auto-tracking of
    SHIPPED orders, *also* store creds via `POST /api/v1/admin/integrations/shiprocket`
    (`{email, password, enabled:true}`).
- **Test sequence:**
  1. Set the env vars but keep `DISPATCH_MODE=test` (or `off`). Book a shipment from an
     order -> you get a `SIMULATED` AWB and **no** network call. Confirms wiring.
  2. To verify the live credential without booking: the track endpoint will auth against
     Shiprocket as soon as creds exist (read-only).
  3. For a true live booking, set `DISPATCH_MODE=live` and book **one low-value real order**,
     then cancel it in the Shiprocket dashboard. (Shiprocket has no TEST_PHONE-style
     allowlist; a live booking is a real courier order that can cost money - so test with one
     disposable shipment, not a blast.)

### 4. PageSpeed  (PIXEL - frontend Lighthouse + a11y audits)

- **Code:** `backend/agents/implementations/pixel.py`.
- **State today:** heartbeat-only. Without the key PIXEL records a "heartbeat" row so you can
  see it is scheduled, but runs no real audits.
- **Set on Railway:**
  - `PAGESPEED_API_KEY` - free from Google Cloud Console (PageSpeed Insights API).
  - `FRONTEND_BASE_URL` (optional) - defaults to `https://ims-2-0-railway.vercel.app`.
- **No DISPATCH_MODE gate** (read-only).
- **Test:** set the key, trigger a PIXEL run (on-demand from `/jarvis`, on the next daily
  2 AM tick, or on a Vercel deploy event), then check the `ui_audits` collection for a
  `kind: scheduled_audit` row with real Lighthouse scores.

### 5. Razorpay  (payment reconciliation + webhooks, via NEXUS)

- **Code:** `backend/agents/nexus_providers.py::razorpay_list_payments` (read-only pull),
  `routers/webhooks.py` + `agents/webhook_verify.py` (inbound webhook HMAC verify).
- **State today:** dormant. **No env-var path exists** - creds come only from the
  `integrations` collection.
- **Activate (use admin.py, NOT the Settings tab - see Gotcha 2):**
  - `POST /api/v1/admin/integrations/razorpay` with body
    `{key_id, key_secret, webhook_secret, enabled: true}` (SUPERADMIN/ADMIN).
  - Point your Razorpay dashboard webhook at `https://<backend>/api/v1/webhooks/razorpay`
    using the **same** `webhook_secret`.
- **DISPATCH_MODE:** the payment-list pull is read-only (runs in any mode). No write path
  (refunds) is exposed, by design.
- **Test:** configure via the admin endpoint, then watch `sync_runs` after the next NEXUS
  hourly tick (or check the status surface). For webhooks: send a test event from Razorpay
  and confirm a `webhook_inbox` row appears and verifies.

### 6. Shopify  (catalog push / order pull, via NEXUS)

- **Code:** `backend/agents/nexus_providers.py::shopify_pull_orders` / `shopify_push_product`.
- **State today:** dormant. **No env-var path** - creds come only from the `integrations`
  collection.
- **Decision needed first:** per project memory, the **BVI app under `ecommerce/` owns the
  Shopify relationship** (storefront stays on bettervision.in / Shopify). Decide whether IMS
  should *also* talk to Shopify directly through NEXUS, or leave Shopify entirely to BVI. If
  the latter, keep this dormant on purpose.
- **If activating:** `POST /api/v1/admin/integrations/shopify`
  `{shop_url, access_token, enabled:true}`. Order pull is read-only; **product push is gated
  on `DISPATCH_MODE=live`** (returns SIMULATED otherwise).

### 7. Tally  (nightly sales-voucher XML)

- **Code:** `nexus_providers.tally_build_day_voucher_xml` + `nexus.py::_build_tally_export`.
- **State today:** **export-only, and that already works.** NEXUS builds per-store voucher
  XML at 23:00 into the `tally_exports` collection; the CA downloads it via
  `GET /api/v1/admin/integrations/tally/voucher.xml`. Payroll JV / PF ECR are similar file
  exports.
- **"Live push" to a Tally HTTP-Server is NOT wired** - `admin.py` stores a `server_url` but
  nothing posts to it. There is **no Railway variable** that turns on a live Tally push; it
  is a build item (INTEGRATIONS_PLAN.md Phase I-4). No action needed for go-live unless you
  want that feature built.

### 8. GST portal / GSP (GSTR-1 / GSTR-3B e-filing)

- **Code:** `backend/api/services/gstn_export.py` - produces the **offline-tool JSON** the
  accountant imports manually on gst.gov.in. That is the entire current capability.
- **Live e-filing is NOT wired and is not a config toggle.** Direct GSTN filing requires a
  **licensed GSP** (e.g. ClearTax / Masters India / etc.) plus e-sign / DSC. The code
  comment and INTEGRATIONS_PLAN.md both mark it out of scope.
- **Feasibility / what's missing:** a commercial GSP contract + their API credentials, a new
  `gst_provider.py` connector, DSC/e-sign handling, and reconciliation of IMS return data to
  the GSP schema. This is a project, not a Railway variable. No go-live action today; flag if
  you want it scoped.

---

## Recommended go-live order (lowest risk first)

1. **Anthropic** - read-only, instant value (real JARVIS/ORACLE output), trivial cost on Haiku.
   Set `ANTHROPIC_API_KEY` (+ `AGENT_CLAUDE_MODEL=claude-haiku-4-5`).
2. **PageSpeed** - read-only, free. Set `PAGESPEED_API_KEY`.
3. **MSG91 in `test` mode** - set keys + `DISPATCH_MODE=test` + `TEST_PHONE`; verify a
   message to yourself; then flip to `live`.
4. **Shiprocket** - set creds, verify SIMULATED booking in `test`/`off`, then one disposable
   `live` booking.
5. **Razorpay** - admin.py config + webhook; read-only, safe.
6. **Shopify** - only after the BVI ownership decision.
7. **Tally live push / GST e-filing** - build items, not config. Scope separately if wanted.

After each step, check the read-only **Integration Status** surface (SUPERADMIN, on the
Jarvis page) to confirm `configured` + `dispatch_mode` flipped as expected - it reports KEY
presence only, never values.
