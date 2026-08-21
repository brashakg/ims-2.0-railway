"""BUG-104 final round: the vendor / procurement / agent dates.

Three findings live here, and they are NOT all the same bug:

  1 IST FRAME (BUG-104 proper). A day derived from a stored instant and shown
    to somebody -- the vendor spend month, the last-GRN date on the stock
    ledger, the PO date in a Jarvis briefing, the 7-day revenue series the
    owner is read back. VALUE rule: move it FORWARD (+5:30) with ist_date_str.
    Where a Mongo bound and a label are the two halves of one series (the
    7-day revenue), the bound moves BACKWARD with ist_day_start_utc.

  2 FINANCIAL-YEAR RULE OFF THE BOX CLOCK. vendors.py carried two more private
    copies of `now.month >= 4` on datetime.utcnow(). Between 00:00 and 05:30
    IST on 1 April both returned the PREVIOUS financial year -- on the s.194Q
    TDS threshold and on the 26Q return.

  3 FRAME **TYPE** (not IST). `purchase_orders` / `grns`.created_at are BSON
    datetimes, and Mongo type-brackets, so the ISO-STRING `$gte` bounds those
    screens used matched NOTHING: the vendor scorecard, the vendor spend chart
    and the stock-ledger GRN chip were silently, permanently empty. Established
    from the WRITER (BaseRepository._add_timestamps), not assumed.

Every assertion reads a RETURNED PAYLOAD. Every shifted case is paired with a
POSITIVE CONTROL. No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("DISPATCH_MODE", "off")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory as inventory_mod  # noqa: E402
from api.routers import jarvis as jarvis_mod  # noqa: E402
from api.routers import vendors as vendors_mod  # noqa: E402
from database.repositories.base_repository import BaseRepository  # noqa: E402

STORE = "ZZ-IST-STORE"
VENDOR = "ZZ-VEND-1"

# 2026-06-30 20:00 UTC IS 1-Jul-2026 01:30 IST.
FROZEN_NOW = datetime(2026, 6, 30, 20, 0, 0)
# 2026-03-31 20:00 UTC IS 1-Apr-2026 01:30 IST -- the financial-year instant.
FY_EVE = datetime(2026, 3, 31, 20, 0, 0)


class _AnyDatetimeIsMine(type):
    """Metaclass so ``isinstance(a_real_datetime, _FrozenDatetime)`` stays
    True: the routers use the same module-global ``datetime`` name for
    ``datetime.now()`` AND for isinstance checks, and a fixture that fails
    those checks is a fixture that agrees with anything."""

    def __instancecheck__(cls, obj):
        return isinstance(obj, datetime)


def _frozen(base=FROZEN_NOW):
    class _FrozenDatetime(datetime, metaclass=_AnyDatetimeIsMine):
        @classmethod
        def now(cls, tz=None):
            return base if tz is None else base.replace(tzinfo=tz)

        @classmethod
        def utcnow(cls):
            return base

    return _FrozenDatetime


class _PatchAttrs:
    def __init__(self, mod, **attrs):
        self.mod = mod
        self.attrs = attrs
        self._saved = {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self._saved[k] = getattr(self.mod, k)
            setattr(self.mod, k, v)
        return self

    def __exit__(self, *_a):
        for k, v in self._saved.items():
            setattr(self.mod, k, v)
        return False


# ---------------------------------------------------------------------------
# A STRICT collection fake. Mongo type-brackets: a string bound never matches a
# Date and vice versa. This fake reproduces that instead of comparing loosely,
# so the dead-query tests below cannot pass by the fake being generous.
# ---------------------------------------------------------------------------


class _TypeBracketColl:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]
        self.last_filter = None

    @staticmethod
    def _cmp_ok(actual, val, op):
        if isinstance(actual, str) != isinstance(val, str):
            # Different BSON types never compare in a range query.
            return False
        if op == "$gte":
            return actual >= val
        if op == "$gt":
            return actual > val
        if op == "$lte":
            return actual <= val
        if op == "$lt":
            return actual < val
        raise AssertionError("fake does not implement %r" % op)

    def _match(self, doc, flt):
        for key, expected in (flt or {}).items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                for op, val in expected.items():
                    if op == "$in":
                        ok = actual in val
                    elif op == "$nin":
                        ok = actual not in val
                    elif op == "$ne":
                        ok = actual != val
                    else:
                        ok = actual is not None and self._cmp_ok(actual, val, op)
                    if not ok:
                        return False
            elif actual != expected:
                return False
        return True

    def find(self, flt=None, *_a, **_k):
        self.last_filter = flt
        return _Cursor([dict(d) for d in self.docs if self._match(d, flt)])

    def find_one(self, flt=None, *_a, **_k):
        rows = list(self.find(flt))
        return rows[0] if rows else None

    def count_documents(self, flt=None, *_a, **_k):
        return len(list(self.find(flt)))


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def limit(self, *_a):
        return self.rows

    def sort(self, *_a, **_k):
        return self

    def __iter__(self):
        return iter(self.rows)


class _DB:
    def __init__(self, **colls):
        self._c = {k: _TypeBracketColl(v) for k, v in colls.items()}

    def get_collection(self, name):
        return self._c.setdefault(name, _TypeBracketColl([]))

    def __getitem__(self, name):
        return self.get_collection(name)


class _VendorRepo:
    @staticmethod
    def find_by_id(_vid):
        return {"vendor_id": VENDOR, "legal_name": "ZZ Vendor Pvt Ltd"}


# ===========================================================================
# THE STORED TYPE -- established from the writer, not assumed
# ===========================================================================


def test_base_repository_stamps_created_at_as_a_real_datetime():
    """The whole vendor/GRN triage rests on this. BaseRepository._add_timestamps
    OVERWRITES whatever created_at the caller passed (vendors.py hands it an
    ISO string) with a datetime, so every purchase_orders / grns row is
    Date-typed and an ISO-string $gte bound can never match one."""
    stamped = BaseRepository._add_timestamps(
        object(), {"created_at": "2026-06-30T20:00:00"}, is_update=False
    )
    assert isinstance(stamped["created_at"], datetime)
    assert not isinstance(stamped["created_at"], str)


# ===========================================================================
# 1 + 3. VENDOR PURCHASE HISTORY -- a dead query AND an IST month key
# ===========================================================================


def _po(created_at, oid, amount=10000.0):
    return {
        "po_id": oid,
        "po_number": oid,
        "vendor_id": VENDOR,
        "status": "SENT",
        "created_at": created_at,
        "total_amount": amount,
        "items": [
            {
                "product_id": "ZZ-P1",
                "product_name": "ZZ Frame",
                "sku": "ZZ-SKU-1",
                "unit_price": 1000.0,
                "quantity": 10,
            }
        ],
    }


def _grn(created_at, gid, received=10):
    return {
        "grn_id": gid,
        "po_id": gid,
        "vendor_id": VENDOR,
        "status": "ACCEPTED",
        "created_at": created_at,
        "total_received": received,
        "total_accepted": received,
    }


def _purchase_history(pos, grns, months=12):
    db = _DB(purchase_orders=pos, grns=grns)
    with _PatchAttrs(
        vendors_mod,
        datetime=_frozen(),
        _get_db=lambda: db,
        get_vendor_repository=lambda: _VendorRepo(),
    ):
        return asyncio.run(
            vendors_mod.vendor_purchase_history(
                vendor_id=VENDOR, months=months, current_user={}
            )
        )


def test_vendor_purchase_history_is_no_longer_permanently_empty():
    """FRAME-TYPE finding. The bound was `cutoff.isoformat()` -- a STRING --
    against a Date-typed created_at, so this chart returned zero rows for every
    vendor, always. Nothing on the screen said so; it just looked like a vendor
    with no purchases."""
    out = _purchase_history([_po(FROZEN_NOW - timedelta(days=40), "ZZ-PO-1")], [])
    assert out["total_pos"] == 1
    assert out["total_spend"] == 10000.0
    assert out["monthly"], out


def test_vendor_purchase_history_months_are_ist_months():
    """VALUE rule. A PO raised 1-Jul 01:30 IST used to be charted in JUNE."""
    out = _purchase_history(
        [
            _po(FROZEN_NOW.replace(hour=20), "ZZ-PO-JUL"),      # 1-Jul 01:30 IST
            _po(FROZEN_NOW.replace(hour=11), "ZZ-PO-JUN"),      # 30-Jun 16:30 IST
        ],
        [],
    )
    months = sorted(row["month"] for row in out["monthly"])
    assert months == ["2026-06", "2026-07"]


def test_vendor_purchase_history_afternoon_po_keeps_its_month_positive_control():
    out = _purchase_history([_po(FROZEN_NOW.replace(hour=11), "ZZ-PO-A")], [])
    assert [row["month"] for row in out["monthly"]] == ["2026-06"]


def test_vendor_purchase_history_grn_units_land_in_the_same_ist_month():
    """Both legs of the same chart row must share a frame."""
    out = _purchase_history(
        [_po(FROZEN_NOW.replace(hour=20), "ZZ-PO-J2")],
        [_grn(FROZEN_NOW.replace(hour=20), "ZZ-GRN-J2", received=7)],
    )
    by_month = {row["month"]: row for row in out["monthly"]}
    assert by_month["2026-07"]["pos"] == 1
    assert by_month["2026-07"]["units_received"] == 7


def _performance(grns, months=6, bills=None, now=FROZEN_NOW):
    db = _DB(
        grns=grns,
        purchase_orders=[],
        vendor_bills=bills or [],
        workshop_jobs=[],
    )
    ist_now = (now + timedelta(hours=5, minutes=30))
    with _PatchAttrs(
        vendors_mod,
        datetime=_frozen(now),
        now_ist=lambda: ist_now,
        _get_db=lambda: db,
        get_vendor_repository=lambda: _VendorRepo(),
    ):
        return asyncio.run(
            vendors_mod.vendor_performance(
                vendor_id=VENDOR, months=months, current_user={}
            )
        )


def test_vendor_performance_scorecard_is_no_longer_permanently_empty():
    """The THIRD dead ISO-string cutoff. The vendor quality/punctuality
    scorecard read zero GRNs for every vendor and reported an honest-looking
    'no GRN data found' that was really a type mismatch."""
    out = _performance(
        [
            _grn(FROZEN_NOW - timedelta(days=d), "ZZ-GRN-S%d" % d, received=10)
            for d in (5, 10, 15)
        ]
    )
    assert out.get("insufficient_data") is not True, out
    assert out.get("total_received") == 30, out


def test_vendor_mtd_spend_asks_for_the_IST_month():
    """VALUE/BOUND pairing on a string column. `bill_date` is already an IST
    business-date string; the month PREFIX it is matched against was the UTC
    month, so for the five and a half hours after IST midnight on the 1st the
    vendor's month-to-date spend showed the PREVIOUS month's total."""
    bills = [
        {"vendor_id": VENDOR, "bill_date": "2026-07-01", "total_amount": 12000.0},
        {"vendor_id": VENDOR, "bill_date": "2026-06-28", "total_amount": 999.0},
    ]
    out = _performance([], bills=bills)
    assert out["mtd_spend"] == 12000.0


def test_vendor_mtd_spend_afternoon_reads_its_own_month_positive_control():
    """At 16:30 IST on 30 June the IST month and the UTC month agree, and the
    June bill is the one that counts."""
    bills = [
        {"vendor_id": VENDOR, "bill_date": "2026-07-01", "total_amount": 12000.0},
        {"vendor_id": VENDOR, "bill_date": "2026-06-28", "total_amount": 999.0},
    ]
    out = _performance([], bills=bills, now=FROZEN_NOW.replace(hour=11))
    assert out["mtd_spend"] == 999.0


# ===========================================================================
# 2. THE FINANCIAL-YEAR RULE -- s.194Q threshold and the 26Q return
# ===========================================================================


def _ist_fy(now):
    """The real ist.fy_start_year_ist, evaluated at a FROZEN instant.

    vendors.py must reach the FY through this shared helper; the test freezes
    the helper's answer rather than the helper's logic, so reverting the fix to
    a private `datetime.utcnow().month >= 4` rule is still detectable."""
    from api.utils import ist as ist_mod

    ist_now = (now + timedelta(hours=5, minutes=30)).replace(tzinfo=ist_mod.IST)
    return lambda dt=None: ist_mod.fy_start_year_ist(ist_now if dt is None else dt)


def _threshold(payments, now=FY_EVE):
    db = _DB(vendor_payments=payments, tds_rate_config=[])
    with _PatchAttrs(
        vendors_mod,
        datetime=_frozen(now),
        fy_start_year_ist=_ist_fy(now),
        _get_db=lambda: db,
    ):
        return asyncio.run(
            vendors_mod.get_tds_threshold_status(
                vendor_id=VENDOR,
                section="194C_OTHER",
                current_payment=1000.0,
                fy_start=None,
                current_user={"roles": ["ADMIN"]},
            )
        )


def _payment(day, amount):
    return {
        "vendor_id": VENDOR,
        "payment_date": day,
        "amount": amount,
        "tds_amount": 0.0,
        "tds_section": "194C_OTHER",
    }


def test_tds_threshold_fy_starts_on_the_IST_1_april():
    """At 01:30 IST on 1 April, `datetime.utcnow().month >= 4` is still False,
    so the old private rule opened the FY on 1-Apr of the PREVIOUS year and
    dragged a whole extra year of payments into the s.194Q cumulative -- which
    is what decides whether TDS is deducted at all."""
    out = _threshold([])
    assert out["fy_start"] == "2026-04-01"
    assert out["fy_start"] != "2025-04-01"


def test_tds_threshold_cumulative_excludes_the_previous_financial_year():
    """The money claim behind the date. 4,00,000 paid last FY must NOT count
    towards this FY's threshold."""
    out = _threshold(
        [_payment("2025-06-15", 400000.0), _payment("2026-04-01", 5000.0)]
    )
    assert out["cumulative_fy_payments"] == 5000.0


def test_tds_threshold_includes_a_payment_dated_1_april_itself():
    """`payment_date` is a DATE-ONLY string, so the old
    `fy_start_dt.isoformat()` bound ('2026-04-01T00:00:00') sorted AFTER
    '2026-04-01' and silently dropped every 1-April payment."""
    out = _threshold([_payment("2026-04-01", 5000.0)])
    assert out["cumulative_fy_payments"] == 5000.0


def test_tds_threshold_afternoon_on_31_march_still_reads_the_old_fy_positive_control():
    """31-Mar 16:30 IST really is the old FY in both frames. Without this a
    'always the next year' implementation passes the tests above."""
    out = _threshold([], now=datetime(2026, 3, 31, 11, 0))
    assert out["fy_start"] == "2025-04-01"


def _26q(payments, now=FY_EVE):
    db = _DB(vendor_payments=payments, vendors=[])
    with _PatchAttrs(
        vendors_mod,
        datetime=_frozen(now),
        fy_start_year_ist=_ist_fy(now),
        _get_db=lambda: db,
    ):
        return asyncio.run(
            vendors_mod.export_26q(
                fy=None, quarter=None, current_user={"roles": ["ACCOUNTANT"]}
            )
        )


def test_26q_export_defaults_to_the_IST_financial_year():
    """Same private April rule, on the quarterly TDS return the accountant
    files. An accountant opening this at 01:30 IST on 1 April used to get the
    PRIOR year's return."""
    pay = _payment("2026-04-01", 100000.0)
    pay["tds_amount"] = 2000.0
    out = _26q([pay])
    assert "2026-27" in out["form_26q"], out["form_26q"]
    assert out["summary"]["total_tds_26q"] == 2000.0


def test_26q_export_march_afternoon_still_defaults_to_the_old_fy_positive_control():
    pay = _payment("2025-06-10", 100000.0)
    pay["tds_amount"] = 2000.0
    out = _26q([pay], now=datetime(2026, 3, 31, 11, 0))
    assert "2025-26" in out["form_26q"], out["form_26q"]


# ===========================================================================
# 1 + 3. THE STOCK LEDGER's last-GRN chip
# ===========================================================================


def _last_grn(grns):
    db = _DB(grns=grns)
    with _PatchAttrs(inventory_mod, datetime=_frozen(), _get_db=lambda: db):
        return inventory_mod._last_grn_by_product(STORE)


def _ledger_grn(created_at, gid):
    return {
        "grn_id": gid,
        "grn_number": gid,
        "store_id": STORE,
        "status": "ACCEPTED",
        "created_at": created_at,
        "accepted_at": created_at,
        "items": [{"product_id": "ZZ-P1", "accepted_qty": 6, "received_qty": 6}],
    }


def test_last_grn_join_is_no_longer_permanently_empty():
    """FRAME-TYPE finding again: the 30-day cutoff was an ISO STRING against a
    Date-typed created_at, so the '+6 via GRN-xxx' source chip never once
    appeared on the stock ledger."""
    out = _last_grn([_ledger_grn(FROZEN_NOW - timedelta(days=2), "ZZ-GRN-L1")])
    assert "ZZ-P1" in out, out
    assert out["ZZ-P1"]["qty"] == 6


def test_last_grn_date_is_the_ist_business_day():
    """VALUE rule: goods booked in at 01:30 IST used to show yesterday."""
    out = _last_grn([_ledger_grn(FROZEN_NOW.replace(hour=20), "ZZ-GRN-L2")])
    assert out["ZZ-P1"]["date"] == "2026-07-01"
    assert out["ZZ-P1"]["date"] != "2026-06-30"


def test_last_grn_afternoon_receipt_keeps_its_own_date_positive_control():
    out = _last_grn([_ledger_grn(FROZEN_NOW.replace(hour=11), "ZZ-GRN-L3")])
    assert out["ZZ-P1"]["date"] == "2026-06-30"


# ===========================================================================
# 1. THE JARVIS BRIEFING -- a PO date the owner reads
# ===========================================================================


def _jarvis_purchases(pos):
    db = _DB(purchase_orders=pos)

    def _coll(name):
        return db.get_collection(name) if name == "purchase_orders" else None

    with _PatchAttrs(jarvis_mod, get_db_collection=_coll):
        return jarvis_mod.JarvisAnalyticsEngine.get_extended_context()


def _jarvis_po(created_at, num):
    return {
        "po_number": num,
        "vendor_name": "ZZ Vendor",
        "status": "SENT",
        "total": 5000.0,
        "created_at": created_at,
    }


def test_jarvis_open_po_date_is_the_ist_business_day():
    """VALUE rule. purchase_orders.created_at is a BSON datetime in the
    naive-UTC frame (established above), and this day is quoted back to the
    owner in a briefing."""
    ctx = _jarvis_purchases([_jarvis_po(FROZEN_NOW.replace(hour=20), "ZZ-PO-JV1")])
    sample = ctx["purchases"]["open_pos_sample"]
    assert sample and sample[0]["created_at"] == "2026-07-01"


def test_jarvis_open_po_afternoon_date_unchanged_positive_control():
    ctx = _jarvis_purchases([_jarvis_po(FROZEN_NOW.replace(hour=11), "ZZ-PO-JV2")])
    sample = ctx["purchases"]["open_pos_sample"]
    assert sample and sample[0]["created_at"] == "2026-06-30"


# ===========================================================================
# 1. THE ORACLE 7-DAY REVENUE SERIES -- bound and label, opposite directions
# ===========================================================================


class _OracleColl:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    def find(self, flt=None, *_a, **_k):
        rng = ((flt or {}).get("created_at")) or {}
        rows = []
        for d in self.docs:
            ca = d.get("created_at")
            if "$gte" in rng and not (ca is not None and ca >= rng["$gte"]):
                continue
            if "$lt" in rng and not (ca is not None and ca < rng["$lt"]):
                continue
            rows.append(dict(d))
        return rows


def _oracle_revenue(orders, today):
    """Drive the REAL 7-day revenue block. `ist_today` is patched on the ist
    module itself because oracle imports it inside the function -- patching the
    module attribute is therefore the only seam, and it is the same object the
    Mongo bound is built from, so bound and label cannot drift apart in the
    test the way they used to in the code."""
    from agents.implementations import oracle as oracle_mod
    import api.utils.ist as ist_mod

    agent = oracle_mod.OracleAgent.__new__(oracle_mod.OracleAgent)
    coll = _OracleColl(orders)
    agent.get_collection = lambda name: coll if name == "orders" else None

    real_today = ist_mod.ist_today
    ist_mod.ist_today = lambda: today
    try:
        ctx = asyncio.run(agent._build_context_for_query("revenue"))
    finally:
        ist_mod.ist_today = real_today
    return ctx.get("revenue_last_7d") or {}


def test_oracle_seven_day_revenue_labels_and_bounds_share_the_ist_frame():
    """The owner is read this series as 'yesterday's revenue'. On the old UTC
    days a sale at 01:30 IST was reported against the previous day; the fix
    moves the day LABEL forward and the Mongo bound backward, together."""
    today = date(2026, 7, 1)
    by_day = _oracle_revenue(
        [
            {"created_at": datetime(2026, 6, 30, 20, 0), "grand_total": 5000.0},
            {"created_at": datetime(2026, 6, 30, 11, 0), "grand_total": 700.0},
        ],
        today,
    )
    assert by_day.get("2026-07-01") == 5000.0
    # POSITIVE CONTROL: the 16:30-IST sale on 30 June stays on 30 June.
    assert by_day.get("2026-06-30") == 700.0


def test_oracle_seven_day_revenue_loses_no_rupee_between_the_days():
    today = date(2026, 7, 1)
    orders = [
        {"created_at": datetime(2026, 6, 30, 20, 0), "grand_total": 5000.0},
        {"created_at": datetime(2026, 6, 30, 11, 0), "grand_total": 700.0},
        {"created_at": datetime(2026, 6, 28, 9, 0), "grand_total": 300.0},
    ]
    by_day = _oracle_revenue(orders, today)
    assert sum(by_day.values()) == 6000.0
    assert sorted(by_day) == [
        (today - timedelta(days=d)).isoformat() for d in range(6, -1, -1)
    ]
