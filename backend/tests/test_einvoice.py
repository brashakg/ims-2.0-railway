"""
Tests for backend/api/services/einvoice.py -- FIN-1 GST e-invoice scaffolding.

Coverage:
  1. SIMULATED when IMS_EINVOICE_ENABLED is off (DARK default).
  2. SIMULATED when env is on but no creds configured.
  3. SIMULATED when env is on and creds present but order already has an IRN
     (SKIPPED, idempotent).
  4. JSON builder output shape (mandatory NIC fields present).
  5. Gate function (einvoice_enabled).
  6. _parse_irp_response: NIC InfoDtls shape.
  7. _parse_irp_response: flat GSP wrapper shape.
  8. generate_irn goes LIVE path and returns GENERATED when gate is up
     (IRP call is monkeypatched -- no real network).
  9. generate_irn returns FAILED cleanly when the IRP call raises.
 10. cancel_irn returns SIMULATED with no IRN.

All tests use a fake in-memory db (MockCollection / MockDB). No live network.
Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_einvoice.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

# Keep e-invoice DARK for all tests by default; individual tests opt-in.
os.environ.pop("IMS_EINVOICE_ENABLED", None)

import importlib
import api.services.einvoice as einvoice_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fake DB helpers
# ---------------------------------------------------------------------------


class MockCollection:
    """Minimal in-memory collection stub used by multiple test suites."""

    def __init__(self, docs=None):
        self._docs = list(docs or [])

    def find_one(self, query=None, projection=None):
        # Support simple equality + $or queries (good enough for tests).
        for doc in self._docs:
            if self._matches(doc, query or {}):
                return dict(doc)
        return None

    def update_one(self, query, update):
        class _Result:
            matched_count = 0
        res = _Result()
        for doc in self._docs:
            if self._matches(doc, query or {}):
                set_val = (update or {}).get("$set") or {}
                doc.update(set_val)
                res.matched_count = 1
                break
        return res

    def _matches(self, doc, query):
        for k, v in query.items():
            if k == "$or":
                if not any(self._matches(doc, sub) for sub in v):
                    return False
            elif isinstance(v, dict):
                pass  # skip complex operators in tests (not needed here)
            elif doc.get(k) != v:
                return False
        return True


class MockDB:
    def __init__(self, collections=None):
        self._collections = collections or {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = MockCollection()
        return self._collections[name]


def _make_db_with_creds(gstin="20AAAAA1234A1ZX"):
    """Return a MockDB that has a configured einvoice integration."""
    integrations = MockCollection([
        {
            "type": "einvoice",
            "enabled": True,
            "config": {
                "gstin": gstin,
                "gsp_url": "https://fake-gsp.example.com",
                "username": "testuser",
                "password": "testpass",
            },
        }
    ])
    return MockDB({"integrations": integrations})


def _sample_order(with_irn=False):
    order = {
        "id": "ORD-001",
        "order_number": "INV/2026/001",
        "invoice_number": "INV/2026/001",
        "store_gstin": "20AAAAA1234A1ZX",
        "customer_name": "Test Customer",
        "grand_total": 590.0,
        "taxable_amount": 500.0,
        "cgst_amount": 45.0,
        "sgst_amount": 45.0,
        "igst_amount": 0.0,
        "items": [
            {
                "name": "Spectacle Frame",
                "hsn_code": "9003",
                "quantity": 1,
                "unit_price": 500.0,
                "taxable_amount": 500.0,
                "cgst_amount": 45.0,
                "sgst_amount": 45.0,
                "igst_amount": 0.0,
                "gst_rate": 18.0,
                "total": 590.0,
            }
        ],
    }
    if with_irn:
        order["irn"] = "EXISTING_IRN_ABC123"
        order["ack_no"] = "ACK001"
        order["ack_date"] = "2026-06-01 10:00:00"
        order["einvoice_signed_qr"] = "FAKE_SIGNED_QR_STRING"
    return order


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simulated_when_env_disabled():
    """Gate 1: IMS_EINVOICE_ENABLED not set -> SIMULATED with helpful reason."""
    os.environ.pop("IMS_EINVOICE_ENABLED", None)
    importlib.reload(einvoice_mod)

    db = MockDB()
    result = await einvoice_mod.generate_irn(db, _sample_order())

    assert result["status"] == einvoice_mod.STATUS_SIMULATED
    assert result["irn"] is None
    assert "IMS_EINVOICE_ENABLED" in (result["reason"] or ""), result["reason"]


@pytest.mark.asyncio
async def test_simulated_when_no_creds(monkeypatch):
    """Gate 2: env is on but no integrations doc -> SIMULATED."""
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    importlib.reload(einvoice_mod)

    db = MockDB()  # empty -- no integrations
    result = await einvoice_mod.generate_irn(db, _sample_order())

    assert result["status"] == einvoice_mod.STATUS_SIMULATED
    assert result["irn"] is None
    assert "GSP" in (result["reason"] or "") or "cred" in (result["reason"] or "").lower()


@pytest.mark.asyncio
async def test_skipped_when_irn_exists(monkeypatch):
    """Idempotency: order already has an IRN -> SKIPPED, no IRP call made."""
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    importlib.reload(einvoice_mod)

    db = _make_db_with_creds()
    order = _sample_order(with_irn=True)
    result = await einvoice_mod.generate_irn(db, order)

    assert result["status"] == einvoice_mod.STATUS_SKIPPED
    assert result["irn"] == "EXISTING_IRN_ABC123"
    assert "skip" in (result["reason"] or "").lower()


def test_einvoice_json_shape():
    """The IRP payload builder emits all mandatory NIC fields."""
    order = _sample_order()
    payload = einvoice_mod._build_einvoice_json(order)

    # Top-level mandatory NIC keys
    for key in ("Version", "TranDtls", "DocDtls", "SellerDtls", "BuyerDtls",
                "ItemList", "ValDtls"):
        assert key in payload, f"Missing top-level key: {key}"

    assert payload["Version"] == "1.1"
    assert payload["DocDtls"]["No"] == "INV/2026/001"
    assert len(payload["ItemList"]) == 1

    item = payload["ItemList"][0]
    for field in ("SlNo", "PrdDesc", "HsnCd", "Qty", "UnitPrice", "TotAmt",
                  "AssAmt", "GstRt", "IgstAmt", "CgstAmt", "SgstAmt",
                  "TotItemVal"):
        assert field in item, f"Missing item field: {field}"

    # ValDtls totals
    vd = payload["ValDtls"]
    assert vd["AssVal"] == 500.0
    assert vd["TotInvVal"] == 590.0


def test_einvoice_json_no_items_fallback():
    """When order has no items[], builder falls back to a single consolidated line."""
    order = dict(_sample_order())
    del order["items"]
    payload = einvoice_mod._build_einvoice_json(order)
    assert len(payload["ItemList"]) == 1
    assert payload["ItemList"][0]["SlNo"] == "1"


def test_gate_disabled_by_default():
    """einvoice_enabled returns False when env is off."""
    os.environ.pop("IMS_EINVOICE_ENABLED", None)
    importlib.reload(einvoice_mod)
    db = _make_db_with_creds()
    assert einvoice_mod.einvoice_enabled(db, "20AAAAA1234A1ZX") is False


def test_gate_enabled_with_creds(monkeypatch):
    """einvoice_enabled returns True when env is on and creds present."""
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    importlib.reload(einvoice_mod)
    db = _make_db_with_creds()
    assert einvoice_mod.einvoice_enabled(db, "20AAAAA1234A1ZX") is True


def test_gate_disabled_no_creds(monkeypatch):
    """einvoice_enabled returns False when env is on but no creds."""
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    importlib.reload(einvoice_mod)
    db = MockDB()
    assert einvoice_mod.einvoice_enabled(db, "20AAAAA1234A1ZX") is False


def test_parse_irp_response_nic_shape():
    """_parse_irp_response correctly parses the NIC InfoDtls envelope."""
    body = {
        "Status": "1",
        "InfoDtls": [
            {
                "InfCd": "EINV-GEN",
                "Desc": {
                    "Irn": "IRN_ABC12345",
                    "AckNo": 67890,
                    "AckDt": "2026-06-05 12:00:00",
                    "SignedQRCode": "FAKE_QR_DATA",
                    "SignedInvoice": "FAKE_SIGNED_INV",
                },
            }
        ],
    }
    result = einvoice_mod._parse_irp_response(body)
    assert result["irn"] == "IRN_ABC12345"
    assert result["ack_no"] == "67890"
    assert result["ack_date"] == "2026-06-05 12:00:00"
    assert result["signed_qr"] == "FAKE_QR_DATA"


def test_parse_irp_response_flat_gsp_shape():
    """_parse_irp_response handles a flattened GSP wrapper."""
    body = {
        "data": {
            "Irn": "FLAT_IRN_XYZ",
            "AckNo": 11111,
            "AckDt": "2026-06-05 13:00:00",
            "SignedQRCode": "FLAT_QR",
        }
    }
    result = einvoice_mod._parse_irp_response(body)
    assert result["irn"] == "FLAT_IRN_XYZ"
    assert result["signed_qr"] == "FLAT_QR"


def test_parse_irp_response_empty():
    """_parse_irp_response returns {} on an empty / error body."""
    assert einvoice_mod._parse_irp_response({}) == {}
    assert einvoice_mod._parse_irp_response(None) == {}  # type: ignore[arg-type]
    assert einvoice_mod._parse_irp_response({"Status": "0", "ErrorDetails": []}) == {}


@pytest.mark.asyncio
async def test_generate_irn_live_path_monkeypatched(monkeypatch):
    """When gate is up, generate_irn calls _call_irp and returns GENERATED."""
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    importlib.reload(einvoice_mod)

    fake_irp_response = {
        "Status": "1",
        "InfoDtls": [
            {
                "InfCd": "EINV-GEN",
                "Desc": {
                    "Irn": "LIVE_IRN_789",
                    "AckNo": 99999,
                    "AckDt": "2026-06-05 15:00:00",
                    "SignedQRCode": "LIVE_QR_DATA",
                },
            }
        ],
    }

    async def _fake_call_irp(cfg, payload):
        return fake_irp_response

    monkeypatch.setattr(einvoice_mod, "_call_irp", _fake_call_irp)

    orders_coll = MockCollection([dict(_sample_order())])
    db = _make_db_with_creds()
    db._collections["orders"] = orders_coll

    result = await einvoice_mod.generate_irn(db, _sample_order())

    assert result["status"] == einvoice_mod.STATUS_GENERATED
    assert result["irn"] == "LIVE_IRN_789"
    assert result["ack_no"] == "99999"
    assert result["signed_qr"] == "LIVE_QR_DATA"
    # Verify persisted on the doc
    doc = orders_coll.find_one({"id": "ORD-001"})
    assert doc is not None
    assert doc.get("irn") == "LIVE_IRN_789"
    assert doc.get("einvoice_status") == "GENERATED"


@pytest.mark.asyncio
async def test_generate_irn_irp_network_failure_returns_failed(monkeypatch):
    """When the IRP call raises, generate_irn returns FAILED (never propagates)."""
    monkeypatch.setenv("IMS_EINVOICE_ENABLED", "1")
    importlib.reload(einvoice_mod)

    async def _raise_irp(cfg, payload):
        raise ConnectionError("IRP unreachable")

    monkeypatch.setattr(einvoice_mod, "_call_irp", _raise_irp)

    db = _make_db_with_creds()
    result = await einvoice_mod.generate_irn(db, _sample_order())

    assert result["status"] == einvoice_mod.STATUS_FAILED
    assert result["irn"] is None
    assert "IRP unreachable" in (result["reason"] or "")


@pytest.mark.asyncio
async def test_cancel_irn_no_irn_simulated():
    """cancel_irn returns SIMULATED when the order has no IRN."""
    os.environ.pop("IMS_EINVOICE_ENABLED", None)
    importlib.reload(einvoice_mod)

    db = MockDB()
    result = await einvoice_mod.cancel_irn(db, _sample_order())
    assert result["status"] == einvoice_mod.STATUS_SIMULATED
    assert "No IRN" in (result["reason"] or "")


@pytest.mark.asyncio
async def test_cancel_irn_dark_when_env_off():
    """cancel_irn returns SIMULATED when env gate is off even with an IRN."""
    os.environ.pop("IMS_EINVOICE_ENABLED", None)
    importlib.reload(einvoice_mod)

    db = _make_db_with_creds()
    order = _sample_order(with_irn=True)
    result = await einvoice_mod.cancel_irn(db, order)
    # Either SIMULATED (no IRN check first) or dark (env disabled)
    assert result["status"] == einvoice_mod.STATUS_SIMULATED


def test_fmt_date_conversions():
    """_fmt_date_ddmmyyyy handles ISO and already-formatted inputs."""
    assert einvoice_mod._fmt_date_ddmmyyyy("2026-06-05") == "05/06/2026"
    assert einvoice_mod._fmt_date_ddmmyyyy("2026-06-05T10:30:00") == "05/06/2026"
    # Already in the right format -- pass through
    assert einvoice_mod._fmt_date_ddmmyyyy("05/06/2026") == "05/06/2026"
    # Junk input -> returns today's date (not None, not crash)
    result = einvoice_mod._fmt_date_ddmmyyyy("not-a-date")
    assert "/" in result and len(result) == 10


def test_einvoice_qr_block_no_irn():
    """einvoice_qr_block returns present=False when the order has no IRN."""
    from api.services.print_legal import einvoice_qr_block

    block = einvoice_qr_block({})
    assert block["present"] is False
    assert block["irn"] == ""

    block2 = einvoice_qr_block({"id": "ORD-X"})
    assert block2["present"] is False


def test_einvoice_qr_block_with_irn():
    """einvoice_qr_block returns present=True with irn/ack fields populated."""
    from api.services.print_legal import einvoice_qr_block

    order = {
        "irn": "SOME_IRN",
        "ack_no": "12345",
        "ack_date": "2026-06-05 10:00:00",
        "einvoice_signed_qr": "FAKE_QR_PAYLOAD",
    }
    block = einvoice_qr_block(order)
    assert block["present"] is True
    assert block["irn"] == "SOME_IRN"
    assert block["ack_no"] == "12345"
    # signed_qr_raw should be set when qrcode is absent
    assert block["signed_qr_raw"] == "FAKE_QR_PAYLOAD"
    # qr_data_uri may be None (qrcode not installed in test env) -- that's ok
    # render_note should be present in that case
    if block["qr_data_uri"] is None:
        assert "TODO" in (block["render_note"] or "") or block["render_note"] == ""
