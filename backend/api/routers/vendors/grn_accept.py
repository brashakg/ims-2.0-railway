"""POST /grn/{grn_id}/accept - the three-way match and the stock mint."""

from ._shared import (
    Depends,
    HTTPException,
    List,
    Optional,
    _VENDOR_ROLES,
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
from .gst import _promote_cost_from_rate
from .numbering import (
    _cumulative_received_by_product,
    _grn_barcode,
    _grn_stock_audit,
    compute_po_receipt_state,
)
from .grn_accept_lock import (
    _GRN_MINT_DUPLICATE,
    _advance_grn_terminal_status,
    _claim_grn_for_accept,
    _finalise_grn_accept_metadata,
    _grn_accept_conflict,
    _grn_accept_heartbeat_tick,
    _grn_already_minted,
    _grn_mint_unit,
    _grn_unit_index_present,
    _release_grn_accept_claim,
    _stock_create_raises_on_duplicate,
)


@router.post("/grn/{grn_id}/accept")
async def accept_grn(
    grn_id: str, current_user: dict = Depends(require_roles(*_VENDOR_ROLES))
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
    ``current_user`` their own ``require_roles(*_VENDOR_ROLES)`` gate produced.
    """
    grn_repo = get_grn_repository()
    stock_repo = get_stock_repository()
    po_repo = get_purchase_order_repository()

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

    try:
        return _accept_grn_claimed(
            grn_id,
            grn,
            current_user,
            grn_repo,
            stock_repo,
            po_repo,
            claim_token,
            claimed_at,
        )
    except Exception:
        # Nothing was committed we can attribute to this call, or the accept
        # died half-way: hand the claim back (token-guarded) so the operator can
        # retry immediately instead of waiting out the stale window. The
        # per-(grn, line) mint guard makes that retry idempotent.
        _release_grn_accept_claim(grn_repo, grn_id, claim_token)
        raise


def _accept_grn_claimed(
    grn_id: str,
    grn: dict,
    current_user: dict,
    grn_repo,
    stock_repo,
    po_repo,
    claim_token: Optional[str] = None,
    claimed_at=None,
) -> dict:
    """The accept body, run ONLY by the caller that won the F8 claim.

    Unchanged from the original inline body: idempotent per-(grn, line) stock
    minting, the Hub-Phase-2 ghost-stock hold, cost backfill, the GRN status
    flip and the PO receipt math. Extracted so the claim can be released on any
    failure without re-indenting the whole engine."""
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
            try:
                accepted_qty = int(item.get("accepted_qty", 0) or 0)
            except (TypeError, ValueError):
                accepted_qty = 0
            product_id = item.get("product_id")
            if accepted_qty <= 0 or not product_id:
                continue

            # Hub Phase 2 ghost-stock gate: only mint against a product that
            # exists on the `products` spine. An uncatalogued line is HELD (no
            # ghost stock) for "Catalog now". Fail-soft: when no product repo is
            # available we cannot verify, so we mint exactly as before.
            prod = product_repo.find_by_id(product_id) if product_repo else None
            if product_repo is not None and prod is None:
                unresolved_lines.append(
                    {
                        "product_id": product_id,
                        "accepted_qty": accepted_qty,
                        "reason": "not_catalogued",
                    }
                )
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
            try:
                already = _grn_already_minted(
                    stock_repo,
                    {
                        "source_type": "GRN",
                        "source_id": grn_id,
                        "product_id": product_id,
                        "grn_line_index": line_index,
                    },
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
                    product_id, prod, line_cost, "GRN_PO", product_repo
                )

            # Hub Phase 2: only mint sellable AVAILABLE stock for a CATALOG-
            # COMPLETE product. After the cost backfill above, a product still
            # missing catalogue fields beyond cost remains a non-purchasable
            # DRAFT -- HOLD its line (like an uncatalogued line) instead of
            # minting sellable stock POS could not lawfully price/sell. Fail-soft:
            # only when a product repo is available to verify completeness.
            if product_repo is not None and prod is not None:
                merged_for_status = dict(prod)
                if cost_fields:
                    merged_for_status["cost_price"] = cost_fields["cost_price"]
                if _pm.compute_catalog_status(merged_for_status)[1]:
                    unresolved_lines.append(
                        {
                            "product_id": product_id,
                            "accepted_qty": accepted_qty,
                            "reason": "incomplete_catalog",
                        }
                    )
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
            "accepted_at": datetime.now().isoformat(),
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

    # Advance the PO received state. Sum the accepted qty across EVERY accepted
    # GRN for this PO (this one is now ACCEPTED) and compare against the ordered
    # lines: full receipt -> RECEIVED, otherwise PARTIALLY_RECEIVED. Fail-soft.
    po_status = None
    if po_repo is not None and po_id:
        try:
            po = po_repo.find_by_id(po_id)
            received_by_product = _cumulative_received_by_product(grn_repo, po_id)
            po_items = (po.get("items") if po else []) or []
            po_status = compute_po_receipt_state(po_items, received_by_product)
            # Map the cumulative per-product received qty down onto each PO line
            # + derive the line residual status (drives the receiving cockpit's
            # "open POs" / "pending not-received" panels).
            updated_items = []
            for it in po_items:
                ordered = it.get("ordered_qty", it.get("quantity", 0)) or 0
                recv = received_by_product.get(it.get("product_id"), 0)
                updated_items.append(
                    {
                        **it,
                        "received_qty": recv,
                        "line_status": (
                            "RECEIVED"
                            if ordered and recv >= ordered
                            else ("PARTIAL" if recv > 0 else "OPEN")
                        ),
                    }
                )
            po_repo.update(
                po_id,
                {
                    "status": po_status,
                    "items": updated_items,
                    "received_qty_by_product": received_by_product,
                    "total_received_qty": sum(received_by_product.values()),
                    "last_received_at": datetime.now().isoformat(),
                },
            )
        except Exception:  # noqa: BLE001
            # Never lose the stock write on a PO-update failure. Best effort:
            # at least flag the PO as partially received.
            try:
                po_repo.update(po_id, {"status": "PARTIALLY_RECEIVED"})
                po_status = "PARTIALLY_RECEIVED"
            except Exception:  # noqa: BLE001
                pass

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
