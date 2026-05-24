"""
IMS no longer owns the Shopify catalog -- the e-commerce app (BVI) is the single
owner. These tests pin that IMS Shopify WRITES are retired by default (so two
systems can't push to the same Shopify store).
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.nexus_providers import (  # noqa: E402
    ims_shopify_writes_enabled,
    shopify_push_product,
)


def test_writes_disabled_by_default(monkeypatch):
    monkeypatch.delenv("IMS_SHOPIFY_WRITES", raising=False)
    assert ims_shopify_writes_enabled() is False


def test_writes_can_be_reenabled(monkeypatch):
    monkeypatch.setenv("IMS_SHOPIFY_WRITES", "1")
    assert ims_shopify_writes_enabled() is True
    monkeypatch.setenv("IMS_SHOPIFY_WRITES", "off")
    assert ims_shopify_writes_enabled() is False


def test_push_is_retired_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("IMS_SHOPIFY_WRITES", raising=False)
    res = asyncio.run(shopify_push_product(None, {"title": "Frame X"}))
    # ok=True (intentional skip, not a failure) and clearly marked RETIRED.
    assert res.ok is True
    assert "RETIRED" in (res.notes or "")
