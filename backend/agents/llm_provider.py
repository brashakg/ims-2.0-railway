"""
IMS 2.0 — Pluggable LLM provider
================================
One async entry point (`complete`) that routes to whichever model the
caller selects, plus a registry of the models that are configured via
env. Lets JARVIS + the agents run a self-hosted OSS model (Ollama /
vLLM / any OpenAI-compatible server) and/or Anthropic Claude, with a
runtime per-query selector — a hybrid of "local & private" and
"Claude deep analysis".

Model registry is env-driven so no code change is needed to add/remove
a model:

  Local (OpenAI-compatible — Ollama, vLLM, LM Studio, Groq, …):
    LLM_LOCAL_BASE_URL   e.g. http://ollama.railway.internal:11434/v1
    LLM_LOCAL_MODEL      e.g. qwen2.5:3b-instruct   (default)
    LLM_LOCAL_LABEL      e.g. "Local (fast & private)"
    LLM_LOCAL_API_KEY    optional (Ollama needs none; Groq needs a key)

  Claude:
    ANTHROPIC_API_KEY    (already used elsewhere)
    AGENT_CLAUDE_MODEL   default claude-haiku-4-5
    LLM_CLAUDE_LABEL     e.g. "Claude (deep analysis)"

  Optional third (any OpenAI-compatible):
    LLM_EXTRA_BASE_URL / LLM_EXTRA_MODEL / LLM_EXTRA_LABEL / LLM_EXTRA_API_KEY

  LLM_DEFAULT_MODEL      id to default to (local | claude | extra)

Privacy: `scrub_pii` redacts customer PII (names, phones, emails,
addresses, GSTIN) from any structured context before it leaves the
process. On by default for every provider — defence in depth even for
the local one.
"""
from __future__ import annotations

from typing import Optional, Dict, Any, List
import json
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)


# ============================================================================
# PII scrubbing
# ============================================================================

_PHONE_RE = re.compile(r"\b\d{10}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# Customer-only PII — third-party data the chain holds on behalf of
# patients/buyers. Always scrubbed before going to any LLM, even local.
_PII_KEYS_CUSTOMER = {
    "customername", "patientname",
    "customerphone", "customeremail",
    "patientphone", "patientemail",
    "billingaddress", "shippingaddress",
}

# Owner-data PII — names/phones of the chain's own employees + vendors.
# JARVIS is SUPERADMIN-only and the SUPERADMIN owns this data, so by
# default (scrub_level="customer") we let it through so JARVIS can say
# "Ravi sold 12 units today" instead of "[redacted] sold 12 units".
# scrub_level="all" still strips it (use that mode for outbound flows
# where data leaves the owner's perimeter — e.g. third-party support).
_PII_KEYS_OWNER = {
    "phone", "mobile", "email",
    "name", "fullname", "firstname", "lastname",
    "address",
    "gstin", "pan", "pannumber",
}

_PII_KEYS_ALL = _PII_KEYS_CUSTOMER | _PII_KEYS_OWNER


def _norm_key(k: str) -> str:
    return k.lower().replace("_", "").replace("-", "")


def scrub_pii(obj: Any, level: str = "all") -> Any:
    """Recursively redact PII from a dict/list/str so it never leaves the
    process in an LLM prompt.

    `level`:
      - "all"      : strip every PII key + mask phone/email in free text.
                     Default — defence in depth.
      - "customer" : strip only customer/patient PII keys. Owner-data
                     (own staff names, vendor names, store GSTIN) flows
                     through so JARVIS can reason about it by name.
                     Free-text phone/email patterns are still NOT
                     masked here because the cost of false-positives
                     (masking a part number that looks like a phone)
                     outweighs the benefit for owner-data.
      - "none"     : no redaction. Use only when you know the model is
                     fully local and the data is owner-owned.
    """
    if level == "none":
        return obj

    if level == "customer":
        keys = _PII_KEYS_CUSTOMER
        mask_regex = False
    else:  # "all" or anything else → safe default
        keys = _PII_KEYS_ALL
        mask_regex = True

    return _walk_scrub(obj, keys, mask_regex)


def _walk_scrub(obj: Any, keys: set, mask_regex: bool) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and _norm_key(k) in keys:
                out[k] = "[redacted]"
            else:
                out[k] = _walk_scrub(v, keys, mask_regex)
        return out
    if isinstance(obj, list):
        return [_walk_scrub(x, keys, mask_regex) for x in obj]
    if isinstance(obj, str) and mask_regex:
        s = _PHONE_RE.sub("[phone]", obj)
        s = _EMAIL_RE.sub("[email]", s)
        return s
    return obj


# ============================================================================
# Model registry (env-driven)
# ============================================================================

def _registry() -> Dict[str, Dict[str, Any]]:
    """Build the live registry from env. Called fresh each time so a
    config change (e.g. via Railway redeploy) is picked up without a
    code change."""
    models: Dict[str, Dict[str, Any]] = {}

    local_url = os.getenv("LLM_LOCAL_BASE_URL") or os.getenv("OLLAMA_BASE_URL")
    if local_url:
        models["local"] = {
            "id": "local",
            "label": os.getenv("LLM_LOCAL_LABEL", "Local (fast & private)"),
            "provider": "openai_compat",
            "base_url": local_url.rstrip("/"),
            "model": os.getenv("LLM_LOCAL_MODEL", "qwen2.5:3b-instruct"),
            "api_key": os.getenv("LLM_LOCAL_API_KEY", ""),
            "tier": "free",
        }

    if os.getenv("ANTHROPIC_API_KEY"):
        models["claude"] = {
            "id": "claude",
            "label": os.getenv("LLM_CLAUDE_LABEL", "Claude (deep analysis)"),
            "provider": "anthropic",
            "model": os.getenv("AGENT_CLAUDE_MODEL", "claude-haiku-4-5"),
            "api_key": os.getenv("ANTHROPIC_API_KEY"),
            "api_url": os.getenv("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"),
            "tier": "standard",
        }
        # Opus as a third option — same API key, different model. Marked
        # premium so the UI can warn before users opt into the pricier
        # tier (roughly 20× Haiku per query). Flip LLM_OPUS_ENABLED=false
        # to hide it.
        if os.getenv("LLM_OPUS_ENABLED", "true").lower() == "true":
            models["claude-opus"] = {
                "id": "claude-opus",
                "label": os.getenv("LLM_OPUS_LABEL", "Claude Opus 4.7 (premium)"),
                "provider": "anthropic",
                "model": os.getenv("LLM_OPUS_MODEL", "claude-opus-4-7"),
                "api_key": os.getenv("ANTHROPIC_API_KEY"),
                "api_url": os.getenv("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"),
                "tier": "premium",
            }

    extra_url = os.getenv("LLM_EXTRA_BASE_URL")
    if extra_url:
        models["extra"] = {
            "id": "extra",
            "label": os.getenv("LLM_EXTRA_LABEL", "Cloud (fast)"),
            "provider": "openai_compat",
            "base_url": extra_url.rstrip("/"),
            "model": os.getenv("LLM_EXTRA_MODEL", "llama-3.3-70b-versatile"),
            "api_key": os.getenv("LLM_EXTRA_API_KEY", ""),
            "tier": "standard",
        }

    return models


def list_models() -> List[Dict[str, str]]:
    """Public list for the UI selector — id + label + provider + tier.

    `tier` is one of "free" / "standard" / "premium" so the UI can warn
    users before switching to the pricier models. Defaults to "standard"
    for back-compat with any registry entries that haven't been tagged.
    """
    return [
        {
            "id": m["id"],
            "label": m["label"],
            "provider": m["provider"],
            "tier": m.get("tier", "standard"),
        }
        for m in _registry().values()
    ]


def default_model_id() -> Optional[str]:
    reg = _registry()
    pref = os.getenv("LLM_DEFAULT_MODEL")
    if pref and pref in reg:
        return pref
    # Prefer the private local model, then claude, then extra.
    for k in ("local", "claude", "extra"):
        if k in reg:
            return k
    return None


def model_budgets(model_id: Optional[str]) -> Dict[str, int]:
    """Tier-appropriate token + context budgets for an LLM call.

    Local Ollama is typically a small (3B-class) model running on a
    shared CPU instance with a 4096-token context window. Sending it
    a 32k-char (~8k token) prompt forces truncation AND blows past the
    LLM_TIMEOUT. Claude/Opus have 200k context — no constraint there.

    Returns a dict callers can spread into `complete()`:
        {"max_tokens": int, "context_budget": int}

    Tunable via env:
        LLM_LOCAL_MAX_TOKENS       (default 512)
        LLM_LOCAL_CONTEXT_BUDGET   (default 3000)
        LLM_STANDARD_MAX_TOKENS    (default 2048)
        LLM_STANDARD_CONTEXT_BUDGET(default 16000)
        LLM_PREMIUM_MAX_TOKENS     (default 4096)
        LLM_PREMIUM_CONTEXT_BUDGET (default 48000)
    """
    reg = _registry()
    tier = (reg.get(model_id or "", {}) or {}).get("tier", "standard")

    if tier == "free":
        return {
            "max_tokens": int(os.getenv("LLM_LOCAL_MAX_TOKENS", "512")),
            "context_budget": int(os.getenv("LLM_LOCAL_CONTEXT_BUDGET", "3000")),
        }
    if tier == "premium":
        return {
            "max_tokens": int(os.getenv("LLM_PREMIUM_MAX_TOKENS", "4096")),
            "context_budget": int(os.getenv("LLM_PREMIUM_CONTEXT_BUDGET", "48000")),
        }
    return {
        "max_tokens": int(os.getenv("LLM_STANDARD_MAX_TOKENS", "2048")),
        "context_budget": int(os.getenv("LLM_STANDARD_CONTEXT_BUDGET", "16000")),
    }


def any_available() -> bool:
    return bool(_registry())


# ============================================================================
# Completion routing
# ============================================================================

DEFAULT_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120.0"))


def _context_block(business_data: Optional[Dict], level: str = "all", budget: int = 12000) -> str:
    if not business_data:
        return ""
    safe = scrub_pii(business_data, level=level)
    try:
        return "\n\nBUSINESS DATA (JSON):\n" + json.dumps(safe, default=str)[:budget]
    except Exception:
        return ""


async def complete(
    system: str,
    user: str,
    *,
    model_id: Optional[str] = None,
    business_data: Optional[Dict] = None,
    history: Optional[List[Dict[str, str]]] = None,
    max_tokens: int = 800,
    timeout: float = DEFAULT_TIMEOUT,
    scrub: bool = True,
    scrub_level: Optional[str] = None,
    context_budget: int = 12000,
) -> Optional[str]:
    """Route a completion to the selected (or default) model. Returns the
    text, or None on any failure / no model configured (caller falls back
    to deterministic output). PII in business_data is scrubbed by default.

    `scrub_level` (new, takes precedence over the legacy `scrub` bool):
      - "all"      : full PII scrub (default — defence in depth).
      - "customer" : strip only customer/patient PII; staff/vendor data
                     flows through so JARVIS can name them.
      - "none"     : no redaction.

    `scrub=False` is equivalent to `scrub_level="none"` for back-compat.
    `context_budget` is the max chars from business_data baked into the
    system prompt — bumped from 12k by callers that pass a wider lens.
    """
    reg = _registry()
    mid = model_id if (model_id and model_id in reg) else default_model_id()
    if not mid:
        return None
    m = reg[mid]

    level = scrub_level if scrub_level else ("all" if scrub else "none")
    ctx = _context_block(business_data, level=level, budget=context_budget)
    full_system = system + ctx

    try:
        if m["provider"] == "anthropic":
            return await _call_anthropic(full_system, user, m, history, max_tokens, timeout)
        return await _call_openai_compat(full_system, user, m, history, max_tokens, timeout)
    except httpx.TimeoutException:
        logger.warning("[LLM] %s timeout after %ss", mid, timeout)
    except httpx.HTTPError as e:
        logger.warning("[LLM] %s HTTP error: %s", mid, e)
    except (ValueError, KeyError, TypeError) as e:
        logger.warning("[LLM] %s parse error: %s", mid, e)
    return None


async def _call_anthropic(system, user, m, history, max_tokens, timeout) -> Optional[str]:
    messages: List[Dict[str, str]] = []
    if history:
        messages.extend(history[-8:])
    messages.append({"role": "user", "content": user})
    payload = {"model": m["model"], "max_tokens": max_tokens, "system": system, "messages": messages}
    headers = {
        "x-api-key": m["api_key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(m["api_url"], headers=headers, json=payload)
    if resp.status_code != 200:
        logger.warning("[LLM] anthropic %s: %s", resp.status_code, resp.text[:300])
        return None
    content = (resp.json().get("content") or [])
    if content and isinstance(content, list) and isinstance(content[0], dict):
        return content[0].get("text")
    return None


async def _call_openai_compat(system, user, m, history, max_tokens, timeout) -> Optional[str]:
    messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-8:])
    messages.append({"role": "user", "content": user})
    payload = {"model": m["model"], "messages": messages, "max_tokens": max_tokens, "stream": False}
    headers = {"content-type": "application/json"}
    if m.get("api_key"):
        headers["authorization"] = f"Bearer {m['api_key']}"
    url = m["base_url"] + "/chat/completions"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        logger.warning("[LLM] openai_compat %s: %s", resp.status_code, resp.text[:300])
        return None
    choices = resp.json().get("choices") or []
    if choices and isinstance(choices[0], dict):
        return (choices[0].get("message") or {}).get("content")
    return None


async def complete_json(system: str, user: str, **kwargs) -> Optional[Dict[str, Any]]:
    """complete() that expects a JSON object back."""
    json_system = (
        system
        + "\n\nRESPONSE FORMAT (STRICT): Respond with a single JSON object. "
        + "No markdown, no code fences, no commentary. First char must be `{`."
    )
    text = await complete(json_system, user, **kwargs)
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None
