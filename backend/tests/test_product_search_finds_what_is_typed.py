"""
IMS 2.0 -- the product search finds what a buyer actually types (audit F21)
===========================================================================
Create Purchase Order -> product box. A store manager typed the model number
off the vendor's price list ('8895' for Carrera 'CA 8895'), the brand as
people say it ('ray ban', 'rayban') and a colour ('black'), and every one
answered 'No catalogued products match.' The search matched only the START of
brand / model / SKU per token (anchored ^), so only 'carrera' and a full SKU
ever worked.

The box calls GET /products?search=, which is ProductRepository.search_products
-- the ONE product search (POS uses the same one). These pins run that real
query against a real Mongo engine (CI's service container; mongomock on a dev
box with no Mongo). Expected sets are written out BY HAND.

Kept on purpose: a SKU and a barcode still match from their START, and every
search the old rule answered still answers (the new rule only adds matches).

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_product_search_finds_what_is_typed.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.repositories.product_repository import ProductRepository  # noqa: E402


@pytest.fixture(scope="module")
def repo():
    from pymongo import MongoClient

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    db_name = f"ims_test_prodsearch_{uuid.uuid4().hex[:8]}"
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except Exception:
        try:
            import mongomock
        except ImportError:
            pytest.skip("no Mongo and no mongomock available")
            return
        client = mongomock.MongoClient()
    db = client[db_name]
    # Spine-shaped docs: flat brand / model / color (the colour CODE), the
    # colour WORDS under attributes, exactly as product_master writes them.
    db["products"].insert_many(
        [
            {
                "_id": "P-CAR",
                "product_id": "P-CAR",
                "sku": "FR-CAR-CA8895-C1",
                "brand": "Carrera",
                "model": "CA 8895",
                "color": "C1",
                "attributes": {"frame_color": "Matte Black"},
                "barcode": "2000000000031",
                "is_active": True,
            },
            {
                "_id": "P-RB",
                "product_id": "P-RB",
                "sku": "SG-RAY-RB3025-001",
                "brand": "Ray-Ban",
                "model": "RB3025",
                "color": "001",
                "attributes": {"frame_color": "Gold", "lens_colour": "G-15 Green"},
                "is_active": True,
            },
            {
                # A legacy row that spelt the brand with a space.
                "_id": "P-RB-OLD",
                "product_id": "P-RB-OLD",
                "sku": "FR-RB-2140",
                "brand": "Ray Ban",
                "model": "RB2140",
                "is_active": True,
            },
            {
                "_id": "P-OAK",
                "product_id": "P-OAK",
                "sku": "SG-OAK-OO9208-01",
                "brand": "Oakley",
                "model": "OO9208",
                "color": "BLACK",
                "is_active": True,
            },
        ]
    )
    try:
        yield ProductRepository(db["products"])
    finally:
        try:
            client.drop_database(db_name)
        except Exception:
            pass


def _ids(repo, q):
    docs = repo.search_products(q)
    # The list and its total are the SAME query -- they may never drift.
    assert repo.count_search_products(q) == len(docs), q
    return {d["product_id"] for d in docs}


@pytest.mark.parametrize(
    "typed, expected",
    [
        # The model number off the vendor's price list is the END of 'CA 8895'.
        ("8895", {"P-CAR"}),
        ("ca8895", {"P-CAR"}),
        ("3025", {"P-RB"}),
        # Brand spelling: spaces and hyphens do not matter, either way round.
        ("ray ban", {"P-RB", "P-RB-OLD"}),
        ("rayban", {"P-RB", "P-RB-OLD"}),
        ("RAY-BAN", {"P-RB", "P-RB-OLD"}),
        ("ray ban 3025", {"P-RB"}),
        # Colours: the colour word under attributes and the flat colour.
        ("black", {"P-CAR", "P-OAK"}),
        ("g-15", {"P-RB"}),
        ("carrera black", {"P-CAR"}),
    ],
)
def test_what_a_buyer_types_finds_the_frame(repo, typed, expected):
    assert _ids(repo, typed) == expected


@pytest.mark.parametrize(
    "typed, expected",
    [
        # What already worked keeps working.
        ("carrera", {"P-CAR"}),
        ("FR-CAR-CA8895-C1", {"P-CAR"}),
        ("2000000000031", {"P-CAR"}),
        ("oakley OO92", {"P-OAK"}),
        # Codes stay anchored at their start: the middle of a SKU or a
        # barcode is not a scan.
        ("0000000031", set()),
        ("8896", set()),
    ],
)
def test_codes_still_match_from_their_start(repo, typed, expected):
    assert _ids(repo, typed) == expected


def test_new_rule_only_adds_matches(repo):
    """Every search the old tokenized-prefix rule answered still answers."""
    legacy_fields = ["brand", "model", "sku", "variant", "barcode"]
    for q in ("ray", "Ray-Ban", "RB", "carrera CA", "SG-", "oak", "2000"):
        legacy = {
            d["product_id"] for d in repo.search(q, legacy_fields, {"is_active": True})
        }
        assert _ids(repo, q) >= legacy, q
