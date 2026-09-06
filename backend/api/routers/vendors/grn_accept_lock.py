"""GRN accept concurrency: claim, heartbeat, guarded writes, unit mint."""

from ._shared import HTTPException, Optional, datetime, logger, timedelta, uuid


# ---------------------------------------------------------------------------
# F8 -- GRN acceptance is CLAIMED atomically before any stock is minted.
# ---------------------------------------------------------------------------
# Accepting a GRN used to be a classic check-then-act: read the doc, test
# status == PENDING, mint serialized stock_units, and only THEN flip the status.
# Two concurrent POSTs (an impatient double-click on "Accept", a retry, two
# terminals) both passed the status test and BOTH ran the minting loop -- the
# per-line `stock_repo.count` idempotency guard could not save it either,
# because both requests read `already = 0` before either had written. Result:
# real received inventory doubled.
#
# The fix is the same guarded single-document claim the rest of this codebase
# uses for "only one caller may proceed" (purchase_invoices._stamp_dcs_matched,
# marketing.credit_referral_reward, lens_stock reserve/commit): ONE
# find_one_and_update whose FILTER carries both the acceptable statuses and the
# lock state. Exactly one racing caller matches; the loser gets 409.
#
# The claim writes a LOCK FIELD and deliberately does NOT touch `status`: the
# status vocabulary is read by the receiving cockpit, the PO math, the DC/bulk
# invoice screens and the frontend, so a transient in-flight status would leak
# into all of them. (The accept_lock_* keys DO ride along in the raw GRN JSON;
# no reader interprets them, but they are not literally invisible.)
_GRN_ACCEPTABLE_STATUSES = ("PENDING", "PARTIALLY_ACCEPTED")

# Statuses that mean "this receipt HAS been accepted". Anything else the flip
# lands on (VOID, ESCALATED, missing) is a failure, not a benign no-op.
_GRN_TERMINAL_ACCEPT_STATUSES = ("ACCEPTED", "PARTIALLY_ACCEPTED")

# A crashed / KILLED worker (SIGKILL, OOM, Railway redeploy -- nothing that any
# except/finally can catch) must not freeze a receipt forever, so a lock older
# than this may be taken over. That wait is pure SHOP-FLOOR TIME: a carton on the
# counter that nobody can receive, with no admin unlock anywhere in the app.
#
# SIZING (round 4). Round 2 set this to 1800s to buy margin against a worker
# wedged inside one pymongo call (prod sets no socketTimeoutMS, so a blackholed
# socket parks a request indefinitely). That margin is no longer bought here:
#   * the heartbeat re-stamps every 25 units / 10s, so a LIVE accept is equally
#     protected at 300s as at 1800s;
#   * the heartbeat now FAILS CLOSED -- a worker that cannot prove it still holds
#     the claim stops minting after 3 consecutive errors or half this window,
#     i.e. it gives up BEFORE the window even permits a takeover;
#   * a takeover's only remaining overlap (one in-flight insert) is rejected by
#     the unique (source_id, grn_line_index, line_unit_seq) index.
# So the window's only job is recovering from a genuinely dead worker, and 300s
# is 30x the heartbeat cadence -- far too wide to steal a live claim, and a
# 5-minute worst case on the shop floor instead of 30.
_GRN_ACCEPT_LOCK_STALE_SECONDS = 300

# Heartbeat cadence for a long accept: re-stamp the lock at least this often, in
# units minted or in wall-clock seconds, whichever comes first. The seconds arm
# is the one that matters after a stall: the very next unit past a multi-minute
# block is overdue, so the abort check fires before another unit is minted.
_GRN_ACCEPT_HEARTBEAT_UNITS = 25
_GRN_ACCEPT_HEARTBEAT_SECONDS = 10

# How many heartbeat writes in a row may fail before the mint gives up. An
# UNVERIFIABLE claim must never be read as "still ours" -- see the tick below.
_GRN_ACCEPT_HEARTBEAT_MAX_ERRORS = 3

# The DB-level backstop this router leans on. Named so its presence can be
# verified at runtime and so a DuplicateKeyError can be attributed to it.
_GRN_UNIT_INDEX_NAME = "uniq_grn_line_unit_seq"

# Distinct outcome for "an atomic primitive EXISTS but the write raised". It is
# NOT the same as "this repo exposes no atomic primitive" (a minimal mock): a
# raising primitive means the state is UNKNOWN and Mongo is unhappy, so callers
# must fail CLOSED. Collapsing the two into None let a replica-set stepdown hand
# a claim token to BOTH racing double-click POSTs -- the exact double-mint F8
# exists to prevent, with no stale window involved.
_GRN_WRITE_ERROR = object()


def _grn_atomic_primitive(grn_repo):
    """The ONE guarded single-document primitive to use, or None when this repo
    exposes none at all.

    Chosen by ATTRIBUTE PRESENCE, never by catching an exception, and exactly
    one is used -- falling through from a raised find_one_and_update to
    update_one would re-test the same filter that our own partially-applied
    write may already have invalidated (and self-409 the only caller).

    The real GRNRepository wraps the pymongo `grns` collection (`.collection`);
    some minimal test/mock repos expose the primitive on themselves instead."""
    for target in (getattr(grn_repo, "collection", None), grn_repo):
        if target is None:
            continue
        for name in ("find_one_and_update", "update_one"):
            fn = getattr(target, name, None)
            if callable(fn):
                return name, fn
    return None


def _guarded_grn_write(grn_repo, flt: dict, patch: dict, pre_image: dict = None):
    """Run ONE guarded single-document write and report the outcome.

    Returns True (won), False (lost -- the filter matched nothing), None (this
    repo exposes NO atomic primitive: a minimal mock, callers may fail open) or
    ``_GRN_WRITE_ERROR`` (a primitive existed but the write raised: UNKNOWN,
    callers must fail closed).

    ``pre_image``, when given, receives the pre-update document under key "doc"
    (find_one_and_update only) so a caller can tell a fresh claim from a
    stale-lock takeover."""
    primitive = _grn_atomic_primitive(grn_repo)
    if primitive is None:
        return None
    name, fn = primitive
    try:
        res = fn(flt, patch)
    except Exception as exc:  # noqa: BLE001
        logger.error("[VENDOR] GRN guarded %s failed (failing CLOSED): %s", name, exc)
        return _GRN_WRITE_ERROR
    if name == "find_one_and_update":
        if pre_image is not None and isinstance(res, dict):
            pre_image["doc"] = res
        return res is not None
    return bool(getattr(res, "modified_count", 0) or getattr(res, "matched_count", 0))


def _claim_grn_for_accept(grn_repo, grn_id: str, user_id) -> Optional[str]:
    """Atomically claim a GRN for acceptance. Returns the claim token, or None
    when another caller holds the claim / the GRN is no longer acceptable.

    Raises 503 when the claim write itself errored: if Mongo cannot answer
    "did I get the lock?", minting stock next is the wrong move. Nothing has
    been minted and no lock was written, so a 503 strands nothing.

    Fail-open ONLY for a repo with no atomic primitive at all (minimal mock):
    receiving must never be blocked by an infrastructure gap -- same convention
    as is_online_store and the marketing.py claim fallback."""
    token = "GACC-" + uuid.uuid4().hex[:16]
    stale_cutoff = (
        datetime.now() - timedelta(seconds=_GRN_ACCEPT_LOCK_STALE_SECONDS)
    ).isoformat()
    flt = {
        "grn_id": grn_id,
        "status": {"$in": list(_GRN_ACCEPTABLE_STATUSES)},
        "$or": [
            {"accept_lock_at": {"$exists": False}},
            {"accept_lock_at": None},
            {"accept_lock_at": {"$lt": stale_cutoff}},
        ],
    }
    patch = {
        "$set": {
            "accept_lock_at": datetime.now().isoformat(),
            "accept_lock_token": token,
            "accept_lock_by": user_id or "",
        }
    }
    pre_image: dict = {}
    won = _guarded_grn_write(grn_repo, flt, patch, pre_image=pre_image)
    if won is _GRN_WRITE_ERROR:
        # The write may have APPLIED server-side and only then lost its reply --
        # in which case OUR token is now on the doc with nobody heartbeating it,
        # and the receipt would be frozen for the whole stale window on a claim
        # nobody is using. Clear it, guarded on our own token: that token is a
        # fresh uuid4, so this can only ever remove a lock THIS call wrote and
        # can never touch another holder's. Only now is it true that a 503
        # strands nothing.
        _release_grn_accept_claim(grn_repo, grn_id, token)
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not reserve this goods receipt -- try again. Nothing was "
                "added to stock."
            ),
        )
    if won is None:
        return token
    if not won:
        return None
    prior = (pre_image.get("doc") or {}).get("accept_lock_at")
    if prior:
        # A takeover should be RARE (only a genuinely dead worker gets this far).
        # Log loudly: it is the one path where two workers could ever overlap.
        logger.error(
            "[VENDOR] GRN %s accept claim TAKEN OVER from a stale lock "
            "(locked at %s by %s) -- previous worker presumed dead",
            grn_id,
            prior,
            (pre_image.get("doc") or {}).get("accept_lock_by"),
        )
    return token


def _guarded_grn_write_retried(grn_repo, flt: dict, patch: dict, *, what, grn_id):
    """_guarded_grn_write, retried once on _GRN_WRITE_ERROR and LOUD if it still
    cannot be written.

    _guarded_grn_write swallows the driver exception into a sentinel, so a
    caller that ignores the return value fails completely silently -- which is
    how a single failed lock release could park a receipt behind the stale
    window with nobody able to see why."""
    res = None
    for attempt in range(2):
        if attempt:
            # A brief pause: back-to-back attempts against a broken socket both
            # fail in milliseconds and the "retry" buys nothing. This is short
            # enough not to matter to a request and long enough to clear a
            # primary stepdown that is already completing.
            import time

            time.sleep(0.25)
        res = _guarded_grn_write(grn_repo, flt, patch)
        if res is not _GRN_WRITE_ERROR:
            return res
    logger.error(
        "[VENDOR] GRN %s: %s could NOT be written (2 attempts). The receipt may "
        "stay locked until the %ss stale window expires.",
        grn_id,
        what,
        _GRN_ACCEPT_LOCK_STALE_SECONDS,
    )
    return res


def _release_grn_accept_claim(grn_repo, grn_id: str, token: Optional[str]) -> None:
    """Best-effort, TOKEN-GUARDED release so a failed accept does not park the
    receipt behind a lock. Guarded on our own token so we can never release a
    lock a stale-takeover handed to somebody else. Never raises."""
    if not token:
        return
    try:
        _guarded_grn_write_retried(
            grn_repo,
            {"grn_id": grn_id, "accept_lock_token": token},
            {
                "$set": {
                    "accept_lock_at": None,
                    "accept_lock_token": None,
                    "accept_lock_by": None,
                }
            },
            what="accept-claim release",
            grn_id=grn_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[VENDOR] GRN accept-claim release skipped for %s: %s", grn_id, exc
        )


def _advance_grn_terminal_status(grn_repo, grn_id: str, grn_status: str) -> bool:
    """Flip the receipt to its terminal status. Returns False when the write
    could not be confirmed (so the caller can be honest about it).

    NOT token-guarded on purpose: a worker whose claim was stolen has still put
    real units on the shelf, and leaving them behind a PENDING receipt is worse
    than a stale flip. But it IS guarded to only ever ADVANCE -- the filter
    accepts only the pre-terminal statuses -- so a stale worker cannot demote an
    already-ACCEPTED receipt back to PARTIALLY_ACCEPTED.

    Matching nothing is NOT automatically success. It only means the doc is no
    longer in a pre-terminal status, which is benign if somebody already
    advanced it -- and a silent catastrophe if the receipt went VOID underneath
    us, because the caller would then answer a green "GRN accepted, stock added"
    over a VOID doc while N real sellable units sit behind it. So on a no-match
    we RE-READ and only report success for a genuinely terminal status."""
    res = _guarded_grn_write(
        grn_repo,
        {"grn_id": grn_id, "status": {"$in": list(_GRN_ACCEPTABLE_STATUSES)}},
        {"$set": {"status": grn_status}},
    )
    if res is None:
        # Minimal mock with no atomic primitive: never lose the flip.
        return bool(grn_repo.update(grn_id, {"status": grn_status}))
    if res is _GRN_WRITE_ERROR:
        logger.error(
            "[VENDOR] GRN %s: terminal status flip to %s could NOT be written. "
            "Units are already minted; the receipt stays in its previous status "
            "and a re-accept is idempotent.",
            grn_id,
            grn_status,
        )
        return False
    if res is False:
        try:
            current = grn_repo.find_by_id(grn_id)
        except Exception:  # noqa: BLE001
            current = None
        status_now = (current or {}).get("status")
        if status_now in _GRN_TERMINAL_ACCEPT_STATUSES:
            return True  # somebody already advanced it; nothing is stranded
        logger.error(
            "[VENDOR] GRN %s: terminal status flip to %s did NOT apply -- the "
            "receipt is now %s. Units are already minted and are NOT attached "
            "to an accepted receipt; this needs manual reconciliation.",
            grn_id,
            grn_status,
            status_now,
        )
        return False
    return True


def _finalise_grn_accept_metadata(
    grn_repo, grn_id: str, token: Optional[str], fields: dict
) -> None:
    """Record who accepted / how much AND release the lock, in ONE token-guarded
    write. A worker whose claim was taken over matches nothing and leaves the
    real holder's numbers -- and the real holder's lock -- untouched."""
    patch = dict(fields)
    patch.update(
        {
            "accept_lock_at": None,
            "accept_lock_token": None,
            "accept_lock_by": None,
        }
    )
    if not token:
        grn_repo.update(grn_id, patch)
        return
    res = _guarded_grn_write_retried(
        grn_repo,
        {"grn_id": grn_id, "accept_lock_token": token},
        {"$set": patch},
        what="accept metadata + lock release",
        grn_id=grn_id,
    )
    if res is None:
        # No atomic primitive (minimal mock): plain write, nothing to guard.
        grn_repo.update(grn_id, patch)
        return
    if res is False:
        logger.error(
            "[VENDOR] GRN %s: this worker's accept claim had been taken over -- "
            "leaving the holder's accepted_by / units_added and its live lock "
            "intact",
            grn_id,
        )


def _grn_accept_heartbeat_tick(grn_repo, grn_id: str, token, state: dict) -> None:
    """Called once per unit ABOUT to be minted: keep this claim fresh, and ABORT
    the moment it is no longer ours.

    Two jobs, both needed to make the stale-lock valve safe:
      * re-stamp `accept_lock_at` so a long-but-live accept is never declared
        stale and stolen while it is still minting;
      * detect (token-guarded, so only OUR claim matches) that a takeover HAS
        happened and stop this worker immediately -- otherwise the wedged worker
        would resume against a `to_mint` it computed before the takeover and
        mint the same units the takeover holder is already minting.

    Raises 409 on a lost claim. Whatever was already minted stays (it is real
    stock and the per-line count guard makes the takeover's own arithmetic see
    it); the takeover holder owns the receipt from here."""
    if not token:
        return
    state["units"] += 1
    now = datetime.now()
    due = (
        state["units"] >= _GRN_ACCEPT_HEARTBEAT_UNITS
        or (now - state["at"]).total_seconds() >= _GRN_ACCEPT_HEARTBEAT_SECONDS
    )
    if not due:
        return
    # NOTE the cadence counters are NOT reset here. They are reset only when the
    # write comes back with a DEFINITE answer, so an errored heartbeat re-arms
    # the fence on the very next unit instead of buying the worker another 25
    # units / 10 seconds of unfenced minting.
    res = _guarded_grn_write(
        grn_repo,
        {"grn_id": grn_id, "accept_lock_token": token},
        {"$set": {"accept_lock_at": now.isoformat()}},
    )
    if res is None:
        # No atomic primitive at all (minimal mock): there is no lock to keep
        # fresh and no takeover to detect. Never block receiving.
        state.update({"units": 0, "at": now, "errors": 0, "confirmed_at": now})
        return
    if res is _GRN_WRITE_ERROR:
        # UNKNOWN -- and unknown must NEVER read as "still ours". That is the
        # exact fail-open collapse the CLAIM path was fixed for, and here it is
        # CORRELATED, not independent: the stepdown that wedges a socket for
        # minutes is the same event that makes this write raise. Reading it as
        # "keep minting" re-opened the full double mint (a panel reproduced 34
        # units for a 20-unit receipt, HTTP 200, receipt looking clean).
        state["errors"] += 1
        unconfirmed_for = (now - state["confirmed_at"]).total_seconds()
        if (
            state["errors"] >= _GRN_ACCEPT_HEARTBEAT_MAX_ERRORS
            or unconfirmed_for >= _GRN_ACCEPT_LOCK_STALE_SECONDS / 2
        ):
            logger.error(
                "[VENDOR] GRN %s: accept claim UNVERIFIABLE for %.0fs / %s "
                "consecutive heartbeat errors -- stopping the mint rather than "
                "minting against a claim we can no longer prove we hold",
                grn_id,
                unconfirmed_for,
                state["errors"],
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Could not confirm this goods receipt is still reserved, so "
                    "nothing further was added. Open the receiving screen and "
                    "accept it again -- the units already received are safe and "
                    "will not be counted twice."
                ),
            )
        logger.warning(
            "[VENDOR] GRN %s accept heartbeat could not be written (%s in a "
            "row, %.0fs since the claim was last confirmed)",
            grn_id,
            state["errors"],
            unconfirmed_for,
        )
        return
    # A definite answer: the cadence and the error run both reset.
    state.update({"units": 0, "at": now, "errors": 0})
    if not res:
        logger.error(
            "[VENDOR] GRN %s accept claim was TAKEN OVER mid-mint -- aborting "
            "this worker's remaining units to avoid a double mint",
            grn_id,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "Accepting this goods receipt took too long and was taken over "
                "by another attempt. Refresh -- the units already received are "
                "safe and will not be counted twice."
            ),
        )
    state["confirmed_at"] = now


def _grn_already_minted(stock_repo, flt: dict) -> int:
    """How many units this GRN LINE has already put into stock_units.

    Counts through the RAW collection when one is available, because
    BaseRepository.count swallows driver errors and returns 0
    (base_repository.py:230-243) -- and a silent 0 here is a licence to mint an
    entire receipt a second time. A failure must reach the caller, which aborts.
    Falls back to repo.count only for minimal mocks that expose no collection.

    This count is ALSO the origin of each unit's `line_unit_seq` ordinal, so it
    is what makes the unique (source_id, grn_line_index, line_unit_seq) index
    line up across retries instead of colliding."""
    coll = getattr(stock_repo, "collection", None)
    counter = getattr(coll, "count_documents", None)
    if callable(counter):
        return int(counter(flt) or 0)
    repo_count = getattr(stock_repo, "count", None)
    if callable(repo_count):
        return int(repo_count(flt) or 0)
    raise RuntimeError("stock repository exposes no way to count minted units")


def _stock_create_raises_on_duplicate(stock_repo) -> bool:
    """Does this stock repository's create() accept raise_on_duplicate?

    BaseRepository does (it re-raises DuplicateKeyError instead of swallowing
    it into a silent None); minimal test/mock repos take only the document.
    Probed once per accept so a duplicate rejection is reported EXPLICITLY
    rather than being indistinguishable from a generic insert failure."""
    try:
        import inspect

        return "raise_on_duplicate" in inspect.signature(stock_repo.create).parameters
    except Exception:  # noqa: BLE001
        return False


# One-shot, process-wide cache for the backstop-index presence check.
# Exposed so tests can reset it.
_GRN_UNIT_INDEX_STATE: dict = {"checked": False, "present": None}


def _grn_unit_index_present(stock_repo):
    """Is the DB-level backstop actually installed? Checked ONCE per process.

    ensure_indexes builds every index fail-soft, so uniq_grn_line_unit_seq can
    be ABSENT in prod with nothing whatsoever saying so -- a safety net whose
    disappearance is invisible. It is a BACKSTOP, not the primary defence (the
    atomic claim, the fail-closed heartbeat and the fail-closed per-line count
    all sit above it), so a missing index must NOT block a real delivery from
    being received -- but it must be LOUD. Returns True / False / None (could
    not determine)."""
    if _GRN_UNIT_INDEX_STATE["checked"]:
        return _GRN_UNIT_INDEX_STATE["present"]
    present = None
    try:
        coll = getattr(stock_repo, "collection", None)
        info_fn = getattr(coll, "index_information", None)
        if callable(info_fn):
            present = _GRN_UNIT_INDEX_NAME in (info_fn() or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[VENDOR] could not verify the %s index: %s", _GRN_UNIT_INDEX_NAME, exc
        )
        present = None
    _GRN_UNIT_INDEX_STATE.update({"checked": True, "present": present})
    if present is False:
        logger.error(
            "[VENDOR] STOCK BACKSTOP MISSING: the unique index %s is NOT "
            "installed on stock_units. GRN receiving continues (the accept "
            "claim and the fail-closed heartbeat still hold), but the "
            "database-level duplicate-unit guard is absent -- investigate the "
            "index build now.",
            _GRN_UNIT_INDEX_NAME,
        )
    return present


def _is_grn_unit_duplicate(exc) -> bool:
    """Did OUR unique index fire, or some other unique index on stock_units?

    Treating ANY DuplicateKeyError as "already minted, skip" would silently DROP
    a real received unit the day another unique index lands on this collection
    (there is already one on `serial`). Attribute the error before skipping."""
    details = getattr(exc, "details", None) or {}
    keypat = details.get("keyPattern")
    if isinstance(keypat, dict) and set(keypat) == {
        "source_id",
        "grn_line_index",
        "line_unit_seq",
    }:
        return True
    for blob in (details.get("errmsg"), keypat, str(exc)):
        if blob and _GRN_UNIT_INDEX_NAME in str(blob):
            return True
    return False


# "This ordinal already exists" -- the index did its job. DISTINCT from a falsy
# return, which now means the insert genuinely did not land.
_GRN_MINT_DUPLICATE = object()


def _grn_mint_unit(stock_repo, doc: dict, raises_on_duplicate: bool):
    """Insert ONE serialized unit for a GRN line.

    Returns the created row, ``_GRN_MINT_DUPLICATE`` when the unique
    (source_id, grn_line_index, line_unit_seq) index rejected it, or a FALSY
    value when the insert genuinely failed.

    That three-way answer is load-bearing. A duplicate is the EXPECTED outcome
    of the last remaining race (an insert already in flight inside a worker that
    got declared stale and taken over) -- skipping it is correct. A real failure
    is the opposite: `seq` must NOT advance past it, because the loop would
    leave a HOLE in the line's ordinals that the unique index then makes
    PERMANENTLY unfillable -- the re-accept computes `already` from the row
    COUNT, lands on an ordinal that already exists, is rejected as a duplicate,
    and the missing physical unit can never be received. Collapsing the two into
    one falsy return is exactly that bug.

    A DuplicateKeyError from ANY OTHER index is re-raised: skipping it would
    silently lose a real received unit. NOTE the real BaseRepository.create
    swallows every non-duplicate exception and returns None (it only re-raises
    DuplicateKeyError under raise_on_duplicate), so in production a generic
    insert failure arrives here as a falsy return, not as an exception."""
    try:
        if raises_on_duplicate:
            return stock_repo.create(doc, raise_on_duplicate=True)
        return stock_repo.create(doc)
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ != "DuplicateKeyError":
            raise
        if not _is_grn_unit_duplicate(exc):
            logger.error(
                "[VENDOR] GRN %s line %s unit #%s hit a DuplicateKeyError from "
                "a DIFFERENT index -- not skipping (that would lose a real "
                "received unit): %s",
                doc.get("source_id"),
                doc.get("grn_line_index"),
                doc.get("line_unit_seq"),
                exc,
            )
            raise
        logger.warning(
            "[VENDOR] GRN %s line %s unit #%s was already minted by another "
            "attempt -- the unique key rejected the duplicate, skipping",
            doc.get("source_id"),
            doc.get("grn_line_index"),
            doc.get("line_unit_seq"),
        )
        return _GRN_MINT_DUPLICATE


def _grn_accept_lock_minutes_left(locked_at) -> Optional[int]:
    """Whole minutes until a held lock goes stale, or None if it cannot be
    computed. Used to make the 409 HONEST: after a hard process kill (SIGKILL /
    OOM / redeploy -- nothing any except/finally can catch) the wait really is
    the stale window, so "wait a moment" would be a lie."""
    if not locked_at:
        return None
    try:
        held_for = (
            datetime.now() - datetime.fromisoformat(str(locked_at))
        ).total_seconds()
    except (TypeError, ValueError):
        return None
    remaining = _GRN_ACCEPT_LOCK_STALE_SECONDS - held_for
    if remaining <= 0:
        return None
    return max(1, int(remaining // 60) + (1 if remaining % 60 else 0))


def _grn_accept_conflict(grn_repo, grn_id: str) -> HTTPException:
    """The 409 the LOSER of the accept claim gets. Re-reads the GRN so the
    message tells the operator which of the two races they lost."""
    current = None
    try:
        current = grn_repo.find_by_id(grn_id)
    except Exception:  # noqa: BLE001
        current = None
    status = (current or {}).get("status")
    if status and status not in _GRN_ACCEPTABLE_STATUSES:
        detail = (
            "This goods receipt was already accepted -- its stock is on the "
            "shelf. Refresh to see the current status."
        )
    else:
        detail = (
            "This goods receipt is already being accepted right now. Do not "
            "submit it twice."
        )
        mins = _grn_accept_lock_minutes_left((current or {}).get("accept_lock_at"))
        if mins:
            detail += (
                f" If the first attempt has died, this clears automatically in "
                f"about {mins} minute(s)."
            )
    return HTTPException(status_code=409, detail=detail)
