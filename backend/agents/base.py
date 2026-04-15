"""
IMS 2.0 — JarvisAgent Base Class
==================================
Abstract base class that every Jarvis agent extends.
Provides: DB access, logging, health checks, background tick, config management.

Extends the existing BaseAgent pattern from core/subagents.py with:
  - Background scheduling support (background_tick)
  - ON/OFF toggle awareness
  - Health check protocol
  - Event emission
  - Audit logging with before/after state
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from enum import Enum
from dataclasses import dataclass, field
import logging
import traceback

logger = logging.getLogger(__name__)


# ============================================================================
# DATA MODELS
# ============================================================================


class AgentType(str, Enum):
    FOUNDATION = "foundation"
    ORCHESTRATOR = "orchestrator"
    MONITOR = "monitor"
    AUDITOR = "auditor"
    EXECUTOR = "executor"
    ANALYZER = "analyzer"
    INTEGRATOR = "integrator"


class AgentStatus(str, Enum):
    RUNNING = "running"
    SLEEPING = "sleeping"
    STOPPED = "stopped"
    ERROR = "error"
    STARTING = "starting"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class AgentResponse:
    """Standard response from any agent action."""
    success: bool
    agent_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    message: str = ""
    errors: List[str] = field(default_factory=list)
    execution_time_ms: float = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "agent_id": self.agent_id,
            "data": self.data,
            "message": self.message,
            "errors": self.errors,
            "execution_time_ms": self.execution_time_ms,
            "timestamp": self.timestamp,
        }


@dataclass
class AgentContext:
    """Context passed to agents during query dispatch."""
    user_id: str = ""
    store_id: Optional[str] = None
    query: str = ""
    query_type: Optional[str] = None
    parent_agent: Optional[str] = None
    chain_data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# JARVIS AGENT BASE CLASS
# ============================================================================


class JarvisAgent(ABC):
    """
    Abstract base class for all IMS 2.0 Jarvis agents.

    Every agent MUST define:
      - agent_id: unique string identifier
      - agent_name: human-readable name
      - agent_type: AgentType enum
      - description: what this agent does
      - _do_background_work(): the actual background loop logic

    Agents CAN override:
      - run(): on-demand query handler (called by CORTEX)
      - health_check(): self-diagnostic
      - on_event(): handle events from other agents
    """

    # --- Subclass must define these ---
    agent_id: str = "base"
    agent_name: str = "Base Agent"
    agent_type: AgentType = AgentType.MONITOR
    description: str = "Base agent"
    version: str = "1.0.0"

    # --- Toggleability ---
    toggleable: bool = True  # Can be turned ON/OFF (False for JARVIS, CORTEX)

    # --- Capabilities ---
    capabilities: List[str] = []
    requires_confirmation: List[str] = []  # Commands needing human approval

    def __init__(self, db=None):
        """
        Initialize agent with optional database connection.

        Args:
            db: PyMongo database instance (from get_seeded_db())
        """
        self._db = db
        self._status = AgentStatus.STOPPED
        self._last_error: Optional[str] = None
        self._run_count = 0
        self._error_count = 0
        self._last_run: Optional[datetime] = None

    # --- Database Access ---

    @property
    def db(self):
        """Get the database connection."""
        if self._db is None:
            try:
                import sys
                import os
                sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                from database.connection import get_seeded_db
                self._db = get_seeded_db()
            except Exception as e:
                logger.error(f"[{self.agent_id}] Failed to get DB: {e}")
        return self._db

    def get_collection(self, name: str):
        """Get a MongoDB collection by name."""
        if self.db:
            try:
                return self.db.get_collection(name)
            except Exception:
                # Try attribute access pattern
                return getattr(self.db, name, None)
        return None

    # --- Background Execution ---

    async def background_tick(self):
        """
        Called by APScheduler on the agent's schedule.
        Checks if agent is enabled before executing.
        DO NOT override this — override _do_background_work() instead.
        """
        import time
        start = time.time()

        # Check if agent is enabled
        config = await self.get_config()
        if config and not config.get("enabled", True):
            self._status = AgentStatus.STOPPED
            return  # Agent is toggled OFF — skip

        self._status = AgentStatus.RUNNING
        try:
            await self._do_background_work()
            elapsed = (time.time() - start) * 1000
            self._run_count += 1
            self._last_run = datetime.now(timezone.utc)
            self._last_error = None
            self._status = AgentStatus.SLEEPING

            # Update config with run stats
            await self._update_run_stats("success", elapsed)

        except Exception as e:
            elapsed = (time.time() - start) * 1000
            self._error_count += 1
            self._last_error = str(e)
            self._status = AgentStatus.ERROR
            logger.error(f"[{self.agent_id}] background_tick failed: {e}\n{traceback.format_exc()}")

            await self._update_run_stats("error", elapsed, error=str(e))

    @abstractmethod
    async def _do_background_work(self):
        """
        Implement the agent's background logic here.
        Called on schedule when the agent is enabled.
        """
        pass

    # --- On-Demand Query Handling ---

    async def run(self, query: str, context: AgentContext) -> AgentResponse:
        """
        Handle an on-demand query from CORTEX or direct API call.
        Override this for agents that respond to user queries.

        Default implementation returns a not-implemented response.
        """
        return AgentResponse(
            success=False,
            agent_id=self.agent_id,
            message=f"{self.agent_name} does not handle direct queries",
        )

    # --- Health Check ---

    async def health_check(self) -> Dict[str, Any]:
        """
        Self-diagnostic. SENTINEL polls this for every agent.
        Override to add agent-specific health checks.
        """
        return {
            "agent_id": self.agent_id,
            "status": self._status.value,
            "health": HealthStatus.HEALTHY.value if self._status != AgentStatus.ERROR else HealthStatus.UNHEALTHY.value,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "run_count": self._run_count,
            "error_count": self._error_count,
            "last_error": self._last_error,
            "version": self.version,
        }

    # --- Event Handling ---

    async def on_event(self, event: str, payload: Dict[str, Any]):
        """
        Handle an event from the event bus.
        Override to react to specific events.
        """
        pass

    async def emit_event(self, event: str, payload: Dict[str, Any]):
        """Publish event to the event bus (Redis pub/sub in production)."""
        # For now, use in-memory event dispatch
        # TODO: Replace with Redis pub/sub when Redis is configured
        from .registry import dispatch_event
        await dispatch_event(event, payload, source=self.agent_id)

    # --- Audit Logging ---

    async def log_action(self, action: str, details: Dict[str, Any],
                         before_state: Dict = None, after_state: Dict = None):
        """
        Log an action to the agent_audit_log collection.
        Every state change made by an agent should be logged here.
        """
        log_entry = {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "action": action,
            "details": details,
            "before_state": before_state,
            "after_state": after_state,
            "timestamp": datetime.now(timezone.utc),
        }
        try:
            col = self.get_collection("agent_audit_log")
            if col:
                col.insert_one(log_entry)
        except Exception as e:
            logger.error(f"[{self.agent_id}] Failed to log action: {e}")

    # --- Config Management ---

    async def get_config(self) -> Optional[Dict[str, Any]]:
        """Read this agent's config from agent_config collection."""
        try:
            col = self.get_collection("agent_config")
            if col:
                config = col.find_one({"agent_id": self.agent_id})
                return config
        except Exception as e:
            logger.warning(f"[{self.agent_id}] Failed to read config: {e}")
        return None

    async def _update_run_stats(self, status: str, elapsed_ms: float, error: str = None):
        """Update last_run, last_status, run_count in agent_config."""
        try:
            col = self.get_collection("agent_config")
            if col:
                update = {
                    "$set": {
                        "last_run": datetime.now(timezone.utc),
                        "last_status": status,
                        "last_error": error,
                        "avg_run_time_ms": elapsed_ms,
                    },
                    "$inc": {
                        "run_count": 1,
                        **({"error_count": 1} if status == "error" else {}),
                    },
                }
                col.update_one({"agent_id": self.agent_id}, update, upsert=True)
        except Exception as e:
            logger.warning(f"[{self.agent_id}] Failed to update run stats: {e}")

    # --- Serialization ---

    def to_dict(self) -> Dict[str, Any]:
        """Serialize agent metadata for API responses."""
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "agent_type": self.agent_type.value,
            "description": self.description,
            "version": self.version,
            "toggleable": self.toggleable,
            "capabilities": self.capabilities,
            "requires_confirmation": self.requires_confirmation,
            "status": self._status.value,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "run_count": self._run_count,
            "error_count": self._error_count,
            "last_error": self._last_error,
        }
