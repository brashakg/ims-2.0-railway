"""
IMS 2.0 — Shared Claude API client for agents
================================================
Lightweight async httpx wrapper around the Anthropic Messages API, used by
any agent that needs to call Claude (ORACLE for anomaly narratives,
TASKMASTER for action reasoning, MEGAPHONE for campaign copy, etc.).

Design notes:
- Failing soft — returns None if ANTHROPIC_API_KEY is unset, the call
  times out, or the API errors. Callers must handle None and fall back
  to deterministic output. We never block an agent's tick on an
  external API being reachable.
- Model + timeout overridable via env so Phase 4 benchmarking can
  cheap out with Haiku and an integration test can mock-shim the URL.
- `json_response=True` makes Claude emit a JSON object and parses it;
  the helper retries once on a parse failure before surrendering.
"""

from typing import Optional, Dict, Any, List
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)


ANTHROPIC_API_URL = os.getenv("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
DEFAULT_MODEL = os.getenv("AGENT_CLAUDE_MODEL", "claude-haiku-4-5")
DEFAULT_MAX_TOKENS = int(os.getenv("AGENT_CLAUDE_MAX_TOKENS", "1024"))
DEFAULT_TIMEOUT = float(os.getenv("AGENT_CLAUDE_TIMEOUT", "30.0"))


def is_claude_available() -> bool:
    """Quick check for agents to decide whether to bother calling."""
    return bool(ANTHROPIC_API_KEY)


async def call_claude(
    system: str,
    user: str,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: float = DEFAULT_TIMEOUT,
    history: Optional[List[Dict[str, str]]] = None,
) -> Optional[str]:
    """
    Call Claude with a system prompt + user message. Returns the text
    completion, or None on any failure. Failure modes (all logged but
    never raised):
      - ANTHROPIC_API_KEY unset
      - Network timeout / connection error
      - Non-200 response
      - Malformed response body

    Args:
      system: The system prompt. Keep short for agent tasks.
      user: The user message. Usually the anomaly / task context.
      model: Claude model ID; defaults to Haiku for cheap agent ticks.
      max_tokens: Response cap.
      timeout: Per-request timeout.
      history: Optional prior messages [{"role": "user"|"assistant", "content": ...}]
               appended in order before the user message.
    """
    if not ANTHROPIC_API_KEY:
        logger.debug("[CLAUDE] API key unset — skipping call")
        return None

    messages: List[Dict[str, str]] = []
    if history:
        # Only keep the last 8 turns to bound token usage
        messages.extend(history[-8:])
    messages.append({"role": "user", "content": user})

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(ANTHROPIC_API_URL, headers=headers, json=payload)
        if resp.status_code != 200:
            logger.warning(f"[CLAUDE] {resp.status_code} from Anthropic: {resp.text[:400]}")
            return None
        body = resp.json()
        # Response shape: {"content": [{"type": "text", "text": "..."}], ...}
        content = body.get("content") or []
        if not content or not isinstance(content, list):
            logger.warning(f"[CLAUDE] Empty content in response: {body}")
            return None
        first = content[0]
        text = first.get("text") if isinstance(first, dict) else None
        if not text:
            logger.warning(f"[CLAUDE] No text in first content block: {first}")
            return None
        return text
    except httpx.TimeoutException:
        logger.warning(f"[CLAUDE] Timeout after {timeout}s")
        return None
    except httpx.HTTPError as e:
        logger.warning(f"[CLAUDE] HTTP error: {e}")
        return None
    except (ValueError, KeyError, TypeError) as e:
        logger.warning(f"[CLAUDE] Response parse error: {e}")
        return None


async def call_claude_json(
    system: str,
    user: str,
    **kwargs,
) -> Optional[Dict[str, Any]]:
    """
    Like call_claude but expects a JSON object back. Forces the model
    into JSON-emission mode by prepending a strict instruction to the
    system prompt. Retries once on parse failure with an even stricter
    prompt before giving up.
    """
    json_system = (
        system
        + "\n\nRESPONSE FORMAT (STRICT):\n"
        + "Respond with a single JSON object. No markdown, no code fences,\n"
        + "no preamble, no commentary. If you can't produce the requested\n"
        + "fields, still return valid JSON with nulls. The first character\n"
        + "of your response must be `{`."
    )

    text = await call_claude(json_system, user, **kwargs)
    if text is None:
        return None

    # Strip any accidental code fence wrapping
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        # Remove possible `json` language tag
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].lstrip()

    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        pass

    # Retry once with an even more explicit instruction
    retry_system = (
        json_system
        + "\n\nYOUR PREVIOUS RESPONSE WAS NOT VALID JSON. RETRY. "
        + "Emit nothing but the JSON object."
    )
    retry = await call_claude(retry_system, user, **kwargs)
    if retry is None:
        return None
    retry_stripped = retry.strip().strip("`")
    if retry_stripped.lower().startswith("json"):
        retry_stripped = retry_stripped[4:].lstrip()
    try:
        return json.loads(retry_stripped)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"[CLAUDE] JSON retry also failed: {e}; got: {retry_stripped[:200]!r}")
        return None
