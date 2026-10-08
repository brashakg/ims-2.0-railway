"""
Online Store -> Design Queue -> Publish a photo (the simple rebuild of #1152)
============================================================================
The rules a person pressing Publish on a design photo relies on, each pinned
through ``shopify_push.push_image`` over the mocked transport (the package's
``_graphql`` answers like a production listing; no network call is made):

  1. Pressing Publish twice never puts the photo up twice. Before attaching,
     the press reads the listing and skips when IMS's own copy is already
     there: the media id the row recorded, else the IMS file name (the CDN
     copy keeps the source file name -- measured on production 2026-09-06).
     One media, one owner: a media the product's own photos or another
     design row record is never this row's copy, whatever its name.
  2. Replacing a photo swaps it: the new one goes up first, and the old one
     comes down only once a read shows the new one READY on the listing. A
     failed or unfinished upload leaves the old one up. Only a photo IMS
     uploaded ever comes down.
  3. (tests/test_image_editing.py) a re-edit gets a new file name.
  4. When IMS cannot tell -- the listing read fails, a photo is still being
     processed, two copies look alike, the attach got no answer -- it changes
     nothing more and says so in plain words. It never deletes a photo
     another part of IMS recorded.

The production shape the fake keeps: ``image.url`` is null until a media is
READY, and the CDN url carries the source file name plus ``?v=``.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import asyncio  # noqa: E402
import copy  # noqa: E402

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from api.services import shopify_push  # noqa: E402

P_GID = "gid://shopify/Product/111"
OWN = "https://cdn.example.com/p1/front.jpg"  # the product's own photo
A = "https://store.example.com/P1/3f2a9c01.png"  # a design photo
B = "https://store.example.com/P1/7b1d4402.png"  # its replacement
HAND = "https://cdn.shopify.com/s/files/1/0001/files/lifestyle.jpg?v=1690000000"


def _m(n):
    return "gid://shopify/MediaImage/%d" % n


def _cdn(src):
    return "https://cdn.shopify.com/s/files/1/0001/files/%s?v=1700000000" % src.rsplit("/", 1)[-1]


class _EngineDB:
    def __init__(self):
        self._colls = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, MockCollection(name))


class _Listing:
    """One Shopify product's media, answering the three media operations the
    design press uses. ``answer``: ok | lost (the media lands, the answer
    times out) | refused (a 502: nothing lands) | failed (Shopify marks it
    FAILED at once). ``lands``: what a new media is on the next read.
    ``fail_reads``: which listing reads (1-based) raise."""

    def __init__(self, *nodes):
        self.nodes = [dict(n) for n in nodes]
        self.calls = []
        self.answer = "ok"
        self.lands = "READY"
        self.fail_reads = set()
        self._src = {}
        self._next = 100
        self._reads = 0

    def ready(self):
        """Shopify finishes processing every media it can fetch."""
        for n in self.nodes:
            if n["status"] in ("UPLOADED", "PROCESSING"):
                n["status"] = "READY"
                n["image"] = {"url": _cdn(self._src[n["id"]])}

    def ids(self):
        return [n["id"] for n in self.nodes]

    def fail(self, mid):
        """Shopify gives up fetching one media."""
        for n in self.nodes:
            if n["id"] == mid:
                n["status"], n["image"] = "FAILED", None

    def sent(self, op):
        return [v for q, v in self.calls if op in q]

    async def __call__(self, db, query, variables):  # noqa: ARG002
        self.calls.append((query, copy.deepcopy(variables)))
        if "imsProductMedia" in query:
            self._reads += 1
            if self._reads in self.fail_reads:
                raise ValueError("shopify request failed after 4 attempts (timeout)")
            return {"data": {"product": {"id": P_GID, "media": {"nodes": copy.deepcopy(self.nodes)}}}}
        if "productCreateMedia" in query:
            if self.answer == "refused":
                raise ValueError("shopify request failed after 4 attempts (status 502)")
            out = []
            for m in variables["media"]:
                mid = _m(self._next)
                self._next += 1
                self._src[mid] = m["originalSource"]
                status = "FAILED" if self.answer == "failed" else self.lands
                self.nodes.append(
                    {
                        "id": mid,
                        "status": status,
                        "image": {"url": _cdn(m["originalSource"])} if status == "READY" else None,
                    }
                )
                out.append(
                    {
                        "id": mid,
                        "status": "FAILED" if self.answer == "failed" else "UPLOADED",
                        "mediaErrors": [{"code": "MEDIA_UNAVAILABLE", "details": "404"}]
                        if self.answer == "failed"
                        else [],
                    }
                )
            if self.answer == "lost":
                raise ValueError("shopify request failed after 4 attempts (timeout: ReadTimeout)")
            return {"data": {"productCreateMedia": {"media": out, "mediaUserErrors": []}}}
        if "productDeleteMedia" in query:
            gone = list(variables["mediaIds"])
            self.nodes = [n for n in self.nodes if n["id"] not in gone]
            return {"data": {"productDeleteMedia": {"deletedMediaIds": gone, "mediaUserErrors": []}}}
        raise AssertionError("unexpected Shopify call: %s" % query[:80])


def _ready(mid, src):
    return {"id": mid, "status": "READY", "image": {"url": _cdn(src)}}


def _world(monkeypatch, listing, **row):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    monkeypatch.setattr(shopify_push, "_graphql", listing)
    db = _EngineDB()
    db["catalog_products"].insert_one(
        {
            "id": "P1",
            "images": [OWN],
            "ecom": {"shopify_product_id": P_GID, "media_map": [{"url": OWN, "id": _m(1)}]},
        }
    )
    db["product_images"].insert_one(
        {"image_id": "I1", "product_id": "P1", "url": A, "status": "APPROVED", "shopify_image_id": None, **row}
    )
    return db


def _press(db, image_id="I1"):
    return asyncio.run(shopify_push.push_image(db, db["product_images"].find_one({"image_id": image_id})))


def _row(db, image_id="I1"):
    doc = db["product_images"].find_one({"image_id": image_id})
    return doc.get("shopify_image_id"), doc.get("shopify_image_src")


def _mutations(listing):
    return [q for q, _v in listing.calls if "imsProductMedia" not in q]


# ---------------------------------------------------------------------------
# Rule 1: pressing twice never puts the photo up twice
# ---------------------------------------------------------------------------


def test_pressing_publish_twice_puts_the_photo_up_once(monkeypatch):
    listing = _Listing(_ready(_m(1), OWN))
    db = _world(monkeypatch, listing, alt_text="Aviator, gold, front")

    first = _press(db)
    assert (first.ok, first.action, first.shopify_id) == (True, "create", _m(100))
    assert _row(db) == (_m(100), A)
    sent = listing.sent("productCreateMedia")[0]["media"]
    assert sent == [{"originalSource": A, "alt": "Aviator, gold, front", "mediaContentType": "IMAGE"}]

    second = _press(db)
    assert (second.ok, second.action, second.shopify_id) == (True, "noop", _m(100))
    assert len(listing.sent("productCreateMedia")) == 1
    assert listing.ids() == [_m(1), _m(100)]
    assert listing.sent("productDeleteMedia") == []


def test_a_lost_answer_is_found_by_its_file_name_and_never_sent_twice(monkeypatch):
    """The attach landed but its answer never came back, so nothing was
    recorded. The next press finds IMS's copy by its file name."""
    listing = _Listing(_ready(_m(1), OWN))
    db = _world(monkeypatch, listing)
    listing.answer = "lost"
    lost = _press(db)
    assert lost.ok is False
    assert lost.error.startswith("IMS could not tell whether the photo went up")
    assert "Press Publish again" in lost.error and "ReadTimeout" not in lost.error
    assert _row(db) == (None, None)

    listing.answer = "ok"
    again = _press(db)
    assert (again.ok, again.action, again.shopify_id) == (True, "noop", _m(100))
    assert _row(db) == (_m(100), A)
    assert len(listing.sent("productCreateMedia")) == 1


def test_a_photo_still_processing_holds_the_press_and_says_so(monkeypatch):
    """Shopify has not named the lost copy yet (image.url is null until
    READY), so IMS cannot tell whether its photo is there: it sends nothing."""
    listing = _Listing(_ready(_m(1), OWN))
    db = _world(monkeypatch, listing)
    listing.answer, listing.lands = "lost", "PROCESSING"
    _press(db)
    listing.answer = "ok"

    held = _press(db)
    assert held.ok is False
    assert "still processing" in held.error
    assert len(listing.sent("productCreateMedia")) == 1
    assert _row(db) == (None, None)

    listing.ready()
    done = _press(db)
    assert (done.ok, done.action) == (True, "noop")
    assert _row(db) == (_m(100), A)
    assert len(listing.sent("productCreateMedia")) == 1


def test_a_sibling_photo_still_processing_does_not_hold_the_press(monkeypatch):
    """Another design row of the same product was just pressed and is still
    processing. That media is recorded, so it is not a doubt about this one."""
    listing = _Listing(_ready(_m(1), OWN))
    db = _world(monkeypatch, listing)
    db["product_images"].insert_one(
        {"image_id": "I2", "product_id": "P1", "url": B, "status": "APPROVED", "shopify_image_id": None}
    )
    listing.lands = "PROCESSING"
    assert _press(db, "I1").ok is True
    second = _press(db, "I2")
    assert (second.ok, second.shopify_id) == (True, _m(101))
    assert _row(db, "I2") == (_m(101), B)


# ---------------------------------------------------------------------------
# Rule 2: replacing a photo swaps it
# ---------------------------------------------------------------------------


def _pushed(monkeypatch):
    """I1 is on the listing as media 100 (photo A); a person's own upload
    sits beside it. Then the row's photo is replaced by B."""
    listing = _Listing(_ready(_m(1), OWN), _ready(_m(100), A), {"id": _m(50), "status": "READY", "image": {"url": HAND}})
    listing._next = 101
    db = _world(monkeypatch, listing, shopify_image_id=_m(100), shopify_image_src=A)
    db["product_images"].update_one({"image_id": "I1"}, {"$set": {"url": B}})
    return listing, db


def test_replacing_a_photo_swaps_it_new_first_then_old(monkeypatch):
    listing, db = _pushed(monkeypatch)
    res = _press(db)
    assert (res.ok, res.action, res.shopify_id) == (True, "update", _m(101))
    assert listing.ids() == [_m(1), _m(50), _m(101)]
    assert _row(db) == (_m(101), B)
    ops = [("create" if "productCreateMedia" in q else "delete") for q in _mutations(listing)]
    assert ops == ["create", "delete"]
    assert listing.sent("productDeleteMedia")[0]["mediaIds"] == [_m(100)]
    tomb = db[shopify_push.TOMBSTONES_COLLECTION].find_one({"media_gid": _m(100)})
    assert tomb is not None and tomb["url"] == A
    # a second press is a no-op
    assert _press(db).action == "noop"
    assert len(listing.sent("productCreateMedia")) == 1


@pytest.mark.parametrize("answer", ["refused", "failed"])
def test_a_failed_upload_leaves_the_old_photo_up(monkeypatch, answer):
    listing, db = _pushed(monkeypatch)
    listing.answer = answer
    res = _press(db)
    assert res.ok is False
    said = {
        "refused": "IMS could not tell whether the photo went up",
        "failed": "The photo did not go up: Shopify could not fetch the photograph",
    }[answer]
    assert res.error.startswith(said)
    assert res.error.endswith(" The old photo stays up.")
    assert listing.sent("productDeleteMedia") == []
    assert _m(100) in listing.ids()
    assert _row(db) == (_m(100), A)


def test_the_old_photo_stays_until_the_new_one_is_confirmed_on_the_listing(monkeypatch):
    listing, db = _pushed(monkeypatch)
    listing.fail_reads = {2}  # the read after the upload
    first = _press(db)
    assert first.ok is False
    assert first.error == shopify_push.media._NOT_CONFIRMED
    assert listing.sent("productDeleteMedia") == []
    assert _m(100) in listing.ids() and _m(101) in listing.ids()
    assert _row(db) == (_m(100), A)

    second = _press(db)  # B is found by its file name: finish the swap
    assert (second.ok, second.shopify_id) == (True, _m(101))
    assert len(listing.sent("productCreateMedia")) == 1
    assert listing.ids() == [_m(1), _m(50), _m(101)]
    assert _row(db) == (_m(101), B)


def test_a_new_url_under_the_same_file_name_still_swaps(monkeypatch):
    """The row records which url its media was made from, so a different
    picture that happens to share the file name is not taken for it."""
    listing, db = _pushed(monkeypatch)
    same_name = "https://other.example.com/shoot/" + A.rsplit("/", 1)[-1]
    db["product_images"].update_one({"image_id": "I1"}, {"$set": {"url": same_name}})
    res = _press(db)
    assert (res.ok, res.action, res.shopify_id) == (True, "update", _m(101))
    assert listing.sent("productDeleteMedia")[0]["mediaIds"] == [_m(100)]
    assert _row(db) == (_m(101), same_name)


def test_a_recorded_photo_shopify_could_not_fetch_is_put_up_again(monkeypatch):
    """A FAILED media is not a photograph: the press attaches the photo again
    and, once the new copy is on the listing, takes its own failed one down."""
    listing = _Listing(_ready(_m(1), OWN), {"id": _m(100), "status": "FAILED", "image": None})
    listing._next = 101
    db = _world(monkeypatch, listing, shopify_image_id=_m(100), shopify_image_src=A)
    res = _press(db)
    assert (res.ok, res.shopify_id) == (True, _m(101))
    assert listing.ids() == [_m(1), _m(101)]
    assert _row(db) == (_m(101), A)


def test_a_new_photo_still_processing_never_takes_the_old_one_down(monkeypatch):
    """Production: the read right after the attach almost always shows the new
    media PROCESSING (image.url null). The old photo stays until a read shows
    the new one READY, so when Shopify then fails to fetch the new one the
    listing still has the old one."""
    listing, db = _pushed(monkeypatch)
    listing.lands = "PROCESSING"
    first = _press(db)
    assert (first.ok, first.shopify_id) == (False, _m(101))
    assert first.error == shopify_push.media._NOT_CONFIRMED
    assert "the old photo stays up" in first.error and "Press Publish again" in first.error
    assert listing.sent("productDeleteMedia") == []
    assert _row(db) == (_m(100), A)

    held = _press(db)  # still processing: nothing sent, nothing taken down
    assert held.ok is False and "still processing" in held.error
    assert len(listing.sent("productCreateMedia")) == 1

    listing.fail(_m(101))  # Shopify could not fetch the new photo
    assert _m(100) in listing.ids()
    listing.lands = "READY"
    again = _press(db)  # B goes up again; the swap finishes on a READY copy
    assert (again.ok, again.action, again.shopify_id) == (True, "update", _m(102))
    assert listing.sent("productDeleteMedia")[0]["mediaIds"] == [_m(100)]
    assert _row(db) == (_m(102), B)


def test_an_unfinished_swap_finishes_once_the_new_photo_is_ready(monkeypatch):
    """The next press finds the new copy by its file name once READY, takes
    the old one down, and records the new one as IMS's own upload (IMS sent
    that url), so a later replacement takes it down too."""
    listing, db = _pushed(monkeypatch)
    listing.lands = "PROCESSING"
    assert _press(db).ok is False
    listing.ready()
    done = _press(db)
    assert (done.ok, done.action, done.shopify_id) == (True, "update", _m(101))
    assert listing.ids() == [_m(1), _m(50), _m(101)]
    assert _row(db) == (_m(101), B)

    listing.lands = "READY"
    db["product_images"].update_one(
        {"image_id": "I1"}, {"$set": {"url": "https://store.example.com/P1/c0ffee77.png"}}
    )
    later = _press(db)
    assert (later.ok, later.action, later.shopify_id) == (True, "update", _m(102))
    assert listing.ids() == [_m(1), _m(50), _m(102)]


@pytest.mark.parametrize("url", [OWN, "https://store.example.com/P1/front.jpg"])
def test_the_products_own_photo_is_never_taken_for_the_design_photo(monkeypatch, url):
    """The product push records media 1 (OWN). A design row with that very url,
    or a different picture with the same file name, gets its own copy: it is
    never reported as already there, and a catalogue photo change (which may
    delete media 1) can never take the design photo down."""
    listing = _Listing(_ready(_m(1), OWN))
    db = _world(monkeypatch, listing, url=url)
    res = _press(db)
    assert (res.ok, res.action, res.shopify_id) == (True, "create", _m(100))
    assert [v["media"][0]["originalSource"] for v in listing.sent("productCreateMedia")] == [url]
    assert _row(db) == (_m(100), url)

    listing.nodes = [n for n in listing.nodes if n["id"] != _m(1)]  # the catalogue swaps its photo
    assert (_press(db).action, listing.ids()) == ("noop", [_m(100)])


def test_another_design_rows_photo_is_never_taken_for_this_one(monkeypatch):
    """I1 is up as media 100. I2 is a different picture with the same file
    name: it is never reported as already there but gets its own copy, so no
    two rows record one media and replacing I1 later takes I1's photo down."""
    listing = _Listing(_ready(_m(1), OWN))
    db = _world(monkeypatch, listing)
    db["product_images"].insert_one(
        {
            "image_id": "I2",
            "product_id": "P1",
            "url": "https://other.example.com/shoot/" + A.rsplit("/", 1)[-1],
            "status": "APPROVED",
            "shopify_image_id": None,
        }
    )
    assert _press(db, "I1").shopify_id == _m(100)
    second = _press(db, "I2")
    assert (second.ok, second.action, second.shopify_id) == (True, "create", _m(101))
    assert _row(db, "I2")[0] == _m(101)

    db["product_images"].update_one({"image_id": "I1"}, {"$set": {"url": B}})
    swap = _press(db, "I1")
    assert (swap.ok, swap.action, swap.shopify_id) == (True, "update", _m(102))
    assert listing.ids() == [_m(1), _m(101), _m(102)]


@pytest.mark.parametrize(
    "new_url",
    [
        "https://store.example.com/P1/9a8b7c6d.png",  # I2's media carries this name
        "https://dropbox.example.com/reshoot/front.jpg",  # the product's own photo does
    ],
)
def test_a_replacement_named_like_another_records_photo_still_goes_up(monkeypatch, new_url):
    """The new url shares a file name with a media another IMS record owns:
    that media is not the new photo, so the new photo is sent, and only the
    row's own old photo comes down."""
    listing, db = _pushed(monkeypatch)
    sib = "https://other.example.com/x/9a8b7c6d.png"
    listing.nodes.append(_ready(_m(200), sib))
    db["product_images"].insert_one(
        {
            "image_id": "I2",
            "product_id": "P1",
            "url": sib,
            "status": "APPROVED",
            "shopify_image_id": _m(200),
            "shopify_image_src": sib,
        }
    )
    db["product_images"].update_one({"image_id": "I1"}, {"$set": {"url": new_url}})
    res = _press(db)
    assert (res.ok, res.action, res.shopify_id) == (True, "update", _m(101))
    assert [v["media"][0]["originalSource"] for v in listing.sent("productCreateMedia")] == [new_url]
    assert listing.sent("productDeleteMedia")[0]["mediaIds"] == [_m(100)]
    assert listing.ids() == [_m(1), _m(50), _m(200), _m(101)]
    assert _row(db) == (_m(101), new_url)


def test_a_photo_another_part_of_ims_records_is_never_taken_down(monkeypatch):
    """The row's record points at the product's own photo (media 1, which the
    product push records). Replacing the row's photo puts up its own copy and
    never takes media 1 down."""
    listing = _Listing(_ready(_m(1), OWN))
    db = _world(monkeypatch, listing, url=B, shopify_image_id=_m(1), shopify_image_src=OWN)
    res = _press(db)
    assert (res.ok, res.action, res.shopify_id) == (True, "create", _m(100))
    assert listing.sent("productDeleteMedia") == []
    assert listing.ids() == [_m(1), _m(100)]
    assert _row(db) == (_m(100), B)


def test_a_photo_found_by_file_name_alone_is_never_taken_down(monkeypatch):
    """The row's url is a person's own upload on the listing (media 50, put up
    in the Shopify admin). The press finds it there and sends nothing, but a
    file name does not prove IMS uploaded it: replacing the row's photo later
    puts the new one up, keeps media 50 and says so."""
    listing = _Listing(_ready(_m(1), OWN), {"id": _m(50), "status": "READY", "image": {"url": HAND}})
    db = _world(monkeypatch, listing, url=HAND, source="SHOPIFY")
    found = _press(db)
    assert (found.ok, found.action, found.shopify_id) == (True, "noop", _m(50))
    assert listing.sent("productCreateMedia") == []
    assert _row(db) == (_m(50), None)

    db["product_images"].update_one({"image_id": "I1"}, {"$set": {"url": B}})
    res = _press(db)
    assert (res.ok, res.shopify_id) == (False, _m(100))
    assert res.error == shopify_push.media._OLD_KEPT
    assert "The old one stays up" in res.error and "Shopify admin" in res.error
    assert listing.sent("productDeleteMedia") == []
    assert listing.ids() == [_m(1), _m(50), _m(100)]
    assert _row(db) == (_m(100), B)
    assert _press(db).action == "noop"


# ---------------------------------------------------------------------------
# Rule 4: when IMS cannot tell, it changes nothing and says so
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "raise",
        {"errors": [{"message": "Internal error"}]},
        {"data": {"product": None}},
        {"data": {"product": {"id": P_GID, "media": None}}},  # malformed
    ],
)
def test_an_unreadable_listing_changes_nothing_and_says_so(monkeypatch, answer):
    listing = _Listing(_ready(_m(1), OWN))
    db = _world(monkeypatch, listing)

    async def _read(db_, query, variables):
        if "imsProductMedia" in query:
            listing.calls.append((query, variables))
            if answer == "raise":
                raise ValueError("shopify request failed after 4 attempts (timeout)")
            return answer
        return await listing(db_, query, variables)

    monkeypatch.setattr(shopify_push, "_graphql", _read)
    res = _press(db)
    assert res.ok is False
    assert "could not read the photos" in res.error
    assert _mutations(listing) == []
    assert _row(db) == (None, None)


def test_two_copies_ims_cannot_tell_apart_change_nothing(monkeypatch):
    """Two media carry the photo's file name (Shopify adds _<uuid> to the
    second upload of one name) and the row recorded neither."""
    twin = {"id": _m(8), "status": "READY", "image": {"url": _cdn(A).replace(
        ".png", "_0f9e8d7c-1234-4abc-9def-0123456789ab.png")}}
    listing = _Listing(_ready(_m(1), OWN), _ready(_m(7), A), twin)
    db = _world(monkeypatch, listing)
    res = _press(db)
    assert res.ok is False
    assert "more than once" in res.error
    assert _mutations(listing) == []
    assert _row(db) == (None, None)
