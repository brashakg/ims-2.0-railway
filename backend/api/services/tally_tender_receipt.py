"""
IMS 2.0 - E5 wiring: Tally tender Receipt-voucher builder (pure, NO DB)
=======================================================================
Turns the day's ``order.payments[]`` rows into Tally **Receipt** vouchers whose
ledger legs come from the merged E5 tender-routing engine
(``tender_routing.build_tender_jv_legs`` -- REUSED, never forked):

  * UPI / CARD hit their mapped BANK ledgers (never Cash),
  * GIFT_VOUCHER / LOYALTY / CREDIT hit liability / receivable ledgers,
  * an unknown / blank method parks on the Suspense A/c (never folded into
    Cash),
  * every voucher is gated by ``assert_voucher_balanced`` BEFORE it is emitted
    (paise-exact; an unbalanced voucher raises instead of reaching Tally).

REGRESSION SAFETY (adversarial-chair guidance): this builder emits a SEPARATE
``VCHTYPE="Receipt"`` voucher stream -- the existing Sales day-voucher
(``agents.nexus_providers.tally_build_day_voucher_xml``) is NOT modified. One
Receipt voucher per order (mirroring the one-Sales-voucher-per-order grain) so
the receipt credits the SAME party ledger the Sales voucher debited, clearing
that exact receivable. Orders with no payment rows emit nothing.

This module is PURE: no DB, no I/O. The router resolves the E2-layered tender
map (``tender_reconciliation.get_effective_tender_map``) and passes a resolver
in; the builder only READS the payment rows -- it never writes a stamp and
never edits a capture field.

No emoji (Windows cp1252).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional
from xml.sax.saxutils import escape

from api.services.tender_routing import (
    assert_voucher_balanced,
    build_tender_jv_legs,
)
from api.utils.dates import to_date_str


def _ledger_entry(name: str, xml_amount: float) -> str:
    """One ALLLEDGERENTRIES.LIST block. Tally XML convention (same as the Sales
    day-voucher builder): a NEGATIVE amount is a debit (ISDEEMEDPOSITIVE Yes), a
    positive amount is a credit (ISDEEMEDPOSITIVE No)."""
    deemed = "Yes" if xml_amount < 0 else "No"
    return f"""
    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>{escape(str(name))}</LEDGERNAME>
      <ISDEEMEDPOSITIVE>{deemed}</ISDEEMEDPOSITIVE>
      <AMOUNT>{xml_amount:.2f}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>"""


def tally_build_tender_receipt_xml(
    orders: Iterable[Dict[str, Any]],
    resolve_tender_map: Optional[Callable[[Optional[str]], Dict[str, str]]] = None,
    store_meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the Tally import XML of **Receipt** vouchers for a set of orders.

    One voucher per order that has at least one payment row with a non-zero
    net. Per voucher:

      * one DEBIT leg per canonical tender from ``build_tender_jv_legs`` (the
        E5 engine resolves the ledger; a net-negative tender -- refund-dominant
        -- posts as a CREDIT on the SAME ledger, never a reversal ledger),
      * one CREDIT leg on the party ledger (the same party name the Sales
        voucher debited) for the sum of the tender legs,
      * ``assert_voucher_balanced`` on the signed legs BEFORE the voucher is
        emitted -- an imbalance raises ``ValueError`` (fail loudly; Tally would
        reject the import anyway).

    ``resolve_tender_map`` maps an order's ``store_id`` to its E2-layered
    tender->ledger map (None -> code defaults). ``store_meta`` mirrors the
    Sales builder: bakes store code/name into NARRATION + COSTCENTRECATEGORY.

    READ-ONLY: the order dicts are never mutated (no stamp, no capture edit).
    """
    meta = store_meta or {}
    store_code = str(meta.get("store_code") or meta.get("store_id") or "").strip()
    store_name = str(meta.get("store_name") or "").strip()
    narration_bits = [b for b in (store_code, store_name) if b]
    narration = " - ".join(narration_bits)
    escaped_store_code = escape(store_code) if store_code else ""
    escaped_narration = escape(narration) if narration else ""

    vouchers: List[str] = []
    for o in orders or []:
        payments = o.get("payments") or []
        if not payments:
            continue
        tender_map: Dict[str, str] = {}
        if resolve_tender_map is not None:
            tender_map = resolve_tender_map(o.get("store_id")) or {}

        # REUSE the merged E5 engine (no fork): legs carry the resolved ledger
        # + the paise-exact net per canonical tender.
        legs = build_tender_jv_legs(payments, tender_map)
        if not legs:
            continue

        # The party credit is the sum of the engine legs (NOT an independently
        # rounded figure) so the voucher balances to zero paise by construction
        # -- and assert_voucher_balanced verifies it before any emit.
        received_total = round(sum(float(leg["amount"]) for leg in legs), 2)
        signed = [{"amount": float(leg["amount"])} for leg in legs]
        signed.append({"amount": -received_total})
        assert_voucher_balanced(signed)

        order_id = escape(str(o.get("order_id", "")))
        order_date = to_date_str(o.get("created_at")).replace("-", "")  # yyyymmdd
        party = escape(str(o.get("customer_name") or "Walk-in Customer"))

        narration_block = (
            f"\n    <NARRATION>{escaped_narration}</NARRATION>" if escaped_narration else ""
        )
        cost_centre_block = (
            f"\n    <COSTCENTRECATEGORY>{escaped_store_code}</COSTCENTRECATEGORY>"
            if escaped_store_code
            else ""
        )

        # Party leg first (credit -- clears the receivable the Sales voucher
        # created), then one leg per tender. Tally XML sign convention matches
        # the Sales builder: debit = negative AMOUNT + ISDEEMEDPOSITIVE Yes.
        entries = _ledger_entry(party, received_total)
        for leg in legs:  # deterministic: engine sorts legs by tender
            entries += _ledger_entry(leg["ledger"], -float(leg["amount"]))

        vouchers.append(
            f"""
  <VOUCHER VCHTYPE="Receipt" ACTION="Create">
    <DATE>{order_date}</DATE>
    <VOUCHERTYPENAME>Receipt</VOUCHERTYPENAME>
    <VOUCHERNUMBER>RCPT-{order_id}</VOUCHERNUMBER>
    <PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>{narration_block}{cost_centre_block}{entries}
  </VOUCHER>"""
        )

    body = "".join(vouchers)
    return f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>{body}
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""
