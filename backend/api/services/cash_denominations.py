"""
IMS 2.0 - Cash Count Block: the ONE shape for "which notes and coins"
=====================================================================
Every place in IMS that records notes-and-coins detail stores the SAME object,
built here. There is exactly one face-value ladder, one row normaliser, one
greedy suggestion and one arithmetic in the codebase -- a second copy of any of
them is a defect, because two copies are how two screens come to disagree.

THE AUTHORITY RULE (read this before changing anything in this file)
--------------------------------------------------------------------
  * For a MOVEMENT (cash tendered on a sale, change handed back, a refund leg,
    a till payout) the AMOUNT is truth. The count block rides ALONGSIDE the
    amount as an attached record. ``matches_amount: False`` is a FLAG for a
    human. It never corrects, rounds, rewrites or rejects the amount. Nothing
    in this module returns a money figure that a caller is expected to store as
    the payment amount.
  * For a DRAWER COUNT (opening float, closing count) the COUNT is truth --
    there is no other source; the money in the drawer IS what the notes add up
    to. ``build_drawer_block`` sets ``amount_paisa = total_paisa`` so
    ``matches_amount`` is trivially true.

THE THREE STATES (blank is never zero)
--------------------------------------
  NOT_CAPTURED  nobody entered anything. rows [], total 0, and
                ``matches_amount`` is **None**, NOT False. Absence is a state.
                A cashier under pressure who skips the count produces this --
                never a fabricated zero, and never a refused sale.
  SUGGESTED     the machine proposed it and a human accepted it untouched (a
                rounded-amount shortcut, or auto-derived change). Real, but
                lower confidence -- managers see counted and suggested apart.
  COUNTED       a human entered or edited the pieces. ``rows: []`` with state
                COUNTED is legitimate and means "counted, and there was none"
                (e.g. a genuine zero-change breakdown).

Paisa everywhere. Face values are whole rupees (the smallest circulating coin
is Rs 1), so a sub-rupee remainder is simply not representable in notes and
coins: ``suggest`` covers the whole-rupee part and the leftover paise surface
as ``matches_amount: False`` -- an honest flag, not a silent rounding.

No emoji (Windows cp1252).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# The ONE ladder. Indian currency in circulation (RBI): Rs 2000 is withdrawn.
# A Rs 10 and a Rs 20 exist as BOTH a note and a coin, which is exactly why a
# row is keyed on (kind, face) and never on face alone.
# ---------------------------------------------------------------------------
NOTE_FACES: Tuple[int, ...] = (500, 200, 100, 50, 20, 10)
COIN_FACES: Tuple[int, ...] = (20, 10, 5, 2, 1)

KIND_NOTE = "note"
KIND_COIN = "coin"

STATE_COUNTED = "COUNTED"
STATE_SUGGESTED = "SUGGESTED"
STATE_NOT_CAPTURED = "NOT_CAPTURED"

_VALID_STATES = (STATE_COUNTED, STATE_SUGGESTED, STATE_NOT_CAPTURED)


def denomination_ladder() -> List[Dict[str, Any]]:
    """The blank count grid the UI starts from (pieces all zero), highest face
    first: notes Rs 500..10 then coins Rs 20..1."""
    rows: List[Dict[str, Any]] = []
    for face in NOTE_FACES:
        rows.append({"face": face, "kind": KIND_NOTE, "pieces": 0})
    for face in COIN_FACES:
        rows.append({"face": face, "kind": KIND_COIN, "pieces": 0})
    return rows


def face_key(row: Optional[Dict[str, Any]]) -> Tuple[str, int]:
    """The identity of a denomination line: (kind, face). ``kind`` is part of
    the key because Rs 10 and Rs 20 are each both a note and a coin -- a
    per-face drawer ledger keyed on face alone silently merges them."""
    r = row or {}
    kind = str(r.get("kind") or KIND_NOTE).lower()
    if kind not in (KIND_NOTE, KIND_COIN):
        kind = KIND_NOTE
    face = _coerce_face(r.get("face")) or 0
    return (kind, face)


# ---------------------------------------------------------------------------
# Coercion + normalisation (same invariants the two legacy copies had)
# ---------------------------------------------------------------------------


def _coerce_pieces(value: Any) -> int:
    """A piece count: a non-negative integer. Junk or negative -> 0."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _coerce_face(value: Any) -> Optional[int]:
    """A face value: a positive integer number of rupees. Junk -> None."""
    try:
        f = int(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def rupees_to_paisa(rupees: Any) -> int:
    """Rupees (float/int/str) -> integer paisa, rounded to the nearest paisa.
    Junk -> 0. round-then-int so float noise never accumulates."""
    try:
        return int(round(float(rupees or 0) * 100))
    except (TypeError, ValueError):
        return 0


def paisa_to_rupees(paisa: Any) -> float:
    """Integer paisa -> a 2-dp rupee float, for the legacy rupee-facing API."""
    try:
        return round(int(paisa or 0) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def normalize_rows(rows: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Clean a list of ``{face, kind, pieces}`` dicts into count rows.

    Drops non-dicts and bad faces, clamps pieces to a non-negative int, defaults
    an unknown ``kind`` to 'note', and attaches ``line_total_paisa``. Input ORDER
    is preserved so the stored document mirrors what the cashier entered."""
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        face = _coerce_face(r.get("face"))
        if face is None:
            continue
        pieces = _coerce_pieces(r.get("pieces"))
        kind = str(r.get("kind") or KIND_NOTE).lower()
        if kind not in (KIND_NOTE, KIND_COIN):
            kind = KIND_NOTE
        out.append(
            {
                "face": face,
                "kind": kind,
                "pieces": pieces,
                "line_total_paisa": face * 100 * pieces,
            }
        )
    return out


def total_paisa(rows: Optional[Iterable[Dict[str, Any]]]) -> int:
    """Sum of face*100*pieces across count rows, in PAISA. Pure."""
    return sum(r["line_total_paisa"] for r in normalize_rows(rows))


def merge_rows(*row_lists: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Sum several count-row lists into one, adding pieces at each (kind, face).

    Used where a document folds several legs of the same tender into one
    canonical row (returns folds two CASH legs into one) -- the notes must fold
    with them or the folded row would carry only half the count. Output is in
    ladder order, highest face first, with off-ladder faces appended; a face
    that ends at zero pieces is dropped, so merging nothing yields []."""
    merged: Dict[Tuple[str, int], int] = {}
    seen_any = False
    for rows in row_lists:
        for row in normalize_rows(rows):
            seen_any = True
            key = face_key(row)
            merged[key] = merged.get(key, 0) + int(row["pieces"])
    if not seen_any:
        return []
    order: List[Tuple[str, int]] = [(KIND_NOTE, f) for f in NOTE_FACES] + [
        (KIND_COIN, f) for f in COIN_FACES
    ]
    extras = sorted(
        (k for k in merged if k not in order), key=lambda k: (-k[1], k[0])
    )
    out: List[Dict[str, Any]] = []
    for kind, face in order + extras:
        pieces = merged.get((kind, face), 0)
        if pieces <= 0:
            continue
        out.append(
            {
                "face": face,
                "kind": kind,
                "pieces": pieces,
                "line_total_paisa": face * 100 * pieces,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Suggestion (the ONE greedy implementation)
# ---------------------------------------------------------------------------

# Faces to HAND OUT, highest first. Deduped by face value keeping the first
# occurrence, so a Rs 20 is suggested as a NOTE (nobody hands out twenty
# one-rupee coins when a note will do) while the Rs 20 coin still exists in the
# ladder for counting a drawer that holds one.
def _suggest_ladder() -> List[Tuple[int, str]]:
    seen: set = set()
    ladder: List[Tuple[int, str]] = []
    for face in NOTE_FACES:
        if face not in seen:
            seen.add(face)
            ladder.append((face, KIND_NOTE))
    for face in COIN_FACES:
        if face not in seen:
            seen.add(face)
            ladder.append((face, KIND_COIN))
    return sorted(ladder, key=lambda t: -t[0])


def suggest(amount_paisa: Any) -> List[Dict[str, Any]]:
    """Greedy minimal-piece breakdown of ``amount_paisa``, highest face first.

    Used by change-suggestion, the rounded-amount shortcut and the per-face
    expected preview -- ONE implementation, so a suggestion can never differ
    between two screens. A negative/junk amount, or an amount under Rs 1,
    returns []. A sub-rupee remainder is NOT representable in notes and coins
    and is deliberately left uncovered: the caller's block then reports
    ``matches_amount: False``, which is an honest flag rather than a silent
    rounding of somebody's money."""
    try:
        remaining = int(amount_paisa or 0)
    except (TypeError, ValueError):
        return []
    if remaining <= 0:
        return []
    rows: List[Dict[str, Any]] = []
    for face, kind in _suggest_ladder():
        unit = face * 100
        pieces = remaining // unit
        if pieces > 0:
            rows.append(
                {
                    "face": face,
                    "kind": kind,
                    "pieces": int(pieces),
                    "line_total_paisa": int(pieces) * unit,
                }
            )
            remaining -= int(pieces) * unit
    return rows


# ---------------------------------------------------------------------------
# The Cash Count Block
# ---------------------------------------------------------------------------


def _actor_id(actor: Optional[Dict[str, Any]]) -> Optional[str]:
    a = actor or {}
    val = a.get("user_id") or a.get("username") or a.get("id")
    return str(val) if val else None


def _now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.utcnow()).isoformat()


def not_captured_block(amount_paisa: Any = 0) -> Dict[str, Any]:
    """The block for "nobody entered anything".

    ``matches_amount`` is **None**, not False: an un-entered breakdown is not a
    mismatch, it is an ABSENCE. Anything that renders or aggregates these must
    treat None as "not captured" and never as a zero count."""
    return {
        "state": STATE_NOT_CAPTURED,
        "rows": [],
        "total_paisa": 0,
        "amount_paisa": int(amount_paisa or 0),
        "matches_amount": None,
        "captured_by": None,
        "captured_at": None,
    }


def build_block(
    rows: Optional[Iterable[Dict[str, Any]]],
    amount_paisa: Any,
    state: Optional[str] = None,
    actor: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the Cash Count Block that rides alongside a MOVEMENT amount.

    ``amount_paisa`` is THE MONEY, copied verbatim from the authoritative amount
    field -- this function NEVER derives, adjusts or returns a replacement for
    it. ``total_paisa`` is derived from the rows and is never supplied by a
    caller.

    ``rows`` absent/None with no explicit ``state`` -> NOT_CAPTURED (see
    ``not_captured_block``). An explicit ``state`` of COUNTED or SUGGESTED is
    honoured even with empty rows -- "counted, and there was none of it" is a
    real answer and must not be downgraded to "never asked"."""
    norm = normalize_rows(rows)
    requested = str(state).upper() if state else None
    if requested not in _VALID_STATES:
        requested = None

    if requested == STATE_NOT_CAPTURED:
        return not_captured_block(amount_paisa)
    if requested is None and not norm:
        # Nothing entered and nobody asserted a state: absence, not zero.
        return not_captured_block(amount_paisa)

    resolved = requested or STATE_COUNTED
    total = sum(r["line_total_paisa"] for r in norm)
    try:
        amount = int(amount_paisa or 0)
    except (TypeError, ValueError):
        amount = 0
    return {
        "state": resolved,
        "rows": norm,
        "total_paisa": int(total),
        "amount_paisa": amount,
        "matches_amount": bool(total == amount),
        "captured_by": _actor_id(actor),
        "captured_at": _now_iso(now),
    }


def build_drawer_block(
    rows: Optional[Iterable[Dict[str, Any]]],
    state: Optional[str] = None,
    actor: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the block for a DRAWER COUNT (opening float / closing count).

    Here the COUNT is truth: there is no independent amount to reconcile
    against, so ``amount_paisa`` is set to the counted total and
    ``matches_amount`` is trivially True. An absent count is still
    NOT_CAPTURED -- a drawer nobody counted must never read as an empty
    drawer."""
    norm = normalize_rows(rows)
    requested = str(state).upper() if state else None
    if requested not in _VALID_STATES:
        requested = None
    if requested == STATE_NOT_CAPTURED or (requested is None and not norm):
        return not_captured_block(0)
    total = sum(r["line_total_paisa"] for r in norm)
    return build_block(norm, total, state=requested or STATE_COUNTED, actor=actor, now=now)


# ---------------------------------------------------------------------------
# The wire shape: what a screen sends, defined ONCE for every router
# ---------------------------------------------------------------------------


class DenominationRow(BaseModel):
    """One line of a count sheet as a screen sends it. ``line_total_paisa`` is
    NOT accepted from a client -- it is derived server-side, so a caller can
    never smuggle a total past the arithmetic."""

    face: int = Field(..., description="Face value in whole rupees")
    kind: str = Field(default=KIND_NOTE, description="'note' or 'coin'")
    pieces: int = Field(default=0, ge=0, description="How many of them")


class CashCountInput(BaseModel):
    """A count sheet on the wire: the rows, plus which of the three states they
    are. OMITTING this object entirely is how a cashier under pressure skips
    the count -- it becomes NOT_CAPTURED, never a zero and never a refusal."""

    rows: List[DenominationRow] = Field(default_factory=list)
    state: Optional[str] = Field(
        default=None, description="COUNTED | SUGGESTED | NOT_CAPTURED"
    )


def block_from_input(
    payload: Optional[CashCountInput],
    amount_paisa: Any,
    actor: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Turn what a screen sent into the stored Cash Count Block.

    ``None`` (the field was simply not sent) -> NOT_CAPTURED against the
    supplied amount. This is the ONLY conversion routers perform, so no router
    holds its own idea of the shape."""
    if payload is None:
        return not_captured_block(amount_paisa)
    return build_block(
        [r.model_dump() for r in (payload.rows or [])],
        amount_paisa,
        state=payload.state,
        actor=actor,
        now=now,
    )


def cash_leg_identity(
    tendered_paisa: Optional[int],
    change_paisa: Optional[int],
    amount_paisa: Any,
) -> Optional[bool]:
    """Per-leg identity: tendered - change == the CASH leg amount.

    Anchored to the LEG, never to the bill: on a UPI Rs 1,000 + CASH Rs 850
    split, the Rs 1,000 note the customer handed over is measured against the
    Rs 850 cash leg, not the Rs 1,850 total. Returns None when either side was
    not captured -- an unknown is not an imbalance. A False is a FLAG; nothing
    anywhere rejects or adjusts a payment because of it."""
    if tendered_paisa is None or change_paisa is None:
        return None
    try:
        return int(tendered_paisa) - int(change_paisa) == int(amount_paisa or 0)
    except (TypeError, ValueError):
        return None


def cash_leg_record(
    *,
    tendered: Optional[CashCountInput],
    change: Optional[CashCountInput],
    tendered_amount_paisa: Optional[int],
    change_amount_paisa: Optional[int],
    amount_paisa: Any,
    actor: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """The block of keys a CASH payment leg carries ALONGSIDE its amount.

    TWO count blocks, never one. A Rs 1,600 cash sale where the customer hands
    4 x Rs 500 and takes Rs 400 back moves the drawer +4 x Rs 500 and
    -2 x Rs 200; the net rupee figure carries no face information at all, so a
    single "denominations of the Rs 1,600" object could never close a per-face
    ledger.

    Every value here is DERIVED or COPIED. Nothing in the returned dict is the
    payment amount, and no caller is expected to write one of these back onto
    ``amount``. A scalar that was not supplied stays None -- blank, not zero."""
    tendered_p = None if tendered_amount_paisa is None else int(tendered_amount_paisa)
    change_p = None if change_amount_paisa is None else int(change_amount_paisa)
    # When the notes were counted but no scalar came with them, the count IS
    # the scalar -- that is what the cashier physically handled.
    t_block = block_from_input(tendered, tendered_p if tendered_p is not None else 0, actor, now)
    if tendered_p is None and is_captured(t_block):
        tendered_p = int(t_block["total_paisa"])
        t_block["amount_paisa"] = tendered_p
        t_block["matches_amount"] = True
    c_block = block_from_input(change, change_p if change_p is not None else 0, actor, now)
    if change_p is None and is_captured(c_block):
        change_p = int(c_block["total_paisa"])
        c_block["amount_paisa"] = change_p
        c_block["matches_amount"] = True
    return {
        "cash_tendered_count": t_block,
        "cash_change_count": c_block,
        "tendered_amount_paisa": tendered_p,
        "change_amount_paisa": change_p,
        "cash_leg_balanced": cash_leg_identity(tendered_p, change_p, amount_paisa),
    }


def is_captured(block: Optional[Dict[str, Any]]) -> bool:
    """True when a human (or an accepted suggestion) actually put a breakdown
    on the record. A missing block and a NOT_CAPTURED block are both False."""
    if not isinstance(block, dict):
        return False
    return str(block.get("state")) in (STATE_COUNTED, STATE_SUGGESTED)


def is_flagged(block: Optional[Dict[str, Any]]) -> bool:
    """True when a CAPTURED breakdown does not add up to its amount -- the flag
    a human must look at. NOT_CAPTURED is never flagged (it claims nothing)."""
    if not is_captured(block):
        return False
    return (block or {}).get("matches_amount") is False


# ---------------------------------------------------------------------------
# Per-face drawer ledger
# ---------------------------------------------------------------------------


def accumulate(
    ledger: Dict[Tuple[str, int], int],
    block: Optional[Dict[str, Any]],
    sign: int = 1,
) -> Dict[Tuple[str, int], int]:
    """Add (sign=+1) or subtract (sign=-1) a block's pieces into a per-face
    ledger keyed on ``(kind, face)``. A NOT_CAPTURED block contributes NOTHING
    -- it is unknown, not zero, and pretending otherwise would invent a
    per-face expectation nobody ever counted. Mutates and returns ``ledger``."""
    if not is_captured(block):
        return ledger
    for row in (block or {}).get("rows") or []:
        key = face_key(row)
        ledger[key] = ledger.get(key, 0) + sign * _coerce_pieces(row.get("pieces"))
    return ledger


def ledger_rows(
    expected: Dict[Tuple[str, int], int],
    counted: Optional[Dict[Tuple[str, int], int]] = None,
) -> List[Dict[str, Any]]:
    """Render a per-face ledger as display rows in ladder order, highest face
    first, with the expected pieces, the counted pieces and the per-face
    discrepancy (counted - expected) in pieces AND in paisa.

    Faces not on the standard ladder (a legacy Rs 2000 note still in a drawer)
    are appended after it rather than dropped -- a count is never edited to fit
    the grid. The SET of rows is every face that appears on EITHER side."""
    counted = counted or {}
    order: List[Tuple[str, int]] = [
        (KIND_NOTE, f) for f in NOTE_FACES
    ] + [(KIND_COIN, f) for f in COIN_FACES]
    extras = sorted(
        (k for k in set(expected) | set(counted) if k not in order),
        key=lambda k: (-k[1], k[0]),
    )
    out: List[Dict[str, Any]] = []
    for key in order + extras:
        exp = int(expected.get(key, 0))
        cnt = int(counted.get(key, 0))
        if exp == 0 and cnt == 0 and key in extras:
            continue
        kind, face = key
        diff = cnt - exp
        out.append(
            {
                "face": face,
                "kind": kind,
                "expected_pieces": exp,
                "counted_pieces": cnt,
                "difference_pieces": diff,
                "difference_paisa": diff * face * 100,
            }
        )
    return out
