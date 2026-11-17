"""PR #1423, decision D104 (closes F-1423-H): the Shopify order-history import
stores ONE order for one Shopify order without relying on a fail-soft index
or on a lenient read.

`ingest_shopify_order` took a failed dedupe read for "not booked yet"
(existing = None) and inserted the order under a random `_id`, trusting
uniq_shopify_order_id -- an index that is built fail-soft at startup. With
that index missing (and order_number_1, which also happens to refuse the
second insert, since the order number is ONL-<shopify id>), a failed read or
two import runs at once stored two orders: revenue and output GST twice.

  (1) a failed dedupe read is a failure for that order (reported), never
      "not found" -- on the live path too (the inbox retries it);
  (2) the import confirms uniq_shopify_order_id before it inserts an order
      (building it when it is missing and buildable) and refuses, saying
      why, when it cannot.

Real ingest code throughout. mongomock in these tests; native Mongo in
test_native_* (the CI mongo service) and in the local probe.
"""
import copy
import importlib.util
import os
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import mongomock
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test_x")
os.environ.setdefault("ENVIRONMENT", "test")

INDEX = "uniq_shopify_order_id"


def wire(monkeypatch):
    import api.dependencies as deps

    stores = SimpleNamespace(find_by_id=lambda _sid: {"gstin": "", "state_code": "20"},
                             find_active=lambda filter=None: [{"store_id": "BV-ONLINE-01", "state_code": "20"}])
    monkeypatch.setattr(deps, "get_product_repository", lambda: None)
    monkeypatch.setattr(deps, "get_store_repository", lambda: stores)
    monkeypatch.setenv("GST_PRICING_MODE", "inclusive")


def hist_order(order_id):
    """A paid, fulfilled Shopify order as the Admin REST API returns it."""
    return {
        "id": order_id, "name": f"#{order_id}", "created_at": "2023-05-10T09:30:00Z",
        "financial_status": "paid", "fulfillment_status": "fulfilled", "total_price": "999.00",
        "email": "buyer@example.invalid", "customer": {"id": 555, "first_name": "Synthetic"},
        "shipping_address": {"province": "20", "province_code": "20"},
        "line_items": [{"id": 9001, "product_id": 7001, "variant_id": 999001, "title": "Synthetic frame",
                        "product_type": "Frames", "sku": "SYN-1", "quantity": 1, "price": "999.00",
                        "total_discount": "0.00"}],
        "_ims_online_store_id": "BV-ONLINE-01",
    }


def ingest(db, order_id, historical=True):
    from api.services import shopify_ingest

    return shopify_ingest.ingest_shopify_order(
        db, copy.deepcopy(hist_order(order_id)), topic="orders/create", historical=historical)


def stored(db, order_id):
    return db.orders.count_documents({"shopify_order_id": str(order_id)})


def has_index(db):
    return INDEX in {i["name"] for i in db.orders.list_indexes()}


def drop_order_indexes(db):
    """Neither index that could refuse a second insert is there."""
    for name in (INDEX, "order_number_1"):
        if name in {i["name"] for i in db.orders.list_indexes()}:
            db.orders.drop_index(name)


def refuse_read_once(monkeypatch, db, order_id, skip=0):
    """The next dedupe read of this Shopify order fails (before it reads),
    after letting `skip` such reads through."""
    cls = type(db.orders)
    real, fired, seen = cls.find_one, [], []

    def find_one(self, flt=None, *a, **k):
        if (self.name == "orders" and isinstance(flt, dict)
                and flt.get("shopify_order_id") == str(order_id) and not fired):
            seen.append(1)
            if len(seen) > skip:
                fired.append(1)
                raise OSError("orders: read refused")
        return real(self, flt, *a, **k)

    monkeypatch.setattr(cls, "find_one", find_one)
    return fired


def index_not_buildable(monkeypatch, db):
    """uniq_shopify_order_id cannot be built or confirmed. Returns the undo."""
    cls = type(db.orders)
    real = cls.create_index

    def create_index(self, keys, *a, **k):
        if self.name == "orders" and k.get("name") == INDEX:
            raise OSError(f"{INDEX} cannot be built")
        return real(self, keys, *a, **k)

    monkeypatch.setattr(cls, "create_index", create_index)
    return lambda: monkeypatch.setattr(cls, "create_index", real)


@pytest.fixture
def db(monkeypatch):
    wire(monkeypatch)
    return mongomock.MongoClient()[f"oh_{uuid.uuid4().hex[:8]}"]


def failed_dedupe_read(db, monkeypatch, order_id=91001):
    """(1) The order is booked; the next run's dedupe read fails while no
    index could refuse a second insert. The run reports the order failed and
    stores nothing; the run after it finds the one order."""
    assert ingest(db, order_id)["status"] == "created"
    drop_order_indexes(db)
    fired = refuse_read_once(monkeypatch, db, order_id)
    again = ingest(db, order_id)
    assert fired and again["status"] == "error" and "dedupe lookup failed" in again["error"], again
    assert stored(db, order_id) == 1
    third = ingest(db, order_id)
    assert third["status"] == "duplicate" and stored(db, order_id) == 1


def index_missing_and_not_buildable(db, monkeypatch, order_id=91002):
    """(2) The index is missing and cannot be built: the import refuses the
    order, names the index, and inserts nothing. Buildable again: booked
    once, and the index is there."""
    drop_order_indexes(db)
    undo = index_not_buildable(monkeypatch, db)
    refused = ingest(db, order_id)
    assert (refused["status"], refused["reason"]) == ("error", "order_index_unconfirmed"), refused
    assert INDEX in refused["error"] and stored(db, order_id) == 0
    undo()
    assert ingest(db, order_id)["status"] == "created" and has_index(db)
    assert ingest(db, order_id)["status"] == "duplicate" and stored(db, order_id) == 1


def test_a_failed_dedupe_read_is_a_failure_never_not_found(db, monkeypatch):
    failed_dedupe_read(db, monkeypatch)


def test_the_import_refuses_while_the_order_index_cannot_be_confirmed(db, monkeypatch):
    index_missing_and_not_buildable(db, monkeypatch)


def test_a_second_run_inside_the_first_runs_window_stores_one_order(db, monkeypatch):
    """Both runs read "not booked" before either inserted (the second run
    runs whole inside the first one's insert). No index was there when they
    started: the import builds it before it inserts, so the first run's
    insert is refused and answers duplicate."""
    cls = type(db.orders)
    real, inner = cls.insert_one, []

    def insert_one(self, doc, *a, **k):
        if self.name == "orders" and not inner:
            inner.append(None)
            inner[0] = ingest(db, 91003)
        return real(self, doc, *a, **k)

    monkeypatch.setattr(cls, "insert_one", insert_one)
    assert not has_index(db)
    outer = ingest(db, 91003)
    assert (inner[0]["status"], outer["status"]) == ("created", "duplicate"), (inner, outer)
    assert outer["order_id"] == inner[0]["order_id"] and stored(db, 91003) == 1


def test_a_live_order_whose_dedupe_read_fails_is_an_error_for_the_inbox_to_retry(db, monkeypatch):
    """The same read on the live webhook path: an error (the inbox row fails
    and is retried), never a second build of an order that may exist."""
    refuse_read_once(monkeypatch, db, 91004)
    res = ingest(db, 91004, historical=False)
    assert res["status"] == "error" and "dedupe lookup failed" in res["error"]
    assert stored(db, 91004) == 0


def test_an_order_that_exists_but_cannot_be_read_back_is_not_answered_duplicate(db, monkeypatch):
    """The insert is refused as a duplicate and the winner's order cannot be
    read: reported failed. It used to answer 'duplicate' naming an order id
    that was never stored, and skipped the import's refund heal in silence."""
    assert ingest(db, 91005)["status"] == "created"
    cls = type(db.orders)
    real, calls = cls.find_one, []

    def find_one(self, flt=None, *a, **k):
        if self.name == "orders" and isinstance(flt, dict) and flt.get("shopify_order_id") == "91005":
            calls.append(1)
            if len(calls) == 1:
                return None  # a stale "not booked" read: the run goes on to insert
            raise OSError("orders: read refused")
        return real(self, flt, *a, **k)

    monkeypatch.setattr(cls, "find_one", find_one)
    res = ingest(db, 91005)
    assert res["status"] == "error" and "could not be read back" in res["error"], res
    assert "order_id" not in res
    monkeypatch.setattr(cls, "find_one", real)
    assert stored(db, 91005) == 1


def _script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "import_shopify_order_history.py"
    spec = importlib.util.spec_from_file_location("import_shopify_order_history", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_import_script_refuses_to_apply_and_says_why(db, monkeypatch, capsys):
    """scripts/import_shopify_order_history.py --apply: with the index not
    confirmable the run stops before it books anything and prints the
    reason; a dry run is unaffected."""
    script = _script()
    orders = [hist_order(91006), hist_order(91007)]
    monkeypatch.setattr(script, "connect_db", lambda uri, name: db)
    monkeypatch.setattr(script, "fetch_all_orders", lambda *a, **k: copy.deepcopy(orders))
    monkeypatch.setattr("api.services.shopify_auth.resolve_shopify_credentials",
                        lambda dbh: {"shop_url": "synthetic.invalid", "access_token": "x", "source": "test"})
    args = dict(mongo_uri="mongodb://synthetic.invalid", db_name="x", store_id="BV-ONLINE-01",
                page_size=50, sleep_s=0.0, cap=None)
    undo = index_not_buildable(monkeypatch, db)
    assert INDEX in script.apply_refusal(db)
    summary = script.run(apply=True, **args)
    assert summary["applied"] is False and INDEX in summary["refused"]
    assert "REFUSED" in capsys.readouterr().out and db.orders.count_documents({}) == 0
    undo()
    assert script.apply_refusal(db) is None
    # every run, a dry one too, shows the refunds an earlier run left unfinished
    db.returns.insert_one({"return_id": "RET-HIST-X", "shopify_refund_id": "X", "status": "PENDING",
                           "source": "shopify_order_history", "credit_note_issued": False})
    dry = script.run(apply=False, **args)
    assert dry["unfinished_import_refunds"] == 1 and "left unfinished: 1" in capsys.readouterr().out
    assert db.orders.count_documents({}) == 0
    db.returns.delete_many({})
    summary = script.run(apply=True, **args)
    assert (summary["created"], summary["errors"], summary.get("refused")) == (2, 0, None)
    assert db.orders.count_documents({}) == 2
    # A dedupe read that fails mid-run -- the apply loop's own (the 2nd read
    # of the order; the 1st is the dry-run tally's) or the ingest's (the 3rd):
    # that order is counted and named, never folded into "skipped".
    for skip in (1, 2):
        fired = refuse_read_once(monkeypatch, db, 91006, skip=skip)
        summary = script.run(apply=True, **args)
        assert fired and (summary["errors"], summary["error_order_ids"]) == (1, ["91006"]), (skip, summary)
        assert (summary["duplicate"], summary["skipped_apply"]) == (1, 0)
        assert "91006 NOT BOOKED" in capsys.readouterr().out and db.orders.count_documents({}) == 2


# --- native Mongo ----------------------------------------------------------------


def two_runs_at_once(db, order_id, timeout=20):
    """Two import runs of one Shopify order, held at their insert until both
    have done their dedupe read."""
    cls = type(db.orders)
    real, gate, out = cls.insert_one, threading.Barrier(2, timeout=5), {}

    def insert_one(self, doc, *a, **k):
        if self.name == "orders" and doc.get("shopify_order_id") == str(order_id):
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                pass  # the other run never reached its insert (refused, or a duplicate read)
        return real(self, doc, *a, **k)

    def go(name):
        try:
            out[name] = ingest(db, order_id)
        except Exception as exc:  # noqa: BLE001
            out[name] = {"status": "EXCEPTION", "error": repr(exc)}

    cls.insert_one = insert_one
    try:
        threads = [threading.Thread(target=go, args=(n,)) for n in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout)
    finally:
        cls.insert_one = real
    return out


def native_two_runs(db, monkeypatch, base=92000):
    """No index that could refuse a second insert. Not buildable: both runs
    refuse, nothing is stored. Buildable: the import builds it, and two runs
    at once store ONE order."""
    drop_order_indexes(db)
    undo = index_not_buildable(monkeypatch, db)
    out = two_runs_at_once(db, base + 1)
    assert sorted(v["status"] for v in out.values()) == ["error", "error"], out
    assert all(v["reason"] == "order_index_unconfirmed" for v in out.values()) and stored(db, base + 1) == 0
    undo()
    for n in range(2, 6):
        drop_order_indexes(db)
        out = two_runs_at_once(db, base + n)
        assert sorted(v["status"] for v in out.values()) == ["created", "duplicate"], out
        assert stored(db, base + n) == 1 and has_index(db)


@pytest.fixture
def native_db(monkeypatch):
    from pymongo import MongoClient

    url = os.getenv("MONGODB_URL") or os.getenv("MONGODB_URI") or "mongodb://localhost:27017"
    client = MongoClient(url, serverSelectionTimeoutMS=2000)
    client.admin.command("ping")
    d = client[f"ims_test_oneorder_{uuid.uuid4().hex[:8]}"]
    wire(monkeypatch)
    yield d
    client.drop_database(d.name)


def test_native_two_import_runs_at_once_store_one_order(native_db, monkeypatch):
    native_two_runs(native_db, monkeypatch)


def test_native_a_failed_read_and_a_missing_index_never_double_an_order(native_db, monkeypatch):
    failed_dedupe_read(native_db, monkeypatch, order_id=92101)
    index_missing_and_not_buildable(native_db, monkeypatch, order_id=92102)
