"""
Every purchase-bill door books what its form showed, and every ITC reader
reports it once -- the panel's second round on F6 / F40, reproduced red first.

  P1  REGRESSION, manual bill: Recipient GSTIN left blank, the form previewed
      CGST 90 + SGST 90 while the server booked IGST 180 on the user's shop's
      GSTIN. The form now shows POST /preview -- the booking's own math.
  P2  Pune shop (Maharashtra) whose store record carries the company's MH
      registration: the bill booked IGST on the Jharkhand number while its
      purchase order said CGST + SGST. The bill now receives on the shop's
      own registration.
  P3  A legacy junk-prefix supplier GSTIN ("88..."): the form said IGST, the
      server booked CGST + SGST. The form holds no rule now; the server's is
      the one shown.
  P4  The form's paisa differed from the stored bill (it halved the unrounded
      sum). The preview carries the stored paisa.
  P5  GSTR-3B scoped purchase credit by COMPANY, so one bill counted on both
      of a two-registration company's returns. It now counts on the GSTIN it
      was received on.
  P6/P7/P13  The Cash Flow '+ bill' door stored no company and no tax heads:
      the ITC register counted its credit, GSTR-3B did not. It now stamps
      both by the same rule, and three screen bills + one door bill across
      two companies read one figure everywhere.
  P8  A bill with no company silently dropped out of GSTR-3B under a green
      Cross-Check. It is now counted and flagged.
  P9/P15/P16  A booking that cannot name a company, or whose typed GSTIN
      cannot be checked, is refused -- never stored with a null company or a
      guessed one; the draft and the booking resolve identically.
  P10/P11  The DC draft's receiving shop and the typed-GSTIN company rule are
      now pinned (both mutants survived before).

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_purchase_bill_one_rule.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import purchase_invoices as pi_router  # noqa: E402
from api.routers import reports  # noqa: E402
from api.routers import vendors as vend  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.finance import itc as itc_mod  # noqa: E402
from api.routers.finance.gst_crosscheck import _run_gst_cross_check  # noqa: E402
from api.routers.vendors.gst import _po_gst_parties  # noqa: E402
from test_purchase_invoice import (  # noqa: E402,F401
    BUY_JH,
    BUY_MH,
    SUP_JH,
    SUP_MH,
    _FakeDB,
    _StubRepo,
    _app,
    _invoice_body,
    _restore_router,
)

_URL = "/api/v1/vendors/purchase-invoices"


@pytest.fixture(autouse=True)
def _restore_vendors_and_itc():
    saved = (
        vend._get_db,
        vend.get_vendor_repository,
        vend.get_grn_repository,
        itc_mod._get_db,
        reports._get_raw_db,
    )
    yield
    (
        vend._get_db,
        vend.get_vendor_repository,
        vend.get_grn_repository,
        itc_mod._get_db,
        reports._get_raw_db,
    ) = saved


def _app_as(db, active_store):
    """_app, with the accountant's active shop set (None = no shop picked)."""
    cli = _app(db)

    async def _u():
        return {
            "user_id": "u1",
            "roles": ["ACCOUNTANT"],
            "store_ids": [active_store] if active_store else [],
            "active_store_id": active_store,
        }

    cli.app.dependency_overrides[get_current_user] = _u
    return cli


def _services(**over):
    """A manual bill as the form books it: Services, freight Rs 1000 @ 18%,
    no receipt, Recipient GSTIN left blank."""
    body = {
        "vendor_id": "V1",
        "invoice_number": "FR-9",
        "invoice_date": "2026-05-03",
        "bill_kind": "SERVICES",
        "lines": [{"description": "Freight", "qty": 1, "unit_price": 1000, "gst_rate": 18}],
    }
    body.update(over)
    return body


def _two_companies(db=None):
    """E1 Better Vision (Jharkhand, shop S1) and E2 WizOpt (Maharashtra, S2)."""
    db = db or _FakeDB()
    db.collections["stores"][0].update({"state_code": "20", "gstin": BUY_JH})
    db.collections["entities"].append(
        {
            "entity_id": "E2",
            "name": "WizOpt",
            "gstins": [{"gstin": BUY_MH, "state_code": "27", "is_primary": True}],
        }
    )
    db.collections["stores"].append(
        {"store_id": "S2", "entity_id": "E2", "state_code": "27", "gstin": BUY_MH}
    )
    return db


_HEAD_KEYS = ("interstate", "recipient_gstin", "cgst_total", "sgst_total", "igst_total", "total")


def _same_split(preview, doc):
    for k in _HEAD_KEYS:
        assert preview[k] == doc[k], (k, preview[k], doc[k])
    assert [(ln["cgst"], ln["sgst"], ln["igst"]) for ln in preview["lines"]] == [
        (ln["cgst"], ln["sgst"], ln["igst"]) for ln in doc["lines"]
    ]


# ===========================================================================
# P1 / P3 / P4 -- the form shows the booking's own math
# ===========================================================================


class TestTheFormShowsTheBooking:
    def test_manual_bill_blank_recipient_preview_is_the_booking(self):
        """P1, the panel's exact booking: accountant at S1 (E1, JH), Services,
        Maharashtra supplier, freight 1000 @ 18%, Recipient GSTIN blank. The
        form showed CGST 90 + SGST 90; the stored bill was IGST 180."""
        cli = _app(_two_companies())
        pv = cli.post(f"{_URL}/preview", json=_services())
        assert pv.status_code == 200, pv.text
        doc = cli.post(_URL, json=_services())
        assert doc.status_code == 201, doc.text
        pv, doc = pv.json(), doc.json()
        _same_split(pv, doc)
        assert doc["recipient_gstin"] == BUY_JH and doc["recipient_entity_id"] == "E1"
        assert doc["interstate"] is True and doc["igst_total"] == 180.0

    def test_the_paisa_shown_are_the_paisa_booked(self):
        """P4: one line of Rs 1001 @ 5% same-state stores CGST 25.02 + SGST
        25.03; three lines of Rs 10.10 @ 5% store 0.78 / 0.75 / 31.83. The
        form showed 25.03 + 25.03 and 0.76 / 0.76 / 31.82."""
        db = _two_companies()
        db.collections["vendors"][0]["gstin"] = SUP_JH
        cli = _app(db)
        one = _services(lines=[{"description": "x", "qty": 1, "unit_price": 1001, "gst_rate": 5}])
        pv = cli.post(f"{_URL}/preview", json=one).json()
        assert (pv["cgst_total"], pv["sgst_total"]) == (25.02, 25.03)
        _same_split(pv, cli.post(_URL, json=one).json())

        three = _services(
            invoice_number="FR-10",
            lines=[{"description": f"x{i}", "qty": 1, "unit_price": 10.10, "gst_rate": 5} for i in range(3)],
        )
        pv = cli.post(f"{_URL}/preview", json=three).json()
        assert (pv["cgst_total"], pv["sgst_total"], pv["total"]) == (0.78, 0.75, 31.83)
        _same_split(pv, cli.post(_URL, json=three).json())

    def test_a_junk_prefix_supplier_gstin_is_previewed_as_booked(self):
        """P3: '88...' names no state, so the one rule books CGST + SGST --
        and that is what the preview (the form's only source) says."""
        db = _two_companies()
        db.collections["vendors"][0]["gstin"] = "88AABCU9603R1ZF"
        cli = _app(db)
        pv = cli.post(f"{_URL}/preview", json=_services()).json()
        assert pv["interstate"] is False
        assert (pv["cgst_total"], pv["sgst_total"], pv["igst_total"]) == (90.0, 90.0, 0.0)
        _same_split(pv, cli.post(_URL, json=_services()).json())


# ===========================================================================
# P2 -- the bill receives on the shop's own registration, like its PO
# ===========================================================================


class TestTheShopsOwnRegistration:
    def test_pune_bill_matches_its_purchase_order(self):
        """BVOPL holds 20... (main) and 27...; the Pune shop is state 27 and
        carries the 27 number. A Maharashtra vendor's Rs 1000 @ 5% receipt
        there: the draft, the form and the stored bill say CGST 25 + SGST 25
        on the 27 number -- the purchase order's own verdict."""
        db = _FakeDB()
        db.collections["entities"][0]["gstins"].append({"gstin": BUY_MH, "state_code": "27"})
        pune = {"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": BUY_MH}
        db.collections["stores"].append(pune)
        grn = {
            "grn_id": "GP",
            "po_id": "POP",
            "vendor_id": "V1",
            "store_id": "PUNE",
            "status": "ACCEPTED",
            "vendor_invoice_no": "MLH-90",
            "vendor_invoice_date": "2026-05-04",
            "items": [{"product_id": "P1", "product_name": "Frame X", "accepted_qty": 1}],
        }
        po = {"po_id": "POP", "items": [{"product_id": "P1", "unit_price": 1000.0, "tax_rate": 5.0}]}
        cli = _app(db)  # the accountant sits at S1 (Jharkhand)
        pi_router.get_grn_repository = lambda: _StubRepo(grn)
        pi_router.get_purchase_order_repository = lambda: _StubRepo(po)

        draft = cli.get(f"{_URL}/from-grn/GP").json()
        assert draft["recipient_gstin"] == BUY_MH and draft["interstate"] is False
        assert (draft["cgst_total"], draft["sgst_total"]) == (25.0, 25.0)
        body = {
            k: draft[k]
            for k in ("vendor_id", "invoice_number", "invoice_date", "recipient_gstin", "po_id", "grn_id", "lines", "total")
        }
        doc = cli.post(_URL, json=body).json()
        assert doc["recipient_gstin"] == BUY_MH and doc["interstate"] is False
        assert (doc["cgst_total"], doc["sgst_total"], doc["igst_total"]) == (25.0, 25.0, 0.0)
        po_verdict = _po_gst_parties(db.collections["vendors"][0], pune)["interstate"]
        assert po_verdict is doc["interstate"] is False


# ===========================================================================
# P9 / P10 / P11 / P15 / P16 -- the recipient is decided, never guessed
# ===========================================================================


class TestTheRecipientIsNeverGuessed:
    def test_a_booking_that_names_no_company_is_refused(self):
        """P9: two companies, the accountant's shop has no company and no
        GSTIN is typed -- stored before as recipient_entity_id null (invisible
        to GSTR-3B). Refused, with nothing written; the preview says so too."""
        db = _two_companies()
        db.collections["stores"][0].pop("entity_id")
        cli = _app(db)
        for url in (f"{_URL}/preview", _URL):
            r = cli.post(url, json=_services())
            assert r.status_code == 422, r.text
            assert r.json()["detail"]["code"] == "RECIPIENT_UNRESOLVED"
        assert db.collections["vendor_bills"] == []

    def test_no_shop_picked_is_refused_too(self):
        db = _two_companies()
        r = _app_as(db, None).post(_URL, json=_services())
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "RECIPIENT_UNRESOLVED"

    def test_a_receipt_shop_without_a_company_is_never_the_users_shops(self):
        """P15: goods received at S3 (no company on its store record) while
        the accountant sits at S1 (E1). The draft said CGST + SGST with no
        recipient; the booking stored IGST on E1. Both now refuse alike."""
        db = _two_companies()
        db.collections["stores"].append({"store_id": "S3"})
        grn = {
            "grn_id": "G3",
            "vendor_id": "V1",
            "store_id": "S3",
            "status": "ACCEPTED",
            "items": [{"product_id": "P1", "accepted_qty": 10}],
        }
        cli = _app(db)
        pi_router.get_grn_repository = lambda: _StubRepo(grn)
        draft = cli.get(f"{_URL}/from-grn/G3")
        booked = cli.post(_URL, json=_invoice_body(grn_id="G3"))
        for r in (draft, booked):
            assert r.status_code == 422, r.text
            assert r.json()["detail"]["code"] == "RECIPIENT_UNRESOLVED"
        assert db.collections["vendor_bills"] == []

    def test_draft_and_booking_pass_the_same_arguments(self):
        """P15, the other half: the from-GRN draft called the recipient rule
        without the user's shop, the booking with it. On a receipt that names
        no shop at all the booking fell back to S1 while the draft did not.
        Both now pass the same arguments, so they name the same company."""
        db = _two_companies()
        grn = {
            "grn_id": "G0",
            "vendor_id": "V1",
            "status": "ACCEPTED",
            "items": [{"product_id": "P1", "accepted_qty": 10}],
        }
        cli = _app(db)
        pi_router.get_grn_repository = lambda: _StubRepo(grn)
        draft = cli.get(f"{_URL}/from-grn/G0").json()
        doc = cli.post(_URL, json=_invoice_body(grn_id="G0")).json()
        assert draft["recipient_entity_id"] == doc["recipient_entity_id"] == "E1"
        assert draft["recipient_gstin"] == doc["recipient_gstin"] == BUY_JH
        assert draft["interstate"] is doc["interstate"] is True

    def test_a_typed_gstin_is_checked_even_when_the_company_lists_none(self):
        """P16: a company with no registrations on file cannot vouch for a
        typed number -- it was stored unchecked."""
        db = _FakeDB()
        db.collections["entities"][0]["gstins"] = []
        r = _app(db).post(_URL, json=_services(recipient_gstin="20AAAAA0000A1Z5"))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "RECIPIENT_GSTIN_NOT_OURS"

    def test_a_typed_gstin_names_its_company_on_a_manual_bill(self):
        """P11 (a surviving mutant): no receipt, active shop S1 (E1), WizOpt's
        number typed as printed on the bill -> the bill is WizOpt's, for a
        caller who can reach a WizOpt shop (round 12 item 8: else 403)."""
        db = _two_companies()
        r = _app(db).post(_URL, json=_services(recipient_gstin=BUY_MH))
        assert r.status_code == 403, r.text  # S1-only accountant
        cli = _app_as(db, "S1")

        async def _u():
            return {
                "user_id": "u1",
                "roles": ["ACCOUNTANT"],
                "store_ids": ["S1", "S2"],
                "active_store_id": "S1",
            }

        cli.app.dependency_overrides[get_current_user] = _u
        r = cli.post(_URL, json=_services(recipient_gstin=BUY_MH))
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["recipient_entity_id"] == "E2" and doc["recipient_gstin"] == BUY_MH
        assert doc["interstate"] is False

    def test_the_dc_draft_names_the_receiving_shops_company(self):
        """P10 (a surviving mutant): a Delivery Challan received at S2
        (WizOpt) drafted by an accountant sitting at S1 (Better Vision)."""
        db = _two_companies()
        db.collections["grns"] = [
            {
                "grn_id": "D1",
                "grn_subtype": "DELIVERY_CHALLAN",
                "status": "ACCEPTED",
                "vendor_id": "V1",
                "store_id": "S2",
                "items": [{"product_id": "P1", "accepted_qty": 2}],
            }
        ]
        r = _app(db).get(f"{_URL}/from-dcs", params={"dc_ids": "D1", "vendor_id": "V1"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["recipient_entity_id"] == "E2" and d["recipient_gstin"] == BUY_MH
        assert d["interstate"] is False


# ===========================================================================
# P5 / P6 / P7 / P8 / P13 -- every door, every reader, one figure
# ===========================================================================


class _Repo:
    def __init__(self, docs, key):
        self._docs = {d[key]: d for d in docs}

    def find_by_id(self, _id):
        d = self._docs.get(_id)
        return dict(d) if d else None


def _mongo(companies):
    """A mongomock world: vendors V1 (Maharashtra) + V2 (Jharkhand), the
    given companies and shops."""
    db = mongomock.MongoClient().db
    db["vendors"].insert_many(
        [
            {"vendor_id": "V1", "trade_name": "Mumbai Lens House", "gstin": SUP_MH, "credit_days": 30},
            {"vendor_id": "V2", "trade_name": "Ranchi Optics", "gstin": SUP_JH, "credit_days": 30},
        ]
    )
    for entity, stores in companies:
        db["entities"].insert_one(entity)
        db["stores"].insert_many(stores)
    return db


def _book_from_grn(cli, grn_id, invoice_number):
    draft = cli.get(f"{_URL}/from-grn/{grn_id}").json()
    body = {k: draft[k] for k in ("vendor_id", "invoice_date", "recipient_gstin", "po_id", "grn_id", "lines", "total")}
    body["invoice_number"] = invoice_number
    r = cli.post(_URL, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _crosscheck(db, entity_id, month=5, year=2026):
    reports._get_raw_db = lambda: db
    return _run_gst_cross_check(db, month, year, entity_id)


def _row(result, metric):
    return next(c for c in result["comparisons"] if c["metric"] == metric)


def _register(db):
    itc_mod._get_db = lambda: db
    return asyncio.run(
        itc_mod.itc_register(period="2026-05", entity_id=None, current_user={"roles": ["ADMIN"]})
    )


class TestOneBillOneReturn:
    def test_a_bill_counts_only_on_the_gstin_it_was_received_on(self):
        """P5: BVOPL (E1) files two returns -- 20... for BV-DHN-02 (S1) and
        27... for PUNE. A bill received on 20... claimed IGST 50 on BOTH."""
        from api.routers.reports import _itc_from_vendor_bills

        db = _mongo(
            [
                (
                    {
                        "entity_id": "E1",
                        "name": "BVOPL",
                        "gstins": [
                            {"gstin": BUY_JH, "state_code": "20", "is_primary": True},
                            {"gstin": BUY_MH, "state_code": "27"},
                        ],
                    },
                    # PUNE listed first: a company-wide dedupe would take ITS
                    # figure, so the Cross-Check depends on the GSTIN slice.
                    [
                        {"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": BUY_MH},
                        {"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
                    ],
                )
            ]
        )
        cli = _app(db)
        r = cli.post(_URL, json=_invoice_body(recipient_gstin=BUY_JH))
        assert r.status_code == 201, r.text
        assert _itc_from_vendor_bills(db, "S1", 2026, 5, 31) == (50.0, 0.0, 0.0)
        assert _itc_from_vendor_bills(db, "PUNE", 2026, 5, 31) == (0.0, 0.0, 0.0)
        assert _crosscheck(db, "E1")["gstr3b"]["itc"]["total"] == 50.0


class TestEveryDoorEveryReader:
    def _world(self, active="S1"):
        db = _mongo(
            [
                (
                    {"entity_id": "E1", "name": "Better Vision", "gstins": [{"gstin": BUY_JH, "state_code": "20", "is_primary": True}]},
                    [{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH}],
                ),
                (
                    {"entity_id": "E2", "name": "WizOpt", "gstins": [{"gstin": BUY_MH, "state_code": "27", "is_primary": True}]},
                    [{"store_id": "S2", "entity_id": "E2", "state_code": "27", "gstin": BUY_MH}],
                ),
            ]
        )
        grns = [
            {"grn_id": gid, "po_id": f"PO-{gid}", "vendor_id": vid, "store_id": sid, "status": "ACCEPTED",
             "vendor_invoice_date": "2026-05-05", "items": [{"product_id": "P1", "product_name": "Frame", "accepted_qty": 1}]}
            for gid, vid, sid in (("GA", "V1", "S1"), ("GB", "V2", "S1"), ("GC", "V1", "S2"))
        ]
        pos = [
            {"po_id": f"PO-{g['grn_id']}", "items": [{"product_id": "P1", "unit_price": 1000.0, "tax_rate": 5.0}]}
            for g in grns
        ]
        cli = _app_as(db, active)  # the accountant's shop (S1 unless told)
        vendors = _Repo(list(db["vendors"].find({}, {"_id": 0})), "vendor_id")
        pi_router.get_grn_repository = lambda: _Repo(grns, "grn_id")
        pi_router.get_purchase_order_repository = lambda: _Repo(pos, "po_id")
        pi_router.get_vendor_repository = lambda: vendors
        vend._get_db = lambda: db
        vend.get_vendor_repository = lambda: vendors
        vend.get_grn_repository = lambda: _Repo(grns, "grn_id")
        return db, cli

    def test_three_screen_bills_and_a_cash_flow_bill_read_one_figure(self):
        """The lens's scenario: A-1 MH supplier -> S1 (IGST 50), B-1 JH
        supplier -> S1 (CGST + SGST 50), C-1 MH supplier -> S2 (CGST + SGST
        50) through the screen, and FR-9 freight (Rs 1000, tax 180) through
        the Cash Flow '+ bill' door. Register 330, GSTR-3B (Cross-Check) 150
        before; every reader now says 330, in the same heads."""
        db, cli = self._world()
        a = _book_from_grn(cli, "GA", "A-1")
        b = _book_from_grn(cli, "GB", "B-1")
        c = _book_from_grn(cli, "GC", "C-1")
        assert (a["igst_total"], b["cgst_total"], c["cgst_total"]) == (50.0, 25.0, 25.0)

        door = FastAPI()
        door.include_router(vend.router, prefix="/api/v1/vendors")
        door.dependency_overrides[get_current_user] = cli.app.dependency_overrides[get_current_user]
        r = TestClient(door).post(
            "/api/v1/vendors/V1/bills",
            json={"bill_number": "FR-9", "bill_date": "2026-05-09", "taxable_amount": 1000,
                  "tax_amount": 180, "total_amount": 1180, "bill_kind": "SERVICES"},
        )
        assert r.status_code == 201, r.text
        fr9 = r.json()
        assert fr9["recipient_entity_id"] == "E1" and fr9["recipient_gstin"] == BUY_JH
        assert (fr9["igst_total"], fr9["cgst_total"], fr9["sgst_total"]) == (180.0, 0.0, 0.0)

        reg = _register(db)
        assert reg["total_itc"] == 330.0
        assert (reg["total_igst"], reg["total_cgst"], reg["total_sgst"]) == (230.0, 50.0, 50.0)
        xc = _crosscheck(db, None)
        assert xc["gstr3b"]["itc"] == {"cgst": 50.0, "sgst": 50.0, "igst": 230.0, "total": 330.0}
        assert _row(xc, "Input credit left off GSTR-3B")["status"] == "MATCH"
        assert _crosscheck(db, "E1")["gstr3b"]["itc"]["total"] == 280.0
        assert _crosscheck(db, "E2")["gstr3b"]["itc"]["total"] == 50.0

        # The Purchase Invoices list is the fourth reader: it listed only the
        # screen's bills (3 rows, tax 150). A transfer mirror is not a bill.
        db["vendor_bills"].insert_one(
            {"bill_id": "m1", "bill_number": "TRF/T1", "source_transfer_id": "T1", "bill_date": "2026-05-10",
             "tax_amount": 500, "recipient_entity_id": "E1", "status": "OUTSTANDING"}
        )
        rows = cli.get(_URL).json()["purchase_invoices"]
        assert sorted(r["bill_number"] for r in rows) == ["A-1", "B-1", "C-1", "FR-9"]
        assert round(sum(r["tax_amount"] for r in rows), 2) == 330.0


class TestCreditLeftOffEveryReturnIsFlagged:
    def _world(self):
        return _mongo(
            [
                (
                    {"entity_id": "E1", "name": "Better Vision", "gstins": [{"gstin": BUY_JH, "state_code": "20", "is_primary": True}]},
                    [{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH}],
                )
            ]
        )

    def test_a_bill_with_no_company_turns_the_crosscheck_red(self):
        """P8: OLD-1, booked before F40 with recipient_entity_id null, IGST
        50. The register and the list count it, GSTR-3B does not, and the
        Cross-Check read MATCH. It is now counted and flagged in every view."""
        db = self._world()
        db["vendor_bills"].insert_one(
            {"bill_id": "b-old", "doc_type": "PURCHASE_INVOICE", "bill_number": "OLD-1", "vendor_id": "V1",
             "bill_date": "2026-05-02", "taxable_amount": 1000, "tax_amount": 50, "cgst_total": 0.0,
             "sgst_total": 0.0, "igst_total": 50.0, "recipient_entity_id": None, "status": "OUTSTANDING"}
        )
        for entity in (None, "E1"):
            xc = _crosscheck(db, entity)
            row = _row(xc, "Input credit left off GSTR-3B")
            assert row["status"] == "MISMATCH" and row["variance"] == 50.0
            assert "OLD-1" in row["note"]
            assert "Input credit left off GSTR-3B" in xc["summary"]["mismatch_metrics"]
            assert xc["itc_unplaced"]["count"] == 1

    def test_a_legacy_bill_with_no_tax_heads_is_flagged(self):
        """P6: a header-only bill from before its door stored heads is read by
        GSTR-3B as Rs 0 (Table 4 sums the heads) -- flagged, not dropped."""
        db = self._world()
        db["vendor_bills"].insert_one(
            {"bill_id": "b-hdr", "bill_number": "HDR-1", "vendor_id": "V1", "bill_date": "2026-05-06",
             "taxable_amount": 1000, "tax_amount": 50, "recipient_entity_id": "E1", "status": "OUTSTANDING"}
        )
        row = _row(_crosscheck(db, "E1"), "Input credit left off GSTR-3B")
        assert row["status"] == "MISMATCH" and row["variance"] == 50.0


# ===========================================================================
# Panel round 3 -- the receipt's shop on every door, every reader in any
# store order, and no check that fails open
# ===========================================================================


def _door(cli, vendor_id, **bill):
    """POST the Cash Flow '+ bill' door (vendors router) as `cli`'s user."""
    app = FastAPI()
    app.include_router(vend.router, prefix="/api/v1/vendors")
    app.dependency_overrides[get_current_user] = cli.app.dependency_overrides[get_current_user]
    return TestClient(app).post(f"/api/v1/vendors/{vendor_id}/bills", json=bill)


class TestTheReceiptsShopOnEveryDoor:
    """The receipt's shop names the company; the accountant's own shop never
    does. GRN GA was received at S1 (Better Vision, JH) from V1 (Maharashtra);
    the accountant sits at S2 (WizOpt, MH). A mutant that drops the receipt's
    shop books WizOpt's 27... number with CGST + SGST -- the credit lands on
    the other company's GSTR-3B."""

    def test_cash_flow_goods_bill_is_the_receipts_company(self):
        """Mutant M1 (ap_bills `_receipt_store = None`) survived every suite."""
        _, cli = TestEveryDoorEveryReader()._world("S2")
        r = _door(cli, "V1", bill_number="G-1", bill_date="2026-05-09", taxable_amount=1000,
                  tax_amount=50, total_amount=1050, bill_kind="GOODS", grn_id="GA")
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["recipient_entity_id"] == "E1" and doc["recipient_gstin"] == BUY_JH
        assert (doc["igst_total"], doc["cgst_total"], doc["sgst_total"]) == (50.0, 0.0, 0.0)

    def test_the_preview_of_a_receipt_bill_is_the_booking(self):
        """Mutant M7 (preview `grn_doc = None`) survived: the preview named
        WizOpt's number while the booking stored Better Vision's."""
        _, cli = TestEveryDoorEveryReader()._world("S2")
        body = {"vendor_id": "V1", "grn_id": "GA", "lines": [
            {"product_id": "P1", "description": "Frame", "qty": 1, "unit_price": 1000, "gst_rate": 5}]}
        pv = cli.post(f"{_URL}/preview", json=body)
        assert pv.status_code == 200, pv.text
        pv = pv.json()
        assert pv["recipient_entity_id"] == "E1" and pv["recipient_gstin"] == BUY_JH
        doc = cli.post(_URL, json={**body, "invoice_number": "A-9", "invoice_date": "2026-05-09", "po_id": "PO-GA"})
        assert doc.status_code == 201, doc.text
        _same_split(pv, doc.json())

    def test_dcs_name_one_shop_for_draft_preview_and_booking(self):
        """DX1 (legacy, no shop) + DX2 at PUNE (BVOPL's 27 number), a
        Maharashtra vendor, the accountant at S1, Recipient GSTIN cleared. The
        draft read the first DC WITH a shop (27..., CGST + SGST); the preview
        and booking read only the first DC (none) and fell back to S1's 20...
        (IGST). One helper now names the shop for all three."""
        db = _mongo(
            [
                (
                    {"entity_id": "E1", "name": "BVOPL", "gstins": [
                        {"gstin": BUY_JH, "state_code": "20", "is_primary": True},
                        {"gstin": BUY_MH, "state_code": "27"}]},
                    [{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
                     {"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": BUY_MH}],
                )
            ]
        )
        db["grns"].insert_many(
            [
                {"grn_id": gid, "grn_subtype": "DELIVERY_CHALLAN", "status": "ACCEPTED", "vendor_id": "V1",
                 "items": [{"product_id": "P1", "accepted_qty": 1}], **extra}
                for gid, extra in (("DX1", {}), ("DX2", {"store_id": "PUNE"}))
            ]
        )
        cli = _app(db)
        draft = cli.get(f"{_URL}/from-dcs", params={"dc_ids": "DX1,DX2", "vendor_id": "V1"})
        assert draft.status_code == 200, draft.text
        assert draft.json()["recipient_gstin"] == BUY_MH and draft.json()["interstate"] is False
        body = {"vendor_id": "V1", "linked_dc_ids": ["DX1", "DX2"], "lines": [
            {"product_id": "P1", "description": "Frame", "qty": 2, "unit_price": 1000, "gst_rate": 5}]}
        pv = cli.post(f"{_URL}/preview", json=body).json()
        assert pv["recipient_gstin"] == BUY_MH and pv["interstate"] is False
        doc = cli.post(_URL, json={**body, "invoice_number": "DX-1", "invoice_date": "2026-05-09"})
        assert doc.status_code == 201, doc.text
        _same_split(pv, doc.json())


class TestTheDraftCarriesTheReceiptsProducts:
    def test_a_dc_draft_names_the_catalogue_product_and_hsn(self):
        """A DC line stores only product_id + qty, and a DC has no PO, so the
        draft opened with a blank name and HSN on every line -- the form would
        not book until each was retyped. The catalogue names them."""
        db = _FakeDB()
        db.collections["products"] = [{"product_id": "P1", "name": "Carrera CA 8895 807", "hsn_code": "9003"}]
        db.collections["grns"] = [
            {"grn_id": "D1", "grn_subtype": "DELIVERY_CHALLAN", "status": "ACCEPTED", "vendor_id": "V1",
             "store_id": "S1", "items": [{"product_id": "P1", "accepted_qty": 2}]}
        ]
        r = _app(db).get(f"{_URL}/from-dcs", params={"dc_ids": "D1", "vendor_id": "V1"})
        assert r.status_code == 200, r.text
        (ln,) = r.json()["lines"]
        assert (ln["description"], ln["hsn"], ln["qty"]) == ("Carrera CA 8895 807", "9003", 2)


class TestNoCheckFailsOpen:
    def test_a_bill_needs_its_invoice_date(self):
        """A blank date booked with invoice_date '' and due_date None, so the
        credit-days due date silently never happened."""
        r = _app(_FakeDB()).post(_URL, json=_invoice_body(invoice_date="  "))
        assert r.status_code == 422, r.text

    def test_an_unreadable_company_master_refuses_the_booking(self):
        """The entities read failing took the 'no company master' branch: the
        typed GSTIN went unchecked and the bill booked anyway."""

        class _Down(_FakeDB):
            def get_collection(self, name):
                if name == "entities":
                    raise RuntimeError("mongo blip")
                return super().get_collection(name)

        db = _Down()
        r = _app(db).post(_URL, json=_services(recipient_gstin=BUY_MH))
        assert r.status_code == 503, r.text
        assert db.collections["vendor_bills"] == []

    def test_the_cash_flow_door_does_not_ask_for_a_gstin_it_has_no_box_for(self):
        """No shop picked, two companies: the refusal told the accountant to
        'type our GSTIN', and the Cash Flow form has no such box."""
        _, cli = TestEveryDoorEveryReader()._world(None)
        r = _door(cli, "V1", bill_number="FR-1", bill_date="2026-05-09",
                  taxable_amount=1000, tax_amount=180, total_amount=1180, bill_kind="SERVICES")
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "RECIPIENT_UNRESOLVED"
        assert "GSTIN" not in detail["message"] and "top bar" in detail["message"]

    def test_an_unreadable_bill_list_never_reads_green(self, monkeypatch):
        """_itc_unplaced swallowed any read error and reported 0 bills, so the
        'Input credit left off GSTR-3B' row read MATCH on a DB failure."""
        from api.routers.reports import gst_itc

        db = TestCreditLeftOffEveryReturnIsFlagged()._world()

        def _boom(*a, **k):
            raise RuntimeError("mongo blip")

        monkeypatch.setattr(gst_itc, "_itc_match", _boom)
        xc = _crosscheck(db, "E1")
        assert _row(xc, "Input credit left off GSTR-3B")["status"] == "MISMATCH"
        assert xc["itc_leg_failed"] is True


class TestOneGstinOneFigureInAnyStoreOrder:
    """E1 holds only the Jharkhand number; its shops S1 (state 20) and PUNE
    (state 27) both carry it (stores._derive_store_gstin's fallback). WizOpt
    (E2) transfers Rs 10,000 to PUNE: the mirror books Rs 500 CGST + SGST with
    NO recipient GSTIN (E1 has no 27 registration). Plus A-1 at S1, IGST 50.
    The GSTIN slice was counted once per GSTIN, and S1's slice (50) and PUNE's
    (550) differed -- whichever shop Mongo listed first won."""

    def _world(self, order):
        shops = {
            "S1": {"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
            "PUNE": {"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": BUY_JH},
        }
        db = _mongo(
            [
                ({"entity_id": "E1", "name": "BVOPL", "gstins": [{"gstin": BUY_JH, "state_code": "20", "is_primary": True}]},
                 [shops[s] for s in order]),
                ({"entity_id": "E2", "name": "WizOpt", "gstins": [{"gstin": BUY_MH, "state_code": "27", "is_primary": True}]},
                 [{"store_id": "S2", "entity_id": "E2", "state_code": "27", "gstin": BUY_MH}]),
            ]
        )
        common = {"status": "OUTSTANDING", "itc_eligible": True, "bill_date": "2026-05-12",
                  "invoice_date": "2026-05-12", "recipient_entity_id": "E1"}
        db["vendor_bills"].insert_many(
            [
                {**common, "bill_id": "m1", "bill_number": "TRF/T1", "source_transfer_id": "T1",
                 "from_store_id": "S2", "to_store_id": "PUNE", "vendor_gstin": BUY_MH, "recipient_gstin": "",
                 "taxable_amount": 10000, "tax_amount": 500, "cgst_total": 250.0, "sgst_total": 250.0,
                 "igst_total": 0.0},
                {**common, "bill_id": "a1", "bill_number": "A-1", "doc_type": "PURCHASE_INVOICE",
                 "vendor_gstin": SUP_MH, "recipient_gstin": BUY_JH, "taxable_amount": 1000,
                 "tax_amount": 50, "cgst_total": 0.0, "sgst_total": 0.0, "igst_total": 50.0},
            ]
        )
        return db

    @pytest.mark.parametrize("order", [("S1", "PUNE"), ("PUNE", "S1")])
    def test_the_crosscheck_counts_the_mirror_whichever_shop_comes_first(self, order):
        from api.routers.reports import _itc_from_vendor_bills

        db = self._world(order)
        assert _register(db)["total_itc"] == 550.0
        xc = _crosscheck(db, "E1")
        assert xc["gstr3b"]["itc"]["total"] == 550.0
        assert _row(xc, "Input credit left off GSTR-3B")["status"] == "MATCH"
        # One GSTIN, one filing: both shops print the same Table 4.
        s1 = _itc_from_vendor_bills(db, "S1", 2026, 5, 31)
        assert s1 == _itc_from_vendor_bills(db, "PUNE", 2026, 5, 31) == (50.0, 250.0, 250.0)


class TestCreditFromAnUnregisteredSupplierIsFlagged:
    def test_gst_typed_on_a_bill_from_a_supplier_with_no_gstin_is_flagged(self):
        """An unregistered supplier's tax never reaches GSTR-2B, so that
        credit is not claimable -- it was counted on 20...'s return with no
        word. Rs 1000.11 @ 5% at S1 from VN (no GSTIN)."""
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        db["vendors"].insert_one({"vendor_id": "VN", "trade_name": "Local Fitter", "credit_days": 0})
        r = _app(db).post(_URL, json=_services(vendor_id="VN", invoice_number="VN-1", lines=[
            {"description": "Fitting", "qty": 1, "unit_price": 1000.11, "gst_rate": 5}]))
        assert r.status_code == 201, r.text
        # Booked before round 12 (when a bill with no valid supplier GSTIN
        # began to be booked no-credit): the credit is still claimed.
        db["vendor_bills"].update_many({}, {"$set": {"itc_eligible": True}})
        xc = _crosscheck(db, "E1")
        assert xc["gstr3b"]["itc"]["total"] == 50.01
        row = _row(xc, "Input credit from suppliers with no GSTIN")
        assert row["status"] == "MISMATCH" and row["variance"] == 50.01
        assert "VN-1" in row["note"]

    def test_a_gstin_given_to_the_supplier_later_does_not_clear_the_bill(self):
        """Panel MEDIUM (gst_itc._itc_unplaced): the row read the vendor
        master's CURRENT GSTIN. FR-1 from VNO (no GSTIN) through the Cash Flow
        door booked CGST 60 + SGST 60 on our JH number. VNO then got a
        Maharashtra GSTIN and the row read MATCH, while the bill still claims
        CGST + SGST and the supplier's GSTR-1 carries IGST 120. The bill's own
        supplier GSTIN, the one its head was set by, judges it."""
        db, cli = TestEveryDoorEveryReader()._world()
        vno = {"vendor_id": "VNO", "trade_name": "Local Fitter", "credit_days": 0}
        db["vendors"].insert_one(dict(vno))
        vend.get_vendor_repository = lambda: _Repo([vno], "vendor_id")
        r = _door(cli, "VNO", **_cash_flow_bill(bill_number="FR-1", tax_amount=120, total_amount=1120))
        assert r.status_code == 201, r.text
        bill = db["vendor_bills"].find_one({"bill_number": "FR-1"})
        assert bill["vendor_gstin"] is None
        assert (bill["cgst_total"], bill["sgst_total"], bill["igst_total"]) == (60.0, 60.0, 0.0)
        # Booked before round 12 (when a bill with no valid supplier GSTIN
        # began to be booked no-credit): the credit is still claimed.
        db["vendor_bills"].update_many({}, {"$set": {"itc_eligible": True}})

        db["vendors"].update_one({"vendor_id": "VNO"}, {"$set": {"gstin": SUP_MH}})
        row = _row(_crosscheck(db, "E1"), "Input credit from suppliers with no GSTIN")
        assert (row["status"], row["variance"]) == ("MISMATCH", 120.0), row
        assert "FR-1" in row["note"]
        assert "book the bill again" not in row["note"].lower()


class TestTransferMirrorHeadsAreTheOneRule:
    def test_whenever_both_gstins_resolve_the_mirror_head_is_classify_supply(self):
        """Panel LOW on transfers.py: the mirror takes its head from the two
        shops' states. With both registrations on file the GSTIN prefixes ARE
        those states, so the verdicts agree for every pair of shops; they part
        only when the receiving company holds no registration in the
        receiving shop's state (recipient GSTIN left blank on purpose, so the
        miss is loud -- transfers._entity_gstin_for_state)."""
        import itertools

        from api.routers import transfers as trf
        from api.services.purchase_invoice_engine import classify_supply

        regs = (("E1", "ZZZZZ9999Z1Z9"), ("E2", "YYYYY8888Y1Z8"))
        shops = [
            {"store_id": f"{e}-{s}", "entity_id": e, "state_code": s, "gstin": f"{s}{g}"}
            for e, g in regs
            for s in ("20", "27")
        ]
        entities = [
            {"entity_id": e, "gstins": [{"gstin": f"{s}{g}", "state_code": s} for s in ("20", "27")]}
            for e, g in regs
        ]
        saved = trf._get_db
        try:
            booked = 0
            for a, b in itertools.permutations(shops, 2):
                db = mongomock.MongoClient().db
                db["stores"].insert_many([dict(s) for s in shops])
                db["entities"].insert_many([dict(e) for e in entities])
                trf._get_db = lambda db=db: db
                trf._book_mirror_purchase({
                    "id": "t", "transfer_number": "T", "total_value": 1000, "items": [],
                    "from_location_id": a["store_id"], "to_location_id": b["store_id"],
                    "completed_at": "2026-05-10T05:00:00",
                })
                bill = db["vendor_bills"].find_one({})
                assert bill, (a["store_id"], b["store_id"])
                booked += 1
                assert bill["vendor_gstin"] and bill["recipient_gstin"]
                verdict = classify_supply(bill["vendor_gstin"], bill["recipient_gstin"])["interstate"]
                assert bill["interstate"] is verdict, (a["store_id"], b["store_id"])
            assert booked == 12
        finally:
            trf._get_db = saved


class TestTheMirrorsCreditIsTheOneHelpers:
    """Round 13 #2: the mirror booked itc_eligible True as a literal while the
    reader asks itc_claimable(vendor_gstin). A sending shop with no
    registration has no GSTIN to claim against."""

    @staticmethod
    def _world():
        return _mongo(
            [
                ({"entity_id": "E1", "name": "BVOPL",
                  "gstins": [{"gstin": BUY_JH, "state_code": "20", "is_primary": True}]},
                 [{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
                  {"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": None}]),
                ({"entity_id": "E2", "name": "WizOpt",
                  "gstins": [{"gstin": BUY_MH, "state_code": "27", "is_primary": True}]},
                 [{"store_id": "S2", "entity_id": "E2", "state_code": "27", "gstin": BUY_MH}]),
            ]
        )

    @staticmethod
    def _mirror(db, src, dst):
        from api.routers import transfers as trf

        saved = trf._get_db
        try:
            trf._get_db = lambda: db
            trf._book_mirror_purchase({
                "id": "t", "transfer_number": "T", "total_value": 1000, "items": [],
                "from_location_id": src, "to_location_id": dst,
                "completed_at": "2026-05-10T05:00:00",
            })
        finally:
            trf._get_db = saved
        return db["vendor_bills"].find_one({}, {"_id": 0})

    def test_a_sender_with_no_registration_gives_no_credit_and_the_row_clears(self):
        db = self._world()
        bill = self._mirror(db, "PUNE", "S1")
        assert bill["vendor_gstin"] == "" and bill["itc_eligible"] is False, bill
        xc = _crosscheck(db, "E1")
        assert _row(xc, "Input credit from suppliers with no GSTIN")["status"] == "MATCH"
        assert xc["gstr3b"]["itc"]["total"] == 0.0

    def test_a_registered_sender_keeps_the_credit(self):
        db = self._world()
        bill = self._mirror(db, "S2", "S1")
        assert bill["vendor_gstin"] == BUY_MH and bill["itc_eligible"] is True, bill
        assert _crosscheck(db, "E1")["gstr3b"]["itc"]["total"] == bill["tax_amount"] > 0

    def test_a_credit_denied_transfer_is_named_on_the_cross_check(self):
        """Round 15 #3: the verdict (no credit) is right, but the screen must
        say it: one INFO row naming the transfer, the sender and the CA check."""
        db = self._world()
        bill = self._mirror(db, "PUNE", "S1")
        xc = _crosscheck(db, "E1")
        row = _row(xc, "Transfers with no input credit")
        assert row["status"] == "INFO", row  # not a mismatch: the verdict is correct
        assert row["sources"] == {"Credit denied": bill["tax_amount"]} and bill["tax_amount"] > 0
        assert bill["bill_number"] in row["note"]
        assert "sender has no valid GSTIN: no input credit" in row["note"]
        assert "check whether outward tax applies with your CA" in row["note"]
        assert row["note"].startswith("Transfer from ")
        assert xc["summary"]["all_matched"] is True or xc["summary"]["mismatch_count"] == 0

    def test_a_registered_sender_gets_no_such_note(self):
        db = self._world()
        self._mirror(db, "S2", "S1")
        xc = _crosscheck(db, "E1")
        assert not [c for c in xc["comparisons"] if c["metric"] == "Transfers with no input credit"]

    @pytest.mark.parametrize("junk", ["URP", "NA", "00AAAAA0000A1Z5"])
    def test_round16_a_junk_sender_gstin_gives_no_credit_and_is_listed(self, junk):
        """Round 15 #7 (booking door): a sender whose GSTIN is present but not
        a real one ('URP', 'NA', a bad state code) is no registered person, so
        the mirror books itc_eligible False and the Cross-Check lists it.
        Fails if transfers.py asks bool(from_gstin) instead of itc_claimable."""
        db = _mongo(
            [
                ({"entity_id": "E1", "name": "BVOPL",
                  "gstins": [{"gstin": BUY_JH, "state_code": "20", "is_primary": True}]},
                 [{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH}]),
                ({"entity_id": "E2", "name": "WizOpt",
                  "gstins": [{"gstin": junk, "state_code": "27", "is_primary": True}]},
                 [{"store_id": "S2", "entity_id": "E2", "state_code": "27", "gstin": junk}]),
            ]
        )
        bill = self._mirror(db, "S2", "S1")
        assert bill["vendor_gstin"] == junk, bill
        assert bill["itc_eligible"] is False, bill
        row = _row(_crosscheck(db, "E1"), "Transfers with no input credit")
        assert row["sources"] == {"Credit denied": bill["tax_amount"]} and bill["tax_amount"] > 0


def _denied_world(extra_bills=(), shops_entity="E1"):
    db = _mongo(
        [
            ({"entity_id": "E1", "name": "BVOPL",
              "gstins": [{"gstin": BUY_JH, "state_code": "20", "is_primary": True}]},
             [{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH}]),
            ({"entity_id": "E2", "name": "WizOpt",
              "gstins": [{"gstin": BUY_MH, "state_code": "27", "is_primary": True}]},
             [{"store_id": "S2", "entity_id": "E2", "state_code": "27", "gstin": BUY_MH}]),
        ]
    )
    db["vendor_bills"].insert_many([dict(b) for b in extra_bills])
    return db


def _denied_bill(**over):
    bill = {"bill_id": "d1", "bill_number": "TRF/D-1", "source_transfer_id": "TD1",
            "status": "OUTSTANDING", "itc_eligible": False, "vendor_gstin": "",
            "vendor_name": "Pune shop", "recipient_entity_id": "E1",
            "bill_date": "2026-05-10", "invoice_date": "2026-05-10",
            "taxable_amount": 1000, "tax_amount": 50, "cgst_total": 25.0,
            "sgst_total": 25.0, "igst_total": 0.0}
    bill.update(over)
    return bill


class TestRound16DeniedTransfersQuery:
    """Round 15 #6 and #7: each filter of the denied-transfers query is pinned.
    The row is absent unless the bill is a denied mirror of this company, in
    this month, still live, whose sender really has no valid GSTIN."""

    METRIC = "Transfers with no input credit"

    def _has_row(self, db, entity="E1", month=5):
        xc = _crosscheck(db, entity, month=month)
        return [c for c in xc["comparisons"] if c["metric"] == self.METRIC]

    def test_the_plain_denied_mirror_is_listed(self):
        rows = self._has_row(_denied_world([_denied_bill()]))
        assert rows and rows[0]["sources"] == {"Credit denied": 50.0}

    def test_a_legacy_mirror_with_credit_claimed_is_not_denied(self):
        """Empty vendor_gstin but itc_eligible True (booked before round 13):
        the 'suppliers with no GSTIN' row owns it. Fails without the
        itc_eligible False filter."""
        assert not self._has_row(_denied_world([_denied_bill(itc_eligible=True)]))

    def test_another_company_is_not_listed(self):
        """Fails without the recipient_entity_id scope."""
        assert not self._has_row(_denied_world([_denied_bill(recipient_entity_id="E2")]))

    def test_another_month_is_not_listed(self):
        """Fails without the month window."""
        db = _denied_world([_denied_bill(bill_date="2026-06-10", invoice_date="2026-06-10")])
        assert not self._has_row(db, month=5)
        assert self._has_row(db, month=6)

    @pytest.mark.parametrize("dead", ["CANCELLED", "cancelled", "VOID", "voided"])
    def test_a_cancelled_bill_is_not_listed(self, dead):
        """Fails without the dead-status exclusion."""
        assert not self._has_row(_denied_world([_denied_bill(status=dead)]))

    def test_a_valid_sender_with_credit_switched_off_is_not_listed(self):
        """itc_eligible False but a registered sender: the denial was the
        user's, not the missing registration. Fails without the itc_claimable
        skip inside the loop."""
        assert not self._has_row(_denied_world([_denied_bill(vendor_gstin=BUY_MH)]))

    @pytest.mark.parametrize("junk", ["URP", "NA", "00AAAAA0000A1Z5"])
    def test_a_junk_sender_gstin_is_listed(self, junk):
        """Round 15 #7 (reader door). Fails if the loop asks bool(vendor_gstin)."""
        rows = self._has_row(_denied_world([_denied_bill(vendor_gstin=junk)]))
        assert rows and rows[0]["sources"] == {"Credit denied": 50.0}

    def test_the_note_caps_at_twenty_and_counts_the_rest(self):
        """Round 15 #3: 25 denied mirrors sum to 1250.00 but only 20 are named;
        the note says '(+5 more)'. A bill with no number reads '-', not None."""
        bills = [
            _denied_bill(bill_id=f"d{i}", bill_number=f"TRF/D-{i}", source_transfer_id=f"TD{i}")
            for i in range(24)
        ] + [_denied_bill(bill_id=None, bill_number=None, source_transfer_id="TDX")]
        row = self._has_row(_denied_world(bills))[0]
        assert row["sources"] == {"Credit denied": 1250.0}
        assert row["note"].count("Transfer from ") == 20
        assert row["note"].endswith("(+5 more)")
        small = self._has_row(_denied_world([_denied_bill(bill_id=None, bill_number=None)]))[0]
        assert "(bill -," in small["note"] and "None" not in small["note"]
        assert "more)" not in small["note"]


# ===========================================================================
# Panel round 4 -- a bill's date, the form's shop, the debit note's head,
# the list, and a company with no GST number
# ===========================================================================


def _cash_flow_bill(**over):
    body = {"bill_number": "FR-9", "bill_date": "2026-05-09", "taxable_amount": 1000,
            "tax_amount": 180, "total_amount": 1180, "bill_kind": "SERVICES"}
    body.update(over)
    return body


class TestABillIsDatedOrRefused:
    """HIGH: a bill dated '' or '09/05/2026' booked 201 on both doors, fell
    outside every GSTR-3B month window and outside the check that is meant to
    catch credit left off the returns (register 330, GSTR-3B 150, MATCH)."""

    BAD = ("", "  ", "09/05/2026", "2026-5-9", "2026-02-30", "20260509", "2026-05-09T10:00:00")

    @pytest.mark.parametrize("bad", BAD)
    def test_the_screen_refuses_a_date_no_return_can_place(self, bad):
        db = _FakeDB()
        r = _app(db).post(_URL, json=_services(invoice_date=bad))
        assert r.status_code == 422, r.text
        assert db.collections["vendor_bills"] == []

    @pytest.mark.parametrize("bad", BAD)
    def test_the_cash_flow_door_refuses_it_too(self, bad):
        db, cli = TestEveryDoorEveryReader()._world()
        r = _door(cli, "V1", **_cash_flow_bill(bill_date=bad))
        assert r.status_code == 422, r.text
        assert db["vendor_bills"].count_documents({}) == 0

    def test_a_real_date_books_with_its_due_date(self):
        r = _app(_FakeDB()).post(_URL, json=_services(invoice_date=" 2026-05-03 "))
        assert r.status_code == 201, r.text
        assert (r.json()["invoice_date"], r.json()["due_date"]) == ("2026-05-03", "2026-06-02")

    def test_the_screen_door_honours_the_period_lock(self):
        """The line-detail door never checked the lock on the bill's own date
        (only the DC door checked its earliest DC)."""
        db, cli = TestEveryDoorEveryReader()._world()
        db["period_locks"].insert_one({"month": 5, "year": 2026})
        r = cli.post(_URL, json=_services(invoice_date="2026-05-03"))
        assert r.status_code == 423, r.text
        r = _door(cli, "V1", **_cash_flow_bill())
        assert r.status_code == 423, r.text
        assert db["vendor_bills"].count_documents({}) == 0

    def test_an_undated_bill_already_booked_turns_the_check_red(self):
        """Bills booked before this fix: '' (r3's own blank), a dd/mm/yyyy date,
        and no date field at all. No month window places them, so they are on
        no return -- the check now names them in every month until fixed. A
        properly dated June bill stays out of May's check."""
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        heads = {"vendor_id": "V1", "taxable_amount": 1000, "tax_amount": 180, "cgst_total": 0.0,
                 "sgst_total": 0.0, "igst_total": 180.0, "recipient_entity_id": "E1",
                 "recipient_gstin": BUY_JH, "status": "OUTSTANDING"}
        db["vendor_bills"].insert_many(
            [
                {**heads, "bill_id": "u1", "bill_number": "FR-9", "bill_date": "", "invoice_date": ""},
                {**heads, "bill_id": "u2", "bill_number": "FR-10", "bill_date": "09/05/2026"},
                {**heads, "bill_id": "u3", "bill_number": "FR-11"},
                {**heads, "bill_id": "j1", "bill_number": "JUN-1", "bill_date": "2026-06-02"},
            ]
        )
        for entity in (None, "E1"):
            xc = _crosscheck(db, entity)
            row = _row(xc, "Input credit left off GSTR-3B")
            assert row["status"] == "MISMATCH" and row["variance"] == 540.0, row
            assert all(n in row["note"] for n in ("FR-9", "FR-10", "FR-11"))
            assert "JUN-1" not in row["note"]
            assert xc["gstr3b"]["itc"]["total"] == 0.0


class TestTheFormsShopDecidesPreviewAndBooking:
    """LOW-MEDIUM: the form sends store_id, the schema dropped it, and both
    calls read the shop from the token -- so a shop switched in another tab
    between the preview and Book stored a different GSTIN and tax head."""

    def _world(self):
        db = _FakeDB()
        db.collections["entities"][0]["gstins"].append({"gstin": BUY_MH, "state_code": "27"})
        db.collections["stores"] = [
            {"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
            {"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": BUY_MH},
        ]
        return db

    @staticmethod
    def _as(cli, active):
        async def _u():
            return {"user_id": "u1", "roles": ["ACCOUNTANT"], "store_ids": ["S1", "PUNE"],
                    "active_store_id": active}

        cli.app.dependency_overrides[get_current_user] = _u

    def test_a_shop_switched_between_preview_and_book_changes_nothing(self):
        db = self._world()
        cli = _app(db)
        body = _services(store_id="S1", lines=[{"description": "Freight", "qty": 3, "unit_price": 333.37, "gst_rate": 12}])
        self._as(cli, "S1")
        pv = cli.post(f"{_URL}/preview", json=body)
        assert pv.status_code == 200, pv.text
        self._as(cli, "PUNE")  # the other tab switched shop
        doc = cli.post(_URL, json=body)
        assert doc.status_code == 201, doc.text
        pv, doc = pv.json(), doc.json()
        _same_split(pv, doc)
        assert doc["recipient_gstin"] == BUY_JH and doc["interstate"] is True
        assert doc["igst_total"] == 120.01

    def test_a_shop_the_user_cannot_act_for_is_refused(self):
        db = self._world()
        cli = _app(db)  # store_ids ["S1"]
        for url in (f"{_URL}/preview", _URL):
            r = cli.post(url, json=_services(store_id="PUNE"))
            assert r.status_code == 403, r.text
        assert db.collections["vendor_bills"] == []


class TestACompanyWithNoGstNumberFailsLoud:
    """MEDIUM: E1's company master lists no GSTIN; its shops S1 (20...) and
    PUNE (27...) do. The bill booked with recipient_gstin None as CGST 90 +
    SGST 90, and GSTR-3B then placed it on BOTH shops' returns (360 claimed
    for 180 of tax)."""

    def test_every_door_refuses_and_names_the_fix(self):
        db, cli = TestEveryDoorEveryReader()._world()
        db["entities"].update_one({"entity_id": "E1"}, {"$set": {"gstins": []}})
        db["stores"].insert_one({"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": BUY_MH})
        for r in (
            cli.post(f"{_URL}/preview", json=_services()),
            cli.post(_URL, json=_services()),
            _door(cli, "V1", **_cash_flow_bill()),
        ):
            assert r.status_code == 422, r.text
            detail = r.json()["detail"]
            assert detail["code"] == "RECIPIENT_COMPANY_HAS_NO_GSTIN"
            assert "Better Vision" in detail["message"]
        assert db["vendor_bills"].count_documents({}) == 0


class TestTheDebitNoteReversesTheBillsHead:
    """LOW-MEDIUM: the RTV debit note decided the head from the vendor's
    TYPED state; the bill from its GSTIN only (none = intra). An unregistered
    vendor typed as Maharashtra returning goods from S1 reversed IGST 120 on
    a bill booked CGST + SGST."""

    SELLER = {"entity_id": "E1", "name": "Better Vision", "gstin": BUY_JH, "state_code": "20"}
    LINE = [{"description": "Frame", "qty": 1, "unit_cost": 1000, "gst_rate": 12}]

    @pytest.mark.parametrize(
        "vendor",
        [
            {"vendor_id": "VN", "name": "Local Fitter", "state_code": "27", "address": {"state_code": "27"}},
            {"vendor_id": "V1", "name": "Mumbai Lens House", "gstin": SUP_MH, "state_code": "27"},
            {"vendor_id": "V2", "name": "Ranchi Optics", "gstin": SUP_JH, "state_code": "27"},
        ],
    )
    def test_the_note_and_the_bill_take_one_head(self, vendor):
        from api.services.purchase_invoice_engine import classify_supply
        from api.services.rtv_debit_note import build_debit_note

        note = build_debit_note({"store_id": "S1"}, vendor, self.LINE, "DN/1", seller=self.SELLER)
        bill = classify_supply(vendor.get("gstin"), BUY_JH)
        assert note["is_inter_state"] is bill["interstate"], vendor
        t = note["totals"]
        assert t["tax_paise"] == 12000
        assert (t["igst_paise"] == 12000) is bill["interstate"]

    @pytest.mark.parametrize("shop_gstin", [None, BUY_JH, BUY_MH])
    def test_the_notes_gstin_is_the_one_the_bill_was_received_on(self, shop_gstin):
        """A shop with no GSTIN on its record: the bill receives on the
        company's primary (IGST from a Maharashtra supplier); the note read
        the shop's blank and reversed CGST + SGST. A shop carrying the
        company's Maharashtra number: both take that number (intra)."""
        from api.routers.rtv_debit_notes import _load_seller
        from api.services.purchase_invoice_engine import classify_supply
        from api.services.rtv_debit_note import build_debit_note

        db = _FakeDB()
        db.collections["entities"][0]["gstins"] = [
            {"gstin": BUY_JH, "state_code": "20", "is_primary": True},
            {"gstin": BUY_MH, "state_code": "27"},
        ]
        db.collections["stores"] = [{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": shop_gstin}]
        recipient = pi_router._bill_recipient(db, "S1", None)["recipient_gstin"]
        vendor = {"vendor_id": "V1", "name": "Mumbai Lens House", "gstin": SUP_MH}
        note = build_debit_note({"store_id": "S1"}, vendor, self.LINE, "DN/1", seller=_load_seller(db, "S1", None))
        assert note["seller"]["gstin"] == recipient == (shop_gstin or BUY_JH)
        assert note["is_inter_state"] is classify_supply(SUP_MH, recipient)["interstate"] is (shop_gstin != BUY_MH)


class TestTheGrnDraftNamesTheCatalogueProduct:
    def test_a_grn_draft_takes_the_name_and_hsn_its_po_lacks(self):
        """The from-GRN draft calling the bare lines_from_grn (no catalogue
        fallback) survived every suite; only the DC door pinned it."""
        db = _FakeDB()
        db.collections["products"] = [{"product_id": "P1", "name": "Carrera CA 8895 807", "hsn_code": "9003"}]
        grn = {"grn_id": "G1", "po_id": "PO1", "vendor_id": "V1", "store_id": "S1", "status": "ACCEPTED",
               "items": [{"product_id": "P1", "accepted_qty": 2}]}
        po = {"po_id": "PO1", "items": [{"product_id": "P1", "unit_price": 1000.0, "tax_rate": 5.0}]}
        cli = _app(db)
        pi_router.get_grn_repository = lambda: _StubRepo(grn)
        pi_router.get_purchase_order_repository = lambda: _StubRepo(po)
        r = cli.get(f"{_URL}/from-grn/G1")
        assert r.status_code == 200, r.text
        (ln,) = r.json()["lines"]
        assert (ln["description"], ln["hsn"], ln["qty"]) == ("Carrera CA 8895 807", "9003", 2)


# ===========================================================================
# Panel round 5 -- reverse charge lands with its credit; a bill's year is real
# ===========================================================================


def _bvopl(pune_first=True):
    """BVOPL (E1) holding 20... (S1, Jharkhand) and 27... (PUNE, Maharashtra)."""
    shops = [
        {"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": BUY_MH},
        {"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
    ]
    return _mongo(
        [
            (
                {
                    "entity_id": "E1",
                    "name": "BVOPL",
                    "gstins": [
                        {"gstin": BUY_JH, "state_code": "20", "is_primary": True},
                        {"gstin": BUY_MH, "state_code": "27"},
                    ],
                },
                shops if pune_first else shops[::-1],
            )
        ]
    )


class TestReverseChargeLandsWithItsCredit:
    """MEDIUM: Table 3.1(d) was still placed by COMPANY while Table 4 moved to
    the GSTIN. A reverse-charge bill received on PUNE's 27... (CGST 25 + SGST
    25) charged Rs 50 of reverse charge on the Jharkhand return (S1, cash CGST
    25 / SGST 25, ITC 0) -- a Maharashtra bill paid on the wrong registration
    with nothing to offset it."""

    ZERO = {"integratedTax": 0.0, "centralTax": 0.0, "stateTax": 0.0, "cess": 0.0}
    HALVES = {"integratedTax": 0.0, "centralTax": 25.0, "stateTax": 25.0, "cess": 0.0}

    @pytest.mark.parametrize("pune_first", (True, False))
    def test_the_liability_and_the_credit_are_on_one_return(self, pune_first):
        db = _bvopl(pune_first)
        cli = _app(db)
        TestTheFormsShopDecidesPreviewAndBooking._as(cli, "S1")
        r = cli.post(
            _URL,
            json=_services(
                store_id="PUNE",
                reverse_charge=True,
                lines=[{"description": "Freight", "qty": 1, "unit_price": 1000, "gst_rate": 5}],
            ),
        )
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["recipient_gstin"] == BUY_MH
        assert (doc["cgst_total"], doc["sgst_total"], doc["igst_total"]) == (25.0, 25.0, 0.0)

        reports._get_raw_db = lambda: db
        jh = reports._compute_gstr3b("2026-05", "S1")
        mh = reports._compute_gstr3b("2026-05", "PUNE")
        for key in ("inwardSuppliesReverseCharge", "taxPaidCash", "itcAvailable"):
            assert jh[key] == self.ZERO, (key, jh[key])
            assert mh[key] == self.HALVES, (key, mh[key])
        assert (jh["inwardSuppliesReverseChargeValue"], mh["inwardSuppliesReverseChargeValue"]) == (0.0, 1000.0)

        # A legacy reverse-charge bill naming no GSTIN is company-wide, like
        # its credit: on both returns, counted ONCE by the Cross-Check.
        db["vendor_bills"].insert_one(
            {"bill_id": "old", "bill_number": "OLD-1", "vendor_id": "V1", "bill_date": "2026-05-04",
             "invoice_date": "2026-05-04", "reverse_charge": True, "recipient_entity_id": "E1",
             "recipient_gstin": None, "taxable_amount": 200.0, "tax_amount": 20.0,
             "cgst_total": 10.0, "sgst_total": 10.0, "igst_total": 0.0, "status": "OUTSTANDING"}
        )
        assert reports._compute_gstr3b("2026-05", "S1")["inwardSuppliesReverseCharge"]["centralTax"] == 10.0
        xc = _crosscheck(db, "E1")["gstr3b"]
        assert xc["rcm"] == {"taxableValue": 1200.0, "cgst": 35.0, "sgst": 35.0, "igst": 0.0, "total": 70.0}
        assert xc["itc"]["total"] == 70.0


class TestABillsYearIsReal:
    """MEDIUM: the one date rule took any calendar date from 0001 to 9999, so
    '0202-05-09' (a half-typed year) or '2062-05-09' (swapped digits) booked
    201 on both doors; May's Cross-Check read GSTR-3B ITC 0 and 'credit left
    off' MATCH while the register carried Rs 360 under period 0202-05."""

    @staticmethod
    def _tomorrow():
        from datetime import timedelta

        from api.utils.ist import ist_today

        return (ist_today() + timedelta(days=1)).isoformat()

    BAD = ("0202-05-09", "2062-05-09", "1999-05-09", "2101-05-09", "2017-06-30")

    @pytest.mark.parametrize("bad", BAD + ("tomorrow",))
    def test_both_doors_refuse_a_date_outside_gst_and_today(self, bad):
        bad = self._tomorrow() if bad == "tomorrow" else bad
        db, cli = TestEveryDoorEveryReader()._world()
        for r in (cli.post(_URL, json=_services(invoice_date=bad)), _door(cli, "V1", **_cash_flow_bill(bill_date=bad))):
            assert r.status_code == 422, r.text
            assert "1 July 2017" in r.text
        assert db["vendor_bills"].count_documents({}) == 0

    def test_the_first_day_of_gst_and_today_book(self):
        from api.utils.ist import ist_today

        db, cli = TestEveryDoorEveryReader()._world()
        for n, day in enumerate(("2017-07-01", ist_today().isoformat())):
            r = cli.post(_URL, json=_services(invoice_number=f"FR-{n}", invoice_date=day))
            assert r.status_code == 201, r.text
            assert r.json()["invoice_date"] == day

    def test_a_bill_already_booked_with_an_impossible_year_turns_the_check_red(self):
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        heads = {"vendor_id": "V1", "taxable_amount": 1000, "tax_amount": 180, "cgst_total": 0.0,
                 "sgst_total": 0.0, "igst_total": 180.0, "recipient_entity_id": "E1",
                 "recipient_gstin": BUY_JH, "status": "OUTSTANDING"}
        db["vendor_bills"].insert_many(
            [
                {**heads, "bill_id": "y1", "bill_number": "Y-0202", "bill_date": "0202-05-09", "invoice_date": "0202-05-09"},
                {**heads, "bill_id": "y2", "bill_number": "Y-2062", "bill_date": "2062-05-09", "invoice_date": "2062-05-09"},
                {**heads, "bill_id": "ok", "bill_number": "MAY-1", "bill_date": "2026-05-09", "invoice_date": "2026-05-09"},
            ]
        )
        for entity in (None, "E1"):
            xc = _crosscheck(db, entity)
            row = _row(xc, "Input credit left off GSTR-3B")
            assert row["status"] == "MISMATCH" and row["variance"] == 360.0, row
            assert "Y-0202" in row["note"] and "Y-2062" in row["note"] and "MAY-1" not in row["note"]
            assert xc["gstr3b"]["itc"]["total"] == 180.0



# ===========================================================================
# Panel round 7 -- ONE answer to "which GSTIN is this shop's" for every door
# (owner, 2026-09-30: the registration decides the state), a day that is a
# day, the credit nobody may claim, and a match verdict nobody invented
# ===========================================================================


def _one_company(stores, gstins=(BUY_JH,)):
    """E1 Better Vision holding `gstins` (the first is primary), with `stores`."""
    regs = [{"gstin": g, "state_code": g[:2], "is_primary": i == 0} for i, g in enumerate(gstins)]
    return _mongo([({"entity_id": "E1", "name": "Better Vision", "gstins": regs}, stores)])


def _po_store_doc(db, store_id):
    """The shop as every purchase-order door sees it (vendors.po_gst_context)."""
    import api.routers.vendors.gst as po_gst

    saved = (po_gst.get_store_repository, po_gst._get_db)
    try:
        po_gst.get_store_repository = lambda: _Repo(list(db["stores"].find({}, {"_id": 0})), "store_id")
        po_gst._get_db = lambda: db
        return po_gst.po_gst_context(store_id, None)[1]
    finally:
        po_gst.get_store_repository, po_gst._get_db = saved


class TestOneShopGstinForEveryDoor:
    def test_a_gstin_less_shops_bill_is_on_its_gstr3b(self):
        """r7 #1: S1 has NO GSTIN on its record (created before its company
        had one). The bill booked IGST 180 on 20... (the recipient read the
        company), GSTR-3B read the shop's blank stores.gstin and dropped the
        bill, and the Cross-Check showed ITC 0 beside a Rs 180 MISMATCH."""
        db = _one_company([{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": None}])
        r = _app_as(db, "S1").post(_URL, json=_services())
        assert r.status_code == 201, r.text
        assert (r.json()["recipient_gstin"], r.json()["igst_total"]) == (BUY_JH, 180.0)
        reports._get_raw_db = lambda: db
        assert reports._compute_gstr3b("2026-05", "S1")["itcAvailable"]["integratedTax"] == 180.0
        assert _register(db)["total_itc"] == 180.0
        xc = _crosscheck(db, "E1")
        assert xc["gstr3b"]["itc"]["total"] == 180.0
        assert _row(xc, "Input credit left off GSTR-3B")["status"] == "MATCH"

    def test_a_pune_shop_without_a_gstin_orders_and_bills_on_its_states_number(self):
        """r7 #9: no GSTIN on the Pune shop's record, the company holds 20
        (primary) and 27. The bill fell back to the primary -- IGST on the
        Jharkhand number while its order said CGST + SGST."""
        db = _one_company([{"store_id": "PUNE", "entity_id": "E1", "state_code": "27"}], (BUY_JH, BUY_MH))
        r = _app_as(db, "PUNE").post(_URL, json=_services(store_id="PUNE"))
        assert r.status_code == 201, r.text
        assert (r.json()["recipient_gstin"], r.json()["interstate"]) == (BUY_MH, False)
        store_doc = _po_store_doc(db, "PUNE")
        assert store_doc["gstin"] == BUY_MH
        vendor = db["vendors"].find_one({"vendor_id": "V1"}, {"_id": 0})
        assert _po_gst_parties(vendor, store_doc)["interstate"] is False

    def test_a_shop_whose_company_has_no_number_for_its_state_is_refused(self):
        """Fail loud: a Maharashtra shop of a company registered only in
        Jharkhand. The bill door refuses (never the Jharkhand primary), the
        order names no GSTIN, and a new shop is not stamped with one."""
        from api.routers import stores as stores_router

        db = _one_company([{"store_id": "NSK", "entity_id": "E1", "state_code": "27", "gstin": None}])
        r = _app_as(db, "NSK").post(_URL, json=_services(store_id="NSK"))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "RECIPIENT_SHOP_HAS_NO_GSTIN"
        assert "Maharashtra" in r.json()["detail"]["message"]
        assert db["vendor_bills"].count_documents({}) == 0
        assert _po_store_doc(db, "NSK")["gstin"] == ""
        assert stores_router._derive_store_gstin(db, "E1", "27") is None
        assert stores_router._derive_store_gstin(db, "E1", "20") == BUY_JH

    @staticmethod
    def _two_company_world():
        """E1 holds only 20...: S1 (JH) and PUNE, which declares Maharashtra
        but carries E1's 20... number. E2 WizOpt: S2 (MH) on 27..."""
        return _mongo(
            [
                (
                    {"entity_id": "E1", "name": "Better Vision",
                     "gstins": [{"gstin": BUY_JH, "state_code": "20", "is_primary": True}]},
                    [{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
                     {"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": BUY_JH}],
                ),
                (
                    {"entity_id": "E2", "name": "WizOpt",
                     "gstins": [{"gstin": BUY_MH, "state_code": "27", "is_primary": True}]},
                    [{"store_id": "S2", "entity_id": "E2", "state_code": "27", "gstin": BUY_MH}],
                ),
            ]
        )

    def test_a_transfer_to_a_shop_follows_its_registration(self):
        """r7 #8: the mirror bill took its head from the shops' DECLARED
        states. WizOpt's Maharashtra shop sends PUNE (declared Maharashtra,
        trading on E1's Jharkhand number) Rs 1000: IGST on 20..., the head
        PUNE's bills and orders take -- it was CGST + SGST with no recipient
        GSTIN. A move between two shops on ONE registration is no supply."""
        from api.routers import transfers as trf

        saved = trf._get_db
        try:
            for src, supply in (("S2", True), ("S1", False)):
                db = self._two_company_world()
                trf._get_db = lambda db=db: db
                trf._book_mirror_purchase({
                    "id": "t", "transfer_number": "T", "total_value": 1000, "items": [],
                    "from_location_id": src, "to_location_id": "PUNE",
                    "completed_at": "2026-05-10T05:00:00",
                })
                bill = db["vendor_bills"].find_one({}, {"_id": 0})
                if not supply:
                    assert bill is None, bill
                    continue
                assert bill["recipient_gstin"] == BUY_JH
                assert bill["interstate"] is True and bill["supply_place_recipient"] == "20"
                assert bill["igst_total"] > 0 and bill["cgst_total"] == 0.0
        finally:
            trf._get_db = saved

    def test_pune_is_ordered_as_it_is_billed(self):
        """r7 #8, the order: PUNE's purchase order read its declared state
        (CGST + SGST from a Maharashtra vendor) while its bill booked IGST on
        the Jharkhand number it trades on."""
        db = self._two_company_world()
        r = _app_as(db, "PUNE").post(_URL, json=_services(store_id="PUNE"))
        assert r.status_code == 201, r.text
        assert (r.json()["recipient_gstin"], r.json()["interstate"]) == (BUY_JH, True)
        vendor = db["vendors"].find_one({"vendor_id": "V1"}, {"_id": 0})
        assert _po_gst_parties(vendor, _po_store_doc(db, "PUNE"))["interstate"] is True

    def test_the_go_live_checklist_names_a_shop_its_gstin_contradicts(self):
        """Flagged, never silently used: PUNE declares Maharashtra on a
        Jharkhand number; W carries a number its company does not hold."""
        from api.routers import stores as stores_router

        db = self._two_company_world()
        db["stores"].insert_one({"store_id": "W", "entity_id": "E2", "state_code": "27", "gstin": BUY_JH})
        saved = stores_router.get_db
        try:
            stores_router.get_db = lambda: db
            out = asyncio.run(stores_router.go_live_checklist(current_user={"roles": ["ADMIN"]}))
        finally:
            stores_router.get_db = saved
        check = next(c for c in out["checks"] if c["key"] == "store_gst_state")
        assert (check["status"], check["count"]) == ("WARN", 2), check
        assert "PUNE: declared Maharashtra" in check["hint"] and "W: its GSTIN" in check["hint"]
        assert "S1:" not in check["hint"] and "S2:" not in check["hint"]


class TestTheCrossCheckCountsEachCompanysSlice:
    def test_a_shared_gstin_is_counted_once_per_company(self):
        """r7 #2: the GSTIN-bound slice was deduped by GSTIN alone. A shop of
        E2 on the same number listed first held E2's slice (0) and silently
        dropped E1's (50) -- the figure depended on store order."""
        from api.services.gst_crosscheck import aggregate_gstr3b

        w = {"itcAvailableGstin": {"integratedTax": 0.0}}
        s1 = {"itcAvailableGstin": {"integratedTax": 50.0}}
        for reps, ents in (([w, s1], ["E2", "E1"]), ([s1, w], ["E1", "E2"])):
            assert aggregate_gstr3b(reps, ents, [BUY_JH, BUY_JH])["itc"]["total"] == 50.0


class TestOnlyARealDayIsDated:
    def test_a_well_formed_date_that_is_no_day_turns_the_check_red(self):
        """r7 #4: the undated check read the SHAPE, so '2026-04-31' (no such
        day) sat between April's and May's windows, on no return and in no
        check. A transfer mirror's full IST timestamp is a real day and
        stays on May's return."""
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        heads = {"vendor_id": "V1", "taxable_amount": 1000, "tax_amount": 50, "cgst_total": 0.0,
                 "sgst_total": 0.0, "igst_total": 50.0, "recipient_entity_id": "E1",
                 "recipient_gstin": BUY_JH, "status": "OUTSTANDING"}
        bad = ("2026-04-31", "2026-02-29", "2026-05-00", "2025-13-05", "2025-06-31")
        db["vendor_bills"].insert_many(
            [{**heads, "bill_id": f"x{i}", "bill_number": f"X-{d}", "bill_date": d, "invoice_date": d}
             for i, d in enumerate(bad)]
            + [
                {**heads, "bill_id": "apr", "bill_number": "APR-1", "bill_date": "2026-04-30",
                 "invoice_date": "2026-04-30"},
                {**heads, "bill_id": "m1", "bill_number": "TRF/T1", "source_transfer_id": "T1",
                 "to_store_id": "S1", "bill_date": "2026-05-10T09:30:00.123456",
                 "invoice_date": "2026-05-10T09:30:00.123456"},
            ]
        )
        xc = _crosscheck(db, "E1")
        row = _row(xc, "Input credit left off GSTR-3B")
        assert row["status"] == "MISMATCH" and row["variance"] == 250.0, row
        assert all(f"X-{d}" in row["note"] for d in bad)
        assert "APR-1" not in row["note"] and "TRF/T1" not in row["note"]
        assert xc["gstr3b"]["itc"]["total"] == 50.0

    def test_a_non_day_is_on_no_months_return_and_flagged_in_every_month(self):
        """r10 #1: placement compared the raw string, so '2026-04-31' sat
        inside April's window -- April's GSTR-3B claimed it and April's check
        read green, while every other month flagged it as on no return. One
        real-calendar parse now decides both: on no month's return, flagged
        in every month, the month it sorts into included. '2017-06-30' is a
        real day before GST: the same rule, the same answer."""
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        heads = {"vendor_id": "V1", "taxable_amount": 1000, "tax_amount": 50, "cgst_total": 0.0,
                 "sgst_total": 0.0, "igst_total": 50.0, "recipient_entity_id": "E1",
                 "recipient_gstin": BUY_JH, "status": "OUTSTANDING"}
        bad = ("2026-04-31", "2026-02-29", "2026-05-00", "2025-13-05", "2025-06-31", "2017-06-30")
        db["vendor_bills"].insert_many(
            [{**heads, "bill_id": f"x{i}", "bill_number": f"X-{d}", "bill_date": d, "invoice_date": d}
             for i, d in enumerate(bad)]
            + [{**heads, "bill_id": "apr", "bill_number": "APR-1", "bill_date": "2026-04-30",
                "invoice_date": "2026-04-30"},
               # Its invoice date is a real May day: on May's return, flagged nowhere.
               {**heads, "bill_id": "mix", "bill_number": "MIX-1", "bill_date": "2026-04-31",
                "invoice_date": "2026-05-12"}]
        )
        # Each month a string range put one of them on.
        months = ((2017, 6, 0.0), (2025, 6, 0.0), (2025, 12, 0.0), (2026, 2, 0.0), (2026, 4, 50.0), (2026, 5, 50.0))
        for year, month, real in months:
            xc = _crosscheck(db, "E1", month, year)
            row = _row(xc, "Input credit left off GSTR-3B")
            assert (row["status"], row["variance"]) == ("MISMATCH", 300.0), (year, month, row)
            assert all(f"X-{d}" in row["note"] for d in bad), (year, month)
            assert "APR-1" not in row["note"] and "MIX-1" not in row["note"], (year, month)
            assert xc["gstr3b"]["itc"]["total"] == real, (year, month, xc["gstr3b"]["itc"])


class TestTheUnplacedCheckKeepsItsRules:
    def test_credit_nobody_may_claim_is_not_missing_credit(self):
        """r7 #5: a bill booked itc_eligible False would be listed as credit
        left off GSTR-3B every month -- credit nobody may claim."""
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        db["vendor_bills"].insert_one(
            {"bill_id": "blk", "bill_number": "BLK-1", "vendor_id": "V1", "bill_date": "2026-05-05",
             "invoice_date": "2026-05-05", "taxable_amount": 1000, "tax_amount": 50, "cgst_total": 0.0,
             "sgst_total": 0.0, "igst_total": 50.0, "recipient_entity_id": "E1",
             "recipient_gstin": BUY_JH, "itc_eligible": False, "status": "OUTSTANDING"}
        )
        xc = _crosscheck(db, "E1")
        assert _row(xc, "Input credit left off GSTR-3B")["status"] == "MATCH"
        assert xc["gstr3b"]["itc"]["total"] == 0.0

    def test_a_shop_with_no_company_places_nothing(self):
        """r7 #6: a company-less shop's placement has no company filter, so it
        marked every GSTIN-less bill of every company as placed while the
        Cross-Check counts none of its credit -- the F40 bill (no company,
        tax 50) read MATCH beside GSTR-3B ITC 0."""
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        db["stores"].insert_one({"store_id": "X"})
        db["vendor_bills"].insert_one(
            {"bill_id": "nc", "bill_number": "NC-1", "vendor_id": "V1", "bill_date": "2026-05-05",
             "invoice_date": "2026-05-05", "taxable_amount": 1000, "tax_amount": 50, "cgst_total": 0.0,
             "sgst_total": 0.0, "igst_total": 50.0, "recipient_entity_id": None, "status": "OUTSTANDING"}
        )
        xc = _crosscheck(db, None)
        row = _row(xc, "Input credit left off GSTR-3B")
        assert row["status"] == "MISMATCH" and "NC-1" in row["note"], row
        assert xc["gstr3b"]["itc"]["total"] == 0.0

    def test_a_cancelled_bill_is_not_missing_credit(self):
        """r10 #3: placement skips a dead bill, so without the unplaced
        check's own dead-bill filter every cancelled or void bill would read
        as credit left off GSTR-3B -- red for credit nobody may claim."""
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        db["vendor_bills"].insert_many(
            [{"bill_id": f"d{i}", "bill_number": f"DEAD-{st}", "vendor_id": "V1", "bill_date": "2026-05-05",
              "invoice_date": "2026-05-05", "taxable_amount": 1000, "tax_amount": 50, "cgst_total": 0.0,
              "sgst_total": 0.0, "igst_total": 50.0, "recipient_entity_id": "E1",
              "recipient_gstin": BUY_JH, "status": st}
             for i, st in enumerate(("CANCELLED", "cancelled", "VOID", "voided"))]
        )
        xc = _crosscheck(db, "E1")
        assert xc["itc_unplaced"]["count"] == 0, xc["itc_unplaced"]
        assert _row(xc, "Input credit left off GSTR-3B")["status"] == "MATCH"
        assert xc["gstr3b"]["itc"]["total"] == 0.0

    def test_any_credit_off_every_return_is_a_mismatch(self):
        """r10 #2: the row compared to zero within the Rs 1 rounding
        tolerance, so up to Rs 1.00 of credit on no return read MATCH and did
        not block sign-off. Nothing is rounded here: any amount is a break."""
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        db["vendor_bills"].insert_one(
            {"bill_id": "t1", "bill_number": "TINY-1", "vendor_id": "V1", "bill_date": "",
             "taxable_amount": 18, "tax_amount": 0.90, "cgst_total": 0.0, "sgst_total": 0.0,
             "igst_total": 0.90, "recipient_entity_id": "E1", "recipient_gstin": BUY_JH,
             "status": "OUTSTANDING"}
        )
        xc = _crosscheck(db, "E1")
        row = _row(xc, "Input credit left off GSTR-3B")
        assert (row["status"], row["variance"]) == ("MISMATCH", 0.9), row
        assert "Input credit left off GSTR-3B" in xc["summary"]["mismatch_metrics"]

    def test_any_credit_claimed_from_an_unregistered_supplier_is_a_mismatch(self):
        """r10 #2, the sibling row: the same Rs 1 tolerance let Rs 0.90 of
        credit from a supplier with no GSTIN read MATCH."""
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        db["vendors"].insert_one({"vendor_id": "VN", "trade_name": "Local Fitter", "credit_days": 0})
        db["vendor_bills"].insert_one(
            {"bill_id": "t2", "bill_number": "TINY-2", "vendor_id": "VN", "bill_date": "2026-05-05",
             "invoice_date": "2026-05-05", "taxable_amount": 18, "tax_amount": 0.90, "cgst_total": 0.0,
             "sgst_total": 0.0, "igst_total": 0.90, "recipient_entity_id": "E1",
             "recipient_gstin": BUY_JH, "status": "OUTSTANDING"}
        )
        row = _row(_crosscheck(db, "E1"), "Input credit from suppliers with no GSTIN")
        assert (row["status"], row["variance"]) == ("MISMATCH", 0.9), row


class TestAHeaderOnlyBillHasNoInventedVerdict:
    def test_a_cash_flow_bill_is_not_on_hold_and_shows_its_receipt(self):
        """r7 #7: the list now shows Cash Flow '+ bill' bills. GET /match
        recomputed a verdict from their EMPTY lines -- 'On hold' with an
        Approve button approve-exception refused (400) -- and the row read
        'Manual' because the door stores the receipt's grn_id alone."""
        db, cli = TestEveryDoorEveryReader()._world()
        db["grns"].insert_one({"grn_id": "GA", "grn_number": "RCPT/BV/26-27/0001"})
        r = _door(cli, "V1", bill_number="G-1", bill_date="2026-05-09", taxable_amount=1000,
                  tax_amount=50, total_amount=1050, bill_kind="GOODS", grn_id="GA")
        assert r.status_code == 201, r.text
        bill_id = r.json()["bill_id"]
        m = cli.get(f"{_URL}/{bill_id}/match").json()
        assert (m["match_status"], m["match_detail"]) == (None, None), m
        row = next(x for x in cli.get(_URL).json()["purchase_invoices"] if x["bill_id"] == bill_id)
        assert row["grn_number"] == "RCPT/BV/26-27/0001"

    def test_a_stored_verdict_keeps_its_word(self):
        """A bill whose stored detail was dropped: the recomputed detail
        explains the STORED verdict and never replaces it."""
        db, cli = TestEveryDoorEveryReader()._world()
        a = _book_from_grn(cli, "GA", "A-1")
        db["vendor_bills"].update_one(
            {"bill_id": a["bill_id"]}, {"$set": {"match_status": "MATCHED_OVERRIDE", "match_detail": None}}
        )
        m = cli.get(f"{_URL}/{a['bill_id']}/match").json()
        assert m["match_status"] == "MATCHED_OVERRIDE"
        assert m["match_detail"] and m["match_detail"]["match_status"] == "MATCHED_OVERRIDE"


# ===========================================================================
# Panel round 8 -- the debit note's GSTIN, the Cross-Check's shop GSTINs, a
# mirror no return can file, the month's last second, the check's company
# ===========================================================================


class TestTheDebitNoteHasOurGstinOrIsRefused:
    @pytest.mark.parametrize(
        "entity, shop",
        [
            ({"gstins": []}, {"store_id": "DHN", "state_code": "20"}),
            ({"gstins": [{"gstin": BUY_JH, "state_code": "20", "is_primary": True}]},
             {"store_id": "NSK", "state_code": "27"}),
            ({"gstin": BUY_JH}, {"store_id": "S1", "state_code": "20", "gstin": BUY_JH}),
        ],
        ids=["company-holds-none", "none-for-the-shops-state", "legacy-top-level-number"],
    )
    def test_no_gstin_no_note(self, entity, shop):
        """r8 #1: every bill door refuses a shop with no GSTIN of its company
        (RECIPIENT_*_HAS_NO_GSTIN), yet POST /rtv-debit-notes/issue answered
        201 with seller.gstin '' and CGST 60 + SGST 60 -- a statutory note
        naming no GSTIN. A company's top-level (primary) number was its
        fallback too, which the bill door never reads."""
        from fastapi import HTTPException

        import api.routers.rtv_debit_notes as dn_router

        sid = shop["store_id"]
        db = _mongo([({"entity_id": "E1", "name": "Better Vision", **entity}, [{**shop, "entity_id": "E1"}])])
        r = _app_as(db, sid).post(_URL, json=_services(store_id=sid))
        assert r.status_code == 422, r.text
        db["vendor_returns"].insert_one(
            {"return_id": "VR-1", "store_id": sid, "vendor_id": "V1", "entity_id": "E1",
             "lines": [{"product_id": "P1", "product_name": "Frame", "hsn": "9003", "quantity": 1,
                        "rate_paise": 100000, "gst_rate": 12.0}]}
        )
        user = {"user_id": "u1", "roles": ["ADMIN"], "store_ids": [sid], "active_store_id": sid}
        saved = dn_router._get_db
        try:
            dn_router._get_db = lambda: db
            with pytest.raises(HTTPException) as ei:
                asyncio.run(dn_router.issue_debit_note(
                    dn_router.DebitNoteIssue(source_type="vendor_return", rtv_id="VR-1"), current_user=user
                ))
        finally:
            dn_router._get_db = saved
        assert ei.value.status_code == 422
        assert ei.value.detail["error"] == "seller_has_no_gstin"
        assert sid in ei.value.detail["message"]
        assert db["debit_notes"].count_documents({}) == 0


class TestRoundEightReaders:
    def test_the_crosscheck_reads_a_gstin_less_shops_registration(self):
        """r8 #2: the Cross-Check's switch to the shop-GSTIN answer was pinned
        by no test. S3 (Jharkhand, no GSTIN on its record) files on E1's
        20... like S1; keyed on its raw blank it was counted again as a
        GSTIN-less shop and the Rs 180 bill read ITC 360."""
        db = _one_company([
            {"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
            {"store_id": "S3", "entity_id": "E1", "state_code": "20", "gstin": None},
        ])
        r = _app_as(db, "S1").post(_URL, json=_services())
        assert r.status_code == 201, r.text
        assert r.json()["igst_total"] == 180.0
        assert _crosscheck(db, "E1")["gstr3b"]["itc"]["total"] == 180.0

    def test_a_transfer_into_a_shop_with_no_gstin_is_credit_left_off(self):
        """r8 #3: PUNE (Maharashtra) holds no GSTIN of E1 (Jharkhand only).
        The mirror of S1 -> PUNE (IGST 50, no recipient GSTIN) was placed on
        PUNE by to_store_id alone: the Cross-Check counted ITC 50 and 'left
        off' read MATCH, while E1's only real return (S1) claims none of it
        and pays that IGST in cash. No return files it, so it is flagged."""
        from api.routers import transfers as trf
        from api.routers.reports import _itc_from_vendor_bills

        db = _one_company([
            {"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
            {"store_id": "PUNE", "entity_id": "E1", "state_code": "27"},
        ])
        saved = trf._get_db
        try:
            trf._get_db = lambda: db
            trf._book_mirror_purchase({
                "id": "t", "transfer_number": "T7", "total_value": 1000, "items": [],
                "from_location_id": "S1", "to_location_id": "PUNE",
                "completed_at": "2026-05-10T05:00:00",
            })
        finally:
            trf._get_db = saved
        bill = db["vendor_bills"].find_one({}, {"_id": 0})
        # r11: PUNE has no registration, so the one tax-head rule sees no
        # recipient state and books CGST + SGST; the head is not guessed from
        # PUNE's declared Maharashtra. The bill is still on no return.
        assert (bill["recipient_gstin"], bill["interstate"], bill["igst_total"]) == ("", False, 0.0)
        assert _itc_from_vendor_bills(db, "PUNE", 2026, 5, 31) == (0.0, 0.0, 0.0)
        xc = _crosscheck(db, "E1")
        assert xc["gstr3b"]["itc"]["total"] == 0.0
        row = _row(xc, "Input credit left off GSTR-3B")
        assert row["status"] == "MISMATCH" and "TRF/T7" in row["note"], row

    def test_a_mirror_in_the_months_last_second_is_on_its_return(self):
        """r8 #4: the month ended at 'T23:59:59', and a mirror's IST timestamp
        carries microseconds: '2026-05-31T23:59:59.412000' sorted after it,
        so it was on no GSTR-3B (nor the sender's outward) and the check
        skipped it as dated. June's first instant stays June's."""
        from api.routers.reports import _transfer_outward_bills

        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        mirror = {"source_transfer_id": "T9", "from_store_id": "SX", "to_store_id": "S1", "vendor_id": "E2",
                  "taxable_amount": 1000, "tax_amount": 50, "cgst_total": 0.0, "sgst_total": 0.0,
                  "igst_total": 50.0, "recipient_entity_id": "E1", "recipient_gstin": BUY_JH,
                  "status": "OUTSTANDING"}
        db["vendor_bills"].insert_many([
            {**mirror, "bill_id": "m9", "bill_number": "TRF/T9",
             "bill_date": "2026-05-31T23:59:59.412000", "invoice_date": "2026-05-31T23:59:59.412000"},
            {**mirror, "bill_id": "j1", "bill_number": "TRF/J1", "source_transfer_id": "J1",
             "bill_date": "2026-06-01", "invoice_date": "2026-06-01"},
        ])
        xc = _crosscheck(db, "E1")
        assert xc["gstr3b"]["itc"]["total"] == 50.0
        assert _row(xc, "Input credit left off GSTR-3B")["status"] == "MATCH"
        assert [b["bill_id"] for b in _transfer_outward_bills(db, "SX", 2026, 5, 31)] == ["m9"]
        assert [b["bill_id"] for b in _transfer_outward_bills(db, "SX", 2026, 6, 30)] == ["j1"]
        assert reports._compute_gstr3b("2026-06", "S1")["itcAvailable"]["integratedTax"] == 50.0

    def test_the_check_lists_only_its_own_companys_bills(self):
        """r8 #5: the check's company scope was pinned by no test. Without it
        E1's Cross-Check listed E2's header-only bill as E1's credit left off
        GSTR-3B, with E2's tax and bill number."""
        db = TestOneShopGstinForEveryDoor._two_company_world()
        db["vendor_bills"].insert_one(
            {"bill_id": "w1", "bill_number": "WZ-1", "vendor_id": "V1", "bill_date": "2026-05-06",
             "invoice_date": "2026-05-06", "taxable_amount": 1000, "tax_amount": 50,
             "recipient_entity_id": "E2", "recipient_gstin": BUY_MH, "status": "OUTSTANDING"}
        )
        assert _row(_crosscheck(db, "E1"), "Input credit left off GSTR-3B")["status"] == "MATCH"
        row = _row(_crosscheck(db, "E2"), "Input credit left off GSTR-3B")
        assert row["status"] == "MISMATCH" and "WZ-1" in row["note"], row


# ===========================================================================
# Round 11 -- the no-GSTIN note, the mirror's head, the sole-company guess
# and the legacy badge
# ===========================================================================


def _mirror(db, src, dst, number="TR-9"):
    from api.routers import transfers as trf

    saved = trf._get_db
    try:
        trf._get_db = lambda: db
        trf._book_mirror_purchase({
            "id": "t" + number, "transfer_number": number, "total_value": 1000, "items": [],
            "from_location_id": src, "to_location_id": dst,
            "completed_at": "2026-05-10T05:00:00",
        })
    finally:
        trf._get_db = saved
    return db["vendor_bills"].find_one({"source_transfer_id": "t" + number}, {"_id": 0})


class TestTheNoGstinNoteNamesOnlyWhatTheAppCanDo:
    NOTE = "Input credit from suppliers with no GSTIN"

    def test_a_booked_bills_note_never_says_to_book_it_again(self):
        """r11 #1: rebooking the same vendor + invoice number is a 409, and a
        second vendor record books the credit twice. The note says so."""
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        db["vendors"].insert_one({"vendor_id": "VN", "trade_name": "Local Fitter", "credit_days": 0})
        r = _app(db).post(_URL, json=_services(vendor_id="VN", invoice_number="VN-1"))
        assert r.status_code == 201, r.text
        # Booked before round 12 (when a bill with no valid supplier GSTIN
        # began to be booked no-credit): the credit is still claimed.
        db["vendor_bills"].update_many({}, {"$set": {"itc_eligible": True}})
        note = _row(_crosscheck(db, "E1"), self.NOTE)["note"]
        low = note.lower()
        assert "VN-1" in note and "stock-transfer" not in low
        assert "do not book it again" in low and "twice" in low
        assert "book the bill again" not in low and "mark it as no" not in low
        assert "developer" not in low and "corrected" not in low
        assert "on the gst portal, leave this credit out of table 4" in low

    def test_a_transfer_mirror_gets_its_own_truthful_text(self):
        """r11 #1: a mirror (made by SYSTEM, source_transfer_id set) from a
        sender with no registration. Its head was decided from the two shops'
        GST numbers, not 'without the supplier's state', and nothing can
        rebook it."""
        db = _one_company(
            [{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH}]
        )
        db["entities"].insert_one(
            {"entity_id": "E2", "name": "WizOpt", "gstins": [{"gstin": BUY_MH, "state_code": "27"}]}
        )
        db["stores"].insert_one({"store_id": "S9", "entity_id": "E2", "state_code": "29"})
        assert _mirror(db, "S9", "S1")["vendor_gstin"] == ""
        # A mirror made before round 14 (it booked credit as a literal True).
        db["vendor_bills"].update_many({}, {"$set": {"itc_eligible": True}})
        row = _row(_crosscheck(db, "E1"), self.NOTE)
        assert row["status"] == "MISMATCH", row
        low = row["note"].lower()
        assert "TRF/TR-9" in row["note"] and "stock-transfer" in low
        assert "without the supplier's state" not in low
        assert "book the bill again" not in low and "mark it as no" not in low
        assert "cannot be booked again" in low


class TestTheMirrorHeadIsTheOneRule:
    def _world(self):
        return _one_company(
            [{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
             {"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": None}]
        )

    def test_a_shop_with_no_registration_gets_no_head_from_its_declared_state(self):
        """r11 #2, the reviewer's case: E1 holds only 20...; PUNE has no GSTIN
        and declares 27. PUNE -> S1 of 1000 was IGST 50 by the declared state;
        classify_supply('', 20...) is CGST + SGST."""
        from api.services.purchase_invoice_engine import classify_supply

        db = self._world()
        bill = _mirror(db, "PUNE", "S1")
        assert (bill["vendor_gstin"], bill["recipient_gstin"]) == ("", BUY_JH)
        verdict = classify_supply(bill["vendor_gstin"], bill["recipient_gstin"])["interstate"]
        assert bill["interstate"] is verdict is False
        assert (bill["cgst_total"], bill["sgst_total"], bill["igst_total"]) == (25.0, 25.0, 0.0)
        assert bill["place_of_supply"] == ""
        # Round 14: a sender with no registration gives no credit, so the row clears.
        assert bill["itc_eligible"] is False
        row = _row(_crosscheck(db, "E1"), "Input credit from suppliers with no GSTIN")
        assert row["status"] == "MATCH", row

    def test_the_reverse_direction_is_flagged_as_unplaced(self):
        """S1 -> PUNE: PUNE has no registration, so the mirror is on no return
        and the Cross-Check says so, whatever PUNE declares."""
        db = self._world()
        bill = _mirror(db, "S1", "PUNE")
        assert (bill["recipient_gstin"], bill["igst_total"]) == ("", 0.0)
        xc = _crosscheck(db, "E1")
        row = _row(xc, "Input credit left off GSTR-3B")
        assert row["status"] == "MISMATCH" and "TRF/TR-9" in row["note"], row


class TestNoSoleCompanyGuess:
    def test_a_shop_with_no_company_is_refused_even_with_one_company(self):
        """r11 #3: with exactly one company in the master, a shop with no
        entity_id was given that company's registration (IGST 180 on 20...),
        while GSTR-3B, the Cross-Check and the RTV note all read it as having
        none. The bill door now agrees: refused, nothing booked."""
        db = _one_company([{"store_id": "S9", "state_code": "20"}])
        r = _app_as(db, "S9").post(_URL, json=_services(store_id="S9"))
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "RECIPIENT_UNRESOLVED"
        assert "S9" in detail["message"] and "no company" in detail["message"]
        assert db["vendor_bills"].count_documents({}) == 0
        from api.services import org_validation as ov

        assert ov.shop_gstins(db)["S9"] == ""


# ===========================================================================
# Round 12 -- recipient authority, fail-loud, no credit without a valid
# supplier GSTIN, honest notes, one splitter, the mirror's junk-prefix head
# ===========================================================================

from fastapi import HTTPException  # noqa: E402


def _pune_world():
    """E1 holds 20... (S1) and 27... (PUNE); BLR is a third shop, user-less."""
    return _one_company(
        [
            {"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
            {"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": BUY_MH},
        ],
        gstins=(BUY_JH, BUY_MH),
    )


def _refused(fn, *a, **kw):
    with pytest.raises(HTTPException) as e:
        fn(*a, **kw)
    return e.value


class TestRound12RecipientAuthority:
    def test_item1_a_receipt_shop_is_never_moved_by_a_typed_gstin(self):
        """#1: goods received at PUNE (27...), the company's OTHER number (20...)
        typed -> 422, never IGST on the Jharkhand return."""
        db = _pune_world()
        err = _refused(pi_router._bill_recipient, db, "PUNE", BUY_JH)
        assert err.status_code == 422
        assert err.detail["code"] == "RECIPIENT_GSTIN_NOT_RECEIPT_SHOP"
        assert BUY_MH in err.detail["message"] and "PUNE" in err.detail["message"]
        for typed in (None, "", BUY_MH, BUY_MH.lower()):
            r = pi_router._bill_recipient(db, "PUNE", typed)
            assert r["recipient_gstin"] == BUY_MH

    def test_item1_on_the_booking_and_the_preview(self):
        """#1 through the doors: the same 422 on POST / and POST /preview."""
        db = _pune_world()
        db["vendors"].insert_one({"vendor_id": "V1", "trade_name": "M", "gstin": SUP_MH})
        db["grns"].insert_one(
            {"grn_id": "GP", "vendor_id": "V1", "store_id": "PUNE", "status": "ACCEPTED",
             "items": [{"product_id": "P1", "accepted_qty": 1}]}
        )
        cli = _app_as(db, "S1")
        pi_router.get_grn_repository = lambda: _Repo(list(db["grns"].find({}, {"_id": 0})), "grn_id")

        async def _u():
            return {"user_id": "u1", "roles": ["ACCOUNTANT"], "store_ids": ["S1", "PUNE"],
                    "active_store_id": "S1"}

        cli.app.dependency_overrides[get_current_user] = _u
        body = {"vendor_id": "V1", "grn_id": "GP", "recipient_gstin": BUY_JH,
                "lines": [{"description": "x", "qty": 1, "unit_price": 1000, "gst_rate": 5, "product_id": "P1"}]}
        for url in (f"{_URL}/preview", _URL):
            r = cli.post(url, json={**body, "invoice_number": "X-1", "invoice_date": "2026-05-03"})
            assert r.status_code == 422, (url, r.text)
            assert r.json()["detail"]["code"] == "RECIPIENT_GSTIN_NOT_RECEIPT_SHOP"

    def test_item8_a_typed_gstin_needs_a_shop_of_its_company(self):
        """#8: S1-only caller types WizOpt's number -> 403; a caller with a
        WizOpt shop, and an admin, are let through."""
        db = _two_companies()
        s1 = {"roles": ["ACCOUNTANT"], "store_ids": ["S1"], "active_store_id": "S1"}
        err = _refused(pi_router._bill_recipient, db, None, BUY_MH, "S1", current_user=s1)
        assert err.status_code == 403
        both = {"roles": ["ACCOUNTANT"], "store_ids": ["S1", "S2"], "active_store_id": "S1"}
        r = pi_router._bill_recipient(db, None, BUY_MH, "S1", current_user=both)
        assert (r["recipient_entity_id"], r["recipient_gstin"]) == ("E2", BUY_MH)
        adm = {"roles": ["ADMIN"], "store_ids": [], "active_store_id": None}
        assert pi_router._bill_recipient(db, None, BUY_MH, "S1", current_user=adm)["recipient_entity_id"] == "E2"
        own = pi_router._bill_recipient(db, None, BUY_JH, "S1", current_user=s1)
        assert own["recipient_entity_id"] == "E1"

    def test_item2_no_company_master_and_no_typed_gstin_is_a_422(self):
        """#2: the recipient GSTIN may never resolve to None, in the helper, the
        preview and the booking alike."""
        db = _FakeDB()
        db.collections["entities"].clear()
        err = _refused(pi_router._bill_recipient, db, None, None, "S1")
        assert err.status_code == 422 and err.detail["code"] == "RECIPIENT_NO_COMPANY_MASTER"
        cli = _app(db)
        for url in (_URL, f"{_URL}/preview"):
            r = cli.post(url, json=_services(invoice_number="NM-1"))
            assert r.status_code == 422, (url, r.text)
            assert r.json()["detail"]["code"] == "RECIPIENT_NO_COMPANY_MASTER"
        assert not db.collections.get("purchase_invoices")
        # A typed GSTIN with no master to check it against is still taken.
        assert pi_router._bill_recipient(db, None, BUY_JH, "S1")["recipient_gstin"] == BUY_JH


class TestRound12NoCreditWithoutAValidSupplierGstin:
    BAD = ("NA", "URP", "N/A", "-", "0", "27", "88AAAAA1111A1Z1", "", "  ")

    def test_the_one_helper(self):
        from api.services.org_validation import has_valid_gstin

        for bad in (*self.BAD, None, 27, "27AAAAA1111A1Z", "27AAAAA1111A1Z12"):
            assert has_valid_gstin(bad) is False, bad
        assert has_valid_gstin(SUP_MH) and has_valid_gstin(" " + SUP_JH.lower() + " ")

    @pytest.mark.parametrize("gstin", BAD)
    def test_every_bad_string_books_no_credit_on_the_screen_door(self, gstin):
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        db["vendors"].insert_one({"vendor_id": "VN", "trade_name": "Local", "gstin": gstin, "credit_days": 0})
        r = _app(db).post(_URL, json=_services(vendor_id="VN", invoice_number="VN-1"))
        assert r.status_code == 201, r.text
        assert r.json()["itc_eligible"] is False
        xc = _crosscheck(db, "E1")
        assert xc["gstr3b"]["itc"]["total"] == 0.0
        assert _register(db)["total_itc"] == 0.0
        assert _row(xc, "Input credit from suppliers with no GSTIN")["status"] == "MATCH"
        assert _row(xc, "Input credit left off GSTR-3B")["status"] == "MATCH"
        pre = _app(db).post(f"{_URL}/preview", json={"vendor_id": "VN", "lines": _services()["lines"]})
        assert pre.json()["itc_eligible"] is False

    @pytest.mark.parametrize("gstin", ("NA", "88AAAAA1111A1Z1", ""))
    def test_the_cash_flow_door_too(self, gstin):
        db, cli = TestEveryDoorEveryReader()._world()
        vno = {"vendor_id": "VNO", "trade_name": "Local", "credit_days": 0, "gstin": gstin}
        db["vendors"].insert_one(dict(vno))
        vend.get_vendor_repository = lambda: _Repo([vno], "vendor_id")
        r = _door(cli, "VNO", **_cash_flow_bill(bill_number="FR-1", tax_amount=120, total_amount=1120))
        assert r.status_code == 201, r.text
        assert db["vendor_bills"].find_one({"bill_number": "FR-1"})["itc_eligible"] is False
        xc = _crosscheck(db, "E1")
        assert xc["gstr3b"]["itc"]["total"] == 0.0
        assert _row(xc, "Input credit from suppliers with no GSTIN")["status"] == "MATCH"

    def test_a_valid_gstin_still_claims_credit(self):
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        r = _app(db).post(_URL, json=_services())
        assert r.status_code == 201, r.text
        assert r.json()["itc_eligible"] is True
        assert _crosscheck(db, "E1")["gstr3b"]["itc"]["total"] == 180.0

    def test_the_reader_judges_a_stored_junk_gstin_with_the_same_helper(self):
        """A bill booked before the rule, vendor_gstin 'NA' with the credit
        claimed, is flagged by the row (it was non-blank, so it read clean)."""
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        db["vendors"].insert_one({"vendor_id": "VN", "trade_name": "Local", "gstin": "NA"})
        assert _app(db).post(_URL, json=_services(vendor_id="VN", invoice_number="J-1")).status_code == 201
        db["vendor_bills"].update_many({}, {"$set": {"itc_eligible": True}})
        row = _row(_crosscheck(db, "E1"), "Input credit from suppliers with no GSTIN")
        assert row["status"] == "MISMATCH" and row["variance"] == 180.0 and "J-1" in row["note"]


class TestRound12NotesNameOnlyRealActions:
    def test_the_left_off_note(self):
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        db["vendor_bills"].insert_one(
            {"bill_id": "b-old", "bill_number": "OLD-1", "vendor_id": "V1", "bill_date": "2026-05-02",
             "taxable_amount": 1000, "tax_amount": 50, "igst_total": 50.0, "cgst_total": 0.0,
             "sgst_total": 0.0, "recipient_entity_id": None, "status": "OUTSTANDING"}
        )
        note = _row(_crosscheck(db, "E1"), "Input credit left off GSTR-3B")["note"]
        assert note.startswith("1 booked bill(s) carry input credit that IMS left off every GSTIN's GSTR-3B")
        assert "IMS cannot edit a booked bill" in note
        assert "on the GST portal, claim it in Table 4 only if your accountant confirms" in note
        assert note.endswith("OLD-1")
        low = note.lower()
        for gone in ("correct those", "corrected", "developer"):
            assert gone not in low

    def test_the_no_gstin_notes(self):
        from api.services.gst_crosscheck import _unregistered_note

        note = _unregistered_note({"bill_numbers": ["A-1", "TRF/T-1"], "transfer_bill_numbers": ["TRF/T-1"]})
        assert "IMS counted this credit in the GSTR-3B figure on this screen" in note
        assert "On the GST portal, leave this credit out of Table 4 of the GSTR-3B you file" in note
        assert "marked no-credit by IMS automatically: A-1" in note
        assert "so IMS set the head from the two shops' GST numbers" in note
        assert "of the GSTR-3B you file: TRF/T-1" in note
        low = note.lower()
        for gone in ("developer", "corrected", "correct those", "have the stored bill"):
            assert gone not in low


class TestRound12OneSplitterAndTheMirrorHead:
    def test_item4_the_aggregate_mirror_uses_gst_rates_split_gst(self, monkeypatch):
        from api.routers import transfers as trf
        from api.services import gst_rates

        monkeypatch.setattr(gst_rates, "split_gst", lambda tax, inter: ("SENTINEL", tax, inter))
        assert trf._tax_split(10.01, False) == ("SENTINEL", 10.01, False)

    @pytest.mark.parametrize("tax", (0.01, 180.01, 0.05, 33.33, 5.01))
    def test_item4_the_values_still_agree(self, tax):
        from api.routers import transfers as trf
        from api.services.gst_rates import split_gst

        for inter in (True, False):
            assert trf._tax_split(tax, inter) == split_gst(tax, inter)

    def test_item9_a_junk_prefix_registration_takes_its_head_from_classify_supply(self):
        """#9: E1 holds 20... and a junk-prefix '88AAAAA1111A1Z1'. classify_supply
        (no state for 88) says CGST + SGST; the old `from_state != to_state`
        said IGST. The mirror reads the one rule."""
        from api.services.purchase_invoice_engine import classify_supply

        junk = "88AAAAA1111A1Z1"
        db = _one_company(
            [
                {"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
                {"store_id": "JUNK", "entity_id": "E1", "state_code": "27", "gstin": junk},
            ],
            gstins=(BUY_JH, junk),
        )
        assert classify_supply(BUY_JH, junk)["interstate"] is False
        bill = _mirror(db, "S1", "JUNK")
        assert (bill["vendor_gstin"], bill["recipient_gstin"]) == (BUY_JH, junk)
        assert bill["interstate"] is False
        assert (bill["cgst_total"], bill["sgst_total"], bill["igst_total"]) == (25.0, 25.0, 0.0)


class TestRound12ThePoComposerShowsTheServersHead:
    def _client(self, db, roles=("ACCOUNTANT",), stores=("S1", "PUNE")):
        import api.routers.vendors.cockpit as cockpit
        import api.routers.vendors.gst as po_gst

        saved = (po_gst.get_store_repository, po_gst._get_db, cockpit._get_db)
        po_gst.get_store_repository = lambda: _Repo(list(db["stores"].find({}, {"_id": 0})), "store_id")
        po_gst._get_db = lambda: db
        cockpit._get_db = lambda: db
        self._restore = lambda: (
            setattr(po_gst, "get_store_repository", saved[0]),
            setattr(po_gst, "_get_db", saved[1]),
            setattr(cockpit, "_get_db", saved[2]),
        )
        app = FastAPI()
        app.include_router(vend.router, prefix="/api/v1/vendors")

        async def _u():
            return {"user_id": "u1", "roles": list(roles), "store_ids": list(stores),
                    "active_store_id": stores[0] if stores else None}

        app.dependency_overrides[get_current_user] = _u
        return TestClient(app)

    def test_heads_come_from_shop_gstin_and_classify_supply(self):
        db = _pune_world()
        db["vendors"].insert_many([
            {"vendor_id": "VJ", "trade_name": "Ranchi", "gstin": SUP_JH},
            {"vendor_id": "VN", "trade_name": "Local"},
            {"vendor_id": "VX", "trade_name": "Junk", "gstin": "88AAAAA1111A1Z1"},
        ])
        try:
            cli = self._client(db)
            pune = cli.get("/api/v1/vendors/po-gst-heads", params={"store_id": "PUNE"}).json()
            s1 = cli.get("/api/v1/vendors/po-gst-heads", params={"store_id": "S1"}).json()
        finally:
            self._restore()
        assert pune["shop_gstin"] == BUY_MH and s1["shop_gstin"] == BUY_JH
        # Maharashtra shop: the MH vendor is CGST+SGST, the JH vendor IGST.
        assert pune["heads"] == {"V1": False, "V2": True, "VJ": True, "VN": None, "VX": None}
        assert s1["heads"]["VJ"] is False and s1["heads"]["VN"] is None and s1["heads"]["VX"] is None

    def test_a_shop_the_company_holds_no_number_for_says_cannot_tell(self):
        """The old composer read raw store.gstin: a stale/blank own GSTIN gave a
        stale head or 'cannot tell'. The server resolves through shop_gstin."""
        db = _one_company(
            [{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": "27STALE0000Z1Z0"},
             {"store_id": "BLR", "entity_id": "E1", "state_code": "29", "gstin": None}],
            gstins=(BUY_JH,),
        )
        db["vendors"].insert_one({"vendor_id": "VJ", "trade_name": "R", "gstin": SUP_JH})
        try:
            cli = self._client(db, stores=("S1", "BLR"))
            s1 = cli.get("/api/v1/vendors/po-gst-heads", params={"store_id": "S1"}).json()
            blr = cli.get("/api/v1/vendors/po-gst-heads", params={"store_id": "BLR"}).json()
        finally:
            self._restore()
        assert s1["shop_gstin"] == BUY_JH and s1["heads"]["VJ"] is False  # stale own number ignored
        assert blr["shop_gstin"] == "" and blr["heads"]["VJ"] is None

    def test_role_and_store_gate(self):
        db = _pune_world()
        try:
            assert self._client(db, roles=("SALES_STAFF",)).get("/api/v1/vendors/po-gst-heads").status_code == 403
            assert self._client(db, stores=("S1",)).get(
                "/api/v1/vendors/po-gst-heads", params={"store_id": "PUNE"}
            ).status_code == 403
        finally:
            self._restore()


class TestRound13OneItcClaimableHelper:
    """Round 13 items 1+2: ONE helper decides claimable credit, so booking,
    /preview and the reader agree. Under reverse charge the recipient pays the
    tax and may claim it even from an unregistered supplier."""

    def _vendor(self, db, gstin):
        db["vendors"].insert_one({"vendor_id": "VN", "trade_name": "Local", "gstin": gstin, "credit_days": 0})

    def test_the_helper_truth_table(self):
        from api.services.org_validation import itc_claimable

        assert itc_claimable(SUP_MH) is True
        assert itc_claimable("NA") is False
        assert itc_claimable("NA", reverse_charge=True) is True
        assert itc_claimable(None, reverse_charge=True) is True
        assert itc_claimable(SUP_MH, user_allows=False) is False
        assert itc_claimable("NA", reverse_charge=True, user_allows=False) is False
        assert itc_claimable(SUP_MH, reverse_charge=True, user_allows=False) is False

    def test_an_rcm_bill_from_an_na_vendor_keeps_its_credit(self):
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        self._vendor(db, "NA")
        r = _app(db).post(_URL, json=_services(vendor_id="VN", invoice_number="RC-1", reverse_charge=True))
        assert r.status_code == 201, r.text
        assert r.json()["itc_eligible"] is True
        xc = _crosscheck(db, "E1")["gstr3b"]
        assert xc["rcm"]["total"] == 180.0 and xc["itc"]["total"] == 180.0
        assert _register(db)["total_itc"] == 180.0
        assert _row(_crosscheck(db, "E1"), "Input credit from suppliers with no GSTIN")["status"] == "MATCH"

    def test_a_non_rcm_bill_from_an_na_vendor_has_none(self):
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        self._vendor(db, "NA")
        r = _app(db).post(_URL, json=_services(vendor_id="VN", invoice_number="NR-1"))
        assert r.status_code == 201, r.text
        assert r.json()["itc_eligible"] is False
        assert _crosscheck(db, "E1")["gstr3b"]["itc"]["total"] == 0.0

    @pytest.mark.parametrize(
        "gstin,rcm,user,want",
        [
            (SUP_MH, False, True, True),
            (SUP_MH, False, False, False),
            ("NA", False, True, False),
            ("NA", True, True, True),
            ("NA", True, False, False),
        ],
    )
    def test_the_preview_says_what_the_booking_stores(self, gstin, rcm, user, want):
        db = TestCreditLeftOffEveryReturnIsFlagged()._world()
        self._vendor(db, gstin)
        extra = dict(vendor_id="VN", reverse_charge=rcm, itc_eligible=user)
        pre = _app(db).post(f"{_URL}/preview", json={"lines": _services()["lines"], **extra})
        assert pre.status_code == 200, pre.text
        r = _app(db).post(_URL, json=_services(invoice_number="PV-1", **extra))
        assert r.status_code == 201, r.text
        assert pre.json()["itc_eligible"] is r.json()["itc_eligible"] is want


class TestRound13NotesNameTheRealScreen:
    def test_the_mirror_note_names_organization_and_table_4(self):
        from api.services.gst_crosscheck import _unregistered_note

        note = _unregistered_note({"bill_numbers": ["TRF/T-1"], "transfer_bill_numbers": ["TRF/T-1"]})
        assert (
            "Add the sending shop's company registration in Organization (left menu) "
            "so later transfers carry one; on the GST portal, leave this credit out "
            "of Table 4 of the GSTR-3B you file: TRF/T-1"
        ) in note
        assert "Settings, companies" not in note

    def test_the_debit_note_refusal_names_organization(self):
        import inspect
        from api.services import rtv_debit_note

        src = inspect.getsource(rtv_debit_note)
        assert "shop's state in Organization (left menu) or correct the shop's state" in src
        assert "Settings, companies" not in src

    def test_the_booking_refusals_name_organization(self):
        db = _FakeDB()
        db.collections["entities"].clear()
        err = _refused(pi_router._bill_recipient, db, None, None, "S1")
        assert "Add the company and its GSTIN in Organization (left menu)" in err.detail["message"]
        assert "Settings, companies" not in err.detail["message"]

    def test_round15_no_dead_settings_stores_screen_is_named(self):
        """Round 15 #4: '(Settings, stores)' and '/settings?tab=stores' name a
        screen that does not exist; the screen is Organization (/organization)."""
        import inspect
        from api.routers import stores as stores_router
        from api.services import rtv_debit_note

        for mod in (pi_router, rtv_debit_note, stores_router):
            src = inspect.getsource(mod)
            assert "Settings, stores" not in src, mod.__name__
            assert "/settings?tab=stores" not in src, mod.__name__
        assert "/organization" in inspect.getsource(stores_router)

    def test_round16_no_file_in_backend_api_names_the_dead_stores_screen(self):
        """Round 15 #1: the guard above read three modules; this one reads every
        .py under backend/api, so a dead name cannot hide in another router."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1] / "api"
        dead = ("(Settings, stores)", "Settings -> Stores", "/settings?tab=stores",
                "Store Setup -> UPI VPA")
        hits = []
        for f in root.rglob("*.py"):
            rel = f.relative_to(root.parent).as_posix()
            text = f.read_text(encoding="utf-8")
            hits += [f"{rel}: {d}" for d in dead if d in text]
        assert not hits, hits
        db = _FakeDB()
        db.collections["entities"].clear()
        err = _refused(pi_router._bill_recipient, db, None, None, "S1")
        assert "Organization (left menu)" in err.detail["message"]


class TestRound13NoRegistrationReceiptShop:
    def test_a_typed_company_gstin_at_a_shop_with_no_registration_is_422(self):
        """A receipt at a shop the company holds no number for, plus a typed
        company GSTIN, is refused -- never booked on the typed number."""
        db = _one_company(
            [{"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
             {"store_id": "BLR", "entity_id": "E1", "state_code": "29", "gstin": None}],
            gstins=(BUY_JH,),
        )
        err = _refused(pi_router._bill_recipient, db, "BLR", BUY_JH)
        assert err.status_code == 422
        assert err.detail["code"] == "RECIPIENT_GSTIN_NOT_RECEIPT_SHOP"
        assert err.detail["message"].startswith(
            f"{BUY_JH} is not the GST number of the shop that received the goods (BLR, which has none)."
        )
