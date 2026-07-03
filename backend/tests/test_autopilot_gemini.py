"""
IMS 2.0 - Catalog Autopilot: Gemini web-grounded search source
==============================================================
Covers the new GeminiSearchAdapter, which uses Google Gemini with the built-in
google_search grounding tool to return web-REFERENCED product data on the
owner's Google Cloud credit:

  1. integration_config.get_gemini_config(): DB-first, env fallback
     (GEMINI_API_KEY / GOOGLE_API_KEY), model default.
  2. GeminiSearchAdapter.is_enabled() gates on a configured api_key and the
     AUTOPILOT_DISABLE_NETWORK kill-switch.
  3. Response parsing: text + grounding references extracted; lenient JSON;
     candidate carries source_url + references (grounding URIs) + specs.
  4. _search does NO network call when disabled / unconfigured (fail-soft).
  5. The adapter is registered and the integrations catalog exposes a "gemini"
     row with the api_key field marked secret (so it encrypts + masks).

NO real network call happens: _http_post_json is monkeypatched with a canned
Gemini response for the one search test.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

from api.services import catalog_autopilot as ap  # noqa: E402
from api.services import integration_config as ic  # noqa: E402


# A minimal but realistic Gemini generateContent response with grounding.
_GEMINI_RESPONSE = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "text": (
                            "```json\n"
                            '{"products": [{'
                            '"brand": "Ray-Ban", "model": "RB3025", '
                            '"color": "Gold", "size": "58", '
                            '"title": "Ray-Ban Aviator RB3025", '
                            '"frame_shape": "Aviator", "frame_material": "Metal", '
                            '"gender": "Unisex", '
                            '"description": "Iconic teardrop aviator.", '
                            '"usp": "Timeless pilot design", '
                            '"specs": {"lens_width": "58"}, '
                            '"suggested_hsn": "9004", "suggested_gst_rate": 18, '
                            '"image_url": "https://img.example.com/rb3025.jpg", '
                            '"confidence": 0.9'
                            "}]}\n"
                            "```"
                        )
                    }
                ]
            },
            "groundingMetadata": {
                "groundingChunks": [
                    {"web": {"uri": "https://ray-ban.com/rb3025", "title": "Ray-Ban"}},
                    {"web": {"uri": "https://lenskart.com/rb3025", "title": "Lenskart"}},
                ]
            },
        }
    ]
}


# ---------------------------------------------------------------------------
# 1. integration_config.get_gemini_config -- DB-first, env fallback
# ---------------------------------------------------------------------------


class TestGeminiConfig:
    def test_unconfigured_returns_empty(self, monkeypatch):
        monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert ic.get_gemini_config() == {}

    def test_env_key_fallback_with_default_model(self, monkeypatch):
        monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})
        monkeypatch.setenv("GEMINI_API_KEY", "env-key")
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        cfg = ic.get_gemini_config()
        assert cfg["api_key"] == "env-key"
        assert cfg["model"] == ic.DEFAULT_GEMINI_MODEL

    def test_google_api_key_also_accepted(self, monkeypatch):
        monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "goog-key")
        assert ic.get_gemini_config()["api_key"] == "goog-key"

    def test_db_wins_over_env(self, monkeypatch):
        monkeypatch.setattr(
            ic, "_load_db_config", lambda _type: {"api_key": "db-key", "model": "gemini-x"}
        )
        monkeypatch.setenv("GEMINI_API_KEY", "env-key")
        cfg = ic.get_gemini_config()
        assert cfg["api_key"] == "db-key"
        assert cfg["model"] == "gemini-x"


# ---------------------------------------------------------------------------
# 2. GeminiSearchAdapter -- config + network gate is_enabled()
# ---------------------------------------------------------------------------


class TestGeminiAdapterGate:
    def test_disabled_without_key(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setattr(ic, "get_gemini_config", lambda: {})
        assert ap.GeminiSearchAdapter().is_enabled() is False

    def test_enabled_with_key(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setattr(
            ic, "get_gemini_config", lambda: {"api_key": "k", "model": "gemini-2.0-flash"}
        )
        adapter = ap.GeminiSearchAdapter()
        assert adapter.is_enabled() is True
        assert "Google Cloud credit" in adapter.reason()

    def test_network_disabled_forces_off(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "1")
        monkeypatch.setattr(ic, "get_gemini_config", lambda: {"api_key": "k"})
        adapter = ap.GeminiSearchAdapter()
        assert adapter.is_enabled() is False
        assert "AUTOPILOT_DISABLE_NETWORK" in adapter.reason()

    def test_source_classification(self):
        adapter = ap.GeminiSearchAdapter()
        assert adapter.name == "gemini"
        assert adapter.source_class == ap.UNVERIFIED
        assert adapter.priority == 2


# ---------------------------------------------------------------------------
# 3. Response parsing -- text, references, lenient JSON, candidate shape
# ---------------------------------------------------------------------------


class TestGeminiParsing:
    def test_extract_text_and_references(self):
        text, refs = ap.GeminiSearchAdapter._extract(_GEMINI_RESPONSE)
        assert "RB3025" in text
        assert [r["url"] for r in refs] == [
            "https://ray-ban.com/rb3025",
            "https://lenskart.com/rb3025",
        ]
        assert all(r["source"] == "gemini" for r in refs)

    def test_extract_empty_response(self):
        assert ap.GeminiSearchAdapter._extract({}) == ("", [])
        assert ap.GeminiSearchAdapter._extract({"candidates": []}) == ("", [])

    def test_parse_products_fenced(self):
        text = '```json\n{"products": [{"brand": "Ray-Ban", "model": "RB3025"}]}\n```'
        out = ap.GeminiSearchAdapter._parse_products(text)
        assert len(out) == 1 and out[0]["model"] == "RB3025"

    def test_parse_products_bare_object(self):
        text = '{"brand": "Oakley", "model": "OO9208", "title": "Radar"}'
        out = ap.GeminiSearchAdapter._parse_products(text)
        assert len(out) == 1 and out[0]["brand"] == "Oakley"

    def test_parse_products_junk_returns_empty(self):
        assert ap.GeminiSearchAdapter._parse_products("not json at all") == []
        assert ap.GeminiSearchAdapter._parse_products("") == []

    def test_to_candidate_maps_fields_and_references(self):
        adapter = ap.GeminiSearchAdapter()
        refs = [{"source": "gemini", "url": "https://ray-ban.com/rb3025", "title": "RB"}]
        product = {
            "title": "Ray-Ban Aviator",
            "color": "Gold",
            "size": "58",
            "frame_shape": "Aviator",
            "description": "Iconic.",
            "usp": "Classic",
            "specs": {"lens_width": "58"},
            "suggested_hsn": "9004",
            "suggested_gst_rate": 18,
            "image_url": "https://img.example.com/rb3025.jpg",
            "confidence": 0.9,
        }
        cand = adapter._to_candidate(product, "Ray-Ban", "RB3025", "", "", refs)
        assert cand["source"] == "gemini"
        assert cand["source_class"] == ap.UNVERIFIED
        assert cand["brand"] == "Ray-Ban" and cand["model"] == "RB3025"
        assert cand["color"] == "Gold"
        assert cand["source_url"] == "https://ray-ban.com/rb3025"
        assert cand["references"] == refs
        assert cand["image_urls"] == ["https://img.example.com/rb3025.jpg"]
        assert cand["frame_shape"] == "Aviator"
        assert cand["suggested_gst_rate"] == 18.0


# ---------------------------------------------------------------------------
# 4. _search -- end-to-end with a canned response; and no-network safety
# ---------------------------------------------------------------------------


class TestGeminiSearch:
    def test_search_returns_referenced_candidate(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setattr(
            ic, "get_gemini_config", lambda: {"api_key": "k", "model": "gemini-2.0-flash"}
        )
        # Ensure httpx is treated as present, and stub the HTTP layer.
        monkeypatch.setattr(ap, "httpx", object(), raising=False)
        captured = {}

        def _fake_post(url, payload, headers=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return _GEMINI_RESPONSE

        monkeypatch.setattr(ap, "_http_post_json", _fake_post)
        out = ap.GeminiSearchAdapter().search("Ray-Ban", "RB3025", "Gold", "58", 25)
        assert len(out) == 1
        cand = out[0]
        assert cand["source"] == "gemini"
        assert cand["model"] == "RB3025"
        assert cand["references"][0]["url"] == "https://ray-ban.com/rb3025"
        # The key must ride in the header, never the URL.
        assert "gemini-2.0-flash:generateContent" in captured["url"]
        assert "k" not in captured["url"]
        assert captured["headers"].get("x-goog-api-key") == "k"

    def test_search_no_network_makes_no_call(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "1")
        monkeypatch.setattr(ic, "get_gemini_config", lambda: {"api_key": "k"})

        def _boom(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("network called while disabled")

        monkeypatch.setattr(ap, "_http_post_json", _boom)
        assert ap.GeminiSearchAdapter().search("Ray-Ban", "RB3025", "", "", 25) == []


# ---------------------------------------------------------------------------
# 5. Registry + integrations catalog wiring
# ---------------------------------------------------------------------------


class TestGeminiWiring:
    def test_registry_includes_gemini(self):
        names = [a.name for a in ap.build_registry()]
        assert "gemini" in names
        # Registered ahead of ai_enrich at the same priority so it wins ties.
        assert names.index("gemini") < names.index("ai_enrich")

    def test_catalog_has_gemini_row(self):
        from api.routers.settings import _INTEGRATION_CATALOG

        by_type = {e["type"]: e for e in _INTEGRATION_CATALOG}
        assert "gemini" in by_type
        assert by_type["gemini"]["category"] == "Commerce"

    def test_gemini_api_key_field_is_secret(self):
        from api.routers.settings import _INTEGRATION_CATALOG

        entry = next(e for e in _INTEGRATION_CATALOG if e["type"] == "gemini")
        api_key_field = next(f for f in entry["fields"] if f["key"] == "api_key")
        assert api_key_field["secret"] is True
