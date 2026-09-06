"""TechCherry R3: the LLM-narrated growth blueprint."""

from fastapi import Depends, Query
from typing import Any, Dict, Optional
from datetime import datetime
from ...utils.ist import (
    now_ist,
)
from ..auth import get_current_user
from ...dependencies import (
    get_db,
    validate_store_access,
)
from ._shared import router
from .analytics import (
    footfall_audit,
    sales_lens_deep_dive,
    sales_price_bands,
    sales_seasonality,
)
from .purchase import purchase_recommendations

# ----------------------------------------------------------------------------
# R3 — Growth Blueprint (LLM-narrated synthesis of R1 + R2)
#
# Spec: docs/TECHCHERRY_PORT_SCOPE.md §7.
# Calls JARVIS's pluggable LLM provider with a structured prompt that
# bundles every R1+R2 endpoint output. Returns a 12-section consultant-
# style narrative the operator can read on /reports/blueprint or print.
# ----------------------------------------------------------------------------

_BLUEPRINT_SECTIONS = [
    "Where the business stands today",
    "Revenue trajectory — ATV-driven or footfall-driven?",
    "Premiumization proof — price band movement",
    "Staff honest assessment",
    "Footfall integrity — hidden sales quantified",
    "Lens business as profit engine",
    "Discount discipline",
    "Growth levers (ranked by ₹ impact)",
    "Revenue projections (conservative 3-year)",
    "Quick wins (zero-cost)",
    "Top 10 actions",
    "Competitive positioning",
]

_BLUEPRINT_SYSTEM_PROMPT = """You are JARVIS acting as a senior retail-strategy consultant to the Superadmin of Better Vision + WizOpt — a premium optical retail chain in India.

You have been handed a structured brief containing every analytics output the chain has on record:
  - Footfall audit (walk-in vs walkout vs orders, hidden sales)
  - Price band shift (premiumization signal)
  - Lens deep-dive (the profit engine)
  - Seasonality (day-of-week + month-of-year)
  - Purchase recommendations (what to reorder)

Your job: produce a 12-section **Growth Blueprint** that synthesises this into a strategic narrative. Each section must be GROUNDED in the numbers — quote specific ₹ values, percentages, or counts from the brief. Where the data is empty or zero, say so plainly and explain what to track to fill the gap. Do NOT fabricate numbers.

## Tone
- Senior consultant brief, not a marketing deck
- Direct, honest assessments — including bad news
- Indian Rupee formatting (₹, L for Lakh, Cr for Crore)
- Markdown for structure (## headings, bullet lists, **bold** for ₹ figures)
- 1500–2500 words total

## Required sections (exactly these, in this order):

{sections_list}

Each section is one or two short paragraphs followed by 3–5 bullet points where appropriate. Section 8 (Growth levers) MUST be a numbered list with ₹ impact per lever. Section 11 (Top 10 actions) MUST be exactly 10 bullets, each starting with an imperative verb (Audit / Train / Reorder / Run / Track…).

End with: a single line stating the report's confidence level (HIGH / MEDIUM / LOW) and the most important caveat (e.g. "limited by 3-month order history" or "footfall data sparse for 2 stores"). Do NOT add any other closing text — no "let me know if…", no signature.
"""


async def _r3_assemble_inputs(
    store_id: Optional[str], current_user: dict
) -> Dict[str, Any]:
    """Call the R1+R2 endpoints internally and bundle their outputs.
    Each call is wrapped — a single failing report shouldn't take down
    the whole blueprint. Returns a dict the LLM prompt can format."""
    inputs: Dict[str, Any] = {}
    # Direct function calls to avoid an HTTP round-trip back into ourselves
    try:
        inputs["footfall_audit"] = await footfall_audit(  # type: ignore[arg-type]
            store_id=store_id,
            months_back=12,
            current_user=current_user,
        )
    except Exception as e:
        inputs["footfall_audit"] = {"error": str(e)}
    try:
        inputs["price_bands"] = await sales_price_bands(  # type: ignore[arg-type]
            store_id=store_id,
            fy_count=3,
            current_user=current_user,
        )
    except Exception as e:
        inputs["price_bands"] = {"error": str(e)}
    try:
        inputs["lens_deep_dive"] = await sales_lens_deep_dive(  # type: ignore[arg-type]
            store_id=store_id,
            months_back=12,
            current_user=current_user,
        )
    except Exception as e:
        inputs["lens_deep_dive"] = {"error": str(e)}
    try:
        inputs["seasonality"] = await sales_seasonality(  # type: ignore[arg-type]
            store_id=store_id,
            years_back=2,
            current_user=current_user,
        )
    except Exception as e:
        inputs["seasonality"] = {"error": str(e)}
    try:
        inputs["purchase_recommendations"] = await purchase_recommendations(  # type: ignore[arg-type]
            store_id=store_id,
            lookback_days=90,
            lead_time_days=30,
            reorder_cycle_days=30,
            safety_buffer_days=7,
            min_velocity=2,
            limit=50,
            current_user=current_user,
        )
    except Exception as e:
        inputs["purchase_recommendations"] = {"error": str(e)}
    # Plus a summary overview so the blueprint has total revenue context
    try:
        from ..jarvis import JarvisAnalyticsEngine  # local import to avoid cycle

        inputs["overview"] = JarvisAnalyticsEngine.get_business_overview()
    except Exception as e:
        inputs["overview"] = {"error": str(e)}
    return inputs


@router.get(
    "/blueprint",
    summary="Growth Blueprint — JARVIS-narrated consultant synthesis of R1+R2",
    description=(
        "Calls JARVIS's LLM provider with every R1+R2 analytics output "
        "bundled as context and asks for a 12-section consultant-style "
        "narrative. Cost-sensitive — local OSS or Claude Haiku is the "
        "default; Opus is opt-in via `model_id=claude-opus`. "
        "Cached per (store, month) for 24h so refreshes don't incur "
        "extra LLM cost during the same business day."
    ),
)
async def growth_blueprint(
    store_id: Optional[str] = Query(None),
    model_id: Optional[str] = Query(
        None, description="LLM model id (local/claude/claude-opus). None = default."
    ),
    nocache: bool = Query(False, description="Bypass per-(store,month) cache"),
    current_user: dict = Depends(get_current_user),
):
    """Generate (or fetch from cache) the Growth Blueprint."""
    import json as _json
    from agents import llm_provider  # local import — agents pkg may be optional

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    month_key = now_ist().strftime("%Y-%m")
    cache_key = f"{active_store}::{month_key}::{model_id or 'default'}"
    db = get_db()

    # 1. Cache lookup — 24h TTL on per-(store, month, model) key
    if not nocache and db is not None:
        try:
            cache_col = db.get_collection("report_blueprints")
            existing = cache_col.find_one({"cache_key": cache_key})
            if existing:
                age_hours = (
                    (
                        datetime.now() - existing.get("generated_at", datetime.min)
                    ).total_seconds()
                    / 3600
                    if isinstance(existing.get("generated_at"), datetime)
                    else 999
                )
                if age_hours <= 24:
                    return {
                        "narrative_markdown": existing.get("narrative_markdown", ""),
                        "sections": _BLUEPRINT_SECTIONS,
                        "model_used": existing.get("model_used"),
                        "store_id": active_store,
                        "month": month_key,
                        "generated_at": existing.get("generated_at"),
                        "from_cache": True,
                        "cache_age_hours": round(age_hours, 1),
                    }
        except Exception:
            pass

    # 2. Pull the inputs
    inputs = await _r3_assemble_inputs(active_store, current_user)

    # 3. Compose the user prompt with the analytics brief
    sections_list = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_BLUEPRINT_SECTIONS))
    # .replace() rather than .format() — defensive against future edits
    # adding curly-braced literals (e.g. JSON shape examples) to the
    # prompt. .format() would treat them as missing placeholders.
    system = _BLUEPRINT_SYSTEM_PROMPT.replace("{sections_list}", sections_list)
    user_msg = (
        f"Store: {active_store}\n"
        f"As-of: {now_ist().strftime('%Y-%m-%d %H:%M IST')}\n\n"
        f"Produce the full 12-section Growth Blueprint grounded in the "
        f"BUSINESS DATA below."
    )

    # 4. Call LLM (defence-in-depth: scrub_level='customer' so any
    # accidentally-included customer PII gets redacted; staff/vendor
    # data is preserved)
    if not llm_provider.any_available():
        return {
            "narrative_markdown": (
                "# Growth Blueprint unavailable\n\n"
                "No LLM provider is configured on this deployment. "
                "Set `ANTHROPIC_API_KEY` (Claude) and retry."
            ),
            "sections": _BLUEPRINT_SECTIONS,
            "model_used": None,
            "store_id": active_store,
            "month": month_key,
            "generated_at": datetime.now().isoformat(),
            "from_cache": False,
            "error": "no LLM provider configured",
        }

    try:
        narrative = await llm_provider.complete(
            system,
            user_msg,
            model_id=model_id,
            business_data=inputs,
            max_tokens=4096,
            timeout=180.0,
            scrub_level="customer",
            context_budget=48000,
        )
    except Exception as e:
        return {
            "narrative_markdown": (
                f"# Growth Blueprint generation failed\n\n"
                f"LLM call raised: `{type(e).__name__}: {e}`\n\n"
                "Try again, or switch to a different model."
            ),
            "sections": _BLUEPRINT_SECTIONS,
            "model_used": model_id,
            "store_id": active_store,
            "month": month_key,
            "generated_at": datetime.now().isoformat(),
            "from_cache": False,
            "error": f"{type(e).__name__}: {e}",
        }

    if not narrative:
        return {
            "narrative_markdown": (
                "# Growth Blueprint generation returned empty\n\n"
                "The LLM did not return a response. This usually means a "
                "timeout or rate limit. Try again in a minute, or switch "
                "to a different model."
            ),
            "sections": _BLUEPRINT_SECTIONS,
            "model_used": model_id,
            "store_id": active_store,
            "month": month_key,
            "generated_at": datetime.now().isoformat(),
            "from_cache": False,
            "error": "empty LLM response",
        }

    model_used = model_id or llm_provider.default_model_id() or "default"
    generated_at = datetime.now()

    # 5. Persist to cache
    if db is not None:
        try:
            cache_col = db.get_collection("report_blueprints")
            cache_col.update_one(
                {"cache_key": cache_key},
                {
                    "$set": {
                        "cache_key": cache_key,
                        "store_id": active_store,
                        "month": month_key,
                        "model_used": model_used,
                        "narrative_markdown": narrative,
                        "generated_at": generated_at,
                        "inputs_meta": {
                            "footfall_months": (inputs.get("footfall_audit") or {})
                            .get("rolling", {})
                            .get("orders_total"),
                            "purchase_recs_count": (
                                inputs.get("purchase_recommendations") or {}
                            )
                            .get("summary", {})
                            .get("total_recommendations"),
                            "price_bands_fy_count": len(
                                (inputs.get("price_bands") or {}).get("bands", [])
                            ),
                        },
                    }
                },
                upsert=True,
            )
        except Exception:
            pass  # Cache write failure is non-fatal

    return {
        "narrative_markdown": narrative,
        "sections": _BLUEPRINT_SECTIONS,
        "model_used": model_used,
        "store_id": active_store,
        "month": month_key,
        "generated_at": generated_at.isoformat(),
        "from_cache": False,
    }


