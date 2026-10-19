"""Historical returns never create sellable stock while recovery lacks proof."""

from types import SimpleNamespace

import mongomock
import pytest
from bson import ObjectId

from test_online_order_status import (
    _counter_return, _historical_order, _historical_refund, _minted,
    _retry, _set, _stock_in, returns_router, shopify_refund,
)
from test_online_order_mapper import wired  # noqa: F401
from test_shopify_order_catchup import pull  # noqa: F401
from test_shopify_status_catchup import swept  # noqa: F401

# The doors under test number their GST documents (a return's credit note,
# a debit note): these fakes hold no store records, so a stub mint numbers
# them (conftest.gst_numbers). The mint is tested in test_gst_document_numbers.
pytestmark = pytest.mark.usefixtures("gst_numbers")


def _fail_only_stock_in(monkeypatch, tasks):
    insert = tasks.insert_one

    def fail(doc, *args, **kwargs):
        if doc.get("task_type") == "historical_stock_in":
            raise RuntimeError("stock-in task store unavailable")
        return insert(doc, *args, **kwargs)

    monkeypatch.setattr(tasks, "insert_one", fail)
    return insert


@pytest.mark.parametrize("saved_stamp", [True, False, None, "missing"])
def test_physical_shop_retry_without_parent_never_mints(swept, monkeypatch, saved_stamp):
    oid = 60901
    _historical_order(swept, oid)
    _set(swept, oid, store_id="BV-GANGA-01")
    tasks = swept["db"]["tasks"]
    _fail_only_stock_in(monkeypatch, tasks)
    out = _counter_return(swept, oid)
    ret = swept["returns"].find_one({"return_id": out["return_id"]})
    assert ret["historical"] is True
    assert ret["store_id"] == "BV-GANGA-01" and not out["restock_applied"]
    update = ({"$unset": {"historical": ""}} if saved_stamp == "missing"
              else {"$set": {"historical": saved_stamp}})
    swept["returns"].update_one({"return_id": ret["return_id"]}, update)
    monkeypatch.setattr(returns_router, "_load_order_for_restock", lambda _: None)

    result = _retry(ret)

    assert result["restock_applied"] is False and _minted(swept) == []
    assert "Add nothing by hand" in result["message"]
    assert "or add the items" not in result["message"]
    stored = swept["returns"].find_one({"return_id": ret["return_id"]})
    assert not stored["restock_in_progress"] and not stored["restock_applied"]
    assert len(list(tasks.find({"source_ref": f"return_restock_blocked:{ret['return_id']}"}))) == 1


def test_counter_stock_in_failure_raises_one_visible_recovery_task(swept, monkeypatch):
    oid = 60902
    _historical_order(swept, oid)
    _set(swept, oid, store_id="BV-GANGA-01")
    tasks = swept["db"]["tasks"]
    insert = _fail_only_stock_in(monkeypatch, tasks)

    out = _counter_return(swept, oid)
    ret = swept["returns"].find_one({"return_id": out["return_id"]})
    assert not out["restock_applied"] and _minted(swept) == []
    [blocked] = list(tasks.find({"source_ref": f"return_restock_blocked:{ret['return_id']}"}))
    assert blocked["status"] == "OPEN"
    assert "could not save" in blocked["description"]
    assert "Add nothing by hand before that stock-in task" in blocked["description"]
    assert not _retry(ret)["restock_applied"]
    assert len(list(tasks.find({"source_ref": blocked["source_ref"]}))) == 1

    monkeypatch.setattr(tasks, "insert_one", insert)
    assert _retry(ret)["restock_applied"] is True
    assert _minted(swept) == [] and len(_stock_in(swept, oid)) == 1
    assert tasks.find_one({"task_id": blocked["task_id"]})["status"] == "COMPLETED"


def test_live_counter_return_keeps_a_false_historical_flag(swept):
    oid = 60904
    _historical_order(swept, oid)
    _set(swept, oid, historical=False)
    out = _counter_return(swept, oid)
    ret = swept["returns"].find_one({"return_id": out["return_id"]})
    assert ret["historical"] is False


@pytest.mark.parametrize("historical", [False, True])
def test_shopify_credit_return_keeps_the_actual_historical_flag(swept, monkeypatch, historical):
    oid, rid = 60903, 70903
    row = _historical_refund(swept, monkeypatch, oid, rid, ims_product_id="IMS-P-1")
    _set(swept, oid, historical=historical)
    _fail_only_stock_in(monkeypatch, swept["db"]["tasks"])
    result = shopify_refund.post_from_review(swept["db"], row)
    ret = swept["returns"].find_one({"shopify_refund_id": str(rid)})
    assert ret["historical"] is historical
    if historical:
        assert not result["restock_applied"] and _minted(swept) == []


@pytest.mark.parametrize("sid", ["legacy-stock-row", ObjectId()])
@pytest.mark.parametrize("lost_reply", [False, True])
def test_legacy_id_only_unit_is_reactivated_by_its_exact_identity(sid, lost_reply):
    coll = mongomock.MongoClient().db.stock_units
    coll.insert_one({"_id": sid, "product_id": "P1", "store_id": "S1",
                     "order_id": "O1", "status": "SOLD"})

    class LostReply:
        def find(self, query):
            return coll.find(query)

        def find_one(self, query):
            return coll.find_one(query)

        def update_one(self, query, change):
            result = coll.update_one(query, change)
            if lost_reply:
                raise RuntimeError("reply lost after commit")
            return result

    repo = SimpleNamespace(collection=LostReply())
    used = set()
    assert returns_router._reactivate_original_unit(
        repo, "P1", "S1", "O1", used, exact_order_only=True,
    ) == str(sid)
    restored = coll.find_one({"_id": sid})
    assert restored["status"] == "AVAILABLE" and restored["order_id"] is None
    assert restored["returned_from_order_id"] == "O1" and coll.count_documents({}) == 1
    assert returns_router._reactivate_original_unit(
        repo, "P1", "S1", "O1", used, exact_order_only=True,
    ) is None
