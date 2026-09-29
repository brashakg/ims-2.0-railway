"""Audit F28 (2026-09-29): a refused receipt burned a receipt number.

The GRN number (RCPT/{store}/{FY}/{serial}) is a GST document series and must
run without gaps. _create_grn_impl minted it BEFORE the duplicate-invoice
guard, so a correctly refused duplicate consumed RCPT/.../0002 and the real
receipt came out 0003 (series 0001, 0003, 0004). The same held for the DC
duplicate guard and the tally (untallied lines) refusal.

Contract: every refusal answers before the number is minted.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from api.routers import vendors as v  # noqa: E402
from api.routers.vendors import GRNCreate, GRNItemCreate, create_grn  # noqa: E402
from tests.test_grn_duplicate_guard import _MemGRNRepo, _attach, _user, _wire  # noqa: E402


def _counting_minter(mp):
    minted = []

    def _mint(store):
        minted.append(store)
        return f"RCPT/{store}/26-27/{len(minted):04d}"

    mp.setattr(v, "generate_grn_number", _mint)
    return minted


def _body(store, tallied=True, **kw):
    return GRNCreate(
        po_id=kw.pop("po_id", "PO1"),
        vendor_invoice_no=kw.pop("invoice_no", "JOT/26-27/0451"),
        items=[
            GRNItemCreate(
                product_id="P1",
                received_qty=10,
                accepted_qty=10,
                rejected_qty=0,
                tallied=tallied,
            )
        ],
        **_attach(store),
        **kw,
    )


def _create(grn):
    return asyncio.run(create_grn(grn, current_user=_user()))


def test_refused_duplicate_invoice_takes_no_receipt_number(monkeypatch):
    repo = _MemGRNRepo()
    store = _wire(monkeypatch, repo)
    minted = _counting_minter(monkeypatch)
    first = _create(_body(store))
    assert first["grn_number"].endswith("/0001")
    with pytest.raises(HTTPException) as exc:
        _create(_body(store))
    assert exc.value.status_code == 409
    assert len(minted) == 1  # the refusal burned nothing
    nxt = _create(_body(store, invoice_no="JOT/26-27/0452"))
    assert nxt["grn_number"].endswith("/0002")  # no gap


def test_refused_untallied_receipt_takes_no_receipt_number(monkeypatch):
    repo = _MemGRNRepo()
    store = _wire(monkeypatch, repo)
    minted = _counting_minter(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _create(_body(store, tallied=False))
    assert exc.value.status_code == 422
    assert minted == []


def test_refused_duplicate_challan_takes_no_receipt_number(monkeypatch):
    repo = _MemGRNRepo(
        [
            {
                "grn_id": "DC-1",
                "grn_subtype": "DELIVERY_CHALLAN",
                "vendor_id": "V1",
                "dc_number": "DC-77",
                "store_id": "BV-TEST-01",
            }
        ]
    )
    store = _wire(monkeypatch, repo)
    minted = _counting_minter(monkeypatch)

    class _Db:
        def get_collection(self, name):
            class _C:
                def find_one(self, flt, proj=None):
                    return repo.find_one(flt)

            return _C()

    monkeypatch.setattr(v, "_get_db", lambda: _Db())
    dc = GRNCreate(
        po_id=None,
        vendor_id="V1",
        vendor_invoice_no="",
        grn_subtype="DELIVERY_CHALLAN",
        dc_number="DC-77",
        dc_date="2026-09-17",
        items=[GRNItemCreate(product_id="P1", received_qty=2, accepted_qty=2, rejected_qty=0)],
    )
    with pytest.raises(HTTPException) as exc:
        _create(dc)
    assert exc.value.status_code == 409
    assert minted == []
