"""Shared legacy-bill allocation: the receiving GSTIN of a bill that names none."""


def legacy_bill_gstin(bill, entity_id, held, gstins, store_entities, receipt_store):
    """Existing return rule: verified receiving evidence, else sole registration.

    None means missing/conflicting evidence. Read failures propagate to the
    caller, which decides whether to flag a return or refuse a summary.
    """
    ids = {bill.get(k) for k in ("store_id", "to_store_id") if bill.get(k)}
    receipt_ids = set(bill.get("linked_dc_ids") or [])
    if bill.get("grn_id"):
        receipt_ids.add(bill["grn_id"])
    for rid in receipt_ids:
        sid = receipt_store(rid)
        if not sid:
            return None
        ids.add(sid)
    candidates = set()
    for sid in ids:
        gstin = gstins.get(sid)
        if store_entities.get(sid) != entity_id or gstin not in held:
            return None
        candidates.add(gstin)
    if not ids and not receipt_ids:
        candidates = held
    return next(iter(candidates)) if len(candidates) == 1 else None
