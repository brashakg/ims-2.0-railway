"""
IMS 2.0 — PIXEL: UI/UX Quality Agent
======================================
Hero Identity: Batman / The Detective (DC)
"The world's greatest detective. Sees every flaw, misses nothing."

PIXEL audits the production frontend on a daily cadence and on every
Vercel deploy event. Each run scores performance + accessibility + best
practices + SEO for the core 9 module routes, records results to the
ui_audits collection, and emits ui.regression_detected when a metric
crosses a previously-established threshold.

## Audit source

Uses Google PageSpeed Insights API — the hosted Lighthouse endpoint.
Cleaner than bundling Node + lighthouse + puppeteer into the Railway
Docker image. Free tier allows ~25k requests/day which is ~500x what
we need (9 routes × 1 run/day = 9 audits/day).

## What it records

Per-URL per-run:
  - lighthouse_scores: {performance, accessibility, best_practices, seo}
    all 0-1 scaled (Lighthouse native scale)
  - core_web_vitals: LCP, CLS, TBT from lab data
  - a11y_violations: count of failing audits in the accessibility category
  - ran_at, commit_sha (from Vercel event if triggered by deploy)

Per-run summary:
  - overall_min_score: worst score across all pages (surfaced on Jarvis)
  - regressions: pages that dropped > 10 points vs last week

## Activation

Requires PAGESPEED_API_KEY env var (get free at Google Cloud Console).
Without it, PIXEL falls back to heartbeat-only behavior (pre-Phase 4).
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import logging
import os

import httpx

from ..base import JarvisAgent, AgentType, AgentResponse, AgentContext

logger = logging.getLogger(__name__)


# Env config
PAGESPEED_API_KEY = os.getenv("PAGESPEED_API_KEY", "")
PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
FRONTEND_BASE_URL = os.getenv(
    "FRONTEND_BASE_URL",
    "https://ims-2-0-railway.vercel.app",
)
AUDIT_TIMEOUT = float(os.getenv("PAGESPEED_TIMEOUT", "60.0"))

# Core routes to audit each day — the 9 design modules + the login page.
# Login first (unauthenticated is the only page PageSpeed can reach; the
# others will show the login redirect until we add a public health page,
# but we still record them for regression baselines on the redirect chain).
AUDIT_ROUTES = [
    "/login",
    "/dashboard",  # Hub — redirects to login when unauth
    "/pos",
    "/clinical",
    "/inventory",
    "/reports",
    "/tasks",
    "/print",
    "/settings",
]

# How much a score can drop vs last week before we flag a regression.
# Lighthouse scores are 0–1; 0.1 = 10 points on the typical 0–100 scale.
REGRESSION_THRESHOLD = 0.10


def _is_pagespeed_available() -> bool:
    return bool(PAGESPEED_API_KEY)


async def _audit_url(url: str) -> Optional[Dict[str, Any]]:
    """
    Call PageSpeed Insights for one URL. Returns the parsed result or None
    on any failure. Fails soft so a single bad URL doesn't kill the run.
    """
    if not _is_pagespeed_available():
        return None
    try:
        async with httpx.AsyncClient(timeout=AUDIT_TIMEOUT) as client:
            resp = await client.get(
                PAGESPEED_URL,
                params=[
                    ("url", url),
                    ("key", PAGESPEED_API_KEY),
                    # Request all 4 categories Lighthouse supports
                    ("category", "performance"),
                    ("category", "accessibility"),
                    ("category", "best-practices"),
                    ("category", "seo"),
                    ("strategy", "mobile"),  # mobile audit; ~80% of BV traffic
                ],
            )
        if resp.status_code != 200:
            logger.warning(f"[PIXEL] PageSpeed {resp.status_code} for {url}: {resp.text[:300]}")
            return None
        body = resp.json()
        lh = body.get("lighthouseResult") or {}
        cats = lh.get("categories") or {}
        audits = lh.get("audits") or {}

        # Extract category scores (0–1)
        scores = {
            "performance":    (cats.get("performance") or {}).get("score"),
            "accessibility":  (cats.get("accessibility") or {}).get("score"),
            "best_practices": (cats.get("best-practices") or {}).get("score"),
            "seo":            (cats.get("seo") or {}).get("score"),
        }

        # Core Web Vitals from lab data
        cwv = {
            "lcp_ms":  (audits.get("largest-contentful-paint") or {}).get("numericValue"),
            "cls":     (audits.get("cumulative-layout-shift") or {}).get("numericValue"),
            "tbt_ms":  (audits.get("total-blocking-time") or {}).get("numericValue"),
            "fcp_ms":  (audits.get("first-contentful-paint") or {}).get("numericValue"),
            "si_ms":   (audits.get("speed-index") or {}).get("numericValue"),
        }

        # Accessibility violations: count audits in a11y category with score < 1
        a11y_violations = []
        a11y_category = cats.get("accessibility") or {}
        for ref in (a11y_category.get("auditRefs") or []):
            audit_id = ref.get("id")
            if not audit_id:
                continue
            audit = audits.get(audit_id) or {}
            score = audit.get("score")
            if score is not None and score < 1:
                a11y_violations.append({
                    "id": audit_id,
                    "title": audit.get("title"),
                    "score": score,
                    "impact": ref.get("weight", 0),
                })

        return {
            "url": url,
            "scores": scores,
            "core_web_vitals": cwv,
            "a11y_violations_count": len(a11y_violations),
            "a11y_violations_top3": sorted(a11y_violations, key=lambda v: -v["impact"])[:3],
            "fetch_time_ms": (lh.get("timing") or {}).get("total"),
        }
    except httpx.TimeoutException:
        logger.warning(f"[PIXEL] PageSpeed timeout on {url}")
        return None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        logger.warning(f"[PIXEL] PageSpeed error on {url}: {e}")
        return None


class PixelAgent(JarvisAgent):
    """UI/UX quality auditor — Lighthouse scores, a11y violations, regressions."""

    agent_id = "pixel"
    agent_name = "PIXEL"
    agent_type = AgentType.AUDITOR
    description = "UI/UX quality — performance, accessibility, visual regression on every Vercel deploy"
    version = "2.0.0"
    toggleable = True

    capabilities = [
        "performance_audit",
        "accessibility_audit",
        "core_web_vitals",
        "regression_detection",
        "deploy_event_handler",
    ]

    async def _do_background_work(self):
        """
        Run a full audit cycle: each core route gets a PageSpeed run,
        results persist to ui_audits, regressions vs last week emit
        ui.regression_detected events for CORTEX.
        """
        coll = self.get_collection("ui_audits")
        if coll is None:
            logger.info("[PIXEL] ui_audits collection unavailable — skipping audit")
            return

        if not _is_pagespeed_available():
            # Fall back to heartbeat behavior — record that the agent ticked
            # but didn't have credentials to do real work. This lets the
            # operator see that PIXEL is scheduled correctly even before
            # PAGESPEED_API_KEY is provisioned.
            try:
                coll.insert_one({
                    "ran_at": datetime.now(timezone.utc).isoformat(),
                    "agent_id": self.agent_id,
                    "kind": "heartbeat",
                    "notes": "PAGESPEED_API_KEY unset — heartbeat only",
                })
            except Exception as e:
                logger.warning(f"[PIXEL] Heartbeat write failed: {e}")
            return

        ran_at = datetime.now(timezone.utc).isoformat()
        page_results: List[Dict[str, Any]] = []

        for route in AUDIT_ROUTES:
            full_url = FRONTEND_BASE_URL.rstrip("/") + route
            result = await _audit_url(full_url)
            if result:
                page_results.append(result)

        if not page_results:
            logger.warning("[PIXEL] All PageSpeed calls failed — no audit recorded")
            return

        # Compute run-level metrics
        all_perf = [r["scores"]["performance"] for r in page_results if r["scores"]["performance"] is not None]
        all_a11y = [r["scores"]["accessibility"] for r in page_results if r["scores"]["accessibility"] is not None]
        summary = {
            "overall_min_perf":  min(all_perf) if all_perf else None,
            "overall_min_a11y":  min(all_a11y) if all_a11y else None,
            "total_a11y_violations": sum(r["a11y_violations_count"] for r in page_results),
            "pages_audited": len(page_results),
        }

        # Regression detection vs last week
        regressions = await self._detect_regressions(coll, page_results)

        audit_doc = {
            "ran_at": ran_at,
            "agent_id": self.agent_id,
            "kind": "scheduled_audit",
            "summary": summary,
            "pages": page_results,
            "regressions": regressions,
        }
        try:
            coll.insert_one(audit_doc)
        except Exception as e:
            logger.warning(f"[PIXEL] Audit write failed: {e}")

        # Emit events for regressions
        if regressions:
            await self._emit_regression_events(regressions)

        logger.info(
            f"[PIXEL] Audit complete — {len(page_results)} pages, "
            f"min perf={summary['overall_min_perf']}, "
            f"min a11y={summary['overall_min_a11y']}, "
            f"regressions={len(regressions)}"
        )

    async def _detect_regressions(self, coll, current: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compare each URL's scores vs the last audit from > 7 days ago."""
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            baseline_doc = coll.find_one(
                {"kind": "scheduled_audit", "ran_at": {"$lt": cutoff}},
                sort=[("ran_at", -1)],
            )
        except Exception as e:
            logger.debug(f"[PIXEL] Baseline lookup error: {e}")
            baseline_doc = None

        if not baseline_doc:
            return []

        baseline_by_url = {p["url"]: p for p in (baseline_doc.get("pages") or [])}
        regressions = []
        for page in current:
            base = baseline_by_url.get(page["url"])
            if not base:
                continue
            for metric in ("performance", "accessibility", "best_practices", "seo"):
                cur_score = page["scores"].get(metric)
                base_score = (base.get("scores") or {}).get(metric)
                if cur_score is None or base_score is None:
                    continue
                delta = cur_score - base_score
                if delta <= -REGRESSION_THRESHOLD:
                    regressions.append({
                        "url": page["url"],
                        "metric": metric,
                        "current": round(cur_score, 3),
                        "baseline": round(base_score, 3),
                        "delta": round(delta, 3),
                    })
        return regressions

    async def _emit_regression_events(self, regressions: List[Dict[str, Any]]):
        from ..registry import dispatch_event
        for reg in regressions:
            try:
                await dispatch_event(
                    "ui.regression_detected",
                    reg,
                    source=self.agent_id,
                )
            except Exception as e:
                logger.warning(f"[PIXEL] Event dispatch failed: {e}")

    async def on_event(self, event: str, payload: Dict[str, Any]):
        """Vercel deploy.success → run an immediate audit cycle."""
        if event == "deploy.success":
            logger.info(f"[PIXEL] Deploy detected ({payload.get('commit', '?')}) — running audit")
            await self._do_background_work()

    async def run(self, query: str, context: AgentContext) -> AgentResponse:
        """On-demand: most recent audit summary + any open regressions."""
        coll = self.get_collection("ui_audits")
        if coll is None:
            return AgentResponse(
                success=False,
                agent_id=self.agent_id,
                message="ui_audits collection unavailable",
            )
        try:
            recent = list(
                coll.find({"agent_id": self.agent_id}, {"_id": 0})
                .sort("ran_at", -1)
                .limit(5)
            )
        except Exception as e:
            return AgentResponse(success=False, agent_id=self.agent_id, message=str(e))

        latest = recent[0] if recent else None
        return AgentResponse(
            success=True,
            agent_id=self.agent_id,
            data={
                "latest_audit": latest,
                "recent_audit_count": len(recent),
                "pagespeed_ready": _is_pagespeed_available(),
                "frontend_url": FRONTEND_BASE_URL,
            },
            message=(
                f"PIXEL · {len(recent)} audits on record · "
                f"pagespeed_ready={_is_pagespeed_available()} · "
                f"latest: {latest.get('ran_at') if latest else 'none'}"
            ),
        )
