"""POST /orders -- the order create door.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

import secrets
import uuid
from database.repositories.order_repository import derive_bill_type
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, Header
from typing import Any, Dict, List, Optional
from ..auth import get_current_user
from ...dependencies import (
    get_customer_repository,
    get_order_repository,
    get_product_repository,
    get_walkin_counter_repository,
)
from ...services.stores_util import is_online_store
from ._shared import (
    POS_WRITE_ROLES,
    _compute_per_category_gst,
    _get_db,
    logger,
    router,
)
from .pricing import (
    _enforce_line_pricing,
    assert_stack_within_cap,
    effective_line_discount_pct,
)
from .models import OrderCreate
from .rx import _validate_order_line_rx
from .numbering import (
    _order_create_response,
    generate_order_number,
)
from .stock import (
    _assert_serialized_stock_available,
    _canonical_pid,
    _lens_reservation_key,
    _mark_units_sold,
    _resolve_billable_product,
    _resolve_product_doc,
)


@router.post("", status_code=201)
async def create_order(
    order: OrderCreate,
    current_user: dict = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Create new sales order.

    C-5 (DELTA 3): supports an OPTIONAL `Idempotency-Key` request header. When a
    non-empty key is supplied and an order with that key already exists for this
    store, the EXISTING order is returned (same response shape as a fresh
    create) instead of creating a duplicate -- this makes a double-clicked /
    retried "Pay now" safe. The key is persisted on the order doc. Fail-soft:
    with no DB (or no header) the behaviour is identical to before.
    """
    # RBAC: only POS-facing roles may create orders. This was relying on the
    # frontend alone -> ACCOUNTANT / OPTOMETRIST / CATALOG_MANAGER / WORKSHOP_STAFF
    # could all POST an order. Enforce server-side.
    if not any(r in current_user.get("roles", []) for r in POS_WRITE_ROLES):
        raise HTTPException(
            status_code=403, detail="Your role is not permitted to create orders."
        )
    order_repo = get_order_repository()
    customer_repo = get_customer_repository()
    store_id = current_user.get("active_store_id")

    # W1.4 / OS-005 (owner-approved 2026-07-23): an ONLINE store (BV-ONLINE-01 /
    # WO-ONLINE-01) owns no stock, has no till and no walk-ins -- a manual POS
    # sale rung under it would issue a real GST invoice with zero stock
    # movement (the serialized-availability gate never fires because an online
    # store owns no stock_units rows). Reject BEFORE any validation/persist.
    #
    # The guard is UNCONDITIONAL on an ONLINE active store -- there is no
    # source-based carve-out (OrderCreate has no `source` field, and store_id
    # binds ONLY to current_user['active_store_id'], never a request-body
    # override). It is safe precisely because nothing legitimate routes an
    # online sale through here: the Shopify webhook ingest writes order docs
    # directly (shopify_ingest.py -> orders_coll.insert_one) and never calls
    # this route, and online_store_orders is read/remap-only. So the guard
    # over-blocks (an in-store user ringing under an online store) and never
    # under-blocks a real online/Shopify sale.
    if is_online_store(_get_db(), store_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "This is an online store - website orders arrive from Shopify; "
                "in-store billing is disabled. Switch to a physical store to "
                "ring a sale."
            ),
        )
    # Salesperson attribution drives the incentive engine. Prefer the
    # explicit POS picker value; fall back to the logged-in user so older
    # clients (and quick sales) still attribute somewhere.
    # A supplied split OWNS the primary attribution: the largest share is the
    # primary (ties -> the first listed), so salesperson_id and salespersons[]
    # can never tell two different stories to two different readers.
    salespersons_split = None
    if order.salespersons:
        salespersons_split = [
            {
                "salesperson_id": s.salesperson_id,
                "salesperson_name": s.salesperson_name,
                "percent": round(float(s.percent), 2),
            }
            for s in order.salespersons
        ]
        _primary = max(salespersons_split, key=lambda s: s["percent"])
        salesperson_id = _primary["salesperson_id"]
        # _validate_split already folded the picker's name into the entry it
        # names, so the entry is the whole story here.
        picked_name = _primary.get("salesperson_name")
    else:
        salesperson_id = order.salesperson_id or current_user.get("user_id")
        picked_name = order.salesperson_name
    # ONE last-resort fallback for both paths: a split that carries no names
    # must not lose the logged-in user's name that a plain sale would keep.
    salesperson_name = (
        picked_name or current_user.get("full_name") or current_user.get("name")
    )

    # Validate items
    if not order.items:
        raise HTTPException(status_code=400, detail="Order must have at least one item")

    MAX_CART_ITEMS = 15
    if len(order.items) > MAX_CART_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"Cart exceeds maximum of {MAX_CART_ITEMS} items. Split into multiple orders.",
        )

    # Accounting period lock: cannot create orders in a closed month.
    # IST audit: the lock day must be the IST business day, not the UTC day --
    # date.today() on Railway (UTC) is yesterday between 00:00-05:30 IST, which
    # falsely blocked POS orders for 5.5h on the 1st after a month-lock.
    db = _get_db()
    if db is not None:
        from ..finance import check_period_locked
        from ...utils.ist import ist_today

        check_period_locked(db, ist_today())

    # Validate product_ids exist.
    #
    # Audit Run #2 (2026-04-21) blocker: this used to call
    # stock_repo.find_by_id(product_id), which looks in `stock_units`
    # (keyed on stock_id), while the POS catalog + /inventory both
    # serve from `products` (keyed on product_id). Every order-create
    # failed with "Product not found: prod-fr-001". Switched to
    # ProductRepository, and added virtual-id passthroughs for the
    # POS lens configurator ("lens-*"), lens suggestion helper
    # ("lens-sug-*"), and manual custom items ("custom-*").
    product_repo = get_product_repository()
    # ONE resolution per product for the whole create: existence + refusal
    # policy live in _resolve_billable_product (shared with the add-item
    # door), and every later consumer (price snapshot, per-line cap gate,
    # cart-discount stacking, promo-tier stamp) reads THIS dict instead of
    # re-querying -- re-resolving in four places is how the copies drifted.
    _pdoc_by_pid: Dict[str, dict] = {}
    if product_repo is not None:
        for item in order.items:
            pid = item.product_id or ""
            if not pid or pid in _pdoc_by_pid:
                continue
            doc = _resolve_billable_product(product_repo, pid, item.product_name or "")
            if doc is not None:
                _pdoc_by_pid[pid] = doc

    # BUG-005 / BUG-006 (patient-safety): validate every line's clinical Rx
    # powers (range / 0.25 grid / whole-axis / cyl-requires-axis) and require a
    # valid prescription on spectacle-lens / contact-lens lines. Runs BEFORE any
    # money math (validation only -- it never touches pricing/GST/payment) so a
    # clinically impossible lens power or a lens line with no Rx is rejected with
    # a clear 422 before it can be persisted and sent to the lab.
    #
    # INVARIANT (do NOT break on refactor): this gate is order_type-AGNOSTIC. The
    # POS "quick sale" / fast-sale (order_type="quick_sale", which skips the
    # workshop step) posts THIS SAME endpoint, so it MUST run the identical Rx
    # gate -- a quick sale is not a hole through which a spectacle (Rx) lens line
    # reaches the lab without a valid, customer-matching, non-expired Rx. Never
    # wrap this loop in an `if order.order_type != "quick_sale"` (or any sale_type)
    # branch. Pinned by test_order_rx_validation.py::test_quicksale_* .
    for item in order.items:
        # SECURITY (Rx-item_type spoof): resolve the PRODUCT MASTER so the
        # Rx-required decision keys off the product's canonical item_type /
        # category, not the client-supplied item_type (a lens sent as 'FRAME'
        # must still require an Rx). Virtual lens-*/custom-* lines and
        # unresolvable ids have no product doc -> the client value is the
        # fallback. Fail-soft: any resolution error -> None -> client fallback.
        _rx_product_doc = None
        _rx_pid = item.product_id or ""
        if _rx_pid and not _rx_pid.startswith(("custom-", "lens-", "lens-sug-")):
            try:
                _rx_product_doc = _resolve_product_doc(product_repo, _rx_pid)
            except Exception:  # noqa: BLE001 -- resolution is best-effort
                _rx_product_doc = None
        _validate_order_line_rx(
            item, order.customer_id, current_user, product_doc=_rx_product_doc
        )

    # BUG-119/BUG-118: the real server-side price ceiling / cost floor / discount
    # validation runs in the totals loop below (AFTER the idempotency check, using
    # the catalog MRP/offer/cost snapshot). The OrderItemCreate model carries no
    # mrp/offer_price field, so the old getattr(item,"mrp",0) guard here was always
    # reading 0 and never fired -- a client could set any unit_price.

    if order_repo is not None and customer_repo is not None:
        # C-5 (DELTA 3): order-create idempotency. If the request carries a
        # non-empty Idempotency-Key and an order with that key already exists
        # for this store, return THAT order rather than creating a duplicate.
        # Looked up before any work so a retried "Pay now" is a cheap no-op.
        # Fail-soft: any lookup error falls through to a normal create.
        idem_key = (idempotency_key or "").strip()
        if idem_key:
            try:
                existing = order_repo.find_one(
                    {"idempotency_key": idem_key, "store_id": store_id}
                )
                if existing:
                    return _order_create_response(existing)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ORDERS] idempotency lookup skipped: %s", exc)

        # Verify customer exists (allow walk-in with generated IDs)
        customer = customer_repo.find_by_id(order.customer_id)
        is_walkin = not customer and (
            order.customer_id.startswith("walkin-") or order.customer_id == "walk-in"
        )
        if not customer and not is_walkin:
            raise HTTPException(status_code=404, detail="Customer not found")

        customer_name = customer.get("name") if customer else "Walk-in Customer"
        customer_phone = (
            customer.get("phone") or customer.get("mobile") if customer else ""
        )

        # BILL-TO-MEMBER P1 (council 2026-06-19): resolve the MEMBER this order
        # bills to -- never the bare account. NON-BREAKING:
        #   * explicit order.patient_id MUST belong to this account -> else 422.
        #     A cross-account patient_id is the ONLY hard reject (mis-billing
        #     guard); it would otherwise attribute a sale to a stranger's family.
        #   * absent/blank patient_id -> AUTO-RESOLVE to the account's Primary
        #     member (council chose auto->Primary over a hard reject so the ~38
        #     existing tests + automated/online order-create paths that never
        #     send a member keep working). A real account missing a Primary gets
        #     one minted + persisted; a walk-in (no DB doc) gets a synthetic
        #     Primary stamped on the order only (no loyalty -- account is fake).
        # The resolved member id is persisted as patient_id (already wired into
        # order_data below) PLUS billed_to_member_name for receipts/reports, so
        # no order is ever left bare-account.
        from api.services.member_billing import (
            ensure_primary_member,
            find_member,
            build_primary_member,
        )

        requested_patient_id = (order.patient_id or "").strip()
        billed_member: Optional[Dict[str, Any]] = None

        if requested_patient_id:
            if customer:
                billed_member = find_member(customer, requested_patient_id)
                if billed_member is None:
                    # The member id does not belong to this account -> reject.
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "patient_id must be a member of this account "
                            "(customer_id)."
                        ),
                    )
            else:
                # Walk-in / synthetic account: trust the supplied id, stamp a
                # synthetic Primary member around it so the order still carries a
                # real member name.
                billed_member = build_primary_member(
                    name=customer_name or "Walk-in Customer",
                    mobile=customer_phone,
                    patient_id=requested_patient_id,
                )
        else:
            if customer:
                # Auto-resolve to the account's Primary; mint + persist one when
                # the account has none yet (legacy/imported accounts).
                billed_member, _changed = ensure_primary_member(customer)
                if _changed:
                    try:
                        customer_repo.update(
                            order.customer_id,
                            {
                                "patients": customer.get("patients", []),
                                "primary_patient_id": customer.get(
                                    "primary_patient_id"
                                ),
                            },
                        )
                    except Exception as exc:  # noqa: BLE001
                        # Persisting the seeded Primary is best-effort -- the
                        # order still bills to the resolved member id even if the
                        # account write is briefly unavailable; the backfill
                        # migration reconciles any account left without a Primary.
                        logger.warning(
                            "[ORDERS] could not persist seeded Primary member "
                            "for %s: %s",
                            order.customer_id,
                            exc,
                        )
            else:
                # True walk-in with no member supplied -> synthesize a Primary.
                billed_member = build_primary_member(
                    name=customer_name or "Walk-in Customer",
                    mobile=customer_phone,
                )

        resolved_patient_id = billed_member.get("patient_id") if billed_member else None
        billed_to_member_name = (
            billed_member.get("name") if billed_member else customer_name
        )

        # Pre-fetch cost_price for every product on the order so we can
        # snapshot it onto each line as cost_at_sale. This freezes COGS at
        # sale time so historical P&L doesn't drift when cost_price is
        # edited later. Virtual SKUs (custom-/lens-/lens-sug-) don't have
        # a product doc -> cost_at_sale stays None and the finance layer
        # falls back to 60% of line total.
        _cost_by_pid: Dict[str, float] = {}
        # BUG-119/BUG-118: snapshot the catalog MRP + offer_price per product so
        # the pricing loop can enforce a server-side price ceiling (unit_price may
        # never exceed the product's real MRP, or its offer_price when HQ has
        # already discounted it), a cost floor, and the "no further discount on an
        # HQ-discounted (offer<MRP) item" rule. The client unit_price/discount is
        # never trusted for these. Fail-soft: absent product -> no constraint.
        _mrp_by_pid: Dict[str, float] = {}
        _offer_by_pid: Dict[str, float] = {}

        def _num_or_none(v):
            try:
                f = float(v)
                return f
            except (TypeError, ValueError):
                return None

        try:
            for pid, pdoc in _pdoc_by_pid.items():
                c = _num_or_none(pdoc.get("cost_price"))
                if c is not None:
                    _cost_by_pid[pid] = c
                m = _num_or_none(pdoc.get("mrp"))
                if m is not None and m > 0:
                    _mrp_by_pid[pid] = m
                o = _num_or_none(pdoc.get("offer_price"))
                if o is not None and o > 0:
                    _offer_by_pid[pid] = o
        except Exception:
            # Pricing snapshot is fail-soft -- never block order create.
            _cost_by_pid, _mrp_by_pid, _offer_by_pid = {}, {}, {}

        # Calculate totals
        items_data = []
        subtotal = 0.0

        # NEW-ORDER-PRODUCTID-STAMP: resolve each client-supplied product ref
        # (which may be a SKU or _id) to the catalog's canonical product_id ONCE,
        # so the persisted order lines reconcile against the catalog instead of
        # storing a raw SKU. Virtual + unresolvable ids map to themselves.
        _canon_by_pid: Dict[str, str] = {}
        for _it in order.items:
            _ip = _it.product_id or ""
            if _ip and _ip not in _canon_by_pid:
                _canon_by_pid[_ip] = _canonical_pid(product_repo, _ip)

        # Retrieve user discount cap for enforcement.
        # Use the role-aware effective cap helper rather than the raw user
        # document field — that one defaults to 10% even for SUPERADMIN,
        # which was the long-standing "why is my cap 10%" bug.
        from api.services.role_caps import effective_discount_cap

        user_roles = current_user.get("roles", [])
        user_discount_cap = effective_discount_cap(
            user_roles, current_user.get("discount_cap")
        )
        # Only HQ roles bypass discount caps. STORE_MANAGER has a real 20%
        # cap (SYSTEM_INTENT discount matrix) and MUST flow through the
        # effective_cap + category-cap path -- it was incorrectly bypassing.
        is_admin = any(r in user_roles for r in ["SUPERADMIN", "ADMIN"])

        for item in order.items:
            item_total = item.unit_price * item.quantity

            # ---- Per-line price integrity + discount caps (SHARED gate) ----
            # BUG-119/BUG-118 ceiling / cost floor / HQ-offer rule / role +
            # category + luxury-brand cap / reason requirement all live in
            # _enforce_line_pricing, THE one implementation shared with the
            # POST /{order_id}/items door (they were written twice and drifted:
            # the add-item copy resolved products narrowly, so SKU/_id/catalog
            # references skipped every guard).
            _pid = item.product_id or ""
            _line_gate = _enforce_line_pricing(
                item,
                _pdoc_by_pid.get(_pid),
                is_admin=is_admin,
                role_cap=user_discount_cap,
            )
            _loyalty_eff = _line_gate["loyalty_eff"]

            discount_amount = item_total * (item.discount_percent / 100)
            item_subtotal = item_total - discount_amount

            # ============================================================
            # INCENTIVE AUTO-TAGGING
            # Detects qualifying items for kicker tracking at POS time
            # Tags: brand group, subbrand, kicker type, item value, discount
            # Replaces the manual PRODUCT_INCENTIVE Excel entirely
            # ============================================================
            INCENTIVE_BRANDS = {
                "ZEISS": "ZEISS",
                "SAFILO": "SAFILO",
                "CARRERA": "SAFILO",
                "POLAROID": "SAFILO",
                "MARC JACOB": "SAFILO",
                "HUGO": "SAFILO",
                "SEVENTH STREET": "SAFILO",
                "BOSS": "SAFILO",
                "TOMMY HILFIGER": "SAFILO",
                "PIERRE CARDIN": "SAFILO",
                "UNDER ARMOUR": "SAFILO",
            }
            brand_upper = (item.brand or "").upper()
            subbrand_upper = (item.subbrand or "").upper()
            product_name_upper = (item.product_name or "").upper()

            # Check brand, subbrand, AND product name for matches
            # (lens details like "1.5 ZEISS PROGRESSIVE LIGHT..." appear in product name)
            incentive_brand = None
            matched_key = None
            for key, group in INCENTIVE_BRANDS.items():
                if (
                    key in brand_upper
                    or key in subbrand_upper
                    or key in product_name_upper
                ):
                    incentive_brand = group
                    matched_key = key
                    break

            # Detect kicker type from lens_details, subbrand, or product name
            incentive_kicker = None
            incentive_lens_type = None
            incentive_addon = None

            if incentive_brand == "ZEISS":
                # Check lens_details dict first (structured data from LensDetailsModal)
                lens_type_str = ""
                if item.lens_details:
                    lens_type_str = (item.lens_details.get("type", "") or "").upper()
                    lens_material = (
                        item.lens_details.get("material", "") or ""
                    ).upper()
                    lens_coatings = " ".join(
                        item.lens_details.get("coatings", []) or []
                    ).upper()
                    lens_type_str = f"{lens_type_str} {lens_material} {lens_coatings}"

                # Also check product name (e.g., "1.5 ZEISS PROGRESSIVE LIGHT 2 3D DVP UV")
                full_check = f"{lens_type_str} {product_name_upper} {subbrand_upper}"

                if "SMARTLIFE" in full_check or "SMART LIFE" in full_check:
                    incentive_kicker = "ZEISS_SMARTLIFE"
                    incentive_lens_type = "PAL" if "PROGRESS" in full_check else "SV"
                    incentive_addon = "SMART LIFE"
                elif "PHOTOFUSION" in full_check or "PFX" in full_check:
                    incentive_kicker = "ZEISS_PHOTOFUSION"
                    incentive_lens_type = "PAL" if "PROGRESS" in full_check else "SV"
                    incentive_addon = "PFX"
                elif "PROGRESSIVE" in full_check or "PAL" in full_check:
                    incentive_kicker = "ZEISS_PROGRESSIVE"
                    incentive_lens_type = "PAL"
                elif "FSV" in full_check or "SINGLE" in full_check:
                    incentive_kicker = "ZEISS_SV"
                    incentive_lens_type = "SV"
                else:
                    incentive_kicker = "ZEISS_OTHER"

            elif incentive_brand == "SAFILO":
                if item.item_type in ("FRAME",):
                    incentive_kicker = "SAFILO_FRAME"
                elif item.item_type in ("SUNGLASS",) or (
                    item.category and "SG" in item.category.upper()
                ):
                    incentive_kicker = "SAFILO_SG"
                else:
                    incentive_kicker = "SAFILO_OTHER"

            # Build incentive tag (None if not qualifying)
            incentive_tag = None
            if incentive_brand:
                incentive_tag = {
                    "brand_group": incentive_brand,
                    "brand": item.brand,
                    "subbrand": item.subbrand or matched_key,
                    "kicker": incentive_kicker,
                    "lens_type": incentive_lens_type,
                    "addon": incentive_addon,
                    "item_value": item_subtotal,
                    "item_mrp": item.unit_price * item.quantity,
                    "discount_percent": item.discount_percent,
                    "discount_amount": discount_amount,
                    "salesperson_id": salesperson_id,
                    "tagged_at": datetime.now().isoformat(),
                }

            _pdoc = _pdoc_by_pid.get(item.product_id or "") or {}
            items_data.append(
                {
                    "item_id": str(uuid.uuid4()),
                    "item_type": item.item_type,
                    "product_id": _canon_by_pid.get(item.product_id or "")
                    or item.product_id,
                    "product_name": item.product_name,
                    "sku": item.sku,
                    # The product MASTER's brand wins over the client echo --
                    # the luxury-brand cap (promo clamp included) reads this
                    # field, and a spoofed/blank client brand must not dodge
                    # the Cartier/Gucci floor. Client value kept when the
                    # master has none (virtual lines etc.).
                    "brand": _pdoc.get("brand") or item.brand,
                    "subbrand": item.subbrand,
                    "category": item.category,
                    # The DISCOUNT TIER (MASS/PREMIUM/LUXURY/SERVICE/
                    # NON_DISCOUNTABLE) from the product master. The promo
                    # engine's cap clamp reads this field; it was never
                    # stamped, so the clamp fell back to the merchandising
                    # `category` label and every line clamped at the MASS 15%
                    # default -- including 0%-cap NON_DISCOUNTABLE lines.
                    "discount_category": _pdoc.get("discount_category"),
                    # Catalog MRP at sale time: the base every discount cap is
                    # measured on (promo clamp headroom math reads it).
                    "mrp": _mrp_by_pid.get(item.product_id or ""),
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "discount_percent": item.discount_percent,
                    "effective_discount_percent": round(_loyalty_eff, 4),
                    "discount_amount": discount_amount,
                    # C-4 (DELTA 2): per-line approver + reason (consumed by the
                    # required-approval gate for a 100%-line-discount order).
                    "discount_approved_by": item.discount_approved_by,
                    "discount_reason": item.discount_reason,
                    "item_total": item_subtotal,
                    # COGS-freeze: snapshot the product cost at sale time so
                    # historical P&L stays stable when cost_price is edited.
                    # None when the cost is unknown (virtual / no product doc).
                    "cost_at_sale": _cost_by_pid.get(item.product_id or ""),
                    "prescription_id": item.prescription_id,
                    "lens_options": item.lens_options,
                    "lens_details": item.lens_details,
                    "incentive_tag": incentive_tag,
                    # Carry the explicit serialized unit so a future return can
                    # re-activate exactly the unit that left. Optional; FIFO
                    # allocation by product+store fills the gap when missing.
                    "stock_id": getattr(item, "stock_id", None),
                    # Lens catalog cell coordinates (B'4) -- consumed by
                    # the lens_stock_hook on reserve/commit/release.
                    "lens_line_id": getattr(item, "lens_line_id", None),
                    "sph": getattr(item, "sph", None),
                    "cyl": getattr(item, "cyl", None),
                    "add": getattr(item, "add", None),
                    # BUG-005: persist the validated cylinder axis alongside the
                    # power so the lab gets the full grind spec.
                    "axis": getattr(item, "axis", None),
                    # POS-10: per-line staff note (e.g. "tight frame — be careful
                    # tightening screws"). Carried from posStore CartLineItem.
                    "item_note": getattr(item, "item_note", None) or None,
                }
            )
            subtotal += item_subtotal

        # Phase 6.15 — per-category GST (Indian rules). Audit Run #4
        # caught a phantom-balance bug; further audit (May-2026) caught
        # the per-cat fix itself zeroing every order's tax_amount
        # because the loop read `it.get("subtotal")` while the dict was
        # built with key "item_total". The per-category math is now in
        # `_compute_per_category_gst`, used by create + add + remove.
        cart_discount_percent = max(0.0, min(100.0, order.cart_discount_percent or 0.0))
        # Order-level cart discount must honour the SAME caps as per-item
        # discounts: the role cap AND the strictest category / luxury-brand cap
        # across the cart's real product lines. It used to be clamped only to the
        # role cap, so a cart discount could land >cap on a Cartier (2%) or
        # NON_DISCOUNTABLE (0%) line the per-item path above would block.
        # Owner ruling 2026-08-30: bill-level manual discounts carry a written
        # reason — for EVERY role, admins included (the reason is
        # accountability, not a cap; mirrors the per-line guard above).
        if (
            cart_discount_percent > 0
            and len(str(order.cart_discount_reason or "").strip()) < 4
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"A reason (at least 4 characters) is required for the "
                    f"{cart_discount_percent}% bill-level discount "
                    f"(no offer applied)."
                ),
            )

        if not is_admin and cart_discount_percent > 0:
            cart_cap = user_discount_cap
            from api.services.pricing_caps import (
                effective_discount_cap as _line_discount_cap,
            )

            for _it in order.items:
                _pid = getattr(_it, "product_id", None)
                if not _pid or _pid.startswith(("custom-", "lens-", "lens-sug-")):
                    # A lens / custom line resolves to no product doc, so no
                    # category or luxury-brand cap can tighten it -- but the ROLE
                    # cap still applies, and a lens is MOST of an optical
                    # ticket's money. Skipping the line outright (what this did)
                    # enforced the stacking cap on the frame and not on the lens:
                    # 10% on the lens + a 10% bill discount = 19% on the single
                    # biggest line, under a 10% cap.
                    assert_stack_within_cap(
                        getattr(_it, "product_name", None) or _pid or "this line",
                        effective_line_discount_pct(
                            getattr(_it, "discount_percent", 0) or 0,
                            getattr(_it, "unit_price", 0) or 0,
                            _mrp_by_pid.get(_pid) or 0,
                        ),
                        cart_discount_percent,
                        user_discount_cap,
                    )
                    continue
                # BUG-118: a cart-level discount on an HQ-discounted line is the
                # same forbidden "further discount" (SYSTEM_INTENT s3) -- block it.
                _m = _mrp_by_pid.get(_pid)
                _o = _offer_by_pid.get(_pid)
                if _o and _m and _o < _m:
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            f"Cannot apply a cart discount: "
                            f"{getattr(_it, 'product_name', None) or _pid} is already "
                            f"HQ-discounted (offer Rs{_o} < MRP Rs{_m}). Contact an "
                            f"administrator to override."
                        ),
                    )
                # The ONE resolution made at the top of create (same docs the
                # per-line gate used) -- never re-query here, a second lookup
                # is how this loop and the per-line cap started disagreeing.
                _prod = _pdoc_by_pid.get(_pid)
                if _prod:
                    _this_cap = min(
                        user_discount_cap,
                        _line_discount_cap(
                            _prod.get("discount_category"), _prod.get("brand")
                        ),
                    )
                    cart_cap = min(cart_cap, _this_cap)

                    # STACKING CAP. The line cap and the cart cap were checked
                    # INDEPENDENTLY and never against their product, so both
                    # could sit exactly at the ceiling and the customer still
                    # walked out with roughly twice it: 10% + 10% = 19% under a
                    # 10% cap, and 2% + 2% = 3.96% on a Cartier line whose brand
                    # cap is 2%. Proven with a 201 from this very route.
                    #
                    # A cap that two controls can each satisfy while their
                    # product breaks it is not a cap. The bill discount lands on
                    # an already-discounted line, so the terms MULTIPLY.
                    assert_stack_within_cap(
                        getattr(_it, "product_name", None) or _pid,
                        effective_line_discount_pct(
                            getattr(_it, "discount_percent", 0) or 0,
                            getattr(_it, "unit_price", 0) or 0,
                            _mrp_by_pid.get(_pid) or 0,
                        ),
                        cart_discount_percent,
                        _this_cap,
                    )
            if cart_discount_percent > cart_cap + 1e-9:
                raise HTTPException(
                    status_code=403,
                    detail=f"Cart discount {cart_discount_percent}% exceeds the "
                    f"maximum {cart_cap}% allowed for these items "
                    f"(role + category/brand caps). Contact a manager for approval.",
                )

        # ============================================================
        # F11/F12 ADVANCED PROMOTIONS + CROSS-CATEGORY BUNDLING (DARK)
        # ------------------------------------------------------------
        # Gated by PROMO_ENGINE_ENABLED (default OFF). When OFF this block is a
        # COMPLETE no-op: `applied_promos` stays [] and items_data is untouched,
        # so the GST + grand_total math below is byte-identical to the pre-promo
        # path (asserted by test_promo_engine dark-by-default). When ON, the
        # pure engine (services/promo_engine.evaluate_promos) picks the single
        # best promo (EXCLUSIVE by default; a campaign may opt into stacking),
        # NEVER breaches the category/luxury caps (the engine clamps), and the
        # resulting per-line rupee discount reduces each line's item_total before
        # GST. The atomic uses_count guard + promo_applications audit row are
        # written by promotions.commit_promo_application AFTER the order persists.
        # Fully fail-soft: any promo error logs + skips -- a promo can NEVER
        # block or alter the sale beyond the discount it grants.
        applied_promos: List[Dict[str, Any]] = []
        promo_total_discount = 0.0
        promo_evaluation: Optional[Dict[str, Any]] = None
        try:
            from ..promotions import promo_engine_enabled, evaluate_for_order

            if promo_engine_enabled() and db is not None:
                # PURE evaluate (no DB writes yet) so the per-line discount can be
                # folded into the GST math; the atomic uses_count $inc +
                # promo_applications audit row are committed AFTER the order
                # persists (commit_promo_application below, with the real order_id).
                promo_evaluation = evaluate_for_order(
                    db,
                    store_id=store_id,
                    customer_id=order.customer_id,
                    items=items_data,
                    customer=customer,
                )
                if promo_evaluation and promo_evaluation.get("applied"):
                    promo_per_line = promo_evaluation.get("per_line_discount") or {}
                    applied_promos = promo_evaluation.get("applied_promos") or []
                    # Apply the per-line discount to each line's all-in total. The
                    # engine already clamped each share to that line's category/
                    # luxury cap, so this can never breach a hardlock.
                    for _line in items_data:
                        _lid = str(_line.get("item_id") or "")
                        _pd = float(promo_per_line.get(_lid, 0.0) or 0.0)
                        if _pd > 0:
                            _new_total = max(
                                0.0, float(_line.get("item_total") or 0.0) - _pd
                            )
                            _line["promo_discount_amount"] = round(_pd, 2)
                            _line["item_total"] = round(_new_total, 2)
                            promo_total_discount += _pd
                    promo_total_discount = round(promo_total_discount, 2)
        except Exception as _promo_exc:  # noqa: BLE001 - promos never block a sale
            logger.warning("[PROMO] order-create evaluation skipped: %s", _promo_exc)
            applied_promos, promo_total_discount, promo_evaluation = [], 0.0, None

        gst = _compute_per_category_gst(items_data, cart_discount_percent)
        taxable_after_cart_discount = gst["taxable"]
        tax_amount = gst["tax"]
        cart_discount_amount = gst["cart_discount_amount"]
        total_discount = gst["total_discount"]
        tax_rate = gst["dominant_rate"]
        grand_total = round(taxable_after_cart_discount + tax_amount, 2)

        # Fcostfloor (DECISIONS sec 9, owner sign-off 2026-06-09): E2-flag-
        # gated post-discount cost+pct% floor on each DISCOUNTED line's
        # EFFECTIVE per-unit taxable price (after the per-line discount AND
        # its share of the cart discount, as stamped by
        # _compute_per_category_gst). Owner rev 2: pure full-sticker lines
        # are exempt -- the flag below tells the guard whether a cart-level
        # discount applies to this order (server-derived, never the raw
        # client amount). Read-only math over the already-computed line
        # finals -- no GST, payment or persistence change. Fail-OPEN on
        # missing/zero cost; Rs 0 / 100%-discount lines stay
        # C-4-approval-gated-exempt; the floor COMPOSES with (never
        # replaces) the role/category/brand caps above. Flag off ->
        # immediate no-op (pre-change behavior). Raises BEFORE the lens
        # reserve below so a floor 400 leaks no reservation.
        from ...services.cost_floor import enforce_cost_floor

        enforce_cost_floor(
            items_data,
            _cost_by_pid,
            store_id,
            order_has_cart_discount=bool(
                cart_discount_percent > 0 or cart_discount_amount > 0
            ),
        )

        # Resolve delivery date — explicit date > expected_delivery_days
        if order.delivery_date:
            expected_delivery = datetime.combine(
                order.delivery_date, datetime.min.time()
            )
        else:
            expected_delivery = datetime.now() + timedelta(
                days=order.expected_delivery_days
            )

        # Branch B' sub-PR 4 -- atomic lens-stock reserve BEFORE the
        # order is persisted. Owner-decreed flow (2026-05-28): the POS
        # "Pay now" action validates the cart, calls reserve for each
        # lens line, and only then persists the order. If any reserve
        # 409s, the order is NEVER created and the POS surfaces a clean
        # "out of stock for SPH X CYL Y; available: N" message.
        #
        # We pre-generate the order_id so the reserve audit rows have a
        # stable source_id (`{order_id}#{line_index}`). This is the same
        # value the workshop commit + order cancel paths will use to
        # find/idempotency-check the reservation later.
        precomputed_order_id = str(uuid.uuid4())

        # BUG-097: block serialized non-lens oversell BEFORE reserving any lens
        # cells, so a 409 here leaks no reservation.
        _assert_serialized_stock_available(items_data, store_id)

        lens_reserve_failed = False
        lens_reservations: List[Dict[str, Any]] = []
        # Import the hook BEFORE the try so `release_for_cancel` is
        # unconditionally bound for the compensating-rollback path in the
        # except block (pylint E0601 otherwise: the import lived inside try).
        from ...services.lens_stock_hook import (
            reserve_for_order_item,
            release_for_cancel,
        )

        try:
            for idx, oi in enumerate(items_data):
                # STABLE RESERVATION KEY: the lens hook's idempotency /
                # release key is "{order_id}#{line_index}", so whatever we pass
                # here IS the key. We pass the line's IMMUTABLE item_id (uuid4,
                # minted once, never reused) rather than its position: positions
                # shift when a DRAFT line is removed, and a max+1 counter gets
                # REUSED when the highest line is removed -- either way a later
                # reserve/release hits the wrong cell. `line_index` is still
                # persisted purely so orders written by the older code can still
                # be released under their original key.
                oi["line_index"] = idx
                res_key = _lens_reservation_key(oi, idx)
                rec = await reserve_for_order_item(
                    order_item=oi,
                    order_id=precomputed_order_id,
                    line_index=res_key,
                    store_id=store_id or "",
                    user=current_user,
                )
                if rec is not None:
                    lens_reservations.append({"line_index": res_key, **rec})
                    if rec.get("status") == "failed":
                        lens_reserve_failed = True
        except HTTPException as exc:
            # Insufficient stock (409) -- compensating release for any
            # lines that already succeeded, then re-raise so POS sees
            # the original "available=N" message and the user can fix.
            if exc.status_code == 409:
                try:
                    for prev_idx, prev_oi in enumerate(items_data):
                        try:
                            await release_for_cancel(
                                order_item=prev_oi,
                                order_id=precomputed_order_id,
                                line_index=_lens_reservation_key(prev_oi, prev_idx),
                                store_id=store_id or "",
                                user=current_user,
                            )
                        except Exception as inner_rb:  # noqa: BLE001
                            logger.warning(
                                "[LENS_HOOK] compensating release "
                                "failed (line %s): %s",
                                prev_idx,
                                inner_rb,
                            )
                except Exception as rb_exc:  # noqa: BLE001
                    logger.warning(
                        "[LENS_HOOK] rollback outer error: %s",
                        rb_exc,
                    )
            raise
        except Exception as exc:  # noqa: BLE001
            # Non-blocking soft failure (mongo blip, etc.). Tag the
            # order and continue -- never crash POS create on a hook
            # error. Revenue protection takes priority.
            logger.warning(
                "[LENS_HOOK] reserve fail-soft pre-create: %s",
                exc,
            )
            lens_reserve_failed = True

        # C-4 (DELTA 2): a fully-discounted / Rs 0 order (100% line or cart
        # discount, or a grand_total that rounds to 0) is a sensitive giveaway
        # that REQUIRES explicit approval -- it is no longer silently
        # auto-stamped. When the order triggers the zero-total condition, the
        # request MUST carry an approver AND a non-empty reason at the level
        # that triggered it ("whichever applies"):
        #   * cart-level trigger (cart_discount 100% / grand_total 0)
        #       -> cart_discount_approved_by + cart_discount_reason
        #   * a 100% LINE discount
        #       -> that line's discount_approved_by + discount_reason
        #          (the order-wide cart_discount_* fields are accepted as a
        #          fallback so a single order-level sign-off still works)
        # Missing approver or empty reason -> HTTP 400. When approval IS
        # present we ALLOW the sale and still write the immutable
        # ORDER_ZERO_TOTAL_APPROVED audit row (below).
        acting_user_id = current_user.get("user_id")

        def _nonempty(value) -> bool:
            return bool(value is not None and str(value).strip())

        full_line_items = [
            it
            for it in items_data
            if float((it or {}).get("discount_percent") or 0) >= 100.0
        ]
        has_full_line_discount = bool(full_line_items)
        cart_level_zero = round(grand_total, 2) <= 0.0 or cart_discount_percent >= 100.0
        is_zero_total = cart_level_zero or has_full_line_discount

        cart_discount_approved_by = order.cart_discount_approved_by
        cart_discount_reason = order.cart_discount_reason
        zero_total_approved_by = None
        if is_zero_total:
            # Resolve the effective approver + reason from whichever level
            # supplied them: cart-level first, then any 100%-discount line.
            approver = (
                cart_discount_approved_by
                if _nonempty(cart_discount_approved_by)
                else None
            )
            reason = cart_discount_reason if _nonempty(cart_discount_reason) else None
            if approver is None and has_full_line_discount:
                for it in full_line_items:
                    if _nonempty((it or {}).get("discount_approved_by")):
                        approver = it["discount_approved_by"]
                        break
            if reason is None and has_full_line_discount:
                for it in full_line_items:
                    if _nonempty((it or {}).get("discount_reason")):
                        reason = it["discount_reason"]
                        break

            if approver is None or reason is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Zero-total or 100% discount requires an approver and a "
                        "reason."
                    ),
                )

            zero_total_approved_by = approver

        order_data = {
            "order_id": precomputed_order_id,
            "order_number": generate_order_number(store_id),
            # Public order-tracking token — long, unguessable, customer-facing.
            # Powers the no-login /portal/track/{token} link + QR. Additive;
            # does not touch any POS pricing/tax logic. Backfill-safe: orders
            # created before this field get a token lazily minted on lookup
            # (see portal.ensure_tracking_token).
            "tracking_token": secrets.token_urlsafe(24),
            "store_id": store_id,
            "customer_id": order.customer_id,
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            # BILL-TO-MEMBER P1: the RESOLVED member (explicit-and-valid, or the
            # auto-selected/seeded/synthetic Primary) -- never the raw client id,
            # never bare-account. billed_to_member_name is denormalized for
            # receipts/reports (council invoice rule "Billed to: [Member]").
            "patient_id": resolved_patient_id,
            "billed_to_member_name": billed_to_member_name,
            "salesperson_id": salesperson_id,
            "salesperson_name": salesperson_name,
            # Owner spec 13 — the two-way split, when the counter recorded one.
            # Absent on single-seller sales so existing docs/readers are
            # byte-identical to before.
            **({"salespersons": salespersons_split} if salespersons_split else {}),
            "visufit_id": (order.visufit_id or None),
            "items": items_data,
            "subtotal": subtotal,
            "cart_discount_percent": cart_discount_percent,
            "cart_discount_amount": cart_discount_amount,
            "cart_discount_reason": cart_discount_reason,
            # C-4 (DELTA 2): the approver the POS supplied (now REQUIRED for a
            # zero-total / 100%-discount order -- never auto-stamped).
            "cart_discount_approved_by": cart_discount_approved_by,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "total_discount": total_discount,
            "grand_total": grand_total,
            # Self-label the GST model this order was billed under, so any
            # deploy-skew / flag-flip order is identifiable + reports can trust
            # the stored per-line taxable/tax without guessing the era.
            "pricing_model": gst.get("pricing_model", "inclusive"),
            "amount_paid": 0.0,
            "balance_due": grand_total,
            "payment_status": "UNPAID",
            # Owner ruling 2026-08-30: bill type follows the money (derived
            # in ONE place — database/repositories/order_repository.py).
            "bill_type": derive_bill_type("UNPAID"),
            "status": "DRAFT",
            "expected_delivery": expected_delivery.isoformat(),
            "delivery_time_slot": order.delivery_time_slot,
            "delivery_priority": (order.delivery_priority or "NORMAL").upper(),
            "notes": order.notes,
            # POS-10: order_type persisted so reports/audit can distinguish
            # a quick POS sale from a full workshop-linked order.
            "order_type": (order.order_type or None),
            "payments": [],
            "lens_reservations": lens_reservations,
            "lens_reserve_failed": bool(lens_reserve_failed),
            # C-4: zero-total accountability flags (persisted so reports + the
            # order view can surface a Rs 0 sale and who approved it).
            "zero_total": bool(is_zero_total),
            "zero_total_approved_by": zero_total_approved_by,
            # F11/F12: promos that fired on this order (empty [] when the engine
            # is dark or nothing matched -- the dark-default path). Surfaced on
            # the order view, the Tally JV, and the Offer Tally report.
            "applied_promos": applied_promos,
            "promo_discount_total": round(promo_total_discount, 2),
            # C-5 (DELTA 3): the request's Idempotency-Key (None when absent).
            # A repeat POST with the same key returns this order rather than
            # creating a duplicate (store-scoped lookup at the top of create).
            "idempotency_key": (idem_key or None),
            # POS-12: initial status_history entry so the DRAFT create is always
            # the first row in the timeline. Subsequent status changes append via
            # OrderRepository.update_status -> $push {"status_history": {...}}.
            "status_history": [
                {
                    "status": "DRAFT",
                    "timestamp": datetime.now().isoformat(),
                    "changed_by": current_user.get("user_id") or "system",
                }
            ],
        }

        try:
            # P3-B: order_number carries a UNIQUE sparse index. Under
            # concurrency two creates can mint the same value and the loser
            # hits a Mongo E11000 -- which previously 500'd. create_unique
            # regenerates JUST the order_number and retries (bounded), mirroring
            # vouchers.issue_voucher. order_id / _id are stable UUIDs and never
            # change across retries. Behaviour-preserving on the no-collision
            # path: a single insert, identical doc.
            created = order_repo.create_unique(
                order_data,
                number_field="order_number",
                regenerate=lambda: generate_order_number(store_id),
            )
        except Exception as create_exc:  # noqa: BLE001
            # Order persist failed AFTER reservations succeeded -- run
            # the compensating release so the cells don't leak.
            logger.error(
                "[ORDERS] order_repo.create failed; releasing %d lens "
                "reservations for order %s",
                len(lens_reservations),
                precomputed_order_id,
            )
            try:
                from ...services.lens_stock_hook import release_for_cancel

                for idx, oi in enumerate(items_data):
                    try:
                        await release_for_cancel(
                            order_item=oi,
                            order_id=precomputed_order_id,
                            line_index=_lens_reservation_key(oi, idx),
                            store_id=store_id or "",
                            user=current_user,
                        )
                    except Exception:  # noqa: BLE001
                        pass  # fail-soft compensating action
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(
                status_code=500,
                detail="Failed to create order: {0}".format(create_exc),
            )

        if created:
            created_order_id = created.get("order_id") or ""
            # C-4: write an immutable audit row for a zero-total / fully
            # discounted sale so a Rs 0 order is never silent. Uses the same
            # append-only AuditRepository every other sensitive action uses
            # (returns / payouts / price edits). Fail-soft: an audit failure
            # must NEVER block the sale.
            if is_zero_total:
                try:
                    from ...dependencies import get_audit_repository

                    audit = get_audit_repository()
                    if audit is not None:
                        audit.create(
                            {
                                "action": "ORDER_ZERO_TOTAL_APPROVED",
                                "entity_type": "order",
                                "entity_id": created_order_id,
                                "store_id": store_id,
                                "user_id": acting_user_id,
                                "severity": "WARNING",
                                "details": {
                                    "grand_total": grand_total,
                                    "subtotal": subtotal,
                                    "cart_discount_percent": cart_discount_percent,
                                    "has_full_line_discount": has_full_line_discount,
                                    # C-4 (DELTA 2): approval is now REQUIRED,
                                    # so the approver + reason are always real
                                    # (never auto-stamped).
                                    "approved_by": zero_total_approved_by,
                                    "reason": cart_discount_reason,
                                },
                                "created_at": datetime.now().isoformat(),
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[ORDERS] zero-total audit skipped: %s", exc)

            # Flip serialized stock units to SOLD with this order_id stamped on
            # them. This is what lets returns.py reactivate THE EXACT unit that
            # left (preferred path in _reactivate_original_unit; the fallback
            # is "any non-AVAILABLE unit for this product+store" which can
            # collide across orders). Fail-soft: a stock-side failure logs and
            # never blocks the POS sale - bad stock data must not break revenue.
            try:
                _mark_units_sold(created_order_id, items_data, store_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[STOCK] mark_units_sold failed: %s", exc)

            # IMS = inventory MASTER (council B11): after the in-store sale
            # reduces on-hand, push the reduced AVAILABLE qty to Shopify so the
            # website can't oversell. Gated (IMS_SHOPIFY_WRITES + DISPATCH_MODE)
            # + fire-and-forget + fully fail-soft: a Shopify error can NEVER
            # block or slow the sale. No online mapping for a SKU -> no-op.
            try:
                from ...services.online_stock_writeback import writeback_after_sale

                writeback_after_sale(None, items_data, store_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[STOCK] online write-back skipped: %s", exc)

            # F11/F12: commit the promo application AFTER the order persists --
            # atomically $inc each fired promo's uses_count (guarded
            # find_one_and_update; concurrent terminals can't overshoot) and write
            # the immutable promo_applications audit row with the real order_id +
            # margin estimate. Fully fail-soft: any error logs + skips; the order
            # is already saved with applied_promos stamped, so the audit is
            # best-effort and never blocks the sale. No-op when nothing fired.
            if applied_promos and promo_evaluation is not None:
                try:
                    from ..promotions import commit_promo_application

                    commit_promo_application(
                        db,
                        order_id=created_order_id,
                        order_number=created.get("order_number") or "",
                        store_id=store_id,
                        customer_id=order.customer_id,
                        cashier_id=current_user.get("user_id"),
                        items=items_data,
                        # commit reads the RAW engine dict (fired/breakdown/...),
                        # which evaluate_for_order nests under "evaluation".
                        evaluation=(promo_evaluation or {}).get("evaluation") or {},
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[PROMO] commit skipped: %s", exc)

            # Pune-incentive walk-in counter (Module i, Phase 4): bump
            # the per-store-per-day counter, dedup'd by mobile.
            try:
                walkin_repo = get_walkin_counter_repository()
                if walkin_repo is not None:
                    walkin_repo.auto_increment(
                        store_id=store_id or "",
                        sales_person_id=salesperson_id or "",
                        mobile=customer_phone or None,
                    )
            except Exception:
                pass  # fail-soft — counter must never block order create

            # Loyalty engine — award earn points. Idempotent on
            # (customer_id, order_id), and fully fail-soft (any failure
            # is logged but never blocks the order response).
            try:
                from ..loyalty import earn_for_order_internal

                # Skip walk-ins: they have no real customer_id to credit.
                if order.customer_id and not order.customer_id.startswith(
                    ("walkin-", "walk-in")
                ):
                    earn_for_order_internal(
                        customer_id=order.customer_id,
                        order_id=created.get("order_id") or "",
                        items=items_data,
                        rupee_value=float(taxable_after_cart_discount),
                        user_id=current_user.get("user_id"),
                        store_id=store_id,
                        cart_discount_percent=cart_discount_percent,
                    )
            except Exception:
                pass  # fail-soft — loyalty must never block POS

            # C-5 (DELTA 3): same envelope the idempotency replay returns, so a
            # retried request is indistinguishable from the original create.
            return _order_create_response(created)

        raise HTTPException(status_code=500, detail="Failed to create order")

    return {
        "order_id": str(uuid.uuid4()),
        "order_number": generate_order_number(store_id or "STR"),
        "status": "DRAFT",
        "message": "Order created successfully",
    }
