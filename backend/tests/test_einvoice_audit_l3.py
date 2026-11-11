"""Codex whole-app audit, lane 3: the three e-invoice enablement blockers.

  L3-19  the IRP payload is built from the order's CANONICAL tax fields (the
         till's stamped per-line taxable_value / tax_amount / gst_rate and the
         order's tax_amount / grand_total / round_off -- the figures the tax
         invoice prints and GSTR-1 files), never from field names no order
         writer stamps.
  L3-20  the credential loader fails closed: only an ENABLED config for the
         REQUESTED GSTIN is ever returned -- never another legal entity's.
  L3-21  a successful provider response is persisted through a database
         handle that refuses truth-testing, exactly as the real PyMongo
         Database the finance router passes does.

Owner ruling 2026-11-11: e-invoicing stays switched OFF. Nothing here calls
the IRP or any provider -- the one network boundary (``_call_irp`` /
``httpx.AsyncClient``) is always stubbed, and the dark gate is asserted.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_einvoice_audit_l3.py -q
"""

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.pop("IMS_EINVOICE_ENABLED", None)  # dark unless a test opts in

from api.services import einvoice  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

JH_GSTIN = "20AAAAA1234A1ZX"  # the order's own shop (Jharkhand)
MH_GSTIN = "27BBBBB5678B1ZY"  # ANOTHER legal entity (Maharashtra)
STORE = {"store_id": "BV-BOK-01", "store_name": "Bokaro", "state_code": "20", "gstin": JH_GSTIN}

_PYMONGO_BOOL = (
    "Database objects do not implement truth value testing or bool(). "
    "Please compare with None instead: database is not None"
)


class PyMongoLikeDB(StrictDB):
    """The strict fake, refusing truth-testing as pymongo's Database does
    (pinned against the real class in test_the_stand_in_is_faithful...)."""

    def __bool__(self):
        raise NotImplementedError(_PYMONGO_BOOL)


def _creds(gstin, enabled=True):
    return {
        "type": "einvoice",
        "enabled": enabled,
        "config": {"gstin": gstin, "gsp_url": "http://127.0.0.1:9", "username": "synthetic",
                   "password": "synthetic-only"},
    }


def _db(order=None, creds=()):
    db = PyMongoLikeDB()
    db.seed("stores", [dict(STORE)])
    db.seed("integrations", [dict(c) for c in creds])
    if order:
        db.seed("orders", [dict(order)])
    return db


def _till_order(lines, cart_discount_pct=0.0, **extra):
    """An order priced by THE till rule (orders._compute_per_category_gst), so
    the lines carry exactly the tax fields a real POS bill stores."""
    from api.routers.orders._shared import _compute_per_category_gst

    items = [dict(ln) for ln in lines]
    priced = _compute_per_category_gst(items, cart_discount_pct)
    return {
        "order_id": "ORD-L3", "order_number": "BV-ORD-L3", "invoice_number": "BOK01/2627/00001",
        "invoice_date": "2026-11-04", "store_id": STORE["store_id"], "customer_name": "Audit Buyer",
        "items": items, "subtotal": priced["subtotal"], "tax_amount": priced["tax"],
        "tax_rate": priced["dominant_rate"], "grand_total": priced["grand_total"],
        "round_off": priced["round_off"], "discount_total": priced["total_discount"], **extra,
    }


FRAME = {"product_name": "Frame", "item_type": "FRAME", "category": "FRAME", "hsn_code": "9003",
         "quantity": 1, "unit_price": 1000.0, "item_total": 1000.0}
SUNGLASS = {"product_name": "Sunglass", "item_type": "SUNGLASS", "category": "SUNGLASS",
            "hsn_code": "900410", "quantity": 2, "unit_price": 1299.5, "item_total": 2599.0}


def _heads(row, keys=("CgstAmt", "SgstAmt", "IgstAmt")):
    return round(sum(row[k] for k in keys), 2)


def _assert_irp_arithmetic(payload):
    """The IRP's own cross-checks: the header is the sum of its lines on every
    head, each line's value is its assessable + tax, and the invoice value is
    assessable + tax + round off (no discount is taken a second time)."""
    val, lines = payload["ValDtls"], payload["ItemList"]
    for head, line_key in (("AssVal", "AssAmt"), ("CgstVal", "CgstAmt"), ("SgstVal", "SgstAmt"),
                           ("IgstVal", "IgstAmt")):
        assert val[head] == round(sum(ln[line_key] for ln in lines), 2), head
    for ln in lines:
        assert ln["TotItemVal"] == round(ln["AssAmt"] + _heads(ln), 2)
        assert ln["AssAmt"] == round(ln["TotAmt"] - ln["Discount"], 2)
    assert val["TotInvVal"] == round(
        val["AssVal"] + val["CgstVal"] + val["SgstVal"] + val["IgstVal"]
        + val["RndOffAmt"] - val["Discount"], 2
    )


# ===========================================================================
# L3-19 -- the payload is the order's canonical tax representation
# ===========================================================================


def test_l3_19_the_audit_fixture_line_and_header_carry_the_stored_tax():
    """The audit's own figures: a line stamped taxable_value 952.38 +
    tax_amount 47.62 on a Rs 1,000 bill went out as assessable 0 / tax 0 on
    the line and assessable 1,000 / tax 0 on the header."""
    order = {
        "order_id": "AUDIT-INTEGRATION-ORDER", "invoice_number": "AUD/2627/00001",
        "invoice_date": "2026-11-04", "store_id": STORE["store_id"],
        "subtotal": 1000.0, "tax_amount": 47.62, "tax_rate": 5, "grand_total": 1000.0,
        "items": [{"product_name": "Frame", "hsn_code": "9003", "quantity": 1, "unit_price": 1000.0,
                   "item_total": 1000.0, "gst_rate": 5, "taxable_value": 952.38, "tax_amount": 47.62}],
    }
    payload = einvoice._build_einvoice_json(order, STORE)
    line, val = payload["ItemList"][0], payload["ValDtls"]

    assert (line["AssAmt"], _heads(line), line["GstRt"]) == (952.38, 47.62, 5)
    assert (val["AssVal"], _heads(val, ("CgstVal", "SgstVal", "IgstVal"))) == (952.38, 47.62)
    assert (line["CgstAmt"], line["SgstAmt"], line["IgstAmt"]) == (23.81, 23.81, 0.0)
    assert val["TotInvVal"] == 1000.0 and line["TotItemVal"] == 1000.0
    _assert_irp_arithmetic(payload)


def test_l3_19_a_till_priced_mixed_rate_bill_matches_the_tax_invoice_and_gstr1():
    """A 5% frame + an 18% sunglass under a cart discount, priced by the till
    rule: every IRP line is its own stamped taxable / tax at its own rate, and
    the header equals what the tax invoice prints and what GSTR-1 files."""
    from api.routers.orders.invoices import _build_invoice_gst_split
    from api.routers.reports.gst_base import _order_taxable_and_tax

    order = _till_order([FRAME, SUNGLASS], cart_discount_pct=10.0)
    payload = einvoice._build_einvoice_json(order, STORE)
    val, lines = payload["ValDtls"], payload["ItemList"]

    assert [ln["PrdDesc"] for ln in lines] == ["Frame", "Sunglass"]  # the bill's own order
    for ln, item in zip(lines, order["items"]):
        assert ln["AssAmt"] == item["taxable_value"] and ln["GstRt"] == item["gst_rate"]
        assert _heads(ln) == item["tax_amount"] and ln["IgstAmt"] == 0.0
        assert ln["Qty"] == item["quantity"] and ln["HsnCd"] == item["hsn_code"]
    assert len({ln["GstRt"] for ln in lines}) == 2  # two rates really are in play

    printed = _build_invoice_gst_split(order["items"], STORE, None)["totals"]
    assert (val["AssVal"], val["CgstVal"], val["SgstVal"], val["IgstVal"]) == (
        printed["taxable"], printed["cgst"], printed["sgst"], printed["igst"])
    assert (val["AssVal"], round(val["CgstVal"] + val["SgstVal"], 2)) == _order_taxable_and_tax(order)
    assert val["TotInvVal"] == order["grand_total"] and val["RndOffAmt"] == (order["round_off"] or 0.0)
    _assert_irp_arithmetic(payload)


def test_l3_19_an_interstate_order_goes_out_as_igst_to_its_own_place_of_supply():
    """The head the order was booked with (its own interstate / place-of-supply
    stamp) is the head on the IRP -- the whole tax as IGST, no CGST/SGST."""
    order = _till_order([FRAME], interstate=True, place_of_supply="27",
                        customer_gstin=MH_GSTIN)
    payload = einvoice._build_einvoice_json(order, STORE)
    val, line = payload["ValDtls"], payload["ItemList"][0]

    assert (val["IgstVal"], val["CgstVal"], val["SgstVal"]) == (order["tax_amount"], 0.0, 0.0)
    assert (line["IgstAmt"], line["CgstAmt"], line["SgstAmt"]) == (order["tax_amount"], 0.0, 0.0)
    assert payload["BuyerDtls"]["Pos"] == "27" and payload["BuyerDtls"]["Gstin"] == MH_GSTIN
    assert payload["TranDtls"]["SupTyp"] == "B2B" and payload["SellerDtls"]["Stcd"] == "20"
    _assert_irp_arithmetic(payload)


def test_l3_19_the_buyer_gstin_is_the_one_the_invoice_was_minted_with():
    """An invoice minted as a consumer bill (blank stamp) stays B2C even if the
    customer record gained a GSTIN later; one minted before the stamp existed
    reads the customer record -- the GST returns' own rule."""
    live = {"customer_id": "C1", "gstin": MH_GSTIN}
    minted_consumer = _till_order([FRAME], customer_id="C1", customer_gstin="")
    legacy = _till_order([FRAME], customer_id="C1")

    assert einvoice._build_einvoice_json(minted_consumer, STORE, live)["BuyerDtls"]["Gstin"] == "URP"
    assert einvoice._build_einvoice_json(legacy, STORE, live)["BuyerDtls"]["Gstin"] == MH_GSTIN


@pytest.mark.asyncio
@pytest.mark.parametrize("damage, why", [
    ({"grand_total": "not money"}, "unreadable"),
    ({"grand_total": 0.0, "tax_amount": 0.0, "items": []}, "no taxable value"),
])
async def test_l3_19_an_order_the_returns_cannot_file_is_never_sent(monkeypatch, damage, why):
    """An unreadable amount or a Rs 0 bill is refused BEFORE the provider call
    (FAILED, a reason, nothing stored) -- never sent as zeros."""
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    order = {**_till_order([FRAME]), **damage}
    db = _db(order, [_creds(JH_GSTIN)])

    async def _never(cfg, payload):
        raise AssertionError("the provider must not be called")

    monkeypatch.setattr(einvoice, "_call_irp", _never)
    out = await einvoice.generate_irn(db, order)

    assert out["status"] == einvoice.STATUS_FAILED and out["irn"] is None
    assert why in out["reason"].lower()
    assert "irn" not in db.get_collection("orders").find_one({"order_id": "ORD-L3"})


# ===========================================================================
# L3-20 -- credentials fail closed
# ===========================================================================


@pytest.mark.parametrize("stored, asked", [
    ([_creds(MH_GSTIN, enabled=False)], JH_GSTIN),  # the audit case
    ([_creds(MH_GSTIN)], JH_GSTIN),                 # another entity, enabled
    ([_creds(JH_GSTIN, enabled=False)], JH_GSTIN),  # its own, switched off
    ([_creds(JH_GSTIN)], ""),                       # no GSTIN asked -> nothing
    ([_creds(JH_GSTIN)], None),
])
def test_l3_20_no_config_is_returned_for_another_gstin_or_a_disabled_one(stored, asked):
    assert einvoice._load_creds(_db(creds=stored), asked) == {}


def test_l3_20_the_enabled_config_of_the_requested_gstin_is_the_one_returned():
    db = _db(creds=[_creds(MH_GSTIN), _creds(JH_GSTIN, enabled=False), _creds(JH_GSTIN)])
    cfg = einvoice._load_creds(db, JH_GSTIN)
    assert cfg["gstin"] == JH_GSTIN and cfg["username"] == "synthetic"


@pytest.mark.asyncio
async def test_l3_20_with_the_gate_lifted_another_entitys_config_never_reaches_the_provider(monkeypatch):
    """The audit's second half: with the env gate lifted and only ANOTHER
    GSTIN's config stored, the provider stub received that config against
    this shop's seller GSTIN. Now: SIMULATED, no call, nothing stored."""
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    order = _till_order([FRAME])
    db = _db(order, [_creds(MH_GSTIN, enabled=False), _creds(MH_GSTIN)])
    calls = []

    async def _spy(cfg, payload):
        calls.append((cfg.get("gstin"), payload["SellerDtls"]["Gstin"]))
        return {"Irn": "SYNTHETIC-IRN-NOT-VALID"}

    monkeypatch.setattr(einvoice, "_call_irp", _spy)
    out = await einvoice.generate_irn(db, order)

    assert calls == [] and out["status"] == einvoice.STATUS_SIMULATED and out["irn"] is None
    assert JH_GSTIN in out["reason"]
    assert "irn" not in db.get_collection("orders").find_one({"order_id": "ORD-L3"})


class _NoNetwork:
    """httpx.AsyncClient stand-in that fails the test if anything dials out."""

    def __init__(self, *a, **k):
        raise AssertionError("no provider call is allowed")


@pytest.mark.asyncio
async def test_l3_20_a_cancel_never_uses_another_entitys_config(monkeypatch):
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    monkeypatch.setattr(einvoice.httpx, "AsyncClient", _NoNetwork)
    order = {**_till_order([FRAME]), "irn": "SYNTHETIC-IRN-NOT-VALID"}
    # this shop has no config; a shop-less order asks with a blank GSTIN
    for doc in (order, {**order, "store_id": "NO-SUCH-SHOP"}):
        out = await einvoice.cancel_irn(_db(doc, [_creds(MH_GSTIN)]), doc)
        assert out["status"] == einvoice.STATUS_SIMULATED


# ===========================================================================
# L3-21 -- the IRN is persisted through the real database wrapper
# ===========================================================================

_IRP_OK = {"Irn": "SYNTHETIC-IRN-NOT-VALID", "AckNo": "SYNTHETIC-ACK", "AckDt": "2026-11-04",
           "SignedQRCode": "SYNTHETIC-QR-NOT-VALID"}


def test_the_stand_in_is_faithful_to_the_real_pymongo_database():
    """PyMongoLikeDB refuses truth-testing with the SAME exception the real
    pymongo Database raises (a lazy client: nothing is dialled)."""
    from pymongo import MongoClient

    client = MongoClient("mongodb://127.0.0.1:9", connect=False)
    with pytest.raises(NotImplementedError) as real_exc:
        bool(client["ims_never_opened"])
    client.close()
    with pytest.raises(NotImplementedError) as fake_exc:
        bool(PyMongoLikeDB())
    assert str(fake_exc.value) == str(real_exc.value)


async def _generate_with_stubbed_provider(monkeypatch, db, order):
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    calls = []

    async def _ok(cfg, payload):
        calls.append({"config_gstin": cfg.get("gstin"), "seller_gstin": payload["SellerDtls"]["Gstin"]})
        return dict(_IRP_OK)

    monkeypatch.setattr(einvoice, "_call_irp", _ok)
    return await einvoice.generate_irn(db, order), calls


def _assert_irn_saved(out, calls, stored):
    assert calls == [{"config_gstin": JH_GSTIN, "seller_gstin": JH_GSTIN}]
    assert out["status"] == einvoice.STATUS_GENERATED and out["persisted"] is True
    assert out["irn"] == "SYNTHETIC-IRN-NOT-VALID" and out["reason"] is None
    assert (stored["irn"], stored["ack_no"], stored["ack_date"], stored["einvoice_signed_qr"],
            stored["einvoice_status"]) == ("SYNTHETIC-IRN-NOT-VALID", "SYNTHETIC-ACK", "2026-11-04",
                                           "SYNTHETIC-QR-NOT-VALID", "GENERATED")
    assert stored["einvoice_generated_at"] is not None


@pytest.mark.asyncio
async def test_l3_21_a_successful_response_is_saved_on_the_order(monkeypatch):
    """The audit case: provider success, then `not db` raised
    NotImplementedError out of generate_irn and no IRN was saved."""
    order = _till_order([FRAME])
    db = _db(order, [_creds(JH_GSTIN)])
    out, calls = await _generate_with_stubbed_provider(monkeypatch, db, order)
    _assert_irn_saved(out, calls, db.get_collection("orders").find_one({"order_id": "ORD-L3"}))


@pytest.mark.asyncio
async def test_l3_21_an_irn_that_could_not_be_saved_says_so(monkeypatch):
    """The IRP issued the IRN but the order row is gone: the result still
    carries the IRN, flagged as NOT saved -- never a silent 'all good'."""
    order = _till_order([FRAME])
    db = _db(None, [_creds(JH_GSTIN)])  # no order row to write to
    out, _ = await _generate_with_stubbed_provider(monkeypatch, db, order)
    assert out["status"] == einvoice.STATUS_GENERATED and out["irn"] == "SYNTHETIC-IRN-NOT-VALID"
    assert out["persisted"] is False and "not saved" in out["reason"].lower()


class _CancelOK:
    """httpx.AsyncClient stand-in: the provider accepts the cancel. No network."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        class _Resp:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {"Status": "1"}

        return _Resp()


@pytest.mark.asyncio
async def test_l3_21_an_accepted_cancel_is_saved_on_the_order(monkeypatch):
    """The same truth-test sat on the cancel path: the provider cancelled the
    IRN, the write never ran, and the order still read GENERATED."""
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    monkeypatch.setattr(einvoice.httpx, "AsyncClient", _CancelOK)
    order = {**_till_order([FRAME]), "irn": "SYNTHETIC-IRN-NOT-VALID", "einvoice_status": "GENERATED"}
    db = _db(order, [_creds(JH_GSTIN)])

    out = await einvoice.cancel_irn(db, order, 3, "synthetic")

    assert out == {"status": "CANCELLED", "reason": None}
    stored = db.get_collection("orders").find_one({"order_id": "ORD-L3"})
    assert stored["einvoice_status"] == "CANCELLED" and stored["einvoice_cancelled_at"] is not None


# --- the same proof on a REAL MongoDB (CI's mongo:7.0; skipped on a PC without one)

MONGO_HOST = os.getenv("MONGO_HOST", "127.0.0.1")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))


def _mongo_db():
    """A throwaway database on the local mongo, or skip when none answers."""
    from pymongo import MongoClient

    try:
        client = MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=1500)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no local mongo at {MONGO_HOST}:{MONGO_PORT} ({exc})")
    name = f"ims_test_einvoice_{uuid.uuid4().hex[:8]}"
    return client, client[name], name


@pytest.fixture
def real_db():
    client, db, name = _mongo_db()
    assert name.startswith("ims_test_einvoice_") and not db.list_collection_names()
    try:
        yield db
    finally:
        client.drop_database(name)
        client.close()


@pytest.mark.asyncio
async def test_l3_21_real_pymongo_database_saves_the_irn(monkeypatch, real_db):
    """End to end on the real wrapper: a real pymongo Database (the object
    finance/_shared._get_db hands the service), real writes, provider stubbed."""
    order = _till_order([FRAME])
    real_db.stores.insert_one(dict(STORE))
    real_db.orders.insert_one(dict(order))
    # only ANOTHER entity's config first: dark, nothing written (L3-20)
    real_db.integrations.insert_one(_creds(MH_GSTIN, enabled=False))
    out, calls = await _generate_with_stubbed_provider(monkeypatch, real_db, order)
    assert calls == [] and out["status"] == einvoice.STATUS_SIMULATED
    assert "irn" not in real_db.orders.find_one({"order_id": "ORD-L3"})

    real_db.integrations.insert_one(_creds(JH_GSTIN))
    out, calls = await _generate_with_stubbed_provider(monkeypatch, real_db, order)
    _assert_irn_saved(out, calls, real_db.orders.find_one({"order_id": "ORD-L3"}))
    assert out["einvoice_json"]["ValDtls"]["TotInvVal"] == order["grand_total"]


# ===========================================================================
# Still dark
# ===========================================================================


@pytest.mark.asyncio
async def test_e_invoicing_stays_dark_without_the_env_gate(monkeypatch):
    """Owner ruling 2026-11-11: switched OFF. With valid creds stored but the
    env gate unset: SIMULATED, no provider call, no write."""
    monkeypatch.delenv("IMS_EINVOICE_ENABLED", raising=False)
    monkeypatch.setattr(einvoice.httpx, "AsyncClient", _NoNetwork)
    order = _till_order([FRAME])
    db = _db(order, [_creds(JH_GSTIN)])

    out = await einvoice.generate_irn(db, order)
    cancel = await einvoice.cancel_irn(db, {**order, "irn": "SYNTHETIC-IRN-NOT-VALID"})

    assert out["status"] == einvoice.STATUS_SIMULATED and out["irn"] is None
    assert cancel["status"] == einvoice.STATUS_SIMULATED
    assert einvoice.einvoice_enabled(db, JH_GSTIN) is False
    assert "irn" not in db.get_collection("orders").find_one({"order_id": "ORD-L3"})
