"""
IMS 2.0 — Jarvis Agent System
================================
Always-on background agents with per-agent ON/OFF toggle control.
Extends the existing Jarvis AI framework.

Architecture:
  - JarvisAgent: Abstract base class all agents extend
  - AgentScheduler: APScheduler-based daemon that runs agents on schedule
  - AgentConfigManager: MongoDB-backed config with ON/OFF toggle
  - EventBus: Redis pub/sub for inter-agent communication (future)

Agents:
  - JARVIS: Foundation (NLP, Claude, conversation memory) — always on
  - CORTEX: Orchestrator (intent classification, multi-agent dispatch) — always on
  - SENTINEL: Health & monitoring — toggleable
  - PIXEL: UI/UX quality auditing — toggleable
  - MEGAPHONE: Marketing & customer engagement — toggleable
  - ORACLE: AI analysis & business intelligence — toggleable
  - TASKMASTER: Task execution & operations — toggleable
  - NEXUS: Data & integration orchestration — toggleable
"""

from .base import JarvisAgent, AgentResponse, AgentContext, HealthStatus
from .config import AgentConfigManager
from .scheduler import AgentScheduler
from .registry import AGENT_REGISTRY, register_agent, get_agent

__all__ = [
    "JarvisAgent",
    "AgentResponse",
    "AgentContext",
    "HealthStatus",
    "AgentConfigManager",
    "AgentScheduler",
    "AGENT_REGISTRY",
    "register_agent",
    "get_agent",
]
