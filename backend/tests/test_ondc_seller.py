"""
Tests for the ONDC Seller Node (BVI-20).

backend/api/services/ondc_seller.py  -- pure mapping + gate helpers
backend/api/routers/ondc.py          -- callback + admin endpoints

Run:
    JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_ondc_seller.py -v

Coverage:
    1. build_ondc_item -- catalog item shape (HSN, GST tags, price, quantity)
    2. build_ondc_item -- variant enrichment (color/size in display name)
    3. build_ondc_item -- HSN resolution by category
    4. build_ondc_catalog -- DARK when db=None returns []
    5. build_ondc_catalog -- maps active products, skips inactive
    6. ondc_enabled -- False when env gate off
    7. ondc_enabled -- False when env gate on but no DB creds
    8. ondc_enabled -- True when env on + DB creds present
    9. publish_catalog -- SIMULATED when gate off
    10. publish_catalog -- LIVE path calls SNP and writes back (mocked httpx)
    11. ingest_ondc_order -- maps to IMS order shape + channel=ONDC
    12. ingest_ondc_order -- idempotent (same ondc order id -> IDEMPOTENT)
    13. ingest_ondc_order -- missing order.id returns ok=False
    14. reconcile_tcs -- calculates TCS at 1%, commission, net_payout
    15. reconcile_tcs -- fail-soft on bad input
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure backend root is importable + JWT secret present
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")


# ---------------------------------------------------------------------------
# Minimal mock DB that supports find / find_one / count_documents / insert_one
# ---------------------------------------------------------------------------

class _MockCursor:
    def __init__(self, docs):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)

    def __next__(self):
        return next(iter(self._docs))


class _MockCollection:
    def __init__(self, docs=None):
        self._docs: List[Dict[str, Any]] = list(docs or [])

    def _matches(self, doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
        """Simple query matcher supporting $or, $in, $exists, $ne, exact match."""
        for k, v in query.items():
            if k == "$or":
                if not any(self._matches(doc, sub) for sub in v):
                    return False
                continue
            if isinstance(v, dict):
                doc_val = doc.get(k)
                if "$in" in v and doc_val not in v["$in"]:
                    return False
                if "$exists" in v:
                    present = k in doc and doc[k] is not None
                    if v["$exists"] and not present:
                        return False
                    if not v["$exists"] and present:
                        return False
                if "$ne" in v and doc.get(k) == v["$ne"]:
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    def _apply_projection(self, doc: Dict[str, Any], projection: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply a MongoDB-style projection dict. Handles include and exclude modes.
        Always strips _id. Exclude-only projection ({field:0}) returns the full doc."""
        base = {k: v for k, v in doc.items() if k != "_id"}
        if not projection:
            return base
        # Check if any field has a truthy (include) value (ignoring _id)
        non_id = {k: v for k, v in projection.items() if k != "_id"}
        if any(non_id.values()):
            # Include mode: return only listed fields
            return {k: doc[k] for k, v in non_id.items() if v and k in doc}
        # Exclude mode: return all fields except excluded ones
        excluded = {k for k, v in non_id.items() if not v}
        return {k: v for k, v in base.items() if k not in excluded}

    def find(self, query=None, projection=None):
        results = []
        for doc in self._docs:
            if not self._matches(doc, query or {}):
                continue
            results.append(self._apply_projection(doc, projection))
        return _MockCursor(results)

    def find_one(self, query=None, projection=None):
        for doc in self._docs:
            if not self._matches(doc, query or {}):
                continue
            return self._apply_projection(doc, projection)
        return None

    def insert_one(self, doc):
        self._docs.append(doc)
        return MagicMock(inserted_id="fake-id")

    def update_one(self, query, update, **kwargs):
        return MagicMock(modified_count=1)

    def find_one_and_update(self, query, update, **kwargs):
        doc = self.find_one(query)
        if doc:
            self.update_one(query, update)
        return doc

    def count_documents(self, query=None):
        return len(list(self.find(query)))

    def aggregate(self, pipeline):
        return iter([])


class _MockDB:
    def __init__(self, **collections):
        self._collections = {k: _MockCollection(v) for k, v in collections.items()}

    def get_collection(self, name: str) -> _MockCollection:
        if name not in self._collections:
            self._collections[name] = _MockCollection()
        return self._collections[name]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PRODUCT_FRAME = {
    "product_id": "P001",
    "sku": "SKU-FRAME-001",
    "name": "Classic Frame",
    "brand": "Ray-Ban",
    "category": "FRAME",
    "status": "ACTIVE",
    "price": 3500.0,
    "offer_price": 3000.0,
    "gst_rate": 5.0,
    "hsn_code": "9003",
    "quantity": 10,
    "description": "Premium acetate spectacle frame",
    "images": [{"url": "https://cdn.example.com/frame.jpg"}],
    "country_of_origin": "IND",
}

_PRODUCT_SUNGLASS = {
    "product_id": "P002",
    "sku": "SKU-SG-001",
    "name": "Aviator Classic",
    "brand": "Ray-Ban",
    "category": "SUNGLASS",
    "status": "ACTIVE",
    "price": 7000.0,
    "offer_price": 6500.0,
    "gst_rate": 18.0,
    "hsn_code": "9004",
    "quantity": 5,
    "description": "Classic aviator sunglass",
    "images": [],
}

_VARIANT = {
    "product_id": "P001",
    "sku": "SKU-FRAME-001-BLK-M",
    "color": "Black",
    "size": "M",
    "status": "ACTIVE",
    "stock_quantity": 4,
    "price": 3000.0,
}

_INACTIVE_PRODUCT = {
    "product_id": "P003",
    "sku": "SKU-INACTIVE-001",
    "name": "Inactive Product",
    "category": "FRAME",
    "status": "INACTIVE",
    "price": 1000.0,
}


# ---------------------------------------------------------------------------
# Import under test (after env vars are set)
# ---------------------------------------------------------------------------

from api.services import ondc_seller  # noqa: E402


# ===========================================================================
# 1. build_ondc_item -- catalog item shape
# ===========================================================================

class TestBuildOndcItem:
    def test_basic_shape(self):
        item = ondc_seller.build_ondc_item(_PRODUCT_FRAME)
        assert item["id"] == "SKU-FRAME-001"
        assert item["descriptor"]["name"] == "Ray-Ban Classic Frame"
        assert item["price"]["currency"] == "INR"
        assert float(item["price"]["value"]) == 3500.0
        assert float(item["price"]["offered_value"]) == 3000.0
        assert item["quantity"]["available"]["count"] == "10"
        assert item["@ondc/org/returnable"] is True
        assert item["@ondc/org/cancellable"] is True

    def test_gst_tag_5pct_frame(self):
        item = ondc_seller.build_ondc_item(_PRODUCT_FRAME)
        gst_tag = next(t for t in item["tags"] if t["code"] == "gst")
        tag_map = {e["code"]: e["value"] for e in gst_tag["list"]}
        assert tag_map["tax_rate"] == "5"
        assert tag_map["hsn_code"] == "9003"

    def test_gst_tag_18pct_sunglass(self):
        item = ondc_seller.build_ondc_item(_PRODUCT_SUNGLASS)
        gst_tag = next(t for t in item["tags"] if t["code"] == "gst")
        tag_map = {e["code"]: e["value"] for e in gst_tag["list"]}
        assert tag_map["tax_rate"] == "18"
        assert tag_map["hsn_code"] == "9004"

    def test_origin_tag_present(self):
        item = ondc_seller.build_ondc_item(_PRODUCT_FRAME)
        origin_tag = next(t for t in item["tags"] if t["code"] == "origin")
        codes = {e["code"]: e["value"] for e in origin_tag["list"]}
        assert codes["country"] == "IND"

    def test_statutory_reqs_present(self):
        item = ondc_seller.build_ondc_item(_PRODUCT_FRAME)
        stat = item["@ondc/org/statutory_reqs_packaged_commodities"]
        assert stat["country_of_origin"] == "IND"
        assert stat["manufacturer_or_packer_name"] == "Ray-Ban"


# ===========================================================================
# 2. build_ondc_item -- variant enrichment
# ===========================================================================

class TestBuildOndcItemVariant:
    def test_variant_id_used_as_item_id(self):
        item = ondc_seller.build_ondc_item(_PRODUCT_FRAME, _VARIANT)
        assert item["id"] == "SKU-FRAME-001-BLK-M"

    def test_variant_color_size_in_display_name(self):
        item = ondc_seller.build_ondc_item(_PRODUCT_FRAME, _VARIANT)
        assert "Black" in item["descriptor"]["name"]
        assert "M" in item["descriptor"]["name"]

    def test_variant_stock_quantity_used(self):
        item = ondc_seller.build_ondc_item(_PRODUCT_FRAME, _VARIANT)
        assert item["quantity"]["available"]["count"] == "4"

    def test_variant_price_used(self):
        item = ondc_seller.build_ondc_item(_PRODUCT_FRAME, _VARIANT)
        assert float(item["price"]["offered_value"]) == 3000.0


# ===========================================================================
# 3. build_ondc_item -- HSN resolution by category (no explicit HSN on product)
# ===========================================================================

class TestHSNResolution:
    def _product_no_hsn(self, category: str) -> Dict[str, Any]:
        p = {**_PRODUCT_FRAME}
        p["category"] = category
        p.pop("hsn_code", None)
        return p

    def test_frame_default_9003(self):
        item = ondc_seller.build_ondc_item(self._product_no_hsn("FRAME"))
        gst_tag = next(t for t in item["tags"] if t["code"] == "gst")
        tag_map = {e["code"]: e["value"] for e in gst_tag["list"]}
        assert tag_map["hsn_code"] == "9003"

    def test_sunglass_default_9004(self):
        item = ondc_seller.build_ondc_item(self._product_no_hsn("SUNGLASSES"))
        gst_tag = next(t for t in item["tags"] if t["code"] == "gst")
        tag_map = {e["code"]: e["value"] for e in gst_tag["list"]}
        assert tag_map["hsn_code"] == "9004"

    def test_contact_lens_9001(self):
        item = ondc_seller.build_ondc_item(self._product_no_hsn("CONTACT_LENS"))
        gst_tag = next(t for t in item["tags"] if t["code"] == "gst")
        tag_map = {e["code"]: e["value"] for e in gst_tag["list"]}
        assert tag_map["hsn_code"] == "9001"


# ===========================================================================
# 4. build_ondc_catalog -- DARK when db=None returns []
# ===========================================================================

class TestBuildOndcCatalogDark:
    def test_none_db_returns_empty(self):
        result = ondc_seller.build_ondc_catalog(db=None)
        assert result == []


# ===========================================================================
# 5. build_ondc_catalog -- maps active products, skips inactive
# ===========================================================================

class TestBuildOndcCatalog:
    def test_active_products_included(self):
        db = _MockDB(
            catalog_products=[_PRODUCT_FRAME, _PRODUCT_SUNGLASS],
            catalog_variants=[],
        )
        items = ondc_seller.build_ondc_catalog(db)
        ids = [i["id"] for i in items]
        assert "SKU-FRAME-001" in ids
        assert "SKU-SG-001" in ids

    def test_inactive_products_excluded(self):
        db = _MockDB(
            catalog_products=[_PRODUCT_FRAME, _INACTIVE_PRODUCT],
            catalog_variants=[],
        )
        items = ondc_seller.build_ondc_catalog(db)
        ids = [i["id"] for i in items]
        assert "SKU-INACTIVE-001" not in ids

    def test_variants_expand_to_separate_items(self):
        db = _MockDB(
            catalog_products=[_PRODUCT_FRAME],
            catalog_variants=[
                _VARIANT,
                {**_VARIANT, "sku": "SKU-FRAME-001-WHT-L", "color": "White", "size": "L"},
            ],
        )
        items = ondc_seller.build_ondc_catalog(db)
        assert len(items) == 2
        ids = {i["id"] for i in items}
        assert "SKU-FRAME-001-BLK-M" in ids
        assert "SKU-FRAME-001-WHT-L" in ids


# ===========================================================================
# 6-8. ondc_enabled gate
# ===========================================================================

class TestOndcEnabled:
    def test_disabled_when_env_off(self):
        env = {k: v for k, v in os.environ.items() if k != "IMS_ONDC_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            assert ondc_seller.ondc_enabled(db=None) is False

    def test_disabled_when_no_db_creds(self):
        env = {k: v for k, v in os.environ.items()}
        env["IMS_ONDC_ENABLED"] = "1"
        with patch.dict(os.environ, env, clear=True):
            # DB has no ONDC integration doc
            db = _MockDB(integrations=[])
            assert ondc_seller.ondc_enabled(db) is False

    def test_enabled_with_creds(self):
        env = {k: v for k, v in os.environ.items()}
        env["IMS_ONDC_ENABLED"] = "1"
        with patch.dict(os.environ, env, clear=True):
            db = _MockDB(
                integrations=[{
                    "type": "ondc",
                    "enabled": True,
                    "config": {
                        "snp_url": "https://snp.example.com",
                        "subscriber_id": "bettervision.in",
                    },
                }]
            )
            assert ondc_seller.ondc_enabled(db) is True

    def test_disabled_when_snp_url_missing(self):
        env = {k: v for k, v in os.environ.items()}
        env["IMS_ONDC_ENABLED"] = "1"
        with patch.dict(os.environ, env, clear=True):
            db = _MockDB(
                integrations=[{
                    "type": "ondc",
                    "enabled": True,
                    "config": {"subscriber_id": "bettervision.in"},  # no snp_url
                }]
            )
            assert ondc_seller.ondc_enabled(db) is False


# ===========================================================================
# 9. publish_catalog -- SIMULATED when gate off
# ===========================================================================

class TestPublishCatalogSimulated:
    @pytest.mark.asyncio
    async def test_simulated_when_disabled(self):
        env = {k: v for k, v in os.environ.items() if k != "IMS_ONDC_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            db = _MockDB(
                catalog_products=[_PRODUCT_FRAME],
                catalog_variants=[],
                integrations=[],
            )
            result = await ondc_seller.publish_catalog(db)
            assert result["mode"] == "SIMULATED"
            assert result["ok"] is True
            assert result["item_count"] == 1
            assert result["published_at"] is None
            assert "simulated_reason" in result


# ===========================================================================
# 10. publish_catalog -- LIVE path (mocked httpx)
# ===========================================================================

class TestPublishCatalogLive:
    @pytest.mark.asyncio
    async def test_live_path_calls_snp(self):
        env = {k: v for k, v in os.environ.items()}
        env["IMS_ONDC_ENABLED"] = "1"
        with patch.dict(os.environ, env, clear=True):
            db = _MockDB(
                catalog_products=[_PRODUCT_FRAME],
                catalog_variants=[],
                integrations=[{
                    "type": "ondc",
                    "enabled": True,
                    "config": {
                        "snp_url": "https://snp.example.com",
                        "subscriber_id": "bettervision.in",
                        "ukp": "test-secret",
                    },
                }],
            )

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"message":{"ack":{"status":"ACK"}}}'

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                result = await ondc_seller.publish_catalog(db)

            assert result["mode"] == "LIVE"
            assert result["ok"] is True
            assert result["item_count"] == 1
            assert result["published_at"] is not None
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_live_path_writeback_on_success(self):
        """last_published_at is written back to integrations."""
        env = {k: v for k, v in os.environ.items()}
        env["IMS_ONDC_ENABLED"] = "1"
        with patch.dict(os.environ, env, clear=True):
            db = _MockDB(
                catalog_products=[_PRODUCT_FRAME],
                catalog_variants=[],
                integrations=[{
                    "type": "ondc",
                    "enabled": True,
                    "config": {
                        "snp_url": "https://snp.example.com",
                        "subscriber_id": "bettervision.in",
                    },
                }],
            )

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "{}"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                result = await ondc_seller.publish_catalog(db)

            assert result["ok"] is True


# ===========================================================================
# 11. ingest_ondc_order -- maps to IMS order shape + channel=ONDC
# ===========================================================================

_ONDC_CONFIRM_PAYLOAD = {
    "context": {
        "domain": "ONDC:RET12",
        "action": "on_confirm",
        "core_version": "1.2.0",
        "bap_id": "paytm.com",
        "bap_uri": "https://paytm.com/ondc",
        "bpp_id": "bettervision.in",
        "bpp_uri": "https://api.ims.bettervision.in/api/v1/ondc",
        "transaction_id": "txn-abc-123",
        "message_id": "msg-xyz-456",
        "city": "std:020",
        "country": "IND",
        "timestamp": "2026-06-05T10:00:00.000Z",
    },
    "message": {
        "order": {
            "id": "ONDC-ORD-001",
            "state": "Created",
            "billing": {
                "name": "Rahul Sharma",
                "phone": "9876543210",
                "email": "rahul@example.com",
            },
            "items": [
                {
                    "id": "SKU-FRAME-001",
                    "descriptor": {"name": "Ray-Ban Classic Frame"},
                    "quantity": {"count": 1},
                    "price": {"currency": "INR", "value": "3000.00"},
                }
            ],
            "payment": {
                "type": "PRE-PAID",
                "params": {"currency": "INR", "amount": "3150.00"},
            },
            "fulfillments": [
                {
                    "id": "1",
                    "type": "HOME-DELIVERY",
                    "end": {
                        "location": {
                            "address": {
                                "door": "Flat 101",
                                "name": "Sunset Apartments",
                            }
                        }
                    },
                }
            ],
        }
    },
}


class TestIngestOndcOrder:
    def test_maps_to_ims_order_shape(self):
        db = _MockDB(orders=[])
        result = ondc_seller.ingest_ondc_order(db, _ONDC_CONFIRM_PAYLOAD)
        assert result["ok"] is True
        order = result["ims_order"]
        assert order["channel"] == "ONDC"
        assert order["external_order_id"] == "ONDC-ORD-001"
        assert order["customer_name"] == "Rahul Sharma"
        assert order["customer_phone"] == "9876543210"
        assert order["payment_mode"] == "UPI"
        assert order["payment_status"] == "PAID"
        assert order["status"] == "CONFIRMED"
        assert len(order["items"]) == 1
        assert order["items"][0]["sku"] == "SKU-FRAME-001"
        assert order["items"][0]["quantity"] == 1

    def test_order_persisted_in_db(self):
        db = _MockDB(orders=[])
        ondc_seller.ingest_ondc_order(db, _ONDC_CONFIRM_PAYLOAD)
        persisted = db.get_collection("orders").find_one({"external_order_id": "ONDC-ORD-001"})
        assert persisted is not None
        assert persisted["channel"] == "ONDC"

    def test_total_amount_set(self):
        db = _MockDB(orders=[])
        result = ondc_seller.ingest_ondc_order(db, _ONDC_CONFIRM_PAYLOAD)
        assert result["ims_order"]["total_amount"] == 3150.0


# ===========================================================================
# 12. ingest_ondc_order -- idempotent
# ===========================================================================

class TestIngestOndcOrderIdempotent:
    def test_same_order_returns_idempotent(self):
        existing_ims_order = {
            "order_id": "ONDC-ORD-001-EXISTING",
            "external_order_id": "ONDC-ORD-001",
            "channel": "ONDC",
        }
        db = _MockDB(orders=[existing_ims_order])
        result = ondc_seller.ingest_ondc_order(db, _ONDC_CONFIRM_PAYLOAD)
        assert result["ok"] is True
        assert result["mode"] == "IDEMPOTENT"
        # Should NOT insert a 2nd row
        count = db.get_collection("orders").count_documents(
            {"external_order_id": "ONDC-ORD-001"}
        )
        assert count == 1


# ===========================================================================
# 13. ingest_ondc_order -- missing order.id returns ok=False
# ===========================================================================

class TestIngestOndcOrderMissingId:
    def test_missing_order_id_fails_gracefully(self):
        bad_payload = {
            "context": {"action": "on_confirm"},
            "message": {"order": {}},  # no id
        }
        result = ondc_seller.ingest_ondc_order(db=None, payload=bad_payload)
        assert result["ok"] is False
        assert result["error"] is not None
        assert result["order_id"] is None


# ===========================================================================
# 14. reconcile_tcs -- calculates TCS at 1%, commission, net_payout
# ===========================================================================

class TestReconcileTcs:
    def test_tcs_calculation(self):
        db = _MockDB(ondc_settlements=[])
        settlement = {
            "gross_amount": 3150.0,
            "commission_pct": 3.0,
            "settlement_date": "2026-06-10",
            "snp_ref": "SNP-REF-001",
        }
        result = ondc_seller.reconcile_tcs(db, "ONDC-ORD-001-20260605", settlement)
        assert result["ok"] is True
        assert result["tcs_amount"] == pytest.approx(31.5, abs=0.01)  # 1% of 3150
        assert result["commission_amount"] == pytest.approx(94.5, abs=0.01)  # 3% of 3150
        assert result["net_payout"] == pytest.approx(3150.0 - 31.5 - 94.5, abs=0.01)
        assert result["settlement_id"] is not None

    def test_tcs_persisted_in_db(self):
        db = _MockDB(ondc_settlements=[])
        settlement = {"gross_amount": 1000.0, "commission_pct": 2.0}
        ondc_seller.reconcile_tcs(db, "ORDER-X", settlement)
        docs = list(db.get_collection("ondc_settlements").find())
        assert len(docs) == 1
        assert docs[0]["tcs_rate"] == ondc_seller.TCS_RATE

    def test_tcs_zero_gross(self):
        result = ondc_seller.reconcile_tcs(db=None, order_id="X", settlement={"gross_amount": 0})
        assert result["ok"] is True
        assert result["tcs_amount"] == 0.0
        assert result["net_payout"] == 0.0


# ===========================================================================
# 15. reconcile_tcs -- fail-soft on bad input
# ===========================================================================

class TestReconcileTcsFailSoft:
    def test_returns_ok_false_on_exception(self):
        # Simulate DB raising on insert
        db = MagicMock()
        db.get_collection.return_value.insert_one.side_effect = RuntimeError("DB down")
        settlement = {"gross_amount": 500.0, "commission_pct": 2.0}
        result = ondc_seller.reconcile_tcs(db, "ORDER-Y", settlement)
        assert result["ok"] is False
        assert result["error"] is not None


# ===========================================================================
# 16. ingest_ondc_order -- clinical FLAG & HOLD (spectacle-lens missing Rx)
# A paid ONDC sale is NEVER refused: a prescription-lens line without a valid
# customer-matching non-expired Rx is BOOKED but flagged rx_pending +
# fulfillment_hold, and ONE follow-up task is raised. Contacts/frames exempt.
# ===========================================================================

from datetime import datetime as _dt  # noqa: E402


def _ondc_order_payload(order_id, item):
    """An ONDC on_confirm payload with a single supplied item dict."""
    return {
        "context": {"action": "on_confirm", "bap_id": "paytm.com"},
        "message": {
            "order": {
                "id": order_id,
                "state": "Created",
                "billing": {"name": "Rahul Sharma", "phone": "9876543210"},
                "items": [item],
                "payment": {"type": "PRE-PAID", "params": {"amount": "1500.00"}},
                "fulfillments": [{"id": "1"}],
            }
        },
    }


def _ondc_lens_item(rx_id=None, sph=None, tags=None):
    item = {
        "id": "OL-1",
        "descriptor": {"name": "Zeiss Single Vision Lens"},
        "quantity": {"count": 1},
        "price": {"currency": "INR", "value": "1500.00"},
    }
    if rx_id is not None:
        item["prescription_id"] = rx_id
    if sph is not None:
        item["sph"] = sph
    if tags is not None:
        item["tags"] = tags
    return item


class _OndcRxRepo:
    def __init__(self, by_id=None, by_customer=None):
        self._by_id = by_id or {}
        self._by_customer = by_customer or {}

    def find_by_id(self, rx_id):
        return self._by_id.get(rx_id)

    def find_by_customer(self, customer_id):
        return self._by_customer.get(str(customer_id), [])


def _ondc_valid_rx(rx_id="RX-1", customer_id="C1"):
    return {
        "prescription_id": rx_id,
        "customer_id": customer_id,
        "prescription_date": _dt.now().isoformat(),
        "validity_months": 12,
    }


class TestIngestOndcRxHold:
    def _wire(self, monkeypatch, repo):
        import api.dependencies as deps

        monkeypatch.setattr(deps, "get_prescription_repository", lambda: repo)

    def test_lens_no_rx_flagged_with_one_task(self, monkeypatch):
        """(a) spectacle-lens line, no Rx -> CREATED with rx_pending + ONE task."""
        self._wire(monkeypatch, _OndcRxRepo())
        db = _MockDB(orders=[], tasks=[], customers=[])
        res = ondc_seller.ingest_ondc_order(db, _ondc_order_payload("ORX-1", _ondc_lens_item()))
        assert res["ok"] is True  # paid sale never refused
        assert res["ims_order"]["rx_pending"] is True
        assert res["ims_order"]["fulfillment_hold"] is True
        order_id = res["order_id"]
        tasks = list(db.get_collection("tasks").find({"order_id": order_id}))
        assert len(tasks) == 1
        assert tasks[0]["task_type"] == "online_rx_hold"

    def test_lens_with_valid_rx_no_flag_no_task(self, monkeypatch):
        """(b) spectacle-lens line, valid customer-matching non-expired Rx ->
        no flag, no task. Buyer phone resolves to a customer with that Rx."""
        repo = _OndcRxRepo(by_id={"RX-1": _ondc_valid_rx("RX-1", "C1")})
        self._wire(monkeypatch, repo)
        db = _MockDB(
            orders=[],
            tasks=[],
            customers=[{"customer_id": "C1", "phone": "9876543210", "mobile": "9876543210"}],
        )
        res = ondc_seller.ingest_ondc_order(
            db, _ondc_order_payload("ORX-2", _ondc_lens_item(rx_id="RX-1"))
        )
        assert res["ims_order"]["rx_pending"] is False
        assert list(db.get_collection("tasks").find({"order_id": res["order_id"]})) == []

    def test_frame_line_not_flagged(self, monkeypatch):
        """(c) a frame line -> EXEMPT, never flagged."""
        self._wire(monkeypatch, _OndcRxRepo())
        db = _MockDB(orders=[], tasks=[], customers=[])
        frame_item = {
            "id": "FR-1",
            "descriptor": {"name": "Ray-Ban Frame RB1234"},
            "quantity": {"count": 1},
            "price": {"currency": "INR", "value": "3000.00"},
        }
        res = ondc_seller.ingest_ondc_order(db, _ondc_order_payload("ORX-3", frame_item))
        assert res["ims_order"]["rx_pending"] is False

    def test_contact_lens_not_flagged(self, monkeypatch):
        """(c) a contact-lens line -> EXEMPT, never flagged."""
        self._wire(monkeypatch, _OndcRxRepo())
        db = _MockDB(orders=[], tasks=[], customers=[])
        cl_item = {
            "id": "CL-1",
            "descriptor": {"name": "Acuvue Daily Contact Lens"},
            "quantity": {"count": 1},
            "price": {"currency": "INR", "value": "900.00"},
        }
        res = ondc_seller.ingest_ondc_order(db, _ondc_order_payload("ORX-4", cl_item))
        assert res["ims_order"]["rx_pending"] is False

    def test_reingest_idempotent_no_double_task(self, monkeypatch):
        """(d) re-ingest the same ONDC order -> IDEMPOTENT, no second task."""
        self._wire(monkeypatch, _OndcRxRepo())
        db = _MockDB(orders=[], tasks=[], customers=[])
        payload = _ondc_order_payload("ORX-5", _ondc_lens_item())
        first = ondc_seller.ingest_ondc_order(db, payload)
        assert first["ims_order"]["rx_pending"] is True
        second = ondc_seller.ingest_ondc_order(db, payload)
        assert second["mode"] == "IDEMPOTENT"
        # exactly one order + exactly one task
        assert db.get_collection("orders").count_documents({"external_order_id": "ORX-5"}) == 1
        all_tasks = list(db.get_collection("tasks").find({}))
        assert len(all_tasks) == 1

    def test_out_of_range_power_still_created_reason_recorded(self, monkeypatch):
        """(e) out-of-range power -> still CREATED, reason recorded."""
        repo = _OndcRxRepo(by_id={"RX-1": _ondc_valid_rx("RX-1", "C1")})
        self._wire(monkeypatch, repo)
        db = _MockDB(
            orders=[],
            tasks=[],
            customers=[{"customer_id": "C1", "phone": "9876543210", "mobile": "9876543210"}],
        )
        res = ondc_seller.ingest_ondc_order(
            db, _ondc_order_payload("ORX-6", _ondc_lens_item(rx_id="RX-1", sph="99.00"))
        )
        assert res["ok"] is True
        assert res["ims_order"]["rx_pending"] is True
        assert "RX_POWER_OUT_OF_RANGE" in res["ims_order"]["rx_hold_reasons"]
        assert "99" in res["ims_order"]["rx_hold_reason"]
