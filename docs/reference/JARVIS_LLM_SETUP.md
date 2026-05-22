# JARVIS LLM — Claude setup

JARVIS (and the agents) run through a pluggable LLM provider
(`backend/agents/llm_provider.py`), configured for **Anthropic Claude**.
Customer PII is **scrubbed** from the business context before any prompt
leaves the process.

If **nothing** is configured, JARVIS still works — it falls back to the
deterministic, real-data template responses (no fabricated numbers).

> The previous self-hosted Ollama / local-LLM option has been **removed** —
> the system is **Claude-only** now.

---

## How model selection works

The registry is built from env vars at request time. Configured models show
up in the chat's model dropdown (and `GET /jarvis/models`):

| Model id | Enabled by | Use |
|---|---|---|
| `claude` | `ANTHROPIC_API_KEY` set | Anthropic Claude — chat + analysis (default) |
| `extra` | `LLM_EXTRA_BASE_URL` set | Any other OpenAI-compatible endpoint (e.g. Groq) |

`LLM_DEFAULT_MODEL` picks the default (else `claude` → `extra`). The selector
only renders when 2+ models are configured.

---

## Setup on Railway

On your **backend** service → **Variables**:
```
ANTHROPIC_API_KEY  = sk-ant-...
AGENT_CLAUDE_MODEL = claude-haiku-4-5
```
Railway redeploys on a variable change. Verify: `GET /api/v1/jarvis/models`
lists Claude and the JARVIS chat works.

- **Turn off Claude**: unset `ANTHROPIC_API_KEY` — JARVIS uses the template
  fallback on real data.
- **Add another cloud option**: set `LLM_EXTRA_BASE_URL` + `LLM_EXTRA_API_KEY`
  (any OpenAI-compatible endpoint, e.g. Groq/OpenRouter) — it appears as a
  second choice. Note: data leaves your network on that path.

---

## Notes
- **Privacy**: PII (names, phones, emails, addresses, GSTIN) is redacted by
  `scrub_pii` before any prompt is sent.
- **Cost**: Claude is pay-per-token (small at your volume).

---

## Other agent / integration env vars (unrelated to the LLM choice)
Per `CLAUDE.md`, these light up the non-chat agents:
- **MEGAPHONE** (WhatsApp/SMS): `MSG91_API_KEY`, `MSG91_WHATSAPP_INTEGRATED_NUMBER`,
  `MSG91_WHATSAPP_NAMESPACE`, `MSG91_SMS_TEMPLATE_ID`, `MSG91_SENDER`,
  `DISPATCH_MODE` (`off`→`test`→`live`), `TEST_PHONE`
- **PIXEL** (Lighthouse): `PAGESPEED_API_KEY`, `FRONTEND_BASE_URL`
- **NEXUS** (integrations): Shopify / Razorpay / Shiprocket / Tally keys
- **Event bus** (multi-worker): `REDIS_URL`
- **ORACLE** narratives use the same Claude provider.
