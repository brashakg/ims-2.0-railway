"""H1 (GST review 2026-10-19): the unclaimed GST a bill adds to a unit's cost
(written before S4, still on old units; the one average cost since) and the bill a cost came from
are cost: no counter role reads either on any route that returns stock units.
The accounts roles still do (the control that the seed reaches a reader)."""

import json

import pytest

from api.services import cost_mask
from tests.test_counter_roles_no_cost_anywhere import (  # noqa: F401 -- `shop` is the fixture
    COUNTER_ROLES,
    STORE,
    _headers,
    shop,
)

GST_PER_PIECE = 216.09
SECRET_BILL = "BILL-SUPPLIER-7731"

# Every GET that hands back stock-unit documents (grep: routers reading
# stock_units / the stock repository and returning the rows).
UNIT_READS = [
    "/api/v1/inventory/stock?product_id=P1",
    f"/api/v1/inventory/stock?store_id={STORE}",
    f"/api/v1/inventory?store_id={STORE}",
    "/api/v1/inventory/units?product_id=P1",
    "/api/v1/inventory/serials?product_id=P1",
    "/api/v1/inventory/barcode/2900000000017",
    "/api/v1/inventory/stock/barcode/2900000000017",
    "/api/v1/inventory/barcode/2900000000017/trace",
    "/api/v1/inventory/sellable?product_id=P1",
    "/api/v1/inventory/lookup?q=SKU-P1",
    "/api/v1/inventory/expiring",
    "/api/v1/inventory/stock/quarantined",
    "/api/v1/inventory/cross-store-stock?product_id=P1",
    "/api/v1/inventory/movements?product_id=P1",
]


@pytest.fixture
def stamped(shop):
    shop.stock_units.update_one({"stock_id": "U1"}, {"$set": {
        "unclaimed_gst": {"per_piece": GST_PER_PIECE, "pieces": 2, "bills": [SECRET_BILL]},
        "cost_source_id": SECRET_BILL,
        "cost_source": "PURCHASE_INVOICE",
    }})
    return shop


def _leaks(text):
    return [needle for needle in ("unclaimed_gst", "cost_source_id", SECRET_BILL, str(GST_PER_PIECE))
            if needle in text]


def test_both_keys_are_cost_to_the_one_mask():
    assert {"unclaimed_gst", "cost_source_id"} <= cost_mask._COST_FIELDS


@pytest.mark.parametrize("role", COUNTER_ROLES)
@pytest.mark.parametrize("path", UNIT_READS)
def test_counter_roles_never_read_the_unclaimed_gst(client, stamped, role, path):
    r = client.get(path, headers=_headers(role))
    assert not _leaks(r.text), (role, path, r.status_code, _leaks(r.text))


def test_the_stock_read_hands_counter_staff_the_unit_without_it(client, stamped):
    """Pinned on one route that really returns the unit: the counter sees U1,
    not its unclaimed GST; an admin sees both."""
    path = "/api/v1/inventory/stock?product_id=P1"
    counter = client.get(path, headers=_headers("SALES_STAFF"))
    assert counter.status_code == 200 and "U1" in counter.text and not _leaks(counter.text)
    assert "unclaimed_gst" in client.get(path, headers=_headers("ADMIN")).text


def test_the_accounts_roles_still_read_it(client, stamped):
    seen = [path for path in UNIT_READS
            if "unclaimed_gst" in json.dumps(client.get(path, headers=_headers("ADMIN")).json())]
    assert seen, "no unit read returned the stamped unit to an admin"
