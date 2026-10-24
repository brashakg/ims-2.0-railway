"""Per-entity GST reconciliation, the month-end cross-check and its sign-off.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from calendar import monthrange
from ...utils.ist import now_ist, ist_month_window_utc
from typing import Optional
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from ..auth import get_current_user
from ...dependencies import user_store_scope
from ...services.org_validation import shop_gstins
from ._shared import (
    real_sale_match,
    _customer_state_map,
    _get_db,
    _iso_now,
    _order_is_interstate,
    _require_org_finance,
    _store_maps,
    _store_state_map,
    gst_reconciliation,
    logger,
    router,
)

# === GST Reconciliation (per entity) + Tally export ===


def gst_period(month: Optional[int], year: Optional[int]) -> tuple:
    """(month, year) of a GST view, the current IST month by default; 400 for
    a month that is not 1-12 or a year outside 2017-2100 (LOW 11)."""
    now = now_ist()
    m = now.month if month is None else month
    y = now.year if year is None else year
    if not (1 <= int(m) <= 12 and 2017 <= int(y) <= 2100):
        raise HTTPException(status_code=400, detail="month must be 1-12 and year a real year")
    return int(m), int(y)


def gstin_groups(store_ids, s2e: dict, s2g: dict) -> dict:
    """{(entity_id, gstin): [shops]} -- one GSTR-3B return per company + GSTIN
    (H2), in the order the shops come. A shop with no GSTIN files none; such
    shops share one group per company."""
    groups: dict = {}
    for sid in store_ids:
        groups.setdefault((s2e.get(sid), s2g.get(sid, "")), []).append(sid)
    return groups


_REGISTRATION_REFUSAL = (
    "GST figures cannot be calculated because a registration is missing or ambiguous. "
    "Accounts must correct the shop/entity GST registration or the supplier bill's "
    "receiving registration, then retry."
)


def _unavailable(exc=None):
    err = HTTPException(
        status_code=503,
        detail="The GST figures could not be read for every shop; please retry.",
    )
    if exc is not None:
        logger.exception("GST by registration could not be read")
    return err


def gst_by_registration(db, m: int, y: int, scope: Optional[str],
                        entity_id: Optional[str] = None) -> tuple:
    """THE Finance GST figures, one row per GST registration, read off each
    GSTIN's GSTR-3B return (reports._compute_gstr3b, H2) -- the table AND the
    summary cards above it (M3), so neither keeps a rule of its own.

    `scope` is the caller's shop by the one shop rule (None = every shop, an
    admin). A GSTIN that also files for shops outside the scope shows its
    outward tax for the scope's shops only and NO input-side figure (credit,
    held-back credit, reverse charge, cash): those are the whole GSTIN's (M4).

    Returns (rows, warnings). Refused (409, owner-approved A04 wording)
    rather than guessed only where a DATED bill of this month is missing
    from this month's return (no company, a GSTIN that is no shop's, an
    unproven legacy allocation, tax without heads -- gst_itc._itc_unplaced),
    a shop with sales and no GSTIN, or (all shops) sales booked to a shop
    that does not exist. Bills no month can hold (undated) and bills GSTR-3B
    already leaves out (an unregistered supplier's with credit left on) are
    a warning, not a refusal (review round 2). 503 when a source cannot be
    read -- never a zero figure (M7)."""
    from ..reports import _compute_gstr3b, _itc_unplaced

    try:
        stores = list(db.get_collection("stores").find({}, {"_id": 0, "store_id": 1, "entity_id": 1}))
        enames = {e.get("entity_id"): e.get("name") for e in db.get_collection("entities").find(
            {}, {"_id": 0, "entity_id": 1, "name": 1})}
        s2g = shop_gstins(db)
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc
    s2e = {st["store_id"]: st.get("entity_id") for st in stores if st.get("store_id")}
    store_ids = [sid for sid in s2e if not entity_id or s2e[sid] == entity_id]
    if scope:
        store_ids = [scope] if scope in store_ids else []
    period = f"{y:04d}-{m:02d}"
    rows = []
    try:
        for (eid, gstin), shops in gstin_groups(store_ids, s2e, s2g).items():
            ret = _compute_gstr3b(period, shops[0], shops=shops, db=db)
            out = ret["outwardTaxableSupplies"]
            hidden = sum(1 for sid, g in s2g.items()
                         if gstin and g == gstin and s2e.get(sid) == eid and sid not in shops)

            def head_sum(block):
                return round(sum(float((block or {}).get(k) or 0)
                                 for k in ("integratedTax", "centralTax", "stateTax")), 2)

            credit = head_sum(ret["itcAvailable"])
            collected = head_sum(out)
            if not gstin and collected:
                raise HTTPException(status_code=409, detail=_REGISTRATION_REFUSAL)
            rows.append({
                "entity_id": eid or "_unassigned",
                "entity_name": enames.get(eid, eid) or "No company",
                "gstin": gstin,
                "shops": len(shops),
                "gst_collected": collected,
                "cgst": round(float(out.get("centralTax") or 0), 2),
                "sgst": round(float(out.get("stateTax") or 0), 2),
                "igst": round(float(out.get("integratedTax") or 0), 2),
                "shops_outside_view": hidden,
                # The whole GSTIN's: withheld when it files for shops outside
                # this view (M4) -- never part of a return shown as the whole.
                **{key: (None if hidden else value) for key, value in {
                    "input_credit": credit,
                    "input_credit_excluded": round(head_sum(ret["itcIneligible"]) + head_sum(ret["itcBlocked"]), 2),
                    "reverse_charge": head_sum(ret["inwardSuppliesReverseCharge"]),
                    "net_payable": head_sum(ret["taxPaidCash"]),
                    "credit_carried_forward": round(max(0.0, credit - head_sum(ret["itcUtilized"])), 2),
                }.items()},
            })
        last_day = monthrange(y, m)[1]
        unplaced = _itc_unplaced(db, y, m, last_day, s2e.get(scope) if scope else entity_id,
                                 shops=store_ids if scope else None)
        stray = 0 if scope else db.get_collection("orders").count_documents({
            "created_at": {"$gte": ist_month_window_utc(y, m)[0], "$lt": ist_month_window_utc(y, m)[1]},
            **real_sale_match(db),
            "store_id": {"$nin": list(s2e)},
        })
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - one shop short is a wrong total
        raise _unavailable(exc) from exc
    if unplaced.get("failed"):
        raise _unavailable()
    undated = set(unplaced.get("undated_bill_numbers") or [])
    excluded = set((unplaced.get("unregistered") or {}).get("excluded_bill_numbers") or [])
    if set(unplaced.get("bill_numbers") or []) - undated - excluded or stray:
        raise HTTPException(status_code=409, detail=_REGISTRATION_REFUSAL)
    warnings = []
    if undated:
        warnings.append(f"{len(undated)} bill(s) need a date written as YYYY-MM-DD - no month's "
                        f"return can count them: {', '.join(sorted(map(str, undated)))}. Fix them.")
    if excluded - undated:
        warnings.append(f"{len(excluded - undated)} bill(s) from a supplier with no valid GSTIN have "
                        f"credit left on and are excluded from GSTR-3B: "
                        f"{', '.join(sorted(map(str, excluded - undated)))}. Fix them.")
    rows.sort(key=lambda r: (-r["gst_collected"], r["entity_name"], r["gstin"]))
    return rows, warnings


@router.get("/gst/reconciliation")
async def get_gst_reconciliation(
    month: Optional[int] = None,
    year: Optional[int] = None,
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """The Finance GST tab's table: one row per GST registration (company +
    GSTIN), each its GSTR-3B return (gst_by_registration -- the cards above
    read the same rows). Output tax net of credit notes; input credit from
    booked supplier bills placed on the GSTIN they were received on, leaving
    out credit switched off / not claimable (section 17(5)), less supplier
    credit notes in their note's month; reverse charge; cash due after
    set-off.

    It used to take input credit from purchase-ORDER tax pooled per company,
    so 'net payable' could be wrong (audit A04-3). You file the actual returns
    through Tally; this is the cross-check."""
    # Entity-grouped GST reconciliation is a filing/accounting view --
    # finance-admin only (owner decision 2026-06-16), so a single-store
    # STORE_MANAGER/AREA_MANAGER cannot read any entity's GST position.
    scope = _require_org_finance(current_user)
    m, y = gst_period(month, year)
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    # THE shop rule the GST summary cards above this table read
    # (resolve_store_scope, via _require_org_finance): ADMIN / SUPERADMIN
    # every shop, an accountant his own shop -- so one tab never shows one
    # login two scopes (a Pune accountant read Pune's input credit on the
    # cards and every entity's in this table, round 4).
    rows, warnings = gst_by_registration(db, m, y, scope, entity_id)

    def total(key):
        if any(r[key] is None for r in rows):
            return None
        return round(sum(r[key] for r in rows), 2)

    return {
        "registrations": rows,
        # Bills the return cannot count that need fixing (never a refusal).
        "warnings": warnings,
        "total_collected": total("gst_collected"),
        "total_input_credit": total("input_credit"),
        "total_reverse_charge": total("reverse_charge"),
        "total_net_payable": total("net_payable"),
        "month": m,
        "year": y,
        "note": (
            "One row per GST registration, as its GSTR-3B: output tax after "
            "credit notes, input credit from booked supplier bills (credit not "
            "claimable left out, supplier credit notes taken off in their "
            "note's month), cash due after set-off plus reverse charge. File "
            "via Tally."
        ),
    }


# ---------------------------------------------------------------------------
# GST cross-check (accountant month-end sign-off)
# ---------------------------------------------------------------------------
# The accountant's reconciliation aid: for a chosen (month, entity) it lays the
# IMS GSTR-1 / GSTR-3B numbers SIDE BY SIDE against the books (orders, payments,
# Tally sales-JV totals, purchase-side ITC) so they can confirm everything
# agrees before filing, then record a CHECKED sign-off with notes. The tax math
# is entirely REUSED (reports._compute_gstr1 / _compute_gstr3b and the finance
# order aggregations); this layer only compares + audits. It does NOT lock the
# period -- that is the separate /period-lock action.

_GST_CROSSCHECK_SIGNOFFS = "gst_crosscheck_signoffs"


class GstCrossCheckSignoff(BaseModel):
    month: int = Field(..., ge=1, le=12)
    year: int = Field(..., ge=2000, le=2100)
    entity_id: Optional[str] = None
    note: Optional[str] = None
    # What the CLIENT claims it saw. The server RECOMPUTES the cross-check at
    # sign-off and records its OWN mismatch_count / gst_payable as the
    # authoritative audit snapshot; these client values are kept only for drift
    # forensics and must never be trusted as the record of what was signed off.
    mismatch_count: Optional[int] = None
    gst_payable: Optional[float] = None


def _gst_month_window(y: int, m: int):
    """IST-calendar-month -> naive-UTC created_at bounds [start, end). Same
    framing as /gst/summary and /gst/reconciliation so every GST view agrees
    on which invoices belong to the period."""
    start, end = ist_month_window_utc(y, m)
    return start, end


def _cancelled_behind_credit_note(db, store_ids, start, end, store_states, customer_states) -> dict:
    """Invoices the refund-advance door cancelled behind a credit note for the
    whole invoice (PR #1264). GSTR-1 / GSTR-3B keep filing them (their note
    reverses them -- utils.real_sale gst_documents view) while the books drop
    every cancelled order, so build_crosscheck adds them back to Books / Tally
    like the deemed supply. Same field expressions as the books leg above."""
    from ...utils.real_sale import is_real_sale

    match = {
        "created_at": {"$gte": start, "$lt": end},
        "status": {"$in": ["CANCELLED", "CANCELED", "cancelled", "canceled"]},
        "advance_refund_status": "COMPLETED",
    }
    if store_ids is not None:
        match["store_id"] = {"$in": store_ids}
    out = {"count": 0, "taxableValue": 0.0, "tax": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0}
    for o in db.get_collection("orders").find(match):
        if not is_real_sale(o, gst_documents=True):
            continue
        grand = float(o.get("grand_total") or o.get("total") or 0)
        tax = float(o.get("tax_amount") or o.get("tax_total") or 0)
        out["count"] += 1
        out["taxableValue"] += grand - float(o.get("round_off") or 0) - tax
        out["tax"] += tax
        if _order_is_interstate(o, store_states, customer_states):
            out["igst"] += tax
        else:
            cgst, sgst = _jv_cgst_sgst_split(tax)
            out["cgst"] += cgst
            out["sgst"] += sgst
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in out.items()}


def _books_and_tally_for_stores(db, store_ids, start, end) -> tuple:
    """Books + Tally-JV totals for a store set over a created_at window.

    Returns (books, tally). books = {sales_taxable, sales_tax,
    sales_grand_total, payments_collected}; tally = {taxable, tax, cgst, sgst,
    igst}. Reuses the SAME field expressions and intra/inter-state split as the
    finance /gst/summary and /tally/sales-jv endpoints -- no new tax math."""
    o_match = {
        "created_at": {"$gte": start, "$lt": end},
        **real_sale_match(db),
    }
    if store_ids is not None:
        o_match["store_id"] = {"$in": store_ids}

    store_states = _store_state_map(db)
    customer_states = _customer_state_map(db)

    sales_grand = 0.0
    sales_tax = 0.0
    # Bill round off (owner ruling 2026-10-08): in the gross, not in taxable.
    sales_round_off = 0.0
    payments_collected = 0.0
    t_cgst = t_sgst = t_igst = 0.0

    # A routed online order held on its seller (GSTIN) check is in neither
    # leg: GSTR-1/3B do not file it and the Tally sales JV does not export it
    # (tally._filed_orders -- the SAME filter), so the books compared against
    # them must not count it either.
    from .tally import _filed_orders

    cursor = db.get_collection("orders").find(
        o_match,
        {
            "_id": 0,
            "store_id": 1,
            "customer_id": 1,
            "grand_total": 1,
            "total": 1,
            "round_off": 1,
            "tax_amount": 1,
            "tax_total": 1,
            "payments": 1,
            # OS-008: the order-carried inter-state flag (online orders).
            "interstate": 1,
            "place_of_supply": 1,
            "place_of_supply_assumed": 1,
            "fulfillment_route": 1,
            "fulfillment_hold": 1,
            "stock_hold_reason": 1,
        },
    )
    for o in _filed_orders(db, cursor):
        grand = float(o.get("grand_total") or o.get("total") or 0)
        tax = float(o.get("tax_amount") or o.get("tax_total") or 0)
        sales_grand += grand
        sales_tax += tax
        sales_round_off += float(o.get("round_off") or 0)
        # OS-008: the order's own interstate flag wins (online orders carry it);
        # store-vs-customer state stays the fallback for docs without it.
        if _order_is_interstate(o, store_states, customer_states):
            t_igst += tax
        else:
            cgst, sgst = _jv_cgst_sgst_split(tax)
            t_cgst += cgst
            t_sgst += sgst
        for p in o.get("payments") or []:
            try:
                payments_collected += float(p.get("amount", 0) or 0)
            except (TypeError, ValueError):
                pass

    books = {
        "sales_grand_total": round(sales_grand, 2),
        "sales_tax": round(sales_tax, 2),
        "sales_taxable": round(sales_grand - sales_round_off - sales_tax, 2),
        "payments_collected": round(payments_collected, 2),
        "cancelled_behind_credit_note": _cancelled_behind_credit_note(
            db, store_ids, start, end, store_states, customer_states),
    }
    tally = {
        "taxable": round(sales_grand - sales_round_off - sales_tax, 2),
        "tax": round(sales_tax, 2),
        "cgst": round(t_cgst, 2),
        "sgst": round(t_sgst, 2),
        "igst": round(t_igst, 2),
    }
    return books, tally


def _run_gst_cross_check(
    db,
    m: int,
    y: int,
    entity_id: Optional[str],
    shop: Optional[str] = None,
    reach: Optional[set] = None,
) -> dict:
    """Compute the full GST cross-check payload for (month, year, entity).

    Shared by the GET (display) and the POST sign-off, which RECOMPUTES
    server-side so the durable audit snapshot is the SERVER's figure, never the
    client's claim. Raises 503 when the DB is unreachable and 404 when a named
    entity resolves to no stores (which would otherwise render every source as
    0.00 -- a false 'all sources reconcile' green screen). Returns the result
    WITHOUT the sign-off marker (the GET attaches that separately).

    `shop` (owner 2026-10-18): the check covers that shop's GSTIN filing --
    every shop on its GSTIN when `reach` (the caller's shops; None = every
    shop, an admin) covers them all. When it does not (BV-PUN-01 and
    BV-ONLINE-01 share one GSTIN), the check covers the shop alone and the
    GSTR-3B input credit / net cash -- filed for the whole GSTIN -- are
    withheld: the books side only, no verdict, no company-wide figure ("admin
    view"). Unplaced credit is the covered shops' bills only. A named entity
    the shop is not part of has no stores here (404)."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    from ...services import gst_crosscheck as _xc
    from ..reports import _compute_gstr1, _compute_gstr3b, _itc_unplaced

    period = f"{y:04d}-{m:02d}"

    s2e, enames = _store_maps(db)
    # THE shop's GSTIN (the one GSTR-3B's scope files on), so a report's
    # GSTIN-bound slice is counted once per filing. Unreadable -> the ITC
    # leg is dead (no sign-off), never a silent per-store sum.
    try:
        s2g = shop_gstins(db)
        gstins_failed = False
    except Exception:  # noqa: BLE001
        logger.exception("cross-check: shop GSTINs unreadable")
        s2g, gstins_failed = {}, True
    gstin_note = None
    scope_ids = None
    if shop:
        gstin = None if gstins_failed else s2g.get(shop)
        group = sorted(sid for sid, g in s2g.items() if gstin and g == gstin) or [shop]
        whole = not gstins_failed and (reach is None or all(s in reach for s in group))
        scope_ids = set(group if whole else [shop])
        if not whole:
            gstin_note = (
                "GSTR-3B input credit, reverse charge and net cash are filed for "
                "the whole GSTIN, which other shops share - admin view. This "
                "check shows your shop's books only."
            )
    if entity_id or shop:
        store_ids = [
            sid
            for sid, eid in s2e.items()
            if (not entity_id or eid == entity_id) and (scope_ids is None or sid in scope_ids)
        ]
        if not store_ids:
            raise HTTPException(
                status_code=404, detail="No stores found for this entity"
            )
    else:
        store_ids = list(s2e.keys())
        # HR-2: an empty store map (wiped foundation data or a transient stores
        # query failure) would otherwise render every source 0.00 and sign off a
        # false all-entities "all sources reconcile" green -- the same failure
        # mode the named-entity 404 guards. Refuse the unscoped figure.
        if not store_ids:
            raise HTTPException(
                status_code=404, detail="No stores configured - nothing to reconcile"
            )

    # Reuse the per-store GST-return computations (single source of truth for
    # the tax math), then aggregate to the entity level in the pure service. A
    # per-store compute failure is tracked (not silently dropped) so the screen
    # can flag partial data instead of presenting understated GSTR totals as a
    # tax break, and so sign-off can be blocked.
    g1_reports, g3_reports, g3_entities, g3_gstins = [], [], [], []
    failed: set = set()
    for sid in store_ids:
        try:
            g1_reports.append(_compute_gstr1(period, sid))
        except Exception:  # noqa: BLE001 -- one bad store must not sink the view
            logger.exception("cross-check: GSTR-1 compute failed for %s", sid)
            failed.add(sid)
    # GSTR-3B is a per-GSTIN return (H2): one per company + GSTIN, over the
    # shops of that GSTIN this check covers.
    for (eid, gstin), shops in gstin_groups(store_ids, s2e, s2g).items():
        try:
            g3_reports.append(_compute_gstr3b(period, shops[0], shops=shops, db=db))
            g3_entities.append(eid)
            g3_gstins.append(gstin)
        except Exception:  # noqa: BLE001
            logger.exception("cross-check: GSTR-3B compute failed for %s", gstin or shops)
            failed.update(shops)

    gstr1 = _xc.aggregate_gstr1(g1_reports)
    # Company-wide ITC / RCM are identical for every sibling store, so pass the
    # entity per store report -- aggregate_gstr3b counts them ONCE per entity.
    # GSTIN-bound ITC (every bill received on a GSTIN, R1) is counted ONCE per
    # GSTIN, making a multi-GSTIN entity's ITC independent of store enumeration
    # order and never counting one bill on two registrations.
    gstr3b = _xc.aggregate_gstr3b(g3_reports, g3_entities, g3_gstins)

    start, end = _gst_month_window(y, m)
    books, tally = _books_and_tally_for_stores(db, store_ids, start, end)

    # Purchase-side ITC (from purchase_orders) as an INDEPENDENT cross-check
    # against GSTR-3B's vendor-bills ITC. Reuse gst_reconciliation()'s math.
    itc_leg_failed = False
    try:
        # purchase_orders.created_at is a BSON datetime (BaseRepository
        # _add_timestamps); no writer ever set a `date` field, so the old
        # `date` string filter matched NOTHING and ITC read a permanent 0. Use
        # the SAME IST created_at window as the orders side, and map a PO to an
        # entity via delivery_store_id OR store_id (gst_reconciliation's mapping).
        # HR-3: exclude DRAFT / CANCELLED POs (auto-draft POs are tax-less, but a
        # human DRAFT or CANCELLED PO with tax_amount would inflate books ITC and
        # flag a phantom mismatch) -- match the case variants PO writers write.
        p_match: dict = {
            "created_at": {"$gte": start, "$lt": end},
            "status": {"$nin": ["DRAFT", "draft", "CANCELLED", "cancelled"]},
        }
        if entity_id or shop:
            p_match["$or"] = [
                {"delivery_store_id": {"$in": store_ids}},
                {"store_id": {"$in": store_ids}},
            ]
        purchases = list(
            db.get_collection("purchase_orders").find(
                p_match,
                {"_id": 0, "delivery_store_id": 1, "store_id": 1, "tax_amount": 1},
            )
        )
        recon = gst_reconciliation(
            [],
            purchases,
            s2e,
            enames,
            store_state_by_id=_store_state_map(db),
            customer_state_by_id=_customer_state_map(db),
        )
        if entity_id:
            match = next(
                (e for e in recon["entities"] if e["entity_id"] == entity_id), None
            )
            books["input_credit"] = match["input_credit"] if match else 0.0
        else:
            books["input_credit"] = recon.get("total_input_credit", 0.0)
    except Exception:  # noqa: BLE001
        logger.exception("cross-check: purchase-side ITC failed")
        # HR-1: a dead ITC leg must not sign off green. input_credit=None renders
        # the ITC row INFO (one source), which the mismatch_count silently
        # ignores; flag it so the sign-off gate can block (409) and the record
        # is stamped for forensics.
        books["input_credit"] = None
        itc_leg_failed = True

    # Booked credit that no GSTIN's return counts (no company, no tax heads,
    # or a GSTIN that is no shop's): a row of its own, so a month with credit
    # missing from GSTR-3B can never read green.
    unplaced = _itc_unplaced(
        db,
        y,
        m,
        monthrange(y, m)[1],
        entity_id or (s2e.get(shop) if shop else None),
        shops=store_ids if shop else None,
    )
    # A bill read that failed is a dead ITC leg too (HR-1): no sign-off.
    itc_leg_failed = itc_leg_failed or gstins_failed or bool(unplaced.get("failed"))
    result =_xc.build_crosscheck(gstr1, gstr3b, books, tally, unplaced=unplaced)
    result.update(
        {
            "month": m,
            "year": y,
            "period": period,
            "entity_id": entity_id,
            "entity_name": enames.get(entity_id) if entity_id else "All entities",
            "store_id": shop,
            "store_count": len(store_ids),
            "stores_computed": len(store_ids) - len(failed),
            "failed_store_ids": sorted(failed),
            "partial": bool(failed),
            "itc_leg_failed": itc_leg_failed,
            "itc_unplaced": unplaced,
            "gstr1": {
                "totalTaxableValue": gstr1["totalTaxableValue"],
                "totalTax": gstr1["totalTax"],
                "cgst": gstr1["cgst"],
                "sgst": gstr1["sgst"],
                "igst": gstr1["igst"],
            },
            "gstr3b": {
                "outwardTaxableValue": gstr3b["outwardTaxableValue"],
                "outwardTax": gstr3b["outwardTax"],
                "itc": gstr3b["itc"],
                "netCash": gstr3b["netCash"],
                "rcm": gstr3b["rcm"],
            },
            "books": books,
            "tally": tally,
            "gstin_note": gstin_note,
        }
    )
    if gstin_note:
        # The GSTIN-wide figures are withheld: the books side only, no verdict.
        for i, row in enumerate(result["comparisons"]):
            if row["metric"] in ("Input tax credit (ITC)", "Net GST payable (cash)"):
                result["comparisons"][i] = {
                    **row,
                    "sources": {k: v for k, v in row["sources"].items() if k.startswith("Books")},
                    "variance": 0.0,
                    "status": "INFO",
                    "note": gstin_note,
                }
        result["gstr3b"].update(itc=None, netCash=None, rcm=None)
        broken = [r["metric"] for r in result["comparisons"] if r["status"] == "MISMATCH"]
        result["summary"].update(
            mismatch_count=len(broken),
            mismatch_metrics=broken,
            all_matched=not broken,
            gst_payable=None,
        )
    return result


@router.get("/gst/cross-check")
async def get_gst_cross_check(
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    entity_id: Optional[str] = None,
    store_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Accountant GST cross-check for a (month, entity): GSTR-1 / GSTR-3B vs the
    books (orders / payments / Tally / purchase-side ITC), side by side, with
    per-rate breakup, CDNR + deemed-supply detail, and mismatch flags. A review
    aid only -- it changes no figure and locks no period. An accountant's
    check covers his own shop -- `store_id` picks which of his shops (owner
    2026-10-18); an admin may name a shop too."""
    shop = _require_org_finance(current_user, store_id, entity_id)
    every_shop, mine = user_store_scope(current_user)
    db = _get_db()

    now = now_ist()
    m = month or now.month
    y = year or now.year

    result = _run_gst_cross_check(
        db, m, y, entity_id, shop, None if every_shop else set(mine)
    )

    # Attach the latest sign-off (if any) so the screen shows CHECKED status.
    signoff = None
    try:
        signoff = db.get_collection(_GST_CROSSCHECK_SIGNOFFS).find_one(
            {"year": y, "month": m, "entity_id": entity_id or "_all"}, {"_id": 0}
        )
    except Exception:  # noqa: BLE001
        signoff = None
    result["signoff"] = signoff
    return result


@router.post("/gst/cross-check-signoff")
async def gst_cross_check_signoff(
    body: GstCrossCheckSignoff,
    current_user: dict = Depends(get_current_user),
):
    """Accountant marks a (month, entity) GST cross-check as CHECKED, with notes.

    Idempotent upsert keyed on (year, month, entity_id). Audit-logged. This is a
    review marker only -- it does NOT lock the accounting period (that is the
    separate /period-lock action) and changes no GST figure.

    The sign-off is the COMPANY's month (keyed by entity, one per period): an
    accountant assigned to a shop sees only that shop's check, so his sign-off
    would vouch for figures he was not shown -- company-wide needs an admin
    (owner 2026-10-18)."""
    if _require_org_finance(current_user):
        raise HTTPException(
            status_code=403,
            detail="Signing off the company's GST cross-check needs an admin.",
        )
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    m = int(body.month)
    y = int(body.year)

    # Recompute the cross-check SERVER-SIDE so the durable audit snapshot is the
    # server's figure, never a (forgeable) client-supplied mismatch_count=0.
    server = _run_gst_cross_check(db, m, y, body.entity_id)
    # HR-1: block sign-off when EITHER reconciliation leg is degraded -- store
    # compute failure (understated GSTR totals) or a dead purchase-side ITC leg
    # (ITC row silently drops to INFO and escapes mismatch_count). Name the leg.
    if server.get("partial") or server.get("itc_leg_failed"):
        reasons = []
        if server.get("partial"):
            reasons.append(
                "%d store(s) failed to compute"
                % len(server.get("failed_store_ids") or [])
            )
        if server.get("itc_leg_failed"):
            reasons.append("the purchase-side ITC leg failed to compute")
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot sign off: %s, so the figures are understated. Resolve "
                "and retry." % " and ".join(reasons)
            ),
        )
    server_summary = server.get("summary", {})
    server_mismatch = int(server_summary.get("mismatch_count", 0) or 0)
    server_payable = float(server_summary.get("gst_payable", 0.0) or 0.0)

    key = {
        "year": y,
        "month": m,
        "entity_id": body.entity_id or "_all",
    }
    reviewer = current_user.get("name") or current_user.get("full_name")
    record = {
        **key,
        "checked": True,
        "checked_by": current_user.get("user_id"),
        "checked_by_name": reviewer,
        "checked_at": _iso_now(),
        "note": body.note,
        # Server-recomputed authoritative snapshot.
        "mismatch_count": server_mismatch,
        "gst_payable": server_payable,
        "mismatch_metrics": server_summary.get("mismatch_metrics", []),
        # Forensic marker: both legs computed cleanly (the gate above blocks a
        # sign-off otherwise, so this is always False on a stored record).
        "itc_leg_failed": bool(server.get("itc_leg_failed")),
        # What the client claimed it saw (drift forensics only, never trusted).
        "client_mismatch_count": body.mismatch_count,
        "client_gst_payable": body.gst_payable,
    }

    # Preserve any prior sign-off in a history array instead of silently
    # overwriting another reviewer's record.
    prior = None
    try:
        prior = db.get_collection(_GST_CROSSCHECK_SIGNOFFS).find_one(key, {"_id": 0})
    except Exception:  # noqa: BLE001
        prior = None
    update: dict = {"$set": record}
    if prior:
        # HR-5: keep BOTH drift-forensics figures (server + client gst_payable)
        # and cap the history at the last 50 sign-offs so it cannot grow without
        # bound.
        update["$push"] = {
            "history": {
                "$each": [
                    {
                        k: prior.get(k)
                        for k in (
                            "checked_by",
                            "checked_by_name",
                            "checked_at",
                            "note",
                            "mismatch_count",
                            "gst_payable",
                            "client_mismatch_count",
                            "client_gst_payable",
                        )
                    }
                ],
                "$slice": -50,
            }
        }
    try:
        db.get_collection(_GST_CROSSCHECK_SIGNOFFS).update_one(key, update, upsert=True)
    except Exception:  # noqa: BLE001
        logger.exception("GST cross-check sign-off failed")
        raise HTTPException(
            status_code=500,
            detail="Could not record the sign-off - try again or contact support",
        )

    # Audit (fail-soft; never undoes the sign-off write).
    try:
        from api.dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is not None:
            repo.create(
                {
                    "action": "gst_crosscheck.signoff",
                    "entity_type": "gst_crosscheck",
                    "entity_id": f"{key['year']}-{key['month']:02d}:{key['entity_id']}",
                    "user_id": current_user.get("user_id"),
                    "user_name": reviewer,
                    "severity": "INFO",
                    "source": "finance",
                    "after_state": {
                        "checked": True,
                        "note": body.note,
                        "mismatch_count": server_mismatch,
                        "gst_payable": server_payable,
                        "client_mismatch_count": body.mismatch_count,
                    },
                }
            )
    except Exception:  # noqa: BLE001
        pass

    return {"ok": True, "signoff": record}


def _jv_cgst_sgst_split(tax: float) -> tuple:
    """Split a line's total GST into intra-state CGST + SGST so they sum to the
    tax EXACTLY (the rounding residual goes on SGST). A naive round(tax/2) on both
    sides over-states by a paisa on odd-paise tax (100.01 -> 50.01 + 50.01 =
    100.02), which IMBALANCES the Tally voucher and gets it rejected on import.
    Mirrors orders._build_invoice_gst_split."""
    cgst = round(tax / 2.0, 2)
    sgst = round(tax - cgst, 2)
    return cgst, sgst
