"""POST /products/generate-description -- the Add-Product form's
"Auto-fill with AI" button (owner request 2026-07-04).

Contract: ALWAYS 200 with a status field (never 5xx); gated to catalog write
roles; uses agents.claude_client.call_claude (monkeypatched here -- no network).
"""

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio  # noqa: E402

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers import products as products_mod  # noqa: E402
from agents import claude_client  # noqa: E402


def _req(attributes, category="SUNGLASS", max_length=350):
    return products_mod.DescriptionGenerateRequest(
        category=category, attributes=attributes, max_length=max_length
    )


def _user(roles=("CATALOG_MANAGER",)):
    return {"user_id": "u1", "username": "t", "roles": list(roles)}


_ATTRS = {
    "brand_name": "Ray-Ban",
    "model_no": "RB3025",
    "lens_colour": "Green G-15",
    "polarization": "Yes",
    "frame_color": "Gold",
    "empty_field": "",
    "none_field": None,
}


def test_generated_happy_path(monkeypatch):
    async def fake_call(system, user, **kw):
        # The prompt must carry the filled attributes but not the empty ones.
        assert "Ray-Ban" in user and "RB3025" in user
        assert "empty_field" not in user and "none_field" not in user
        assert "invent" in system  # no-invented-specs rule present
        # SEO contract (owner 2026-07-04): keyword rules + front-loading present.
        assert "SEO" in system and "keyword" in system.lower()
        assert "front-load" in system.lower() and "stuffing" in system
        return "Classic Ray-Ban RB3025 aviators with polarized Green G-15 lenses in a gold frame."

    monkeypatch.setattr(claude_client, "is_claude_available", lambda: True)
    monkeypatch.setattr(claude_client, "call_claude", fake_call)
    out = asyncio.run(products_mod.generate_product_description(_req(_ATTRS), _user()))
    assert out["status"] == "GENERATED"
    # Owner 2026-07-05: the description is now the FULL Shopify-format HTML --
    # deterministic template sections with the AI paragraph inside a <p>.
    assert out["description"].startswith("<h4>Product Details</h4>")
    assert "<p>Classic Ray-Ban" in out["description"]
    assert "<h4>Technical Specifications</h4>" in out["description"]
    assert "<h4>General Information</h4>" in out["description"]
    # Spec rows come from the FILLED fields only.
    assert "<td>Polarization</td>" in out["description"]
    assert "<td>Yes</td>" in out["description"]
    assert "empty_field" not in out["description"]
    # The raw paragraph is also returned for FE preview/edit.
    assert out["paragraph"].startswith("Classic Ray-Ban")


def test_no_key_returns_failed_no_key(monkeypatch):
    monkeypatch.setattr(claude_client, "is_claude_available", lambda: False)
    out = asyncio.run(products_mod.generate_product_description(_req(_ATTRS), _user()))
    assert out == {"description": "", "status": "FAILED_NO_KEY"}


def test_empty_attributes_short_circuits(monkeypatch):
    called = {"n": 0}

    async def fake_call(*a, **k):
        called["n"] += 1
        return "x"

    monkeypatch.setattr(claude_client, "call_claude", fake_call)
    out = asyncio.run(
        products_mod.generate_product_description(_req({"a": "", "b": None}), _user())
    )
    assert out == {"description": "", "status": "EMPTY_ATTRIBUTES"}
    assert called["n"] == 0  # model never invoked


def test_model_failure_returns_failed_generation(monkeypatch):
    async def fake_call(*a, **k):
        return None  # claude_client's fail-soft contract

    monkeypatch.setattr(claude_client, "is_claude_available", lambda: True)
    monkeypatch.setattr(claude_client, "call_claude", fake_call)
    out = asyncio.run(products_mod.generate_product_description(_req(_ATTRS), _user()))
    assert out == {"description": "", "status": "FAILED_GENERATION"}


def test_overlong_output_is_trimmed_to_max_length(monkeypatch):
    async def fake_call(*a, **k):
        return "word " * 200  # ~1000 chars

    monkeypatch.setattr(claude_client, "is_claude_available", lambda: True)
    monkeypatch.setattr(claude_client, "call_claude", fake_call)
    out = asyncio.run(
        products_mod.generate_product_description(_req(_ATTRS, max_length=120), _user())
    )
    assert out["status"] == "GENERATED"
    # The cap applies to the AI PARAGRAPH (the spec tables are deterministic
    # template output and not counted against it).
    assert len(out["paragraph"]) <= 121  # cap + closing period
    assert out["paragraph"].endswith(".")
    assert f"<p>{out['paragraph']}</p>" in out["description"]


def test_shopify_template_builder_pure():
    """The deterministic template: sections render only when fields exist,
    values are HTML-escaped, and the warranty section links the store page."""
    from api.services.product_description import (
        build_model_line,
        build_shopify_description_html,
    )

    attrs = {
        "brand_name": "Ray-Ban",
        "model_no": "RB3025",
        "colour_code": "L0205",
        "lens_size": "58",
        "frame_material": "Metal & <Gold>",
        "warranty": "2 Years",
        "gender": "Unisex",
    }
    html = build_shopify_description_html("Sunglass", attrs, "A paragraph.")
    assert html.startswith("<h4>Product Details</h4>")
    assert "<h5>Model Number: Ray-Ban RB3025 L0205 58 Sunglass</h5>" in html
    assert "<p>A paragraph.</p>" in html
    # Category row injected; escaping applied to the material value.
    assert "<td>Product Category</td>" in html and "<td>Sunglass</td>" in html
    assert "Metal &amp; &lt;Gold&gt;" in html
    assert "<td>Gender</td>" in html
    assert 'href="https://bettervision.in/pages/warranty"' in html
    assert "warranty of 2 Years" in html
    # No warranty field -> no warranty section; empty attrs -> headers only.
    html2 = build_shopify_description_html("Sunglass", {"brand_name": "X"}, "")
    assert "<h4>Warranty</h4>" not in html2
    assert build_model_line("Frame", {"brand_name": "B", "model_no": "M1"}) == "B M1 Frame"


def test_rbac_row_catalogued():
    from api.services.rbac_policy import POLICY

    rows = [
        r
        for r in POLICY
        if r.get("path") == "/api/v1/products/generate-description"
        and r.get("method") == "POST"
    ]
    assert len(rows) == 1
    assert set(rows[0]["allowed"]) == {"ADMIN", "CATALOG_MANAGER"}
