"""
Audit L1-F41: a product image is stored only when its BYTES are an image.

Reproduction (audit security-abuse-tests SA09): HTML bytes uploaded to
POST /api/v1/products/image with the declared type image/jpeg returned 201
and were served byte-for-byte as a product image.

Covers the three doors that store kind="product_image":
  * the multipart upload (declared Content-Type is the client's claim),
  * the re-host from a URL (Content-Type is the remote server's claim),
  * the background-removal edit (bytes come back from the editor).
The stored and served type is what the bytes ARE, never the label.

Run: JWT_SECRET_KEY=test python -m pytest \
       backend/tests/test_audit_b04_image_upload_content.py -q
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import httpx  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from api.routers import products as products_router  # noqa: E402
from api.services import image_rehost  # noqa: E402
from api.services.file_store import InMemoryFileStore, set_file_store  # noqa: E402
from api.services.image_content import image_mime_of  # noqa: E402

_UPLOAD = "/api/v1/products/image"
_REHOST = "/api/v1/products/image/from-url"
_HTML = b"<html><body><script>window.marker=true</script></body></html>" * 4


def _real(fmt: str) -> bytes:
    """A small genuine image, encoded by Pillow."""
    buf = io.BytesIO()
    Image.effect_noise((64, 48), 60).convert("RGB").save(buf, fmt)
    return buf.getvalue()


def _box(kind: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def _bmff(major: bytes, *compatible: bytes) -> bytes:
    """A structurally valid ISO-BMFF image container (ftyp + meta + mdat).
    SYNTHETIC: it has the container shape of a HEIC/AVIF file, not decodable
    pixel data - the app ships no HEIC decoder to check that with."""
    ftyp = _box(b"ftyp", major + b"\x00\x00\x00\x00" + b"".join(compatible))
    return ftyp + _box(b"meta", b"\x00" * 40) + _box(b"mdat", b"\x01" * 200)


@pytest.fixture
def mem_store():
    store = InMemoryFileStore()
    set_file_store(store)
    yield store
    set_file_store(None)


def _upload(client, headers, content, declared, name="photo.jpg"):
    return client.post(
        _UPLOAD,
        headers=headers,
        files={"file": (name, io.BytesIO(content), declared)},
    )


def _stored(mem_store):
    return mem_store._files  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# The audit reproduction
# ---------------------------------------------------------------------------


def test_html_bytes_labelled_jpeg_are_refused_and_nothing_is_stored(
    client, auth_headers, mem_store
):
    r = _upload(client, auth_headers, _HTML, "image/jpeg")
    assert r.status_code == 400, r.text
    assert "not a valid image" in r.json()["detail"]
    assert _stored(mem_store) == {}


@pytest.mark.parametrize(
    "content",
    [
        b"\xff\xd8\xff\xe0" + _HTML,                # JPEG signature, then junk
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,        # PNG signature, then zeros
        b"RIFF\x10\x00\x00\x00WEBP" + _HTML,        # WebP signature, then junk
        b"GIF89a" + _HTML,
        b"%PDF-1.7 not an image",
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
    ],
)
def test_bytes_that_only_start_like_an_image_are_refused(
    client, auth_headers, mem_store, content
):
    r = _upload(client, auth_headers, content, "image/jpeg")
    assert r.status_code == 400, r.text
    assert _stored(mem_store) == {}


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_truncated_image_is_refused(client, auth_headers, mem_store, fmt):
    whole = _real(fmt)
    r = _upload(client, auth_headers, whole[: len(whole) // 2], f"image/{fmt.lower()}")
    assert r.status_code == 400, r.text
    assert _stored(mem_store) == {}


# ---------------------------------------------------------------------------
# Genuine images still upload, and are served as what they are
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fmt,mime",
    [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp"),
     ("GIF", "image/gif")],
)
def test_genuine_image_uploads_and_serves_back_identically(
    client, auth_headers, mem_store, fmt, mime
):
    content = _real(fmt)
    r = _upload(client, auth_headers, content, mime)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["content_type"] == mime and body["size"] == len(content)

    served = client.get(body["url"])
    assert served.status_code == 200
    assert served.content == content
    assert served.headers["content-type"].startswith(mime)
    assert served.headers.get("x-content-type-options") == "nosniff"


def test_jpg_alias_label_still_uploads(client, auth_headers, mem_store):
    r = _upload(client, auth_headers, _real("JPEG"), "image/jpg")
    assert r.status_code == 201, r.text
    assert r.json()["content_type"] == "image/jpeg"


def test_a_real_image_under_the_wrong_label_is_stored_as_what_it_is(
    client, auth_headers, mem_store
):
    """A PNG the browser called image/jpeg is a real photo: keep it, but
    store and serve the type the bytes are."""
    content = _real("PNG")
    r = _upload(client, auth_headers, content, "image/jpeg")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["content_type"] == "image/png"
    rec = _stored(mem_store)[body["file_id"]]
    assert rec["mime_type"] == "image/png"
    assert client.get(body["url"]).headers["content-type"].startswith("image/png")


@pytest.mark.parametrize(
    "content,mime",
    [
        (_bmff(b"heic", b"mif1", b"heic"), "image/heic"),
        (_bmff(b"mif1", b"mif1", b"heic"), "image/heic"),
        (_bmff(b"mif1", b"mif1", b"miaf"), "image/heif"),
        (_bmff(b"avif", b"mif1", b"miaf"), "image/avif"),
    ],
)
def test_phone_container_formats_upload(client, auth_headers, mem_store, content, mime):
    r = _upload(client, auth_headers, content, mime, name="IMG_0001.HEIC")
    assert r.status_code == 201, r.text
    assert r.json()["content_type"] == mime


def test_cut_short_or_non_image_container_is_refused(client, auth_headers, mem_store):
    heic = _bmff(b"heic", b"mif1", b"heic")
    mp4 = _box(b"ftyp", b"isom\x00\x00\x00\x00isommp42") + _box(b"moov", b"\x00" * 40)
    for content in (heic[:-50], heic[:24], mp4):
        r = _upload(client, auth_headers, content, "image/heic", name="x.heic")
        assert r.status_code == 400, r.text
    assert _stored(mem_store) == {}


# ---------------------------------------------------------------------------
# Controls that already held stay in place
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("declared", ["text/html", "image/svg+xml", "application/pdf"])
def test_declared_non_image_types_are_still_refused(
    client, auth_headers, mem_store, declared
):
    r = _upload(client, auth_headers, _real("PNG"), declared)
    assert r.status_code == 400, r.text
    assert _stored(mem_store) == {}


def test_empty_upload_is_still_refused(client, auth_headers, mem_store):
    r = _upload(client, auth_headers, b"", "image/png")
    assert r.status_code in (400, 422), r.text
    assert _stored(mem_store) == {}


def test_wrong_role_is_still_refused(client, staff_headers, mem_store):
    r = _upload(client, staff_headers, _real("PNG"), "image/png")
    assert r.status_code == 403, r.text
    assert _stored(mem_store) == {}


# ---------------------------------------------------------------------------
# Door 2: re-host from a URL (the remote server's Content-Type is a claim too)
# ---------------------------------------------------------------------------


def _remote(monkeypatch, body, content_type):
    monkeypatch.setattr(image_rehost, "_resolve_host", lambda host: ["93.184.216.34"])
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": content_type}, content=body
        )
    )
    real_client = httpx.Client

    class MockedClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", MockedClient)


def test_rehost_refuses_html_served_as_jpeg(client, auth_headers, mem_store, monkeypatch):
    _remote(monkeypatch, _HTML, "image/jpeg")
    r = client.post(
        _REHOST, headers=auth_headers, json={"url": "https://cdn.example/a.jpg"}
    )
    assert r.status_code == 400, r.text
    assert "not a valid image" in r.json()["detail"]
    assert _stored(mem_store) == {}


def test_rehost_stores_the_type_the_bytes_are(
    client, auth_headers, mem_store, monkeypatch
):
    content = _real("PNG")
    _remote(monkeypatch, content, "image/jpeg")
    r = client.post(
        _REHOST, headers=auth_headers, json={"url": "https://cdn.example/a"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["content_type"] == "image/png"
    assert body["filename"] == "a.png"
    assert _stored(mem_store)[body["file_id"]]["mime_type"] == "image/png"


# ---------------------------------------------------------------------------
# Door 3: the background-removal edit stores what the editor returned
# ---------------------------------------------------------------------------


class _Editor:
    def __init__(self, out):
        self.out = out

    def available(self):
        return True

    async def edit(self, raw, spec):
        return self.out


def _seed(mem_store):
    return mem_store.put(
        content=_real("PNG"),
        filename="orig.png",
        mime_type="image/png",
        metadata={"kind": "product_image"},
    )


def test_edit_refuses_a_non_image_editor_reply(
    client, auth_headers, mem_store, monkeypatch
):
    fid = _seed(mem_store)
    monkeypatch.setattr(
        products_router, "get_image_editor", lambda: _Editor(b'{"error":"quota"}')
    )
    r = client.post(f"/api/v1/products/image/{fid}/edit", headers=auth_headers)
    assert r.status_code == 502, r.text
    assert list(_stored(mem_store)) == [fid], "no second file is stored"


def test_edit_stores_the_type_the_editor_returned(
    client, auth_headers, mem_store, monkeypatch
):
    fid = _seed(mem_store)
    out = _real("WEBP")
    monkeypatch.setattr(products_router, "get_image_editor", lambda: _Editor(out))
    r = client.post(f"/api/v1/products/image/{fid}/edit", headers=auth_headers)
    assert r.status_code == 201, r.text
    assert _stored(mem_store)[r.json()["file_id"]]["mime_type"] == "image/webp"


# ---------------------------------------------------------------------------
# The shared rule itself
# ---------------------------------------------------------------------------


def test_image_mime_of_reads_the_bytes_not_a_label():
    assert image_mime_of(_real("JPEG")) == "image/jpeg"
    assert image_mime_of(_real("PNG")) == "image/png"
    assert image_mime_of(_real("WEBP")) == "image/webp"
    assert image_mime_of(_real("GIF")) == "image/gif"
    for junk in (b"", _HTML, b"\xff\xd8\xff", b"RIFF\x00\x00\x00\x00WEBP"):
        assert image_mime_of(junk) is None, junk[:12]


def test_a_large_jpeg_is_checked_without_a_full_size_decode():
    """12 megapixels: the check decodes at 1/8 scale (bounded memory)."""
    buf = io.BytesIO()
    Image.new("RGB", (4000, 3000), (120, 60, 30)).save(buf, "JPEG")
    assert image_mime_of(buf.getvalue()) == "image/jpeg"
