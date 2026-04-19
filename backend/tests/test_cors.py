"""
IMS 2.0 — CORS regression tests
================================
These tests lock down the CORS permanent fix. The frontend is free to add
new custom headers without coordinating a backend release: the server
reflects whatever the browser asks for, as long as the origin is on the
allow-list.

If one of these tests breaks, a legitimate browser request will be
refused with "Network error" before even reaching the server. Don't
"fix" by narrowing allow_headers — that's the exact regression we're
guarding against.
"""

import pytest

# Vercel prod + any *.vercel.app preview should pass; random origin should fail.
ALLOWED_ORIGINS = [
    "https://ims-2-0-railway.vercel.app",
    "https://ims-20-railway.vercel.app",
    "https://some-preview-abc123.vercel.app",  # matches *.vercel.app rule
    "http://localhost:3000",
    "http://localhost:5173",
]

FORBIDDEN_ORIGINS = [
    "https://evil.example.com",
    "http://attacker.local",
]


class TestCORSPreflight:
    """Preflight (OPTIONS) must succeed for allowed origins and reflect headers."""

    @pytest.mark.parametrize("origin", ALLOWED_ORIGINS)
    def test_preflight_allows_known_headers(self, client, origin):
        """Standard headers (Content-Type, Authorization) work."""
        resp = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type, authorization",
            },
        )
        assert resp.status_code == 200, (
            f"CORS preflight should succeed for {origin}; got {resp.status_code}"
        )
        assert resp.headers.get("Access-Control-Allow-Origin") == origin
        assert resp.headers.get("Access-Control-Allow-Credentials") == "true"
        # Crucial: allow-headers must include every header the browser asked for.
        allow = (resp.headers.get("Access-Control-Allow-Headers") or "").lower()
        assert "content-type" in allow
        assert "authorization" in allow

    @pytest.mark.parametrize("custom_header", [
        "x-retry-count",      # the header that broke prod
        "x-request-id",       # tracing
        "x-client-version",   # version pinning
        "x-some-new-header",  # forward-looking: any future header must work
    ])
    def test_preflight_reflects_custom_headers(self, client, custom_header):
        """Permanent fix: server reflects ANY custom header from allowed origins.

        If this test starts failing, do NOT add the header to a static list —
        fix the reflection logic in main.py's dynamic_cors_handler so future
        custom headers keep working.
        """
        requested = f"content-type, {custom_header}"
        resp = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "https://ims-2-0-railway.vercel.app",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": requested,
            },
        )
        assert resp.status_code == 200, (
            f"Preflight must not 400 on unknown custom header {custom_header!r}"
        )
        allow = (resp.headers.get("Access-Control-Allow-Headers") or "").lower()
        assert custom_header in allow, (
            f"Server must reflect {custom_header!r} in Access-Control-Allow-Headers; "
            f"got {allow!r}"
        )

    def test_preflight_without_request_headers_still_ok(self, client):
        """Some tools preflight without naming headers — should still succeed."""
        resp = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "https://ims-2-0-railway.vercel.app",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("Access-Control-Allow-Origin") == "https://ims-2-0-railway.vercel.app"

    @pytest.mark.parametrize("origin", FORBIDDEN_ORIGINS)
    def test_preflight_from_unknown_origin_not_allowed(self, client, origin):
        """Origins not on the whitelist don't get CORS approval.
        (The request may return 200/405 depending on route, but Access-Control-
        Allow-Origin must NOT be set — that's the browser-level refusal.)
        """
        resp = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        # The key assertion: no Access-Control-Allow-Origin header → browser
        # will block the request regardless of status code.
        assert resp.headers.get("Access-Control-Allow-Origin") != origin, (
            f"Unknown origin {origin} should not receive CORS allow"
        )


class TestCORSResponseHeaders:
    """Regular (non-preflight) responses need CORS headers too."""

    def test_health_response_has_cors_for_allowed_origin(self, client):
        resp = client.get("/health", headers={"Origin": "https://ims-2-0-railway.vercel.app"})
        assert resp.status_code == 200
        assert resp.headers.get("Access-Control-Allow-Origin") == "https://ims-2-0-railway.vercel.app"
        assert resp.headers.get("Access-Control-Allow-Credentials") == "true"

    def test_health_response_no_cors_for_unknown_origin(self, client):
        resp = client.get("/health", headers={"Origin": "https://evil.example.com"})
        assert resp.status_code == 200
        # Response still served (server-level), but browser won't accept it cross-origin.
        assert resp.headers.get("Access-Control-Allow-Origin") != "https://evil.example.com"

    def test_vary_header_set_on_cross_origin_response(self, client):
        """Vary: Origin prevents CDN/browser cache from mixing cross-origin responses."""
        resp = client.get("/health", headers={"Origin": "https://ims-2-0-railway.vercel.app"})
        vary = (resp.headers.get("Vary") or "").lower()
        assert "origin" in vary, f"Vary should include Origin; got {vary!r}"
