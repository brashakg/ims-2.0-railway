"""
GST tax invoice numbers fit the 16-character limit (owner ruling 2026-10-18)
============================================================================
CGST Rule 46(b): an invoice number is at most 16 characters (letters, digits,
'-' or '/'), unique per financial year per GSTIN. The old mint printed
BV/BOK-01/26-27/0001 (20), BV/ONLINE-01/26-27/0001 (23). The ONE mint
(OrderRepository.next_invoice_number -- counter, online and admin-edit doors)
now prints SHOPCODE/FYFY/NNNNN, e.g. BOK01/2627/00001 = 16.

Revert-proof map (revert the named piece -> the named test goes red):
  * the shop-code rule (derive_invoice_code) -> test_prod_store_codes,
    test_every_prod_store_mints_16_chars
  * the clash check in the mint -> test_code_clash_is_refused,
    test_shops_sharing_a_gstin_never_share_a_number
  * the per-(code, FY) counter key -> test_serial_restarts_each_fy,
    test_old_format_counter_is_not_continued
  * the IST conversion -> test_fy_boundary_on_a_utc_instant
  * the rule check at the mint -> test_serial_100000_is_refused_not_truncated
  * no time-derived fallback -> test_db_less_mint_is_refused
  * the invoice door keeps a stored number -> test_door_keeps_an_old_number
  * GSTR-1 uses the mint's rule -> test_gstr1_flags_old_and_bad_numbers
  * the store validator -> test_store_invoice_code_*

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_gst_invoice_number_16.py -q
"""

from __future__ import annotations

import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strict_fakes import StrictDB  # noqa: E402
from api.utils.ist import IST  # noqa: E402
from database.repositories import order_repository as repo_module  # noqa: E402
from database.repositories.order_repository import (  # noqa: E402
    InvoiceNumberError,
    OrderRepository,
    derive_invoice_code,
    gst_invoice_number_problem,
    store_invoice_code,
)

# The six stores on prod (read-only census 2026-10-18; GSTINs replaced by
# labels -- what matters is WHICH stores share one). Pune's store_id is a UUID.
PROD_STORES = [
    {"store_id": "4dc49c44-08a1-46e1-85fb-8b7eca55f560", "store_code": "BV-PUN-01",
     "gstin": "MH-BVOPL", "invoice_prefix": "BV-PUN-01"},
    {"store_id": "BV-DHN-02", "store_code": "BV-DHN-02", "gstin": "JH-BVOPL",
     "invoice_prefix": "BV-HP"},
    {"store_id": "WIZ-DHN-01", "store_code": "WIZ-DHN-01", "gstin": "JH-BVOPL",
     "invoice_prefix": "WIZ-DHN"},
    {"store_id": "BV-BOK-02", "store_code": "BV-BOK-02", "gstin": "JH-MSBV",
     "invoice_prefix": "BV-BOK"},
    {"store_id": "BV-ONLINE-01", "store_code": "BV-ONLINE-01", "gstin": "MH-BVOPL"},
    {"store_id": "WO-ONLINE-01", "store_code": "WO-ONLINE-01", "gstin": None},
]
EXPECTED = {
    "BV-PUN-01": "PUN01",
    "BV-DHN-02": "DHN02",
    "WIZ-DHN-01": "WDHN1",
    "BV-BOK-02": "BOK02",
    "BV-ONLINE-01": "ONL01",
    "WO-ONLINE-01": "WONL1",
}
NUMBER_RE = re.compile(r"^[A-Z][A-Z0-9]{4}/\d{4}/\d{5}$")
MAY_2026 = datetime(2026, 5, 1, 11, 0, tzinfo=IST)


def _world(stores=PROD_STORES):
    db = StrictDB()
    db.seed("stores", [dict(s) for s in stores])
    orders = db.get_collection("orders")
    orders.database = db  # the repo reaches counters/stores through it
    return OrderRepository(orders), db


def _sid(code):
    return next(s["store_id"] for s in PROD_STORES if s["store_code"] == code)


# ---------------------------------------------------------------------------
# Shop codes: every store -> a 5-character code, unique, 16-char numbers
# ---------------------------------------------------------------------------


def test_prod_store_codes():
    got = {s["store_code"]: store_invoice_code(s) for s in PROD_STORES}
    assert got == EXPECTED
    assert len(set(got.values())) == len(got)  # unique across ALL stores


def test_every_prod_store_mints_16_chars():
    repo, _db = _world()
    for s in PROD_STORES:
        n = repo.next_invoice_number(s["store_id"], MAY_2026)
        assert n == f"{EXPECTED[s['store_code']]}/2627/00001"
        assert len(n) == 16 and NUMBER_RE.match(n)
        assert gst_invoice_number_problem(n) is None


def test_derivation_rule_edges():
    assert derive_invoice_code("BV-BOK-01") == "BOK01"
    assert derive_invoice_code("bv-gangadham-01") == "GAN01"
    assert derive_invoice_code("WO-PUN-01") == "WPUN1"
    # Never a squeezed guess: these need an invoice_code.
    for code in ("BV-HQ-01", "WIZ-DHN-10", "BV-BOK-100", "XX-BOK-01", "BOK01",
                 "4dc49c44-08a1-46e1-85fb-8b7eca55f560", "", None):
        assert derive_invoice_code(code) is None, code


def test_uuid_store_id_uses_the_store_code():
    # Pune's store_id is a UUID; the online door passes ONLY the id.
    repo, _db = _world()
    assert repo.next_invoice_number(_sid("BV-PUN-01"), MAY_2026) == "PUN01/2627/00001"
    # An order carrying the CODE instead still lands in Pune's one series.
    assert repo.next_invoice_number("BV-PUN-01", MAY_2026) == "PUN01/2627/00002"
    # A UUID with no store doc is refused -- never a code cut from the UUID.
    bare, _db2 = _world([])
    with pytest.raises(InvoiceNumberError, match="no valid invoice code"):
        bare.next_invoice_number(_sid("BV-PUN-01"), MAY_2026)


def test_invoice_code_overrides_the_derived_code():
    stores = [dict(s) for s in PROD_STORES]
    stores[3]["invoice_code"] = "bks02"  # BV-BOK-02, normalised to capitals
    repo, _db = _world(stores)
    assert repo.next_invoice_number("BV-BOK-02", MAY_2026) == "BKS02/2627/00001"


def test_shops_sharing_a_gstin_never_share_a_number():
    """BV-PUN-01 + BV-ONLINE-01 file under one Maharashtra GSTIN, BV-DHN-02 +
    WIZ-DHN-01 under one Jharkhand GSTIN: different shop codes, so their
    numbers can never collide, however many each issues."""
    repo, _db = _world()
    by_gstin = {}
    for s in PROD_STORES:
        nums = {repo.next_invoice_number(s["store_id"], MAY_2026) for _ in range(30)}
        assert len(nums) == 30
        by_gstin.setdefault(s["gstin"], []).append(nums)
    shared = [sets for g, sets in by_gstin.items() if g and len(sets) > 1]
    assert len(shared) == 2  # the two shared GSTINs above
    for a, b in shared:
        assert not a & b


def test_code_clash_is_refused():
    clash = [dict(s) for s in PROD_STORES] + [
        {"store_id": "BV-RAN-01", "store_code": "BV-RAN-01", "invoice_code": "DHN02"}
    ]
    repo, db = _world(clash)
    with pytest.raises(InvoiceNumberError, match="BV-RAN-01"):
        repo.next_invoice_number("BV-DHN-02", MAY_2026)
    assert db.get_collection("counters").docs == []  # nothing consumed


# ---------------------------------------------------------------------------
# Financial year (IST) and the serial
# ---------------------------------------------------------------------------


def test_fy_boundary_31_march_1_april_ist():
    repo, _db = _world()
    last = datetime(2026, 3, 31, 23, 59, 59, tzinfo=IST)
    first = datetime(2026, 4, 1, 0, 0, 0, tzinfo=IST)
    assert repo.next_invoice_number("BV-BOK-02", last) == "BOK02/2526/00001"
    assert repo.next_invoice_number("BV-BOK-02", first) == "BOK02/2627/00001"
    assert repo.next_invoice_number("BV-BOK-02", datetime(2027, 3, 31, 12, tzinfo=IST)) == (
        "BOK02/2627/00002"
    )


def test_fy_boundary_on_a_utc_instant():
    # 18:30 UTC on 31 March IS 00:00 IST on 1 April.
    repo, _db = _world()
    before = datetime(2026, 3, 31, 18, 29, 59, tzinfo=timezone.utc)
    after = before + timedelta(seconds=1)
    assert repo.next_invoice_number("BV-BOK-02", before).split("/")[1] == "2526"
    assert repo.next_invoice_number("BV-BOK-02", after).split("/")[1] == "2627"


def test_default_clock_is_ist(monkeypatch):
    repo, _db = _world()
    monkeypatch.setattr(
        repo_module, "now_ist", lambda: datetime(2026, 4, 1, 0, 5, tzinfo=IST)
    )
    assert repo.next_invoice_number("BV-BOK-02") == "BOK02/2627/00001"


def test_serial_restarts_each_fy():
    repo, _db = _world()
    fy25 = datetime(2026, 2, 1, tzinfo=IST)
    assert [repo.next_invoice_number("BV-BOK-02", fy25) for _ in range(3)][-1] == (
        "BOK02/2526/00003"
    )
    assert repo.next_invoice_number("BV-BOK-02", MAY_2026) == "BOK02/2627/00001"
    assert repo.next_invoice_number("BV-BOK-02", fy25) == "BOK02/2526/00004"


def test_old_format_counter_is_not_continued():
    """Counter-series switch: an old-format counter (invoice:{PREFIX}:{STORE}:
    {fy}) is left alone and the new series starts at 00001; a store that
    already minted new-format numbers continues its own series."""
    repo, db = _world()
    counters = db.get_collection("counters")
    counters.insert_one({"_id": "invoice:BV-BOK:BOK-02:2026", "seq": 41})
    counters.insert_one({"_id": "invoice:DHN02:2026", "seq": 7})
    assert repo.next_invoice_number("BV-BOK-02", MAY_2026) == "BOK02/2627/00001"
    assert repo.next_invoice_number("BV-DHN-02", MAY_2026) == "DHN02/2627/00008"
    assert counters.find_one({"_id": "invoice:BV-BOK:BOK-02:2026"})["seq"] == 41


def test_concurrent_mints_are_unique_and_consecutive():
    """16 threads x 400 mints against one counter: the mint's only source of
    the serial is the counter's atomic $inc (here a lock, as Mongo serialises
    one document), so every number is distinct and 1..400 has no gap."""
    repo, db = _world()
    counters = db.get_collection("counters")
    lock = threading.Lock()
    raw = counters.find_one_and_update

    def atomic(*a, **k):
        with lock:
            return raw(*a, **k)

    counters.find_one_and_update = atomic
    with ThreadPoolExecutor(max_workers=16) as pool:
        nums = list(pool.map(lambda _i: repo.next_invoice_number("BV-BOK-02", MAY_2026),
                             range(400)))
    assert len(set(nums)) == 400
    assert sorted(int(n[-5:]) for n in nums) == list(range(1, 401))


# ---------------------------------------------------------------------------
# Refusals: never truncate, never guess
# ---------------------------------------------------------------------------


def test_serial_100000_is_refused_not_truncated():
    repo, db = _world()
    db.get_collection("counters").insert_one({"_id": "invoice:BOK02:2026", "seq": 99998})
    assert repo.next_invoice_number("BV-BOK-02", MAY_2026) == "BOK02/2627/99999"
    with pytest.raises(InvoiceNumberError, match="17 characters"):
        repo.next_invoice_number("BV-BOK-02", MAY_2026)


def test_over_16_and_bad_characters_are_problems():
    assert gst_invoice_number_problem("BOK01/2627/00001") is None
    assert "20 characters" in gst_invoice_number_problem("BV/BOK-01/26-27/0001")
    for bad in ("bok01/2627/00001", "BOK01_2627_0001", "0OK01/2627/00001",
                "/BOK01/2627/001", "BOK 1/2627/0001", ""):
        assert gst_invoice_number_problem(bad), bad


def test_store_without_a_code_is_refused():
    repo, _db = _world(PROD_STORES + [{"store_id": "BV-HQ-01", "store_code": "BV-HQ-01"}])
    with pytest.raises(InvoiceNumberError, match="BV-HQ-01 has no valid invoice code"):
        repo.next_invoice_number("BV-HQ-01", MAY_2026)


def test_malformed_invoice_code_is_refused_not_derived():
    stores = [dict(s) for s in PROD_STORES]
    stores[3]["invoice_code"] = "BOK-2"
    repo, _db = _world(stores)
    with pytest.raises(InvoiceNumberError, match="no valid invoice code"):
        repo.next_invoice_number("BV-BOK-02", MAY_2026)


def test_db_less_mint_is_refused():
    class _NoDb:  # a mock collection with no .database (DB-less mode)
        pass

    with pytest.raises(InvoiceNumberError, match="counter unavailable"):
        OrderRepository(_NoDb()).next_invoice_number("BV-BOK-02", MAY_2026)


# ---------------------------------------------------------------------------
# The counter door: a stored (old) number is never rewritten; a new one mints
# ---------------------------------------------------------------------------


@pytest.fixture
def door(monkeypatch):
    from api.routers import orders as orders_module
    from api.routers.auth import get_current_user
    from api import dependencies as deps_module
    from api.services import print_identity

    repo, db = _world()

    class _StoreRepo:
        def find_by_id(self, sid):
            doc = db.get_collection("stores").find_one({"store_id": sid})
            return dict(doc, gstin="20AAAAA0000A1Z5", entity_id="e1") if doc else None

    class _CustomerRepo:
        def find_by_id(self, cid):
            return None

    monkeypatch.setattr(orders_module, "get_order_repository", lambda: repo)
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: _CustomerRepo())
    monkeypatch.setattr(orders_module, "validate_store_access", lambda sid, user: sid)
    monkeypatch.setattr(deps_module, "get_store_repository", lambda: _StoreRepo())
    monkeypatch.setattr(print_identity, "load_entity_for_store", lambda store: {
        "entity_id": "e1", "gstins": [{"gstin": "20AAAAA0000A1Z5"}],
    })
    app = FastAPI()
    app.include_router(orders_module.router, prefix="/orders")

    async def _user():
        return {"user_id": "u1", "roles": ["SUPERADMIN"], "active_store_id": "BV-BOK-02"}

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app), db


def _order(oid, **extra):
    return {"_id": oid, "order_id": oid, "store_id": "BV-BOK-02", "status": "CONFIRMED",
            "grand_total": 100.0, "items": [], **extra}


def test_door_keeps_an_old_number(door):
    client, db = door
    old = "BV/BOK-02/26-27/0001"
    db.get_collection("orders").insert_one(_order("O-OLD", invoice_number=old))
    r = client.get("/orders/O-OLD/invoice")
    assert r.status_code == 200, r.text
    assert r.json()["invoiceNumber"] == old
    assert db.get_collection("orders").find_one({"order_id": "O-OLD"})["invoice_number"] == old
    assert db.get_collection("counters").docs == []  # nothing minted


def test_door_mints_a_16_char_number_once(door):
    client, db = door
    db.get_collection("orders").insert_one(_order("O-NEW"))
    first = client.get("/orders/O-NEW/invoice").json()["invoiceNumber"]
    again = client.get("/orders/O-NEW/invoice").json()["invoiceNumber"]
    assert first == again and NUMBER_RE.match(first) and first.startswith("BOK02/")
    assert db.get_collection("orders").find_one({"order_id": "O-NEW"})["invoice_number"] == first


def test_door_refusal_is_a_400_naming_the_reason(door):
    client, db = door
    db.get_collection("stores").update_one(
        {"store_id": "BV-BOK-02"}, {"$set": {"invoice_code": "DHN02"}}
    )
    db.get_collection("orders").insert_one(_order("O-CLASH"))
    r = client.get("/orders/O-CLASH/invoice")
    assert r.status_code == 400
    assert "BV-DHN-02" in r.json()["detail"]
    assert "invoice_number" not in db.get_collection("orders").find_one({"order_id": "O-CLASH"})


# ---------------------------------------------------------------------------
# GSTR-1: the real number is filed verbatim; an illegal one is flagged
# ---------------------------------------------------------------------------


def test_gstr1_files_new_numbers_without_issues():
    from api.routers.reports import _gstr1_bill_number

    issues: list = []
    for code in EXPECTED.values():
        n = f"{code}/2627/00001"
        assert _gstr1_bill_number({"invoice_number": n}, issues) == n
    assert issues == []


def test_gstr1_flags_old_and_bad_numbers():
    from api.routers.reports import _gstr1_bill_number

    issues: list = []
    old = "BV/ONLINE-01/26-27/0001"
    assert _gstr1_bill_number({"invoice_number": old}, issues) == old  # verbatim
    assert _gstr1_bill_number({"invoice_number": "bok01/2627/00001"}, issues)
    assert len(issues) == 1 and issues[0]["count"] == 2
    assert issues[0]["issue_code"] == "INVOICE_SERIAL_OVER_16"
    assert "23 characters" in issues[0]["issue"]


# ---------------------------------------------------------------------------
# The store's invoice_code: validated (400), unique (409), "" clears
# ---------------------------------------------------------------------------


@pytest.fixture
def store_api(monkeypatch):
    from api.routers import stores
    from api.routers.auth import get_current_user

    db = StrictDB()
    db.seed("stores", [dict(s) for s in PROD_STORES])
    coll = db.get_collection("stores")

    class _Repo:
        def find_by_id(self, sid):
            return coll.find_one({"store_id": sid})

        def update(self, sid, data):
            coll.update_one({"store_id": sid}, {"$set": dict(data)})
            return True

    monkeypatch.setattr(stores, "get_store_repository", lambda: _Repo())
    monkeypatch.setattr(stores, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(stores.router, prefix="/stores")

    async def _u():
        return {"user_id": "su", "roles": ["SUPERADMIN"], "store_ids": []}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app), coll


def test_store_invoice_code_is_saved_in_capitals(store_api):
    client, coll = store_api
    r = client.put("/stores/BV-BOK-02", json={"invoice_code": " bks02 "})
    assert r.status_code == 200, r.text
    assert coll.find_one({"store_id": "BV-BOK-02"})["invoice_code"] == "BKS02"
    assert client.put("/stores/BV-BOK-02", json={"invoice_code": ""}).status_code == 200
    assert coll.find_one({"store_id": "BV-BOK-02"})["invoice_code"] is None


@pytest.mark.parametrize("bad", ["BOK2", "BOK002", "BOK-2", "0BK02", "B" + chr(0xD6) + "K02"])
def test_store_invoice_code_malformed_is_400(store_api, bad):
    client, coll = store_api
    r = client.put("/stores/BV-BOK-02", json={"invoice_code": bad})
    assert r.status_code == 400
    assert "invoice_code" not in coll.find_one({"store_id": "BV-BOK-02"})


def test_store_invoice_code_taken_is_409(store_api):
    client, coll = store_api
    # WDHN1 is WIZ-DHN-01's DERIVED code; PUN01 is Pune's (UUID store_id).
    for taken, holder in (("WDHN1", "WIZ-DHN-01"), ("PUN01", "BV-PUN-01")):
        r = client.put("/stores/BV-BOK-02", json={"invoice_code": taken})
        assert r.status_code == 409 and holder in r.json()["detail"]
    # Re-saving a store's own derived code is not a clash.
    assert client.put("/stores/BV-BOK-02", json={"invoice_code": "BOK02"}).status_code == 200
