"""Shared legacy-bill allocation and strict finance-summary registration reads."""
from .org_validation import has_valid_gstin, resolve_state_code, shop_gstin


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


class RegistrationAmbiguous(ValueError):
    """A monetary row has no uniquely verified receiving registration."""


class SummaryRegistrations:
    """One consistent master-data snapshot for a finance-summary calculation."""

    def __init__(self, db):
        self.db = db
        self.entities = {e.get("entity_id"): e for e in db.get_collection("entities").find(
            {}, {"entity_id": 1, "gstins": 1}
        )}
        self.stores = {s.get("store_id"): s for s in db.get_collection("stores").find(
            {}, {"store_id": 1, "entity_id": 1, "gstin": 1, "state_code": 1, "state": 1}
        )}
        self.store_entities = {sid: s.get("entity_id") for sid, s in self.stores.items()}
        self.gstins = {
            sid: shop_gstin(self.entities.get(s.get("entity_id")), s)
            for sid, s in self.stores.items()
        }
        self.receipts = {}

    def held(self, entity_id):
        return {
            str(g.get("gstin")).strip().upper()
            for g in (self.entities.get(entity_id) or {}).get("gstins") or []
            if isinstance(g, dict) and has_valid_gstin(g.get("gstin"))
        }

    def shop_key(self, store_id):
        shop = self.stores.get(store_id) or {}
        eid = shop.get("entity_id")
        held = self.held(eid)
        gstin = self.gstins.get(store_id)
        own = str(shop.get("gstin") or "").strip().upper()
        state = resolve_state_code(shop.get("state_code"), shop.get("state"))
        # The shared resolver chooses a state fallback. A summary must refuse
        # ambiguity rather than depend on registration list order.
        candidates = {g for g in held if state and g[:2] == state}
        if not eid or gstin not in held or (own not in held and len(candidates) != 1):
            raise RegistrationAmbiguous()
        return eid, gstin

    def receipt_store(self, receipt_id):
        if receipt_id not in self.receipts:
            receipt = self.db.get_collection("grns").find_one({"grn_id": receipt_id}) or {}
            self.receipts[receipt_id] = receipt.get("store_id")
        return self.receipts[receipt_id]

    def bill_key(self, bill):
        eid = bill.get("recipient_entity_id")
        ids = {bill.get(k) for k in ("store_id", "to_store_id") if bill.get(k)}
        receipt_ids = set(bill.get("linked_dc_ids") or [])
        if bill.get("grn_id"):
            receipt_ids.add(bill["grn_id"])
        # Older finance bills may carry shop ownership without entity_id.
        # Infer it only from verified, unanimous receiving evidence.
        if not eid:
            for rid in receipt_ids:
                sid = self.receipt_store(rid)
                if not sid:
                    raise RegistrationAmbiguous()
                ids.add(sid)
            owners = {self.store_entities.get(sid) for sid in ids}
            if len(owners) != 1 or None in owners:
                raise RegistrationAmbiguous()
            eid = owners.pop()
        held = self.held(eid)
        stamped = str(bill.get("recipient_gstin") or "").strip().upper()
        if stamped:
            # A verified booked registration is authoritative, even if the
            # receiving shop's current registration was edited afterwards.
            if stamped not in held:
                raise RegistrationAmbiguous()
            return eid, stamped
        for rid in receipt_ids:
            sid = self.receipt_store(rid)
            if not sid:
                raise RegistrationAmbiguous()
            ids.add(sid)
        for sid in ids:
            self.shop_key(sid)
        inferred = legacy_bill_gstin(
            bill, eid, held, self.gstins, self.store_entities, self.receipt_store
        )
        if not eid or not inferred:
            raise RegistrationAmbiguous()
        return eid, inferred
