"""
IMS 2.0 — JARVIS data scope tests
==================================
Locks in the expanded data-access contract:

  1. `scrub_pii(level="customer")` strips customer/patient PII but lets
     owner-data (own staff, vendor names, store GSTIN) flow through.
  2. `scrub_pii(level="all")` still strips everything (the safe default
     for outbound flows).
  3. `JarvisAnalyticsEngine.get_staff_insights()` returns a roster list,
     not just a count, so JARVIS can refer to staff by name.
  4. `get_extended_context()` is fail-soft — missing collections yield
     `{}` / `[]` rather than raising.
"""

from __future__ import annotations

import os
import sys

# Must be set BEFORE auth.py is imported by jarvis.py — that module
# raises at import time if JWT_SECRET_KEY is unset.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ----- scrub_pii ----------------------------------------------------------

def test_scrub_customer_strips_customer_keys_only():
    from agents.llm_provider import scrub_pii

    payload = {
        "customer_name": "Anita Sharma",
        "customer_phone": "9876543210",
        "patient_email": "anita@example.com",
        "billing_address": "12 MG Road, Pune",
        "staff": [
            {"name": "Ravi Kumar", "phone": "9000011111", "role": "Manager"},
            {"name": "Priya Singh", "phone": "9000022222", "role": "Optometrist"},
        ],
        "vendor": {"name": "Luxottica India Pvt Ltd", "gstin": "27AAACL1234A1Z5"},
    }
    out = scrub_pii(payload, level="customer")

    # Customer fields scrubbed
    assert out["customer_name"] == "[redacted]"
    assert out["customer_phone"] == "[redacted]"
    assert out["patient_email"] == "[redacted]"
    assert out["billing_address"] == "[redacted]"

    # Staff names + phones preserved (owner data)
    assert out["staff"][0]["name"] == "Ravi Kumar"
    assert out["staff"][0]["phone"] == "9000011111"
    assert out["staff"][1]["name"] == "Priya Singh"

    # Vendor name + GSTIN preserved (owner data)
    assert out["vendor"]["name"] == "Luxottica India Pvt Ltd"
    assert out["vendor"]["gstin"] == "27AAACL1234A1Z5"


def test_scrub_all_still_strips_everything():
    from agents.llm_provider import scrub_pii

    payload = {
        "staff": [{"name": "Ravi Kumar", "phone": "9000011111"}],
        "customer_name": "Anita",
        "vendor": {"name": "Luxottica", "gstin": "27AAACL1234A1Z5"},
        "free_text": "Call me at 9876543210 or anita@example.com",
    }
    out = scrub_pii(payload, level="all")

    assert out["staff"][0]["name"] == "[redacted]"
    assert out["staff"][0]["phone"] == "[redacted]"
    assert out["customer_name"] == "[redacted]"
    assert out["vendor"]["name"] == "[redacted]"
    assert out["vendor"]["gstin"] == "[redacted]"
    assert "[phone]" in out["free_text"]
    assert "[email]" in out["free_text"]


def test_scrub_none_passes_through():
    from agents.llm_provider import scrub_pii

    payload = {"customer_name": "Anita", "phone": "9876543210"}
    out = scrub_pii(payload, level="none")
    assert out == payload


def test_scrub_back_compat_default_is_all():
    """Legacy callers passing no `level` (or `scrub=True` via complete())
    must still get the safe-default behaviour — strip everything."""
    from agents.llm_provider import scrub_pii

    payload = {"name": "Ravi", "customer_name": "Anita"}
    out = scrub_pii(payload)  # default level="all"
    assert out["name"] == "[redacted]"
    assert out["customer_name"] == "[redacted]"


# ----- extended context fail-soft -----------------------------------------

def test_extended_context_no_db_returns_empty_dict():
    """When the DB is absent (every get_db_collection returns None) the
    method must still return a dict — fail-soft contract."""
    from api.routers import jarvis as jarvis_router

    # Force every collection lookup to return None (simulates no-DB)
    original = jarvis_router.get_db_collection
    jarvis_router.get_db_collection = lambda _name: None
    try:
        ctx = jarvis_router.JarvisAnalyticsEngine.get_extended_context()
    finally:
        jarvis_router.get_db_collection = original

    assert isinstance(ctx, dict)
    # No collection available → no keys populated, but it must not raise.


def test_staff_insights_shape_no_db():
    """get_staff_insights() must always return the documented shape even
    when the users collection is absent."""
    from api.routers import jarvis as jarvis_router

    original = jarvis_router.get_db_collection
    jarvis_router.get_db_collection = lambda _name: None
    try:
        out = jarvis_router.JarvisAnalyticsEngine.get_staff_insights()
    finally:
        jarvis_router.get_db_collection = original

    assert "roster" in out
    assert isinstance(out["roster"], list)
    assert "attendance_summary" in out
    assert out["attendance_summary"]["total_staff"] == 0


# ----- wide-lens data endpoint --------------------------------------------

def test_jarvis_data_allow_list_includes_owner_collections():
    """The /jarvis/data/{collection} allow-list must include the
    high-value owner collections (expenses, payroll, audit_logs etc.)
    even if no caller is currently reading them — the contract is the
    set of collections JARVIS is permitted to inspect."""
    from api.routers.jarvis import _JARVIS_QUERYABLE_COLLECTIONS

    must_have = {
        # Operational
        "orders", "products", "stock", "purchase_orders", "tasks",
        "workshop_jobs", "prescriptions", "eye_tests",
        # Finance
        "expenses", "budgets", "salary_records", "payroll",
        "incentives", "incentive_settings", "payout_snapshots",
        "targets", "period_locks",
        # Loyalty
        "loyalty_accounts",
        # Agent infra
        "agent_config", "agent_events", "anomalies", "ui_audits",
        "audit_logs", "health_checks",
        # Integrations
        "integrations", "sync_runs", "webhook_inbox",
    }
    missing = must_have - _JARVIS_QUERYABLE_COLLECTIONS
    assert not missing, f"Missing from allow-list: {missing}"


def test_jarvis_data_customer_pii_collections_marked():
    """Customer-PII collections must be on the customer-scrub list so
    the ad-hoc endpoint redacts before returning."""
    from api.routers.jarvis import _CUSTOMER_PII_COLLECTIONS

    assert "customers" in _CUSTOMER_PII_COLLECTIONS
    assert "prescriptions" in _CUSTOMER_PII_COLLECTIONS


def test_coerce_mongo_value_parses_json():
    from api.routers.jarvis import _coerce_mongo_value

    assert _coerce_mongo_value('{"status":"OPEN"}') == {"status": "OPEN"}
    assert _coerce_mongo_value("OPEN") == "OPEN"
    assert _coerce_mongo_value("") == ""
    assert _coerce_mongo_value("42") == 42  # ints parse fine via json
    assert _coerce_mongo_value("not-json-just-string") == "not-json-just-string"


# ----- PR #127 regression: prompt-template format bug --------------------

def test_model_budgets_per_tier():
    """Local Ollama gets a SMALL budget that fits the 4096-token Ollama
    context window. Claude/Opus get the full lens. Regression-locks the
    fix for the Ollama timeout/truncate problem."""
    from agents.llm_provider import model_budgets

    # The function reads tier from the live registry. To avoid depending
    # on env state, we patch it with a known tier→model mapping by
    # monkeypatching _registry.
    import agents.llm_provider as lp

    original = lp._registry  # pylint: disable=protected-access
    lp._registry = lambda: {  # pylint: disable=protected-access
        "local": {"tier": "free"},
        "claude": {"tier": "standard"},
        "claude-opus": {"tier": "premium"},
    }
    try:
        local = model_budgets("local")
        std = model_budgets("claude")
        prem = model_budgets("claude-opus")
        # Local must be small enough to fit Ollama's 4096-token window
        assert local["max_tokens"] <= 1024, "local max_tokens too high for 3B model"
        assert local["context_budget"] <= 4000, "local context too large for 4096-token window"
        # Standard / premium are progressively larger
        assert std["max_tokens"] >= local["max_tokens"] * 2
        assert prem["max_tokens"] >= std["max_tokens"]
        assert std["context_budget"] >= local["context_budget"] * 3
        assert prem["context_budget"] >= std["context_budget"]
    finally:
        lp._registry = original  # pylint: disable=protected-access


def test_local_system_prompt_is_short_enough_for_ollama_4k_window():
    """The LOCAL system prompt must be compact — full prompt is ~1200
    tokens which eats 30% of a 4096-token Ollama window. Compact one
    should be under 300 tokens (~1200 chars)."""
    from api.routers.jarvis import ClaudeClient

    local_prompt = ClaudeClient.JARVIS_LOCAL_SYSTEM_PROMPT
    # ~4 chars per token rough estimate
    approx_tokens = len(local_prompt) // 4
    assert approx_tokens < 300, (
        f"LOCAL prompt is ~{approx_tokens} tokens — must stay under 300 "
        "so the 4096-token Ollama window has room for business data + query"
    )
    # And it must still contain the persona + scrub contract markers
    assert "JARVIS" in local_prompt
    assert "Sir" in local_prompt
    assert "{current_datetime}" in local_prompt  # placeholder still there for .replace()


def test_system_prompt_renders_with_literal_curly_braces():
    """REGRESSION: PR #127 left literal `{collection}` in the JARVIS
    system prompt as documentation of `/jarvis/data/{collection}`. The
    `.format(current_datetime=...)` call then raised KeyError because
    it tried to look up a `collection` keyword argument. Every JARVIS
    query 500'd in prod for a few hours.

    Fixed by switching to `.replace("{current_datetime}", ...)` so only
    the explicit token gets replaced. This test pins that contract — if
    anyone goes back to .format() (or adds new format placeholders to
    the prompt), the test catches it.
    """
    from api.routers import jarvis as jarvis_router

    # Render the prompt the way the code does
    prompt = jarvis_router.ClaudeClient.JARVIS_SYSTEM_PROMPT.replace(
        "{current_datetime}", "2026-05-21 10:00:00 IST"
    )
    # The literal `{collection}` must survive untouched (it's load-bearing
    # documentation for the LLM about the ad-hoc reader endpoint)
    assert "{collection}" in prompt
    # The datetime placeholder must be substituted
    assert "{current_datetime}" not in prompt
    assert "2026-05-21" in prompt


# ----- PIXEL audit history endpoint contract -----------------------------

def test_extended_context_includes_ui_audit_keys_when_data_present():
    """If ui_audits has at least one PIXEL doc, the extended context
    surfaces ui_audit_latest + ui_audits_total."""
    from api.routers import jarvis as jarvis_router

    class _FakeUiAudits:
        def find_one(self, _flt, **_kw):
            return {
                "ran_at": "2026-05-21T02:00:00+00:00",
                "summary": {"overall_min_perf": 0.84, "overall_min_a11y": 0.92,
                            "total_a11y_violations": 3, "pages_audited": 9},
                "regressions": [{"url": "/pos", "metric": "performance"}],
            }
        def count_documents(self, _flt):
            return 42

    original = jarvis_router.get_db_collection
    jarvis_router.get_db_collection = lambda name: _FakeUiAudits() if name == "ui_audits" else None
    try:
        ctx = jarvis_router.JarvisAnalyticsEngine.get_extended_context()
    finally:
        jarvis_router.get_db_collection = original

    assert "ui_audit_latest" in ctx
    assert ctx["ui_audit_latest"]["summary"]["overall_min_perf"] == 0.84
    assert ctx["ui_audit_latest"]["regressions_count"] == 1
    assert ctx["ui_audits_total"] == 42
