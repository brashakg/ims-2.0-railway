"""
Tests for the Autopilot v2 image RE-HOST endpoint
(POST /api/v1/products/image/from-url) + its SSRF-hardened fetcher
(api/services/image_rehost.py).

Covers, over a TestClient with an in-memory file store (no live Mongo/GridFS)
and httpx fully mocked (NO real network):
  * RBAC: route catalogued in rbac_policy (catalog roles) and SALES_STAFF 403.
  * SSRF: private/loopback/link-local/metadata IP literals -> 400 and the
    network is NEVER touched; a hostname resolving to a private IP -> 400;
    a redirect hop into a private range -> 400; redirect-loop cap -> 400;
    non-http schemes -> 400.
  * Content guards: non-image Content-Type -> 400; oversize (declared and
    mid-stream) -> 400; empty body -> 400.
  * Happy path: fetch stores kind="product_image" (+ source_url audit) and
    the returned self-hosted url serves the same bytes back.

Run: JWT_SECRET_KEY=test python -m pytest \
       backend/tests/test_product_image_rehost.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import httpx  # noqa: E402
import pytest  # noqa: E402

from api.services import image_rehost  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402
from api.services.file_store import InMemoryFileStore, set_file_store  # noqa: E402

_REHOST_PATH = "/api/v1/products/image/from-url"
_CATALOG_SET = {"ADMIN", "CATALOG_MANAGER"}

_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 128


@pytest.fixture
def mem_store():
    store = InMemoryFileStore()
    set_file_store(store)
    yield store
    set_file_store(None)


def _install_mock_httpx(monkeypatch, handler):
    """Route every httpx.Client through a MockTransport (no real network)."""
    transport = httpx.MockTransport(handler)
    original_client = httpx.Client

    class _TestClient(original_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _TestClient)


def _no_network(monkeypatch):
    """A transport whose handler explodes — proves a blocked URL is never fetched."""

    def handler(request):  # pragma: no cover - must not run
        raise AssertionError(f"network must not be touched: {request.url}")

    _install_mock_httpx(monkeypatch, handler)


def _resolve_public(monkeypatch, mapping=None):
    """Monkeypatch DNS so tests never resolve real hostnames. Defaults every
    host to a public documentation address."""
    table = mapping or {}

    def fake_resolve(host):
        return table.get(host, ["93.184.216.34"])

    monkeypatch.setattr(image_rehost, "_resolve_host", fake_resolve)


# ---------------------------------------------------------------------------
# RBAC catalogue + role gate
# ---------------------------------------------------------------------------


def test_rehost_route_catalogued_with_catalog_roles():
    entry = rbac.policy_for("POST", _REHOST_PATH)
    assert entry is not None, "from-url route not catalogued in rbac_policy"
    assert set(entry["allowed"]) == _CATALOG_SET
    assert str(entry["path"]).endswith("/image/from-url")


def test_rehost_check_access_allows_catalog_denies_others():
    for role in ("SUPERADMIN", "ADMIN", "CATALOG_MANAGER"):
        assert rbac.check_access("POST", _REHOST_PATH, [role]) is True, role
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF"):
        assert rbac.check_access("POST", _REHOST_PATH, [role]) is False, role


def test_rehost_rbac_denied_below_catalog_roles(client, staff_headers, mem_store):
    r = client.post(
        _REHOST_PATH, headers=staff_headers, json={"url": "https://x.example/a.jpg"}
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# SSRF guards (pure + endpoint level). The mock transport RAISES, proving a
# blocked URL is refused BEFORE any fetch happens.
# ---------------------------------------------------------------------------


def test_blocked_ip_predicate_covers_the_classic_ranges():
    for ip in (
        "127.0.0.1",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.10",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
    ):
        assert image_rehost._is_blocked_ip(ip) is True, ip
    for ip in ("93.184.216.34", "1.1.1.1", "2606:2800:220:1:248:1893:25c8:1946"):
        assert image_rehost._is_blocked_ip(ip) is False, ip
    # Unparsable -> blocked (fail closed).
    assert image_rehost._is_blocked_ip("not-an-ip") is True


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/img.jpg",
        "http://10.0.0.5/img.jpg",
        "http://172.16.0.1/img.jpg",
        "http://192.168.1.10/img.jpg",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/img.jpg",
    ],
)
def test_private_ip_literal_is_blocked_without_fetch(
    client, auth_headers, mem_store, monkeypatch, url
):
    _no_network(monkeypatch)
    r = client.post(_REHOST_PATH, headers=auth_headers, json={"url": url})
    assert r.status_code == 400, r.text
    assert "blocked" in r.json()["detail"].lower()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://host/img.jpg",
        "data:image/png;base64,AAAA",
        "not a url",
    ],
)
def test_non_http_schemes_are_blocked(
    client, auth_headers, mem_store, monkeypatch, url
):
    _no_network(monkeypatch)
    r = client.post(_REHOST_PATH, headers=auth_headers, json={"url": url})
    assert r.status_code == 400, r.text


def test_hostname_resolving_to_private_ip_is_blocked(
    client, auth_headers, mem_store, monkeypatch
):
    _no_network(monkeypatch)
    _resolve_public(monkeypatch, {"internal.example": ["10.0.0.7"]})
    r = client.post(
        _REHOST_PATH,
        headers=auth_headers,
        json={"url": "https://internal.example/a.jpg"},
    )
    assert r.status_code == 400, r.text
    assert "blocked" in r.json()["detail"].lower()


def test_mixed_resolution_one_private_record_blocks(monkeypatch):
    # DNS answer with one public + one private address -> fail closed.
    _resolve_public(monkeypatch, {"tricky.example": ["93.184.216.34", "192.168.0.9"]})
    with pytest.raises(image_rehost.ImageFetchError) as ei:
        image_rehost.assert_url_allowed("https://tricky.example/a.jpg")
    assert ei.value.status == 400


def test_redirect_into_private_range_is_blocked(
    client, auth_headers, mem_store, monkeypatch
):
    _resolve_public(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/x"})
        raise AssertionError("must not fetch the private hop")

    _install_mock_httpx(monkeypatch, handler)
    r = client.post(
        _REHOST_PATH, headers=auth_headers, json={"url": "https://cdn.example/a.jpg"}
    )
    assert r.status_code == 400, r.text


def test_redirect_loop_capped(client, auth_headers, mem_store, monkeypatch):
    _resolve_public(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://cdn.example/next.jpg"})

    _install_mock_httpx(monkeypatch, handler)
    r = client.post(
        _REHOST_PATH, headers=auth_headers, json={"url": "https://cdn.example/a.jpg"}
    )
    assert r.status_code == 400, r.text
    assert "redirect" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Content guards
# ---------------------------------------------------------------------------


def test_non_image_content_type_rejected(client, auth_headers, mem_store, monkeypatch):
    _resolve_public(monkeypatch)

    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"<html>"
        )

    _install_mock_httpx(monkeypatch, handler)
    r = client.post(
        _REHOST_PATH, headers=auth_headers, json={"url": "https://cdn.example/a.jpg"}
    )
    assert r.status_code == 400, r.text
    assert "image" in r.json()["detail"].lower()


def test_declared_oversize_rejected(client, auth_headers, mem_store, monkeypatch):
    _resolve_public(monkeypatch)
    huge = str(300 * 1024 * 1024)  # 300 MB declared

    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg", "content-length": huge},
            content=b"",
        )

    _install_mock_httpx(monkeypatch, handler)
    r = client.post(
        _REHOST_PATH, headers=auth_headers, json={"url": "https://cdn.example/a.jpg"}
    )
    assert r.status_code == 400, r.text
    assert "cap" in r.json()["detail"].lower()


def test_streaming_oversize_aborts(monkeypatch):
    # Unit-level: a body larger than max_bytes aborts DURING the stream.
    _resolve_public(monkeypatch)

    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "image/jpeg"}, content=b"x" * 64
        )

    _install_mock_httpx(monkeypatch, handler)
    with pytest.raises(image_rehost.ImageFetchError) as ei:
        image_rehost.fetch_external_image(
            "https://cdn.example/a.jpg",
            allowed_mimes=frozenset({"image/jpeg"}),
            max_bytes=10,
        )
    assert ei.value.status == 400
    assert "cap" in ei.value.detail.lower()


def test_empty_body_rejected(client, auth_headers, mem_store, monkeypatch):
    _resolve_public(monkeypatch)

    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"")

    _install_mock_httpx(monkeypatch, handler)
    r = client.post(
        _REHOST_PATH, headers=auth_headers, json={"url": "https://cdn.example/a.png"}
    )
    assert r.status_code == 400, r.text


def test_upstream_error_maps_to_502(client, auth_headers, mem_store, monkeypatch):
    _resolve_public(monkeypatch)

    def handler(request):
        return httpx.Response(404, content=b"nope")

    _install_mock_httpx(monkeypatch, handler)
    r = client.post(
        _REHOST_PATH, headers=auth_headers, json={"url": "https://cdn.example/a.jpg"}
    )
    assert r.status_code == 502, r.text


def test_blank_url_rejected(client, auth_headers, mem_store):
    r = client.post(_REHOST_PATH, headers=auth_headers, json={"url": "  "})
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# Happy path: fetch -> store (kind=product_image + source_url) -> serve back
# ---------------------------------------------------------------------------


def test_happy_path_stores_and_serves(client, auth_headers, mem_store, monkeypatch):
    _resolve_public(monkeypatch)

    def handler(request):
        assert request.url.host == "cdn.example"
        return httpx.Response(
            200, headers={"content-type": "image/jpeg"}, content=_JPEG_BYTES
        )

    _install_mock_httpx(monkeypatch, handler)
    r = client.post(
        _REHOST_PATH,
        headers=auth_headers,
        json={"url": "https://cdn.example/photos/rb4105-black.jpg"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["file_id"]
    assert body["url"] == f"/api/v1/products/image/{body['file_id']}"
    assert body["content_type"] == "image/jpeg"
    assert body["size"] == len(_JPEG_BYTES)
    assert body["filename"] == "rb4105-black.jpg"

    # Stored with the product_image kind + the source_url audit stamp.
    rec = mem_store._files[body["file_id"]]  # type: ignore[attr-defined]
    assert rec["metadata"]["kind"] == "product_image"
    assert (
        rec["metadata"]["source_url"] == "https://cdn.example/photos/rb4105-black.jpg"
    )

    # The self-hosted url serves the identical bytes back (public serve).
    served = client.get(body["url"])
    assert served.status_code == 200
    assert served.content == _JPEG_BYTES


def test_follows_safe_redirect_then_stores(
    client, auth_headers, mem_store, monkeypatch
):
    _resolve_public(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old.jpg":
            return httpx.Response(301, headers={"location": "/photos/new.jpg"})
        return httpx.Response(
            200, headers={"content-type": "image/png"}, content=_JPEG_BYTES
        )

    _install_mock_httpx(monkeypatch, handler)
    r = client.post(
        _REHOST_PATH, headers=auth_headers, json={"url": "https://cdn.example/old.jpg"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["filename"] == "new.jpg"


def test_filename_from_url_fallbacks():
    assert (
        image_rehost.filename_from_url("https://x/a/b/photo.png", "image/png")
        == "photo.png"
    )
    assert (
        image_rehost.filename_from_url("https://x/a/b/photo", "image/jpeg")
        == "photo.jpg"
    )
    assert (
        image_rehost.filename_from_url("https://x/", "image/webp")
        == "autopilot-image.webp"
    )
    # Path traversal / odd chars are stripped.
    assert "/" not in image_rehost.filename_from_url(
        "https://x/..%2F..%2Fetc", "image/png"
    )
