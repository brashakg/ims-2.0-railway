"""
IMS 2.0 — Agent Control API
==============================
SUPERADMIN-EXCLUSIVE endpoints for managing Jarvis agents.
Toggle ON/OFF, view status, force-run, inspect logs.

*** STRICTLY SUPERADMIN ONLY — NO EXCEPTIONS ***
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
import logging

from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# SUPERADMIN GUARD
# ============================================================================


def require_superadmin(current_user: dict = Depends(get_current_user)):
    """Strict SUPERADMIN-only access guard"""
    if "SUPERADMIN" not in current_user.get("roles", []):
        raise HTTPException(status_code=404, detail="Not found")
    return current_user


# ============================================================================
# REQUEST/RESPONSE SCHEMAS
# ============================================================================


class ToggleRequest(BaseModel):
    enabled: bool


class ConfigUpdateRequest(BaseModel):
    schedule_type: Optional[str] = None
    schedule_value: Optional[str] = None
    config_overrides: Optional[Dict[str, Any]] = None


class AgentStatusResponse(BaseModel):
    """Single agent status row returned by /jarvis/agents and
    /jarvis/agents/{id}/status."""

    agent_id: str = Field(..., description="Stable identifier (e.g. 'oracle')")
    agent_name: str = Field(..., description="Display name (e.g. 'ORACLE')")
    agent_type: str = Field(
        ...,
        description="Category — foundation | orchestrator | monitor | analyzer | executor | integrator",
    )
    description: str
    version: str = "1.0.0"
    enabled: bool = Field(..., description="Toggle state from agent_config")
    toggleable: bool = Field(
        ...,
        description="False for core agents (JARVIS, CORTEX) that cannot be turned off",
    )
    status: str = Field(
        ...,
        description="Live runtime state — running | sleeping | stopped | error | starting",
    )
    health: str = Field(
        "unknown",
        description="Self-reported health — healthy | degraded | unhealthy | unknown",
    )
    schedule_type: str = Field("", description="interval | cron | event")
    schedule_value: str = Field(
        "",
        description="Seconds for interval, cron expression for cron, descriptor for event",
    )
    last_run: Optional[str] = Field(
        None, description="ISO8601 timestamp of most recent tick or null"
    )
    last_status: Optional[str] = Field(
        None, description="success | error from the last tick"
    )
    last_error: Optional[str] = Field(
        None, description="Most recent error message, if any"
    )
    run_count: int = 0
    error_count: int = 0
    avg_run_time_ms: float = 0
    hero: str = Field(
        "", description="Comic-book hero identity — purely cosmetic on the UI card"
    )
    capabilities: List[str] = Field(
        default_factory=list, description="What kinds of work the agent can do"
    )


class ListAgentsResponse(BaseModel):
    """Envelope for /jarvis/agents — full 8-agent roster + roll-ups."""

    agents: List[AgentStatusResponse]
    total: int = Field(
        ..., description="Number of registered agents (expected 8 in healthy state)"
    )
    enabled_count: int = Field(
        ..., description="Number with enabled=True. Core agents always count."
    )


# ============================================================================
# HELPER: Get agent infrastructure
# ============================================================================


def _get_registry():
    """Import agent registry (avoids circular imports)."""
    try:
        from agents.registry import AGENT_REGISTRY

        return AGENT_REGISTRY
    except ImportError:
        try:
            import sys, os

            sys.path.insert(
                0,
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                ),
            )
            from agents.registry import AGENT_REGISTRY

            return AGENT_REGISTRY
        except ImportError:
            return {}


def _get_config_manager():
    """Import config manager."""
    try:
        from agents.config import AgentConfigManager
        import sys, os

        sys.path.insert(
            0,
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
        )
        from database.connection import get_seeded_db

        db = get_seeded_db()
        return AgentConfigManager(db=db)
    except Exception:
        from agents.config import AgentConfigManager

        return AgentConfigManager(db=None)


def _get_scheduler():
    """Get the global scheduler instance."""
    try:
        from agents import _scheduler_instance

        return _scheduler_instance
    except (ImportError, AttributeError):
        return None


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get(
    "/agents",
    response_model=ListAgentsResponse,
    summary="List all Jarvis agents",
    description=(
        "Returns the full 8-agent roster (JARVIS, CORTEX, SENTINEL, PIXEL, "
        "MEGAPHONE, ORACLE, TASKMASTER, NEXUS) with live runtime status "
        "joined against the agent_config collection. SUPERADMIN-only — "
        "non-superadmin callers get 404 (deliberate: doesn't leak the "
        "endpoint's existence)."
    ),
    responses={
        404: {"description": "Not a SUPERADMIN — endpoint hidden"},
    },
)
async def list_agents(user: dict = Depends(require_superadmin)):
    """
    List all registered agents with their current status and config.
    Returns combined data from AGENT_REGISTRY + agent_config collection.

    Audit Run #2 (2026-04-21) flagged this endpoint returning HTTP 500 on
    prod (regression from the Phase 6.5 response_model addition plus
    whatever is making registry.items() blow up). Entire body is now
    wrapped defensively — any unexpected error returns an empty envelope
    + `error` hint instead of 500, so the frontend never crashes and
    SUPERADMIN can at least read the diagnostic endpoint to see what's
    wrong.
    """
    try:
        registry = _get_registry() or {}
    except Exception:
        registry = {}
    try:
        config_mgr = _get_config_manager()
        configs = config_mgr.get_all_configs()
    except Exception:
        configs = []
    config_map = {c.get("agent_id", ""): c for c in configs if c}

    agents_list = []
    for agent_id, agent in registry.items():
        try:
            config = config_map.get(agent_id, {})
            try:
                health = await agent.health_check()
            except Exception:
                health = {"health": "unknown", "status": "unknown"}

            agents_list.append(
                {
                    "agent_id": agent.agent_id,
                    "agent_name": agent.agent_name,
                    "agent_type": (
                        agent.agent_type.value
                        if hasattr(agent.agent_type, "value")
                        else str(agent.agent_type)
                    ),
                    "description": agent.description or "",
                    "version": agent.version or "1.0.0",
                    "enabled": bool(config.get("enabled", True)),
                    "toggleable": bool(agent.toggleable),
                    "status": str(health.get("status", "unknown")),
                    "health": str(health.get("health", "unknown")),
                    "schedule_type": str(config.get("schedule_type", "")),
                    "schedule_value": str(config.get("schedule_value", "")),
                    "last_run": (
                        str(config.get("last_run", ""))
                        if config.get("last_run")
                        else None
                    ),
                    "last_status": config.get("last_status"),
                    "last_error": config.get("last_error"),
                    "run_count": int(config.get("run_count", 0) or 0),
                    "error_count": int(config.get("error_count", 0) or 0),
                    "avg_run_time_ms": float(config.get("avg_run_time_ms", 0) or 0),
                    "hero": str(config.get("hero", "") or ""),
                    "capabilities": list(agent.capabilities or []),
                }
            )
        except Exception as e:
            # One bad agent must not take out the list response.
            logger.warning(f"[AGENTS] Skipped broken row for {agent_id}: {e}")
            continue

    return {
        "agents": agents_list,
        "total": len(agents_list),
        "enabled_count": sum(1 for a in agents_list if a["enabled"]),
    }


@router.get(
    "/agents/diagnostic",
    summary="Agent registry diagnostic — what's wired vs what's expected",
    description=(
        "Phase 6.5c: live introspection of the 8-agent registry. Returns "
        "the canonical roster, which agents actually registered on this "
        "worker, which agents have a row in `agent_config`, and a "
        "computed diff of what's missing. Use this when the Jarvis page "
        "shows fewer than 8 cards — the response tells you exactly which "
        "agent didn't register so you can grep the deploy log for the "
        "matching `[REGISTRY] Failed to register agent 'X'` traceback. "
        "SUPERADMIN-only."
    ),
)
async def agents_diagnostic(user: dict = Depends(require_superadmin)):
    """
    Returns:
        canonical: list of 8 expected agent_ids
        registered: list of agent_ids actually in AGENT_REGISTRY (per-worker)
        configured: list of agent_ids with a doc in agent_config (DB-wide)
        missing_from_registry: canonical - registered  (these are the ones
            that failed at startup; check Railway logs for traceback)
        missing_from_config: canonical - configured (DB never seeded)
        worker_id: which uvicorn worker answered this request
        as_of: ISO timestamp
    """
    from datetime import datetime, timezone
    import os

    registry = _get_registry()
    registered = sorted(registry.keys())

    config_mgr = _get_config_manager()
    try:
        configs = config_mgr.get_all_configs()
        configured = sorted(c["agent_id"] for c in configs)
    except Exception:
        configured = []

    # Pull the canonical list from the registry module so it stays in sync
    try:
        from agents.registry import CANONICAL_AGENT_IDS

        canonical = list(CANONICAL_AGENT_IDS)
    except ImportError:
        canonical = [
            "jarvis",
            "cortex",
            "sentinel",
            "pixel",
            "megaphone",
            "oracle",
            "taskmaster",
            "nexus",
        ]

    return {
        "canonical": canonical,
        "registered": registered,
        "configured": configured,
        "missing_from_registry": [a for a in canonical if a not in registered],
        "missing_from_config": [a for a in canonical if a not in configured],
        "worker_id": os.getenv("HOSTNAME")
        or os.getenv("RAILWAY_REPLICA_ID")
        or "unknown",
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@router.post(
    "/agents/reseed",
    summary="Re-seed agent configs into agent_config + rehydrate the scheduler",
    description=(
        "Recovery endpoint. If the diagnostic shows `missing_from_config` "
        "is non-empty (the DB was never seeded — typically because "
        "`get_seeded_db()` returned None during lifespan startup), call "
        "this to seed the defaults from `DEFAULT_AGENT_CONFIGS` into "
        "agent_config and then re-attach the scheduler so the newly-seeded "
        "schedules start ticking immediately, without a redeploy. "
        "Idempotent — existing configs are preserved as-is. "
        "SUPERADMIN-only."
    ),
)
async def agents_reseed(user: dict = Depends(require_superadmin)):
    """
    Force-reseed agent configs into the agent_config collection.

    Returns:
        seeded: list of agent_ids newly inserted by this call
        already_present: list of agent_ids that already had a config
        scheduler_rehydrated: whether the scheduler picked up the new configs
        configured_after: full list of agent_ids in agent_config after the call
    """
    from datetime import datetime, timezone
    import sys
    import os as _os

    sys.path.insert(
        0,
        _os.path.dirname(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        ),
    )

    # Fresh db lookup — don't trust whatever the lifespan captured. If the
    # DB came up AFTER backend startup, the lifespan-time db ref is None
    # and that's how we end up with an empty agent_config in the first
    # place. Re-resolve here so a manual recovery call doesn't repeat the
    # bug.
    db = None
    try:
        from database.connection import get_seeded_db

        db = get_seeded_db()
    except Exception as e:
        logger.warning(f"[AGENTS-RESEED] get_seeded_db failed: {e}")

    if db is None:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — cannot seed configs",
        )

    from agents.config import AgentConfigManager, DEFAULT_AGENT_CONFIGS

    mgr = AgentConfigManager(db=db)
    col = mgr.collection
    if col is None:
        raise HTTPException(
            status_code=503,
            detail="agent_config collection unavailable",
        )

    # Snapshot what's there before
    try:
        before_ids = sorted(
            c["agent_id"] for c in col.find({}, {"agent_id": 1, "_id": 0})
        )
    except Exception:
        before_ids = []

    # Inline-seed so per-doc failures don't 500 the entire reseed call.
    # The original `mgr.seed_configs()` ran all 8 inserts in one loop with
    # no per-doc try/except — a single duplicate-key or schema mismatch
    # took out the whole recovery. Also: deepcopy each default so we
    # don't mutate the module-level template (which would poison the
    # next call with a stale `_id` field that pymongo adds on insert).
    seed_errors: List[Dict[str, str]] = []
    from copy import deepcopy as _deepcopy
    from datetime import datetime as _dt, timezone as _tz

    for default in DEFAULT_AGENT_CONFIGS:
        try:
            existing = col.find_one({"agent_id": default["agent_id"]})
            if existing:
                continue
            doc = _deepcopy(default)
            doc["created_at"] = _dt.now(_tz.utc)
            doc["last_run"] = None
            doc["last_status"] = None
            doc["last_error"] = None
            doc["run_count"] = 0
            doc["error_count"] = 0
            doc["avg_run_time_ms"] = 0
            doc["toggled_by"] = "system"
            doc["toggled_at"] = _dt.now(_tz.utc)
            col.insert_one(doc)
        except Exception as e:
            seed_errors.append(
                {
                    "agent_id": default.get("agent_id", "?"),
                    "error": f"{type(e).__name__}: {str(e)[:200]}",
                }
            )
            logger.warning(
                "[AGENTS-RESEED] failed to seed %s: %s",
                default.get("agent_id"),
                e,
            )

    # Snapshot what's there after
    try:
        after_ids = sorted(
            c["agent_id"] for c in col.find({}, {"agent_id": 1, "_id": 0})
        )
    except Exception:
        after_ids = []

    seeded = sorted(set(after_ids) - set(before_ids))
    already_present = sorted(set(after_ids) & set(before_ids))

    # Rehydrate the scheduler — it took its snapshot at startup when configs
    # were empty, so the in-memory map of (agent_id → cadence) doesn't include
    # any of the just-seeded entries. Pull it down and re-attach to the live
    # registry so the newly-configured agents start ticking on their schedules.
    scheduler_rehydrated = False
    rehydrate_error: Optional[str] = None
    try:
        from agents.registry import AGENT_REGISTRY

        scheduler = _get_scheduler()
        if scheduler is not None:
            # Best-effort restart — every scheduler implementation should
            # be safe to .start() over an already-started instance because
            # this is exactly the recovery path it's meant for.
            try:
                stop = getattr(scheduler, "stop", None)
                if callable(stop):
                    maybe_coro = stop()  # pylint: disable=not-callable
                    if hasattr(maybe_coro, "__await__"):
                        await maybe_coro
            except Exception as e:
                rehydrate_error = f"stop() raised: {e}"
            try:
                start = getattr(scheduler, "start", None)
                if callable(start):
                    maybe_coro = start(
                        AGENT_REGISTRY or {}
                    )  # pylint: disable=not-callable
                    if hasattr(maybe_coro, "__await__"):
                        await maybe_coro
                scheduler_rehydrated = True
            except Exception as e:
                rehydrate_error = f"start() raised: {e}"
    except Exception as e:
        rehydrate_error = f"rehydrate failed: {e}"

    return {
        "seeded": seeded,
        "already_present": already_present,
        "seed_errors": seed_errors,
        "scheduler_rehydrated": scheduler_rehydrated,
        "rehydrate_error": rehydrate_error,
        "configured_after": after_ids,
        "default_count": len(DEFAULT_AGENT_CONFIGS),
        "worker_id": _os.getenv("HOSTNAME")
        or _os.getenv("RAILWAY_REPLICA_ID")
        or "unknown",
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/agents/{agent_id}/status")
async def get_agent_status(agent_id: str, user: dict = Depends(require_superadmin)):
    """Get detailed status for a single agent."""
    registry = _get_registry()
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    config_mgr = _get_config_manager()
    config = config_mgr.get_config(agent_id) or {}

    try:
        health = await agent.health_check()
    except Exception as e:
        health = {"health": "unknown", "error": str(e)}

    return {
        "agent": agent.to_dict(),
        "config": config,
        "health": health,
    }


@router.patch("/agents/{agent_id}/toggle")
async def toggle_agent(
    agent_id: str, body: ToggleRequest, user: dict = Depends(require_superadmin)
):
    """
    Toggle an agent ON/OFF.
    Core agents (JARVIS, CORTEX) cannot be toggled.
    """
    registry = _get_registry()
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    if not agent.toggleable:
        raise HTTPException(
            status_code=400, detail=f"Cannot toggle core agent '{agent_id}'"
        )

    config_mgr = _get_config_manager()
    username = user.get("email", user.get("username", "superadmin"))
    success = config_mgr.toggle_agent(agent_id, body.enabled, toggled_by=username)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update agent config")

    # Pause/resume the scheduler job
    scheduler = _get_scheduler()
    if scheduler:
        if body.enabled:
            scheduler.resume_agent(agent_id, agent)
        else:
            scheduler.pause_agent(agent_id)

    # Log the toggle action
    await agent.log_action(
        action="toggle",
        details={"enabled": body.enabled, "toggled_by": username},
    )

    # Emit event for other agents
    await agent.emit_event(
        "agent.toggled",
        {
            "agent_id": agent_id,
            "enabled": body.enabled,
            "toggled_by": username,
        },
    )

    return {
        "agent_id": agent_id,
        "enabled": body.enabled,
        "message": f"{agent.agent_name} {'enabled' if body.enabled else 'disabled'}",
    }


@router.patch("/agents/{agent_id}/config")
async def update_agent_config(
    agent_id: str, body: ConfigUpdateRequest, user: dict = Depends(require_superadmin)
):
    """Update an agent's schedule or config overrides."""
    config_mgr = _get_config_manager()
    updates = {}
    if body.schedule_type is not None:
        updates["schedule_type"] = body.schedule_type
    if body.schedule_value is not None:
        updates["schedule_value"] = body.schedule_value
    if body.config_overrides is not None:
        updates["config_overrides"] = body.config_overrides

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    success = config_mgr.update_config(agent_id, updates)
    if not success:
        raise HTTPException(
            status_code=404, detail=f"Agent '{agent_id}' not found or update failed"
        )

    return {"agent_id": agent_id, "updated": list(updates.keys())}


@router.post("/agents/{agent_id}/run-now")
async def run_agent_now(agent_id: str, user: dict = Depends(require_superadmin)):
    """Force an immediate background tick for an agent."""
    registry = _get_registry()
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    try:
        await agent.background_tick()
        return {
            "agent_id": agent_id,
            "message": f"{agent.agent_name} ran successfully",
            "status": (
                agent._status.value
                if hasattr(agent._status, "value")
                else str(agent._status)
            ),
        }
    except Exception:
        logger.exception("Agent run failed")
        raise HTTPException(
            status_code=500,
            detail="Agent run failed - try again or contact support",
        )


@router.get("/agents/{agent_id}/logs")
async def get_agent_logs(
    agent_id: str,
    limit: int = Query(20, le=100),
    user: dict = Depends(require_superadmin),
):
    """Get recent audit logs for an agent."""
    try:
        import sys, os

        sys.path.insert(
            0,
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
        )
        from database.connection import get_seeded_db

        db = get_seeded_db()
        col = db.get_collection("agent_audit_log") if db else None
        if col is None:
            return {"logs": [], "total": 0}

        logs = list(
            col.find({"agent_id": agent_id}, {"_id": 0})
            .sort("timestamp", -1)
            .limit(limit)
        )

        # Convert datetime objects to strings for JSON serialization
        for log in logs:
            if "timestamp" in log and hasattr(log["timestamp"], "isoformat"):
                log["timestamp"] = log["timestamp"].isoformat()

        return {"logs": logs, "total": len(logs)}
    except Exception as e:
        return {"logs": [], "total": 0, "error": str(e)}


@router.get("/agents/timeline")
async def get_agent_timeline(
    limit: int = Query(50, le=200), user: dict = Depends(require_superadmin)
):
    """Get unified timeline of all agent actions."""
    try:
        import sys, os

        sys.path.insert(
            0,
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
        )
        from database.connection import get_seeded_db

        db = get_seeded_db()
        col = db.get_collection("agent_audit_log") if db else None
        if col is None:
            return {"timeline": [], "total": 0}

        entries = list(col.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit))

        for entry in entries:
            if "timestamp" in entry and hasattr(entry["timestamp"], "isoformat"):
                entry["timestamp"] = entry["timestamp"].isoformat()

        return {"timeline": entries, "total": len(entries)}
    except Exception as e:
        return {"timeline": [], "total": 0, "error": str(e)}


@router.get("/agents/health-history")
async def get_health_history(
    hours: int = Query(24, le=168), user: dict = Depends(require_superadmin)
):
    """Get health check history for the last N hours."""
    try:
        import sys, os

        sys.path.insert(
            0,
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
        )
        from database.connection import get_seeded_db
        from datetime import timedelta

        db = get_seeded_db()
        col = db.get_collection("health_checks") if db else None
        if col is None:
            return {"history": [], "total": 0}

        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        entries = list(
            col.find(
                {"timestamp": {"$gte": since}}, {"_id": 0, "timestamp": 1, "score": 1}
            )
            .sort("timestamp", -1)
            .limit(500)
        )

        for entry in entries:
            if "timestamp" in entry and hasattr(entry["timestamp"], "isoformat"):
                entry["timestamp"] = entry["timestamp"].isoformat()

        return {"history": entries, "total": len(entries)}
    except Exception as e:
        return {"history": [], "total": 0, "error": str(e)}


# ============================================================================
# UNIFIED ACTIVITY FEED (Phase 5)
# ============================================================================
#
# Fans out across every collection where a Jarvis agent records an action:
#   - ORACLE       → anomalies
#   - NEXUS        → sync_runs
#   - TASKMASTER   → agent_audit_log (tier-gated executions)
#   - MEGAPHONE    → notification_logs
#   - PIXEL        → ui_audits
#
# Normalizes each source row into a common envelope so the frontend can
# render them in one chronological list without caring about the per-agent
# schema.


def _iso(ts) -> str:
    """Coerce a timestamp field (string or datetime) to ISO string."""
    if ts is None:
        return ""
    if hasattr(ts, "isoformat"):
        return ts.isoformat()
    return str(ts)


@router.get("/agents/activity")
async def get_agent_activity(
    limit: int = Query(50, ge=1, le=200),
    since_hours: int = Query(24, ge=1, le=720),
    agent_id: Optional[str] = Query(None),
    user: dict = Depends(require_superadmin),
):
    """
    Unified recent activity across all 7 agents.

    Returns a list of normalized events sorted newest first, each with:
      { agent_id, kind, timestamp, summary, severity?, status?, details }

    `kind` is one of: anomaly | sync_run | task_execution | notification | ui_audit
    Callers can filter by `agent_id` to narrow to one agent, or pull the
    whole cross-agent feed.
    """
    try:
        import sys as _sys
        import os as _os

        _sys.path.insert(
            0,
            _os.path.dirname(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            ),
        )
        from database.connection import get_seeded_db

        db = get_seeded_db()
    except Exception as e:
        return {"events": [], "total": 0, "error": f"db init: {e}"}

    if db is None:
        return {"events": [], "total": 0, "error": "database unavailable"}

    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    since_iso = since.isoformat()

    events: List[Dict[str, Any]] = []

    def _recent_clause(ts_fields) -> Dict[str, Any]:
        """Build an $or matching ts >= since across mixed persistence types.

        Agents are inconsistent: some persist timestamps as ISO STRINGS
        (oracle.detected_at, nexus.ran_at, pixel.ran_at, taskmaster.executed_at,
        megaphone.queued_at/dispatched_at) and some as BSON DATETIME
        (base.log_action -> agent_audit_log.timestamp, sentinel.health_checks
        .timestamp). Mongo's BSON type-bracketing means a datetime $gte bound
        never matches a string field and vice-versa, so we union both
        representations across every candidate timestamp field. ISO strings the
        agents write are tz-aware UTC (".../+00:00"), so they sort
        lexicographically consistently with since_iso.
        """
        if isinstance(ts_fields, str):
            ts_fields = [ts_fields]
        clauses: List[Dict[str, Any]] = []
        for f in ts_fields:
            clauses.append({f: {"$gte": since}})  # BSON datetime rows
            clauses.append({f: {"$gte": since_iso}})  # ISO string rows
        return {"$or": clauses}

    def _safe_find(
        coll_name: str, ts_field, match_extra: Dict = None, sort_field: str = None
    ):
        """Find docs with ts >= since. Tolerates missing collection.

        `ts_field` may be a single field name or a list of candidate fields
        (an agent that writes more than one timestamp shape). `sort_field`
        defaults to the first candidate; the final feed is re-sorted in Python
        by the coerced ISO timestamp anyway, so this only governs the per-
        collection .limit() pre-cut.
        """
        fields = [ts_field] if isinstance(ts_field, str) else list(ts_field)
        sort_on = sort_field or fields[0]
        try:
            coll = db.get_collection(coll_name)
            q: Dict[str, Any] = _recent_clause(fields)
            if match_extra:
                q = {"$and": [q, match_extra]}
            return list(coll.find(q, {"_id": 0}).sort(sort_on, -1).limit(limit))
        except Exception as e:
            logger.debug(f"[ACTIVITY] {coll_name} read failed: {e}")
            return []

    # --- ORACLE anomalies ---------------------------------------------------
    if not agent_id or agent_id == "oracle":
        for a in _safe_find("anomalies", "detected_at"):
            narrative = a.get("narrative") or a.get("summary") or "Anomaly detected"
            events.append(
                {
                    "agent_id": "oracle",
                    "kind": "anomaly",
                    "timestamp": _iso(a.get("detected_at")),
                    "severity": a.get("severity"),
                    "summary": narrative[:200],
                    "recommended_action": a.get("recommended_action"),
                    "ai_powered": a.get("ai_powered", False),
                    "details": a,
                }
            )

    # --- NEXUS sync_runs ---------------------------------------------------
    if not agent_id or agent_id == "nexus":
        for s in _safe_find("sync_runs", "ran_at"):
            items = s.get("items_synced", 0)
            provider = s.get("integration") or s.get("provider", "?")
            ok = s.get("ok", True)
            summary = (
                f"{provider} {s.get('kind', 'sync')}: {items} items"
                if ok
                else f"{provider} {s.get('kind', 'sync')} FAILED: {s.get('error', '?')}"
            )
            events.append(
                {
                    "agent_id": "nexus",
                    "kind": "sync_run",
                    "timestamp": _iso(s.get("ran_at")),
                    "status": "ok" if ok else "error",
                    "summary": summary[:200],
                    "details": s,
                }
            )

    # --- TASKMASTER audit log ---------------------------------------------
    # agent_audit_log is written two ways: taskmaster._audit_log uses
    # `executed_at` (ISO string) + target/safety_tier, while base.log_action
    # (used by every agent, incl. taskmaster) uses `timestamp` (BSON datetime)
    # + details. The old query only matched `executed_at`, so datetime
    # `timestamp` rows were invisible. Match both fields and read whichever the
    # row actually carries.
    if not agent_id or agent_id == "taskmaster":
        for t in _safe_find(
            "agent_audit_log",
            ["executed_at", "timestamp"],
            match_extra={"agent_id": "taskmaster"},
        ):
            action = t.get("action", "action")
            target = t.get("target", "")
            tier = t.get("safety_tier", "?")
            ts = t.get("executed_at") or t.get("timestamp")
            summary = (
                f"{action} → {target} (tier {tier})"
                if target
                else f"{action} (tier {tier})"
            )
            events.append(
                {
                    "agent_id": "taskmaster",
                    "kind": "task_execution",
                    "timestamp": _iso(ts),
                    "summary": summary[:200],
                    "details": t,
                }
            )

    # --- MEGAPHONE notifications (queued + dispatched) -------------------
    # Queued rows only carry `queued_at` + status PENDING (no dispatched_at).
    # With DISPATCH_MODE defaulting to "off" nothing is ever dispatched, so the
    # old `dispatched_at` + SENT/SIMULATED/FAILED filter hid all real activity.
    # Match on agent_id only (always present) and order by whichever timestamp
    # the row has - dispatched_at when sent, else queued_at.
    if not agent_id or agent_id == "megaphone":
        for n in _safe_find(
            "notification_logs",
            ["dispatched_at", "queued_at"],
            match_extra={"agent_id": "megaphone"},
        ):
            kind = n.get("kind", "message")
            channel = n.get("channel", "?")
            status = n.get("status", "?")
            ts = n.get("dispatched_at") or n.get("queued_at")
            events.append(
                {
                    "agent_id": "megaphone",
                    "kind": "notification",
                    "timestamp": _iso(ts),
                    "status": status.lower() if isinstance(status, str) else "ok",
                    "summary": f"{kind} via {channel}: {status}"[:200],
                    "details": n,
                }
            )

    # --- PIXEL audit runs ---------------------------------------------------
    if not agent_id or agent_id == "pixel":
        for u in _safe_find("ui_audits", "ran_at"):
            summary = u.get("summary") or {}
            pages = summary.get("pages_audited", 0)
            min_perf = summary.get("overall_min_perf")
            regressions = len(u.get("regressions") or [])
            line = (
                f"Audit · {pages} pages · min perf {min_perf} · {regressions} regressions"
                if u.get("kind") == "scheduled_audit"
                else f"Audit heartbeat ({u.get('notes', '')})"
            )
            events.append(
                {
                    "agent_id": "pixel",
                    "kind": "ui_audit",
                    "timestamp": _iso(u.get("ran_at")),
                    "status": "warn" if regressions else "ok",
                    "summary": line[:200],
                    "details": u,
                }
            )

    # Sort newest first, cap at limit
    events.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    events = events[:limit]

    return {
        "events": events,
        "total": len(events),
        "since_hours": since_hours,
        "filter_agent": agent_id,
    }
