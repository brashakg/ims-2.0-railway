"""
IMS 2.0 -- the product search finds what a buyer actually types (audit F21)
===========================================================================
Create Purchase Order -> product box. A store manager typed the model number
off the vendor's price list ('8895' for Carrera 'CA 8895'), the brand as
people say it ('ray ban', 'rayban') and a colour ('black'), and every one
answered 'No catalogued products match.' The search matched only the START of
brand / model / SKU per token (anchored ^), so only 'carrera' and a full SKU
ever worked.

The box calls GET /products?search=...&match=anywhere, which is
ProductRepository.search_products(anywhere=True). The SAME endpoint without
`match` is what the till, goods receipt, the command palette, Returns and
QuickShare call, and for them NOTHING changes (owner rule: ask before touching
POS): every word still has to START brand / model / SKU / variant / barcode.
The wide rule would crowd the till -- 'ray' is inside 'Gunmetal Gray' -- so it
is opt-in, and even there what the till's rule finds comes FIRST, so a result
limit can never push it off the list.

These pins run that real query against a real Mongo engine (CI's service
container; mongomock on a dev box with no Mongo). Expected sets are written out
BY HAND.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_product_search_finds_what_is_typed.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.repositories.product_repository import ProductRepository  # noqa: E402


def _open_client():
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
        client = mongomock.MongoClient()
    return client, db_name


def _repo_over(docs):
    client, db_name = _open_client()
    db = client[db_name]
    db["products"].insert_many(docs)
    try:
        yield ProductRepository(db["products"])
    finally:
        try:
            client.drop_database(db_name)
        except Exception:
            pass


@pytest.fixture(scope="module")
def repo():
    # Spine-shaped docs: flat brand / model / color (the colour CODE), the
    # colour WORDS under attributes, exactly as product_master writes them.
    yield from _repo_over(
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
            {
                # The only colour WORD is on the temples (FRAME optional).
                "_id": "P-TMP",
                "product_id": "P-TMP",
                "sku": "FR-VOG-VO5051-W44",
                "brand": "Vogue",
                "model": "VO5051",
                "color": "W44",
                "attributes": {"temple_color": "Tortoise"},
                "is_active": True,
            },
            {
                # A watch: the colour is its dial (WATCH optional), and the
                # manufacturer's barcode printed on the box is its GTIN.
                "_id": "P-WAT",
                "product_id": "P-WAT",
                "sku": "WT-FAS-3217SL-C2",
                "brand": "Fastrack",
                "model": "3217SL",
                "color": "C2",
                "attributes": {"dial_color": "Navy Blue", "gtin": "8901234567893"},
                "is_active": True,
            },
            {
                # A sunglass whose lens tint is the colour word people say.
                "_id": "P-TNT",
                "product_id": "P-TNT",
                "sku": "SG-IDE-IDS2890-C3",
                "brand": "IDEE",
                "model": "IDS2890",
                "color": "C3",
                "attributes": {"tint": "Brown Gradient"},
                "is_active": True,
            },
        ]
    )


def _ids(repo, q):
    """The purchase-order product box: the wide search."""
    docs = repo.search_products(q, anywhere=True)
    # The list and its total are the SAME query -- they may never drift.
    assert repo.count_search_products(q, anywhere=True) == len(docs), q
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
        # A hyphen typed as its own word is spacing, not something to match.
        ("ray - ban", {"P-RB", "P-RB-OLD"}),
        ("CA - 8895", {"P-CAR"}),
        # Colours: the colour word under attributes and the flat colour.
        ("black", {"P-CAR", "P-OAK"}),
        ("g-15", {"P-RB"}),
        ("carrera black", {"P-CAR"}),
        # Every colour word the catalogue stores, not only frame/lens.
        ("tortoise", {"P-TMP"}),
        ("blue", {"P-WAT"}),
        ("gradient", {"P-TNT"}),
        # The manufacturer's barcode on the box (the GTIN), whole or its start.
        ("8901234567893", {"P-WAT"}),
        ("890123", {"P-WAT"}),
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
        # Codes stay anchored at their start: the middle of a SKU, a barcode
        # or a GTIN is not a scan.
        ("0000000031", set()),
        ("8896", set()),
        ("4567893", set()),
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


def test_every_colour_field_the_catalogue_stores_is_searched():
    """Pinned to product_master's own vocabulary, so a colour field added there
    later cannot silently fall out of the search (temple, dial and tint were
    missed once)."""
    from api.services import product_master as pm

    spec_fields = {
        f for s in pm._CATEGORY_SPECS.values() for f in (*s.required, *s.optional)
    }
    words = {k for k in spec_fields | pm._CASE_NAME if "colo" in k or k == "tint"}
    # colour_code is stored flat, as `color`.
    assert "color" in ProductRepository.NAME_SEARCH_FIELDS
    assert {f"attributes.{k}" for k in words - {"colour_code"}} <= set(
        ProductRepository.NAME_SEARCH_FIELDS
    )


@pytest.fixture
def lens():
    """A contact lens whose name carries regex characters: brackets, a plus
    and a dot."""
    yield from _repo_over(
        [
            {
                "_id": "P-CL",
                "product_id": "P-CL",
                "sku": "CL-ACU-OASYS-P250",
                "brand": "Acuvue",
                "model": "Oasys (Moist) +2.50",
                "is_active": True,
            },
            {
                "_id": "P-TIT",
                "product_id": "P-TIT",
                "sku": "WT-TIT-T1500-C1",
                "brand": "Titan",
                "model": "T1500",
                "is_active": True,
            },
        ]
    )


@pytest.mark.parametrize(
    "typed, expected",
    [
        # Typed characters are matched as themselves, never as a pattern:
        # unescaped, '(' and '+2.50' are broken patterns (no results at all),
        # '.' matches every product and 'a|b' matches any 'a' or 'b'.
        ("(moist)", {"P-CL"}),
        ("+2.50", {"P-CL"}),
        ("(", {"P-CL"}),
        (".", {"P-CL"}),
        ("a|b", set()),
        (".*", set()),
        ("c++", set()),
        ("[", set()),
        ("\\", set()),
    ],
)
def test_regex_characters_are_matched_as_typed(lens, typed, expected):
    assert _ids(lens, typed) == expected


# ---------------------------------------------------------------------------
# The till is untouched (owner rule: ask before touching POS)
# ---------------------------------------------------------------------------

LEGACY_FIELDS = ["brand", "model", "sku", "variant", "barcode"]


@pytest.mark.parametrize(
    "q",
    ["ray", "Ray-Ban", "RB", "carrera CA", "SG-", "oak", "2000", "8895", "black", "blue"],
)
def test_the_till_search_is_untouched(repo, q):
    """No `match`: POS, goods receipt, command palette, Returns, QuickShare.
    The old rule exactly -- same rows, same order, same total."""
    legacy = repo.search(q, LEGACY_FIELDS, {"is_active": True})
    assert repo.search_products(q) == legacy, q
    assert repo.count_search_products(q) == len(legacy), q


def test_the_till_does_not_get_the_wide_matches(repo):
    assert repo.search_products("8895") == []
    assert repo.search_products("black") == []
    assert repo.search_products("8901234567893") == []


@pytest.fixture
def crowded():
    """30 Vogue frames in 'Gunmetal Gray' stored BEFORE one Ray-Ban: 'ray' is
    inside 'Gray', so an unranked wide search fills any limit with Vogues."""
    yield from _repo_over(
        [
            {
                "_id": f"P-VOG-{i:02d}",
                "product_id": f"P-VOG-{i:02d}",
                "sku": f"FR-VOG-VO{4000 + i}-GM",
                "brand": "Vogue",
                "model": f"VO{4000 + i}",
                "attributes": {"frame_color": "Gunmetal Gray"},
                "is_active": True,
            }
            for i in range(30)
        ]
        + [
            {
                "_id": "P-RB",
                "product_id": "P-RB",
                "sku": "SG-RAY-RB3025-001",
                "brand": "Ray-Ban",
                "model": "RB3025",
                "is_active": True,
            }
        ]
    )


def test_the_till_strip_still_shows_the_ray_ban(crowded):
    # The POS results strip puts 24 on screen.
    hits = crowded.search_products("ray", limit=24)
    assert [d["product_id"] for d in hits] == ["P-RB"]


def test_wide_search_puts_what_the_till_finds_first(crowded):
    page = crowded.search_products("ray", anywhere=True, limit=20)
    assert page[0]["product_id"] == "P-RB"
    assert len(page) == 20
    assert crowded.count_search_products("ray", anywhere=True) == 31
    # Paging through it returns every match exactly once, the Ray-Ban first.
    seen = []
    for skip in range(0, 31, 7):
        seen += [
            d["product_id"]
            for d in crowded.search_products("ray", anywhere=True, skip=skip, limit=7)
        ]
    assert seen[0] == "P-RB"
    assert len(seen) == len(set(seen)) == 31


def test_the_endpoint_opts_in_only_with_match_anywhere(repo, monkeypatch):
    import api.services.cache as cache_mod
    from api.routers import products as products_mod

    class _DictCache:
        # A real cache: the till's answer for '8895' must not be served to
        # the wide search (or back), so the key has to carry the rule.
        TTL_MEDIUM = 0

        def __init__(self):
            self.d = {}

        def get(self, k):
            return self.d.get(k)

        def set(self, k, v, **kw):
            self.d[k] = v

    monkeypatch.setattr(cache_mod, "cache", _DictCache())
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: repo)

    def _list(**kw):
        params = dict(
            category=None,
            brand=None,
            search=None,
            tag=None,
            created_by=None,
            store_id=None,
            skip=0,
            limit=50,
            is_active=None,
            photo=None,
            current_user={
                "user_id": "u1",
                "roles": ["STORE_MANAGER"],
                "active_store_id": "S1",
            },
        )
        params.update(kw)
        out = asyncio.run(products_mod.list_products(**params))
        return {p["product_id"] for p in out["products"]}, out["total_count"]

    assert _list(search="8895") == (set(), 0)
    assert _list(search="8895", match="anywhere") == ({"P-CAR"}, 1)
    assert _list(search="8895") == (set(), 0)
