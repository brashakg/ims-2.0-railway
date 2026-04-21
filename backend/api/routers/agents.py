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
    agent_type: str = Field(..., description="Category — foundation | orchestrator | monitor | analyzer | executor | integrator")
    description: str
    version: str = "1.0.0"
    enabled: bool = Field(..., description="Toggle state from agent_config")
    toggleable: bool = Field(..., description="False for core agents (JARVIS, CORTEX) that cannot be turned off")
    status: str = Field(..., description="Live runtime state — running | sleeping | stopped | error | starting")
    health: str = Field("unknown", description="Self-reported health — healthy | degraded | unhealthy | unknown")
    schedule_type: str = Field("", description="interval | cron | event")
    schedule_value: str = Field("", description="Seconds for interval, cron expression for cron, descriptor for event")
    last_run: Optional[str] = Field(None, description="ISO8601 timestamp of most recent tick or null")
    last_status: Optional[str] = Field(None, description="success | error from the last tick")
    last_error: Optional[str] = Field(None, description="Most recent error message, if any")
    run_count: int = 0
    error_count: int = 0
    avg_run_time_ms: float = 0
    hero: str = Field("", description="Comic-book hero identity — purely cosmetic on the UI card")
    capabilities: List[str] = Field(default_factory=list, description="What kinds of work the agent can do")


class ListAgentsResponse(BaseModel):
    """Envelope for /jarvis/agents — full 8-agent roster + roll-ups."""
    agents: List[AgentStatusResponse]
    total: int = Field(..., description="Number of registered agents (expected 8 in healthy state)")
    enabled_count: int = Field(..., description="Number with enabled=True. Core agents always count.")


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
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            from agents.registry import AGENT_REGISTRY
            return AGENT_REGISTRY
        except ImportError:
            return {}

def _get_config_manager():
    """Import config manager."""
    try:
        from agents.config import AgentConfigManager
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
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
    """
    registry = _get_registry()
    config_mgr = _get_config_manager()
    configs = config_mgr.get_all_configs()
    config_map = {c["agent_id"]: c for c in configs}

    agents_list = []
    for agent_id, agent in registry.items():
        config = config_map.get(agent_id, {})
        try:
            health = await agent.health_check()
        except Exception:
            health = {"health": "unknown", "status": "unknown"}

        agents_list.append({
            "agent_id": agent.agent_id,
            "agent_name": agent.agent_name,
            "agent_type": agent.agent_type.value if hasattr(agent.agent_type, 'value') else str(agent.agent_type),
            "description": agent.description,
            "version": agent.version,
            "enabled": config.get("enabled", True),
            "toggleable": agent.toggleable,
            "status": health.get("status", "unknown"),
            "health": health.get("health", "unknown"),
            "schedule_type": config.get("schedule_type", ""),
            "schedule_value": config.get("schedule_value", ""),
            "last_run": str(config.get("last_run", "")) if config.get("last_run") else None,
            "last_status": config.get("last_status"),
            "last_error": config.get("last_error"),
            "run_count": config.get("run_count", 0),
            "error_count": config.get("error_count", 0),
            "avg_run_time_ms": config.get("avg_run_time_ms", 0),
            "hero": config.get("hero", ""),
            "capabilities": agent.capabilities,
        })

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
        canonical = ["jarvis", "cortex", "sentinel", "pixel",
                     "megaphone", "oracle", "taskmaster", "nexus"]

    return {
        "canonical": canonical,
        "registered": registered,
        "configured": configured,
        "missing_from_registry": [a for a in canonical if a not in registered],
        "missing_from_config": [a for a in canonical if a not in configured],
        "worker_id": os.getenv("HOSTNAME") or os.getenv("RAILWAY_REPLICA_ID") or "unknown",
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
async def toggle_agent(agent_id: str, body: ToggleRequest, user: dict = Depends(require_superadmin)):
    """
    Toggle an agent ON/OFF.
    Core agents (JARVIS, CORTEX) cannot be toggled.
    """
    registry = _get_registry()
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    if not agent.toggleable:
        raise HTTPException(status_code=400, detail=f"Cannot toggle core agent '{agent_id}'")

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
    await agent.emit_event("agent.toggled", {
        "agent_id": agent_id,
        "enabled": body.enabled,
        "toggled_by": username,
    })

    return {
        "agent_id": agent_id,
        "enabled": body.enabled,
        "message": f"{agent.agent_name} {'enabled' if body.enabled else 'disabled'}",
    }


@router.patch("/agents/{agent_id}/config")
async def update_agent_config(agent_id: str, body: ConfigUpdateRequest,
                               user: dict = Depends(require_superadmin)):
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
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found or update failed")

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
            "status": agent._status.value if hasattr(agent._status, 'value') else str(agent._status),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent run failed: {str(e)}")


@router.get("/agents/{agent_id}/logs")
async def get_agent_logs(agent_id: str, limit: int = Query(20, le=100),
                          user: dict = Depends(require_superadmin)):
    """Get recent audit logs for an agent."""
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from database.connection import get_seeded_db
        db = get_seeded_db()
        col = db.get_collection("agent_audit_log") if db else None
        if not col:
            return {"logs": [], "total": 0}

        logs = list(col.find(
            {"agent_id": agent_id},
            {"_id": 0}
        ).sort("timestamp", -1).limit(limit))

        # Convert datetime objects to strings for JSON serialization
        for log in logs:
            if "timestamp" in log and hasattr(log["timestamp"], "isoformat"):
                log["timestamp"] = log["timestamp"].isoformat()

        return {"logs": logs, "total": len(logs)}
    except Exception as e:
        return {"logs": [], "total": 0, "error": str(e)}


@router.get("/agents/timeline")
async def get_agent_timeline(limit: int = Query(50, le=200),
                              user: dict = Depends(require_superadmin)):
    """Get unified timeline of all agent actions."""
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from database.connection import get_seeded_db
        db = get_seeded_db()
        col = db.get_collection("agent_audit_log") if db else None
        if not col:
            return {"timeline": [], "total": 0}

        entries = list(col.find(
            {},
            {"_id": 0}
        ).sort("timestamp", -1).limit(limit))

        for entry in entries:
            if "timestamp" in entry and hasattr(entry["timestamp"], "isoformat"):
                entry["timestamp"] = entry["timestamp"].isoformat()

        return {"timeline": entries, "total": len(entries)}
    except Exception as e:
        return {"timeline": [], "total": 0, "error": str(e)}


@router.get("/agents/health-history")
async def get_health_history(hours: int = Query(24, le=168),
                              user: dict = Depends(require_superadmin)):
    """Get health check history for the last N hours."""
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from database.connection import get_seeded_db
        from datetime import timedelta
        db = get_seeded_db()
        col = db.get_collection("health_checks") if db else None
        if not col:
            return {"history": [], "total": 0}

        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        entries = list(col.find(
            {"timestamp": {"$gte": since}},
            {"_id": 0, "timestamp": 1, "score": 1}
        ).sort("timestamp", -1).limit(500))

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
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
        from database.connection import get_seeded_db
        db = get_seeded_db()
    except Exception as e:
        return {"events": [], "total": 0, "error": f"db init: {e}"}

    if db is None:
        return {"events": [], "total": 0, "error": "database unavailable"}

    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    since_iso = since.isoformat()

    events: List[Dict[str, Any]] = []

    def _safe_find(coll_name: str, ts_field: str, match_extra: Dict = None):
        """Find docs with ts >= since. Tolerates missing collection."""
        try:
            coll = db.get_collection(coll_name)
            q: Dict[str, Any] = {ts_field: {"$gte": since_iso}}
            if match_extra:
                q.update(match_extra)
            return list(coll.find(q, {"_id": 0}).sort(ts_field, -1).limit(limit))
        except Exception as e:
            logger.debug(f"[ACTIVITY] {coll_name} read failed: {e}")
            return []

    # --- ORACLE anomalies ---------------------------------------------------
    if not agent_id or agent_id == "oracle":
        for a in _safe_find("anomalies", "detected_at"):
            narrative = a.get("narrative") or a.get("summary") or "Anomaly detected"
            events.append({
                "agent_id": "oracle",
                "kind": "anomaly",
                "timestamp": _iso(a.get("detected_at")),
                "severity": a.get("severity"),
                "summary": narrative[:200],
                "recommended_action": a.get("recommended_action"),
                "ai_powered": a.get("ai_powered", False),
                "details": a,
            })

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
            events.append({
                "agent_id": "nexus",
                "kind": "sync_run",
                "timestamp": _iso(s.get("ran_at")),
                "status": "ok" if ok else "error",
                "summary": summary[:200],
                "details": s,
            })

    # --- TASKMASTER audit log ---------------------------------------------
    if not agent_id or agent_id == "taskmaster":
        for t in _safe_find("agent_audit_log", "executed_at",
                            match_extra={"agent_id": "taskmaster"}):
            action = t.get("action", "action")
            target = t.get("target", "")
            tier = t.get("safety_tier", "?")
            events.append({
                "agent_id": "taskmaster",
                "kind": "task_execution",
                "timestamp": _iso(t.get("executed_at")),
                "summary": f"{action} → {target} (tier {tier})"[:200],
                "details": t,
            })

    # --- MEGAPHONE notifications (dispatched only, not queued) ------------
    if not agent_id or agent_id == "megaphone":
        for n in _safe_find("notification_logs", "dispatched_at",
                            match_extra={"agent_id": "megaphone",
                                         "status": {"$in": ["SENT", "SIMULATED", "FAILED"]}}):
            kind = n.get("kind", "message")
            channel = n.get("channel", "?")
            status = n.get("status", "?")
            events.append({
                "agent_id": "megaphone",
                "kind": "notification",
                "timestamp": _iso(n.get("dispatched_at")),
                "status": status.lower() if isinstance(status, str) else "ok",
                "summary": f"{kind} via {channel}: {status}"[:200],
                "details": n,
            })

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
            events.append({
                "agent_id": "pixel",
                "kind": "ui_audit",
                "timestamp": _iso(u.get("ran_at")),
                "status": "warn" if regressions else "ok",
                "summary": line[:200],
                "details": u,
            })

    # Sort newest first, cap at limit
    events.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    events = events[:limit]

    return {
        "events": events,
        "total": len(events),
        "since_hours": since_hours,
        "filter_agent": agent_id,
    }
