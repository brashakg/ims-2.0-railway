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
    """The IRP's own checks: the header is the sum of its lines on every head;
    on each line TotAmt = Qty x UnitPrice, AssAmt = TotAmt - Discount and the
    line value is its assessable + tax; the invoice value is assessable + tax
    - header Discount + round off. Decision D2 (2026-11-11): a line shows its
    NET unit price with Discount 0, so these hold for every bill."""
    val, lines = payload["ValDtls"], payload["ItemList"]
    for head, line_key in (("AssVal", "AssAmt"), ("CgstVal", "CgstAmt"), ("SgstVal", "SgstAmt"),
                           ("IgstVal", "IgstAmt")):
        assert val[head] == round(sum(ln[line_key] for ln in lines), 2), head
    for ln in lines:
        assert abs(ln["Qty"] * ln["UnitPrice"] - ln["TotAmt"]) < 0.01, ln
        assert ln["AssAmt"] == round(ln["TotAmt"] - ln["Discount"], 2), ln
        assert ln["TotItemVal"] == round(ln["AssAmt"] + _heads(ln), 2)
    assert val["TotInvVal"] == round(
        val["AssVal"] + val["CgstVal"] + val["SgstVal"] + val["IgstVal"]
        - val["Discount"] + val["RndOffAmt"], 2
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


# -- Decision D2 (Claude, under the owner's delegation, 2026-11-11) ----------
# Q1-A: a line shows its NET unit price (after every discount, GST taken out)
# with Discount 0. Q2-A: the buyer GSTIN is GSTR-1's own rule.

# Three pieces at an MRP of Rs 1,000 with Rs 301 off the line: a discounted,
# GST-inclusive line whose stored unit price is NOT its taxable value.
DISCOUNTED = {"product_name": "Frame", "item_type": "FRAME", "category": "FRAME", "hsn_code": "9003",
              "quantity": 3, "unit_price": 1000.0, "discount_amount": 301.0, "discount": 301.0,
              "item_total": 2699.0}


def test_l3_19_each_line_shows_the_net_unit_price_and_no_discount():
    """The stored MRP and stored discount went out beside the correct taxable
    value, so the IRP's own TotAmt = Qty x UnitPrice and AssAmt = TotAmt -
    Discount could not hold for a discounted or GST-inclusive line. Each line
    now shows the bill's net price, and no discount is shown twice."""
    order = _till_order([DISCOUNTED, SUNGLASS], cart_discount_pct=10.0)
    assert order["discount_total"] > 301.0  # a line discount AND a cart discount are in play
    payload = einvoice._build_einvoice_json(order, STORE)
    val, lines = payload["ValDtls"], payload["ItemList"]

    for ln, item in zip(lines, order["items"]):
        assert (ln["TotAmt"], ln["Discount"], ln["AssAmt"]) == (
            item["taxable_value"], 0.0, item["taxable_value"])
        assert ln["UnitPrice"] == round(item["taxable_value"] / item["quantity"], 3)
        assert ln["UnitPrice"] < item["unit_price"]  # net of discount and of GST
    # The discount is already inside every line: a header discount would take it twice.
    assert val["Discount"] == 0.0
    assert val["TotInvVal"] == order["grand_total"]  # still the bill
    _assert_irp_arithmetic(payload)


OLD_BUYER = {"customer_id": "CUST-B2B", "name": "Old Buyer Pvt Ltd", "gstin": MH_GSTIN,
             "state_code": "20"}
OTHER_GSTIN = "20CCCCC9999C1Z7"


@pytest.mark.parametrize("stamp, record, expected", [
    # an invoice older than the stamp: the customer record, as GSTR-1 reads it
    ({}, OLD_BUYER, MH_GSTIN),
    # minted B2C (a blank stamp): a GSTIN added to the record later changes nothing
    ({"customer_gstin": ""}, OLD_BUYER, ""),
    # minted B2B: the frozen GSTIN, never the record as edited since
    ({"customer_gstin": OTHER_GSTIN}, OLD_BUYER, OTHER_GSTIN),
    # no stamp and no record (a walk-in): unregistered
    ({}, None, ""),
    ({}, {"customer_id": "CUST-B2C", "name": "Consumer"}, ""),
])
def test_l3_19_the_buyer_gstin_is_the_one_gstr1_files(stamp, record, expected):
    """One rule (gst_base._invoice_gstin) names the buyer for the IRN and for
    GSTR-1, so the two can never disagree on B2B vs B2C."""
    from api.routers.reports.gst_base import _invoice_gstin

    order = _till_order([FRAME], customer_id="CUST-B2B", **stamp)
    payload = einvoice._build_einvoice_json(order, STORE, record)

    assert _invoice_gstin(order, (record or {}).get("gstin", "")) == expected  # GSTR-1's answer
    assert payload["BuyerDtls"]["Gstin"] == (expected or "URP")
    assert payload["TranDtls"]["SupTyp"] == ("B2B" if expected else "B2C")
    _assert_irp_arithmetic(payload)


@pytest.mark.asyncio
async def test_l3_19_an_old_b2b_bill_reaches_the_provider_with_the_customers_gstin(monkeypatch):
    """Through the real door (generate_irn reads the customer record itself):
    an invoice with no GSTIN stamp goes out B2B to its customer's GSTIN. The
    provider is a stub; nothing leaves this process."""
    order = _till_order([FRAME], customer_id="CUST-B2B")
    assert "customer_gstin" not in order
    db = _db(order, [_creds(JH_GSTIN)])
    db.seed("customers", [dict(OLD_BUYER)])
    out, calls = await _generate_with_stubbed_provider(monkeypatch, db, order)

    assert out["status"] == einvoice.STATUS_GENERATED and len(calls) == 1
    sent = out["einvoice_json"]
    assert (sent["BuyerDtls"]["Gstin"], sent["TranDtls"]["SupTyp"]) == (MH_GSTIN, "B2B")
    _assert_irp_arithmetic(sent)


def _decimal128(text):
    from bson.decimal128 import Decimal128

    return Decimal128(text)


@pytest.mark.parametrize("grand_total", ["1,000.00", "Rs 1000", _decimal128("1000.00"), 1000.0])
def test_l3_19_the_invoice_value_and_round_off_are_read_by_the_returns_money_rule(grand_total):
    """Every amount gst_base._money reads (a comma string, an 'Rs' prefix, a
    BSON Decimal128) is the SAME figure on the IRP as in GSTR-1: the invoice
    value and the round off went out as 0 through a private parser."""
    from api.routers.reports.gst_base import _money

    order = {
        "order_id": "ORD-L3", "invoice_number": "AUD/2627/00002", "invoice_date": "2026-11-04",
        "store_id": STORE["store_id"], "grand_total": grand_total, "tax_amount": 47.6,
        "tax_rate": 5, "round_off": _decimal128("0.40"),
        "items": [{"product_name": "Frame", "hsn_code": "9003", "quantity": 1, "item_total": 999.6,
                   "gst_rate": 5, "taxable_value": 952.0, "tax_amount": 47.6}],
    }
    payload = einvoice._build_einvoice_json(order, STORE)
    val = payload["ValDtls"]

    assert val["TotInvVal"] == _money(grand_total, order) == 1000.0  # GSTR-1's invoiceValue
    assert (val["AssVal"], val["RndOffAmt"]) == (952.0, 0.4)
    _assert_irp_arithmetic(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage, why", [
    ({"grand_total": "not money"}, "unreadable"),
    ({"grand_total": 0.0, "tax_amount": 0.0, "items": []}, "no taxable value"),
    # Rs 90 tax on Rs 1,000 with no line and no rate: 9% is no GST slab
    ({"grand_total": 1090.0, "tax_amount": 90.0, "tax_rate": None, "items": []}, "no gst slab"),
    ({"items": [{"product_name": "Mystery", "item_total": 1000.0}]}, "no gst rate"),
])
async def test_l3_19_an_order_the_returns_cannot_file_is_never_sent(monkeypatch, damage, why):
    """An unreadable amount, a Rs 0 bill, a tax that fits no GST slab or a
    line with no GST rate is refused BEFORE the provider call (FAILED, a
    reason, nothing stored) -- never sent as zeros or at a guessed rate."""
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    order = {**_till_order([FRAME]), **damage}
    db = _db(order, [_creds(JH_GSTIN)])

    async def _never(cfg, payload):
        raise AssertionError("the provider must not be called")

    monkeypatch.setattr(einvoice, "_call_irp", _never)
    out = await einvoice.generate_irn(db, order)

    assert out["status"] == einvoice.STATUS_FAILED and out["irn"] is None
    assert why in out["reason"].lower() and "return" not in out["reason"].lower()
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
    assert "record it" not in out["reason"].lower()  # no screen records an IRN by hand


def _provider(monkeypatch, *answers):
    """The provider stub answers in turn (an Exception is raised); returns the
    list every call is logged to. Gate lifted for the test only."""
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    queue, calls = list(answers), []

    async def _answer(cfg, payload):
        calls.append(payload["DocDtls"]["No"])
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return dict(nxt)

    monkeypatch.setattr(einvoice, "_call_irp", _answer)
    return calls


@pytest.mark.asyncio
async def test_l3_21_a_second_press_with_the_same_stale_order_never_calls_the_provider_again(monkeypatch):
    """The caller's copy of the order still has no IRN (a double press, a
    retry): the order as STORED is re-read before the provider call."""
    order = _till_order([FRAME])
    db = _db(order, [_creds(JH_GSTIN)])
    calls = _provider(monkeypatch, _IRP_OK, _IRP_OK)

    first = await einvoice.generate_irn(db, order)
    second = await einvoice.generate_irn(db, order)  # the same dict: no irn on it

    assert len(calls) == 1 and first["status"] == einvoice.STATUS_GENERATED
    assert (second["status"], second["irn"], second["signed_qr"], second["ack_no"]) == (
        einvoice.STATUS_SKIPPED, "SYNTHETIC-IRN-NOT-VALID", "SYNTHETIC-QR-NOT-VALID", "SYNTHETIC-ACK")


def test_l3_21_a_later_answer_never_blanks_the_saved_ack_or_signed_qr():
    """Two presses inside one provider round trip: the second answer is the
    IRP's duplicate reply (the IRN, no QR). It never overwrites with blanks."""
    order = _till_order([FRAME])
    db = _db(order)
    full = einvoice._parse_irp_response(_IRP_OK)

    assert einvoice._persist_irn(db, "ORD-L3", full) is True
    assert einvoice._persist_irn(db, "ORD-L3", einvoice._parse_irp_response({"Irn": full["irn"]})) is True

    stored = db.get_collection("orders").find_one({"order_id": "ORD-L3"})
    assert (stored["irn"], stored["ack_no"], stored["ack_date"], stored["einvoice_signed_qr"]) == (
        "SYNTHETIC-IRN-NOT-VALID", "SYNTHETIC-ACK", "2026-11-04", "SYNTHETIC-QR-NOT-VALID")


@pytest.mark.asyncio
async def test_l3_21_an_irn_saved_without_its_signed_qr_says_so_on_every_press(monkeypatch):
    """A lost reply, then the retry gets the IRP's duplicate answer (IRN only):
    the IRN is saved and the reply SAYS no signed QR came -- never a clean
    'all good' -- and so does every later press."""
    order = _till_order([FRAME])
    db = _db(order, [_creds(JH_GSTIN)])
    calls = _provider(monkeypatch, TimeoutError("synthetic timeout"),
                      {"Irn": "SYNTHETIC-IRN-NOT-VALID"})

    lost = await einvoice.generate_irn(db, order)
    assert lost["status"] == einvoice.STATUS_FAILED
    assert "irn" not in db.get_collection("orders").find_one({"order_id": "ORD-L3"})

    retry = await einvoice.generate_irn(db, order)
    assert (retry["status"], retry["persisted"]) == (einvoice.STATUS_GENERATED, True)
    assert "no signed qr" in retry["reason"].lower()
    stored = db.get_collection("orders").find_one({"order_id": "ORD-L3"})
    assert stored["irn"] == "SYNTHETIC-IRN-NOT-VALID" and not stored.get("einvoice_signed_qr")

    third = await einvoice.generate_irn(db, order)
    assert third["status"] == einvoice.STATUS_SKIPPED and "no signed qr" in third["reason"].lower()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_l3_21_an_order_that_cannot_be_re_read_is_not_sent(monkeypatch):
    """No way to confirm the stored order has no IRN yet: the provider is not
    asked for one (it could not be saved either)."""
    order = _till_order([FRAME])
    db = _db(order, [_creds(JH_GSTIN)])
    calls = _provider(monkeypatch, _IRP_OK)

    def _down(_db, _order_id):
        raise ConnectionError("synthetic: database unreachable")

    monkeypatch.setattr(einvoice, "_stored_order", _down, raising=False)
    out = await einvoice.generate_irn(db, order)

    assert calls == [] and out["status"] == einvoice.STATUS_FAILED and out["irn"] is None


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

    assert out == {"status": "CANCELLED", "reason": None, "persisted": True}
    stored = db.get_collection("orders").find_one({"order_id": "ORD-L3"})
    assert stored["einvoice_status"] == "CANCELLED" and stored["einvoice_cancelled_at"] is not None
    assert "einvoice_cancel_pending" not in stored


# --- 1409-P2: a cancel is never reported done while the stored order says
# GENERATED. Each case runs on the strict fake AND a native PyMongo Database.

_IRP_ALREADY = {"Status": "0", "ErrorDetails": [{"ErrorCode": "9999", "ErrorMessage": "Invoice is not active"}]}


def _cancel_irp(monkeypatch, *replies):
    """httpx.AsyncClient stand-in for the cancel call: answers ``replies`` in
    turn and records every post. No network."""
    from types import SimpleNamespace

    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    queue, posts = list(replies), []

    class _IRP:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, *a, **k):
            posts.append(url)
            body = queue.pop(0)
            return SimpleNamespace(status_code=200, text="", json=lambda: body)

    monkeypatch.setattr(einvoice.httpx, "AsyncClient", _IRP)
    return posts


def _break_writes(monkeypatch, db, when):
    """Every update_one on this database whose update matches ``when`` raises,
    as a refused write does (the real pymongo Collection class on Mongo)."""
    broken = {"on": True}
    cls = type(db.get_collection("orders"))
    real = cls.update_one

    def update_one(self, flt, update, *a, **k):
        if broken["on"] and when(update):
            raise ConnectionError("synthetic: the local save is unavailable")
        return real(self, flt, update, *a, **k)

    monkeypatch.setattr(cls, "update_one", update_one)
    return broken


def _cancel_write(update):
    return (update.get("$set") or {}).get("einvoice_status") == "CANCELLED"


@pytest.fixture(params=["strict_fake", "native_pymongo"])
def cancel_db(request):
    db = PyMongoLikeDB() if request.param == "strict_fake" else request.getfixturevalue("real_db")
    db.get_collection("stores").insert_one(dict(STORE))
    db.get_collection("integrations").insert_one(_creds(JH_GSTIN))
    return db


def _issued(db, coll="orders", **ids):
    order = {k: v for k, v in _till_order([FRAME]).items() if k != "order_id"}
    order.update(ids, irn="SYNTHETIC-IRN-NOT-VALID", einvoice_status="GENERATED")
    db.get_collection(coll).insert_one(dict(order))
    return order


def _row(db, coll, **ids):
    return db.get_collection(coll).find_one(ids, {"_id": 0})


@pytest.mark.asyncio
async def test_1409_p2_order_id_cancel_is_saved_once(monkeypatch, cancel_db):
    order = _issued(cancel_db, order_id="ORD-L3")
    posts = _cancel_irp(monkeypatch, {"Status": "1"})

    out = await einvoice.cancel_irn(cancel_db, order, 3, "synthetic")
    stored = _row(cancel_db, "orders", order_id="ORD-L3")
    assert out == {"status": "CANCELLED", "reason": None, "persisted": True}
    assert stored["einvoice_status"] == "CANCELLED" and "einvoice_cancel_pending" not in stored

    again = await einvoice.cancel_irn(cancel_db, order, 3, "synthetic")
    assert (again["status"], again["persisted"], len(posts)) == ("CANCELLED", True, 1)
    assert _row(cancel_db, "orders", order_id="ORD-L3")["einvoice_cancelled_at"] == stored["einvoice_cancelled_at"]


@pytest.mark.asyncio
async def test_1409_p2_an_invoice_id_only_record_is_cancelled_by_the_generation_id_rule(
    monkeypatch, cancel_db
):
    """Codex's case: generate_irn saved the IRN on an invoice known only by
    invoice_id; cancel answered CANCELLED but its own filter had no
    invoice_id, so the stored invoice still read GENERATED."""
    inv = {k: v for k, v in _till_order([FRAME]).items() if k != "order_id"}
    inv["invoice_id"] = "INV-L3-ONLY"
    cancel_db.get_collection("invoices").insert_one(dict(inv))
    gen, _calls = await _generate_with_stubbed_provider(monkeypatch, cancel_db, inv)
    assert (gen["status"], gen["persisted"]) == (einvoice.STATUS_GENERATED, True)
    saved = _row(cancel_db, "invoices", invoice_id="INV-L3-ONLY")
    posts = _cancel_irp(monkeypatch, {"Status": "1"})

    out = await einvoice.cancel_irn(cancel_db, saved)
    stored = _row(cancel_db, "invoices", invoice_id="INV-L3-ONLY")
    assert out == {"status": "CANCELLED", "reason": None, "persisted": True}
    assert stored["einvoice_status"] == "CANCELLED" and stored["einvoice_cancelled_at"] is not None

    retry = await einvoice.cancel_irn(cancel_db, saved)  # the identical (stale) copy
    assert (retry["status"], retry["persisted"], len(posts)) == ("CANCELLED", True, 1)
    assert _row(cancel_db, "invoices", invoice_id="INV-L3-ONLY")["einvoice_cancelled_at"] == stored[
        "einvoice_cancelled_at"]


@pytest.mark.asyncio
async def test_1409_p2_a_cancel_the_irp_took_but_the_order_did_not_save_converges_on_retry(
    monkeypatch, cancel_db
):
    """Codex's case: the IRP cancelled, the local write raised, and the
    answer was still CANCELLED / reason None; the identical retry got the
    IRP's "already cancelled" and answered FAILED for ever. Now the first
    answer says NOT saved, the request is on the order, and the retry
    records CANCELLED exactly once."""
    order = _issued(cancel_db, order_id="ORD-L3")
    posts = _cancel_irp(monkeypatch, {"Status": "1"}, _IRP_ALREADY)
    broken = _break_writes(monkeypatch, cancel_db, _cancel_write)

    first = await einvoice.cancel_irn(cancel_db, order)
    stored = _row(cancel_db, "orders", order_id="ORD-L3")
    assert (first["status"], first["persisted"]) == ("CANCELLED", False)
    assert "NOT saved" in first["reason"]
    assert stored["einvoice_status"] == "GENERATED"
    assert stored["einvoice_cancel_pending"]["irn"] == "SYNTHETIC-IRN-NOT-VALID"

    broken["on"] = False
    retry = await einvoice.cancel_irn(cancel_db, order)
    stored = _row(cancel_db, "orders", order_id="ORD-L3")
    assert (retry["status"], retry["persisted"]) == ("CANCELLED", True)
    assert "already cancelled" in retry["reason"]
    assert stored["einvoice_status"] == "CANCELLED" and "einvoice_cancel_pending" not in stored

    third = await einvoice.cancel_irn(cancel_db, order)
    assert (third["status"], third["persisted"], len(posts)) == ("CANCELLED", True, 2)
    assert _row(cancel_db, "orders", order_id="ORD-L3")["einvoice_cancelled_at"] == stored["einvoice_cancelled_at"]


_IRP_TOKEN = {"Status": "0", "ErrorDetails": [{"ErrorCode": "1005", "ErrorMessage": "Invalid Token"}]}


@pytest.mark.asyncio
async def test_1409_p2_an_unrelated_refusal_between_presses_keeps_the_earlier_request(
    monkeypatch, cancel_db
):
    """Reviewer's sequence: the IRP took the cancel but the order did not
    save; the next press got an unrelated "no" (expired token). That "no"
    says nothing about the EARLIER accepted request, so its record must stay
    and the press after records CANCELLED once."""
    order = _issued(cancel_db, order_id="ORD-L3")
    posts = _cancel_irp(monkeypatch, {"Status": "1"}, _IRP_TOKEN, _IRP_ALREADY)
    broken = _break_writes(monkeypatch, cancel_db, _cancel_write)

    first = await einvoice.cancel_irn(cancel_db, order)
    assert (first["status"], first["persisted"]) == ("CANCELLED", False)
    broken["on"] = False

    second = await einvoice.cancel_irn(cancel_db, order)
    stored = _row(cancel_db, "orders", order_id="ORD-L3")
    assert second["status"] == einvoice.STATUS_FAILED
    assert stored["einvoice_status"] == "GENERATED"
    assert stored["einvoice_cancel_pending"]["irn"] == "SYNTHETIC-IRN-NOT-VALID"

    third = await einvoice.cancel_irn(cancel_db, order)
    stored = _row(cancel_db, "orders", order_id="ORD-L3")
    assert (third["status"], third["persisted"]) == ("CANCELLED", True)
    assert stored["einvoice_status"] == "CANCELLED" and "einvoice_cancel_pending" not in stored

    fourth = await einvoice.cancel_irn(cancel_db, order)
    assert (fourth["status"], fourth["persisted"], len(posts)) == ("CANCELLED", True, 3)
    assert _row(cancel_db, "orders", order_id="ORD-L3")["einvoice_cancelled_at"] == stored["einvoice_cancelled_at"]


@pytest.mark.asyncio
async def test_1409_p2_the_same_lost_save_on_an_invoice_id_only_record(monkeypatch, cancel_db):
    inv = _issued(cancel_db, "invoices", invoice_id="INV-L3-ONLY")
    posts = _cancel_irp(monkeypatch, {"Status": "1"}, _IRP_ALREADY)
    broken = _break_writes(monkeypatch, cancel_db, _cancel_write)

    first = await einvoice.cancel_irn(cancel_db, inv)
    assert (first["status"], first["persisted"]) == ("CANCELLED", False)
    assert _row(cancel_db, "invoices", invoice_id="INV-L3-ONLY")["einvoice_status"] == "GENERATED"

    broken["on"] = False
    retry = await einvoice.cancel_irn(cancel_db, inv)
    assert (retry["status"], retry["persisted"], len(posts)) == ("CANCELLED", True, 2)
    assert _row(cancel_db, "invoices", invoice_id="INV-L3-ONLY")["einvoice_status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_1409_p2_a_cancel_that_cannot_be_recorded_first_is_not_sent(monkeypatch, cancel_db):
    """No record of the request, no request: the IRP is never asked."""
    order = _issued(cancel_db, order_id="ORD-L3")
    posts = _cancel_irp(monkeypatch, {"Status": "1"})
    _break_writes(monkeypatch, cancel_db, lambda update: True)

    out = await einvoice.cancel_irn(cancel_db, order)
    assert (out["status"], out["persisted"], posts) == (einvoice.STATUS_FAILED, False, [])
    assert _row(cancel_db, "orders", order_id="ORD-L3")["einvoice_status"] == "GENERATED"

    stale = await einvoice.cancel_irn(cancel_db, {**order, "irn": "ANOTHER-IRN"})
    assert (stale["status"], posts) == (einvoice.STATUS_FAILED, [])


@pytest.mark.asyncio
async def test_1409_p2_an_already_cancelled_answer_ims_never_asked_for_is_not_trusted(
    monkeypatch, cancel_db
):
    """Only an unsettled request of IMS's own makes "already cancelled" an
    answer; otherwise the stored order is left as it is."""
    order = _issued(cancel_db, order_id="ORD-L3")
    posts = _cancel_irp(monkeypatch, _IRP_ALREADY, _IRP_ALREADY)

    for _ in range(2):
        out = await einvoice.cancel_irn(cancel_db, order)
        stored = _row(cancel_db, "orders", order_id="ORD-L3")
        assert out["status"] == einvoice.STATUS_FAILED
        assert stored["einvoice_status"] == "GENERATED" and "einvoice_cancel_pending" not in stored
    assert len(posts) == 2


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

    # a second press with the same stale copy: the stored order is re-read
    again, calls = await _generate_with_stubbed_provider(monkeypatch, real_db, order)
    assert calls == [] and again["status"] == einvoice.STATUS_SKIPPED
    assert again["signed_qr"] == "SYNTHETIC-QR-NOT-VALID"


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
