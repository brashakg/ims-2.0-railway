"""
Audit L1-F32 (alternate routes): EVERY door where the server reads a stored,
staff-typed image URL must use the same address check as the Add-Product
re-host - not only that one door.

Three sibling doors fetched the stored url with a plain HTTP client (no
scheme/host/private-address check, redirects followed) or opened it as a
local file path:

  * POST /online-store/images/{id}/auto-edit   (online_store_images
    ._fetch_image_bytes) - and it then SENT those bytes to the third-party
    photo editor;
  * the catalogue PDF / colour suggestion photo pre-fetch
    (catalogue_pdf.fetch_images);
  * the SUPERADMIN /uploads/ re-host tool (online_sync_health._fetch_bytes),
    which then published the bytes to object storage.

The "internal" target here is a loopback HTTP server: before the fix each door
reads it; after the fix none of them may reach it. No test touches a real
network.

Run: JWT_SECRET_KEY=test python -m pytest \
       backend/tests/test_audit_b04_stored_image_fetch.py -q
"""

import asyncio
import base64
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

import api.services.image_editor as ied  # noqa: E402
import api.services.object_storage as ost  # noqa: E402
from api.routers import online_store_images as imod  # noqa: E402
from api.services import catalogue_pdf, image_rehost, online_sync_health  # noqa: E402

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jF90A"
    "AAAASUVORK5CYII="
)
_SECRET = b"JWT_SECRET_KEY=not-an-image-internal-only"
USER = {"user_id": "u1", "roles": ["ADMIN"]}


class _Server:
    """A loopback HTTP server that records what reached it. `/secret` answers
    like an internal service (text), anything else answers a PNG."""

    def __init__(self):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                outer.requests.append(
                    {"path": self.path, "host": self.headers.get("Host")}
                )
                body = _SECRET if self.path.startswith("/secret") else _PNG
                self.send_response(200)
                # An internal service can label its answer anything it likes.
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.lower(), raising=False)
    srv = _Server()
    yield srv
    srv.close()


def _as_public_host(monkeypatch, host):
    """Let the loopback server play a public host called `host`: the SSRF check
    is told the name passed. Only for the 'a genuine photo still loads' tests -
    the real predicate is covered in test_audit_b04_image_fetch_binding.py."""
    monkeypatch.setattr(image_rehost, "_resolve_host", lambda h: ["127.0.0.1"])
    monkeypatch.setattr(image_rehost, "_is_blocked_ip", lambda ip: False)
    return host


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    """A server working directory holding one uploaded photo and one file that
    is NOT an upload (the stand-in for /proc/self/environ, a .env, a key)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IMAGE_LOCAL_DIR", raising=False)
    (tmp_path / "uploads" / "edited" / "p1").mkdir(parents=True)
    (tmp_path / "uploads" / "edited" / "p1" / "photo.png").write_bytes(_PNG)
    (tmp_path / "uploads" / "edited" / "p1" / "notes.txt").write_bytes(_SECRET)
    (tmp_path / "secret.env").write_bytes(_SECRET)
    return tmp_path


# ---------------------------------------------------------------------------
# Door 1: the design-queue auto-edit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:{port}/secret",
        "http://localhost:{port}/secret",
        "http://[::ffff:127.0.0.1]:{port}/secret",
    ],
)
def test_auto_edit_fetch_refuses_an_internal_address(server, url):
    with pytest.raises(image_rehost.ImageFetchError):
        asyncio.run(imod._fetch_image_bytes(url.format(port=server.port)))
    assert server.requests == [], "the internal server must never be reached"


def test_auto_edit_fetch_refuses_the_cloud_metadata_address_without_a_connect(
    monkeypatch,
):
    """The reviewer's exact url. No connect is attempted at all: the HTTP
    client is replaced by one that fails the test if it is ever built."""
    import httpx

    def _no_client(*args, **kwargs):
        raise AssertionError("an HTTP client was opened for a blocked address")

    monkeypatch.setattr(httpx, "AsyncClient", _no_client)
    monkeypatch.setattr(httpx, "Client", _no_client)
    with pytest.raises(image_rehost.ImageFetchError) as ei:
        asyncio.run(
            imod._fetch_image_bytes("http://169.254.169.254/latest/meta-data/")
        )
    assert ei.value.status == 400


@pytest.mark.parametrize(
    "path",
    [
        "secret.env",
        "/secret.env",
        "../secret.env",
        "/uploads/../secret.env",
        "/uploads/edited/../../secret.env",
        "file:///secret.env",
    ],
)
def test_auto_edit_fetch_refuses_a_local_path_outside_the_uploads_folder(
    uploads, path
):
    with pytest.raises(image_rehost.ImageFetchError):
        asyncio.run(imod._fetch_image_bytes(path))


def test_auto_edit_fetch_refuses_an_absolute_path(uploads):
    with pytest.raises(image_rehost.ImageFetchError):
        asyncio.run(imod._fetch_image_bytes(str(uploads / "secret.env")))


def test_auto_edit_fetch_refuses_an_upload_that_is_not_an_image(uploads):
    """Inside the folder is not enough: only image bytes may go to the editor."""
    with pytest.raises(image_rehost.ImageFetchError):
        asyncio.run(imod._fetch_image_bytes("/uploads/edited/p1/notes.txt"))


def test_auto_edit_fetch_still_reads_a_locally_stored_photo(uploads):
    """The url LocalDiskStorage.put hands back keeps working."""
    url = ost.LocalDiskStorage().put("p1/photo.png", _PNG, "image/png")
    assert url == "/uploads/edited/p1/photo.png"
    assert asyncio.run(imod._fetch_image_bytes(url)) == _PNG


def test_auto_edit_fetch_still_reads_a_public_photo(server, monkeypatch):
    host = _as_public_host(monkeypatch, "cdn.audit-b04.invalid")
    got = asyncio.run(imod._fetch_image_bytes(f"http://{host}:{server.port}/a.png"))
    assert got == _PNG
    assert server.requests == [
        {"path": "/a.png", "host": f"{host}:{server.port}"}
    ], "one request, carrying the original hostname"


def test_auto_edit_fetch_refuses_a_public_url_that_is_not_an_image(
    server, monkeypatch
):
    """A 200 labelled image/png whose body is text is not a photo."""
    host = _as_public_host(monkeypatch, "cdn.audit-b04.invalid")
    with pytest.raises(image_rehost.ImageFetchError):
        asyncio.run(imod._fetch_image_bytes(f"http://{host}:{server.port}/secret"))


class _Repo:
    def __init__(self, img):
        self.img = dict(img)

    def get_by_id(self, _iid):
        return dict(self.img)

    def set_status(self, _iid, target, by=None):  # noqa: ARG002
        self.img["status"] = target
        return dict(self.img)

    def attach_edited(self, _iid, url, by=None):  # noqa: ARG002
        self.img.update(status="REVIEW", edited_url=url, kind="EDITED")
        return dict(self.img)


class _Editor:
    name = "fake"

    def __init__(self):
        self.received = []

    def available(self):
        return True

    async def edit(self, raw, spec):  # noqa: ARG002
        self.received.append(raw)
        return b"EDITED"


class _Storage:
    name = "fakestore"

    def __init__(self):
        self.puts = []

    def available(self):
        return True

    def put(self, key, data, content_type="image/png"):  # noqa: ARG002
        self.puts.append(key)
        return f"https://cdn.example/{key}"


@pytest.mark.parametrize("stored", ["http://127.0.0.1:{port}/secret", "/uploads/../secret.env"])
def test_auto_edit_route_sends_nothing_internal_to_the_editor(
    server, uploads, monkeypatch, stored
):
    """The reviewer's two steps end to end: a queued image whose url is an
    internal address (or a server file), then auto-edit. Nothing may reach the
    third-party editor and nothing may be stored as the 'edited' photo."""
    repo = _Repo(
        {
            "image_id": "IMG1",
            "product_id": "P1",
            "status": "QUEUED",
            "url": stored.format(port=server.port),
        }
    )
    editor, storage = _Editor(), _Storage()
    monkeypatch.setattr(imod, "_require_repo", lambda: repo)
    monkeypatch.setattr(ied, "get_image_editor", lambda: editor)
    monkeypatch.setattr(ost, "get_object_storage", lambda: storage)

    out = asyncio.run(imod.auto_edit_image("IMG1", current_user=USER))

    assert out["auto_edit"] == "failed"
    assert editor.received == [], "internal bytes were sent to the photo editor"
    assert storage.puts == []
    assert server.requests == []
    assert _SECRET.decode() not in str(out)
    assert not repo.img.get("edited_url")


# ---------------------------------------------------------------------------
# Door 2: the catalogue PDF / colour-suggestion photo pre-fetch
# ---------------------------------------------------------------------------


def test_catalogue_photo_fetch_refuses_an_internal_address(server):
    rows = [
        {"image": f"http://127.0.0.1:{server.port}/a.png"},
        {"image": f"http://localhost:{server.port}/a.png"},
    ]
    assert asyncio.run(catalogue_pdf.fetch_images(rows)) == {}
    assert server.requests == [], "the internal server must never be reached"


def test_catalogue_photo_fetch_still_reads_a_public_photo(server, monkeypatch):
    host = _as_public_host(monkeypatch, "cdn.audit-b04.invalid")
    rows = [{"image": f"http://{host}:{server.port}/a.png"}, {"image": ""}]
    assert asyncio.run(catalogue_pdf.fetch_images(rows)) == {0: _PNG}


# ---------------------------------------------------------------------------
# Door 3: the SUPERADMIN /uploads/ re-host tool
# ---------------------------------------------------------------------------


def test_uploads_rehost_refuses_an_internal_address(server):
    with pytest.raises(image_rehost.ImageFetchError):
        online_sync_health._fetch_bytes(f"http://127.0.0.1:{server.port}/secret")
    assert server.requests == []


@pytest.mark.parametrize(
    "path", ["/uploads/../secret.env", "../secret.env", "secret.env"]
)
def test_uploads_rehost_refuses_a_path_outside_the_uploads_folder(uploads, path):
    with pytest.raises(image_rehost.ImageFetchError):
        online_sync_health._fetch_bytes(path)


def test_uploads_rehost_still_reads_an_uploaded_file(uploads):
    assert online_sync_health._fetch_bytes("/uploads/edited/p1/photo.png") == _PNG


# ---------------------------------------------------------------------------
# Decision D12: the refusal stays at READ time (no new rule when a url is
# saved). So the message at use must say, in plain words, WHY the image was
# not fetched - at every door that reads a stored url.
# ---------------------------------------------------------------------------

_PLAIN = "Image not fetched: "
_PRIVATE = "private or internal network address"


def _refusal(url, max_bytes=1024 * 1024):
    with pytest.raises(image_rehost.ImageFetchError) as ei:
        image_rehost.read_stored_image(url, max_bytes=max_bytes)
    return ei.value.detail


@pytest.mark.parametrize(
    "url,why",
    [
        ("http://127.0.0.1:{port}/a.png", _PRIVATE),
        ("http://localhost:{port}/a.png", _PRIVATE),
        ("http://169.254.169.254/latest/meta-data/", _PRIVATE),
        ("http://[::ffff:10.0.0.5]/a.png", _PRIVATE),
        ("https:///a.png", "has no host name"),
        ("", "no web address is saved"),
    ],
)
def test_a_refused_read_says_plainly_why(server, url, why):
    detail = _refusal(url.format(port=server.port))
    assert detail.startswith(_PLAIN), detail
    assert why in detail, detail
    assert server.requests == []


def test_a_refused_web_address_is_still_called_blocked(server):
    """The word the Add-Product screen and its tests already key on."""
    assert "(blocked)" in _refusal(f"http://127.0.0.1:{server.port}/a.png")


def test_a_non_web_scheme_is_refused_in_plain_words():
    with pytest.raises(image_rehost.ImageFetchError) as ei:
        image_rehost.fetch_external_image(
            "ftp://cdn.example/a.png", allowed_mimes=None, max_bytes=1024
        )
    assert ei.value.detail.startswith(_PLAIN)
    assert "http:// or https://" in ei.value.detail


def test_an_unknown_host_is_refused_in_plain_words(monkeypatch):
    import socket

    def no_such_host(*a, **kw):
        raise socket.gaierror("synthetic: no such host")

    monkeypatch.setattr(socket, "getaddrinfo", no_such_host)
    detail = _refusal("https://no-such-host.audit-b04.invalid/a.png")
    assert detail.startswith(_PLAIN)
    assert "no-such-host.audit-b04.invalid" in detail and "could not be found" in detail


def test_a_file_outside_the_uploads_folder_is_refused_in_plain_words(uploads):
    for path in ("/uploads/../secret.env", "secret.env"):
        detail = _refusal(path)
        assert detail.startswith(_PLAIN), detail
        assert "uploads folder" in detail
        assert "secret" not in detail.lower()  # never echoes what it would not read


def test_an_oversize_or_empty_or_failed_download_says_which(server, monkeypatch):
    host = _as_public_host(monkeypatch, "cdn.audit-b04.invalid")
    detail = _refusal(f"http://{host}:{server.port}/a.png", max_bytes=10)
    assert detail.startswith(_PLAIN) and "larger than" in detail and "cap" in detail
    # Nothing listening on the port: a network failure, said as one.
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    detail = _refusal(f"http://{host}:{dead_port}/a.png")
    assert detail.startswith(_PLAIN) and "could not be reached" in detail


@pytest.mark.parametrize(
    "stored,why",
    [
        ("http://127.0.0.1:{port}/secret", _PRIVATE),
        ("/uploads/../secret.env", "uploads folder"),
    ],
)
def test_the_auto_edit_reply_carries_the_plain_reason(
    server, uploads, monkeypatch, stored, why
):
    repo = _Repo({"image_id": "IMG1", "product_id": "P1", "status": "QUEUED",
                  "url": stored.format(port=server.port)})
    monkeypatch.setattr(imod, "_require_repo", lambda: repo)
    monkeypatch.setattr(ied, "get_image_editor", lambda: _Editor())
    monkeypatch.setattr(ost, "get_object_storage", lambda: _Storage())

    out = asyncio.run(imod.auto_edit_image("IMG1", current_user=USER))

    assert out["auto_edit"] == "failed"
    assert out["reason"].startswith(_PLAIN) and why in out["reason"], out["reason"]


def test_the_auto_edit_reply_says_when_the_address_is_not_an_image(
    server, uploads, monkeypatch
):
    host = _as_public_host(monkeypatch, "cdn.audit-b04.invalid")
    repo = _Repo({"image_id": "IMG1", "product_id": "P1", "status": "QUEUED",
                  "url": f"http://{host}:{server.port}/secret"})
    monkeypatch.setattr(imod, "_require_repo", lambda: repo)
    monkeypatch.setattr(ied, "get_image_editor", lambda: _Editor())
    monkeypatch.setattr(ost, "get_object_storage", lambda: _Storage())

    out = asyncio.run(imod.auto_edit_image("IMG1", current_user=USER))

    assert out["auto_edit"] == "failed"
    assert out["reason"].startswith(_PLAIN) and "did not return an image" in out["reason"]


def test_the_catalogue_photo_fetch_records_why_a_photo_is_missing(server, caplog):
    """The PDF has nowhere to show a message: the photo is left out, and the
    reason is written to the server log instead of being swallowed."""
    import logging

    rows = [{"image": f"http://127.0.0.1:{server.port}/a.png"}]
    with caplog.at_level(logging.WARNING, logger=catalogue_pdf.__name__):
        assert asyncio.run(catalogue_pdf.fetch_images(rows)) == {}
    said = " ".join(r.getMessage() for r in caplog.records)
    assert _PLAIN in said and _PRIVATE in said


def test_the_uploads_rehost_tool_reports_the_plain_reason(server):
    with pytest.raises(image_rehost.ImageFetchError) as ei:
        online_sync_health._fetch_bytes(f"http://127.0.0.1:{server.port}/secret")
    assert str(ei.value).startswith(_PLAIN) and _PRIVATE in str(ei.value)


def test_no_refusal_text_is_left_unexplained():
    """Every refusal image_rehost can raise starts the same plain way."""
    import inspect
    import re

    source = inspect.getsource(image_rehost)
    raised = re.findall(r"ImageFetchError\(\s*\d{3},\s*f?\"([^\"]*)", source)
    assert len(raised) >= 8, raised
    assert [text for text in raised if not text.startswith(_PLAIN)] == []
    # ...and the refusals kept as shared constants.
    for text in (
        image_rehost._PRIVATE_ADDRESS,
        image_rehost._UNREACHABLE,
        image_rehost._NOT_AN_UPLOAD,
        image_rehost._too_big(25 * 1024 * 1024),
    ):
        assert text.startswith(_PLAIN), text
