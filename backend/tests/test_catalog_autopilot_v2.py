"""
IMS 2.0 - Catalog Autopilot v2 tests
====================================
Covers the v2 additions:
  - category on the job body -> canonicalised, echoed in the query, and
    STAMPED onto every returned candidate (no more guessing downstream)
  - category-aware source queries (_category_query_word)
  - source_url + references audit fields on candidates (scrape-time stamping)
  - brand-site MULTI-candidate: the search-results page is mined for product
    links and each product page becomes its own candidate (>= 4-5 options when
    the source can supply them), with per-page dedupe
  - optional AI spec-mapping enrichment (ai_attributes): deterministic gating
    on ANTHROPIC_API_KEY, restricted to the category's canonical field names,
    and STRICTLY fail-soft (no key / error -> candidates simply lack it)

NO real network call happens: httpx is mocked via httpx.MockTransport and the
AUTOPILOT_DISABLE_NETWORK kill-switch is the module default.
"""

import asyncio
import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ["AUTOPILOT_DISABLE_NETWORK"] = "1"

import httpx  # noqa: E402
import pytest  # noqa: E402

from agents import claude_client  # noqa: E402
from api.services import catalog_autopilot as ap  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers (mirrors the 1b test harness)
# ---------------------------------------------------------------------------

def _clear_source_env(monkeypatch):
    for var in (
        "ECOMMERCE_DATABASE_URL",
        "MYLUXOTTICA_USER",
        "MYLUXOTTICA_PASS",
        "SERP_API_KEY",
        "GOOGLE_CSE_KEY",
        "GOOGLE_CSE_CX",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def _install_mock_httpx(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    original_client = httpx.Client

    class _TestClient(original_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _TestClient)


def _mock_claude_json(monkeypatch, *, returns=None, raises=None):
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


def _stub_adapter(name, source_class, priority, candidates, enabled=True):
    """Fake adapter with the LEGACY 5-arg _search signature (proves old
    adapters keep working now that search() threads a category through)."""

    class _Stub(ap.SourceAdapter):
        pass

    _Stub.name = name
    _Stub.label = name
    _Stub.source_class = source_class
    _Stub.priority = priority
    inst = _Stub()
    inst.is_enabled = lambda: enabled  # type: ignore
    inst._search = lambda brand, model, color, size, limit: [dict(c) for c in candidates]  # type: ignore
    return inst


def _scraped_cand(**over):
    base = {
        "source": "s", "source_class": ap.AUTHORIZED, "url": None,
        "title": "Ray-Ban RB4105 Wayfarer", "brand": "Ray-Ban", "model": "RB4105",
        "color": None, "size": None, "image_urls": [],
        "specs": {"Lens width": "50 mm"}, "description": "Folding wayfarer.",
    }
    base.update(over)
    return base


# A search-results page with three same-host product links carrying the model
# in the href, plus off-site / asset / search links that must be filtered out.
_SEARCH_RESULTS_HTML = """
<html><head><title>Search results | Ray-Ban India</title></head><body>
  <a href="/india/p/rb4105-black">RB4105 Black</a>
  <a href="https://www.ray-ban.com/india/p/rb4105-tortoise">RB4105 Tortoise</a>
  <a href="/india/p/rb4105-blue">RB4105 Blue</a>
  <a href="https://evil.example.com/p/rb4105">off-site dupe</a>
  <a href="/india/search?q=rb4105&page=2">Next page</a>
  <a href="/assets/site.css">stylesheet</a>
  <a href="javascript:void(0)">noop</a>
</body></html>
"""

_PRODUCT_PAGE_HTML = """
<html><head>
  <title>Ray-Ban RB4105 Wayfarer Folding | Ray-Ban India</title>
  <meta name="description" content="The iconic foldable Wayfarer.">
  <meta property="og:image" content="https://img.ray-ban.com/rb4105.jpg">
</head><body>
  <table><tr><th>Frame material</th><td>Acetate</td></tr></table>
</body></html>
"""

# A page with NO anchors -> the legacy single-candidate fallback path.
_NO_LINKS_HTML = """
<html><head>
  <title>Ray-Ban RB4105 Wayfarer Folding | Ray-Ban India</title>
  <meta name="description" content="The iconic foldable Wayfarer.">
</head><body>
  <table><tr><th>Frame material</th><td>Acetate</td></tr></table>
</body></html>
"""


# ---------------------------------------------------------------------------
# Category plumbing
# ---------------------------------------------------------------------------

class TestCategoryPlumbing:
    def test_run_search_stamps_category_on_every_candidate(self, monkeypatch):
        _clear_source_env(monkeypatch)
        reg = [_stub_adapter("s", ap.AUTHORIZED, 1, [_scraped_cand()])]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)
        out = ap.run_search("Ray-Ban", "RB4105", category="SUNGLASS")
        assert out["query"]["category"] == "SUNGLASS"
        assert out["candidate_count"] == 1
        assert out["candidates"][0]["category"] == "SUNGLASS"

    def test_run_search_without_category_keeps_legacy_query_shape(self, monkeypatch):
        monkeypatch.setattr(ap, "build_registry", lambda: [])
        out = ap.run_search("Ray-Ban", "RB4105", "Black", "50")
        # No category key at all when the caller didn't pass one.
        assert out["query"] == {
            "brand": "Ray-Ban", "model": "RB4105", "color": "Black", "size": "50"
        }

    def test_legacy_5_arg_adapter_still_works_with_category(self, monkeypatch):
        # _stub_adapter's _search has NO category parameter; search() must
        # detect that and call it with the legacy signature.
        adapter = _stub_adapter("legacy", ap.AUTHORIZED, 1, [_scraped_cand()])
        out = adapter.search("Ray-Ban", "RB4105", category="SUNGLASS")
        assert len(out) == 1
        # v2 reference fields defaulted by the wrapper.
        assert out[0]["source_url"] is None
        assert out[0]["references"] == []

    def test_category_query_word(self):
        assert ap._category_query_word("SUNGLASS") == "sunglasses"
        assert ap._category_query_word("READING_GLASSES") == "reading glasses"
        assert ap._category_query_word("") == ""
        assert ap._category_query_word(None) == ""
        # Unknown categories fall back to their lower-cased words.
        assert ap._category_query_word("WEIRD_THING") == "weird thing"

    def test_router_body_accepts_and_canonicalises_category(self):
        from api.routers.catalog_autopilot import JobCreate, _normalise_category

        body = JobCreate(brand="Ray-Ban", model="RB4105", category="SG")
        assert body.category == "SG"
        # Picker short-code -> canonical product_master key.
        assert _normalise_category("SG") == "SUNGLASS"
        assert _normalise_category("Sunglasses") == "SUNGLASS"
        assert _normalise_category("CL") == "CONTACT_LENS"
        assert _normalise_category("") == ""
        # Unknown values pass through upper-cased (harmless downstream).
        assert _normalise_category("weird thing") == "WEIRD_THING"

    def test_create_job_echoes_category_and_stamps_candidates(self, monkeypatch):
        # End-to-end through the ROUTER function (no HTTP server needed): the
        # job body carries category=SG -> the response query echoes the
        # canonical SUNGLASS and every candidate is stamped with it.
        _clear_source_env(monkeypatch)
        from api.routers import catalog_autopilot as router_mod

        def _fake_run_search(brand, model, color="", size="", limit=25, category=""):
            cand = _scraped_cand()
            if category:
                cand["category"] = category
            q = {"brand": brand, "model": model, "color": color, "size": size}
            if category:
                q["category"] = category
            return {
                "query": q,
                "candidates": [cand],
                "sources": [],
                "candidate_count": 1,
            }

        monkeypatch.setattr(router_mod.ap, "run_search", _fake_run_search)
        monkeypatch.setattr(router_mod, "_db", lambda: None)
        body = router_mod.JobCreate(brand="Ray-Ban", model="RB4105", category="SG")
        res = asyncio.run(
            router_mod.create_job(body, current_user={"user_id": "test-user"})
        )
        assert res["query"]["category"] == "SUNGLASS"
        assert res["candidates"][0]["category"] == "SUNGLASS"


# ---------------------------------------------------------------------------
# Brand-site multi-candidate + source_url/references
# ---------------------------------------------------------------------------

class TestBrandSiteMultiCandidate:
    def test_extract_product_links_filters_and_ranks(self):
        links = ap.extract_product_links(
            _SEARCH_RESULTS_HTML,
            "https://www.ray-ban.com/india/search?q=RB4105",
            "RB4105",
            limit=5,
        )
        assert links == [
            "https://www.ray-ban.com/india/p/rb4105-black",
            "https://www.ray-ban.com/india/p/rb4105-tortoise",
            "https://www.ray-ban.com/india/p/rb4105-blue",
        ]

    def test_extract_product_links_failsoft(self):
        assert ap.extract_product_links("", "https://x", "RB1") == []
        assert ap.extract_product_links("<a href=", "https://x", "RB1") == []
        assert ap.extract_product_links(_SEARCH_RESULTS_HTML, "", "RB1") == []

    def test_brand_site_yields_one_candidate_per_product_link(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        fetched = []

        def handler(request: httpx.Request) -> httpx.Response:
            fetched.append(str(request.url))
            if "/search" in str(request.url):
                return httpx.Response(200, text=_SEARCH_RESULTS_HTML)
            return httpx.Response(200, text=_PRODUCT_PAGE_HTML)

        _install_mock_httpx(monkeypatch, handler)
        out = ap.BrandSiteAdapter().search("Ray-Ban", "RB4105")
        assert len(out) == 3
        urls = [c["source_url"] for c in out]
        assert urls == [
            "https://www.ray-ban.com/india/p/rb4105-black",
            "https://www.ray-ban.com/india/p/rb4105-tortoise",
            "https://www.ray-ban.com/india/p/rb4105-blue",
        ]
        for c in out:
            assert c["references"] == [{"source": "brand_site", "url": c["source_url"]}]
            assert c["url"] == c["source_url"]
            assert c["specs"]["Frame material"] == "Acetate"

    def test_multi_candidates_survive_run_search_dedupe(self, monkeypatch):
        # Same brand+model+source but DISTINCT source_urls -> all kept; the
        # legacy per-source collapse (no source_url) still collapses.
        c1 = _scraped_cand(source="brand_site", source_url="https://b/p/1")
        c2 = _scraped_cand(source="brand_site", source_url="https://b/p/2")
        c3 = _scraped_cand(source="brand_site", source_url="https://b/p/2")  # dup page
        reg = [_stub_adapter("brand_site", ap.AUTHORIZED, 1, [c1, c2, c3])]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)
        out = ap.run_search("Ray-Ban", "RB4105")
        assert out["candidate_count"] == 2

    def test_brand_site_fallback_single_candidate_when_no_links(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_NO_LINKS_HTML)

        _install_mock_httpx(monkeypatch, handler)
        out = ap.BrandSiteAdapter().search("Ray-Ban", "RB4105")
        assert len(out) == 1
        # Fallback stamps the search URL as the reference.
        assert out[0]["source_url"] and "ray-ban.com" in out[0]["source_url"]
        assert out[0]["references"][0]["source"] == "brand_site"

    def test_brand_site_query_includes_category_word(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.setdefault("url", str(request.url))
            return httpx.Response(200, text=_NO_LINKS_HTML)

        _install_mock_httpx(monkeypatch, handler)
        ap.BrandSiteAdapter().search("Ray-Ban", "RB4105", category="SUNGLASS")
        assert "sunglasses" in captured["url"]

    def test_marketplace_candidates_carry_source_url(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setenv("SERP_API_KEY", "serp-key")
        body = (
            '{"organic_results": [{"title": "RB4105 on Amazon",'
            ' "link": "https://amazon.in/rb4105", "snippet": "Foldable"}]}'
        )

        def handler(request):
            return httpx.Response(200, text=body)

        _install_mock_httpx(monkeypatch, handler)
        out = ap.MarketplaceAdapter().search("Ray-Ban", "RB4105")
        assert out[0]["source_url"] == "https://amazon.in/rb4105"
        assert out[0]["references"] == [
            {"source": "marketplace", "url": "https://amazon.in/rb4105"}
        ]


# ---------------------------------------------------------------------------
# AI spec-mapping enrichment (ai_attributes)
# ---------------------------------------------------------------------------

class TestAISpecEnrichment:
    def test_skipped_cleanly_when_no_key(self, monkeypatch):
        _clear_source_env(monkeypatch)  # also drops ANTHROPIC_API_KEY
        calls = _mock_claude_json(monkeypatch, returns={"colour_code": "601"})
        reg = [_stub_adapter("s", ap.AUTHORIZED, 1, [_scraped_cand()])]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)
        out = ap.run_search("Ray-Ban", "RB4105", category="SUNGLASS")
        assert out["candidate_count"] == 1
        assert "ai_attributes" not in out["candidates"][0]
        assert calls["n"] == 0  # the client was never even called
        # And the direct helper is a clean no-op too.
        assert ap.ai_map_specs_to_fields(
            "SUNGLASS", "t", "d", {"Lens width": "50 mm"}, {"brand": "x"}
        ) == {}

    def test_merges_only_canonical_field_names(self, monkeypatch):
        _clear_source_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        calls = _mock_claude_json(
            monkeypatch,
            returns={
                "colour_code": "601/58",
                "lens_size": "52",
                "bogus_field": "must be dropped",
                "polarization": None,  # None dropped
            },
        )
        reg = [_stub_adapter("s", ap.AUTHORIZED, 1, [_scraped_cand()])]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)
        out = ap.run_search("Ray-Ban", "RB4105", category="SUNGLASS")
        cand = out["candidates"][0]
        # SUNGLASS canonical fields include colour_code + lens_size; bogus is out.
        assert cand["ai_attributes"] == {"colour_code": "601/58", "lens_size": "52"}
        assert calls["n"] == 1
        # The prompt offered the category's canonical field names.
        assert "colour_code" in calls["user"] and "SUNGLASS" in calls["user"]

    def test_failsoft_when_claude_errors(self, monkeypatch):
        _clear_source_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _mock_claude_json(monkeypatch, raises=RuntimeError("api boom"))
        reg = [_stub_adapter("s", ap.AUTHORIZED, 1, [_scraped_cand()])]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)
        out = ap.run_search("Ray-Ban", "RB4105", category="SUNGLASS")
        # Search still succeeds; the candidate simply has no ai_attributes.
        assert out["candidate_count"] == 1
        assert "ai_attributes" not in out["candidates"][0]

    def test_no_ai_pass_without_category(self, monkeypatch):
        _clear_source_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        calls = _mock_claude_json(monkeypatch, returns={"colour_code": "601"})
        reg = [_stub_adapter("s", ap.AUTHORIZED, 1, [_scraped_cand()])]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)
        out = ap.run_search("Ray-Ban", "RB4105")  # no category
        assert "ai_attributes" not in out["candidates"][0]
        assert calls["n"] == 0

    def test_enrichment_capped(self, monkeypatch):
        _clear_source_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AUTOPILOT_AI_ENRICH_MAX", "2")
        calls = _mock_claude_json(monkeypatch, returns={"colour_code": "601"})
        cands = [
            _scraped_cand(source_url=f"https://b/p/{i}") for i in range(5)
        ]
        reg = [_stub_adapter("brand_site", ap.AUTHORIZED, 1, cands)]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)
        out = ap.run_search("Ray-Ban", "RB4105", category="SUNGLASS")
        assert out["candidate_count"] == 5
        enriched = [c for c in out["candidates"] if "ai_attributes" in c]
        assert len(enriched) == 2
        assert calls["n"] == 2
