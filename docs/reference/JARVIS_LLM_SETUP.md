# JARVIS LLM — pluggable provider + Railway setup

JARVIS (and the agents) now run through a **pluggable LLM provider**
(`backend/agents/llm_provider.py`). You can run a **self-hosted open-source
model** (free, private), **Claude** (deep analysis), or **both** with a
runtime model selector in the chat. Customer PII is **scrubbed** from the
business context before any prompt leaves the process.

If **nothing** is configured, JARVIS still works — it falls back to the
deterministic, real-data template responses (no fabricated numbers).

---

## How model selection works

The registry is built from env vars at request time. Whatever you
configure shows up in the chat's model dropdown (and `GET /jarvis/models`):

| Model id | Enabled by | Use |
|---|---|---|
| `local` | `LLM_LOCAL_BASE_URL` set | Self-hosted OSS (Ollama/vLLM) — free, private, default |
| `claude` | `ANTHROPIC_API_KEY` set | Anthropic Claude — deep analysis |
| `extra` | `LLM_EXTRA_BASE_URL` set | Any other OpenAI-compatible endpoint (e.g. Groq) |

`LLM_DEFAULT_MODEL` picks the default (else: `local` → `claude` → `extra`).
The selector only renders when 2+ models are configured.

---

## Recommended setup for you: hybrid (local + Claude) on Railway

### Step 1 — Deploy Ollama as a Railway service
1. In your Railway project: **New → Docker Image** → `ollama/ollama:latest`.
2. Add a **Volume** mounted at `/root/.ollama` (persists the model weights
   across restarts — a 3B model is ~2 GB).
3. Give it enough memory: **Settings → Resources → 6–8 GB RAM** (a 3B Q4
   model needs ~3–4 GB; headroom avoids OOM). CPU-only is fine.
4. Expose port **11434** (Ollama's default). You do **not** need a public
   domain — use Railway's private network (next step).

### Step 2 — Pull the model
Once the service is up, open its shell (Railway → service → Shell) and run:
```
ollama pull qwen2.5:3b-instruct
```
(Alternatives: `llama3.2:3b-instruct`, `qwen2.5:7b-instruct` if you gave it
more RAM. The 3B is the speed/quality sweet spot on CPU.)

### Step 3 — Point the backend at it (private networking)
On your **backend** service in Railway → **Variables**, add:
```
LLM_LOCAL_BASE_URL = http://<ollama-service-name>.railway.internal:11434/v1
LLM_LOCAL_MODEL    = qwen2.5:3b-instruct
LLM_LOCAL_LABEL    = Local (fast & private)
LLM_DEFAULT_MODEL  = local
```
Replace `<ollama-service-name>` with your Ollama service's name (Railway
private DNS is `<service>.railway.internal`). Traffic stays inside
Railway's private network — never hits the public internet.

### Step 4 — Add Claude for the hybrid (optional but you chose it)
```
ANTHROPIC_API_KEY  = sk-ant-...
AGENT_CLAUDE_MODEL = claude-haiku-4-5
LLM_CLAUDE_LABEL   = Claude (deep analysis)
```
Now the JARVIS chat shows a dropdown: **Local (fast & private)** (default)
and **Claude (deep analysis)**. Pick per query.

### Step 5 — Redeploy the backend
Railway redeploys on variable change. Verify: open JARVIS → the model
dropdown appears with both options. `GET /api/v1/jarvis/models` lists them.

---

## Notes & tuning
- **Latency**: 3B on Railway CPU answers in ~2–6 s. If too slow, either use
  a smaller model (`qwen2.5:1.5b-instruct`) or give Ollama more CPU.
- **Privacy**: PII (names, phones, emails, addresses, GSTIN) is redacted by
  `scrub_pii` before any prompt — even to the local model. The local model
  additionally keeps everything inside Railway's private network.
- **Cost**: Ollama service is the only cost (~$10–30/mo depending on RAM/CPU
  hours). Model weights are free. Claude is pay-per-token (tiny at your
  volume — only when you pick it).
- **Want a faster cloud option later?** Set `LLM_EXTRA_BASE_URL` to a
  Groq/OpenRouter OpenAI-compatible URL + `LLM_EXTRA_API_KEY` — it appears
  as a third choice. (Data leaves your network on that path.)
- **Turn off Claude**: just unset `ANTHROPIC_API_KEY` — the selector drops
  to local-only.
- **Turn off everything**: unset both — JARVIS uses the template fallback on
  real data.

---

## Other agent / integration env vars (unrelated to the LLM choice)
Per `CLAUDE.md`, these light up the non-chat agents:
- **MEGAPHONE** (WhatsApp/SMS): `MSG91_API_KEY`, `MSG91_WHATSAPP_INTEGRATED_NUMBER`,
  `MSG91_WHATSAPP_NAMESPACE`, `MSG91_SMS_TEMPLATE_ID`, `MSG91_SENDER`,
  `DISPATCH_MODE` (`off`→`test`→`live`), `TEST_PHONE`
- **PIXEL** (Lighthouse): `PAGESPEED_API_KEY`, `FRONTEND_BASE_URL`
- **NEXUS** (integrations): Shopify / Razorpay / Shiprocket / Tally keys
- **Event bus** (multi-worker): `REDIS_URL`
- **ORACLE** narratives now use the same pluggable LLM — it'll use your
  local model too, no Anthropic key required.
