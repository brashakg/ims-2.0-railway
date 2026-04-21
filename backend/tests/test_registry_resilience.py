"""
IMS 2.0 — Registry resilience + 8-agent roster (Phase 6.5)
=============================================================
Regression tests for two fixes that landed in Phase 6.5:

  1. initialize_registry() now registers all 8 canonical agents —
     JARVIS, CORTEX, SENTINEL, PIXEL, MEGAPHONE, ORACLE, TASKMASTER,
     NEXUS. Previously only 7 were wired; JARVIS was synthesized on
     the frontend, which meant if the frontend dropped the stub the
     grid was missing a card.

  2. Each agent registration is isolated in a try/except via
     _safe_register. Before Phase 6.5, a single agent's import-time
     failure (missing env var, broken dep) would abort the rest of the
     registration in a straight-line function, silently leaving
     Railway with fewer agents than a dev machine.

If either of these invariants breaks we want loud regressions rather
than quiet "wait why does prod only show 5 agents?" surprises.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test gets a fresh AGENT_REGISTRY so ordering doesn't leak."""
    from agents import registry as R
    R.AGENT_REGISTRY.clear()
    R._event_subscriptions.clear()
    yield
    R.AGENT_REGISTRY.clear()
    R._event_subscriptions.clear()


# ---------------------------------------------------------------------------
# 8-agent roster
# ---------------------------------------------------------------------------


def test_initialize_registry_registers_all_eight_canonical_agents():
    from agents.registry import initialize_registry, AGENT_REGISTRY

    initialize_registry(db=None)

    expected = {
        "jarvis", "cortex", "sentinel", "pixel",
        "megaphone", "oracle", "taskmaster", "nexus",
    }
    assert set(AGENT_REGISTRY.keys()) == expected, (
        f"Expected 8 canonical agents, got {sorted(AGENT_REGISTRY.keys())}"
    )


def test_jarvis_is_non_toggleable_foundation():
    """JARVIS + CORTEX are core, never shut off by the toggle UI."""
    from agents.registry import initialize_registry, AGENT_REGISTRY

    initialize_registry(db=None)

    jarvis = AGENT_REGISTRY["jarvis"]
    assert jarvis.toggleable is False
    assert jarvis.agent_type.value == "foundation"

    cortex = AGENT_REGISTRY["cortex"]
    assert cortex.toggleable is False


def test_jarvis_seeded_in_default_configs():
    """The config table has a row for JARVIS so the /jarvis/agents endpoint
    returns a filled schedule_type / hero / description for it."""
    from agents.config import DEFAULT_AGENT_CONFIGS

    ids = {c["agent_id"] for c in DEFAULT_AGENT_CONFIGS}
    assert "jarvis" in ids, "JARVIS must appear in DEFAULT_AGENT_CONFIGS"

    jarvis = next(c for c in DEFAULT_AGENT_CONFIGS if c["agent_id"] == "jarvis")
    assert jarvis["toggleable"] is False
    assert jarvis["schedule_type"] == "event"
    assert jarvis["enabled"] is True


# ---------------------------------------------------------------------------
# Resilience: one failing agent must not take down the others
# ---------------------------------------------------------------------------


def test_one_broken_agent_does_not_cascade(monkeypatch):
    """
    Simulate ORACLE failing at import time (e.g. the anthropic SDK is
    missing on a lean worker). The other 7 must still register.
    """
    from agents import registry as R

    original_import = R._import_class

    def poisoned(module_path, class_name):
        if class_name == "OracleAgent":
            raise ImportError("simulated: anthropic SDK missing")
        return original_import(module_path, class_name)

    monkeypatch.setattr(R, "_import_class", poisoned)

    R.initialize_registry(db=None)

    assert "oracle" not in R.AGENT_REGISTRY, "broken agent must be absent"
    # 7 others should still be present
    for aid in ("jarvis", "cortex", "sentinel", "pixel", "megaphone", "taskmaster", "nexus"):
        assert aid in R.AGENT_REGISTRY, f"Agent '{aid}' must survive ORACLE's failure"
    assert len(R.AGENT_REGISTRY) == 7


def test_multiple_broken_agents_do_not_cascade(monkeypatch):
    """Even if TWO agents break, the rest still register."""
    from agents import registry as R

    original_import = R._import_class

    def poisoned(module_path, class_name):
        if class_name in ("PixelAgent", "NexusAgent"):
            raise RuntimeError(f"simulated broken: {class_name}")
        return original_import(module_path, class_name)

    monkeypatch.setattr(R, "_import_class", poisoned)

    R.initialize_registry(db=None)

    assert "pixel" not in R.AGENT_REGISTRY
    assert "nexus" not in R.AGENT_REGISTRY
    assert len(R.AGENT_REGISTRY) == 6


def test_instantiation_failure_is_isolated(monkeypatch):
    """
    Distinct from import failure — an agent's __init__ might raise even
    when the module imports cleanly. That must also be isolated.
    """
    from agents import registry as R

    original_import = R._import_class

    class ExplodingCtor:
        def __init__(self, *_a, **_kw):
            raise ValueError("simulated: bad state in __init__")

    def poisoned(module_path, class_name):
        if class_name == "MegaphoneAgent":
            return ExplodingCtor
        return original_import(module_path, class_name)

    monkeypatch.setattr(R, "_import_class", poisoned)

    R.initialize_registry(db=None)

    assert "megaphone" not in R.AGENT_REGISTRY
    assert len(R.AGENT_REGISTRY) == 7
