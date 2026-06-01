"""
IMS 2.0 - Catalog Autopilot AI-enrichment adapter tests
=======================================================
Covers the AIEnrichAdapter (the reliable, scraping-free, creds-free contributor
that calls Claude for catalog enrichment TEXT). The shared agents Claude client
is MOCKED end-to-end, so NO real network call and NO ANTHROPIC_API_KEY is needed.

What we assert:
  - is_enabled() is gated on ANTHROPIC_API_KEY (false when unset).
  - With the key set + the client mocked, _search returns ONE well-formed
    candidate (description + specs + category present, image_urls EMPTY,
    source_class AUTHORIZED).
  - Fully fail-soft: a client error -> [], non-JSON / None output -> [].
  - run_search includes the ai_enrich candidate when the adapter is enabled
    (and the candidate flows through scoring unchanged).
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
# Belt-and-braces: the AI adapter never touches httpx, but other adapters in
# run_search do, so keep the network kill-switch on for the whole module.
os.environ["AUTOPILOT_DISABLE_NETWORK"] = "1"

import pytest  # noqa: E402

from api.services import catalog_autopilot as ap  # noqa: E402
from agents import claude_client  # noqa: E402


# A representative, well-formed Claude JSON enrichment payload.
_AI_PAYLOAD = {
    "title": "Ray-Ban RB4105 Wayfarer Folding",
    "category": "SUNGLASS",
    "frame_shape": "Wayfarer",
    "frame_material": "Acetate",
    "lens_material": "Crystal",
    "gender": "Unisex",
    "description": "The iconic foldable Wayfarer in lightweight acetate. "
    "Folds flat to fit a compact case. Offers full UV protection.",
    "usp": "The original Wayfarer that folds away.",
    "specs": {"Lens width": "50 mm", "Bridge": "22 mm", "Temple": "140 mm"},
    "suggested_hsn": "900410",
    "suggested_gst_rate": 18,
    "confidence": 0.86,
    "needs_review": False,
}


def _mock_claude_json(monkeypatch, *, returns=None, raises=None):
    """Patch agents.claude_client.call_claude_json with an async stub. The
    AIEnrichAdapter imports it locally as `from agents.claude_client import
    call_claude_json`, so patching the module attribute is what binds."""
    calls = {"n": 0, "system": None, "user": None}

    async def _stub(system, user, **kwargs):
        calls["n"] += 1
        calls["system"] = system
        calls["user"] = user
        if raises is not None:
            raise raises
        return returns

    monkeypatch.setattr(claude_client, "call_claude_json", _stub)
    return calls


# ---------------------------------------------------------------------------
# is_enabled() gating
# ---------------------------------------------------------------------------

class TestAIEnablement:
    def test_disabled_without_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        adapter = ap.AIEnrichAdapter()
        assert adapter.is_enabled() is False
        assert "ANTHROPIC_API_KEY" in adapter.reason()

    def test_enabled_with_api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        adapter = ap.AIEnrichAdapter()
        # The agents.claude_client module is importable in the test env, so the
        # only remaining gate is the key, which we just set.
        assert adapter.is_enabled() is True

    def test_metadata_contract(self):
        adapter = ap.AIEnrichAdapter()
        assert adapter.name == "ai_enrich"
        assert adapter.label == "AI product enrichment (Claude)"
        assert adapter.source_class == ap.AUTHORIZED
        assert adapter.priority == 2


# ---------------------------------------------------------------------------
# _search / search -> candidate shape
# ---------------------------------------------------------------------------

class TestAISearchHappyPath:
    def test_returns_one_wellformed_candidate(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        calls = _mock_claude_json(monkeypatch, returns=dict(_AI_PAYLOAD))

        out = ap.AIEnrichAdapter().search("Ray-Ban", "RB4105")
        assert len(out) == 1
        cand = out[0]

        # Source identity + copyright class.
        assert cand["source"] == "ai_enrich"
        assert cand["source_class"] == ap.AUTHORIZED

        # Enrichment TEXT present.
        assert "Wayfarer" in cand["title"]
        assert cand["description"].startswith("The iconic foldable Wayfarer")
        assert cand["usp"]
        assert cand["specs"]["Lens width"] == "50 mm"
        assert cand["category"] == "SUNGLASS"

        # AI enrichment fields carried on the candidate.
        assert cand["suggested_hsn"] == "900410"
        assert cand["suggested_gst_rate"] == 18.0
        assert cand["confidence"] == 0.86
        assert cand["needs_review"] is False
        assert cand["frame_material"] == "Acetate"

        # CRITICAL: the AI must NOT fabricate product image URLs.
        assert cand["image_urls"] == []

        # Exactly ONE Claude call, and the allowed IMS categories were offered.
        assert calls["n"] == 1
        assert "FRAME" in calls["user"] and "SUNGLASS" in calls["user"]

    def test_category_maps_to_real_ims_category(self, monkeypatch):
        # The category the AI is asked to choose from must be real IMS GST
        # categories (so POS can price it).
        from api.services.gst_rates import GST_CATEGORY_TABLE

        for c in ap._ai_allowed_categories():
            assert c in GST_CATEGORY_TABLE

    def test_missing_fields_default_failsoft(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        # Minimal payload: only a description. Everything else must default
        # without raising; title falls back to "<brand> <model>".
        _mock_claude_json(monkeypatch, returns={"description": "A nice frame."})
        out = ap.AIEnrichAdapter().search("Oakley", "OO9208")
        assert len(out) == 1
        cand = out[0]
        assert cand["title"] == "Oakley OO9208"
        assert cand["description"] == "A nice frame."
        assert cand["image_urls"] == []
        # needs_review defaults to True when the model didn't say otherwise.
        assert cand["needs_review"] is True


# ---------------------------------------------------------------------------
# Fail-soft behaviour
# ---------------------------------------------------------------------------

class TestAIFailSoft:
    def test_disabled_adapter_returns_empty(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Even if the client would return data, a disabled adapter must not call
        # it and must return [].
        calls = _mock_claude_json(monkeypatch, returns=dict(_AI_PAYLOAD))
        assert ap.AIEnrichAdapter().search("Ray-Ban", "RB4105") == []
        assert calls["n"] == 0

    def test_client_error_is_failsoft(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _mock_claude_json(monkeypatch, raises=RuntimeError("api boom"))
        # The adapter's public search() wrapper swallows; result is [].
        assert ap.AIEnrichAdapter().search("Ray-Ban", "RB4105") == []

    def test_none_output_is_failsoft(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        # call_claude_json already returns None on non-JSON / parse failure.
        _mock_claude_json(monkeypatch, returns=None)
        assert ap.AIEnrichAdapter().search("Ray-Ban", "RB4105") == []

    def test_non_dict_output_is_failsoft(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        # Defensive: if a future helper handed back a list, we still return [].
        _mock_claude_json(monkeypatch, returns=["not", "a", "dict"])
        assert ap.AIEnrichAdapter().search("Ray-Ban", "RB4105") == []


# ---------------------------------------------------------------------------
# run_search integration
# ---------------------------------------------------------------------------

class TestRunSearchIncludesAI:
    def test_ai_candidate_present_when_enabled(self, monkeypatch):
        # Disable the other sources so ai_enrich is the only contributor; assert
        # it flows through run_search's scoring/sort unchanged.
        for var in (
            "ECOMMERCE_DATABASE_URL",
            "MYLUXOTTICA_USER",
            "MYLUXOTTICA_PASS",
            "SERP_API_KEY",
            "GOOGLE_CSE_KEY",
            "GOOGLE_CSE_CX",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "1")  # kills brand_site
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _mock_claude_json(monkeypatch, returns=dict(_AI_PAYLOAD))

        out = ap.run_search("Ray-Ban", "RB4105")
        names = [c["source"] for c in out["candidates"]]
        assert "ai_enrich" in names
        ai = next(c for c in out["candidates"] if c["source"] == "ai_enrich")
        assert ai["source_class"] == ap.AUTHORIZED
        assert ai["source_priority"] == 2
        assert ai["image_urls"] == []
        # Scored by run_search: brand + model both match -> 1.0.
        assert ai["score"] == 1.0
        # ai_enrich still appears in the registry-derived sources list.
        assert "ai_enrich" in {s["name"] for s in out["sources"]}

    def test_ai_absent_when_key_unset(self, monkeypatch):
        for var in (
            "ANTHROPIC_API_KEY",
            "ECOMMERCE_DATABASE_URL",
            "MYLUXOTTICA_USER",
            "MYLUXOTTICA_PASS",
            "SERP_API_KEY",
            "GOOGLE_CSE_KEY",
            "GOOGLE_CSE_CX",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "1")
        out = ap.run_search("Ray-Ban", "RB4105")
        assert out["candidate_count"] == 0
        # The adapter is still listed (just disabled) in sources.
        statuses = {s["name"]: s for s in out["sources"]}
        assert statuses["ai_enrich"]["enabled"] is False
