"""
Hub Phase 0 -- the catalog-done chokepoint.

Locks the owner-approved Buy Desk Phase 0 contract (docs/roadmap/
PRODUCT_HUB_RECOMMENDATION.md sec 3/4/5b):

  * compute_catalog_status(doc) is THE single done-rule: a product is ACTIVE only
    when its category resolves, every per-category required attribute is present,
    mrp/offer are valid (offer<=mrp), cost_price > 0, and hsn/gst are stamped;
    otherwise it is DRAFT with done_gaps naming EVERY missing piece (collect-all).
  * cost_price is part of the done-rule but is NOT a create-blocker -- a STRICT
    create without it SUCCEEDS and lands DRAFT (cost is often only known at GRN).
  * catalog_readiness() is the read-side view of the SAME rule (no second rule);
    purchasable = complete AND is_active.
  * restamp_on_update() auto-promotes a DRAFT the edit completes, and -- the
    load-bearing NEVER-DEMOTE rule (DECISION A: forward-only gate) -- leaves a row
    that READS as ACTIVE (incl. a legacy row with NO catalog_status) entirely
    untouched, even when that row is incomplete-by-rule. This is what stops a
    routine edit from 422-ing / demoting the ~10,800 backfilled legacy rows.
  * the one-time migration (backfill_catalog_status_active) stamps ACTIVE on every
    row missing catalog_status, never clobbers an explicit DRAFT/ACTIVE, and is
    idempotent.

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_hub_phase0_catalog_status.py -q
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import product_master as pm  # noqa: E402
from scripts import backfill_catalog_status_active as mig  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _complete_frame(**over):
    """A doc that PASSES the done-rule: category + colour_code + pricing + cost +
    derived tax fields. Returned as the persisted-spine shape compute_catalog_status
    reads."""
    doc = {
        "category": "FRAME",
        "attributes": {
            "brand_name": "Ray-Ban",
            "model_no": "RB-2140",
            "colour_code": "BLK",
        },
        "mrp": 5000.0,
        "offer_price": 4500.0,
        "cost_price": 2000.0,
        "hsn_code": "9003",
        "gst_rate": 5.0,
    }
    doc.update(over)
    return doc


# ===========================================================================
# 1. compute_catalog_status -- THE done-rule
# ===========================================================================


def test_complete_frame_is_active_no_gaps():
    status, gaps = pm.compute_catalog_status(_complete_frame())
    assert status == pm.CATALOG_STATUS_ACTIVE
    assert gaps == []


def test_missing_cost_is_draft_naming_cost():
    doc = _complete_frame()
    doc.pop("cost_price")
    status, gaps = pm.compute_catalog_status(doc)
    assert status == pm.CATALOG_STATUS_DRAFT
    assert "cost_price" in gaps
    # cost is the ONLY gap on an otherwise-complete frame.
    assert gaps == ["cost_price"]


def test_zero_cost_is_draft():
    status, gaps = pm.compute_catalog_status(_complete_frame(cost_price=0))
    assert status == pm.CATALOG_STATUS_DRAFT
    assert "cost_price" in gaps


def test_missing_required_attribute_is_draft():
    doc = _complete_frame()
    doc["attributes"] = {
        "brand_name": "Ray-Ban",
        "model_no": "RB-2140",
    }  # no colour_code
    status, gaps = pm.compute_catalog_status(doc)
    assert status == pm.CATALOG_STATUS_DRAFT
    assert "colour_code" in gaps


def test_offer_above_mrp_is_blocker():
    status, gaps = pm.compute_catalog_status(
        _complete_frame(mrp=4000.0, offer_price=4500.0)
    )
    assert status == pm.CATALOG_STATUS_DRAFT
    assert "MRP_BELOW_OFFER" in gaps


def test_unknown_category_names_category_gap():
    status, gaps = pm.compute_catalog_status(_complete_frame(category="NOPE"))
    assert status == pm.CATALOG_STATUS_DRAFT
    assert "category" in gaps


def test_collect_all_missing_not_raise_on_first():
    # A bare doc: category missing AND pricing AND cost all gone -> all named.
    status, gaps = pm.compute_catalog_status({"category": "FRAME", "attributes": {}})
    assert status == pm.CATALOG_STATUS_DRAFT
    # colour_code (required attr) + mrp + offer + cost_price + hsn + gst all surface.
    for expected in (
        "colour_code",
        "mrp",
        "offer_price",
        "cost_price",
        "hsn_code",
        "gst_rate",
    ):
        assert expected in gaps, f"{expected} missing from {gaps}"


def test_legacy_top_level_identity_overlay_reads_complete():
    # A legacy row stores identity top-level (brand/model/color), no attributes.
    legacy = {
        "category": "FRAME",
        "brand": "Ray-Ban",
        "model": "RB-2140",
        "color": "BLK",
        "mrp": 5000.0,
        "offer_price": 4500.0,
        "cost_price": 1500.0,
        "hsn_code": "9003",
        "gst_rate": 5.0,
        "attributes": {},
    }
    status, gaps = pm.compute_catalog_status(legacy)
    assert status == pm.CATALOG_STATUS_ACTIVE, gaps


# ===========================================================================
# 2. catalog_readiness + effective_catalog_status
# ===========================================================================


def test_readiness_complete_active_is_purchasable():
    r = pm.catalog_readiness(_complete_frame(is_active=True))
    assert r["complete"] is True
    assert r["missing"] == []
    assert r["purchasable"] is True


def test_readiness_complete_but_inactive_not_purchasable():
    r = pm.catalog_readiness(_complete_frame(is_active=False))
    assert r["complete"] is True
    assert r["purchasable"] is False


def test_readiness_missing_is_active_defaults_purchasable():
    # A legacy row with no is_active flag reads as active -> purchasable when complete.
    doc = _complete_frame()
    assert "is_active" not in doc
    assert pm.catalog_readiness(doc)["purchasable"] is True


def test_readiness_blocker_separated_from_missing():
    r = pm.catalog_readiness(_complete_frame(mrp=4000.0, offer_price=4500.0))
    assert "MRP_BELOW_OFFER" in r["blockers"]
    assert "MRP_BELOW_OFFER" not in r["missing"]
    assert r["purchasable"] is False


def test_effective_status_missing_reads_active():
    assert pm.effective_catalog_status({}) == pm.CATALOG_STATUS_ACTIVE
    assert (
        pm.effective_catalog_status({"catalog_status": ""}) == pm.CATALOG_STATUS_ACTIVE
    )


def test_effective_status_explicit_draft_stays_draft():
    assert (
        pm.effective_catalog_status({"catalog_status": "DRAFT"})
        == pm.CATALOG_STATUS_DRAFT
    )
    # case/space tolerant
    assert (
        pm.effective_catalog_status({"catalog_status": " draft "})
        == pm.CATALOG_STATUS_DRAFT
    )


# ===========================================================================
# 3. restamp_on_update -- auto-promote + NEVER-DEMOTE
# ===========================================================================


def test_restamp_promotes_draft_when_cost_filled():
    current = _complete_frame(catalog_status="DRAFT", done_gaps=["cost_price"])
    current.pop("cost_price")
    out = pm.restamp_on_update(current, {"cost_price": 1800.0})
    assert out == {"catalog_status": pm.CATALOG_STATUS_ACTIVE, "done_gaps": []}


def test_restamp_draft_stays_draft_refreshes_gaps():
    current = {
        "category": "FRAME",
        "attributes": {"brand_name": "B", "model_no": "M"},
        "catalog_status": "DRAFT",
        "done_gaps": ["colour_code", "mrp", "offer_price"],
    }
    # Fill mrp/offer but still no colour_code/cost -> stays DRAFT, gaps refreshed.
    out = pm.restamp_on_update(current, {"mrp": 1000.0, "offer_price": 900.0})
    assert out["catalog_status"] == pm.CATALOG_STATUS_DRAFT
    assert "colour_code" in out["done_gaps"]
    assert "cost_price" in out["done_gaps"]
    assert "mrp" not in out["done_gaps"]


def test_restamp_never_demotes_legacy_active_row():
    # THE load-bearing case: a legacy row reads ACTIVE (no catalog_status) but is
    # incomplete-by-rule (no cost_price). Editing it (e.g. a price tweak) must NOT
    # touch catalog_status -- no demote, no churn, no raise.
    legacy = {
        "category": "FRAME",
        "brand": "RB",
        "model": "M",
        "color": "BLK",
        "mrp": 5000.0,
        "offer_price": 4500.0,
        "hsn_code": "9003",
        "gst_rate": 5.0,
        "attributes": {},
    }  # NO cost_price, NO catalog_status
    assert pm.effective_catalog_status(legacy) == pm.CATALOG_STATUS_ACTIVE
    out = pm.restamp_on_update(legacy, {"offer_price": 4400.0})
    assert out == {}  # untouched


def test_restamp_never_demotes_explicit_active_row():
    active = _complete_frame(catalog_status="ACTIVE")
    # An edit that would break the rule (clear cost via 0) still leaves it ACTIVE.
    out = pm.restamp_on_update(active, {"cost_price": 0})
    assert out == {}


# ===========================================================================
# 4. normalise_payload -- strict create not blocked by missing cost
# ===========================================================================


def test_strict_create_without_cost_succeeds_as_draft():
    # The blast-radius fix: a STRICT (as_draft=False) create that is complete on
    # attributes + pricing but has no cost must NOT raise -- it persists DRAFT.
    doc = pm.normalise_payload(
        category="FRAME",
        attributes={"brand_name": "Ray-Ban", "model_no": "RB-1", "colour_code": "BLK"},
        mrp=5000.0,
        offer_price=4500.0,
        sku="FR-NOCOST-1",
        cost_price=None,
        as_draft=False,
    )
    assert doc["catalog_status"] == pm.CATALOG_STATUS_DRAFT
    assert doc["done_gaps"] == ["cost_price"]


def test_strict_create_with_cost_is_active():
    doc = pm.normalise_payload(
        category="FRAME",
        attributes={"brand_name": "Ray-Ban", "model_no": "RB-2", "colour_code": "BLK"},
        mrp=5000.0,
        offer_price=4500.0,
        sku="FR-COST-1",
        cost_price=2000.0,
        as_draft=False,
    )
    assert doc["catalog_status"] == pm.CATALOG_STATUS_ACTIVE
    assert doc["done_gaps"] == []


def test_strict_create_missing_required_attr_still_422s():
    # cost is soft, but a missing required ATTRIBUTE is still a loud 422 in strict.
    with pytest.raises(pm.ProductMasterError) as ei:
        pm.normalise_payload(
            category="FRAME",
            attributes={"brand_name": "Ray-Ban", "model_no": "RB-3"},  # no colour_code
            mrp=5000.0,
            offer_price=4500.0,
            sku="FR-NOCOLOUR",
            cost_price=2000.0,
            as_draft=False,
        )
    assert ei.value.status == 422


def test_as_draft_incomplete_above_floor_persists_draft():
    doc = pm.normalise_payload(
        category="FRAME",
        attributes={"brand_name": "Ray-Ban", "model_no": "RB-4"},  # no colour_code
        mrp=5000.0,
        offer_price=4500.0,
        sku="FR-DRAFT-1",
        cost_price=None,
        as_draft=True,
    )
    assert doc["catalog_status"] == pm.CATALOG_STATUS_DRAFT
    assert "colour_code" in doc["done_gaps"]
    assert "cost_price" in doc["done_gaps"]


def test_as_draft_below_floor_raises():
    # Below the floor (no brand) even a draft is refused.
    with pytest.raises(pm.ProductMasterError):
        pm.normalise_payload(
            category="FRAME",
            attributes={"model_no": "RB-5"},  # no brand_name
            mrp=5000.0,
            offer_price=4500.0,
            sku="FR-FLOOR",
            cost_price=None,
            as_draft=True,
        )


def test_offer_above_mrp_raises_in_both_modes():
    for as_draft in (False, True):
        with pytest.raises(pm.ProductMasterError):
            pm.normalise_payload(
                category="FRAME",
                attributes={"brand_name": "B", "model_no": "M", "colour_code": "BLK"},
                mrp=4000.0,
                offer_price=4500.0,
                sku=f"FR-OFFER-{as_draft}",
                cost_price=1000.0,
                as_draft=as_draft,
            )


# ===========================================================================
# 5. Migration -- backfill catalog_status=ACTIVE (DECISION A)
# ===========================================================================


class _FakeProducts:
    """A faithful list-backed products collection honouring exactly the migration
    filter (catalog_status missing / None / blank). Lets the migration LOGIC be
    verified deterministically without a live Mongo / without depending on the
    MockCollection $exists handling."""

    def __init__(self, docs):
        self._docs = [dict(d) for d in docs]

    @staticmethod
    def _no_status(doc):
        raw = doc.get("catalog_status", None)
        return raw is None or (isinstance(raw, str) and not raw.strip())

    def count_documents(self, _filter):
        return sum(1 for d in self._docs if self._no_status(d))

    def update_many(self, _filter, update):
        n = 0
        setter = update.get("$set", {})
        for d in self._docs:
            if self._no_status(d):
                d.update(setter)
                n += 1
        return type("R", (), {"modified_count": n})()


def test_migration_dry_run_writes_nothing():
    coll = _FakeProducts([{"sku": "A"}, {"sku": "B"}])
    res = mig._backfill(coll, apply=False)
    assert res["matched"] == 2
    assert res["modified"] == 0
    # nothing stamped
    assert all("catalog_status" not in d for d in coll._docs)


def test_migration_apply_stamps_only_missing():
    coll = _FakeProducts(
        [
            {"sku": "legacy-1"},
            {"sku": "legacy-2", "catalog_status": ""},
            {"sku": "draft-1", "catalog_status": "DRAFT"},
            {"sku": "active-1", "catalog_status": "ACTIVE"},
        ]
    )
    res = mig._backfill(coll, apply=True)
    assert res["matched"] == 2  # only the two with no/blank status
    assert res["modified"] == 2
    by_sku = {d["sku"]: d for d in coll._docs}
    assert by_sku["legacy-1"]["catalog_status"] == "ACTIVE"
    assert by_sku["legacy-2"]["catalog_status"] == "ACTIVE"
    # never clobbered
    assert by_sku["draft-1"]["catalog_status"] == "DRAFT"
    assert by_sku["active-1"]["catalog_status"] == "ACTIVE"


def test_migration_idempotent():
    coll = _FakeProducts([{"sku": "x"}, {"sku": "y"}])
    mig._backfill(coll, apply=True)
    res2 = mig._backfill(coll, apply=True)
    assert res2["matched"] == 0
    assert res2["modified"] == 0


# ---- real-Mongo coverage of the actual $exists/$regex filter (CI only) ----


@pytest.fixture
def mongo_products():
    """Real mongo:7.0 products collection in a throwaway db. Skips if absent."""
    try:
        from pymongo import MongoClient
        from pymongo.errors import ServerSelectionTimeoutError
    except ImportError:
        pytest.skip("pymongo unavailable")
        return None
    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except (ServerSelectionTimeoutError, Exception):  # noqa: BLE001
        pytest.skip(f"Mongo unavailable at {uri}")
        return None
    db_name = f"ims_test_hubp0_{uuid.uuid4().hex[:8]}"
    db = client[db_name]
    try:
        yield db["products"]
    finally:
        try:
            client.drop_database(db_name)
        except Exception:  # noqa: BLE001
            pass
        client.close()


def test_migration_real_filter_stamps_only_legacy(mongo_products):
    mongo_products.insert_many(
        [
            {"sku": "no-field"},
            {"sku": "null", "catalog_status": None},
            {"sku": "empty", "catalog_status": ""},
            {"sku": "ws", "catalog_status": "   "},
            {"sku": "draft", "catalog_status": "DRAFT"},
            {"sku": "active", "catalog_status": "ACTIVE"},
        ]
    )
    res = mig._backfill(mongo_products, apply=True)
    assert res["matched"] == 4
    assert res["modified"] == 4
    got = {d["sku"]: d.get("catalog_status") for d in mongo_products.find({})}
    assert got["no-field"] == "ACTIVE"
    assert got["null"] == "ACTIVE"
    assert got["empty"] == "ACTIVE"
    assert got["ws"] == "ACTIVE"
    assert got["draft"] == "DRAFT"
    assert got["active"] == "ACTIVE"
    # idempotent on the real filter too
    assert mig._backfill(mongo_products, apply=True)["matched"] == 0
