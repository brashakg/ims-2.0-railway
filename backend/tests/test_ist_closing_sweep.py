"""BUG-104 closing sweep: the six verifier items that complete the round-1 table.

Round 1 fixed 29 sites and claimed completeness; the round-1 verifier found six
more, each with a reproduced consequence. This file is their proof layer:

  1 PATIENT-FACING  marketing.py /rx-reminder -- the WhatsApp expiry a patient
    receives quoted the raw UTC instant, 100 lines below the round-1 fix in the
    same file, so the message and the staff screen disagreed. VALUE rule.
  2 STATUTORY       einvoice.py DocDtls.Dt -- the IRN document date sliced the
    UTC day; a 1-Apr-01:30-IST invoice would register prior-FY on the IRP
    while the fixed GSTR-1 files it 1-April. VALUE rule (dark today, SIMULATED).
  3 THIS PR'S OWN HALF-FIX  analytics_v2.py anomaly-detection -- round 1 moved
    the printed dates to IST but left the SELECTION window at naive midnight,
    so every 00:00-05:30-IST void on the first day of a scan was invisible,
    and the old $lte-to-midnight bound dropped the whole LAST day. BOUND rule.
  4 DEAD QUERIES    jarvis.py owner brief -- string $gte bounds against
    BSON-datetime created_at (Mongo type-brackets: they match NOTHING), so
    month-sales, prescriptions-this-month, top-SKUs and new-customer counts
    were honest-looking, permanently empty. Proven with a type-bracketing fake.
  5 MIS-TABLED      campaign_segments.py rx_expiry token -- same defect, same
    customer, different channel as item 1; the two channels must agree.
  6 THE TABLE       printed paper (print_legal.format_date), typed-range bounds
    (customer acquisition, promotions), the leaderboard twins, and the legacy
    fallback legs (drawer recon, HR applied_at, _rx_validity, clinical Rx card).

Arithmetic note (item 1): the task sheet's example expected "01 Jul 2028" for
an Rx created 2026-06-30T22:30 UTC, but 730 days from that instant is
2028-06-29T22:30 UTC (2028 is a leap year), whose IST day is 30 Jun 2028. The
REQUIREMENT is the IST calendar day of created+730d, agreeing across channels;
the assertions below pin that day (and that it is NOT the raw UTC day).

Every shifted case has an afternoon POSITIVE CONTROL beside it. All clocks are
frozen; nothing is calendar-dependent. No emoji (Windows cp1252).
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

from api.routers import analytics_v2 as analytics_v2_mod  # noqa: E402
from api.routers import clinical as clinical_mod  # noqa: E402
from api.routers import finance as finance_mod  # noqa: E402
from api.routers import hr_self_service as hr_mod  # noqa: E402
from api.routers import jarvis as jarvis_mod  # noqa: E402
from api.routers import marketing as marketing_mod  # noqa: E402
from api.routers import payroll as payroll_mod  # noqa: E402
from api.routers import prescriptions as prescriptions_mod  # noqa: E402
from api.routers import reports as reports_mod  # noqa: E402
from api.services import campaign_segments as segments_mod  # noqa: E402
from api.services import einvoice as einvoice_mod  # noqa: E402
from api.services import print_legal as print_legal_mod  # noqa: E402
from api.services import rtv_debit_note as rtv_mod  # noqa: E402
from api.utils.ist import IST, ist_date_str  # noqa: E402

STORE = "ZZ-IST-STORE"
CID = "ZZ-CUST-1"

# 2026-06-30 20:00 UTC IS 1-Jul-2026 01:30 IST -- the early-window instant.
EARLY = datetime(2026, 6, 30, 20, 0, 0)
# 2026-06-30 11:00 UTC IS 30-Jun-2026 16:30 IST -- the afternoon control.
AFTERNOON = datetime(2026, 6, 30, 11, 0, 0)
# 2026-03-31 20:00 UTC IS 1-Apr-2026 01:30 IST -- the financial-year instant.
FY_EVE = datetime(2026, 3, 31, 20, 0, 0)

ADMIN = {"user_id": "ZZ-U1", "roles": ["ADMIN"], "active_store_id": STORE}
SUPER = {"user_id": "ZZ-U0", "roles": ["SUPERADMIN"], "active_store_id": STORE}


class _AnyDatetimeIsMine(type):
    """isinstance(real_datetime, _FrozenDatetime) must stay True (routers use
    the same module-global name for now() AND isinstance checks)."""

    def __instancecheck__(cls, obj):
        return isinstance(obj, datetime)


def _frozen(base):
    class _FrozenDatetime(datetime, metaclass=_AnyDatetimeIsMine):
        @classmethod
        def now(cls, tz=None):
            return base if tz is None else base.replace(tzinfo=tz)

        @classmethod
        def utcnow(cls):
            return base

    return _FrozenDatetime


def _ist_now_of(utc_naive):
    """The aware-IST instant equal to a naive-UTC instant (to freeze now_ist)."""
    return (utc_naive + timedelta(hours=5, minutes=30)).replace(tzinfo=IST)


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
# STRICT collection fake. Mongo type-brackets: a string bound never matches a
# Date and vice versa; this fake reproduces that, so the dead-query tests
# cannot pass by the fake being generous. Extends the round-1 fake with the
# aggregate() shape jarvis's top-SKU pipeline uses.
# ---------------------------------------------------------------------------


class _TypeBracketColl:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    @staticmethod
    def _cmp_ok(actual, val, op):
        if isinstance(actual, str) != isinstance(val, str):
            return False  # different BSON types never compare in a range query
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
            actual = _dig(doc, key)
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
        return _Cursor([dict(d) for d in self.docs if self._match(d, flt)])

    def find_one(self, flt=None, *_a, **_k):
        rows = list(self.find(flt))
        return rows[0] if rows else None

    def count_documents(self, flt=None, *_a, **_k):
        return len(list(self.find(flt)))

    def aggregate(self, pipeline, *_a, **_k):
        rows = [dict(d) for d in self.docs]
        for stage in pipeline:
            (op, spec), = stage.items()
            if op == "$match":
                rows = [r for r in rows if self._match(r, spec)]
            elif op == "$unwind":
                fld = spec.lstrip("$")
                out = []
                for r in rows:
                    for item in r.get(fld) or []:
                        c = dict(r)
                        c[fld] = item
                        out.append(c)
                rows = out
            elif op == "$group":
                groups = {}
                for r in rows:
                    key = _dig(r, spec["_id"].lstrip("$"))
                    slot = groups.setdefault(key, {"_id": key})
                    for out_key, expr in spec.items():
                        if out_key == "_id":
                            continue
                        val = _dig(r, expr["$sum"].lstrip("$")) or 0
                        slot[out_key] = slot.get(out_key, 0) + val
                rows = list(groups.values())
            elif op == "$sort":
                for fld, direction in reversed(list(spec.items())):
                    rows.sort(key=lambda r: r.get(fld) or 0, reverse=direction < 0)
            elif op == "$limit":
                rows = rows[:spec]
            else:
                raise AssertionError("fake does not implement %r" % op)
        return rows


def _dig(doc, dotted):
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def limit(self, *_a):
        return self

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


# ===========================================================================
# 1. PATIENT-FACING: the WhatsApp Rx-expiry the patient RECEIVES
# ===========================================================================


def _send_rx_reminder(rx_created):
    """Drive the real /rx-reminder route; capture the send_notification vars."""
    sent = {}

    async def _capture(**kwargs):
        sent.update(kwargs)
        return {"status": "SIMULATED"}

    db = _DB(
        customers=[{"customer_id": CID, "name": "ZZ Patient", "mobile": "9876543210"}],
        prescriptions=[{"customer_id": CID, "created_at": rx_created}],
    )
    with _PatchAttrs(
        marketing_mod,
        _get_db=lambda: db,
        is_opted_out=lambda *_a, **_k: False,
        send_notification=_capture,
    ):
        asyncio.run(marketing_mod.send_rx_reminder(CID, current_user=dict(ADMIN)))
    return sent["variables"]["expiry_date"]


def test_rx_reminder_whatsapp_expiry_is_the_ist_day():
    """VALUE rule. Rx recorded 2026-06-30 22:30 UTC == 04:00 IST on 1 July.
    Its expiry instant (+730d) is 2028-06-29 22:30 UTC == 04:00 IST 30 June
    2028, so the patient must read '30 Jun 2028' -- the raw strftime said
    '29 Jun 2028', one day early, and disagreed with the staff screen."""
    got = _send_rx_reminder(datetime(2026, 6, 30, 22, 30))
    assert got == "30 Jun 2028"
    assert got != "29 Jun 2028"


def test_rx_reminder_afternoon_expiry_unchanged_positive_control():
    """An Rx recorded 16:30 IST has the same day in both frames."""
    assert _send_rx_reminder(AFTERNOON) == "29 Jun 2028"


def test_rx_reminder_agrees_with_the_staff_alert_screen():
    """The round-1-fixed staff screen shows ist_date_str(created + 730d) for
    the same prescription; the WhatsApp text must quote the SAME calendar day."""
    created = datetime(2026, 6, 30, 22, 30)
    staff_day = ist_date_str(created + timedelta(days=730))
    whatsapp = _send_rx_reminder(created)
    assert datetime.strptime(whatsapp, "%d %b %Y").date().isoformat() == staff_day


# ===========================================================================
# 2. STATUTORY (DARK): the IRN DocDtls date
# ===========================================================================


def test_irn_docdtls_date_is_the_ist_calendar_day():
    """An invoice created 2026-03-31 20:00 UTC is 01:30 IST on 1 APRIL -- new
    financial year. The old UTC-day slice would have registered it on the IRP
    dated 31/03 prior-FY while the fixed GSTR-1 files it 1-April: one invoice,
    two statutory dates (and GSTN cross-populates GSTR-1 from IRN)."""
    payload = einvoice_mod._build_einvoice_json({"created_at": FY_EVE})
    assert payload["DocDtls"]["Dt"] == "01/04/2026"


def test_irn_docdtls_afternoon_date_unchanged_positive_control():
    payload = einvoice_mod._build_einvoice_json(
        {"created_at": datetime(2026, 3, 31, 11, 0)}
    )
    assert payload["DocDtls"]["Dt"] == "31/03/2026"


def test_irn_docdtls_business_date_string_passes_through_unshifted():
    """invoice_date is a date-only business string; it carries no instant to
    shift and must land on the IRP verbatim."""
    payload = einvoice_mod._build_einvoice_json({"invoice_date": "2026-03-31"})
    assert payload["DocDtls"]["Dt"] == "31/03/2026"


def test_irn_date_fallback_today_is_the_ist_day():
    """The junk-input fallback is the same statutory date, so 'today' must be
    the IST day -- at 01:30 IST on 1 April the UTC box still says 31 March."""
    with _PatchAttrs(einvoice_mod, now_ist=lambda: _ist_now_of(FY_EVE)):
        assert einvoice_mod._fmt_date_ddmmyyyy("total junk") == "01/04/2026"


# ===========================================================================
# 3. THE FRAUD SCAN: selection window and range label
# ===========================================================================


def _anomalies(orders, date_from=None, date_to=None):
    db = _DB(orders=orders)
    with _PatchAttrs(analytics_v2_mod, _get_db=lambda: db):
        out = asyncio.run(
            analytics_v2_mod.anomaly_detection(
                store_id=STORE,
                date_from=date_from,
                date_to=date_to,
                current_user=dict(SUPER),
            )
        )
    return out["anomalies"]


# Fixtures use `salesperson_id` because that is the key the create-order door
# actually writes (orders.py). These fixtures previously used `sales_staff_id`,
# a key NO production writer has ever set - so the leaderboards they exercised
# bucketed every order to "unknown" and this file's agreement test passed
# because both implementations were equally blind. The fixture must match the
# real document or the test proves nothing.
def _void(created_at, oid, staff="ZZ-S1"):
    return {
        "order_id": oid,
        "store_id": STORE,
        "status": "cancelled",
        "salesperson_id": staff,
        "salesperson_name": "ZZ Staff",
        "created_at": created_at,
        "total_amount": 1000.0,
    }


def test_fraud_scan_sees_first_day_early_ist_voids():
    """BOUND rule, the verifier's reproduced case: 3 voids at 2026-05-31 20:30
    UTC ARE 02:00 IST on 1 June. A June scan bounded at naive midnight started
    18:30 too late and never saw them -- the exact window a till-fraud pattern
    would pick."""
    voids = [_void(datetime(2026, 5, 31, 20, 30), "ZZ-O%d" % i) for i in range(3)]
    rows = _anomalies(voids, "2026-06-01", "2026-06-30")
    kinds = {a["type"]: a for a in rows}
    assert "excessive_voids" in kinds, rows
    assert "3 voided" in kinds["excessive_voids"]["description"]


def test_fraud_scan_covers_the_whole_last_requested_day():
    """Pre-existing half of the same bound: the old $lte-to-midnight upper
    bound excluded every order on the LAST day the SUPERADMIN asked for."""
    voids = [_void(datetime(2026, 6, 30, 12, 0), "ZZ-L%d" % i) for i in range(3)]
    rows = _anomalies(voids, "2026-06-01", "2026-06-30")
    assert any(a["type"] == "excessive_voids" for a in rows), rows


def test_fraud_scan_excludes_the_prior_ist_month_positive_control():
    """A void at 16:30 IST on 31 May is genuinely OUTSIDE a June scan; an
    implementation that just widens the window would wrongly include it."""
    voids = [_void(datetime(2026, 5, 31, 11, 0), "ZZ-P%d" % i) for i in range(3)]
    assert _anomalies(voids, "2026-06-01", "2026-06-30") == []


def test_fraud_scan_anomaly_dates_echo_the_requested_ist_days():
    """The anomaly row's date must name the days the SUPERADMIN typed --
    printing the shifted bound instants would label a June scan
    '2026-05-31 to 2026-06-29'."""
    voids = [_void(datetime(2026, 6, 15, 12, 0), "ZZ-D%d" % i) for i in range(3)]
    rows = _anomalies(voids, "2026-06-01", "2026-06-30")
    labels = {a["date"] for a in rows if a["type"] == "excessive_voids"}
    assert labels == {"2026-06-01 to 2026-06-30"}, labels


# ===========================================================================
# 4. THE OWNER DAILY BRIEF: dead queries against BSON-datetime created_at
# ===========================================================================


def _jarvis_colls(now=EARLY, **colls):
    db = _DB(**colls)

    def _coll(name):
        return db.get_collection(name) if name in colls else None

    ist_today = (now + timedelta(hours=5, minutes=30)).date()
    return _PatchAttrs(
        jarvis_mod,
        datetime=_frozen(now),
        get_db_collection=_coll,
        ist_today=lambda: ist_today,
        now_ist=lambda: _ist_now_of(now),
    )


def _order(created_at, total=5000.0, status="CONFIRMED"):
    return {
        "status": status,
        "created_at": created_at,
        "grand_total": total,
        "items": [
            {
                "category": "FRAMES",
                "item_total": total,
                "quantity": 2,
                "product_name": "ZZ Frame",
                "sku": "ZZ-SKU-1",
                "line_total": total,
            }
        ],
    }


def test_owner_brief_month_sales_is_no_longer_permanently_empty():
    """DEAD QUERY: orders.created_at is a BSON datetime and the old
    '%Y-%m-01' STRING $gte type-bracketed to zero rows -- month revenue was
    an honest-looking 0, permanently. The frozen clock is 01:30 IST on 1 July,
    and the 00:30-IST-on-the-1st sale (19:00 UTC 30 June) must be in JULY."""
    with _jarvis_colls(orders=[_order(datetime(2026, 6, 30, 19, 0))]):
        out = jarvis_mod.JarvisAnalyticsEngine._compute_sales_live()
    assert out is not None, "month query returned no rows (dead query is back)"
    assert out["month_revenue"] == 5000.0


def test_owner_brief_month_sales_excludes_last_ist_month_positive_control():
    """A 16:30-IST 30-June sale belongs to JUNE and must NOT leak into the
    July brief -- an unbounded query would wrongly include it."""
    with _jarvis_colls(orders=[_order(AFTERNOON)]):
        out = jarvis_mod.JarvisAnalyticsEngine._compute_sales_live()
    assert out is None or out["month_revenue"] == 0.0


def test_owner_brief_new_customers_this_month_counts_again():
    """DEAD QUERY on customers.created_at: 'New this month' was permanently 0."""
    customers = [
        {"customer_id": "ZZ-C-JUL", "created_at": datetime(2026, 6, 30, 19, 0)},
        {"customer_id": "ZZ-C-JUN", "created_at": AFTERNOON},
    ]
    with _jarvis_colls(customers=customers):
        out = jarvis_mod.JarvisAnalyticsEngine.get_customer_insights()
    by_name = {s["name"]: s["count"] for s in out["segments"]}
    assert by_name["All customers"] == 2
    assert by_name["New this month"] == 1  # the 00:30-IST-on-the-1st signup only


def test_owner_brief_prescriptions_this_month_counts_again():
    """DEAD QUERY on prescriptions.created_at inside the extended context."""
    rx = [
        {"prescription_id": "ZZ-R1", "created_at": datetime(2026, 6, 30, 19, 0)},
        {"prescription_id": "ZZ-R2", "created_at": AFTERNOON},
    ]
    with _jarvis_colls(prescriptions=rx):
        ctx = jarvis_mod.JarvisAnalyticsEngine.get_extended_context()
    assert ctx["prescriptions"]["total"] == 2
    assert ctx["prescriptions"]["this_month"] == 1


def test_owner_brief_top_skus_is_no_longer_permanently_empty():
    """DEAD QUERY inside an aggregate $match: the string cutoff matched no
    BSON-datetime rows, so top-SKUs was always []. A rolling 30-day cutoff is
    pure elapsed time -- the 40-day-old order stays out."""
    orders = [
        _order(EARLY - timedelta(days=5)),
        _order(EARLY - timedelta(days=40), total=99.0),
    ]
    with _jarvis_colls(orders=orders):
        ctx = jarvis_mod.JarvisAnalyticsEngine.get_extended_context()
    skus = ctx.get("top_skus_30d") or []
    assert skus, "top-SKU aggregate returned no rows (dead query is back)"
    assert skus[0]["product_name"] == "ZZ Frame"
    assert skus[0]["qty_sold"] == 2  # the 5-day-old order only, not the 40-day


# ---------------------------------------------------------------------------
# 4b. SELF-FOUND in the closing sweep (declared in the PR table): five more
# dead or mis-framed counters in the same owner brief, fixed alongside the
# four named sites. Each proven the same way.
# ---------------------------------------------------------------------------


def _jarvis_ctx(now=EARLY, **colls):
    from api.utils.ist import ist_day_start_utc as _real_start

    frozen_today = (now + timedelta(hours=5, minutes=30)).date()
    db = _DB(**colls)

    def _coll(name):
        return db.get_collection(name) if name in colls else None

    with _PatchAttrs(
        jarvis_mod,
        datetime=_frozen(now),
        get_db_collection=_coll,
        ist_today=lambda: frozen_today,
        now_ist=lambda: _ist_now_of(now),
        ist_day_start_utc=lambda d=None: _real_start(d if d is not None else frozen_today),
    ):
        return jarvis_mod.JarvisAnalyticsEngine.get_extended_context()


def test_owner_brief_grn_count_is_no_longer_permanently_zero():
    """Self-found DEAD QUERY: grns.created_at is a BSON datetime; the old
    '%Y-%m-%d' string bound matched nothing and the brief always said 0 GRNs."""
    grns = [
        {"grn_id": "ZZ-G1", "created_at": EARLY - timedelta(days=5)},
        {"grn_id": "ZZ-G2", "created_at": EARLY - timedelta(days=40)},
    ]
    ctx = _jarvis_ctx(grns=grns)
    assert ctx["grns_last_30d"] == 1  # elapsed 30d: the 40-day-old row stays out


def test_owner_brief_alert_count_reads_the_field_sentinel_writes():
    """Self-found field-name miss: SENTINEL stamps `timestamp` (BSON datetime),
    never `created_at` -- the 7-day alert count was permanently 0 twice over."""
    alerts = [{"alert_id": "ZZ-A1", "timestamp": EARLY - timedelta(days=1)}]
    ctx = _jarvis_ctx(alert_history=alerts)
    assert ctx["alerts_last_7d"] == 1


def test_owner_brief_webhook_inbox_count_is_no_longer_permanently_zero():
    """Self-found DEAD QUERY: webhook_inbox.received_at is a BSON datetime."""
    inbox = [{"webhook_id": "ZZ-W1", "received_at": EARLY - timedelta(hours=2)}]
    ctx = _jarvis_ctx(webhook_inbox=inbox)
    assert ctx["webhook_inbox_recent"] == 1


def test_owner_brief_tally_exports_reads_the_field_nexus_writes():
    """Self-found field-name miss: NEXUS stamps `generated_at` (aware-UTC ISO
    string), never `exported_at` -- the count was permanently 0."""
    exports = [
        {
            "export_id": "ZZ-T1",
            "generated_at": (EARLY - timedelta(days=1)).isoformat() + "+00:00",
        }
    ]
    ctx = _jarvis_ctx(tally_exports=exports)
    assert ctx["tally_exports_recent"] == 1


def test_owner_brief_marketing_sent_today_starts_at_ist_midnight():
    """Self-found BOUND on a string column in the string's own frame: a send at
    01:00 IST on 1 July ('2026-06-30T19:30:00') is TODAY at 01:30 IST; a
    16:30-IST 30-June send is yesterday (positive control)."""
    logs = [
        {"sent_at": "2026-06-30T19:30:00"},
        {"sent_at": "2026-06-30T12:00:00"},
    ]
    ctx = _jarvis_ctx(notification_logs=logs)
    assert ctx["marketing"]["sent_today"] == 1
    assert ctx["marketing"]["sent_this_week"] == 2


def test_owner_brief_expense_month_is_the_ist_month():
    """Self-found month label: expenses.date is an operator-typed IST
    business-date string; 'this month' must be the IST month, which at 01:30
    IST on 1 July is JULY while the UTC box still says June."""
    expenses = [
        {"date": "2026-07-01", "amount": 500.0, "category": "TEA"},
        {"date": "2026-06-28", "amount": 999.0, "category": "TEA"},
    ]
    ctx = _jarvis_ctx(expenses=expenses)
    assert ctx["expenses_mtd"]["total_this_month"] == 500.0


def test_owner_brief_payroll_and_targets_use_the_ist_month_key():
    """Self-found month labels: salary_records.month and targets.period are
    'YYYY-MM' IST payroll/business periods."""
    ctx = _jarvis_ctx(
        salary_records=[
            {"month": "2026-07", "net_pay": 15000.0},
            {"month": "2026-06", "net_pay": 14000.0},  # last month: must stay out
        ],
        targets=[{"store_id": STORE, "period": "2026-07", "target_amount": 1.0}],
    )
    assert ctx["payroll_mtd"]["records_this_month"] == 1
    assert len(ctx["targets"]) == 1


def test_reengagement_nudge_no_longer_calls_every_customer_lapsed():
    """Self-found DEAD QUERY in get_recommendations: the string 180-day bound
    matched no BSON-datetime orders, so recent_buyer_ids was always empty and
    the re-engagement nudge fired on the ENTIRE customer base."""
    customers = [{"customer_id": "ZZ-C%d" % i} for i in range(20)]
    orders = [
        {"customer_id": "ZZ-C%d" % i, "created_at": EARLY - timedelta(days=10)}
        for i in range(16)
    ]
    db = _DB(customers=customers, orders=orders)

    def _coll(name):
        return db.get_collection(name) if name in ("customers", "orders") else None

    with _PatchAttrs(
        jarvis_mod,
        datetime=_frozen(EARLY),
        get_db_collection=_coll,
        ist_today=lambda: date(2026, 7, 1),
        now_ist=lambda: _ist_now_of(EARLY),
    ):
        recs = jarvis_mod.JarvisAnalyticsEngine.get_recommendations()
    # 16 of 20 bought recently -> only 4 lapsed, below the >= 5 nudge floor.
    assert not any("re-engage" in str(r).lower() or "lapsed" in str(r).lower() for r in recs), recs


# ===========================================================================
# 5. MIS-TABLED: the rx_expiry campaign token (must AGREE with item 1)
# ===========================================================================


def _campaign_expiry_token(rx_created):
    expiry = rx_created + timedelta(days=730)
    now = expiry - timedelta(days=10)  # inside the send window
    db = _DB(
        prescriptions=[{"customer_id": CID, "created_at": rx_created}],
        customers=[{"customer_id": CID, "name": "ZZ Patient", "mobile": "9876543210"}],
    )
    rows = segments_mod._resolve_rx_expiry(db, None, window_days=30, now=now)
    assert rows, "rx_expiry segment resolved no audience"
    return rows[0]["variables"]["expiry_date"]


def test_campaign_rx_expiry_token_is_the_ist_day():
    """VALUE rule; round 1 mis-tabled this as 'not stored instants', but
    prescriptions.created_at IS a BaseRepository-stamped BSON datetime."""
    got = _campaign_expiry_token(datetime(2026, 6, 30, 22, 30))
    assert got == "30 Jun 2028"
    assert got != "29 Jun 2028"


def test_campaign_rx_expiry_afternoon_token_unchanged_positive_control():
    assert _campaign_expiry_token(AFTERNOON) == "29 Jun 2028"


def test_both_channels_quote_one_expiry_date():
    """Item 1 and item 5 are the same customer on two channels: the WhatsApp
    /rx-reminder text and the rx_expiry campaign token must be IDENTICAL for
    the same prescription."""
    created = datetime(2026, 6, 30, 22, 30)
    assert _send_rx_reminder(created) == _campaign_expiry_token(created)


def test_lapsed_patient_last_touch_is_the_ist_day():
    """The other campaign_segments site (:638): last_touch_date shown on the
    lapsed-patient audience row must be the IST business day."""
    old_touch = datetime(2024, 6, 30, 20, 0)  # 01:30 IST 1-Jul-2024
    db = _DB(
        orders=[
            {
                "customer_id": CID,
                "created_at": old_touch,
                "status": "COMPLETED",
            }
        ],
        prescriptions=[],
        customers=[{"customer_id": CID, "name": "ZZ Patient", "mobile": "9876543210"}],
    )
    rows = segments_mod._resolve_lapsed_patient(db, None, now=EARLY)
    assert rows, "lapsed segment resolved no audience"
    assert rows[0]["variables"]["last_touch_date"] == "2024-07-01"
    # POSITIVE CONTROL: an afternoon touch keeps its own day.
    db2 = _DB(
        orders=[
            {
                "customer_id": CID,
                "created_at": datetime(2024, 6, 30, 11, 0),
                "status": "COMPLETED",
            }
        ],
        prescriptions=[],
        customers=[{"customer_id": CID, "name": "ZZ Patient", "mobile": "9876543210"}],
    )
    rows2 = segments_mod._resolve_lapsed_patient(db2, None, now=EARLY)
    assert rows2[0]["variables"]["last_touch_date"] == "2024-06-30"


# ===========================================================================
# 6a. PRINTED PAPER: the shared format_date sink
# ===========================================================================


def test_printed_paper_dates_are_the_ist_day():
    """VALUE rule at the one sink every estimate / Rule-55 delivery-challan
    date flows through. All three stored-instant input shapes (established
    from their writers) shift; the date-only business string does not."""
    fd = print_legal_mod.format_date
    # orders.created_at: naive BSON datetime (UTC wall clock)
    assert fd(FY_EVE) == "01-Apr-2026"
    # estimates.created_at: aware '+00:00' ISO string
    assert fd("2026-03-31T20:00:00+00:00") == "01-Apr-2026"
    # stock_transfers.created_at: naive-UTC ISO string
    assert fd("2026-03-31T20:00:00") == "01-Apr-2026"
    # valid_until / invoice_date: date-only business string -- unshifted
    assert fd("2026-03-31") == "31-Mar-2026"


def test_printed_paper_afternoon_dates_unchanged_positive_control():
    fd = print_legal_mod.format_date
    assert fd(datetime(2026, 3, 31, 11, 0)) == "31-Mar-2026"
    assert fd("2026-03-31T11:00:00+00:00") == "31-Mar-2026"


# ===========================================================================
# 6b. TYPED-RANGE BOUNDS: customer acquisition and the promotions report
# ===========================================================================


class _Repo:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    def find_many(self, _flt=None, limit=None):
        return [dict(d) for d in self.docs]


def _acquisition(customers, from_d, to_d):
    with _PatchAttrs(
        reports_mod,
        get_customer_repository=lambda: _Repo(customers),
        get_order_repository=lambda: None,
    ):
        return asyncio.run(
            reports_mod.customer_acquisition(
                store_id=STORE,
                from_date=from_d,
                to_date=to_d,
                current_user=dict(ADMIN),
            )
        )


def test_customer_acquisition_window_covers_the_requested_ist_days():
    """BOUND rule on a user-typed IST range. A signup at 00:30 IST on 1 June
    (2026-05-31 19:00 UTC) belongs to a June report; one at 01:30 IST on
    1 July does not; the 16:30-IST 30-June one does (positive control); and a
    legacy string row keeps the old calendar-day comparison."""
    customers = [
        {"customer_id": "ZZ-A1", "created_at": datetime(2026, 5, 31, 19, 0)},
        {"customer_id": "ZZ-A2", "created_at": datetime(2026, 5, 31, 19, 30)},
        {"customer_id": "ZZ-B", "created_at": datetime(2026, 6, 30, 19, 0)},
        {"customer_id": "ZZ-C", "created_at": AFTERNOON},
        {"customer_id": "ZZ-D", "created_at": "2026-06-15T12:00:00"},
    ]
    out = _acquisition(customers, date(2026, 6, 1), date(2026, 6, 30))
    assert out["new_customers"] == 4  # ZZ-A1 + ZZ-A2 + ZZ-C + ZZ-D, not ZZ-B
    assert out["total_customers"] == 5


def test_customer_acquisition_first_day_alone_still_catches_the_early_signup():
    """The single-day range that the naive-midnight bound always missed."""
    customers = [{"customer_id": "ZZ-A", "created_at": datetime(2026, 5, 31, 19, 0)}]
    out = _acquisition(customers, date(2026, 6, 1), date(2026, 6, 1))
    assert out["new_customers"] == 1


class _RealGetDb:
    """reports.promotions_report reads `get_db().db`."""

    def __init__(self, db):
        self.db = db


def _promotions(apps, start, end):
    db = _DB(promo_applications=apps)
    with _PatchAttrs(reports_mod, get_db=lambda: _RealGetDb(db)):
        return asyncio.run(
            reports_mod.promotions_report(
                start_date=start,
                end_date=end,
                store_id=None,
                current_user=dict(ADMIN),
            )
        )


def _promo_app(applied_at):
    return {
        "order_id": "ZZ-O1",
        "applied_at": applied_at,
        "total_discount_given": 100.0,
        "net_margin_after_promo": -100.0,
        "applied_promos": [{"promo_id": "ZZ-PR1", "name": "ZZ Promo"}],
    }


def test_promotions_report_bounds_are_ist_days_in_the_stored_string_frame():
    """applied_at is a NAIVE-UTC ISO STRING (promotions.py _now_iso), so the
    string bound is the right SHAPE -- the frame was the bug. A promo fired at
    01:00 IST on 1 July ('2026-06-30T19:30:00') belongs to 1 July, not June."""
    app = _promo_app("2026-06-30T19:30:00")
    july = _promotions([app], "2026-07-01", "2026-07-01")
    assert july["summary"]["promos_fired"] == 1
    june = _promotions([app], "2026-06-01", "2026-06-30")
    assert june["summary"]["promos_fired"] == 0


def test_promotions_report_afternoon_row_stays_in_its_day_positive_control():
    app = _promo_app("2026-06-30T12:00:00")
    june = _promotions([app], "2026-06-30", "2026-06-30")
    assert june["summary"]["promos_fired"] == 1


# ===========================================================================
# 6c. THE LEADERBOARD TWINS must agree
# ===========================================================================


def _both_leaderboards(orders, now=EARLY):
    """Run analytics_v2 /staff-leaderboard and payroll /commission/leaderboard
    over the SAME orders at the SAME frozen instant, period=month."""
    ist_now = _ist_now_of(now)
    db1 = _DB(orders=orders)
    with _PatchAttrs(analytics_v2_mod, _get_db=lambda: db1, now_ist=lambda: ist_now):
        a = asyncio.run(
            analytics_v2_mod.staff_leaderboard(
                store_id=STORE, period="month", current_user=dict(ADMIN)
            )
        )
    db2 = _DB(orders=orders)
    with _PatchAttrs(payroll_mod, _get_db=lambda: db2, now_ist=lambda: ist_now):
        p = asyncio.run(
            payroll_mod.get_commission_leaderboard(
                period="month", store_id=STORE, current_user=dict(ADMIN)
            )
        )
    return (
        {e["staff_id"] for e in a["leaderboard"]},
        {e["staff_id"] for e in p["leaderboard"]},
    )


def test_staff_leaderboard_agrees_with_its_payroll_twin_at_ist_month_open():
    """BOUND rule -- and the requirement IS the agreement. At 01:30 IST on
    1 July the box clock still says 30 June: the old analytics window started
    the month at UTC midnight while payroll started it at IST midnight, so the
    two leaderboards ranked different orders. Both must now see exactly the
    staff who sold in the IST month of July."""
    orders = [
        {
            "order_id": "ZZ-O-JUL",
            "store_id": STORE,
            "status": "COMPLETED",
            "salesperson_id": "ZZ-S-JUL",
            "salesperson_name": "July Seller",
            "created_at": datetime(2026, 6, 30, 19, 0),  # 00:30 IST 1 July
            "total_amount": 9000.0,
            "items": [],
        },
        {
            "order_id": "ZZ-O-JUN",
            "store_id": STORE,
            "status": "COMPLETED",
            "salesperson_id": "ZZ-S-JUN",
            "salesperson_name": "June Seller",
            "created_at": AFTERNOON,  # 16:30 IST 30 June
            "total_amount": 7000.0,
            "items": [],
        },
    ]
    analytics_ids, payroll_ids = _both_leaderboards(orders)
    assert analytics_ids == payroll_ids == {"ZZ-S-JUL"}


def test_staff_leaderboard_afternoon_month_positive_control():
    """At 16:30 IST on 30 June both frames agree the month is June, and both
    leaderboards must include the June order."""
    orders = [
        {
            "order_id": "ZZ-O-JUN",
            "store_id": STORE,
            "status": "COMPLETED",
            "salesperson_id": "ZZ-S-JUN",
            "salesperson_name": "June Seller",
            "created_at": datetime(2026, 6, 10, 11, 0),
            "total_amount": 7000.0,
            "items": [],
        }
    ]
    analytics_ids, payroll_ids = _both_leaderboards(orders, now=AFTERNOON)
    assert analytics_ids == payroll_ids == {"ZZ-S-JUN"}


# ===========================================================================
# 6d. FALLBACK LEGS
# ===========================================================================


def _recon_rows(sessions, from_day, to_day):
    db = _DB(cash_register_sessions=sessions)
    with _PatchAttrs(finance_mod, _get_db=lambda: db):
        out = asyncio.run(
            finance_mod.cash_reconciliation_summary(
                store_id=None,
                from_date=from_day,
                to_date=to_day,
                current_user=dict(ADMIN),
            )
        )
    return out["rows"]


def _cr_session(closed_at):
    return {
        "session_id": "ZZ-CR-1",
        "store_id": STORE,
        "status": "CLOSED",
        "opening_float": 1000.0,
        "expected": 5000.0,
        "counted": 5000.0,
        "variance": 0.0,
        "variance_status": "BALANCED",
        "closed_at": closed_at,
        "opened_at": closed_at,
    }


def test_drawer_recon_files_a_past_midnight_close_under_the_ist_day():
    """Parse-then-shift on a naive-UTC ISO string (the _credit_note_date_ist
    shape): a till closed 01:00 IST on 1 July ('2026-06-30T19:30:00') is a
    1-JULY business day. The [:10] slice filed it under 30 June and the
    operator's 1-July query never found it."""
    s = _cr_session("2026-06-30T19:30:00")
    assert len(_recon_rows([s], "2026-07-01", "2026-07-01")) == 1
    assert _recon_rows([s], "2026-06-30", "2026-06-30") == []


def test_drawer_recon_afternoon_close_keeps_its_day_positive_control():
    s = _cr_session("2026-06-30T12:00:00")
    assert len(_recon_rows([s], "2026-06-30", "2026-06-30")) == 1


def test_hr_leave_applied_day_is_the_ist_day():
    """hr.py writes applied_at as datetime.now().isoformat() == naive-UTC.
    An employee who applied at 01:00 IST on 1 July must see 1 July."""
    assert hr_mod._applied_day("2026-06-30T19:30:00") == "2026-07-01"
    assert hr_mod._applied_day("2026-06-30T12:00:00") == "2026-06-30"  # control
    assert hr_mod._applied_day("") == ""
    assert hr_mod._applied_day("garbage-value") == "garbage-va"  # old fallback


def test_rx_validity_created_at_leg_shifts_to_ist():
    """_rx_validity's LEGACY leg only: created_at (a stored UTC instant)
    shifts +5:30, so an eye test at 04:00 IST on 1 July 2026 expires 1 July
    2027 -- the raw leg said 30 June 2027, a day early."""
    expiry, _valid = prescriptions_mod._rx_validity(
        {"created_at": datetime(2026, 6, 30, 22, 30)}
    )
    assert expiry.date() == date(2027, 7, 1)


def test_rx_validity_business_date_leg_is_not_shifted_positive_control():
    """prescription_date is an operator-typed IST business date -- shifting it
    would CREATE the bug."""
    expiry, _valid = prescriptions_mod._rx_validity({"prescription_date": "2026-06-30"})
    assert expiry.date() == date(2027, 6, 30)


def test_rx_validity_compares_against_the_ist_clock():
    """Validity is 'has the IST expiry moment passed', not the UTC box's
    opinion 5h30m behind it."""
    rx = {"prescription_date": "2026-06-30", "validity_months": 12}
    with _PatchAttrs(
        prescriptions_mod, now_ist_naive=lambda: datetime(2027, 7, 1, 1, 0)
    ):
        _expiry, valid = prescriptions_mod._rx_validity(rx)
    assert valid is False  # 01:00 IST on 1-Jul-2027 is past the 30-Jun expiry
    with _PatchAttrs(
        prescriptions_mod, now_ist_naive=lambda: datetime(2027, 6, 29, 23, 0)
    ):
        _expiry, valid = prescriptions_mod._rx_validity(rx)
    assert valid is True  # positive control: the night before, still valid


class _RxRepo:
    def __init__(self, rx):
        self.rx = rx

    def find_by_id(self, _rid):
        return dict(self.rx)


def test_validate_advisory_agrees_with_rx_validity_incl_month_end():
    """The advisory's old private fork raised ValueError on 31-Jan + 1 month
    and silently reported expired=False. It now delegates to _rx_validity
    (day-clamped months, IST frame), so a month-end Rx past its validity IS
    reported expired."""
    rx = {
        "prescription_id": "ZZ-RX-ME",
        "store_id": STORE,
        "prescription_date": "2026-01-31",
        "validity_months": 1,
    }
    with _PatchAttrs(
        prescriptions_mod,
        get_prescription_repository=lambda: _RxRepo(rx),
        can_access_store_scoped=lambda *_a, **_k: True,
        now_ist_naive=lambda: datetime(2026, 3, 15, 12, 0),
    ):
        out = asyncio.run(
            prescriptions_mod.validate_prescription("ZZ-RX-ME", current_user=dict(ADMIN))
        )
    assert out["expired"] is True
    # And it must MATCH the family-view computation for the same rx.
    with _PatchAttrs(
        prescriptions_mod, now_ist_naive=lambda: datetime(2026, 3, 15, 12, 0)
    ):
        _expiry, valid = prescriptions_mod._rx_validity(rx)
    assert out["expired"] == (not valid)


def test_clinical_rx_card_date_is_the_ist_day():
    """The date printed on the patient's Rx card (clinical.py third renderer).
    Stored instants shift; business-date strings and frame-less naive strings
    pass through by documented design."""
    rd = clinical_mod._rx_date
    assert rd({"created_at": EARLY}) == "01 Jul 2026"  # BSON datetime
    assert rd({"created_at": "2026-06-30T20:00:00Z"}) == "01 Jul 2026"  # aware
    assert rd({"created_at": AFTERNOON}) == "30 Jun 2026"  # control
    assert rd({"prescription_date": "2026-06-30"}) == "30 Jun 2026"  # business
    # naive ISO string: no reliable frame -> unshifted (documented pass-through)
    assert rd({"created_at": "2026-06-30T20:00:00"}) == "30 Jun 2026"


def test_rtv_fy_label_still_correct_after_dead_fallback_deletion():
    """rtv_debit_note.financial_year_label lost its unreachable duplicate of
    fy_start_year_ist; the label itself must keep working on both sides of
    the IST 1-April boundary."""
    assert rtv_mod.financial_year_label(_ist_now_of(FY_EVE)) == "2026-27"
    assert (
        rtv_mod.financial_year_label(_ist_now_of(datetime(2026, 3, 31, 11, 0)))
        == "2025-26"
    )
