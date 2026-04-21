"""
IMS 2.0 — JARVIS: NLP & Conversation Core
===========================================
Hero Identity: Iron Man's J.A.R.V.I.S. (Marvel)
"Just A Rather Very Intelligent System."

JARVIS is the conversational / NLP-facing agent — what the user actually
talks to on the Jarvis page chat. The real conversational work happens
in `api/routers/jarvis.py` via the `/jarvis/query` endpoint; this file
exists so JARVIS appears in the 8-agent registry alongside the others,
gets a proper status/health card in the UI, and shares the uniform
event-bus + audit-log plumbing.

JARVIS is a CORE agent — always-on, not toggleable, event-driven only.
No background tick (the NLP endpoint is called per-user-query).
"""

from typing import Dict, Any
import logging

from ..base import JarvisAgent, AgentType, AgentContext, AgentResponse, HealthStatus

logger = logging.getLogger(__name__)


class JarvisCore(JarvisAgent):
    """NLP & conversation core — the voice of the system."""

    agent_id = "jarvis"
    agent_name = "JARVIS"
    agent_type = AgentType.FOUNDATION
    description = "NLP & conversation core — the voice users actually talk to"
    version = "1.0.0"
    toggleable = False   # core agent, always on

    capabilities = [
        "natural_language_query",
        "claude_bridge",
        "intent_classification_passthrough",
        "conversation_memory",
    ]

    async def _do_background_work(self):
        """
        JARVIS is event-driven (per-user-query via /jarvis/query). There's
        no scheduled tick. The method exists to satisfy the abstract base
        class contract; the scheduler config for JARVIS uses
        schedule_type='event' so this is never actually invoked.
        """
        return

    async def run(self, query: str, context: AgentContext) -> AgentResponse:
        """
        Lightweight echo for direct CORTEX-to-JARVIS calls. Real NLP lives
        in the /jarvis/query endpoint which talks to Claude directly; this
        path is only exercised if CORTEX explicitly routes an analytical
        question that it thinks JARVIS should answer first (rare).
        """
        return AgentResponse(
            success=True,
            agent_id=self.agent_id,
            data={"echo": query},
            message="JARVIS received query — real NLP flows via /jarvis/query",
        )

    async def health_check(self) -> Dict[str, Any]:
        """JARVIS is healthy as long as the process is up."""
        return {
            "agent_id": self.agent_id,
            "status": "running",
            "health": HealthStatus.HEALTHY.value,
            "last_run": None,
            "run_count": self._run_count,
            "error_count": self._error_count,
            "last_error": None,
            "version": self.version,
        }
