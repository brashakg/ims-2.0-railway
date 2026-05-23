"""
IMS 2.0 - Catalog Autopilot Phase 1b tests (source-adapter framework)
=====================================================================
Covers the pluggable SourceAdapter framework:
  - adapter interface + fail-soft wrapper (an adapter that raises -> [])
  - is_enabled() env/creds gating (creds toggled on/off via monkeypatch)
  - the stdlib page-scrape helper (no network, no third-party deps)
  - run_search priority ordering, cross-source dedupe, scoring + stable sort
  - GET /sources is derived from the registry (real enabled flags)
  - the copyright stance is preserved (UNVERIFIED-source images never auto-usable)

NO real network call happens: httpx is mocked via httpx.MockTransport, and the
AUTOPILOT_DISABLE_NETWORK kill-switch is set by default so an un-mocked path can
never reach the wire.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
# Default-off network for the whole module; individual network tests opt back in
# AND install an httpx.MockTransport, so nothing ever touches the real internet.
os.environ["AUTOPILOT_DISABLE_NETWORK"] = "1"

import httpx  # noqa: E402
import pytest  # noqa: E402

from api.services import catalog_autopilot as ap  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_source_env(monkeypatch):
    """Strip every credential/key env so adapters start from a known-disabled
    baseline; each test enables exactly what it needs."""
    for var in (
        "ECOMMERCE_DATABASE_URL",
        "MYLUXOTTICA_USER",
        "MYLUXOTTICA_PASS",
        "SERP_API_KEY",
        "GOOGLE_CSE_KEY",
        "GOOGLE_CSE_CX",
    ):
        monkeypatch.delenv(var, raising=False)


def _install_mock_httpx(monkeypatch, handler):
    """Route every httpx.Client through a MockTransport so no real network I/O
    occurs. `handler(request) -> httpx.Response`."""
    transport = httpx.MockTransport(handler)
    original_client = httpx.Client

    class _TestClient(original_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _TestClient)


_SAMPLE_HTML = """
<html><head>
  <title>Ray-Ban RB4105 Wayfarer Folding | Ray-Ban India</title>
  <meta name="description" content="The iconic foldable Wayfarer. Lightweight acetate. UV protection.">
  <meta property="og:image" content="https://img.ray-ban.com/rb4105.jpg">
</head><body>
  <img src="https://img.ray-ban.com/rb4105-front.jpg">
  <img src="data:image/png;base64,AAAA">
  <table>
    <tr><th>Frame material</th><td>Acetate</td></tr>
    <tr><th>Lens width</th><td>50 mm</td></tr>
  </table>
  <dl><dt>Gender</dt><dd>Unisex</dd></dl>
</body></html>
"""


# ---------------------------------------------------------------------------
# Page-scrape helper (stdlib only)
# ---------------------------------------------------------------------------

class TestScrapeHelper:
    def test_extracts_title_description_specs_images_usp(self):
        out = ap.scrape_product_page(_SAMPLE_HTML)
        assert "RB4105" in out["title"]
        assert "foldable Wayfarer" in out["description"].replace("  ", " ")
        assert out["specs"]["Frame material"] == "Acetate"
        assert out["specs"]["Lens width"] == "50 mm"
        assert out["specs"]["Gender"] == "Unisex"
        # USP = first sentence of the description.
        assert out["usp"].startswith("The iconic foldable Wayfarer")
        # og:image + a normal <img>, but NOT the data: URI.
        assert "https://img.ray-ban.com/rb4105.jpg" in out["image_urls"]
        assert "https://img.ray-ban.com/rb4105-front.jpg" in out["image_urls"]
        assert all(not u.startswith("data:") for u in out["image_urls"])

    def test_failsoft_on_empty_and_garbage(self):
        assert ap.scrape_product_page("") == {
            "title": "", "description": "", "specs": {}, "usp": "", "image_urls": [],
        }
        # Malformed HTML must not raise.
        out = ap.scrape_product_page("<html><title>oops<<<>>> <img src=")
        assert isinstance(out, dict)
        assert "title" in out

    def test_no_third_party_html_parser_dep(self):
        # The helper must rely on the stdlib html.parser, never bs4/lxml.
        # Check for IMPORTS specifically (the module docstring legitimately
        # mentions "no bs4/lxml", which is fine).
        import re as _re
        import api.services.catalog_autopilot as mod
        src = open(mod.__file__, encoding="utf-8").read()
        assert not _re.search(r"^\s*(import|from)\s+bs4", src, _re.M)
        assert not _re.search(r"^\s*(import|from)\s+lxml", src, _re.M)
        assert "BeautifulSoup" not in src
        assert "from html.parser import HTMLParser" in src


# ---------------------------------------------------------------------------
# Adapter interface
# ---------------------------------------------------------------------------

class TestAdapterInterface:
    def test_every_adapter_exposes_the_contract(self):
        for a in ap.build_registry():
            assert isinstance(a.name, str) and a.name
            assert isinstance(a.label, str) and a.label
            assert a.source_class in (ap.AUTHORIZED, ap.UNVERIFIED)
            assert isinstance(a.priority, int)
            assert isinstance(a.is_enabled(), bool)
            status = a.status()
            assert set(status) >= {
                "name", "label", "source_class", "priority", "enabled", "reason"
            }

    def test_registry_is_priority_ordered(self):
        names = [a.name for a in ap.build_registry()]
        prios = [a.priority for a in ap.build_registry()]
        assert names == ["brand_site", "myluxottica", "internal_bvi", "marketplace"]
        assert prios == sorted(prios)

    def test_search_wrapper_stamps_source_and_class(self, monkeypatch):
        class _Bare(ap.SourceAdapter):
            name = "bare"
            label = "Bare"
            source_class = ap.AUTHORIZED
            priority = 9

            def is_enabled(self):
                return True

            def _search(self, brand, model, color, size, limit):
                # Return a candidate missing source/source_class on purpose.
                return [{"title": "x", "model": model}]

        out = _Bare().search("Ray-Ban", "RB1")
        assert out[0]["source"] == "bare"
        assert out[0]["source_class"] == ap.AUTHORIZED

    def test_search_wrapper_is_failsoft_when_search_raises(self):
        class _Boom(ap.SourceAdapter):
            name = "boom"
            label = "Boom"
            priority = 9

            def is_enabled(self):
                return True

            def _search(self, brand, model, color, size, limit):
                raise RuntimeError("kaboom")

        # Must swallow the exception and return [].
        assert _Boom().search("Ray-Ban", "RB1") == []


# ---------------------------------------------------------------------------
# is_enabled() env/creds gating
# ---------------------------------------------------------------------------

class TestEnablementGating:
    def test_internal_catalog_gated_on_ecommerce_url(self, monkeypatch):
        _clear_source_env(monkeypatch)
        adapter = ap.InternalCatalogAdapter()
        assert adapter.is_enabled() is False
        monkeypatch.setenv("ECOMMERCE_DATABASE_URL", "postgres://x")
        assert adapter.is_enabled() is True

    def test_myluxottica_needs_both_creds(self, monkeypatch):
        _clear_source_env(monkeypatch)
        adapter = ap.MyLuxotticaAdapter()
        assert adapter.is_enabled() is False
        monkeypatch.setenv("MYLUXOTTICA_USER", "dealer42")
        assert adapter.is_enabled() is False  # password still missing
        monkeypatch.setenv("MYLUXOTTICA_PASS", "secret")
        assert adapter.is_enabled() is True

    def test_marketplace_gated_on_serp_or_cse(self, monkeypatch):
        _clear_source_env(monkeypatch)
        # Allow network for this gating check; we never call search() here.
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        adapter = ap.MarketplaceAdapter()
        assert adapter.is_enabled() is False
        monkeypatch.setenv("SERP_API_KEY", "serp-key")
        assert adapter.is_enabled() is True
        monkeypatch.delenv("SERP_API_KEY", raising=False)
        assert adapter.is_enabled() is False
        # CSE needs BOTH key and cx.
        monkeypatch.setenv("GOOGLE_CSE_KEY", "k")
        assert adapter.is_enabled() is False
        monkeypatch.setenv("GOOGLE_CSE_CX", "cx")
        assert adapter.is_enabled() is True

    def test_brand_site_enabled_with_httpx_and_sites(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        assert ap.BrandSiteAdapter().is_enabled() is True

    def test_network_killswitch_disables_web_adapters(self, monkeypatch):
        _clear_source_env(monkeypatch)
        monkeypatch.setenv("SERP_API_KEY", "serp-key")
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "1")
        assert ap.BrandSiteAdapter().is_enabled() is False
        assert ap.MarketplaceAdapter().is_enabled() is False


# ---------------------------------------------------------------------------
# GET /sources is registry-derived
# ---------------------------------------------------------------------------

class TestProviderStatus:
    def test_provider_status_reflects_real_enabled_flags(self, monkeypatch):
        _clear_source_env(monkeypatch)
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "1")
        status = {s["name"]: s for s in ap._provider_status()}
        assert set(status) == {"brand_site", "myluxottica", "internal_bvi", "marketplace"}
        assert status["brand_site"]["enabled"] is False  # killswitch on
        assert status["myluxottica"]["enabled"] is False
        assert status["internal_bvi"]["enabled"] is False
        assert status["marketplace"]["enabled"] is False

        # Flip internal on -> status follows.
        monkeypatch.setenv("ECOMMERCE_DATABASE_URL", "postgres://x")
        status2 = {s["name"]: s for s in ap._provider_status()}
        assert status2["internal_bvi"]["enabled"] is True

    def test_status_order_matches_priority(self):
        order = [s["priority"] for s in ap._provider_status()]
        assert order == sorted(order)


# ---------------------------------------------------------------------------
# run_search orchestration: priority ordering, dedupe, scoring/sort
# ---------------------------------------------------------------------------

def _stub_adapter(name, source_class, priority, candidates, enabled=True):
    """Build a fake adapter that returns canned candidates."""
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


class TestRunSearchOrchestration:
    def test_only_enabled_adapters_run_and_priority_order(self, monkeypatch):
        calls = []

        def make(name, prio, enabled):
            cand = {
                "source": name, "source_class": ap.AUTHORIZED, "url": None,
                "title": f"{name}-RB4105", "brand": "Ray-Ban", "model": "RB4105",
                "color": None, "size": None, "image_urls": [], "specs": {},
            }
            a = _stub_adapter(name, ap.AUTHORIZED, prio, [cand], enabled=enabled)
            orig = a.search

            def traced(*args, **kwargs):
                calls.append(name)
                return orig(*args, **kwargs)

            a.search = traced  # type: ignore
            return a

        reg = [make("p1", 1, True), make("p2", 2, False), make("p3", 3, True)]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)

        out = ap.run_search("Ray-Ban", "RB4105")
        # Disabled adapter never searched; enabled ones searched in priority order.
        assert calls == ["p1", "p3"]
        # Different sources for the same product are BOTH kept (dedup/enrich
        # view), but the higher-priority source sorts first on a score tie.
        assert out["candidate_count"] == 2
        assert out["candidates"][0]["source"] == "p1"
        assert out["candidates"][0]["source_priority"] == 1

    def test_same_source_duplicate_collapses(self, monkeypatch):
        # One source returning the same brand+model twice (e.g. two URL variants
        # that normalize equal) collapses to a single candidate.
        c1 = {
            "source": "brand_site", "source_class": ap.AUTHORIZED, "url": "a",
            "title": "RB4105", "brand": "Ray-Ban", "model": "RB 4105",
            "color": None, "size": None, "image_urls": ["x.jpg"], "specs": {},
        }
        c2 = {
            "source": "brand_site", "source_class": ap.AUTHORIZED, "url": "b",
            "title": "RB4105 dup", "brand": "rayban", "model": "rb-4105",
            "color": None, "size": None, "image_urls": ["y.jpg"], "specs": {},
        }
        reg = [_stub_adapter("brand_site", ap.AUTHORIZED, 1, [c1, c2])]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)
        out = ap.run_search("Ray-Ban", "RB4105")
        assert out["candidate_count"] == 1
        # First-seen wins within a source.
        assert out["candidates"][0]["url"] == "a"

    def test_cross_source_same_model_both_kept_higher_priority_first(self, monkeypatch):
        auth = {
            "source": "brand_site", "source_class": ap.AUTHORIZED, "url": "a",
            "title": "RB4105", "brand": "Ray-Ban", "model": "RB 4105",  # normalizes equal
            "color": None, "size": None, "image_urls": ["x.jpg"], "specs": {},
        }
        unver = {
            "source": "marketplace", "source_class": ap.UNVERIFIED, "url": "b",
            "title": "RB4105", "brand": "rayban", "model": "rb4105",
            "color": None, "size": None, "image_urls": ["y.jpg"], "specs": {},
        }
        reg = [
            _stub_adapter("brand_site", ap.AUTHORIZED, 1, [auth]),
            _stub_adapter("marketplace", ap.UNVERIFIED, 4, [unver]),
        ]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)

        out = ap.run_search("Ray-Ban", "RB4105")
        # Both kept (different sources), AUTHORIZED brand-site sorts first.
        assert out["candidate_count"] == 2
        winner = out["candidates"][0]
        assert winner["source"] == "brand_site"
        assert winner["source_class"] == ap.AUTHORIZED
        assert out["candidates"][1]["source"] == "marketplace"

    def test_distinct_models_both_kept(self, monkeypatch):
        c1 = {
            "source": "s", "source_class": ap.AUTHORIZED, "url": None, "title": "a",
            "brand": "Ray-Ban", "model": "RB4105", "color": None, "size": None,
            "image_urls": [], "specs": {},
        }
        c2 = dict(c1)
        c2["model"] = "RB2140"
        reg = [_stub_adapter("s", ap.AUTHORIZED, 1, [c1, c2])]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)
        out = ap.run_search("Ray-Ban", "RB4105")
        assert out["candidate_count"] == 2

    def test_scored_and_sorted_desc_with_priority_tiebreak(self, monkeypatch):
        # Two different models so both survive dedupe; exact match should rank
        # above the partial match.
        exact = {
            "source": "s", "source_class": ap.AUTHORIZED, "url": None,
            "title": "exact", "brand": "Ray-Ban", "model": "RB4105",
            "color": None, "size": None, "image_urls": [], "specs": {},
        }
        partial = {
            "source": "s", "source_class": ap.AUTHORIZED, "url": None,
            "title": "partial", "brand": "Ray-Ban", "model": "RB2140",
            "color": None, "size": None, "image_urls": [], "specs": {},
        }
        reg = [_stub_adapter("s", ap.AUTHORIZED, 1, [partial, exact])]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)
        out = ap.run_search("Ray-Ban", "RB4105")
        scores = [c["score"] for c in out["candidates"]]
        assert scores == sorted(scores, reverse=True)
        assert out["candidates"][0]["title"] == "exact"
        assert out["candidates"][0]["score"] == 1.0

    def test_tiebreak_on_source_priority(self, monkeypatch):
        # SAME brand+model (so identical score 1.0) from two DIFFERENT sources
        # -> both survive dedupe; the lower-priority-number source must come
        # first on the score tie.
        hi = {
            "source": "hi", "source_class": ap.AUTHORIZED, "url": None, "title": "hi",
            "brand": "Ray-Ban", "model": "RB4105", "color": None, "size": None,
            "image_urls": [], "specs": {},
        }
        lo = {
            "source": "lo", "source_class": ap.AUTHORIZED, "url": None, "title": "lo",
            "brand": "Ray-Ban", "model": "RB4105", "color": None, "size": None,
            "image_urls": [], "specs": {},
        }
        # Hand them to run_search lo-first to prove the FINAL sort (not input
        # order) drives the result.
        reg = [
            _stub_adapter("lo", ap.AUTHORIZED, 4, [lo]),
            _stub_adapter("hi", ap.AUTHORIZED, 1, [hi]),
        ]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)
        out = ap.run_search("Ray-Ban", "RB4105")
        assert out["candidate_count"] == 2
        scores = [c["score"] for c in out["candidates"]]
        assert scores == [1.0, 1.0]
        # Tie broken by source priority: hi (priority 1) before lo (priority 4).
        assert [c["source"] for c in out["candidates"]] == ["hi", "lo"]

    def test_run_search_failsoft_when_one_adapter_raises(self, monkeypatch):
        good = {
            "source": "good", "source_class": ap.AUTHORIZED, "url": None,
            "title": "good", "brand": "Ray-Ban", "model": "RB4105",
            "color": None, "size": None, "image_urls": [], "specs": {},
        }
        bad = _stub_adapter("bad", ap.AUTHORIZED, 1, [])

        def boom(brand, model, color, size, limit):
            raise RuntimeError("explode")

        bad._search = boom  # type: ignore
        reg = [bad, _stub_adapter("good", ap.AUTHORIZED, 2, [good])]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)

        # The bad adapter raising internally must NOT break run_search.
        out = ap.run_search("Ray-Ban", "RB4105")
        assert out["candidate_count"] == 1
        assert out["candidates"][0]["source"] == "good"

    def test_run_search_failsoft_when_is_enabled_raises(self, monkeypatch):
        flaky = _stub_adapter("flaky", ap.AUTHORIZED, 1, [])

        def boom():
            raise RuntimeError("enabled-explode")

        flaky.is_enabled = boom  # type: ignore
        reg = [flaky]
        monkeypatch.setattr(ap, "build_registry", lambda: reg)
        out = ap.run_search("Ray-Ban", "RB4105")
        assert out["candidate_count"] == 0
        assert out["candidates"] == []

    def test_return_shape_is_exact(self, monkeypatch):
        monkeypatch.setattr(ap, "build_registry", lambda: [])
        out = ap.run_search("Ray-Ban", "RB4105", "Black", "50")
        assert set(out) == {"query", "candidates", "sources", "candidate_count"}
        assert out["query"] == {
            "brand": "Ray-Ban", "model": "RB4105", "color": "Black", "size": "50"
        }
        assert out["candidates"] == []
        assert out["candidate_count"] == 0
        assert out["sources"] == []


# ---------------------------------------------------------------------------
# Live adapters with httpx mocked (NO real network)
# ---------------------------------------------------------------------------

class TestBrandSiteAdapterMocked:
    def test_scrapes_candidate_from_mocked_brand_site(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, text=_SAMPLE_HTML)

        _install_mock_httpx(monkeypatch, handler)
        out = ap.BrandSiteAdapter().search("Ray-Ban", "RB4105")
        assert len(out) == 1
        cand = out[0]
        assert cand["source"] == "brand_site"
        assert cand["source_class"] == ap.AUTHORIZED
        assert "RB4105" in cand["title"]
        assert cand["specs"]["Frame material"] == "Acetate"
        assert cand["image_urls"]  # AUTHORIZED -> usable downstream
        # We hit the configured Ray-Ban India base.
        assert "ray-ban.com" in captured["url"]

    def test_unknown_brand_returns_empty(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")

        def handler(request):
            raise AssertionError("should not fetch for an unknown brand")

        _install_mock_httpx(monkeypatch, handler)
        assert ap.BrandSiteAdapter().search("NoSuchBrand", "X1") == []

    def test_http_error_is_failsoft(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")

        def handler(request):
            return httpx.Response(403, text="forbidden")

        _install_mock_httpx(monkeypatch, handler)
        assert ap.BrandSiteAdapter().search("Ray-Ban", "RB4105") == []

    def test_network_exception_is_failsoft(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")

        def handler(request):
            raise httpx.ConnectError("dns boom")

        _install_mock_httpx(monkeypatch, handler)
        assert ap.BrandSiteAdapter().search("Ray-Ban", "RB4105") == []


class TestMyLuxotticaAdapterMocked:
    def test_login_then_search_yields_candidate(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setenv("MYLUXOTTICA_USER", "dealer42")
        monkeypatch.setenv("MYLUXOTTICA_PASS", "secret")
        seen = {"login": False, "search": False}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                seen["login"] = True
                return httpx.Response(200, text="ok")
            seen["search"] = True
            return httpx.Response(200, text=_SAMPLE_HTML)

        _install_mock_httpx(monkeypatch, handler)
        out = ap.MyLuxotticaAdapter().search("Ray-Ban", "RB4105")
        assert seen["login"] and seen["search"]
        assert len(out) == 1
        assert out[0]["source"] == "myluxottica"
        assert out[0]["source_class"] == ap.AUTHORIZED

    def test_disabled_without_creds(self, monkeypatch):
        _clear_source_env(monkeypatch)
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        assert ap.MyLuxotticaAdapter().search("Ray-Ban", "RB4105") == []

    def test_failed_login_yields_empty(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setenv("MYLUXOTTICA_USER", "dealer42")
        monkeypatch.setenv("MYLUXOTTICA_PASS", "secret")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(401, text="nope")
            raise AssertionError("must not search after a failed login")

        _install_mock_httpx(monkeypatch, handler)
        assert ap.MyLuxotticaAdapter().search("Ray-Ban", "RB4105") == []


class TestMarketplaceAdapterMocked:
    def test_serp_results_parsed_specs_only(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setenv("SERP_API_KEY", "serp-key")
        body = (
            '{"organic_results": [{"title": "RB4105 on Amazon",'
            ' "link": "https://amazon.in/rb4105", "snippet": "Foldable Wayfarer",'
            ' "thumbnail": "https://amazon.in/rb4105.jpg"}]}'
        )

        def handler(request):
            return httpx.Response(200, text=body)

        _install_mock_httpx(monkeypatch, handler)
        out = ap.MarketplaceAdapter().search("Ray-Ban", "RB4105")
        assert len(out) == 1
        cand = out[0]
        assert cand["source"] == "marketplace"
        # CRITICAL copyright stance: marketplace source stays UNVERIFIED, so
        # even though it carries an image URL the gate must block auto-use.
        assert cand["source_class"] == ap.UNVERIFIED
        assert cand["image_urls"] == ["https://amazon.in/rb4105.jpg"]
        assert ap.image_use_allowed(cand["source_class"]) is False
        assert ap.image_use_allowed(cand["source_class"], rights_confirmed=True) is True
        # No specs scraped from a marketplace snippet.
        assert cand["specs"] == {}

    def test_cse_results_parsed(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setenv("GOOGLE_CSE_KEY", "k")
        monkeypatch.setenv("GOOGLE_CSE_CX", "cx")
        body = (
            '{"items": [{"title": "RB4105", "link": "https://x/rb4105",'
            ' "snippet": "snip", "pagemap": {"cse_image": [{"src": "https://x/i.jpg"}]}}]}'
        )

        def handler(request):
            return httpx.Response(200, text=body)

        _install_mock_httpx(monkeypatch, handler)
        out = ap.MarketplaceAdapter().search("Ray-Ban", "RB4105")
        assert len(out) == 1
        assert out[0]["image_urls"] == ["https://x/i.jpg"]
        assert out[0]["source_class"] == ap.UNVERIFIED

    def test_bad_json_is_failsoft(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setenv("SERP_API_KEY", "serp-key")

        def handler(request):
            return httpx.Response(200, text="<<<not json>>>")

        _install_mock_httpx(monkeypatch, handler)
        assert ap.MarketplaceAdapter().search("Ray-Ban", "RB4105") == []


# ---------------------------------------------------------------------------
# End-to-end: run_search with a mocked brand site (one real adapter path)
# ---------------------------------------------------------------------------

class TestRunSearchEndToEndMocked:
    def test_brand_site_candidate_flows_through_run_search(self, monkeypatch):
        _clear_source_env(monkeypatch)
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")

        def handler(request):
            return httpx.Response(200, text=_SAMPLE_HTML)

        _install_mock_httpx(monkeypatch, handler)
        out = ap.run_search("Ray-Ban", "RB4105")
        # Only brand_site is enabled (no creds/URLs for the others).
        assert out["candidate_count"] == 1
        cand = out["candidates"][0]
        assert cand["source"] == "brand_site"
        assert cand["score"] == 1.0  # brand + model both match
        assert cand["source_priority"] == 1
        # sources array still derived from the registry.
        assert {s["name"] for s in out["sources"]} == {
            "brand_site", "myluxottica", "internal_bvi", "marketplace"
        }
