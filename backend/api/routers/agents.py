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
from datetime import datetime, timezone
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
    agent_id: str
    agent_name: str
    agent_type: str
    description: str
    enabled: bool
    toggleable: bool
    status: str
    health: str = "unknown"
    schedule_type: str = ""
    schedule_value: str = ""
    last_run: Optional[str] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    run_count: int = 0
    error_count: int = 0
    avg_run_time_ms: float = 0
    hero: str = ""


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


@router.get("/agents")
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
