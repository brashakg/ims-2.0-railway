"""POST /grn/{grn_id}/accept - the three-way match and the stock mint."""

from contextvars import ContextVar
from datetime import timedelta
from typing import Tuple
from urllib.parse import quote

from ._shared import (
    Depends,
    HTTPException,
    List,
    Optional,
    _RECEIVE_ROLES,
    _get_db,
    _pm,
    can_access_store_scoped,
    datetime,
    get_grn_repository,
    get_product_repository,
    get_purchase_order_repository,
    get_stock_repository,
    is_online_store,
    logger,
    require_roles,
    router,
)
from ...services.po_variance_engine import _ordered_by_product
from .gst import _promote_cost_from_rate
from .po_detail import (
    _as_read,
    _order_or_503,
    _qty,
    _received_by_product,
    _received_per_line,
    _refresh_received,
    beyond_open_quantity,
    puts_nothing_in_stock,
)
from .numbering import (
    _grn_barcode,
    _grn_stock_audit,
    compute_po_receipt_state,
    held_units,
)
from .grn import _require_receipt_number
from .grn_accept_lock import (
    _GRN_MINT_DUPLICATE,
    _advance_grn_terminal_status,
    _claim_grn_for_accept,
    _claimed_receipt,
    _finalise_grn_accept_metadata,
    _grn_accept_conflict,
    _grn_accept_heartbeat_tick,
    _guarded_grn_write,
    _grn_mint_unit,
    _grn_unit_index_present,
    _receipt_units,
    _release_grn_accept_claim,
    _stock_create_raises_on_duplicate,
)


@router.post("/grn/{grn_id}/accept")
async def accept_grn(
    grn_id: str, current_user: dict = Depends(require_roles(*_RECEIVE_ROLES))
):
    """Post a goods-receipt note: mint serialized stock for the accepted units,
    advance the PO to PARTIALLY_RECEIVED / RECEIVED, and write an audit trail.

    Stock is written one row per physical unit into the canonical serialized
    `stock_units` collection via get_stock_repository -- the SAME path the
    inventory /stock/add screen uses (barcode + location + AVAILABLE status), so
    a GRN-received unit is a first-class sellable unit. There is NO parallel
    stock write.

    Double-click safe FOR THIS ENDPOINT (F8): acceptance is CLAIMED with one
    guarded single-document update (status-keyed + lock field) before any stock
    is minted, so of two concurrent POSTs on the SAME grn_id exactly one mints
    and the other gets a 409. (POST /grn/express creates a NEW grn_id per
    click, so the per-GRN claim never engages there -- the create-time
    duplicate guard in _create_grn_impl covers it: a second non-VOID STANDARD
    receipt for the same vendor invoice number is a 409.) Sequential re-POSTs are
    additionally idempotent -- the minting loop skips any (grn_id, product_id,
    grn_line_index) that already has units in stock_units, so a partially-failed
    accept can be safely retried without double-counting.

    Fail-soft ordering: the stock write happens first; the GRN status flip, the
    PO state update, and the per-unit stock_audit rows all follow and are each
    wrapped so that a logging/secondary failure can never lose the stock that
    was already received.
    """
    return await _accept_grn_impl(grn_id, current_user)


async def _accept_grn_impl(grn_id: str, current_user: dict) -> dict:
    """Shared GRN-accept engine behind POST /grn/{grn_id}/accept (and
    POST /grn/express).

    Behavior-preserving extraction of the original accept_grn body: the
    store-scope guard, the PENDING/PARTIALLY_ACCEPTED status gate, idempotent
    per-(grn, line) stock minting, PO receipt math and the audit trail all run
    here unchanged for both callers. Callers pass the authenticated
    ``current_user`` their own ``require_roles(*_RECEIVE_ROLES)`` gate produced.
    """
    grn_repo = get_grn_repository()

    if grn_repo is None:
        return {"message": "GRN accepted, stock added"}

    grn = grn_repo.find_by_id(grn_id)
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")

    # Store-scope (SEC #2 object-level pattern, same as download_grn_doc): a
    # cross-store role (SUPERADMIN/ADMIN) may accept any GRN; a store-level caller
    # may only accept GRNs stamped with one of their stores. A mismatch reads as
    # 404 (not 403) so a GRN's existence in another store isn't disclosed. This
    # gates the stock mint + PO advance + audit writes below.
    if not can_access_store_scoped(grn.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="GRN not found")

    # A receiving manager's "Add to stock" (the route's gate: receiving is
    # managers only, owner 2026-09-28) takes the units in -- those beyond the
    # order too: they are accepted and FLAGGED for the store manager, never
    # refused (owner 2026-10-08 (b), _flag_over_delivery). Only units the
    # order has CANCELLED are refused (_hold_order_open_for_receipt). The
    # order cap is the catalogue release's alone (release_held_receipts).
    return _put_on_shelf(grn_repo, grn_id, grn, current_user)


def held_receipts(product_id: str) -> List[dict]:
    """The receipts holding units of `product_id` back (PARTIALLY_ACCEPTED with
    it in unresolved_lines), oldest first."""
    grn_repo = get_grn_repository()
    if grn_repo is None or not product_id:
        return []
    return (
        grn_repo.find_many(
            {"status": "PARTIALLY_ACCEPTED", "unresolved_lines.product_id": product_id},
            sort=[("created_at", 1)],
        )
        or []
    )


# Set while a failed accept runs its releases (_put_on_shelf): a release it
# runs that fails in turn does not run them again.
_AFTER_A_FAILED_ACCEPT: ContextVar = ContextVar("after_a_failed_accept", default=False)

# A PO in one of these will never bring the item in again.
_PO_DONE_STATUSES = ("CANCELLED", "RECEIVED", "CLOSED")


def _line_qty(it: dict, *keys) -> int:
    for k in keys:
        try:
            if it.get(k) is not None:
                return int(it.get(k) or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def draft_discard_blockers(product_id: str) -> Optional[List[str]]:
    """What still brings `product_id` in, or still waits on it -- each as one
    sentence a person can act on, naming only an action the server allows.

      * an open order line for it that has not been received in full (order
        not cancelled, not fully received, not closed);
      * a receipt still waiting on it: PENDING with a line for it that is
        accepting units, or PARTIALLY_ACCEPTED holding it. A finished receipt
        whose line for it accepted nothing (all rejected) waits on nothing.

    A draft one of these names is still coming in, or already here: discarding
    it would leave a box arriving for -- or held behind -- a deleted product,
    whose catalogue task points at a queue it is no longer in. None if the
    question cannot be answered (no database): the caller refuses then."""
    if not product_id:
        return []
    po_repo = get_purchase_order_repository()
    grn_repo = get_grn_repository()
    if po_repo is None or grn_repo is None:
        return None
    blockers: List[str] = []
    # Strict reads (the raw collections): BaseRepository.find_many prints a
    # read error and answers [] -- "nothing names it", a licence to discard.
    # A read error raises; the delete answers 503.
    for po in _pm.strict_find_many(
        po_repo, {"items.product_id": product_id, "status": {"$nin": list(_PO_DONE_STATUSES)}}
    ):
        lines = [it for it in (po.get("items") or []) if it.get("product_id") == product_id]
        if lines and all(
            _line_qty(it, "received_qty") >= _line_qty(it, "ordered_qty", "quantity") > 0
            for it in lines
        ):
            continue
        number = po.get("po_number") or po.get("po_id")
        # Any open order can drop what is still due (po_detail.cancel_po_line /
        # cancel_po, #1165), whatever already arrived on it.
        blockers.append(
            f"Purchase order {number} still orders it: cancel that line in "
            "Purchase Orders first (Cancel line), or finish the item in the "
            "product editor."
        )
    for g in _pm.strict_find_many(
        grn_repo,
        {"items.product_id": product_id, "status": {"$in": ["PENDING", "PARTIALLY_ACCEPTED", "ESCALATED"]}},
    ):
        status = str(g.get("status") or "").upper()
        if status == "PARTIALLY_ACCEPTED":
            if not any(
                (u or {}).get("product_id") == product_id for u in (g.get("unresolved_lines") or [])
            ):
                continue
        elif not any(
            it.get("product_id") == product_id and _line_qty(it, "accepted_qty") > 0
            for it in (g.get("items") or [])
        ):
            continue
        number = g.get("grn_number") or g.get("grn_id")
        if status == "ESCALATED":
            # No door settles an escalated receipt here (neither accept nor
            # void takes one): name the action that works, nothing else.
            blockers.append(
                f"Receipt {number} names it and is escalated to head office, so it "
                "can be neither accepted nor voided here: finish the item in the "
                "product editor."
            )
        else:
            blockers.append(
                f"Receipt {number} is holding it: if none of that receipt's units "
                "went on the shelf and they are going back, void it in Receive "
                "Goods; otherwise finish the item in the product editor."
            )
    return blockers


def release_held_receipts(product_id: str) -> List[dict]:
    """Audit C1: finishing a product in the catalogue puts every unit a receipt
    was holding for it on the shelf -- through _put_on_shelf, the SAME path
    "Add to stock" runs, so there is one mint and one set of guards.

    Called by the product door (PUT /products/{id}) once an edit leaves the
    product catalogue-complete. The receipt is completed as the manager who
    accepted it (receiving is managers-only; the cataloguer only finished the
    product). Fail-soft per receipt: the catalogue save stands, the receipt
    stays held and says so, and "Add to stock" retries it.

    Oldest receipt first, and never past what its order asked for
    (order_cap): a line that would put more of its product on the shelf than
    the PO ordered (a second receipt of the same box, say) stays held for the
    shop's store manager to void or accept -- a person decides, not the
    cataloguer's save. The receipt's lines within the order still go on the
    shelf."""
    try:
        grn_repo = get_grn_repository()
        held = held_receipts(product_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("[VENDOR] held-receipt lookup for %s failed: %s", product_id, exc)
        return []
    out: List[dict] = []
    for grn in held:
        gid = grn.get("grn_id")
        actor = {"user_id": grn.get("accepted_by") or grn.get("created_by")}
        try:
            out.append(_put_on_shelf(grn_repo, gid, grn, actor, order_cap=True))
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[VENDOR] GRN %s: releasing the units held for %s failed (%s) -- "
                "the receipt stays held; 'Add to stock' retries it",
                gid,
                product_id,
                exc,
            )
    return out


def _order_cap(po_repo, po_id, grn):
    """The catalogue release's order cap: over(product_id, to_mint) is None
    when `to_mint` more units keep the order within what it asks for, else
    the hold's details ({ordered, on_shelf, busy}).

    Read UNDER this receipt's accept claim, from the one count of what the
    order received (po_detail._received_by_product: every other receipt by
    what it accepted or put in stock, wherever a transfer moved it since --
    in ANY status, so a unit sold or moved still fills the order) plus this
    receipt's own units by origin (_receipt_units). Another receipt of the
    order that an accept holds a live claim on counts WHOLE: of two catalogue
    saves racing over two receipts of one order, the later read always sees
    the earlier claim (or its units), so at most one shelves -- both may
    hold, and the store manager accepts one. `busy`: only that claim makes
    it over. Unreadable order or counts: raises, and the receipt stays held."""
    po = _order_or_503(po_repo, po_id)
    if not po:
        raise RuntimeError(f"PO {po_id} could not be read")
    ordered = _ordered_by_product(po)
    stock_repo = get_stock_repository()
    counts: dict = {}

    def received(pid, whole) -> int:
        if whole not in counts:
            counts[whole] = _received_by_product(
                po, leave_out_grn=grn.get("grn_id"), in_flight_whole=whole
            )
        return _qty(counts[whole].get(pid))

    def over(product_id, to_mint) -> Optional[dict]:
        mine = _receipt_units(stock_repo, grn, product_id=product_id)
        want = ordered.get(product_id, 0)
        if received(product_id, True) + mine + to_mint <= want:
            return None
        on_shelf = received(product_id, False) + mine
        return {"ordered": want, "on_shelf": on_shelf, "busy": on_shelf + to_mint <= want}

    return over


def _hand_to_store_manager(db, grn_id: str, grn: dict, over: List[dict], product_repo) -> None:
    """Lines held beyond the order (order_cap) are the shop's store manager's
    to decide (owner 2026-09-29: a receipt problem is his task, by person).
    One task per receipt, item and person: a second item found over the order
    on the same receipt is never lost behind a task closed for the first."""
    store_id = grn.get("store_id")
    shop = _shop_label(db, store_id)
    receipt = grn.get("grn_number") or grn_id
    po = grn.get("po_number") or grn.get("po_id")
    # Opens that vendor's "Receipts still waiting", where the receipt is voided.
    vendor_id = grn.get("vendor_id")
    link = "/purchase/receive" + (f"?vendor_id={quote(str(vendor_id))}" if vendor_id else "")
    people, managers = _people_for(db, store_id, "STORE_MANAGER", entity_wide=False)
    if managers:
        title = f"Check receipt {receipt} at {shop}: more than PO {po} ordered"
    else:
        logger.error(
            "[VENDOR] GRN %s holds more than its PO ordered at %s and NO store "
            "manager covers that shop -- raising the task for the admins",
            receipt,
            store_id,
        )
        title = (
            f"No store manager for {shop}: receipt {receipt} holds more than "
            f"PO {po} ordered"
        )
    for o in over:
        prod = product_repo.find_by_id(o["product_id"]) if product_repo else None
        item = (
            f"{_held_item_label(prod, o['product_id'])}: {held_units(o)} held, "
            f"PO ordered {o['ordered']}, {o['on_shelf']} already received on it"
            + (
                "; another receipt of this order was going into stock at that moment"
                if o.get("busy")
                else ""
            )
        )
        # Both answers work whatever else the receipt put on the shelf (a void
        # does not, once it has: R1-14), so the advice never goes stale.
        description = (
            f"Receipt {receipt} at {shop} is holding more than its order needs "
            f"({item}). In Receive Goods > Receipts still waiting: if the vendor "
            "did not send these extra units (a second receipt of the same box, "
            "say), press 'Not received'; if they really did, press 'Add to stock'."
        )
        for uid, task_store in people or [(None, store_id)]:
            _raise_once(
                db,
                dedupe_ref=f"grn_over_order:{grn_id}:{o['product_id']}:{uid or 'nobody'}",
                title=title,
                description=description,
                category="Purchase",
                store_id=task_store,
                assigned_to=uid,
                extra={"grn_id": grn_id, "link": link},
            )


def _put_on_shelf(
    grn_repo,
    grn_id: str,
    grn: dict,
    current_user: dict,
    order_cap: bool = False,
) -> dict:
    """THE one path that puts a receipt's accepted units on the shelf. "Add to
    stock" (after its store-scope check), express receive and the catalogue
    release (release_held_receipts) all run it. `order_cap` (the release)
    holds any line that would take its product past what the order asks for,
    for the shop's store manager to decide."""
    stock_repo = get_stock_repository()
    po_repo = get_purchase_order_repository()

    # A receipt still on its PENDING/<id> placeholder has no number yet: a
    # stranded one (its request died) is numbered here, a fresh one is refused
    # until its own request has numbered it (audit F28).
    grn = _require_receipt_number(grn_repo, grn)

    # PENDING is the normal first accept. PARTIALLY_ACCEPTED is re-accept after a
    # "Catalog now" -- some lines were held last time because their product was
    # not yet catalogued; the per-(grn,product) idempotency guard below skips the
    # already-minted lines and mints only the newly-resolved ones.
    if grn.get("status") not in ("PENDING", "PARTIALLY_ACCEPTED"):
        raise HTTPException(status_code=400, detail="GRN is not pending")

    # W1.4 / OS-006 (belt and braces): accepting mints real stock_units at the
    # GRN's store. A legacy/imported GRN stamped with an ONLINE store must not
    # create owned stock on a pooled, stockless store.
    if is_online_store(None, grn.get("store_id")):
        raise HTTPException(
            status_code=400,
            detail=(
                "This goods receipt is addressed to an online store, which "
                "holds no stock. Re-create it against a physical shop."
            ),
        )

    # F8 -- CLAIM the receipt before a single unit is minted. The status test
    # above is only a friendly error message; THIS guarded single-document
    # update is the authority. Exactly one of two racing double-click POSTs
    # matches the filter, so exactly one runs the minting loop; the loser 409s
    # without touching stock, the PO or the audit trail.
    # The claim time is captured HERE, not inside the mint body. Everything
    # between this line and the first unit -- the PO fetch, the product lookup,
    # the cost backfill write -- is a round trip that can park on a blackholed
    # socket for minutes (prod sets no socketTimeoutMS). Seeding the heartbeat
    # clock after those reads let a woken worker start with a FRESH clock, so
    # the first tick was not due and it minted the whole delivery without ever
    # re-proving ownership. Measured on the previous build: 24 real sellable
    # units behind a receipt that had been VOIDed in the meantime.
    claimed_at = datetime.now()
    claim_token = _claim_grn_for_accept(grn_repo, grn_id, current_user.get("user_id"))
    if claim_token is None:
        raise _grn_accept_conflict(grn_repo, grn_id)
    # The caller's copy was read before the claim: a 'Not received' that
    # finished in between dropped lines this copy still accepts (panel P1).
    grn = _claimed_receipt(grn_repo, grn_id, claim_token)

    deferred_releases: List[str] = []
    progress: dict = {}
    try:
        # No unit is minted against units the order has cancelled, whoever
        # presses (main #1165, under this claim).
        _hold_order_open_for_receipt(po_repo, grn)
        result = _accept_grn_claimed(
            grn_id,
            grn,
            current_user,
            grn_repo,
            stock_repo,
            po_repo,
            claim_token,
            claimed_at,
            order_cap=order_cap,
            deferred_releases=deferred_releases,
            progress=progress,
        )
    except Exception:
        # THE one way out of an accept that died part-way -- a count it could
        # not read, the order cap's reads, a unit that did not insert, a lost
        # heartbeat: still under the claim, the hold is rewritten to what the
        # receipt really holds now.
        if progress:
            _record_what_a_dead_accept_holds(
                grn_repo, stock_repo, po_repo, grn, claim_token, **progress
            )
        # Hand the claim back (token-guarded) so the operator can retry
        # immediately instead of waiting out the stale window. The per-(grn,
        # line) mint guard makes that retry idempotent.
        _release_grn_accept_claim(grn_repo, grn_id, claim_token)
        # A cost the promote already wrote stays written (the retry promotes
        # nothing), and a catalogue save that lost its release to this claim
        # swallowed its 409: both release now, or they wait on a finished item
        # for ever. Once per failure: a release this runs that fails in turn
        # runs only its own promote's releases, so a failure that keeps
        # failing never retries itself for ever.
        first = not _AFTER_A_FAILED_ACCEPT.get()
        token = _AFTER_A_FAILED_ACCEPT.set(True)
        try:
            if first:
                release_finished_meanwhile(grn_repo, grn_id, deferred_releases)
            else:
                for pid in dict.fromkeys(deferred_releases):
                    release_held_receipts(pid)
        except Exception as exc:  # noqa: BLE001 - never mask the accept's error
            logger.error("[VENDOR] GRN %s: the release after a failed accept failed: %s", grn_id, exc)
        finally:
            _AFTER_A_FAILED_ACCEPT.reset(token)
        raise
    # The receipt's cost finished a draft mid-accept (gst._finished_by_the_promote):
    # its OTHER held receipts go on the shelf now that this claim is handed back.
    release_finished_meanwhile(grn_repo, grn_id, deferred_releases)
    return result


def release_finished_meanwhile(grn_repo, grn_id: str, also=()) -> None:
    """Run by every holder of a receipt's claim once it is handed back -- an
    accept (_put_on_shelf), a void, 'Not received'. A line the receipt holds
    for the catalogue whose product is complete NOW was finished while the
    claim was held: that save's release lost the claim (and swallowed its
    409), so the release is run here -- whichever of the two finishes last
    sees the other (R1-19, follow-ups 1 and 43). `also`: products the caller
    already knows to release. A failed accept runs it once per failure
    (_put_on_shelf), never from a release it ran itself: a release that keeps
    failing would retry itself for ever. Fail-soft: the hold stands, and 'Add
    to stock' or the next save retries it."""
    pids = list(also)
    try:
        grn = grn_repo.find_by_id(grn_id) or {}
        product_repo = get_product_repository()
        if grn.get("status") == "PARTIALLY_ACCEPTED" and product_repo is not None:
            for ln in grn.get("unresolved_lines") or []:
                if ln.get("reason") != "incomplete_catalog":
                    continue
                now_prod = product_repo.find_by_id(ln.get("product_id"))
                if now_prod is not None and not _pm.compute_catalog_status(now_prod)[1]:
                    pids.append(ln["product_id"])
    except Exception:  # noqa: BLE001
        logger.warning("[VENDOR] GRN %s: finished-meanwhile check failed", grn_id, exc_info=True)
    for pid in dict.fromkeys(pids):
        release_held_receipts(pid)


def _record_what_a_dead_accept_holds(
    grn_repo, stock_repo, po_repo, grn, claim_token, decided, line_index, left=None
) -> None:
    """A re-accept of a HELD receipt (PARTIALLY_ACCEPTED: the catalogue
    release, 'Add to stock') that dies part-way through line `line_index` may
    have put lines -- and some of that line's units -- on the shelf. On its
    way out (_put_on_shelf), under its claim, the hold is rewritten to what it
    really holds: the lines before it as this run decided them (`decided`),
    the dead line's units not on the shelf (`left`, when the run knows them
    without a read), each later line's -- and a dead line's `left` does not
    give -- units not yet received by origin, each under the hold it was in.
    The order's received counts are refreshed
    and the tasks re-worded, so the order, the PO timeline, the movements
    ledger and the tasks count what is on the shelf now, not at the last
    accept. A PENDING receipt is left as it is: no reader counts one until
    it is accepted again. Fail-soft: the 503 says 'accept it again' either way."""
    if grn.get("status") != "PARTIALLY_ACCEPTED":
        return
    grn_id = grn.get("grn_id")
    try:
        was = {
            ln.get("product_id"): ln
            for ln in reversed(grn.get("unresolved_lines") or [])
            if isinstance(ln, dict)
        }

        def _as_held(entry: dict) -> dict:
            return {**(was.get(entry["product_id"]) or {"reason": "incomplete_catalog"}), **entry}

        held = list(decided) + ([_as_held(left)] if left else [])
        first = line_index + 1 if left else line_index
        for idx, it in enumerate(grn.get("items") or []):
            pid = it.get("product_id")
            accepted = int(it.get("accepted_qty") or 0)
            if idx < first or accepted <= 0 or not pid:
                continue
            rest = accepted - _receipt_units(
                stock_repo, grn, product_id=pid, grn_line_index=idx
            )
            if rest > 0:
                held.append(_as_held({"product_id": pid, "accepted_qty": accepted, "held_qty": rest}))
        written = _guarded_grn_write(
            grn_repo,
            {"grn_id": grn_id, "accept_lock_token": claim_token},
            {"$set": {"unresolved_lines": held}},
        )
        if written is not True:
            return
        refresh_po_received(po_repo, grn.get("po_id"), grn_id)
        # Holding nothing (it died after its last line), no task waits on it.
        _sync_catalogue_tasks(
            grn_id, grn, held, "PARTIALLY_ACCEPTED" if held else "ACCEPTED", get_product_repository()
        )
    except Exception:  # noqa: BLE001 - never mask the accept's own error
        logger.error("[VENDOR] GRN %s: the hold left by a dead accept was not rewritten", grn_id, exc_info=True)


def refresh_po_received(po_repo, po_id, grn_id=None) -> tuple:
    """Advance a PO's received state from what arrived per product -- the SAME
    count the cancels write (po_detail._received_by_product: the accepted
    receipts, the units in stock, the order's own copy), shared out over
    each product's lines in order -- and compare it against the ordered
    lines: full receipt -> RECEIVED, otherwise PARTIALLY_RECEIVED. Counting
    ACCEPTED receipts only dropped the units a part-accepted or escalated
    receipt put on the shelf, and reopened a line a cancel had closed over
    them. Run by every accept, by 'Not received' and by an accept that died
    part-way. Fail-soft.

    The write is guarded like every other PO change (_as_read): a manager's
    "cancel what is still due" landing between this read and this write must
    not be undone by writing back the lines read before it. On a lost race
    re-read and re-derive -- the receipts are already counted, so the answer
    only depends on the order as it now stands.

    Answers (the new status or None, the (order, count) last read or None):
    all the over-delivery flag needs."""
    po_status = None
    counted = None
    if po_repo is None or not po_id:
        return po_status, counted
    try:
        for _attempt in range(3):
            po = po_repo.find_by_id(po_id)
            if not po or po.get("status") == "CANCELLED":
                break  # a withdrawn order stays withdrawn
            received_by_product = _received_by_product(po)
            counted = (po, received_by_product)
            # Each line's received qty + residual status (drives the
            # receiving cockpit's "open POs" / "pending not-received" panels).
            updated_items = [dict(it) for it in po.get("items") or []]
            _refresh_received(
                updated_items, _received_per_line(po, received_by_product)
            )
            state = compute_po_receipt_state(updated_items, received_by_product)
            if po_repo.update_if(
                po_id,
                _as_read(po),
                {
                    "status": state,
                    "items": updated_items,
                    "received_qty_by_product": received_by_product,
                    "total_received_qty": sum(received_by_product.values()),
                    "last_received_at": datetime.now().isoformat(),
                },
            ):
                po_status = state
                break
        else:
            logger.warning(
                "[VENDOR] GRN %s: PO %s kept changing; receipt state left "
                "for the next receipt or cancel to re-derive",
                grn_id,
                po_id,
            )
    except Exception:  # noqa: BLE001
        # Never lose the stock write on a PO-update failure. Best effort:
        # at least flag the PO as partially received -- never over an order
        # a cancel (or a full receipt) closed meanwhile.
        try:
            if po_repo.update_if(
                po_id,
                {"status": {"$nin": ["RECEIVED", "CANCELLED"]}},
                {"status": "PARTIALLY_RECEIVED"},
            ):
                po_status = "PARTIALLY_RECEIVED"
        except Exception:  # noqa: BLE001
            pass
    return po_status, counted


def _hold_order_open_for_receipt(po_repo, grn) -> None:
    """Compare-and-set on the ORDER before a single unit is minted.

    A cancel looks for a receipt waiting to be accepted, then writes. A receipt
    logged in between slipped past that look and, once accepted, put stock on
    the shelf against units the order says were withdrawn. So the accept reads
    the order, refuses when it was cancelled -- or when a product on this
    receipt is now ordered in fewer units than the receipt was logged against
    (a line cancel, or the rest of a part-received order cancelled) -- and then
    moves the order's write stamp only while the order is still as it read it.
    A cancel that read the order before is refused by its own compare-and-set
    (reload, and the receipt shows); a cancel that landed first refuses this
    accept -- no stock is minted against it.

    Another receipt of the order that an accept has claimed may be minting
    right now, so it counts WHOLE (_received_by_product, in_flight_whole).
    Each accept sets its claim BEFORE it reads here, so of two racing accepts
    at least one sees the other -- both may, and then both are told to try
    again, never both mint. A refusal that only that in-flight receipt causes
    says so: once it finishes, this one either fits or gets the void /
    escalate advice."""
    po_id = grn.get("po_id")
    items = grn.get("items") or []
    if po_repo is None or not po_id or puts_nothing_in_stock(items):
        # Every item rejected: no unit to hold the order for. Even a cancelled
        # order takes it (owner ruling 2026-10-08 (c)) -- it puts nothing in.
        return
    gid = grn.get("grn_id")
    for _attempt in range(3):
        po = _order_or_503(
            po_repo,
            po_id,
            "Could not read the order this delivery is for, so nothing was "
            "added to stock. Try again in a moment.",
        )
        if not po:
            return
        if po.get("status") == "CANCELLED":
            raise HTTPException(
                status_code=409,
                detail=(
                    "This order was cancelled - nothing can be received against "
                    "it any more. Void this receipt."
                ),
            )

        def received(whole):
            return lambda: _received_by_product(
                po, leave_out_grn=gid, in_flight_whole=whole
            )

        over = beyond_open_quantity(po, items, received(True))
        if over:
            if not beyond_open_quantity(po, items, received(False)):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Another delivery for this order is being accepted right "
                        "now - try again in a moment."
                    ),
                )
            raise HTTPException(
                status_code=409,
                detail=(
                    "Part of this order was cancelled, so the receipt no "
                    "longer fits it (" + ", ".join(over) + "). "
                    + _way_out_of(grn)
                ),
            )
        if po_repo.update_if(po_id, _as_read(po), {}):
            return
    raise HTTPException(
        status_code=409,
        detail="This order kept changing while the delivery was being accepted - accept it again.",
    )


def _way_out_of(grn) -> str:
    """What clears a receipt the order no longer has room for. A void, while
    it has put nothing in stock (pending, or held for cataloguing with nothing
    minted) -- void takes both. Once it holds stock, a void would leave those
    units with no receipt behind them, so it can only be escalated; until
    then every cancel on the order waits for it. Its units are counted
    wherever a transfer has moved them (_receipt_units)."""
    stock_repo = get_stock_repository()
    try:
        shelved = stock_repo is not None and _receipt_units(stock_repo, grn)
    except Exception:  # noqa: BLE001 - unknown: never advise a void
        shelved = True
    if not shelved:
        return "Void this receipt and log what arrived again."
    return (
        "It has already put units in stock, so it cannot be voided - escalate "
        "it to head office, which takes it off the order's waiting list."
    )


_CHECK_NEEDED = "Over-delivery check needed on"


def _flag_over_delivery(po_repo, po_id, grn, counted) -> None:
    """Owner ruling 2026-10-08: a delivery that runs over what the order asks
    for is ACCEPTED into stock and FLAGGED, never refused. Every product on
    this receipt now received beyond its ordered quantity is named in the
    receipt's one task -- the order, the product, the extra units -- for the
    shop's purchase manager: the store manager, who raises its orders (there
    is no separate purchase role; owner ruling D17 sends a receipt problem to
    the shop's store manager by name). The receipt's own discrepancy task at
    logging compares only that receipt with the order, so a second delivery
    of a fully received line went unflagged.

    `counted` is the (order, count) the write-back read, whether or not its
    write landed -- the flag needs the count, never the write. When the count
    could not be read there, it is read again here; when it still cannot be,
    a task says the check did not run, so the flag is never lost silently.
    Fail-soft: never undoes the receipt."""
    mine = {
        line.get("product_id")
        for line in grn.get("items") or []
        if _qty(line.get("accepted_qty"))
    }
    if not mine:
        return
    try:
        if counted is None:
            po = _order_or_503(po_repo, po_id)
            if not po or po.get("status") == "CANCELLED":
                return
            counted = (po, _received_by_product(po))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VENDOR] over-delivery count unreadable: %s", exc)
        po_ref = _po_ref(grn.get("po_number"), po_id)
        _raise_over_delivery_task(
            grn,
            f"{_CHECK_NEEDED} {po_ref}",
            f"Goods receipt {grn.get('grn_number')} put units into stock, but "
            f"what {po_ref} has received could not be read to compare it with "
            "the order. Check whether this delivery ran over what was ordered.",
        )
        return
    po, received_by_product = counted
    try:
        ordered: dict = {}
        names: dict = {}
        for it in po.get("items") or []:
            pid = it.get("product_id")
            ordered[pid] = ordered.get(pid, 0) + _qty(it.get("quantity"))
            names.setdefault(pid, it.get("product_name") or it.get("sku") or pid)
        extra = []
        for pid in sorted(mine, key=str):
            got, want = _qty(received_by_product.get(pid)), ordered.get(pid, 0)
            if got > want:
                extra.append(
                    f"{names.get(pid, pid)}: {got - want} more than ordered "
                    f"({got} received, {want} ordered)"
                )
        if not extra:
            return
        po_ref = _po_ref(po.get("po_number"), po.get("po_id"))
        _raise_over_delivery_task(
            grn,
            f"Over-delivery on {po_ref}",
            f"Goods receipt {grn.get('grn_number')} put more into stock than "
            f"{po_ref} ordered - " + "; ".join(extra) + ". The extra units are "
            "in stock. Agree them with the vendor (keep and bill, or return).",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VENDOR] over-delivery flag skipped: %s", exc)


def _po_ref(po_number, po_id) -> str:
    from ...services.purchase_numbering import po_label

    return po_label(po_number, po_id)


def _raise_over_delivery_task(grn, title: str, description: str) -> None:
    """The one task an over-delivery (or a check that could not run) raises,
    for the shop's store manager: ONE per receipt. A receipt accepted again
    (a held line catalogued) can put a second product over; its count is
    cumulative, so the new text names every product over so far and REPLACES
    the open task's -- the flag is never lost behind the first one. A check
    that could not run never replaces a real flag; it is added to it.
    Fail-soft."""
    try:
        from ...dependencies import get_task_repository
        from ...services.task_triggers import active_tasks, create_system_task

        repo = get_task_repository()
        ref = f"grn_over:{grn.get('grn_id')}"
        try:
            open_ = active_tasks(repo, ref) if repo is not None else []
        except Exception:  # noqa: BLE001 - unread: file it; the create dedupes
            open_ = []
        if open_:
            task = open_[0]
            was, said = str(task.get("title") or ""), str(task.get("description") or "")
            if title.startswith(_CHECK_NEEDED) and not was.startswith(_CHECK_NEEDED):
                if description in said:
                    return
                title, description = was, f"{said} {description}"
            if (title, description) != (was, said):
                repo.update(task["task_id"], {"title": title, "description": description})
            return
        create_system_task(
            repo,
            title=title,
            description=description,
            priority="P2",
            category="Purchase",
            store_id=grn.get("store_id"),
            dedupe_ref=ref,
            assigned_to="STORE_MANAGER",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VENDOR] over-delivery flag skipped: %s", exc)


def _accept_grn_claimed(
    grn_id: str,
    grn: dict,
    current_user: dict,
    grn_repo,
    stock_repo,
    po_repo,
    claim_token: Optional[str] = None,
    claimed_at=None,
    order_cap: bool = False,
    deferred_releases: Optional[List[str]] = None,
    progress: Optional[dict] = None,
) -> dict:
    """The accept body, run ONLY by the caller that won the F8 claim.

    Unchanged from the original inline body: idempotent per-(grn, line) stock
    minting, the Hub-Phase-2 ghost-stock hold, cost backfill, the GRN status
    flip and the PO receipt math. Extracted so the claim can be released on any
    failure without re-indenting the whole engine. `progress` is kept current
    as the run goes -- the lines it decided, the line it is on, that line's
    units not yet on the shelf -- for the caller's way out of a run that dies
    (_record_what_a_dead_accept_holds)."""
    if progress is None:
        progress = {}
    store_id = grn.get("store_id")
    po_id = grn.get("po_id")
    grn_number = grn.get("grn_number")
    user_id = current_user.get("user_id")

    # Phase 2 (inventory valuation): build a per-product unit_price map from the
    # PO so each minted serialized unit is stamped with its PROVISIONAL cost
    # (the agreed PO price). This is the receipt-time cost; the purchase invoice
    # later trues it up to the actually-billed price. ADDITIVE + fail-soft: any
    # problem here leaves po_unit_price empty and the units mint exactly as
    # before (no unit_cost), never blocking receiving.
    po_unit_price: dict = {}
    po_for_cost = None
    if po_repo is not None and po_id:
        try:
            po_for_cost = po_repo.find_by_id(po_id)
            for it in (po_for_cost or {}).get("items", []) or []:
                if not isinstance(it, dict):
                    continue
                pid = it.get("product_id")
                if pid is None or pid in po_unit_price:
                    continue
                try:
                    po_unit_price[pid] = round(float(it.get("unit_price") or 0), 2)
                except (TypeError, ValueError):
                    continue
        except Exception:  # noqa: BLE001
            po_unit_price = {}

    # The catalogue release's order cap (_order_cap), read under the claim.
    over_the_order = (
        _order_cap(po_repo, po_id, grn)
        if order_cap and po_id and po_repo is not None
        else None
    )

    minted_stock_ids: List[str] = []
    units_added = 0
    # Hub Phase 2: lines whose product is not yet on the spine are HELD (not
    # minted as ghost stock) -> the GRN stays PARTIALLY_ACCEPTED and the FE shows
    # a "Catalog now" affordance; re-accepting after cataloguing mints them.
    unresolved_lines: List[dict] = []
    product_repo = get_product_repository()
    # F8 round 2: heartbeat state for the whole mint (across every line), so a
    # long accept keeps its claim fresh and stops dead if the claim is stolen.
    # `confirmed_at` starts at the claim (the claim IS proof of ownership) and
    # only advances on a heartbeat that came back with a definite answer.
    #
    # The clock comes from the CALLER, stamped at claim time -- NOT from here.
    # Stamping it here (after the PO fetch above) erased however long that read
    # had parked, so a worker wedged inside it woke with a fresh clock, found
    # the first tick not due (units < 25, elapsed < 10s) and minted with no
    # token check at all. With the claim time, any gap longer than
    # _GRN_ACCEPT_HEARTBEAT_SECONDS makes the FIRST tick due, so a woken worker
    # re-proves ownership before minting a single unit. Zero cost on the happy
    # path: the first unit is milliseconds from the claim, so no extra write.
    _now = claimed_at or datetime.now()
    heartbeat_state = {"units": 0, "at": _now, "errors": 0, "confirmed_at": _now}
    # Probed ONCE per accept, not per unit.
    mint_raises_on_duplicate = _stock_create_raises_on_duplicate(stock_repo)
    # Verify the DB-level backstop exists (cached process-wide, never blocking).
    if stock_repo is not None:
        _grn_unit_index_present(stock_repo)

    if stock_repo is not None:
        for line_index, item in enumerate(grn.get("items", []) or []):
            # Lines before this one are decided (on the shelf, or held in
            # `unresolved_lines`); this one's units on the shelf are not known yet.
            progress.update(decided=unresolved_lines, line_index=line_index, left=None)
            try:
                accepted_qty = int(item.get("accepted_qty", 0) or 0)
            except (TypeError, ValueError):
                accepted_qty = 0
            product_id = item.get("product_id")
            if accepted_qty <= 0 or not product_id:
                continue

            # Idempotency guard keyed on the GRN LINE (grn_line_index), not just
            # the product: a GRN may legitimately carry two lines for the SAME
            # product (e.g. different location_code). A product-only key let line
            # B see line A's units and skip minting -> silent first-accept stock
            # loss. Keying on the line index makes each line mint its own qty and
            # still makes a re-accept idempotent.
            #
            # This read FAILS CLOSED. It used to swallow errors into
            # `already = 0`, which disarmed the two re-run paths this claim
            # machinery depends on (the token-guarded release retry and the
            # stale takeover): one transient error on a retry and an entire
            # 200-unit receipt mints a second time. The unique index on
            # (source_id, grn_line_index, line_unit_seq) is the DB-level
            # backstop, but it only lines up if the ordinals continue this
            # count -- so if we cannot verify what is already received, we STOP.
            #
            # Counted by ORIGIN (_receipt_units): a held receipt whose units
            # were transferred before its re-accept must not mint them again.
            try:
                already = _receipt_units(
                    stock_repo, grn, product_id=product_id, grn_line_index=line_index
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[VENDOR] GRN %s line %s: could not count already-minted "
                    "units (%s) -- aborting rather than risking a double mint",
                    grn_id,
                    line_index,
                    exc,
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Could not verify what has already been received for "
                        "this goods receipt, so nothing further was added. Try "
                        "again in a moment."
                    ),
                ) from exc
            if already >= accepted_qty:
                continue
            to_mint = accepted_qty - already
            # Every hold below records what it really holds back -- the line's
            # units not yet received, by origin (numbering.held_units) -- never
            # the line's accepted_qty: a release that died part-way through a
            # line left some of its units on the shelf.
            held = {"product_id": product_id, "accepted_qty": accepted_qty, "held_qty": to_mint}
            progress["left"] = held

            # Hub Phase 2 ghost-stock gate: only mint against a product that
            # exists on the `products` spine. An uncatalogued line is HELD (no
            # ghost stock) for "Catalog now". Fail-soft: when no product repo is
            # available we cannot verify, so we mint exactly as before.
            prod = product_repo.find_by_id(product_id) if product_repo else None
            if product_repo is not None and prod is None:
                unresolved_lines.append({**held, "reason": "not_catalogued"})
                continue

            location_code = item.get("location_code") or "DEFAULT"
            # Provisional receipt cost from the PO line (Phase 2 valuation).
            # Prefer the GRN line's own unit_price if it carries one, else the PO
            # price. ADDITIVE: only stamped when we have a positive cost, so a
            # priceless receipt mints exactly as before.
            try:
                line_cost = float(item.get("unit_price") or 0) or po_unit_price.get(
                    product_id, 0.0
                )
            except (TypeError, ValueError):
                line_cost = po_unit_price.get(product_id, 0.0)
            cost_fields = {}
            if line_cost and line_cost > 0:
                cost_fields = {
                    "unit_cost": round(line_cost, 2),
                    "cost_price": round(line_cost, 2),
                    "cost_source": "GRN_PO",
                }
                # Hub Phase 2 hero: receiving the goods is where the real cost
                # is confirmed. Same shared promote the PO create path uses --
                # fills only a MISSING cost, then atomically restamps so a DRAFT
                # whose only gap was cost_price becomes ACTIVE right here.
                _promote_cost_from_rate(
                    product_id, prod, line_cost, "GRN_PO", product_repo,
                    deferred_releases=deferred_releases,
                )

            # Hub Phase 2: only mint sellable AVAILABLE stock for a CATALOG-
            # COMPLETE product. A product missing catalogue fields beyond cost
            # remains a non-purchasable DRAFT -- HOLD its line (like an
            # uncatalogued line) instead of minting sellable stock POS could not
            # lawfully price/sell. A missing cost alone never holds the goods:
            # a line nobody priced (a manager's PO for a product never bought
            # from this vendor, owner ruling 2026-10-12) mints without a cost
            # and the vendor's bill brings it (_pm.gaps_before_cost). Fail-soft:
            # only when a product repo is available to verify completeness.
            if product_repo is not None and prod is not None:
                merged_for_status = dict(prod)
                if cost_fields:
                    merged_for_status["cost_price"] = cost_fields["cost_price"]
                if _pm.gaps_before_cost(merged_for_status):
                    unresolved_lines.append({**held, "reason": "incomplete_catalog"})
                    continue

            if over_the_order is not None:
                over = over_the_order(product_id, to_mint)
                if over:
                    unresolved_lines.append({**held, "reason": "over_order", **over})
                    continue

            # P2 (optical batch/expiry): stamp the supplier batch + expiry on
            # each minted unit so contact lenses are dated for FEFO consumption
            # and near-expiry reporting (the stock model + FEFO helpers key on
            # batch_code/expiry_date -- the SAME fields /stock/add persists, so a
            # GRN-received CL unit is indistinguishable from a manually-added
            # one). ADDITIVE + fail-soft: a line with no batch/expiry (frames,
            # undated spectacle lenses) mints exactly as before.
            batch_fields = {}
            _bcode = item.get("batch_code") or item.get("lot_number")
            if _bcode:
                batch_fields["batch_code"] = _bcode
            if item.get("expiry_date"):
                batch_fields["expiry_date"] = item.get("expiry_date")

            # line_unit_seq is the per-line ORDINAL of each physical unit, and it
            # is what the unique index (source_id, grn_line_index, line_unit_seq)
            # constrains. It MUST be derived from `already` -- the count of units
            # this line has already put in stock_units -- and never from a fresh
            # 0 or a random value:
            #   * a retry / stale-takeover mints "the remainder", so its ordinals
            #     have to CONTINUE the line's existing sequence. Restarting at 0
            #     would collide with the rows already on the shelf and the index
            #     would reject every unit -- receiving 1 unit instead of N;
            #   * a random value would be unique per attempt, so two workers
            #     minting the same physical unit would both succeed and the index
            #     would constrain nothing.
            # Deriving it from `already` makes the ordinals of a line exactly
            # 0..accepted_qty-1 no matter how many attempts it took: the winner
            # covers 0..N-1, a takeover that read `already = k` covers k..N-1, the
            # overlap is rejected by the index, and the union is exactly N units.
            for seq in range(already, already + to_mint):
                # Ordinals before `seq` are on the shelf.
                progress["left"] = {**held, "held_qty": accepted_qty - seq}
                # Keep the claim alive while we work, and STOP immediately if it
                # was taken over (raises 409). `to_mint` was computed before this
                # loop started, so without this check a worker that was wedged
                # long enough to be declared stale would resume and mint the same
                # units the takeover holder is already minting.
                _grn_accept_heartbeat_tick(
                    grn_repo, grn_id, claim_token, heartbeat_state
                )
                created = _grn_mint_unit(
                    stock_repo,
                    {
                        "store_id": store_id,
                        "product_id": product_id,
                        "barcode": _grn_barcode(store_id, product_id),
                        "location_code": location_code,
                        "quantity": 1,
                        "status": "AVAILABLE",
                        "is_reserved": False,
                        "barcode_printed": False,
                        "source_type": "GRN",
                        "source_id": grn_id,
                        # The ORIGIN (_receipt_units counts by it): never
                        # rewritten by a transfer, unlike source_type/source_id.
                        "grn_id": grn_id,
                        "grn_line_index": line_index,
                        "line_unit_seq": seq,
                        "grn_number": grn_number,
                        "po_id": po_id,
                        "created_by": user_id,
                        **cost_fields,
                        **batch_fields,
                    },
                    mint_raises_on_duplicate,
                )
                if created is _GRN_MINT_DUPLICATE:
                    # Another attempt already owns this ordinal. Advancing seq
                    # is correct here -- the unit exists, it is simply not ours.
                    continue
                if not created:
                    # A REAL insert failure (BaseRepository.create swallows every
                    # non-duplicate exception into a falsy return, so this is the
                    # only signal there is). STOP: carrying on would mint the
                    # remaining ordinals around this one and leave a hole the
                    # unique index makes permanently unfillable. Aborting here
                    # leaves the line's ordinals a contiguous prefix, so the
                    # re-accept recomputes `already` and mints exactly the
                    # missing units -- it heals.
                    logger.error(
                        "[VENDOR] GRN %s line %s: unit #%s did not insert -- "
                        "stopping so the ordinal sequence stays contiguous and "
                        "a retry can finish the receipt",
                        grn_id,
                        line_index,
                        seq,
                    )
                    # Ordinals 0..seq-1 are on the shelf: the hold says so
                    # (`progress`, on the way out).
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            # Deliberately NO count. `units_added` is THIS
                            # attempt's tally and the client silently retries a
                            # 5xx three times, so after a retry that failed on
                            # its first unit the number reads "0 unit(s)" with
                            # real stock already on the shelf -- which is exactly
                            # the recount-and-hand-add this sentence exists to
                            # prevent.
                            "Some units were received before the stock store "
                            "stopped responding. Open the receiving screen and "
                            "accept it again -- the units already received are "
                            "safe and will not be counted twice."
                        ),
                    )
                if created:
                    units_added += 1
                    stock_id = created.get("stock_id") or created.get("_id")
                    if stock_id:
                        minted_stock_ids.append(str(stock_id))
                        # Fail-soft audit row per unit -- never blocks receiving.
                        _grn_stock_audit(
                            str(stock_id),
                            "AVAILABLE",
                            grn_id,
                            po_id,
                            store_id,
                            user_id,
                        )
                        # E3w: ledger the GRN mint (None -> AVAILABLE) into
                        # item_events. Additive + fail-soft: this runs AFTER the
                        # unit is already in stock_units, performs no CAS / no
                        # projection, and any error is logged + swallowed so it
                        # can never lose the received stock.
                        try:
                            from ...services import item_events as ie

                            _le_db = _get_db()
                            if _le_db is not None:
                                ie.record_post_write_event(
                                    _le_db,
                                    event_type=ie.ItemEventType.MINT,
                                    actor_id=user_id or "",
                                    stock_id=str(stock_id),
                                    from_state=None,
                                    to_state=ie.StockState.AVAILABLE,
                                    store_id=store_id,
                                    product_id=product_id,
                                    source_type="GRN",
                                    source_id=grn_id,
                                    payload={"grn_number": grn_number, "po_id": po_id},
                                )
                        except Exception as _le_exc:  # noqa: BLE001
                            logger.warning(
                                "[VENDOR] GRN mint ledger emit skipped: %s",
                                _le_exc,
                            )

    # Every line is decided.
    if progress:
        progress.update(line_index=len(grn.get("items") or []), left=None)

    # Mark the GRN accepted -- or PARTIALLY_ACCEPTED when one or more lines were
    # HELD because their product is not yet catalogued (Hub Phase 2). A held GRN
    # is re-acceptable after "Catalog now" to mint the now-resolved lines.
    grn_status = "PARTIALLY_ACCEPTED" if unresolved_lines else "ACCEPTED"
    # (a) STATUS -- not token-guarded (a worker whose claim was stolen has still
    #     minted real units, and stranding them behind a PENDING receipt is the
    #     worse outcome), but ADVANCE-ONLY: the filter accepts only the two
    #     pre-terminal statuses, so a stale worker can never demote an ACCEPTED
    #     receipt back to PARTIALLY_ACCEPTED -- which is a CLAIMABLE status and
    #     would invite a third accept.
    status_ok = _advance_grn_terminal_status(grn_repo, grn_id, grn_status)
    # (b) WHO accepted and HOW MUCH, plus the lock release, in ONE TOKEN-GUARDED
    #     write. units_added / accepted_by are what the clerk and the manager
    #     read back to confirm a delivery, so a worker whose claim was STOLEN
    #     must not overwrite the real holder's numbers. Unlike (a), skipping this
    #     strands nothing -- the real holder wrote its own.
    _finalise_grn_accept_metadata(
        grn_repo,
        grn_id,
        claim_token,
        {
            # When the receipt was FIRST accepted: a later release or re-accept
            # of its held lines never restamps it (R1-21); it is last_accepted_at.
            "accepted_at": grn.get("accepted_at") or datetime.now().isoformat(),
            "last_accepted_at": datetime.now().isoformat(),
            "accepted_by": user_id,
            "units_added": units_added,
            "unresolved_lines": unresolved_lines,
        },
    )
    if not status_ok:
        # Be honest rather than showing a green "accepted": the units ARE on the
        # shelf but the receipt did not advance, so the operator must re-accept
        # (idempotent -- the per-line count guard sees the units already minted).
        return {
            "message": (
                f"{units_added} unit(s) were received, but the receipt status "
                "could not be updated. Open the receiving screen and accept it "
                "again -- the units already received will not be counted twice."
            ),
            "grn_id": grn_id,
            "grn_status": grn.get("status"),
            "status_flip_failed": True,
            "units_added": units_added,
            "stock_ids": minted_stock_ids,
            "po_status": None,
            "unresolved_lines": unresolved_lines,
            "needs_cataloguing": bool(unresolved_lines),
            "items_added": len(
                [
                    i
                    for i in (grn.get("items", []) or [])
                    if (i.get("accepted_qty", 0) or 0) > 0
                ]
            ),
        }

    # Advance the PO received state (refresh_po_received). Fail-soft. A lost
    # compare-and-set or a failed count must not drop the over-delivery flag.
    po_status, counted = refresh_po_received(po_repo, po_id, grn_id)
    if po_repo is not None and po_id:
        _flag_over_delivery(po_repo, po_id, grn, counted)

    _sync_catalogue_tasks(grn_id, grn, unresolved_lines, grn_status, product_repo)

    return {
        "message": (
            "GRN accepted, stock added"
            if not unresolved_lines
            else "GRN partially accepted -- some lines need cataloguing"
        ),
        "grn_id": grn_id,
        "grn_status": grn_status,
        "units_added": units_added,
        "stock_ids": minted_stock_ids,
        "po_status": po_status,
        # Hub Phase 2: lines held because their product is not yet on the spine.
        # The FE renders a "Catalog now" affordance; cataloguing + re-accepting
        # mints them.
        "unresolved_lines": unresolved_lines,
        "needs_cataloguing": bool(unresolved_lines),
        "items_added": len(
            [
                i
                for i in (grn.get("items", []) or [])
                if (i.get("accepted_qty", 0) or 0) > 0
            ]
        ),
    }


# ---------------------------------------------------------------------------
# Audit C1: a receipt holding units tells a PERSON (owner 2026-09-03: tasks go
# to people, never a title).
# ---------------------------------------------------------------------------
_TASK_OPEN = ["OPEN", "IN_PROGRESS", "ESCALATED"]


def _people_for(db, store_id, role: str, *, entity_wide: bool) -> tuple:
    """([(user id, task store)], True) for the active holders of `role` who
    reach the receipt's shop -- or, with `entity_wide`, any shop of its legal
    entity (a catalogue manager works for the entity, a store manager for his
    shop) -- else ([(ADMIN -- failing that SUPERADMIN -- id, shop)], False):
    nobody holds the job there, and that must be somebody's problem, not a
    silent drawer.

    The task store is one the person can OPEN -- the receipt's shop when it is
    theirs, else their first store of that entity: the task list and the task
    page are store-scoped (tasks._ensure_task_store_access), so a task stamped
    with a shop outside their reach would be invisible to its own assignee."""
    users = db.get_collection("users")
    stores = db.get_collection("stores")
    scope = {store_id}
    store = stores.find_one({"store_id": store_id}) or {}
    if entity_wide and store.get("entity_id"):
        scope |= {
            s.get("store_id") for s in stores.find({"entity_id": store["entity_id"]})
        }
    flt = {"roles": role, "is_active": True, "store_ids": {"$in": list(scope)}}
    people = []
    for u in users.find(flt):
        mine = [s for s in (u.get("store_ids") or []) if s in scope]
        if u.get("user_id") and mine:
            people.append((u["user_id"], store_id if store_id in mine else mine[0]))
    if people:
        return people, True
    for admin_role in ("ADMIN", "SUPERADMIN"):
        people = [
            (u["user_id"], store_id)
            for u in users.find({"roles": admin_role, "is_active": True})
            if u.get("user_id")
        ]
        if people:
            return people, False
    return [], False


def _shop_label(db, store_id) -> str:
    store = db.get_collection("stores").find_one({"store_id": store_id}) or {}
    return f"{store['store_name']} ({store_id})" if store.get("store_name") else str(store_id)


def _held_item_label(prod: Optional[dict], product_id: str) -> str:
    """Brand model colour size (SKU) -- what the cataloguer searches by."""
    if not prod:
        return f"product {product_id} (not in the catalogue)"
    attrs = prod.get("attributes") or {}
    bits = [
        prod.get("brand") or attrs.get("brand_name"),
        prod.get("model") or attrs.get("model_no") or attrs.get("model_name"),
        attrs.get("colour_code") or prod.get("color"),
        attrs.get("lens_size") or attrs.get("size") or prod.get("size"),
    ]
    label = " ".join(str(b) for b in bits if b)
    return f"{label} (SKU {prod.get('sku')})" if prod.get("sku") else label


def _raise_once(
    db, *, dedupe_ref: str, ever: bool = True, priority: str = "P2",
    refresh_only: bool = False, **task
) -> bool:
    """ONE task per source_ref. `ever` (a receipt's tasks): once a person has
    closed it, a re-press of "Add to stock" or the next catalogue save never
    raises it again. Otherwise (a person ASKING, e.g. a bill's request for
    cataloguing) one only while it is still open -- create_system_task's own
    dedupe -- so asking again after it was closed reaches the person again.
    `refresh_only`: re-word the open task, raise none."""
    from ...dependencies import get_task_repository
    from ...services.task_triggers import create_system_task

    tasks = db.get_collection("tasks")
    # A task still open says what is true NOW (R1-22): the first of two held
    # items finished since it was raised leaves its "Finish 2 item(s)".
    try:
        tasks.update_many(
            {
                "source_ref": dedupe_ref,
                "status": {"$in": _TASK_OPEN},
                "$or": [
                    {"title": {"$ne": task.get("title")}},
                    {"description": {"$ne": task.get("description")}},
                ],
            },
            {
                "$set": {
                    "title": task.get("title"),
                    "description": task.get("description"),
                    # An ask's items follow its words (close_catalogue_asks).
                    **{
                        k: v
                        for k, v in (task.get("extra") or {}).items()
                        if k == "product_ids"
                    },
                    "updated_at": datetime.now(),
                }
            },
        )
    except Exception:  # noqa: BLE001 - stale words never stop the task being raised
        logger.warning("[TASKS] could not refresh %s", dedupe_ref, exc_info=True)
    if refresh_only:
        return False
    if ever and tasks.find_one({"source_ref": dedupe_ref}):
        return True
    repo = get_task_repository()
    create_system_task(repo, priority=priority, dedupe_ref=dedupe_ref, **task)
    # create_system_task answers None both for "already open" and for a write
    # it swallowed: whether the person now HAS the task is read back from the
    # same store it was written to (a failed read reads "not told").
    if repo is None:
        return False
    return any(
        str(t.get("status", "")).upper() in _TASK_OPEN
        for t in repo.find_many({"source_ref": dedupe_ref}) or []
    )


def _complete_receipt_tasks(db, grn_id, note: str, category: Optional[str] = None) -> None:
    """Close the open system tasks raised for this receipt (both kinds, or one
    `category`) -- the receipt no longer needs that person."""
    flt = {"grn_id": grn_id, "source": "SYSTEM"}
    if category:
        flt["category"] = category
    complete_tasks(db, flt, note)


def complete_tasks(db, flt: dict, note: str) -> None:
    """Close the OPEN tasks `flt` matches as done by the system, with `note`."""
    now = datetime.now()
    db.get_collection("tasks").update_many(
        {**flt, "status": {"$in": _TASK_OPEN}},
        {
            "$set": {
                "status": "COMPLETED",
                "completed_at": now,
                "updated_at": now,
                "completed_by": "system",
                "completion_notes": note,
            },
            "$push": {
                "history": {"action": "completed", "by": "system", "notes": note, "at": now}
            },
        },
    )


def close_catalogue_asks(product_id: str, note: str) -> None:
    """Close the asks for cataloguing (a vendor bill's, purchase_invoices.
    request_cataloguing) that name `product_id` once nothing they name waits
    any more: every product on the ask is catalogue-complete, or was
    discarded. One still waiting on others names only those from now on
    (purchase_invoices.ask_catalogue_managers, re-word only): never a
    finished item asked for. Called when a product is finished (PUT
    /products/{id}) or discarded (catalog DELETE). An ask carries no receipt,
    so the receipt's own closing (_complete_receipt_tasks) never reaches it.
    Fail-soft."""
    try:
        db = _get_db()
        if db is None or not product_id:
            return
        reworded = set()
        for t in list(
            db.get_collection("tasks").find(
                {
                    "category": "Catalogue",
                    "source": "SYSTEM",
                    "status": {"$in": _TASK_OPEN},
                    "product_ids": product_id,
                }
            )
        ):
            waiting = asks_still_waiting(db, t.get("product_ids") or [])
            if not waiting:
                complete_tasks(db, {"task_id": t.get("task_id")}, note)
            elif t.get("escalation_group") not in reworded:
                from ..purchase_invoices import _ask_items, ask_catalogue_managers

                reworded.add(t.get("escalation_group"))
                ask_catalogue_managers(
                    db,
                    t.get("ask_store_id") or t.get("store_id"),
                    [i for i in _ask_items(db, waiting) if i["missing"]],
                    reword=t,
                )
    except Exception:  # noqa: BLE001 - a task problem never undoes a save
        logger.warning("[CATALOGUE] could not close the asks naming %s", product_id, exc_info=True)


def asks_still_waiting(db, product_ids) -> List[str]:
    """The products an ask for cataloguing still waits on: those that exist,
    were not discarded, and still miss something the catalogue manager owes
    -- never the cost, which the bill brings (_pm.gaps_before_cost)."""
    products = db.get_collection("products")
    waiting = []
    for pid in product_ids:
        prod = products.find_one({"product_id": pid}) or {}
        if prod and not prod.get("discarded_draft") and _pm.gaps_before_cost(prod):
            waiting.append(pid)
    return waiting


def catalogue_write_done(
    product_id: str, current: dict, patch: dict, *, product_repo, deferred_releases=None
) -> Tuple[dict, int]:
    """THE path after a write to a product, whichever door made it: the
    product editor's save (PUT /products/{id}), the cost an order's or a
    receipt's rate promotes (gst._promote_cost_from_rate), the cost a vendor
    bill writes (purchase_invoices._apply_valuation_trueup). One path, so no
    door finishes a product without the rest:

      * the catalog status is restamped (a DRAFT the write completed turns
        ACTIVE, an ordered draft stops being one -- _pm.apply_restamp_atomic);
      * its catalogue copy mirrors the write (the one mirror rule; a finished
        ordered draft leaves Needs review);
      * the asks for cataloguing that wait on nothing any more close (cost is
        never a gap for them);
      * once the product is catalogue-complete, the units receipts hold for it
        go on the shelf (the one release) -- deferred inside a receipt's
        accept, whose own claim is held.

    Answers (the status fields written, the units released). Fail-soft: the
    write stands."""
    fields = _pm.apply_restamp_atomic(product_id, current, patch, product_repo=product_repo)
    try:
        from api.dependencies import get_db

        _pm.mirror_update_to_catalog_twin(
            product_id=product_id, current=current, patch={**patch, **fields}, db=get_db()
        )
    except Exception:  # noqa: BLE001
        logger.warning("[CATALOGUE] catalog mirror after a write skipped for %s", product_id, exc_info=True)
    close_catalogue_asks(product_id, "The item is catalogued.")
    if _pm.compute_catalog_status({**(current or {}), **patch, **fields})[1]:
        return fields, 0
    if deferred_releases is not None:
        deferred_releases.append(product_id)
        return fields, 0
    return fields, sum(int(r.get("units_added") or 0) for r in release_held_receipts(product_id))


def _sync_catalogue_tasks(grn_id, grn, unresolved_lines, grn_status, product_repo):
    """A receipt holding lines for the catalogue raises ONE task per catalogue
    manager (once per receipt + person, ever -- _raise_once) naming the items,
    the receipt and the shop; lines held beyond the order go to the store
    manager (_hand_to_store_manager). Each person's task closes once nothing on
    the receipt waits for them any more. Fail-soft: a task problem never undoes
    a receipt."""
    try:
        db = _get_db()
        if db is None:
            return
        if not unresolved_lines:
            if grn_status == "ACCEPTED":
                _complete_receipt_tasks(db, grn_id, "The held units are on the shelf.")
            return
        over = [ln for ln in unresolved_lines if ln.get("reason") == "over_order"]
        if over:
            _hand_to_store_manager(db, grn_id, grn, over, product_repo)
        else:
            _complete_receipt_tasks(
                db, grn_id, "Nothing on the receipt is beyond its order now.", category="Purchase"
            )
        unresolved_lines = [ln for ln in unresolved_lines if ln not in over]
        # A line whose product is not on the spine at all (a lens-catalogue id
        # on an auto-PO, a deleted row) has nothing a catalogue manager can
        # finish: no Needs-review row, no product editor, no release on save.
        # It is never put on their list as work they can do; it fails loud.
        unknown = [ln for ln in unresolved_lines if ln.get("reason") == "not_catalogued"]
        if unknown:
            logger.error(
                "[VENDOR] GRN %s holds %d line(s) for products that do not exist "
                "on the catalogue spine (%s) -- no catalogue task can finish them",
                grn_id,
                len(unknown),
                ", ".join(str(ln.get("product_id")) for ln in unknown),
            )
        unresolved_lines = [ln for ln in unresolved_lines if ln not in unknown]
        if not unresolved_lines:
            _complete_receipt_tasks(
                db,
                grn_id,
                "The items are finished; the store manager decides on the rest of this receipt.",
                category="Catalogue",
            )
            return
        store_id = grn.get("store_id")
        shop = _shop_label(db, store_id)
        receipt = grn.get("grn_number") or grn_id

        def _line(ln):
            prod = product_repo.find_by_id(ln["product_id"]) if product_repo else None
            return f"{_held_item_label(prod, ln['product_id'])} x{held_units(ln)}"

        items = "; ".join(_line(ln) for ln in unresolved_lines)
        n = len(unresolved_lines)
        tell_catalogue_managers(
            db,
            store_id,
            dedupe=f"grn_catalogue:{grn_id}",
            title=f"Finish {n} item(s) held on receipt {receipt} at {shop}",
            orphan_title=(
                f"No catalogue manager for {shop}: receipt {receipt} is holding {n} item(s)"
            ),
            description=(
                f"Receipt {receipt} at {shop} is holding these units until each "
                f"product is finished in the catalogue: {items}. Finish them from "
                "Catalogue > Needs review (they are at the top); the units go on "
                "the shelf by themselves when you save."
            ),
            extra={"grn_id": grn_id, "link": "/catalog/review"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[VENDOR] GRN %s catalogue task sync failed: %s", grn_id, exc)


def tell_catalogue_managers(
    db, store_id, *, dedupe: str, title: str, orphan_title: str, description: str,
    extra: Optional[dict] = None, ever: bool = True, refresh_only: bool = False,
) -> Tuple[int, bool]:
    """THE door that tells the catalogue managers an item waits on them -- a
    receipt holding units (_sync_catalogue_tasks), a vendor bill that cannot be
    booked (purchase_invoices.request_cataloguing). One task per active
    CATALOG_MANAGER of the shop's legal entity, BY NAME (owner 2026-09-30),
    once per `dedupe` + person (_raise_once; `ever` False for an ask, which
    reaches them again once the last one is closed), in a store that person
    can open. Nobody holding the job there fails loud: logged, and raised for
    the admins under `orphan_title` -- never a task nobody can see.

    Escalates after a day (owner 2026-09-30), acknowledged or not: P3, whose
    acknowledge clock is a day (task_sla.DEFAULT_SLA), due a day out with no
    overdue grace (overdue_grace_minutes 0). The people's tasks share one
    escalation_group, so they climb to ONE task at the next rung
    (task_escalation.merge_into_twin).

    Answers (how many people now hold the task, whether they are catalogue
    managers) -- False: nobody holds the job, the admins were told."""
    people, are_cataloguers = _people_for(
        db, store_id, "CATALOG_MANAGER", entity_wide=True
    )
    if not are_cataloguers:
        logger.error(
            "[CATALOGUE] %s: NO catalogue manager covers %s -- raising the task "
            "for the admins",
            title,
            store_id,
        )
        title = orphan_title
    told = 0
    for uid, task_store in people or [(None, store_id)]:
        told += _raise_once(
            db,
            dedupe_ref=f"{dedupe}:{uid or 'nobody'}",
            ever=ever,
            refresh_only=refresh_only,
            priority="P3",
            due_at=datetime.now() + timedelta(days=1),
            title=title,
            description=description,
            category="Catalogue",
            store_id=task_store,
            assigned_to=uid,
            # Due a day out, and overdue THEN: pressing Acknowledge stops the
            # P3 ack clock, and the P3 grace would add three more days.
            extra={**(extra or {}), "escalation_group": dedupe, "overdue_grace_minutes": 0},
        )
    return told, are_cataloguers
