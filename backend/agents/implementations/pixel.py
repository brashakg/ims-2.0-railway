"""
IMS 2.0 - PIXEL: UI/UX Quality Agent
======================================
Hero Identity: Batman / The Detective (DC)
"The world's greatest detective. Sees every flaw, misses nothing."

PIXEL audits the production frontend on a daily cadence and on every
Vercel deploy event. Each run scores performance + accessibility + best
practices + SEO for the core 9 module routes, records results to the
ui_audits collection, and emits ui.regression_detected when a metric
crosses a previously-established threshold.

## Audit source

Uses Google PageSpeed Insights API - the hosted Lighthouse endpoint.
Cleaner than bundling Node + lighthouse + puppeteer into the Railway
Docker image. Free tier allows ~25k requests/day which is ~500x what
we need (9 routes x 1 run/day = 9 audits/day).

## EVERY TICK LEAVES A ROW  (Audit Everything - Fail Loudly)

PIXEL wrote NOTHING for its entire life in production. The old code
recorded a heartbeat only when the key was MISSING, so a key that was
present-but-rejected took the real-audit path, watched all 9 PageSpeed
calls answer HTTP 400, hit "All PageSpeed calls failed" and returned in
silence - not even evidence of life. A silent agent is worse than a
disabled one.

Now every tick persists exactly one row to `ui_audits` carrying an
`outcome`:

    ok                    kind="scheduled_audit"  real Lighthouse scores
    no_credentials        kind="run_failed"       no PageSpeed key anywhere
    credentials_rejected  kind="run_failed"       Google answered 400/401/403
    all_calls_failed      kind="run_failed"       timeouts / 5xx / network

Failure rows carry the HTTP status, ONE truncated error message per
route, and the routes attempted, so the outcome is diagnosable from
Mongo alone. The API key is never written to Mongo or to a log line -
every string that could carry it goes through _scrub().

`health_check()` reads that last row, so a broken PIXEL shows DEGRADED
with the reason on its Jarvis agent card instead of a green tile that
does nothing.

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

Requires a Google PageSpeed API key (free at Google Cloud Console), saved
either in Settings -> Integrations -> Google PageSpeed or as the
PAGESPEED_API_KEY env var. Without one, PIXEL records a `no_credentials`
row every tick rather than pretending to work.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import logging
import os

import httpx

from ..base import JarvisAgent, AgentType, AgentResponse, AgentContext, HealthStatus

logger = logging.getLogger(__name__)


# Env config. The API KEY is deliberately NOT captured here: it is resolved
# per audit by _pagespeed_key() -- Settings -> Integrations -> Google PageSpeed
# first, then PAGESPEED_API_KEY -- so a key saved on the screen works without
# a redeploy.
PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
FRONTEND_BASE_URL = os.getenv(
    "FRONTEND_BASE_URL",
    "https://ims-2-0-railway.vercel.app",
)
AUDIT_TIMEOUT = float(os.getenv("PAGESPEED_TIMEOUT", "60.0"))

# Core routes to audit each day - the 9 design modules + the login page.
# Login first (unauthenticated is the only page PageSpeed can reach; the
# others will show the login redirect until we add a public health page,
# but we still record them for regression baselines on the redirect chain).
#
# TODO(layout-probe): PageSpeed runs UNAUTHENTICATED and Lighthouse has no
# "these two panels overlap" audit, so nothing in this list can catch a
# layout defect like the POS surfaces overlapping at 390px. The real check
# is the geometric overlap probe that lives with the Playwright e2e suite
# (e2e/fixtures/overlap.ts + e2e/tests/pos-surface-layout.spec.ts, landing
# on a separate branch). When it lands, PIXEL should READ that CI result
# here. Do NOT re-implement the width list or the overlap rule in this file
# (one rule, one implementation), and do NOT put a browser in the
# python:3.12-slim backend image.
AUDIT_ROUTES = [
    "/login",
    "/dashboard",  # Hub - redirects to login when unauth
    "/pos",
    "/clinical",
    "/inventory",
    "/reports",
    "/tasks",
    "/print",
    "/settings",
]

# How much a score can drop vs last week before we flag a regression.
# Lighthouse scores are 0-1; 0.1 = 10 points on the typical 0-100 scale.
REGRESSION_THRESHOLD = 0.10

# HTTP statuses that mean "Google looked at your key and said no". Anything
# else (timeout, 5xx, network) is a transient failure, not a bad credential.
CREDENTIAL_REJECT_STATUSES = (400, 401, 403)

# One truncated error message per route -- enough to diagnose, small enough
# that nine of them cannot bloat the document.
_ERROR_MAX = 200


def _pagespeed_key() -> str:
    """PageSpeed API key, resolved fresh (screen first, then env)."""
    from api.services.integration_config import get_pagespeed_config

    return get_pagespeed_config().get("api_key", "")


def _is_pagespeed_available() -> bool:
    """Is a key present at all? PRESENCE ONLY - says nothing about validity.

    Deliberately loose: a key Google would accept but that does not match
    today's format must never be locked out client-side. Whether the key
    actually WORKS is answered by the HTTP verdict and recorded as the
    `credentials_rejected` outcome.
    """
    return bool(_pagespeed_key())


def _key_shape_ok(key: str) -> bool:
    """Does the key look like a Google API key (39 chars, 'AIza' prefix)?

    ADVISORY ONLY - recorded on the outcome row so a 57-character paste of
    some other secret is obvious at a glance. Never used to skip the call.
    """
    return len(key) == 39 and key.startswith("AIza")


def _scrub(text: Any, key: str) -> str:
    """Redact the API key from any string before it is persisted or logged.

    httpx exception messages and Google error bodies can carry the request
    URL, and the key rides in that query string. One choke point so no
    caller has to remember. Also truncates to _ERROR_MAX.
    """
    out = str(text)
    if key and len(key) >= 8:
        out = out.replace(key, "***")
    return out[:_ERROR_MAX]


def _google_error_message(resp) -> str:
    """Pull Google's human error message out of a non-200 body.

    Google answers {"error": {"code": 400, "message": "API key not valid..."}}.
    Falls back to the raw body so an unexpected shape is still diagnosable.
    The CALLER scrubs.
    """
    try:
        err = (resp.json() or {}).get("error") or {}
        msg = err.get("message")
        if msg:
            return str(msg)
    except (ValueError, AttributeError, TypeError):
        pass
    return resp.text or f"HTTP {resp.status_code}"


async def _audit_url(url: str, api_key: str) -> Dict[str, Any]:
    """
    Call PageSpeed Insights for one URL. NEVER raises and NEVER returns None:
    the result is always a dict, so the caller can record what happened.

      success -> {"url", "ok": True,  "scores", "core_web_vitals", ...}
      failure -> {"url", "ok": False, "status": int|None, "error": str}

    `status` is the HTTP status when Google answered, None for a
    timeout/network/parse failure. That distinction is what separates
    "your key was rejected" from "the call never completed".
    """
    try:
        async with httpx.AsyncClient(timeout=AUDIT_TIMEOUT) as client:
            resp = await client.get(
                PAGESPEED_URL,
                params=[
                    ("url", url),
                    ("key", api_key),
                    # Request all 4 categories Lighthouse supports
                    ("category", "performance"),
                    ("category", "accessibility"),
                    ("category", "best-practices"),
                    ("category", "seo"),
                    ("strategy", "mobile"),  # mobile audit; ~80% of BV traffic
                ],
            )
        if resp.status_code != 200:
            detail = _scrub(_google_error_message(resp), api_key)
            logger.warning(
                "[PIXEL] PageSpeed %s for %s: %s", resp.status_code, url, detail
            )
            return {
                "url": url,
                "ok": False,
                "status": resp.status_code,
                "error": detail,
            }
        body = resp.json()
        lh = body.get("lighthouseResult") or {}
        cats = lh.get("categories") or {}
        audits = lh.get("audits") or {}

        # Extract category scores (0-1)
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
            "ok": True,
            "scores": scores,
            "core_web_vitals": cwv,
            "a11y_violations_count": len(a11y_violations),
            "a11y_violations_top3": sorted(a11y_violations, key=lambda v: -v["impact"])[:3],
            "fetch_time_ms": (lh.get("timing") or {}).get("total"),
        }
    except httpx.TimeoutException:
        logger.warning("[PIXEL] PageSpeed timeout on %s", url)
        return {"url": url, "ok": False, "status": None, "error": "timeout"}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        detail = _scrub(f"{type(e).__name__}: {e}", api_key)
        logger.warning("[PIXEL] PageSpeed error on %s: %s", url, detail)
        return {"url": url, "ok": False, "status": None, "error": detail}


# The owner-facing next step for each failure outcome. Lives next to the
# outcome names so a new outcome cannot ship without one.
NEXT_STEP = {
    "no_credentials": (
        "Add a Google PageSpeed API key under Settings -> Integrations -> "
        "Google PageSpeed, or set PAGESPEED_API_KEY on Railway."
    ),
    "credentials_rejected": (
        "Google rejected the PageSpeed API key. Create a fresh key at "
        "console.cloud.google.com (APIs & Services -> Credentials), enable "
        "the PageSpeed Insights API on that project, and re-save it under "
        "Settings -> Integrations -> Google PageSpeed."
    ),
    "all_calls_failed": (
        "PageSpeed did not answer for any route. Check that FRONTEND_BASE_URL "
        "is publicly reachable, then re-run the audit."
    ),
}


class PixelAgent(JarvisAgent):
    """UI/UX quality auditor - Lighthouse scores, a11y violations, regressions."""

    agent_id = "pixel"
    agent_name = "PIXEL"
    agent_type = AgentType.AUDITOR
    description = "UI/UX quality - performance, accessibility, visual regression on every Vercel deploy"
    version = "2.1.0"
    toggleable = True

    capabilities = [
        "performance_audit",
        "accessibility_audit",
        "core_web_vitals",
        "regression_detection",
        "deploy_event_handler",
    ]

    # ------------------------------------------------------------------
    # Outcome recording - the ONE door every tick leaves through
    # ------------------------------------------------------------------

    def _record(self, coll, outcome: str, **extra) -> None:
        """Persist exactly one row describing this tick. Fail-soft.

        Every exit path of _do_background_work() goes through here, so
        "PIXEL ran and wrote nothing" is no longer reachable. `extra` never
        carries a credential value - callers pass scrubbed strings only.
        """
        doc = {
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "agent_id": self.agent_id,
            "kind": "scheduled_audit" if outcome == "ok" else "run_failed",
            "outcome": outcome,
            "frontend_base_url": FRONTEND_BASE_URL,
        }
        if outcome != "ok":
            doc["next_step"] = NEXT_STEP.get(outcome, "")
        doc.update(extra)
        try:
            coll.insert_one(doc)
        except Exception as e:  # noqa: BLE001 - recording must never sink a tick
            logger.warning("[PIXEL] ui_audits write failed (%s): %s", outcome, e)

    async def _do_background_work(self):
        """
        Run a full audit cycle: each core route gets a PageSpeed run,
        results persist to ui_audits, regressions vs last week emit
        ui.regression_detected events for CORTEX.

        EVERY path through this method writes exactly one ui_audits row.
        """
        coll = self.get_collection("ui_audits")
        if coll is None:
            # The only silent exit left: there is nowhere to write. Loud in
            # the log, and the base class heartbeat still proves the tick ran.
            logger.error(
                "[PIXEL] ui_audits collection unavailable - tick cannot be recorded"
            )
            return

        api_key = _pagespeed_key()
        if not api_key:
            self._record(
                coll,
                "no_credentials",
                notes="No PageSpeed API key (Settings -> Integrations -> "
                      "Google PageSpeed, or PAGESPEED_API_KEY)",
                routes_attempted=[],
            )
            logger.warning("[PIXEL] No PageSpeed API key - recorded no_credentials")
            return

        key_shape_ok = _key_shape_ok(api_key)
        if not key_shape_ok:
            # Advisory only - we still make the call and let Google decide.
            logger.warning(
                "[PIXEL] PageSpeed key does not look like a Google API key "
                "(expected 39 chars starting AIza) - calling anyway"
            )

        ran_at = datetime.now(timezone.utc).isoformat()
        routes_attempted = [FRONTEND_BASE_URL.rstrip("/") + r for r in AUDIT_ROUTES]

        results = [await _audit_url(url, api_key) for url in routes_attempted]
        page_results: List[Dict[str, Any]] = [r for r in results if r.get("ok")]
        failures = [
            {"url": r["url"], "status": r.get("status"), "error": r.get("error")}
            for r in results
            if not r.get("ok")
        ]

        if not page_results:
            # THE BUG THIS FIXES: this branch used to `return` in silence.
            # A rejected key and a dead network look identical from the
            # outside unless we say which one it was.
            rejected = any(
                f["status"] in CREDENTIAL_REJECT_STATUSES for f in failures
            )
            outcome = "credentials_rejected" if rejected else "all_calls_failed"
            self._record(
                coll,
                outcome,
                routes_attempted=routes_attempted,
                failures=failures,
                key_shape_ok=key_shape_ok,
                notes=(failures[0]["error"] if failures else "no routes attempted"),
            )
            logger.error(
                "[PIXEL] %s - %d/%d routes failed, first error: %s",
                outcome, len(failures), len(routes_attempted),
                failures[0]["error"] if failures else "?",
            )
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

        self._record(
            coll,
            "ok",
            ran_at=ran_at,
            summary=summary,
            pages=page_results,
            regressions=regressions,
            # A partial run is still an "ok" audit, but the routes that did
            # NOT answer are recorded rather than quietly dropped.
            failures=failures,
            routes_attempted=routes_attempted,
            key_shape_ok=key_shape_ok,
        )

        # Emit events for regressions
        if regressions:
            await self._emit_regression_events(regressions)

        logger.info(
            "[PIXEL] Audit complete - %d pages (%d failed), min perf=%s, "
            "min a11y=%s, regressions=%d",
            len(page_results), len(failures), summary["overall_min_perf"],
            summary["overall_min_a11y"], len(regressions),
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
        """Vercel deploy.success -> run an immediate audit cycle."""
        if event == "deploy.success":
            logger.info(f"[PIXEL] Deploy detected ({payload.get('commit', '?')}) - running audit")
            await self._do_background_work()

    # ------------------------------------------------------------------
    # Health - read from Mongo, not from memory
    # ------------------------------------------------------------------

    def last_outcome(self) -> Optional[Dict[str, Any]]:
        """Most recent ui_audits row for PIXEL, whatever its outcome.

        Read from the DATABASE rather than in-process state: the scheduler
        that ticks PIXEL and the API worker that renders the Jarvis card can
        be different processes, so in-memory status is blind there.
        """
        coll = self.get_collection("ui_audits")
        if coll is None:
            return None
        try:
            return coll.find_one(
                {"agent_id": self.agent_id},
                {"_id": 0, "pages": 0},
                sort=[("ran_at", -1)],
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("[PIXEL] last_outcome lookup failed: %s", e)
            return None

    async def health_check(self) -> Dict[str, Any]:
        """DEGRADED whenever PIXEL is not actually producing audits.

        This is what turns a green-but-dead tile on the Jarvis grid into a
        visible problem: `health` + `last_error` are already rendered on the
        agent card, so a rejected key reads as
        "degraded - PageSpeed key rejected by Google: API key not valid".
        """
        health = await super().health_check()
        row = self.last_outcome()

        if row is None:
            health["health"] = HealthStatus.DEGRADED.value
            health["last_error"] = (
                "PIXEL has never recorded a UI audit. "
                + NEXT_STEP["no_credentials"]
            )
            health["last_outcome"] = None
            return health

        outcome = row.get("outcome")
        health["last_outcome"] = outcome
        health["last_outcome_at"] = row.get("ran_at")
        if outcome and outcome != "ok":
            health["health"] = HealthStatus.DEGRADED.value
            label = {
                "no_credentials": "No PageSpeed API key",
                "credentials_rejected": "PageSpeed key rejected by Google",
                "all_calls_failed": "All PageSpeed calls failed",
            }.get(outcome, outcome)
            detail = row.get("notes") or ""
            health["last_error"] = f"{label}: {detail}" if detail else label
        return health

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
        latest_outcome = (latest or {}).get("outcome")
        return AgentResponse(
            # A run that recorded a failure is a truthful read, not a broken
            # one -- but it must not report success.
            success=latest_outcome in (None, "ok"),
            agent_id=self.agent_id,
            data={
                "latest_audit": latest,
                "latest_outcome": latest_outcome,
                "recent_audit_count": len(recent),
                "pagespeed_ready": _is_pagespeed_available(),
                "frontend_url": FRONTEND_BASE_URL,
            },
            message=(
                f"PIXEL - {len(recent)} runs on record - "
                f"key_present={_is_pagespeed_available()} - "
                f"latest: {latest.get('ran_at') if latest else 'none'} "
                f"({latest_outcome or 'no run recorded'})"
            ),
        )
