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
        number typed as printed on the bill -> the bill is WizOpt's."""
        r = _app(_two_companies()).post(_URL, json=_services(recipient_gstin=BUY_MH))
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


def _crosscheck(db, entity_id):
    reports._get_raw_db = lambda: db
    return _run_gst_cross_check(db, 5, 2026, entity_id)


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
    def _world(self):
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
        cli = _app(db)  # the accountant sits at S1
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
