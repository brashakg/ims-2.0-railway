"""Goods receipt creation (the mandatory-attachment gate) and read."""

from ._shared import (
    Depends,
    HTTPException,
    _ATTACHMENT_INVALID_DETAIL,
    _GRN_DOCUMENT_KIND,
    _RECEIVABLE_PO_STATUSES,
    _VENDOR_ROLES,
    _get_db,
    _normalize_invoice_no,
    can_access_store_scoped,
    datetime,
    get_audit_repository,
    get_current_user,
    get_file_store,
    get_grn_repository,
    get_purchase_order_repository,
    is_online_store,
    require_roles,
    router,
    uuid,
)
from .models import GRNCreate, GRN_SUBTYPE_DC
from .numbering import (
    classify_grn_line_variance,
    generate_grn_number,
    grn_has_discrepancy,
)
from .grn import _duplicate_grn_detail, _enrich_grn_names, _find_duplicate_standard_grn


@router.post("/grn", status_code=201)
async def create_grn(
    grn: GRNCreate, current_user: dict = Depends(require_roles(*_VENDOR_ROLES))
):
    """Create a new GRN (STANDARD) or log a Delivery Challan (F9 DC subtype)."""
    return await _create_grn_impl(grn, current_user)


async def _create_grn_impl(grn: GRNCreate, current_user: dict) -> dict:
    """Shared GRN-create engine behind POST /grn (and POST /grn/express).

    Behavior-preserving extraction of the original create_grn body so the
    express receiving chain can run the SAME validation + persistence path --
    attachment gate (F-S3 + BUG-010 file-exists), PO receivable check, F2
    store re-point to the PO's delivery store, per-store numbering and the DC
    guards -- without duplicating any control. Callers pass the authenticated
    ``current_user`` their own ``require_roles(*_VENDOR_ROLES)`` gate produced.
    """
    grn_repo = get_grn_repository()
    po_repo = get_purchase_order_repository()

    grn_id = str(uuid.uuid4())
    store_id = current_user.get("active_store_id")
    is_dc = grn.grn_subtype == GRN_SUBTYPE_DC
    # grn_number is generated AFTER the receiving store is finalised (a standard
    # PO-backed GRN is re-pointed to the PO's delivery store below), so the
    # per-store serial reflects the store the goods are actually booked to.

    # F-S3: mandatory goods-receipt document. The ops user physically receiving a
    # STANDARD shipment MUST attach the vendor invoice/challan (image or PDF)
    # before the GRN is created -- so the accountant always has the source doc to
    # reconcile against and there is no "received with no paperwork" hole. The
    # file is uploaded first via POST /vendors/grn/upload-doc (returns file_id),
    # which already validated size + MIME, so here we only assert it is present.
    # A DELIVERY_CHALLAN is exempt at receipt (its tax invoice arrives later and
    # is attached at reconciliation). Fail LOUD: a 400 with a stable code the UI
    # keys on to keep the user on the upload step.
    if not is_dc and not (
        grn.attachment_file_id and str(grn.attachment_file_id).strip()
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "ATTACHMENT_REQUIRED",
                "message": (
                    "Attach the vendor invoice or challan (image or PDF) before "
                    "creating the goods receipt."
                ),
            },
        )

    # BUG-010: the presence check above only proves a NON-EMPTY id was sent -- a
    # forged / stale id would pass the gate and persist, only 404'ing later at
    # download time, defeating the mandatory-attachment guarantee. Verify the
    # file actually exists in the store BEFORE persisting. Only STANDARD GRNs are
    # gated (a DC has no receipt-time attachment to verify).
    # Storage-down vs forged-id: get_file_store() returning None means storage
    # itself is unavailable -> 503 (do NOT mask the existing fail-loud behavior
    # by 400'ing). A live store whose get() finds nothing == a forged/stale id
    # -> 400 ATTACHMENT_INVALID.
    #
    # P0 (security panel round 6): this gate used to run under `if not is_dc`,
    # while the persistence below wrote attachment_file_id UNCONDITIONALLY -- so
    # adding one JSON field, grn_subtype="DELIVERY_CHALLAN", skipped BOTH the
    # kind check and the store bind and re-opened the round-5 theft verbatim.
    # The gate now runs whenever an attachment id is PRESENT, whatever the
    # subtype; the DC exemption is only from the REQUIREMENT to attach (above),
    # never from authorising an attachment that was sent anyway.
    _attachment_meta = None
    if grn.attachment_file_id and str(grn.attachment_file_id).strip():
        store = get_file_store()
        if store is None:
            raise HTTPException(status_code=503, detail="File storage unavailable")
        # P0 (security panel): existence is NOT authorisation. The id is
        # supplied by the CALLER (GRNCreate.attachment_file_id) and ONE bucket
        # holds every binary in the app, so an existence check passes just as
        # happily for a task attachment or an employee Aadhaar scan.
        # Kind is checked here; the STORE bind runs below, once the receiving
        # store is final (a PO-backed GRN is re-pointed to the PO's store).
        _attachment_meta = store.get_metadata(str(grn.attachment_file_id).strip())
        if (
            _attachment_meta is None
            or _attachment_meta.get("kind") != _GRN_DOCUMENT_KIND
        ):
            raise HTTPException(
                status_code=400,
                detail=_ATTACHMENT_INVALID_DETAIL,
            )

    # Validate PO exists. For a DC, the PO is optional (lens top-ups arrive with
    # no pre-logged PO) -- only validate when one was supplied.
    po = None
    if po_repo is not None and grn.po_id:
        po = po_repo.find_by_id(grn.po_id)
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.get("status") not in _RECEIVABLE_PO_STATUSES:
            raise HTTPException(
                status_code=400, detail="PO is not in receivable status"
            )

        # F2 store boundary: a STANDARD PO-backed GRN is received at the PO's
        # delivery store -- not blindly the caller's active store, else stock from
        # another store's PO could be mis-credited here (the GRN was stamped to
        # active_store_id with no PO cross-check). Re-point store_id to the PO's
        # store and verify the caller may act on it: cross-store roles (ADMIN /
        # AREA_MANAGER / SUPERADMIN) pass; a store-scoped role only for its own
        # store, else 404 (hiding other stores' POs). DC / PO-less receipts stay
        # on the caller's active store.
        if not is_dc:
            po_store = po.get("delivery_store_id")
            if po_store:
                if not can_access_store_scoped(po_store, current_user):
                    raise HTTPException(
                        status_code=404, detail="Purchase order not found"
                    )
                store_id = po_store

    # P0 STORE BIND (security panel round 5, reproduced end to end). Checking
    # only the KIND still let a caller LAUNDER another store's document: the
    # download route is scoped by the GRN RECORD
    # (can_access_store_scoped(grn["store_id"])), so binding a victim's
    # grn_document file_id to a GRN in YOUR OWN store walks it straight past
    # that scope. A STORE_MANAGER at WO-JSR-01 bound an ACCOUNTANT's
    # BV-RANCHI-01 supplier invoice -- a different store AND a different legal
    # entity -- and streamed it back, cost prices, vendor terms and the real
    # filename included, while the front door correctly 404'd.
    #
    # POST /vendors/grn/upload-doc already stamps store_id, so binding the store
    # costs the receiving flow nothing: the live UI uploads and creates from the
    # same component. The UPLOADER is deliberately NOT bound -- that would
    # foreclose an ops-uploads / accountant-creates split -- but "don't bind the
    # uploader" never implied "bind nothing".
    #
    # The test is the caller's REACHABLE stores, not the receiving store.
    # Comparing the blob's stamp to the post-re-point store_id (my round-6
    # attempt) BROKE THE FORWARD RECEIVING FLOW: upload-doc stamps the uploader's
    # ACTIVE store, while a PO-backed GRN is re-pointed to the PO's delivery
    # store, so an ADMIN active at A receiving store B's PO was 400'd -- and
    # re-uploading produced the same stamp, an unescapable loop, with the
    # deliberately indistinguishable message making an outage look like a forged
    # id. The repo's own test_po_store_boundary states that intent.
    #
    # can_access_store_scoped is the canonical helper and gets both directions
    # right: a store-level thief cannot reach the victim's store, so the round-5
    # theft is still refused; a cross-store ADMIN/AREA_MANAGER is granted nothing
    # they could not already read through the download route. A blob with no
    # store_id resolves the same way -- unreachable for a store-level caller,
    # readable by a cross-store one -- so there is no fail-open either.
    if _attachment_meta is not None:
        _blob_store = _attachment_meta.get("store_id")
        if not can_access_store_scoped(_blob_store, current_user):
            raise HTTPException(
                status_code=400,
                detail=_ATTACHMENT_INVALID_DETAIL,
            )

    # W1.4 / OS-006: the receiving store is now FINAL (PO re-point applied).
    # An ONLINE store owns no stock -- accepting goods there would mint real
    # stock_units on the pooled store, corrupting the no-own-stock model and
    # masking the oversell warning. Reject BEFORE the GRN doc is minted.
    if is_online_store(None, store_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "Online stores hold no stock - receive these goods into a "
                "physical shop instead."
            ),
        )

    # F9: the vendor a DC is for -- from the PO when linked, else the body field.
    vendor_id = (po.get("vendor_id") if po else None) or grn.vendor_id

    # Now that the receiving store is final (re-pointed to the PO's delivery
    # store for a standard PO-backed GRN), mint the per-store GRN serial.
    grn_number = generate_grn_number(store_id)

    # F9: DC-specific guards (uniqueness + period lock). Both are best-effort on
    # a DB error (fail-soft) but a found duplicate is a hard 409.
    if is_dc:
        db = None
        try:
            db = _get_db()
        except Exception:
            db = None
        # Application-level DC-number uniqueness per (vendor_id, dc_number,
        # store_id) -- vendors reuse the same DC number across branches, so the
        # key is per store, not just per vendor. (The unique partial index is
        # added post-dedup per the prod-data-blockers convention.)
        if db is not None and grn.dc_number:
            try:
                dup = db.get_collection("grns").find_one(
                    {
                        "grn_subtype": GRN_SUBTYPE_DC,
                        "vendor_id": vendor_id,
                        "dc_number": grn.dc_number,
                        "store_id": store_id,
                    },
                    {"_id": 0, "grn_id": 1},
                )
                if dup:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Delivery Challan '{grn.dc_number}' is already "
                            f"logged for this vendor at this store. Duplicate "
                            f"DC numbers are not allowed."
                        ),
                    )
            except HTTPException:
                raise
            except Exception:
                pass  # fail-soft: skip dup check on DB error, proceed
        # Period lock on the DC date (goods movement into a closed month).
        if db is not None and grn.dc_date:
            try:
                from ..finance import check_period_locked

                check_period_locked(db, grn.dc_date)
            except HTTPException:
                raise
            except Exception:
                pass

    # P0-1 (launch gate): the STANDARD twin of the DC guard above. A vendor
    # invoice number identifies ONE physical delivery + ONE bill, so a second
    # non-VOID receipt carrying it (per PO or per vendor, case/punctuation
    # folded) is a double-submit -- which used to double-mint the stock AND
    # open the payable to being booked twice. The comment at the express 409
    # admitted this hole ("_create_grn_impl has no duplicate guard for
    # STANDARD receipts"); this closes it for BOTH doors, since express
    # creates through this shared impl. A legitimately split delivery arrives
    # with DIFFERENT invoice numbers per shipment and passes untouched.
    if not is_dc:
        dup = _find_duplicate_standard_grn(
            grn_repo, grn.po_id, vendor_id, grn.vendor_invoice_no
        )
        if dup is not None:
            raise HTTPException(
                status_code=409,
                detail=_duplicate_grn_detail(dup, grn.vendor_invoice_no),
            )

    # Ruling 14 -- THE TALLY. Every line of a PO-backed receipt must be ticked
    # before the receipt is written: the quantity that arrived has been counted
    # against the quantity that was ordered, line by line, by a person. Without
    # this the received quantity arrives pre-filled with the ordered quantity
    # and a receipt posts itself.
    if po is not None and not is_dc:
        untallied = [
            {"product_id": it.product_id, "received_qty": it.received_qty}
            for it in grn.items
            if not it.tallied
        ]
        if untallied:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "LINES_NOT_TALLIED",
                    "message": (
                        "Tick every line to confirm you have counted what "
                        "arrived against what was ordered, then post the "
                        "receipt."
                    ),
                    "lines": untallied,
                },
            )

    # Calculate totals
    total_received = sum(item.received_qty for item in grn.items)
    total_accepted = sum(item.accepted_qty for item in grn.items)
    total_rejected = sum(item.rejected_qty for item in grn.items)

    # Stamp the ordered quantity (from the PO, matched by product_id) onto each
    # received line so the GRN doc is self-describing for discrepancy detection
    # and downstream reporting. Fail-soft: a PO line we can't match leaves the
    # GRN line without an ordered_qty (no false discrepancy).
    ordered_by_product: dict = {}
    total_ordered = None
    if po:
        po_items = po.get("items") if isinstance(po.get("items"), list) else []
        for po_item in po_items:
            if not isinstance(po_item, dict):
                continue
            pid = po_item.get("product_id")
            if pid is None:
                continue
            try:
                ordered_by_product[pid] = ordered_by_product.get(pid, 0) + int(
                    po_item.get("quantity", 0) or 0
                )
            except (TypeError, ValueError):
                continue
        if ordered_by_product:
            total_ordered = sum(ordered_by_product.values())

    item_docs = []
    for item in grn.items:
        doc = item.model_dump()
        ordered = ordered_by_product.get(item.product_id)
        if ordered is not None:
            doc["ordered_qty"] = ordered
        # Stamp the per-line short/exact/over flag so the GRN doc is
        # self-describing and the receiving UI / discrepancy report don't have
        # to recompute it. UNMATCHED when the line isn't on the PO.
        doc["variance_status"] = classify_grn_line_variance(item.received_qty, ordered)
        item_docs.append(doc)

    grn_doc = {
        "grn_id": grn_id,
        "grn_number": grn_number,
        "po_id": grn.po_id,
        "po_number": po.get("po_number") if po else None,
        "vendor_id": vendor_id,
        "vendor_name": po.get("vendor_name") if po else None,
        "store_id": store_id,
        "vendor_invoice_no": grn.vendor_invoice_no,
        # Folded identity for the uniq_std_vendor_invoice_store partial unique
        # index (the atomic backstop behind the racy check-then-insert guard
        # above). None for a DC so DC rows never enter that index.
        "vendor_invoice_no_norm": (
            (_normalize_invoice_no(grn.vendor_invoice_no) or None)
            if not is_dc
            else None
        ),
        "vendor_invoice_date": grn.vendor_invoice_date,
        # F-S3: the receipt document the ops user attached (file_store id +
        # metadata). The accountant reconciliation console reads these to render
        # the "view document" link. None for a DC (attached later) -- and that
        # is now ENFORCED, not merely documented: this dict used to persist the
        # caller's id unconditionally, which is what made the DC subtype a
        # bypass of the authorisation gate above.
        "attachment_file_id": None if is_dc else grn.attachment_file_id,
        "attachment_filename": None if is_dc else grn.attachment_filename,
        "attachment_mime": None if is_dc else grn.attachment_mime,
        "items": item_docs,
        "total_received": total_received,
        "total_accepted": total_accepted,
        "total_rejected": total_rejected,
        "total_ordered": total_ordered,
        "notes": grn.notes,
        "status": "PENDING",
        # F9: subtype + DC fields. dc_matched/linked_bulk_invoice_id are flipped
        # when the DC is reconciled into a bulk invoice (see purchase_invoices).
        "grn_subtype": grn.grn_subtype,
        "dc_number": grn.dc_number if is_dc else None,
        "dc_date": grn.dc_date if is_dc else None,
        "dc_matched": False if is_dc else None,
        "linked_bulk_invoice_id": None,
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
    }

    if grn_repo is not None:
        created = grn_repo.create(grn_doc)
        # F9 P3: the repository swallows insert errors (returns None). With the
        # partial UNIQUE (vendor_id, dc_number, store_id) index on DC rows
        # (schemas.py uniq_dc_vendor_number_store), a concurrent duplicate that
        # raced past the app-level check above surfaces as a DuplicateKeyError
        # inside create() -> None. Re-probe the dup key: if a rival row now
        # holds it, map to the SAME 409 as the app-level guard; any other save
        # failure on a DC is a loud 500 (never a false 201).
        if created is None and is_dc:
            dup = None
            try:
                dup = grn_repo.find_one(
                    {
                        "grn_subtype": GRN_SUBTYPE_DC,
                        "vendor_id": vendor_id,
                        "dc_number": grn.dc_number,
                        "store_id": store_id,
                    }
                )
            except Exception:  # noqa: BLE001
                dup = None
            if dup is not None and dup.get("grn_id") != grn_id:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Delivery Challan '{grn.dc_number}' is already "
                        f"logged for this vendor at this store. Duplicate "
                        f"DC numbers are not allowed."
                    ),
                )
            raise HTTPException(
                status_code=500, detail="Failed to save Delivery Challan"
            )
        # P0-1: the STANDARD mirror of the DC branch above. With the
        # uniq_std_vendor_invoice_store partial unique index, a concurrent
        # duplicate that raced past the app-level guard surfaces as a
        # swallowed DuplicateKeyError (create() -> None). Re-probe: rival row
        # holds the invoice number -> the SAME 409 as the guard; any other
        # save failure is a loud 500, never a false "GRN created".
        if created is None and not is_dc:
            dup = None
            try:
                dup = _find_duplicate_standard_grn(
                    grn_repo,
                    grn.po_id,
                    vendor_id,
                    grn.vendor_invoice_no,
                    exclude_grn_id=grn_id,
                )
            except Exception:  # noqa: BLE001
                dup = None
            if dup is not None:
                raise HTTPException(
                    status_code=409,
                    detail=_duplicate_grn_detail(dup, grn.vendor_invoice_no),
                )
            raise HTTPException(status_code=500, detail="Failed to save goods receipt")

    # F9: audit the DC log (immutable; a DC is the accountable checkpoint between
    # physical lens arrival and workshop work). Fail-soft -- never blocks save.
    if is_dc:
        try:
            audit = get_audit_repository()
            if audit is not None:
                audit.create(
                    {
                        "action": "vendor.dc_log",
                        "entity_type": "grn",
                        "entity_id": grn_id,
                        "user_id": current_user.get("user_id"),
                        "detail": {
                            "grn_number": grn_number,
                            "dc_number": grn.dc_number,
                            "dc_date": grn.dc_date,
                            "vendor_id": vendor_id,
                            "store_id": store_id,
                            "total_received": total_received,
                            "total_accepted": total_accepted,
                        },
                    }
                )
        except Exception:
            pass

    # Anti-fraud / variance: a receiving discrepancy (rejected goods or a
    # short/over shipment vs the PO) raises an accountable SYSTEM task so it is
    # investigated rather than silently absorbed. A DC with no PO has nothing to
    # compare ordered-against, so the PO-discrepancy task only fires for a GRN
    # that has an ordered baseline. Fail-soft -- a task failure must never break
    # the GRN save.
    if total_ordered is not None and grn_has_discrepancy(grn_doc):
        try:
            from ...services.task_triggers import create_system_task
            from ...dependencies import get_task_repository

            po_label = grn_doc.get("po_number") or grn.po_id
            create_system_task(
                get_task_repository(),
                title=f"GRN discrepancy on PO {po_label}",
                description=(
                    f"Goods receipt {grn_number} against PO {po_label} shows a "
                    f"discrepancy: received {total_received}, accepted "
                    f"{total_accepted}, rejected {total_rejected}"
                    + (
                        f" vs ordered {total_ordered}"
                        if total_ordered is not None
                        else ""
                    )
                    + ". Reconcile receipt vs order and vendor invoice "
                    f"{grn.vendor_invoice_no}."
                ),
                priority="P2",
                category="Purchase",
                store_id=grn_doc.get("store_id"),
                dedupe_ref=f"grn:{grn_id}",
            )
        except Exception:
            pass

    return {
        "grn_id": grn_id,
        "grn_number": grn_number,
        "grn_subtype": grn.grn_subtype,
        "dc_number": grn.dc_number if is_dc else None,
        "total_received": total_received,
        "has_discrepancy": grn_has_discrepancy(grn_doc),
        "message": "Delivery Challan logged" if is_dc else "GRN created",
    }


@router.get("/grn/{grn_id}")
async def get_grn(grn_id: str, current_user: dict = Depends(get_current_user)):
    """Get GRN details"""
    grn_repo = get_grn_repository()

    if grn_repo is None:
        return {"grn_id": grn_id}

    grn = grn_repo.find_by_id(grn_id)
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")

    _enrich_grn_names([grn])

    return grn
