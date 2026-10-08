"""Goods receipt list, document upload/download and duplicate detection."""

from datetime import datetime

from ...services.ap_engine import GRN_SUBTYPE_NO_PO
from ...services.cost_mask import mask_receipt
from ...services.purchase_invoice_engine import bill_fy, same_bill
from ...services.purchase_invoice_engine import bill_key as _bill_key
from ._shared import (
    ALLOWED_MIME_TYPES,
    Depends,
    File,
    HTTPException,
    MAX_FILE_SIZE_BYTES,
    Optional,
    Query,
    StreamingResponse,
    UploadFile,
    _GRN_DOCUMENT_KIND,
    _RECEIVE_ROLES,
    _VENDOR_ROLES,
    _get_db,
    _normalize_invoice_no,
    can_access_store_scoped,
    datetime,
    get_current_user,
    get_file_store,
    get_grn_repository,
    get_vendor_repository,
    hashlib,
    io,
    logger,
    require_roles,
    router,
    timedelta,
    uuid,
    validate_store_access,
)
from .models import GRN_SUBTYPE_DC, _GRN_SUBTYPES
from .numbering import GRN_PLACEHOLDER_PREFIX, generate_grn_number, grn_number_pending


# The placeholder a receipt carries between its insert and its number.
_PLACEHOLDER_RE = "^" + GRN_PLACEHOLDER_PREFIX
# A live request numbers its own row milliseconds after the insert; a row
# still on the placeholder after this long lost its worker.
_STRANDED_AFTER = timedelta(minutes=1)


def _number_receipt(grn_repo, grn_id: str, store_id) -> Optional[str]:
    """THE receipt numbering write, shared by the live create and the
    stranded-receipt healer below (audit F28). Claim the row while it is still
    on its placeholder, THEN mint, THEN write the number only onto a row still
    on its placeholder under this claim. Whoever loses the row takes no
    number, and a number already written is never overwritten. Returns the
    number written, or None when another worker has (or had) the row.

    A claim older than _STRANDED_AFTER belonged to a worker that died; it is
    taken over. ponytail: a worker stalled longer than that between its claim
    and its write spends one serial (the takeover numbers the row, the stalled
    write lands nowhere); a counter reservation released on loss would close
    it, if it is ever seen.

    A repository without a collection (the in-memory test doubles) has one
    worker and no healer: mint and write."""
    coll = getattr(grn_repo, "collection", None)
    if coll is None:
        number = generate_grn_number(store_id)
        return number if grn_repo.update(grn_id, {"grn_number": number}) else None
    now = datetime.now()
    claim = str(uuid.uuid4())
    won = coll.update_one(
        {
            "grn_id": grn_id,
            "grn_number": {"$regex": _PLACEHOLDER_RE},
            "$or": [
                {"numbering_claimed_at": {"$exists": False}},
                {"numbering_claimed_at": {"$lt": now - _STRANDED_AFTER}},
            ],
        },
        {"$set": {"numbering_claim": claim, "numbering_claimed_at": now}},
    )
    if not getattr(won, "modified_count", 0):
        return None
    number = generate_grn_number(store_id)
    wrote = coll.update_one(
        {"grn_id": grn_id, "numbering_claim": claim, "grn_number": {"$regex": _PLACEHOLDER_RE}},
        {
            "$set": {"grn_number": number},
            "$unset": {"numbering_claim": "", "numbering_claimed_at": ""},
        },
    )
    return number if getattr(wrote, "modified_count", 0) else None


def _number_stranded_receipts(grn_repo, grn_id: Optional[str] = None) -> None:
    """Number every receipt (only ``grn_id``, when given) whose worker died
    between its insert and its number (a killed worker, a deploy mid-request),
    so no row keeps PENDING/<grn_id> for good (audit F28). Runs at the start
    of every receipt create and list (the pending receipts panel), and through
    _receipt_numbered on every read of one receipt. A row younger than
    _STRANDED_AFTER may still be numbered by its own request, so it is left
    alone: until then the duplicate guard says the receipt is still getting
    its number and accept refuses it (a placeholder must never reach a stock
    unit). Each row goes through _number_receipt, the same claimed write the
    live create uses, so a stalled create and this never both number a row.
    Fail-soft: what it cannot do now, the next create or accept does."""
    coll = getattr(grn_repo, "collection", None)
    if coll is None:
        return
    stale = datetime.now() - _STRANDED_AFTER
    query = {"grn_number": {"$regex": _PLACEHOLDER_RE}, "created_at": {"$lt": stale}}
    if grn_id:
        query["grn_id"] = grn_id
    try:
        rows = list(coll.find(query, {"_id": 0, "grn_id": 1, "store_id": 1}).limit(20))
        for row in rows:
            _number_receipt(grn_repo, row["grn_id"], row.get("store_id"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VENDOR] stranded receipt numbering skipped: %s", exc)


def _receipt_numbered(grn_repo, grn: dict) -> dict:
    """One receipt as a reader or an action sees it (audit F28): a receipt
    still on its placeholder whose request died is numbered first, then read
    back. A fresh one is still being numbered by its own request and comes
    back on its placeholder (grn_number_pending says so)."""
    if grn_number_pending(grn):
        _number_stranded_receipts(grn_repo, grn.get("grn_id"))
        grn = grn_repo.find_by_id(grn.get("grn_id")) or grn
    return grn


def _require_receipt_number(grn_repo, grn: dict) -> dict:
    """_receipt_numbered, refusing a receipt that still has no number: accept
    would stamp the placeholder on every unit, the audit row and the bill
    draft, and void on its immutable audit, for good."""
    grn = _receipt_numbered(grn_repo, grn)
    if grn_number_pending(grn):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "GRN_NUMBER_PENDING",
                "message": (
                    "This goods receipt is still getting its receipt number. "
                    "Wait a minute, then try again."
                ),
            },
        )
    return grn


# ============================================================================
# GRN (GOODS RECEIVED NOTE) ENDPOINTS
# ============================================================================


@router.get("/grn")
async def list_grns(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    po_id: Optional[str] = Query(None),
    # F9: Delivery-Challan filters. The accountant's open-DC panel queries
    # grn_subtype=DELIVERY_CHALLAN & dc_matched=false & vendor_id=X & status=ACCEPTED
    # to pick the DCs to reconcile into one bulk invoice.
    grn_subtype: Optional[str] = Query(None),
    dc_matched: Optional[bool] = Query(None),
    vendor_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="DC date >= (ISO)"),
    date_to: Optional[str] = Query(None, description="DC date <= (ISO)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """List GRNs with filters (incl. F9 Delivery-Challan filters). F60: a GRN
    carries the supplier's bill number / date and the bill-scan id, so only the
    receiving roles read it (every screen that lists GRNs is a purchase screen)."""
    grn_repo = get_grn_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )

    if grn_repo is None:
        return {"grns": [], "total": 0}
    _number_stranded_receipts(grn_repo)

    filter_dict: dict = {}
    if active_store:
        filter_dict["store_id"] = active_store
    if status:
        filter_dict["status"] = status
    if po_id:
        filter_dict["po_id"] = po_id
    if grn_subtype:
        # Normalise to the canonical subtype string.
        sub = str(grn_subtype).strip().upper().replace("-", "_")
        if sub in _GRN_SUBTYPES:
            filter_dict["grn_subtype"] = sub
    if dc_matched is not None:
        filter_dict["dc_matched"] = bool(dc_matched)
    if vendor_id:
        filter_dict["vendor_id"] = vendor_id
    # dc_date range filter (string ISO compares lexicographically for YYYY-MM-DD).
    if date_from or date_to:
        rng: dict = {}
        if date_from:
            rng["$gte"] = date_from
        if date_to:
            rng["$lte"] = date_to
        filter_dict["dc_date"] = rng

    # A row still on its placeholder has no receipt number to show, void or
    # accept by (a stranded one was numbered just above; a fresh one is still
    # being numbered by its own request): it is left out until it has one,
    # so the panel never names a PENDING/<id> placeholder (audit F28).
    grns = [
        g
        for g in grn_repo.find_many(filter_dict, skip=skip, limit=limit) or []
        if not grn_number_pending(g)
    ]

    _enrich_grn_names(grns)
    # The price paid on a line goes through the one cost rule (cost_mask).
    for g in grns:
        mask_receipt(g, current_user)

    return {"grns": grns, "total": len(grns)}


def _enrich_grn_names(grns: list) -> None:
    """backlog #4: add created_by_name / vendor_name beside the raw ids on GRN
    rows so the UI shows who received the goods (a name, not a UUID). In-place,
    batched, fail-soft."""
    if not grns:
        return
    try:
        from ...services.name_resolver import user_name_map, vendor_name_map

        db = _get_db()
        umap = user_name_map(db, [g.get("created_by") for g in grns])
        vmap = vendor_name_map(db, [g.get("vendor_id") for g in grns])
        for g in grns:
            cb = g.get("created_by")
            if cb and not g.get("created_by_name") and str(cb) in umap:
                g["created_by_name"] = umap[str(cb)]
            vid = g.get("vendor_id")
            if vid and not g.get("vendor_name") and str(vid) in vmap:
                g["vendor_name"] = vmap[str(vid)]
            # D14: a walk-in dealer bought from without a PO has no supplier
            # record -- the name typed at receipt is the name.
            if not g.get("vendor_name") and g.get("dealer_name"):
                g["vendor_name"] = g["dealer_name"]
    except Exception:  # noqa: BLE001
        pass


@router.post("/grn/upload-doc")
async def upload_grn_doc(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_roles(*_RECEIVE_ROLES)),
):
    """F-S3: upload the goods-receipt document (vendor invoice/challan image or
    PDF) and get back a file_id to attach to the GRN.

    The ops user (Superadmin/Admin/Store Manager) uploads the receipt FIRST,
    then submits the GRN with the returned file_id. create_grn rejects a
    STANDARD GRN that has no attachment_file_id (ATTACHMENT_REQUIRED), so this
    is the only way to clear the gate. Persists the bytes durably in the
    GridFS-backed file store (Railway disk is ephemeral) -- mirrors the
    expenses upload-bill pattern: size + MIME validation, then store.put(...).
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Read + validate before persisting anything.
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB cap",
        )
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File type '{mime}' not allowed. Accepted: "
                f"{sorted(ALLOWED_MIME_TYPES)}"
            ),
        )

    store = get_file_store()
    if store is None:
        # Storage unavailable: fail LOUD with 503 so the UI keeps the user on
        # the upload step rather than letting them proceed paperwork-less.
        raise HTTPException(status_code=503, detail="File storage unavailable")

    sha256 = hashlib.sha256(content).hexdigest()
    # The stamped store is what authorises this blob at GRN-create time, so a
    # blob we cannot stamp is a blob that will be refused later with a message
    # that reads like a forged id. Fail LOUD here instead of minting it: the
    # caller can pick a store and retry, rather than looping on an upload that
    # can never be attached.
    upload_store = current_user.get("active_store_id")
    if not upload_store:
        raise HTTPException(
            status_code=400,
            detail=(
                "Select a store before uploading a goods-receipt document -- "
                "the document is filed against the receiving store."
            ),
        )

    file_id = store.put(
        content=content,
        filename=file.filename,
        mime_type=mime,
        metadata={
            "kind": "grn_document",
            "store_id": upload_store,
            "uploaded_by": current_user.get("user_id"),
            "sha256": sha256,
        },
    )
    if not file_id:
        raise HTTPException(status_code=500, detail="File store write failed")

    return {
        "file_id": file_id,
        "filename": file.filename,
        "mime": mime,
        "size": len(content),
        "sha256": sha256,
        "persisted": True,
    }


@router.get("/grn/{grn_id}/document")
async def download_grn_doc(
    grn_id: str,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """F-S3: stream the goods-receipt document attached to a GRN.

    The accountant reconciliation console links here to view the source invoice/
    challan the ops user uploaded at receipt. Store-scoped: a GRN outside the
    caller's store scope reads as 404 (no cross-store document leak)."""
    grn_repo = get_grn_repository()
    if grn_repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    grn = grn_repo.find_one({"grn_id": grn_id})
    if grn is None:
        raise HTTPException(status_code=404, detail="GRN not found")

    # Store-scope (SEC #2 object-level pattern): cross-store roles
    # (SUPERADMIN/ADMIN) may read any GRN's document; a store-level caller can
    # only read GRNs stamped with one of their stores. A mismatch reads as 404
    # (not 403) so a document's existence in another store isn't disclosed.
    if not can_access_store_scoped(grn.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="GRN not found")

    file_id = grn.get("attachment_file_id")
    if not file_id:
        raise HTTPException(status_code=404, detail="No document attached to this GRN")

    store = get_file_store()
    if store is None:
        raise HTTPException(status_code=503, detail="File storage unavailable")

    # Defence in depth behind the create-time kind check: this route only ever
    # streams THIS router's own kind of file, so a foreign id persisted on a GRN
    # (legacy row, future write path) reads as "no longer available", not bytes.
    rec = store.get(file_id, require_kind=_GRN_DOCUMENT_KIND)
    if rec is None:
        raise HTTPException(status_code=404, detail="Document file no longer available")

    file_content, filename, file_mime = rec
    return StreamingResponse(
        io.BytesIO(file_content),
        media_type=file_mime,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


def _find_duplicate_receipt(grn_repo, grn, vendor_id, photo_sha=None, exclude_grn_id=None):
    """The first live (non-VOID) receipt, in ANY shop, already holding this
    bill -- for every receipt but a Delivery Challan (its own guard keys on
    the DC number). It is the same bill when it has
      * the same bill photo, for a "Bought without PO" receipt (D14) -- the
        same upload (a double-pressed post) or the same bytes uploaded again
        (a retry after a page reload mints a new file id; upload-doc stamps
        the sha256, which every receipt keeps), or
      * the same bill under THE same-bill rule (purchase_invoice_engine.
        same_bill, which both bill doors use too): the same purchase order's
        number, or the same seller's number in the same financial year.
    A VOIDed receipt frees its bill (the sanctioned correction path). The
    atomic twins: uniq_std_vendor_invoice_store (keyed on bill_key) and
    uniq_nopo_bill_hash. Fail-soft on a vendor read.

    ponytail: candidates are the newest 500 receipts carrying this bill
    number (either key shape), plus the PO's and the supplier's receipts for
    rows older than the number key -- index vendor_id + created_at if one
    supplier ever holds thousands of receipts.
    """
    if grn_repo is None or grn.grn_subtype == GRN_SUBTYPE_DC:
        return None

    def live(r) -> bool:
        return (
            r.get("grn_id") != exclude_grn_id
            and r.get("status") != "VOID"
            and r.get("grn_subtype") != GRN_SUBTYPE_DC
        )

    if grn.grn_subtype == GRN_SUBTYPE_NO_PO:
        photo = str(grn.attachment_file_id or "").strip()
        for probe in (
            {"attachment_file_id": photo} if photo else None,
            {"attachment_sha256": photo_sha} if photo_sha else None,
        ):
            for r in grn_repo.find_many(probe, limit=50) if probe else []:
                if live(r):
                    return r

    norm = _normalize_invoice_no(grn.vendor_invoice_no)
    if not norm:
        return None
    now = datetime.now()
    key = _bill_key(grn.vendor_invoice_no, grn.vendor_invoice_date, now)
    candidates: dict = {}
    for flt in (
        {"vendor_invoice_no_norm": key},
        {"vendor_invoice_no_norm": norm} if key != norm else None,
        {"po_id": grn.po_id} if grn.po_id else None,
        {"vendor_id": vendor_id} if vendor_id else None,
    ):
        if not flt:
            continue
        try:
            rows = grn_repo.find_many(flt, sort=[("created_at", -1)], limit=500) or []
        except Exception:  # noqa: BLE001 - fail-soft, like the DC guard
            rows = []
        for r in rows:
            candidates.setdefault(r.get("grn_id"), r)

    def find_vendor(vid):
        repo = get_vendor_repository()
        return repo.find_by_id(vid) if repo is not None else None

    mine = {
        "no": norm,
        "fy": bill_fy(grn.vendor_invoice_date, now),
        "po_id": grn.po_id,
        "vendor_id": vendor_id,
        "dealer_name": grn.dealer_name,
    }
    for r in candidates.values():
        theirs = {
            "no": r.get("vendor_invoice_no"),
            "fy": bill_fy(r.get("vendor_invoice_date"), r.get("created_at")),
            "po_id": r.get("po_id"),
            "vendor_id": r.get("vendor_id"),
            "dealer_name": r.get("dealer_name"),
        }
        if live(r) and same_bill(mine, theirs, find_vendor):
            return r
    return None


def _duplicate_grn_detail(dup: dict, invoice_no) -> dict:
    """409 payload for a duplicate STANDARD receipt.

    The message must say the receipt EXISTS and where to finish it -- never
    'try again': the person reading it has just watched a submit apparently
    fail (timeout, EXPRESS_PARTIAL) and a retry is exactly what would have
    double-minted the stock before this guard existed.
    """
    number = dup.get("grn_number") or dup.get("grn_id")
    status = dup.get("status") or "PENDING"
    if grn_number_pending(dup):
        # Saved, but its request has not given it a number yet (or died
        # before it could): never hand out the placeholder (audit F28).
        return {
            "code": "GRN_DUPLICATE",
            "grn_id": dup.get("grn_id"),
            "grn_number": None,
            "grn_status": status,
            "message": (
                f"A goods receipt for vendor invoice '{invoice_no}' was "
                f"already saved and is still getting its receipt number - do "
                f"not create it again. In a minute it shows numbered in the "
                f"receiving screen's pending receipts panel; finish (accept) "
                f"or void it there."
            ),
        }
    if status == "ACCEPTED":
        hint = (
            "its goods are already on the shelf. Do not receive this delivery "
            "again - if the vendor really shipped a second box under the same "
            "invoice number, check with purchase first."
        )
    else:
        hint = (
            "open the receiving screen's pending receipts panel to finish "
            "(accept) or void it - do not create it again."
        )
    return {
        "code": "GRN_DUPLICATE",
        "grn_id": dup.get("grn_id"),
        "grn_number": dup.get("grn_number"),
        "grn_status": status,
        "message": (
            f"Goods receipt {number} already exists for vendor invoice "
            f"'{invoice_no}' ({status}) - {hint}"
        ),
    }
