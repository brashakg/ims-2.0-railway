"""
Audit L1-F41: a product image is stored only when its BYTES are an image.

Reproduction (audit security-abuse-tests SA09): HTML bytes uploaded to
POST /api/v1/products/image with the declared type image/jpeg returned 201
and were served byte-for-byte as a product image.

Covers the three doors that store kind="product_image":
  * the multipart upload (declared Content-Type is the client's claim),
  * the re-host from a URL (Content-Type is the remote server's claim),
  * the background-removal edit (bytes come back from the editor),
and the three that copy a product photo into image storage:
  * the SUPERADMIN /uploads/ re-host tool,
  * the design-queue auto-edit,
  * the operator script scripts/rehost_bvi_uploads.py.
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

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
    ),
)

import asyncio  # noqa: E402

import httpx  # noqa: E402
import mongomock  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402

import api.services.image_editor as ied  # noqa: E402
import api.services.object_storage as ost  # noqa: E402
import rehost_bvi_uploads as rehost_script  # noqa: E402
from api.routers import online_store_images as queue_router  # noqa: E402
from api.routers import products as products_router  # noqa: E402
from api.services import image_rehost, online_sync_health  # noqa: E402
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
# Door 4: the SUPERADMIN /uploads/ re-host tool (rehost_uploads_images)
# Door 5: the design-queue auto-edit (POST /online-store/images/{id}/auto-edit)
# Door 6: the operator script scripts/rehost_bvi_uploads.py
# Each copies fetched / returned bytes into image storage and points the image
# row at the copy. A parked or soft-404 page (the old uploads host is retired)
# or an editor error body must not become "the photo".
# ---------------------------------------------------------------------------


class _Bucket:
    name = "s3"

    def __init__(self):
        self.puts = []

    def available(self):
        return True

    def put(self, key, data, content_type="image/png"):
        self.puts.append((key, content_type))
        return f"https://cdn.example/{key}"


def _uploads_row(tmp_path, monkeypatch, body):
    """One image row on /uploads/x.jpg whose file holds `body`, read through
    the tool's real fetch (no BVI_UPLOADS_BASE_URL: the local uploads folder)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IMAGE_LOCAL_DIR", raising=False)
    monkeypatch.delenv("BVI_UPLOADS_BASE_URL", raising=False)
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "x.jpg").write_bytes(body)
    db = mongomock.MongoClient()["audit_b04_rehost"]
    db["product_images"].insert_one(
        {"image_id": "IMG1", "sku": "SKU1", "url": "/uploads/x.jpg"}
    )
    bucket = _Bucket()
    monkeypatch.setattr(ost, "get_object_storage", lambda: bucket)
    return db, bucket


def test_uploads_rehost_does_not_store_a_page_that_is_not_an_image(
    tmp_path, monkeypatch
):
    db, bucket = _uploads_row(tmp_path, monkeypatch, _HTML)
    out = online_sync_health.rehost_uploads_images(db, dry_run=False)
    assert (out["rehosted"], out["failed"]) == (0, 1), out
    assert bucket.puts == []
    assert "not an image" in out["items"][0]["error"]
    row = db["product_images"].find_one({"image_id": "IMG1"})
    assert row["url"] == "/uploads/x.jpg", "the row must keep its old url"
    assert "locally_modified" not in row


def test_uploads_rehost_stores_the_type_the_bytes_are(tmp_path, monkeypatch):
    """A PNG saved under a .jpg name is stored as a PNG, not as its name."""
    db, bucket = _uploads_row(tmp_path, monkeypatch, _real("PNG"))
    out = online_sync_health.rehost_uploads_images(db, dry_run=False)
    assert (out["rehosted"], out["failed"]) == (1, 0), out
    assert bucket.puts == [("bvi-rehost/SKU1/IMG1_url.png", "image/png")]
    row = db["product_images"].find_one({"image_id": "IMG1"})
    assert row["url"] == "https://cdn.example/bvi-rehost/SKU1/IMG1_url.png"


class _QueueRepo:
    def __init__(self):
        self.img = {"image_id": "IMG1", "product_id": "P1", "status": "QUEUED", "url": "u"}

    def get_by_id(self, _iid):
        return dict(self.img)

    def set_status(self, _iid, target, by=None):  # noqa: ARG002
        self.img["status"] = target
        return dict(self.img)

    def attach_edited(self, _iid, url, by=None):  # noqa: ARG002
        self.img.update(status="REVIEW", edited_url=url, kind="EDITED")
        return dict(self.img)


def _auto_edit(monkeypatch, editor_reply):
    repo, bucket = _QueueRepo(), _Bucket()
    editor = _Editor(editor_reply)
    editor.name = "fake"

    async def _raw(_url):
        return _real("JPEG")

    monkeypatch.setattr(queue_router, "_require_repo", lambda: repo)
    monkeypatch.setattr(queue_router, "_fetch_image_bytes", _raw)
    monkeypatch.setattr(ied, "get_image_editor", lambda: editor)
    monkeypatch.setattr(ost, "get_object_storage", lambda: bucket)
    out = asyncio.run(
        queue_router.auto_edit_image("IMG1", current_user={"user_id": "u1"})
    )
    return out, repo, bucket


def test_auto_edit_does_not_store_an_editor_reply_that_is_not_an_image(monkeypatch):
    out, repo, bucket = _auto_edit(monkeypatch, b'{"error":"quota"}')
    assert out["auto_edit"] == "failed", out
    assert "valid image" in out["reason"]
    assert bucket.puts == []
    assert not repo.img.get("edited_url")
    assert repo.img["status"] != "REVIEW", "nothing was sent for approval"


def test_auto_edit_stores_the_type_the_editor_returned(monkeypatch):
    out, repo, bucket = _auto_edit(monkeypatch, _real("WEBP"))
    assert out["auto_edit"] == "ok", out
    ((key, content_type),) = bucket.puts
    assert content_type == "image/webp" and key.endswith(".webp")
    assert repo.img["status"] == "REVIEW"


def _script_rehost(monkeypatch, body):
    monkeypatch.setattr(rehost_script, "_fetch_bytes", lambda url, **kw: body)
    db = mongomock.MongoClient()["audit_b04_rehost_script"]
    db["product_images"].insert_one(
        {"image_id": "IMG1", "product_id": "P1", "url": "/uploads/x.jpg"}
    )
    bucket = _Bucket()
    out = rehost_script.run_rehost(
        db,
        dry_run=False,
        durable_hosts=["cdn.example"],
        bvi_hosts=["uniparallel.com"],
        bvi_public_base="https://uniparallel.com",
        storage=bucket,
        storage_durable=True,
    )
    return out, db["product_images"].find_one({"image_id": "IMG1"}), bucket


def test_rehost_script_does_not_store_a_page_that_is_not_an_image(monkeypatch):
    out, row, bucket = _script_rehost(monkeypatch, _HTML)
    assert (out["rehosted"], out["failed"]) == (0, 1), out
    assert bucket.puts == []
    assert row["url"] == "/uploads/x.jpg"


def test_rehost_script_stores_the_type_the_bytes_are(monkeypatch):
    out, row, bucket = _script_rehost(monkeypatch, _real("PNG"))
    assert out["rehosted"] == 1, out
    assert bucket.puts == [("bvi-rehost/P1/IMG1.png", "image/png")]
    assert row["url"] == "https://cdn.example/bvi-rehost/P1/IMG1.png"


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


# ---------------------------------------------------------------------------
# The brand scan is bounded (review must-fix): the ftyp box's size field is
# the uploader's own number, never a licence to index the whole file.
# ---------------------------------------------------------------------------


def _oversized_ftyp(megabytes: int = 1, brand_at_end: bytes = b"") -> bytes:
    """An 'ftyp' box that claims to span the whole file, with every 4-byte
    slot after the header holding a DIFFERENT value - the worst case for a
    scan that collects every slot."""
    slots = megabytes * 1024 * 1024 // 4
    body = b"".join(i.to_bytes(4, "big") for i in range(1, slots + 1))
    payload = b"zzzz" + b"\x00\x00\x00\x00" + body + brand_at_end
    return (0x7FFFFFFF).to_bytes(4, "big") + b"ftyp" + payload


def test_an_oversized_ftyp_box_is_scanned_in_a_small_fixed_window():
    import tracemalloc

    from api.services import image_content

    crafted = _oversized_ftyp(megabytes=1)  # 262,144 distinct brand slots
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        assert image_content._signature_mime(crafted) is None
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Unbounded, this scan held every slot at once: 34.9 MB for this 1 MB
    # file (about 33 bytes of memory per byte of file). Bounded, it holds a
    # few dozen 4-byte brands - far less than the file itself. (A quarter of
    # the file, not a few KB, so another thread allocating cannot flake it.)
    assert peak < len(crafted) // 4, (
        f"brand scan used {peak} bytes for a {len(crafted)} byte file"
    )
    assert image_mime_of(crafted) is None


def test_a_brand_outside_the_scan_window_does_not_name_the_file():
    """The window is where a real ftyp box keeps its brands; a brand planted
    megabytes into a box that claims the whole file is not read at all."""
    from api.services import image_content

    crafted = _oversized_ftyp(megabytes=1, brand_at_end=b"avif")
    assert image_content._signature_mime(crafted) is None


def test_real_brand_lists_are_still_read_inside_the_window():
    from api.services import image_content

    # The image brand as the LAST of 16 compatible brands (a long real list).
    padding = [b"iso8", b"mp41", b"dash", b"M4V ", b"isom", b"iso2", b"mp42",
               b"MSNV", b"3gp4", b"3gp5", b"3g2a", b"qt  ", b"piff", b"iso6", b"cmfc"]
    assert image_mime_of(_bmff(b"mif1", *padding, b"heic")) == "image/heic"
    assert image_mime_of(_bmff(b"mif1", *padding, b"avif")) == "image/avif"
    assert image_mime_of(_bmff(b"mif1", *padding)) == "image/heif"
    # The window is a fixed number of bytes from the start of the file.
    assert image_content._FTYP_SCAN_BYTES <= 1024


def test_a_container_of_millions_of_empty_boxes_is_refused_not_walked():
    """A 4 MB HEIC-shaped file of 8-byte boxes: the container walk stops at
    a fixed number of top-level boxes (a real photo has a handful)."""
    from api.services import image_content

    ftyp = _box(b"ftyp", b"heic" + b"\x00\x00\x00\x00" + b"mif1heic")
    flood = _box(b"free", b"") * (4 * 1024 * 1024 // 8)
    crafted = ftyp + flood + _box(b"meta", b"\x00" * 40)

    seen = []
    real = int.from_bytes

    class _CountingInt(int):
        @classmethod
        def from_bytes(cls, *a, **kw):
            seen.append(1)
            return real(*a, **kw)

    image_content.int = _CountingInt  # module-level name shadows the builtin
    try:
        assert image_mime_of(crafted) is None
    finally:
        del image_content.int
    assert len(seen) <= image_content._MAX_TOP_BOXES + 2, len(seen)
    # A genuine container with a few extra boxes before `meta` still passes.
    ok = ftyp + _box(b"free", b"") * 20 + _box(b"meta", b"\x00" * 40) + _box(b"mdat", b"\x01" * 64)
    assert image_mime_of(ok) == "image/heic"


# ---------------------------------------------------------------------------
# Decision D9, pinned: the two re-host tools do not copy an SVG (it is markup
# that can carry script, and the image doors store raster photos only).
# ---------------------------------------------------------------------------

_SVG = (
    b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="4" height="4">'
    b"<script>window.marker=true</script><rect width=\"4\" height=\"4\"/></svg>"
)


def test_uploads_rehost_does_not_copy_an_svg(tmp_path, monkeypatch):
    db, bucket = _uploads_row(tmp_path, monkeypatch, _SVG)
    out = online_sync_health.rehost_uploads_images(db, dry_run=False)
    assert (out["rehosted"], out["failed"]) == (0, 1), out
    assert bucket.puts == []
    assert db["product_images"].find_one({"image_id": "IMG1"})["url"] == "/uploads/x.jpg"


def test_rehost_script_does_not_copy_an_svg(monkeypatch):
    out, row, bucket = _script_rehost(monkeypatch, _SVG)
    assert (out["rehosted"], out["failed"]) == (0, 1), out
    assert bucket.puts == []
    assert row["url"] == "/uploads/x.jpg"
    assert image_mime_of(_SVG) is None
