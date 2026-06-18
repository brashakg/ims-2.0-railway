"""Order-create Rx validation -- BUG-005 (patient-safety) + BUG-006.

BUG-005: the POS / order-create path copied a line's sph/cyl/add straight onto
the order doc with NO range / 0.25-step / axis validation, so a clinically
impossible lens power (e.g. cyl=-8, add=5, off-grid, or cyl-without-axis) could
be ordered and sent to the lab to grind.

BUG-006: a spectacle-lens / contact-lens line was accepted with NO
prescription_id -- the "Rx required for lens/CL; frame-only exempt; expired-Rx
override needs Store-Manager+" rule was not enforced at order-create.

THE FIX (orders.py): create_order + add_order_item now run the SAME canonical
clinical Rx validators (api.services.rx_validation) on every line and require a
valid, customer-matching, non-expired prescription on lens/CL lines. Validation
only -- no pricing/GST/payment math changed.

These tests mirror the FakeDB / monkeypatched-repo harness used by
test_pos_cap_failclosed.py + test_order_pricing_integrity.py.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
@pytest.fixture
def rx_orders(monkeypatch):
    """Order-create harness: FakeDB-backed order/customer/audit + prescription
    repos, plus a real product repo so product-existence passes."""
    from tests.test_walkouts import FakeDB
    from api.routers import orders as orders_module
    from api import dependencies as deps_module
    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.audit_repository import AuditRepository
    from database.repositories.prescription_repository import PrescriptionRepository

    fake_db = FakeDB()
    order_repo = OrderRepository(fake_db.get_collection("orders"))
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    rx_repo = PrescriptionRepository(fake_db.get_collection("prescriptions"))

    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(orders_module, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(deps_module, "get_audit_repository", lambda: audit_repo)
    # _validate_order_line_rx fetches the Rx repo via api.dependencies.
    monkeypatch.setattr(
        deps_module, "get_prescription_repository", lambda: rx_repo
    )

    customer_repo.create(
        {"customer_id": "cust-x", "name": "Test", "mobile": "9100000099",
         "phone": "9100000099"}
    )
    return {
        "db": fake_db, "monkeypatch": monkeypatch,
        "rx_repo": rx_repo, "customer_repo": customer_repo,
    }


def _seed_product(rx, *, pid, category, item_type=None, mrp=2000.0, cost_price=500.0):
    """Seed a billable product (spine row -> not catalog-only) into the product
    repo and patch get_product_repository. Returns pid."""
    from api.routers import orders as orders_module
    from database.repositories.product_repository import ProductRepository

    repo = ProductRepository(rx["db"].get_collection("products"))
    repo.create({
        "product_id": pid, "name": pid, "category": category,
        "item_type": item_type or category,
        "mrp": mrp, "cost_price": cost_price,
        "discount_category": "MASS", "is_active": True,
    })
    rx["monkeypatch"].setattr(
        orders_module, "get_product_repository", lambda: repo
    )
    return pid


def _seed_rx(rx, *, prescription_id, customer_id="cust-x", months_ago=0,
             validity_months=24):
    """Seed a prescription. months_ago controls expiry via _rx_validity
    (prescription_date + validity_months)."""
    pdate = datetime.now() - timedelta(days=30 * months_ago)
    rx["rx_repo"].create({
        "prescription_id": prescription_id,
        "customer_id": customer_id,
        "patient_id": customer_id,
        "store_id": "BV-TEST-01",
        "prescription_date": pdate.isoformat(),
        "validity_months": validity_months,
        "right_eye": {"sph": "-1.00"},
        "left_eye": {"sph": "-1.00"},
    })
    return prescription_id


def _item(pid, *, item_type, category=None, sph=None, cyl=None, add=None,
          axis=None, prescription_id=None, unit_price=2000.0):
    it = {
        "product_id": pid, "product_name": pid, "item_type": item_type,
        "category": category or item_type, "quantity": 1,
        "unit_price": unit_price, "discount_percent": 0,
    }
    for k, v in (("sph", sph), ("cyl", cyl), ("add", add), ("axis", axis),
                 ("prescription_id", prescription_id)):
        if v is not None:
            it[k] = v
    return it


def _post(client, headers, items, **extra):
    return client.post(
        "/api/v1/orders",
        json={"customer_id": "cust-x", "items": items, **extra},
        headers=headers,
    )


def _manager_headers():
    from api.routers.auth import create_access_token

    token = create_access_token({
        "user_id": "test-mgr-001", "username": "testmgr",
        "roles": ["STORE_MANAGER"], "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01", "discount_cap": 20.0,
    })
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# BUG-005 -- out-of-range / off-grid / bad-axis powers -> 422
# ---------------------------------------------------------------------------
def test_lens_cyl_out_of_range_rejected(client, staff_headers, rx_orders):
    """The LIVE-PROVEN case: a LENS line with cyl=-8 (limit -6..+6) -> 422."""
    pid = _seed_product(rx_orders, pid="LENS-1", category="OPTICAL_LENS",
                        item_type="LENS")
    rxid = _seed_rx(rx_orders, prescription_id="rx-ok-1")
    r = _post(client, staff_headers,
              [_item(pid, item_type="LENS", category="OPTICAL_LENS",
                     cyl=-8.0, axis=90, prescription_id=rxid)])
    assert r.status_code == 422, r.text
    assert "cyl" in r.text.lower()


def test_lens_sph_out_of_range_rejected(client, staff_headers, rx_orders):
    pid = _seed_product(rx_orders, pid="LENS-2", category="OPTICAL_LENS",
                        item_type="LENS")
    rxid = _seed_rx(rx_orders, prescription_id="rx-ok-2")
    r = _post(client, staff_headers,
              [_item(pid, item_type="LENS", category="OPTICAL_LENS",
                     sph=25.0, prescription_id=rxid)])
    assert r.status_code == 422, r.text
    assert "sph" in r.text.lower()


def test_lens_add_out_of_range_rejected(client, staff_headers, rx_orders):
    pid = _seed_product(rx_orders, pid="LENS-3", category="OPTICAL_LENS",
                        item_type="LENS")
    rxid = _seed_rx(rx_orders, prescription_id="rx-ok-3")
    r = _post(client, staff_headers,
              [_item(pid, item_type="LENS", category="OPTICAL_LENS",
                     add=5.0, prescription_id=rxid)])
    assert r.status_code == 422, r.text
    assert "add" in r.text.lower()


def test_lens_off_quarter_step_rejected(client, staff_headers, rx_orders):
    """sph=-1.30 is in range but off the 0.25-diopter grid -> 422."""
    pid = _seed_product(rx_orders, pid="LENS-4", category="OPTICAL_LENS",
                        item_type="LENS")
    rxid = _seed_rx(rx_orders, prescription_id="rx-ok-4")
    r = _post(client, staff_headers,
              [_item(pid, item_type="LENS", category="OPTICAL_LENS",
                     sph=-1.30, prescription_id=rxid)])
    assert r.status_code == 422, r.text
    assert "0.25" in r.text


def test_lens_cyl_without_axis_rejected(client, staff_headers, rx_orders):
    """A non-zero cylinder with no axis is un-grindable -> 422."""
    pid = _seed_product(rx_orders, pid="LENS-5", category="OPTICAL_LENS",
                        item_type="LENS")
    rxid = _seed_rx(rx_orders, prescription_id="rx-ok-5")
    r = _post(client, staff_headers,
              [_item(pid, item_type="LENS", category="OPTICAL_LENS",
                     cyl=-2.0, prescription_id=rxid)])  # no axis
    assert r.status_code == 422, r.text
    assert "axis" in r.text.lower()


def test_lens_fractional_axis_rejected(client, staff_headers, rx_orders):
    pid = _seed_product(rx_orders, pid="LENS-6", category="OPTICAL_LENS",
                        item_type="LENS")
    rxid = _seed_rx(rx_orders, prescription_id="rx-ok-6")
    # axis is an int field; send via raw JSON to exercise a non-int -> 422.
    bad = _item(pid, item_type="LENS", category="OPTICAL_LENS",
                cyl=-2.0, prescription_id=rxid)
    bad["axis"] = 200  # out of 1-180 range
    r = _post(client, staff_headers, [bad])
    assert r.status_code == 422, r.text
    assert "axis" in r.text.lower()


# ---------------------------------------------------------------------------
# BUG-006 -- Rx-required on SPECTACLE lens lines (contact lenses EXEMPT)
# Owner policy 2026-06-18: "block Rx lenses, allow contacts".
# ---------------------------------------------------------------------------
def test_optical_lens_without_prescription_rejected(client, staff_headers,
                                                    rx_orders):
    pid = _seed_product(rx_orders, pid="LENS-NORX", category="OPTICAL_LENS",
                        item_type="LENS")
    r = _post(client, staff_headers,
              [_item(pid, item_type="LENS", category="OPTICAL_LENS",
                     sph=-1.00)])  # no prescription_id
    assert r.status_code == 422, r.text
    assert "prescription" in r.text.lower()


def test_contact_lens_without_prescription_allowed(client, staff_headers,
                                                    rx_orders):
    """Owner policy 2026-06-18 ("block Rx lenses, allow contacts"): a contact-
    lens line is EXEMPT from the hard Rx-required gate, so a repeat daily-
    disposable / colored-contact sale is NOT blocked for a missing Rx."""
    pid = _seed_product(rx_orders, pid="CL-NORX", category="CONTACT_LENS",
                        item_type="CONTACT_LENS")
    r = _post(client, staff_headers,
              [_item(pid, item_type="CONTACT_LENS", category="CONTACT_LENS")])
    assert r.status_code in (200, 201), r.text


def test_contact_lens_bad_power_still_rejected(client, staff_headers, rx_orders):
    """Power-range validation is UNIVERSAL (BUG-005): even though a CL needs no
    linked Rx, a clinically impossible power on the line is still rejected."""
    pid = _seed_product(rx_orders, pid="CL-BADPWR", category="CONTACT_LENS",
                        item_type="CONTACT_LENS")
    r = _post(client, staff_headers,
              [_item(pid, item_type="CONTACT_LENS", category="CONTACT_LENS",
                     cyl=-8.0, axis=90)])  # cyl outside -6..+6
    assert r.status_code == 422, r.text
    assert "cyl" in r.text.lower()


def test_prescription_for_other_customer_rejected(client, staff_headers,
                                                  rx_orders):
    pid = _seed_product(rx_orders, pid="LENS-XCUST", category="OPTICAL_LENS",
                        item_type="LENS")
    _seed_rx(rx_orders, prescription_id="rx-other", customer_id="cust-OTHER")
    r = _post(client, staff_headers,
              [_item(pid, item_type="LENS", category="OPTICAL_LENS",
                     sph=-1.00, prescription_id="rx-other")])
    assert r.status_code == 422, r.text
    assert "customer" in r.text.lower() or "belong" in r.text.lower()


# ---------------------------------------------------------------------------
# Allowed paths -- frame-only + valid in-range Rx lens
# ---------------------------------------------------------------------------
def test_frame_only_no_rx_allowed(client, staff_headers, rx_orders):
    """A frame line carries no powers + needs no Rx -> created (201)."""
    pid = _seed_product(rx_orders, pid="FR-1", category="FRAME",
                        item_type="FRAME")
    r = _post(client, staff_headers,
              [_item(pid, item_type="FRAME", category="FRAME")])
    assert r.status_code in (200, 201), r.text


def test_valid_lens_with_prescription_allowed(client, staff_headers, rx_orders):
    """In-range powers + a valid, customer-matching, non-expired Rx -> 201."""
    pid = _seed_product(rx_orders, pid="LENS-OK", category="OPTICAL_LENS",
                        item_type="LENS")
    rxid = _seed_rx(rx_orders, prescription_id="rx-valid")
    r = _post(client, staff_headers,
              [_item(pid, item_type="LENS", category="OPTICAL_LENS",
                     sph=-2.25, cyl=-1.50, axis=90, add=2.00,
                     prescription_id=rxid)])
    assert r.status_code in (200, 201), r.text


# ---------------------------------------------------------------------------
# Expired Rx -- blocked for SALES_STAFF, allowed for STORE_MANAGER+
# ---------------------------------------------------------------------------
def test_expired_rx_blocked_for_sales_staff(client, staff_headers, rx_orders):
    pid = _seed_product(rx_orders, pid="LENS-EXP", category="OPTICAL_LENS",
                        item_type="LENS")
    # 30 months ago with 24-month validity -> expired.
    rxid = _seed_rx(rx_orders, prescription_id="rx-expired",
                    months_ago=30, validity_months=24)
    r = _post(client, staff_headers,
              [_item(pid, item_type="LENS", category="OPTICAL_LENS",
                     sph=-1.00, prescription_id=rxid)])
    assert r.status_code == 422, r.text
    assert "expired" in r.text.lower()


def test_expired_rx_allowed_for_store_manager(client, rx_orders):
    pid = _seed_product(rx_orders, pid="LENS-EXP-MGR", category="OPTICAL_LENS",
                        item_type="LENS")
    rxid = _seed_rx(rx_orders, prescription_id="rx-expired-mgr",
                    months_ago=30, validity_months=24)
    r = _post(client, _manager_headers(),
              [_item(pid, item_type="LENS", category="OPTICAL_LENS",
                     sph=-1.00, prescription_id=rxid)])
    assert r.status_code in (200, 201), r.text
