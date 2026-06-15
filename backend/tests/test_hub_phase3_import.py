"""
Hub Phase 3 -- vendor price-list import: the PURE match/map core.

Locks the headline owner requirement (docs/roadmap/PRODUCT_HUB_RECOMMENDATION.md
sec 5.2): a vendor SKU that differs from the IMS master only by punctuation /
leading zeros must MATCH -- owner example IMS "RB 3025 001/21" vs vendor
"0RB3025001/21". Also covers SUGGESTED (fuzzy) vs NEW classification, the alias
flywheel, the row->canonical-payload mapping (as_draft), and CSV parsing.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_hub_phase3_import.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")

from api.services import catalog_import as ci  # noqa: E402

# ---------------------------------------------------------------------------
# normalize_sku + similarity
# ---------------------------------------------------------------------------


def test_normalize_folds_punctuation_and_leading_zeros():
    # the owner's headline example
    assert ci.normalize_sku("RB 3025 001/21") == ci.normalize_sku("0RB3025001/21")
    assert ci.normalize_sku("RB 3025 001/21") == "RB302500121"
    # case + dashes + dots all fold
    assert ci.normalize_sku("rb-3025-001.21") == "RB302500121"


def test_normalize_blank_and_none():
    assert ci.normalize_sku(None) == ""
    assert ci.normalize_sku("   ") == ""
    assert ci.normalize_sku("000") == ""  # all-zeros -> empty after lstrip


def test_similarity_identical_normalized_is_one():
    assert ci.sku_similarity("RB 3025 001/21", "0RB3025001/21") == 1.0
    assert ci.sku_similarity("ABC", "") == 0.0
    assert 0.0 < ci.sku_similarity("RB3025", "RB3026") < 1.0


# ---------------------------------------------------------------------------
# classify_vendor_sku: MATCHED / SUGGESTED / NEW
# ---------------------------------------------------------------------------

_PRODUCTS = [
    {"product_id": "P-RB", "sku": "RB 3025 001/21"},
    {"product_id": "P-OAK", "sku": "OX8046-0152"},
]


def test_classify_alias_hit_is_matched():
    idx = ci.build_alias_index([{"vendor_sku": "0RB3025001/21", "product_id": "P-RB"}])
    out = ci.classify_vendor_sku("0RB3025001/21", alias_index=idx, products=[])
    assert out["status"] == ci.MATCH_MATCHED
    assert out["product_id"] == "P-RB"
    assert out["score"] == 1.0


def test_classify_normalized_exact_is_matched_without_alias():
    out = ci.classify_vendor_sku("0RB3025001/21", alias_index={}, products=_PRODUCTS)
    assert out["status"] == ci.MATCH_MATCHED
    assert out["product_id"] == "P-RB"  # normalized-exact against the spine SKU


def test_classify_fuzzy_is_suggested():
    # one transposed/extra char -> high similarity but not exact -> SUGGESTED
    out = ci.classify_vendor_sku(
        "RB 3025 001/2", alias_index={}, products=_PRODUCTS, threshold=0.8
    )
    assert out["status"] == ci.MATCH_SUGGESTED
    assert out["product_id"] == "P-RB"
    assert 0.8 <= out["score"] < 1.0


def test_classify_unrelated_is_new():
    out = ci.classify_vendor_sku("ZZZ-9999", alias_index={}, products=_PRODUCTS)
    assert out["status"] == ci.MATCH_NEW
    assert out["product_id"] is None


def test_classify_blank_is_new():
    out = ci.classify_vendor_sku("", alias_index={}, products=_PRODUCTS)
    assert out["status"] == ci.MATCH_NEW


def test_classify_is_deterministic_on_ties():
    # two products with the same SKU -> stable pick (smallest product_id)
    prods = [
        {"product_id": "P-Z", "sku": "AB1234"},
        {"product_id": "P-A", "sku": "AB1234"},
    ]
    out = ci.classify_vendor_sku("AB1234", alias_index={}, products=prods)
    assert out["status"] == ci.MATCH_MATCHED
    # normalized-exact returns on first hit; ensure repeated runs agree
    out2 = ci.classify_vendor_sku("AB1234", alias_index={}, products=prods)
    assert out["product_id"] == out2["product_id"]


# ---------------------------------------------------------------------------
# column mapping + row -> payload
# ---------------------------------------------------------------------------


def test_guess_column_map_detects_common_headers():
    headers = ["Item Code", "Brand", "Model No", "Colour", "MRP", "Dealer Price", "HSN"]
    m = ci.guess_column_map(headers)
    assert m["sku"] == "Item Code"
    assert m["brand_name"] == "Brand"
    assert m["model_no"] == "Model No"
    assert m["colour_code"] == "Colour"
    assert m["mrp"] == "MRP"
    assert m["cost_price"] == "Dealer Price"
    assert m["hsn_code"] == "HSN"


def test_map_row_builds_as_draft_payload_with_clean_money():
    row = {
        "Item Code": "0RB3025001/21",
        "Brand": "Ray-Ban",
        "Model No": "3025",
        "Colour": "001/21",
        "MRP": "Rs. 7,990.00",
        "Dealer Price": "4,500",
        "HSN": "9004",
    }
    m = ci.guess_column_map(list(row.keys()))
    payload = ci.map_row_to_product(row, m)
    assert payload["as_draft"] is True
    assert payload["sku"] == "0RB3025001/21"
    assert payload["attributes"]["brand_name"] == "Ray-Ban"
    assert payload["attributes"]["model_no"] == "3025"
    assert payload["attributes"]["colour_code"] == "001/21"
    assert payload["mrp"] == 7990.0  # currency + commas stripped
    assert payload["cost_price"] == 4500.0
    assert payload["hsn_code"] == "9004"


def test_map_row_omits_absent_fields():
    payload = ci.map_row_to_product({"sku": "X"}, {"sku": "sku"})
    assert payload["sku"] == "X"
    assert payload["as_draft"] is True
    assert "mrp" not in payload and "cost_price" not in payload


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def test_parse_csv_rows():
    text = "sku,brand,mrp\nRB-1,RayBan,5000\nOX-2,Oakley,6000\n"
    rows = ci.parse_csv(text)
    assert len(rows) == 2
    assert rows[0]["sku"] == "RB-1" and rows[0]["brand"] == "RayBan"
    assert rows[1]["mrp"] == "6000"


def test_parse_csv_empty():
    assert ci.parse_csv("") == []


# ===========================================================================
# Router: /catalog-import/preview + /commit (driven directly with fakes)
# ===========================================================================

import asyncio  # noqa: E402

import pytest  # noqa: E402
from api.routers import catalog_import as cir  # noqa: E402

_CM = {"user_id": "u-cat", "roles": ["CATALOG_MANAGER"]}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.upserts = []

    def find(self, flt=None, proj=None):
        flt = flt or {}
        return [d for d in self.docs if all(d.get(k) == v for k, v in flt.items())]

    def update_one(self, flt, update, upsert=False):
        self.upserts.append((dict(flt), dict(update), upsert))
        return True


class _FakeDB:
    def __init__(self, products=None, aliases=None):
        self.cols = {
            "products": _FakeColl(products),
            "vendor_sku_aliases": _FakeColl(aliases),
        }

    def get_collection(self, name):
        return self.cols.setdefault(name, _FakeColl())


def test_preview_classifies_and_maps(monkeypatch):
    db = _FakeDB(
        products=[{"product_id": "P-RB", "sku": "RB 3025 001/21"}],
        aliases=[],
    )
    monkeypatch.setattr(cir, "_get_db", lambda: db)
    csv_text = (
        "sku,brand,model,mrp,cost\n"
        "0RB3025001/21,Ray-Ban,3025,7990,4500\n"  # MATCHED (normalized-exact)
        "ZZZ-NEW-1,Acme,X,1000,600\n"  # NEW
    )
    body = cir.ImportPreviewRequest(vendor_id="V1", format="csv", content=csv_text)
    out = _run(cir.preview_import(body, _CM))
    assert out["total"] == 2
    statuses = {r["vendor_sku"]: r["match"]["status"] for r in out["rows"]}
    assert statuses["0RB3025001/21"] == ci.MATCH_MATCHED
    assert statuses["ZZZ-NEW-1"] == ci.MATCH_NEW
    # the matched row points at the spine product; both rows carry as_draft payloads
    matched = next(r for r in out["rows"] if r["vendor_sku"] == "0RB3025001/21")
    assert matched["match"]["product_id"] == "P-RB"
    assert all(r["payload"]["as_draft"] is True for r in out["rows"])


def test_preview_pdf_ai_unavailable_400(monkeypatch):
    async def _no_rows(_text):
        return []

    monkeypatch.setattr(cir._ci, "parse_pdf_via_ai", _no_rows)
    monkeypatch.setattr(cir, "_get_db", lambda: _FakeDB())
    body = cir.ImportPreviewRequest(vendor_id="V1", format="pdf", content="garbage")
    with pytest.raises(Exception) as ei:
        _run(cir.preview_import(body, _CM))
    assert getattr(ei.value, "status_code", None) == 400


def test_commit_create_lands_draft_and_teaches_alias(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(cir, "_get_db", lambda: db)
    monkeypatch.setattr(cir, "get_product_repository", lambda: object())

    created_payloads = []

    def _fake_create(payload, **kw):
        created_payloads.append(payload)
        assert payload.get("as_draft") is True  # imports ALWAYS land DRAFT
        assert kw.get("source") == "IMPORT"
        return {"product_id": "NEW-1"}

    monkeypatch.setattr(cir._pm, "create_via_door", _fake_create)
    body = cir.ImportCommitRequest(
        vendor_id="V1",
        rows=[
            cir.ImportCommitRow(
                action="CREATE",
                vendor_sku="ZZZ-1",
                payload={"category": "FRAME", "attributes": {"brand_name": "Acme"}},
            )
        ],
    )
    out = _run(cir.commit_import(body, _CM))
    assert out["created"] == 1
    assert out["created_products"][0]["product_id"] == "NEW-1"
    # the flywheel learned ZZZ-1 -> NEW-1
    upserts = db.cols["vendor_sku_aliases"].upserts
    assert any(
        u[0]["vendor_sku"] == "ZZZ-1" and u[1]["$set"]["product_id"] == "NEW-1"
        for u in upserts
    )


def test_commit_link_writes_alias_only(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(cir, "_get_db", lambda: db)
    monkeypatch.setattr(cir, "get_product_repository", lambda: object())

    def _boom(*a, **k):
        raise AssertionError("create must not be called for LINK")

    monkeypatch.setattr(cir._pm, "create_via_door", _boom)
    body = cir.ImportCommitRequest(
        vendor_id="V1",
        rows=[cir.ImportCommitRow(action="LINK", vendor_sku="VS-9", product_id="P-9")],
    )
    out = _run(cir.commit_import(body, _CM))
    assert out["linked"] == 1 and out["created"] == 0
    assert db.cols["vendor_sku_aliases"].upserts[0][1]["$set"]["product_id"] == "P-9"


def test_commit_create_validation_error_collected(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(cir, "_get_db", lambda: db)
    monkeypatch.setattr(cir, "get_product_repository", lambda: object())

    def _raise(payload, **kw):
        raise cir._pm.ProductMasterError("bad", status=422, field="category")

    monkeypatch.setattr(cir._pm, "create_via_door", _raise)
    body = cir.ImportCommitRequest(
        vendor_id="V1",
        rows=[cir.ImportCommitRow(action="CREATE", vendor_sku="Z", payload={})],
    )
    out = _run(cir.commit_import(body, _CM))
    assert out["created"] == 0
    assert out["errors"][0]["field"] == "category"


def test_commit_skip_does_nothing(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(cir, "_get_db", lambda: db)
    monkeypatch.setattr(cir, "get_product_repository", lambda: object())
    body = cir.ImportCommitRequest(
        vendor_id="V1", rows=[cir.ImportCommitRow(action="SKIP", vendor_sku="Z")]
    )
    out = _run(cir.commit_import(body, _CM))
    assert out["skipped"] == 1 and out["created"] == 0 and out["linked"] == 0
