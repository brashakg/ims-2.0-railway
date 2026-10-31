"""POST /products/suggest-colour-name -- a suggested colour name read from
the product's OWN photo 1 (owner 2026-10-23, PLAN_STATUS 4ad).

Fake Anthropic transport throughout (httpx.MockTransport or a monkeypatched
llm_provider.complete_image) -- no network.
"""

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio  # noqa: E402
import base64  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from PIL import Image  # noqa: E402

from agents import llm_provider  # noqa: E402
from api.routers import products as products_mod  # noqa: E402
from api.services import catalogue_pdf, colour_suggest  # noqa: E402
from api.services import product_description as pd  # noqa: E402
from api.utils.rate_limit import allow  # noqa: E402
from tests.strict_fakes import StrictCollection  # noqa: E402


def _png(w=1200, h=600, colour=(200, 160, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, format="PNG")
    return buf.getvalue()


class _DB:
    def __init__(self, products=()):
        self.cols = {"products": StrictCollection("products", list(products)),
                     "ai_usage_log": StrictCollection("ai_usage_log")}
        self.is_connected = True

    def get_collection(self, name):
        return self.cols.setdefault(name, StrictCollection(name))


_SG = {
    "product_id": "p1",
    "category": "SUNGLASS",
    "images": ["/api/v1/products/image/abc123", "/api/v1/products/image/zzz"],
    "attributes": {"brand_name": "Ray-Ban", "model_no": "RB3025", "colour_code": "001/58",
                   "polarization": "Yes"},
}


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("CATALOG_VISION_MODEL", raising=False)


@pytest.fixture
def photo(monkeypatch):
    """Photo 1's bytes from the file store; records which URLs were read."""
    seen = []

    def local(url):
        seen.append(("local", url))
        return _png() if url.endswith("abc123") or url.endswith("form1") else None

    async def fetch(rows):
        seen.append(("fetch", rows[0]["image"]))
        return {0: _png()} if rows[0]["image"].endswith("abc123") else {}

    monkeypatch.setattr(catalogue_pdf, "_fetch_local_bytes", local)
    monkeypatch.setattr(catalogue_pdf, "fetch_images", fetch)
    return seen


def _fake_claude(monkeypatch, text="Gold / Green Polarized", usage=None):
    calls = []

    async def fake(system, prompt, image_b64, **kw):
        calls.append({"system": system, "prompt": prompt, "image_b64": image_b64, **kw})
        if text is None:
            return None
        return {"text": text, "usage": usage or {"input_tokens": 1000, "output_tokens": 10},
                "model": kw.get("model")}

    monkeypatch.setattr(llm_provider, "complete_image", fake)
    return calls


def _user(roles=("CATALOG_MANAGER",), uid="u1"):
    return {"user_id": uid, "username": "t", "roles": list(roles)}


@pytest.fixture(autouse=True)
def _fresh_rate_bucket():
    products_mod._colour_suggest_hits.clear()
    yield
    products_mod._colour_suggest_hits.clear()


def _run(body, db, monkeypatch, user=None):
    monkeypatch.setattr(products_mod, "_family_db_or_503", lambda: db)
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_db", lambda: db)
    return asyncio.run(products_mod.suggest_colour_name(body, user or _user()))


# --- the transport: base64 bytes, never a URL ------------------------------


def test_complete_image_sends_base64_bytes_and_reads_the_text_block(monkeypatch, keyed):
    sent = {}

    def handler(request):
        sent["body"] = json.loads(request.content)
        sent["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={
            "model": "claude-sonnet-5-5",
            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "Gold / Green"}],
            "usage": {"input_tokens": 812, "output_tokens": 7},
        })

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    out = asyncio.run(llm_provider.complete_image("sys", "name it", "QUJD", model="claude-sonnet-5-5"))

    assert out == {"text": "Gold / Green", "usage": {"input_tokens": 812, "output_tokens": 7},
                   "model": "claude-sonnet-5-5"}
    block = sent["body"]["messages"][0]["content"][0]
    assert block == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}
    assert "url" not in json.dumps(sent["body"])
    assert sent["body"]["model"] == "claude-sonnet-5-5"
    assert sent["key"] == "test-key"


def test_complete_image_without_a_key_makes_no_call(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm_provider, "_post_anthropic", lambda *a, **k: pytest.fail("called"))
    assert asyncio.run(llm_provider.complete_image("s", "p", "QUJD", model="m")) is None


# --- the endpoint -----------------------------------------------------------


def test_suggests_from_photo_one_and_never_writes(monkeypatch, keyed, photo):
    calls = _fake_claude(monkeypatch, text='"Gold / Green Polarized."\nbecause the frame...')
    db = _DB([dict(_SG)])
    before = [dict(d) for d in db.cols["products"].find({})]

    out = _run(products_mod.ColourSuggestRequest(product_id="p1"), db, monkeypatch)

    assert out["status"] == "SUGGESTED"
    assert out["suggestion"] == "Gold / Green Polarized"
    # Photo 1 (not photo 2) was read, as bytes.
    assert photo == [("fetch", "/api/v1/products/image/abc123")]
    jpeg = base64.b64decode(calls[0]["image_b64"])
    img = Image.open(io.BytesIO(jpeg))
    assert img.format == "JPEG" and max(img.size) <= colour_suggest.PHOTO_EDGE
    assert calls[0]["media_type"] == "image/jpeg"
    assert calls[0]["model"] == "claude-sonnet-5-5"
    assert "Ray-Ban" in calls[0]["prompt"] and "Lens: polarized" in calls[0]["prompt"]
    assert "Sunglass" in calls[0]["prompt"]
    assert "Havana / Brown" in calls[0]["system"]  # house style examples
    # Never writes the product.
    assert [dict(d) for d in db.cols["products"].find({})] == before


def test_cost_is_logged_per_call(monkeypatch, keyed, photo):
    _fake_claude(monkeypatch, usage={"input_tokens": 1500, "output_tokens": 20})
    db = _DB([dict(_SG)])
    out = _run(products_mod.ColourSuggestRequest(product_id="p1"), db, monkeypatch)
    rows = list(db.cols["ai_usage_log"].find({}))
    assert len(rows) == 1
    row = rows[0]
    assert row["feature"] == "suggest_colour_name"
    assert row["user_id"] == "u1" and row["product_id"] == "p1"
    assert (row["input_tokens"], row["output_tokens"]) == (1500, 20)
    # Sonnet 5.5: $2 in / $10 out per million tokens.
    assert row["cost_usd"] == pytest.approx(0.0032)
    assert out["cost_usd"] == pytest.approx(0.0032)


def test_model_comes_from_config(monkeypatch, keyed, photo):
    calls = _fake_claude(monkeypatch)
    monkeypatch.setenv("CATALOG_VISION_MODEL", "claude-haiku-5-5")
    _run(products_mod.ColourSuggestRequest(product_id="p1"), _DB([dict(_SG)]), monkeypatch)
    assert calls[0]["model"] == "claude-haiku-5-5"


def test_no_photo_says_so_and_calls_nothing(monkeypatch, keyed, photo):
    calls = _fake_claude(monkeypatch)
    db = _DB([dict(_SG, images=[])])
    out = _run(products_mod.ColourSuggestRequest(product_id="p1"), db, monkeypatch)
    assert out["status"] == "NO_PHOTO"
    assert "photo" in out["message"].lower()
    assert calls == [] and photo == []


def test_unreadable_photo_says_so(monkeypatch, keyed, photo):
    calls = _fake_claude(monkeypatch)
    db = _DB([dict(_SG, images=["/api/v1/products/image/gone"])])
    out = _run(products_mod.ColourSuggestRequest(product_id="p1"), db, monkeypatch)
    assert out["status"] == "NO_PHOTO" and "could not be read" in out["message"]
    assert calls == []


def test_missing_key_says_where_to_add_it(monkeypatch, photo):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    calls = _fake_claude(monkeypatch)
    out = _run(products_mod.ColourSuggestRequest(product_id="p1"), _DB([dict(_SG)]), monkeypatch)
    assert out["status"] == "FAILED_NO_KEY"
    assert "Settings -> Integrations" in out["message"]
    assert calls == []


def test_model_failure_is_a_clear_status(monkeypatch, keyed, photo):
    _fake_claude(monkeypatch, text=None)
    out = _run(products_mod.ColourSuggestRequest(product_id="p1"), _DB([dict(_SG)]), monkeypatch)
    assert out["status"] == "FAILED_GENERATION" and out["suggestion"] == ""


def test_unknown_product_404(monkeypatch, keyed, photo):
    _fake_claude(monkeypatch)
    with pytest.raises(HTTPException) as e:
        _run(products_mod.ColourSuggestRequest(product_id="nope"), _DB([dict(_SG)]), monkeypatch)
    assert e.value.status_code == 404


def test_add_product_reads_the_form_photo_from_the_file_store_only(monkeypatch, keyed, photo):
    calls = _fake_claude(monkeypatch)
    body = products_mod.ColourSuggestRequest(
        image_url="/api/v1/products/image/form1", category="SG",
        attributes={"brand_name": "Oakley", "model_no": "OO9208"})
    out = _run(body, _DB(), monkeypatch)
    assert out["status"] == "SUGGESTED"
    assert photo == [("local", "/api/v1/products/image/form1")]
    assert "Oakley" in calls[0]["prompt"] and "not marked polarized" in calls[0]["prompt"]


def test_add_product_never_fetches_an_outside_url(monkeypatch, keyed, photo):
    calls = _fake_claude(monkeypatch)
    body = products_mod.ColourSuggestRequest(image_url="http://169.254.169.254/latest")
    out = _run(body, _DB(), monkeypatch)
    assert out["status"] == "NO_PHOTO"
    assert all(kind == "local" for kind, _ in photo)
    assert calls == []


def test_per_user_rate_limit(monkeypatch, keyed, photo):
    _fake_claude(monkeypatch)
    db = _DB([dict(_SG)])
    body = products_mod.ColourSuggestRequest(product_id="p1")
    for _ in range(products_mod._COLOUR_SUGGEST_LIMIT):
        assert _run(body, db, monkeypatch)["status"] == "SUGGESTED"
    with pytest.raises(HTTPException) as e:
        _run(body, db, monkeypatch)
    assert e.value.status_code == 429
    # Another user is not held up.
    assert _run(body, db, monkeypatch, user=_user(uid="u2"))["status"] == "SUGGESTED"


def test_rate_limit_window_slides():
    bucket = {}
    assert allow(bucket, "k", 2, 60, now=0) and allow(bucket, "k", 2, 60, now=1)
    assert not allow(bucket, "k", 2, 60, now=2)
    assert allow(bucket, "k", 2, 60, now=61)


def test_clean_name():
    assert colour_suggest.clean_name("Colour name: Havana / Brown Gradient.") == "Havana / Brown Gradient"
    assert colour_suggest.clean_name("  \n'Black / Grey'\nmore") == "Black / Grey"
    assert colour_suggest.clean_name("") == ""
    assert len(colour_suggest.clean_name("x" * 99)) == colour_suggest.MAX_LEN


def test_rbac_row_catalogued():
    from api.services.rbac_policy import POLICY

    rows = [r for r in POLICY if r.get("path") == "/api/v1/products/suggest-colour-name"
            and r.get("method") == "POST"]
    assert len(rows) == 1
    assert set(rows[0]["allowed"]) == {"ADMIN", "CATALOG_MANAGER"}


def test_endpoint_is_gated_to_catalogue_roles():
    route = next(r for r in products_mod.router.routes
                 if getattr(r, "path", "").endswith("/suggest-colour-name"))
    dep = route.dependant.dependencies[-1].call  # require_roles(...)._dep
    ok = asyncio.run(dep(current_user=_user(("ADMIN",))))
    assert ok["roles"] == ["ADMIN"]
    asyncio.run(dep(current_user=_user(("SUPERADMIN",))))
    with pytest.raises(HTTPException) as e:
        asyncio.run(dep(current_user=_user(("SALES_STAFF",))))
    assert e.value.status_code == 403


# --- an eyewear colour name is not a "Lens Colour" spec row -----------------


def test_eyewear_colour_name_is_not_labelled_lens_colour():
    html = pd.build_shopify_description_html(
        "Frame", {"brand_name": "Ray-Ban", "model_no": "RB5154", "colour_name": "Havana"}, "p")
    assert "<td>Lens Colour</td>" not in html


def test_contact_lens_colour_name_keeps_its_lens_colour_row():
    html = pd.build_shopify_description_html(
        "Colour Contact Lens", {"brand_name": "Bausch", "model_name": "Lacelle", "colour_name": "Hazel"}, "p")
    assert "<td>Lens Colour</td>\n<td>Hazel</td>" in html


def test_edit_screen_reads_the_forms_unsaved_photo_one(monkeypatch, keyed, photo):
    _fake_claude(monkeypatch)
    body = products_mod.ColourSuggestRequest(product_id="p1", image_url="/api/v1/products/image/form1")
    out = _run(body, _DB([dict(_SG)]), monkeypatch)
    assert out["status"] == "SUGGESTED"
    assert photo == [("local", "/api/v1/products/image/form1")]


def test_edit_screen_with_the_saved_photo_one_reads_it_as_stored(monkeypatch, keyed, photo):
    _fake_claude(monkeypatch)
    stored = dict(_SG, images=["https://cdn.example/rehosted/abc123"])
    body = products_mod.ColourSuggestRequest(product_id="p1", image_url="https://cdn.example/rehosted/abc123")
    out = _run(body, _DB([stored]), monkeypatch)
    assert out["status"] == "SUGGESTED"
    assert photo == [("fetch", "https://cdn.example/rehosted/abc123")]
