"""
IMS 2.0 - who a printed prescription is FOR (audit L2-CLIN-F02)
================================================================

The clinical exam's auto-created Rx stores `patient_id` + `customer_id` but no
name snapshot, and the print doors only read embedded name fields -- so a saved
exam printed as "Patient: Patient". One rule, shared by every Rx print door:

  1. a name snapshot embedded on the Rx wins (counter/import rows carry one);
  2. else the examined MEMBER on the account (customers.patients[] by
     patient_id, via member_billing.find_member -- which also covers the legacy
     patient_id == customer_id rows);
  3. an empty patient_id is a legacy account-holder Rx: the account holder.

NEVER substitutes the account holder when a DEPENDENT was examined: a
patient_id that names nobody on the account resolves to no name ("Not
recorded" on the card) rather than the holder's. The phone is a contact number:
the member's own mobile, else the account's.

ASCII-only source (Windows cp1252 safe).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .member_billing import find_member
from .rx_print_values import first_present_rx_value, is_absent_rx_value


def _norm(v: Any) -> str:
    return "" if is_absent_rx_value(v) else str(v).strip()


def rx_patient_identity(rx: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """PURE: {"name": str|None, "phone": str|None} for the person examined."""
    rx = rx or {}
    pid = _norm(rx.get("patient_id"))
    cid = _norm(rx.get("customer_id"))
    holder = not pid or (cid and pid == cid)

    member = find_member(customer, pid) if (customer and pid) else None
    if member is None and holder and customer:
        member = {"name": customer.get("name"), "mobile": None}

    account_phone = (
        first_present_rx_value(customer.get("mobile"), customer.get("phone"))
        if customer
        else None
    )
    name = first_present_rx_value(
        rx.get("patient_name"),
        rx.get("patientName"),
        (member or {}).get("name"),
        # An embedded account name is only the patient when the holder WAS the
        # patient; for a dependent it is somebody else's name.
        rx.get("customer_name") if holder else None,
    )
    phone = first_present_rx_value(
        rx.get("patient_phone"),
        rx.get("customer_phone"),
        rx.get("phone"),
        (member or {}).get("mobile"),
        account_phone,
    )
    return {"name": name, "phone": phone}


def _unavailable(detail: str = "Prescription is temporarily unavailable"):
    from fastapi import HTTPException

    return HTTPException(status_code=503, detail=detail)


def read_by_id_strict(repo, doc_id: str, detail: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """find_by_id that tells "not there" (None) from "could not read" (503).

    BaseRepository.find_by_id swallows every read error and returns None --
    on native Mongo a timeout or a primary step-down then looks exactly like
    "no such record" (PR-1430 review 2). Reads the raw collection when the
    repository has one; a repository without one answers through its own
    find_by_id, and a raise there is a 503 too."""
    err = _unavailable(detail) if detail else _unavailable()
    if repo is None:
        raise err
    try:
        coll = getattr(repo, "collection", None)
        if coll is not None and callable(getattr(coll, "find_one", None)):
            doc = coll.find_one({repo.id_field: doc_id})
            clean = getattr(repo, "_clean_id", None)
            return clean(doc) if callable(clean) else doc
        return repo.find_by_id(doc_id)
    except Exception as exc:  # noqa: BLE001 - any read failure = try again
        raise err from exc


def read_rx_for_customer_strict(repo, customer_id: str, detail: Optional[str] = None) -> List[Dict[str, Any]]:
    """PrescriptionRepository.find_by_customer that RAISES 503 on a read error.

    The repository's own find_many returns [] on any exception -- a false
    "this family has no prescriptions". Same filter, order and cap."""
    err = _unavailable(detail) if detail else _unavailable()
    if repo is None:
        raise err
    try:
        coll = getattr(repo, "collection", None)
        if coll is not None and callable(getattr(coll, "find", None)):
            cur = coll.find({"customer_id": customer_id}).sort([("prescription_date", -1)]).limit(100)
            clean = getattr(repo, "_clean_id", None)
            return [clean(d) if callable(clean) else d for d in cur]
        return list(repo.find_by_customer(customer_id) or [])
    except Exception as exc:  # noqa: BLE001 - any read failure = try again
        raise err from exc


def load_rx_patient(rx: Dict[str, Any]) -> Dict[str, Any]:
    """rx_patient_identity with the account loaded.

    PR-1430 review: an account that cannot be READ (store missing or the read
    fails, including the native read errors BaseRepository swallows) is 503
    "try again" -- never a 200 card printing "Not recorded" for a dependent
    whose name IS on file. An account that simply does not exist still prints
    with the embedded fields (a data fact, not an outage)."""
    customer = None
    cid = _norm((rx or {}).get("customer_id"))
    if cid:
        from ..dependencies import get_customer_repository

        customer = read_by_id_strict(get_customer_repository(), cid)
    return rx_patient_identity(rx, customer)
