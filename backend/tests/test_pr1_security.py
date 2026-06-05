"""Regression tests for PR1 live-QA security remediation.

Covers:
  BUG-139  csv_safe formula-injection neutralizer + UTF-8 BOM
  BUG-088  store-scope helpers (user_store_scope / can_access_store_scoped /
           filter_docs_by_store) that gate prescription reads
  BUG-102  ONDC callbacks fail CLOSED (no signing key => unverified; on_confirm /
           on_cancel NACK an unverified payload)
  BUG-032  notification_service tolerates a pymongo-Database-like handle whose
           bool() raises (the `if db is not None:` fix)
"""

import asyncio
import io
import json

from api.services.csv_safe import neutralize_formula, safe_writer, BOM


# ---------------------------------------------------------------------------
# BUG-139 -- CSV formula-injection neutralizer
# ---------------------------------------------------------------------------
class TestCsvSafe:
    def test_neutralizes_formula_leads(self):
        assert neutralize_formula("=cmd|'/C calc'!A0") == "'=cmd|'/C calc'!A0"
        assert neutralize_formula("+1+1") == "'+1+1"
        assert neutralize_formula("-2+3") == "'-2+3"
        assert neutralize_formula("@SUM(A1)") == "'@SUM(A1)"
        assert neutralize_formula("\t=x") == "'\t=x"
        assert neutralize_formula("\r=x") == "'\r=x"

    def test_leaves_safe_strings_untouched(self):
        assert neutralize_formula("Ray-Ban") == "Ray-Ban"
        assert neutralize_formula("Bokaro") == "Bokaro"
        assert neutralize_formula("") == ""

    def test_numbers_and_none_untouched(self):
        # A numeric cell must NOT be turned into text -- a negative number
        # stays a number, never the string "'-5".
        assert neutralize_formula(-5) == -5
        assert neutralize_formula(3.14) == 3.14
        assert neutralize_formula(None) is None
        assert neutralize_formula(True) is True

    def test_safe_writer_neutralizes_only_string_cells(self):
        buf = io.StringIO()
        w = safe_writer(buf)
        w.writerow(["name", "amount"])
        w.writerow(["=HYPERLINK('http://evil','x')", -5])
        out = buf.getvalue()
        assert "'=HYPERLINK" in out  # formula string neutralized
        assert ",-5" in out          # negative number preserved as a number

    def test_bom_is_utf8(self):
        assert BOM == "﻿"
        assert BOM.encode("utf-8") == b"\xef\xbb\xbf"


# ---------------------------------------------------------------------------
# BUG-088 -- prescription store-scope helpers
# ---------------------------------------------------------------------------
from api.dependencies import (
    user_store_scope,
    can_access_store_scoped,
    filter_docs_by_store,
)


class TestStoreScope:
    def test_admins_are_cross_store(self):
        for role in ("SUPERADMIN", "ADMIN"):
            is_cross, _ = user_store_scope({"roles": [role]})
            assert is_cross is True
            assert can_access_store_scoped("any-store", {"roles": [role]}) is True
            assert can_access_store_scoped(None, {"roles": [role]}) is True

    def test_store_role_bounded_to_own_stores(self):
        user = {
            "roles": ["SALES_STAFF"],
            "store_ids": ["BV-BOK-01"],
            "active_store_id": "BV-BOK-01",
        }
        assert can_access_store_scoped("BV-BOK-01", user) is True
        # the core BUG-088 case: a Bokaro cashier cannot read a Pune Rx
        assert can_access_store_scoped("BV-PUN-01", user) is False
        # unattributed (no store) Rx is not readable by store-level roles
        assert can_access_store_scoped(None, user) is False
        assert can_access_store_scoped("", user) is False

    def test_filter_drops_out_of_scope_and_unattributed(self):
        docs = [
            {"id": 1, "store_id": "BV-BOK-01"},
            {"id": 2, "store_id": "BV-PUN-01"},
            {"id": 3, "store_id": None},
            {"id": 4},
        ]
        cashier = {"roles": ["CASHIER"], "store_ids": ["BV-BOK-01"]}
        kept = filter_docs_by_store(docs, cashier)
        assert [d["id"] for d in kept] == [1]
        # admins see everything
        assert len(filter_docs_by_store(docs, {"roles": ["ADMIN"]})) == 4

    def test_area_manager_scoped_to_region(self):
        am = {"roles": ["AREA_MANAGER"], "store_ids": ["BV-BOK-01", "BV-BOK-02"]}
        assert can_access_store_scoped("BV-BOK-02", am) is True
        assert can_access_store_scoped("BV-PUN-01", am) is False


# ---------------------------------------------------------------------------
# BUG-102 -- ONDC callbacks fail closed
# ---------------------------------------------------------------------------
from api.routers import ondc


class _FakeRequest:
    def __init__(self, body: bytes, headers=None):
        self._body = body
        self.headers = headers or {}

        class _URL:
            path = "/api/v1/ondc/on_confirm"

        self.url = _URL()

    async def body(self):
        return self._body


class TestOndcFailClosed:
    def test_no_signing_key_marks_unverified(self):
        req = _FakeRequest(json.dumps({"context": {}, "message": {}}).encode())
        payload = asyncio.run(ondc._verify_callback(req, None))
        assert payload is not None
        # fail closed: absent UKP => UNVERIFIED, not trusted
        assert payload.get("_signature_invalid") is True

    def test_on_confirm_nacks_forged_request(self):
        body = json.dumps(
            {"context": {"transaction_id": "t1"},
             "message": {"order": {"id": "ATTACK-1"}}}
        ).encode()
        resp = asyncio.run(ondc.on_confirm(_FakeRequest(body)))
        assert resp["message"]["ack"]["status"] == "NACK"

    def test_on_cancel_nacks_forged_request(self):
        body = json.dumps(
            {"context": {}, "message": {"order": {"id": "ATTACK-1"}}}
        ).encode()
        resp = asyncio.run(ondc.on_cancel(_FakeRequest(body)))
        assert resp["message"]["ack"]["status"] == "NACK"


# ---------------------------------------------------------------------------
# BUG-032 -- notification_service tolerates a pymongo-Database-like handle
# ---------------------------------------------------------------------------
class _BoolRaises:
    """Mimics a pymongo Database: bool() raises (truth-testing unsupported)."""

    def __bool__(self):
        raise NotImplementedError(
            "Database objects do not implement truth value testing; "
            "compare with None instead"
        )

    def get_collection(self, name):
        class _Coll:
            def insert_one(self, doc):
                return None

        return _Coll()


def test_send_notification_survives_database_like_handle(monkeypatch):
    from api.services import notification_service as ns

    monkeypatch.setattr(ns, "_get_db", lambda: _BoolRaises())
    # Before the BUG-032 fix, `if db:` called bool() on the Database and raised,
    # breaking ALL outbound comms. With `if db is not None:` it must not raise.
    result = asyncio.run(
        ns.send_notification(
            store_id="BV-BOK-01",
            customer_id="cust-1",
            customer_phone="9999999999",
            customer_name="Test",
            template_id="PRESCRIPTION_EXPIRY",
            variables={"customer_name": "Test"},
        )
    )
    assert result is not None
    assert result.get("dispatched") is False
