"""
Adopting a live product's Shopify media into ecom.media_map  (owner 2026-09-06)
=============================================================================
PR #1128's photo pass manages ONLY the media in ``ecom.media_map``; the 42
products that went live before the map existed have none, so their photos
never follow IMS until adopted. scripts/adopt_shopify_media_map.py claims a
media as IMS-owned ONLY on a positive identity match -- a claimed media can
later be DELETED by the pass when IMS drops the photo -- through the ONE rule
shopify_push.media.match_media_to_photos, and writes through the ONE writer
the pass uses (media._writeback_media_map).

***** SAFETY-CRITICAL: shopify_push._graphql is monkeypatched with a fake that
EXPLODES on any mutation; the adoption is a query, never a write to Shopify.
*****

Discriminating power (each test goes red when its rule is removed -- table in
the PR): file name equality (R3) and its edges (CDN-side ``_<uuid>`` only,
extension agreement with NO case folding, no ``_WxH`` / bare-hex / ``.v2``
normalising; a same-named hand upload IS claimed by design and pinned),
originalSource url (R1), NO alt rule, IMS order, 1:1 (ambiguity refused),
repeated url counts once, hand uploads unmanaged and PRINTED, partial = no
write, position / count never match, dry-run = no write, --ids required,
already-mapped skipped (an empty map is not a map), transport failure
reported not fatal.

No emoji (Windows cp1252).
"""

import asyncio
import copy
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

import adopt_shopify_media_map as script  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_push.media import match_media_to_photos  # noqa: E402
from test_online_push_dirty_flag import _DB  # noqa: E402

GID = "gid://shopify/Product/900"
APP = "https://ims-20-railway-production.up.railway.app/api/v1/products/image/"
CDN = "https://cdn.shopify.com/s/files/1/0526/3546/7963/files/"
OID1, OID2, OID3 = (
    "6a5634313371b6aa07bbd9d3",
    "6a5634357edfd8bd742b5e98",
    "6a5634397edfd8bd742b5e9b",
)
U1, U2, U3 = APP + OID1, APP + OID2, APP + OID3


def _m(n):
    return "gid://shopify/MediaImage/%d" % n


def _node(n, file_name, alt="", source=""):
    return {
        "id": _m(n),
        "alt": alt,
        "image": {"url": CDN + file_name + "?v=1788643984"},
        "originalSource": {"url": source or "https://shopify-shop-assets.storage.googleapis.com/x/" + file_name},
    }


class _Shopify:
    """Answers imsProductMedia with the injected nodes; anything else -- and
    ANY mutation -- explodes."""

    def __init__(self, nodes):
        self.nodes = list(nodes)
        self.calls = []

    async def __call__(self, db, query, variables):
        if "mutation" in query:
            raise AssertionError("adoption must never mutate Shopify: %s" % query[:60])
        assert "imsProductMedia" in query, query[:60]
        self.calls.append(copy.deepcopy(variables))
        return {"data": {"product": {"id": variables["id"], "media": {"nodes": self.nodes}}}}


def _seed(db, photos, media_map=None):
    ecom = {"shopify_product_id": GID, "locally_modified": False}
    if media_map is not None:
        ecom["media_map"] = media_map
    db["catalog_products"].insert_one({"id": "P1", "sku": "SKU1", "images": list(photos), "ecom": ecom})


def _run(db, apply, ids=("P1",)):
    return asyncio.run(script.run(db, list(ids), apply=apply))


def _twin(db):
    return copy.deepcopy(db["catalog_products"].find_one({"id": "P1"}))


@pytest.fixture
def db():
    return _DB()


def _wire(monkeypatch, nodes):
    fake = _Shopify(nodes)
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    return fake


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_exact_match_adopts_in_ims_order(db, monkeypatch):
    """R3: the in-app uploader's ObjectId names survive the round trip
    (<oid> on IMS, <oid>.png on the CDN). Shopify holds them in a different
    order; the map is written in IMS photo order, through the one writer,
    with an audit row, without touching locally_modified."""
    shop = _wire(monkeypatch, [_node(3, OID3 + ".png"), _node(1, OID1 + ".png"), _node(2, OID2 + ".jpg")])
    _seed(db, [U1, U2, U3])
    out = _run(db, apply=True)
    row = out["rows"][0]
    assert row["status"] == "adopt"
    assert row["map"] == [{"url": U1, "id": _m(1)}, {"url": U2, "id": _m(2)}, {"url": U3, "id": _m(3)}]
    assert row["unmanaged"] == [] and row["unmatched"] == []
    assert out["written"] == ["P1"]
    twin = _twin(db)
    assert twin["ecom"]["media_map"] == row["map"]
    assert twin["ecom"]["locally_modified"] is False
    assert twin["images"] == [U1, U2, U3]
    audit = list(db["audit_logs"].find({}))
    assert len(audit) == 1 and audit[0]["action"] == "MEDIA_MAP_ADOPT"
    assert audit[0]["entity_id"] == "P1" and audit[0]["after_state"]["media_map"] == row["map"]
    assert "$unset" in audit[0]["reversal"]
    assert shop.calls == [{"id": GID}]


def test_original_source_url_matches(db, monkeypatch):
    """R1: the media's originalSource IS the IMS url -- a positive match even
    when the CDN file name says nothing."""
    photo = "https://photos.example.com/rb/front.jpg"
    _wire(monkeypatch, [_node(7, "renamed-by-shopify_a1b2c3d4e5f6a7b8c9d0.png", source=photo)])
    _seed(db, [photo])
    row = _run(db, apply=False)["rows"][0]
    assert row["status"] == "adopt" and row["map"] == [{"url": photo, "id": _m(7)}]


def test_alt_never_matches(db, monkeypatch):
    """NO alt rule (verifier 2026-09-06): IMS attaches every photo with alt ''
    (build_media_inputs), so an alt equal to the IMS url or file name is a
    HUMAN'S edit on a media IMS did not attach; claiming it would let the
    pass delete a hand upload later. Both spellings stay unmanaged."""
    p_url = "https://photos.example.com/rb/front.jpg"
    p_name = "https://photos.example.com/rb/side.jpg"
    _wire(monkeypatch, [_node(1, "hero-by-hand.png", alt=p_url), _node(2, "other-by-hand.png", alt="side.jpg")])
    _seed(db, [p_url, p_name])
    row = _run(db, apply=True)["rows"][0]
    assert row["status"] == "unmatched" and row["map"] == []
    assert row["unmanaged"] == [_m(1), _m(2)]
    assert "media_map" not in _twin(db)["ecom"]


def test_file_name_rule_edges():
    """R3 is the FILE NAME, not a stem: the ?v= query and Shopify's own
    ``_<uuid>`` collision suffix on the CDN side are not identity; an IMS
    name with no extension (the uploader's bare ObjectId) matches whichever
    image extension Shopify gave the copy; an IMS name WITH an extension
    must find the same one (jpeg vs png is a different file); and a size
    suffix is a human's file name, not Shopify's (image.url never carries
    one) -- so it is not stripped."""
    ok = match_media_to_photos(
        ["https://a/i/rb-front.png", U1, "https://a/i/photo.v2"],
        [
            {"id": _m(1), "image": {"url": CDN + "rb-front_89ffbc2c-6001-45c1-804a-2e4d838a4627.png?v=1"}},
            {"id": _m(2), "image": {"url": CDN + OID1 + ".webp?v=2"}},
            {"id": _m(3), "image": {"url": CDN + "photo.v2.png"}},
        ],
    )
    assert ok["map"] == [
        {"url": "https://a/i/rb-front.png", "id": _m(1)},
        {"url": U1, "id": _m(2)},
        {"url": "https://a/i/photo.v2", "id": _m(3)},
    ]
    no = match_media_to_photos(
        ["https://a/i/rb-front.jpeg", "https://a/i/rb-side.jpg"],
        [
            {"id": _m(1), "image": {"url": CDN + "rb-front.png?v=1"}},
            {"id": _m(2), "image": {"url": CDN + "rb-side_600x600@2x.jpg"}},
        ],
    )
    assert no["map"] == [] and no["unmanaged"] == [_m(1), _m(2)]


def test_hand_upload_stays_unmanaged(db, monkeypatch, capsys):
    """A media no photo claims is never in the map: it stays exactly where it
    is, and the product still adopts (every IMS photo matched). The report
    PRINTS the CDN file name behind every pair and every unmanaged media --
    the evidence an operator reads before --apply."""
    _wire(monkeypatch, [_node(9, "hero-shot-by-hand.png", alt="Ray-Ban front view"), _node(1, OID1 + ".png")])
    _seed(db, [U1])
    out = _run(db, apply=True)
    row = out["rows"][0]
    assert row["status"] == "adopt"
    assert row["map"] == [{"url": U1, "id": _m(1)}]
    assert row["unmanaged"] == [_m(9)]
    printed = capsys.readouterr().out
    assert "+ %s -> %s (%s.png)" % (U1, _m(1), OID1) in printed
    assert "- unmanaged %s (hero-shot-by-hand.png)" % _m(9) in printed
    assert _twin(db)["ecom"]["media_map"] == [{"url": U1, "id": _m(1)}]
    assert _m(9) not in str(_twin(db)["ecom"]["media_map"])


def test_partial_match_writes_nothing(db, monkeypatch):
    """One IMS photo matched, one did not: reported as partial, no write, no
    audit row -- half a map would let the pass attach a duplicate."""
    _wire(monkeypatch, [_node(1, OID1 + ".png"), _node(9, "something-else.png")])
    _seed(db, [U1, U2])
    out = _run(db, apply=True)
    row = out["rows"][0]
    assert row["status"] == "partial"
    assert row["map"] == [{"url": U1, "id": _m(1)}] and row["unmatched"] == [U2]
    assert out["written"] == []
    assert "media_map" not in _twin(db)["ecom"]
    assert list(db["audit_logs"].find({})) == []


def test_position_and_count_never_match(db, monkeypatch):
    """Same count, same order, nothing in common by name: NOT a match. (The
    six IMS-pushed products would also pass a position rule -- this is the
    test that keeps one from ever being added.)"""
    _wire(monkeypatch, [_node(1, "8840328872185__00__0rw4006__601_71.png"), _node(2, "8840328872185__01__0rw4006__601_71.png")])
    _seed(db, [U1, U2])
    out = _run(db, apply=True)
    row = out["rows"][0]
    assert row["status"] == "unmatched"
    assert row["map"] == [] and row["unmatched"] == [U1, U2] and row["unmanaged"] == [_m(1), _m(2)]
    assert out["written"] == [] and "media_map" not in _twin(db)["ecom"]


def test_ambiguity_is_not_a_claim():
    """Two media with the same stem: the photo fits both, so neither is
    claimed (1:1 only). And two photos that fit one media claim nothing."""
    two_media = match_media_to_photos(
        ["https://a/i/front.jpg"],
        [
            {"id": _m(1), "image": {"url": CDN + "front.jpg"}},
            {"id": _m(2), "image": {"url": CDN + "front_89ffbc2c-6001-45c1-804a-2e4d838a4627.jpg"}},
        ],
    )
    assert two_media["map"] == [] and two_media["unmatched_photos"] == ["https://a/i/front.jpg"]
    assert two_media["unmanaged"] == [_m(1), _m(2)]
    two_photos = match_media_to_photos(
        ["https://a/i/front.png", "https://b/i/front.png"],
        [{"id": _m(1), "image": {"url": CDN + "front.png"}}],
    )
    assert two_photos["map"] == [] and len(two_photos["unmatched_photos"]) == 2


# ---------------------------------------------------------------------------
# The script's doors
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(db, monkeypatch):
    _wire(monkeypatch, [_node(1, OID1 + ".png")])
    _seed(db, [U1])
    out = _run(db, apply=False)
    assert out["rows"][0]["status"] == "adopt" and out["rows"][0]["map"] == [{"url": U1, "id": _m(1)}]
    assert out["written"] == []
    assert "media_map" not in _twin(db)["ecom"]
    assert list(db["audit_logs"].find({})) == []


def test_ids_required_and_all_refused():
    with pytest.raises(SystemExit):
        script.parse_args([])
    with pytest.raises(SystemExit):
        script.parse_ids("all")
    with pytest.raises(SystemExit):
        script.parse_ids("P1, ALL")
    with pytest.raises(SystemExit):
        script.parse_ids("")
    assert script.parse_ids(" P1 ,P2 ") == ["P1", "P2"]
    assert script.parse_args(["--ids", "P1", "--apply"]).apply is True
    assert script.parse_args(["--ids", "P1"]).apply is False


def test_already_mapped_is_skipped_without_a_shopify_call(db, monkeypatch):
    """The attach wrote this map; adoption never overwrites it."""
    shop = _wire(monkeypatch, [_node(1, OID1 + ".png")])
    existing = [{"url": U1, "id": _m(55)}]
    _seed(db, [U1], media_map=existing)
    out = _run(db, apply=True)
    assert out["rows"][0]["status"] == "already_mapped" and out["written"] == []
    assert _twin(db)["ecom"]["media_map"] == existing
    assert shop.calls == []


def test_missing_twin_and_no_photos_are_reported_not_written(db, monkeypatch):
    shop = _wire(monkeypatch, [_node(1, OID1 + ".png")])
    _seed(db, [])
    out = _run(db, apply=True, ids=("P1", "NOPE"))
    assert [r["status"] for r in out["rows"]] == ["no_photos", "missing"]
    assert out["written"] == [] and shop.calls == []


def test_a_different_shopify_upload_with_the_same_base_name_is_not_the_photo():
    """VERIFIER (2026-09-06): Shopify appends ``_<uuid>`` to a file whose base
    name collides with one already in Files -- Shopify's OWN marker that this
    is a DIFFERENT file. When the IMS photo is itself a Shopify CDN url (the
    bvi_import twins: 15 of the 57 IMS photos on the 42 carry one) with one
    uuid and the media carries ANOTHER, they are two uploads both named
    '1.jpg', not one photograph. _file_stem strips the uuid on BOTH sides, so
    R3 claims it -- and the pass may later DELETE a media IMS never attached."""
    mine = CDN + "1_89ffbc2c-6001-45c1-804a-2e4d838a4627.jpg?v=1749636658"
    other = {"id": _m(1), "image": {"url": CDN + "1_75f933af-3342-42b3-801f-f1f73ff7c0d8.jpg?v=1749636658"}}
    same = {"id": _m(2), "image": {"url": CDN + "1_89ffbc2c-6001-45c1-804a-2e4d838a4627.jpg?v=1788000000"}}
    # positive control: the SAME file (same uuid, newer ?v=) IS the photo
    assert match_media_to_photos([mine], [same])["map"] == [{"url": mine, "id": _m(2)}]
    out = match_media_to_photos([mine], [other])
    assert out["map"] == [], "a different upload that collided on the base name was claimed"
    assert out["unmatched_photos"] == [mine] and out["unmanaged"] == [_m(1)]


def test_names_that_differ_are_not_claimed_same_name_is():
    """VERIFIER probes: a rule that normalises too much claims a human's file.
    Same word, different extension; a size suffix in a human's name; a hex
    tail on either side that is not Shopify's uuid; a '.v2' that is not an
    extension (the name is 'photo.v2'); case and percent-encoding are not
    folded either. Same-extension pairs on purpose: the extension rule must
    not be what saves them. The name says what the test proves: only pairs
    that DIFFER are refused -- a human's upload with the SAME name IS
    claimed (pinned below): the rule cannot tell it from IMS's own upload,
    and the operator's only defence is the printed pair in the dry-run
    (the script docstring says so)."""
    for ims, cdn in [
        ("1.jpg", "1.png"),
        ("front.png", "front_600x600.png"),
        ("front_0123456789ABCDEF.png", "front.png"),
        ("front.png", "front_0123456789abcdef.png"),
        ("photo.v2", "photo.png"),
        ("Front.JPG", "front.jpg"),
        ("Front.jpg", "front.jpg"),
        ("rb%20front.jpg", "rb_front.jpg"),
    ]:
        out = match_media_to_photos(["https://a/i/" + ims], [{"id": _m(1), "image": {"url": CDN + cdn}}])
        assert out["map"] == [] and out["unmanaged"] == [_m(1)], (ims, cdn)
    # KNOWN LIMIT, by design: a same-named hand upload is claimed (plain, and
    # via the CDN-side uuid strip when it collided with another front.jpg).
    for ims, cdn in [("1.jpg", "1.jpg"), ("front.jpg", "front_89ffbc2c-6001-45c1-804a-2e4d838a4627.jpg")]:
        out = match_media_to_photos(["https://a/i/" + ims], [{"id": _m(1), "image": {"url": CDN + cdn}}])
        assert out["map"] == [{"url": "https://a/i/" + ims, "id": _m(1)}], (ims, cdn)


def test_repeated_photo_url_counts_once():
    """The pure contract: a url listed twice is one photo -> one map row."""
    out = match_media_to_photos([U1, U1], [{"id": _m(1), "image": {"url": CDN + OID1 + ".png"}}])
    assert out["map"] == [{"url": U1, "id": _m(1)}] and out["unmatched_photos"] == []


def test_empty_or_malformed_map_is_not_already_mapped(db, monkeypatch):
    """ecom.media_map == [] (the pass pruned it) or rows without url/id are
    NO map: owned_media drops them, so the twin is adopted, not skipped."""
    shop = _wire(monkeypatch, [_node(1, OID1 + ".png")])
    _seed(db, [U1], media_map=[{"id": "no-url"}, "junk"])
    out = _run(db, apply=True)
    assert out["rows"][0]["status"] == "adopt" and out["written"] == ["P1"]
    assert _twin(db)["ecom"]["media_map"] == [{"url": U1, "id": _m(1)}]
    assert shop.calls == [{"id": GID}]
    # the audit row records what was REALLY there before, not an empty list
    audit = list(db["audit_logs"].find({}))
    assert audit[0]["before_state"] == {"media_map": [{"id": "no-url"}, "junk"]}


def test_transport_failure_is_reported_not_fatal(db, monkeypatch):
    """_graphql gave up (retries spent / a 4xx): the product is reported as
    graphql_error with the shop url blanked, nothing is written, and the
    NEXT id in the same run still gets its report."""

    async def boom(db, query, variables):
        raise ValueError("status 401: https://better-vision.myshopify.com/admin said no")

    monkeypatch.setattr(shopify_push, "_graphql", boom)
    _seed(db, [U1])
    db["catalog_products"].insert_one({"id": "P2", "sku": "SKU2", "images": [], "ecom": {"shopify_product_id": GID}})
    out = _run(db, apply=True, ids=("P1", "P2"))
    assert [r["status"] for r in out["rows"]] == ["graphql_error", "no_photos"]
    assert out["rows"][0]["error"].startswith("ValueError: status 401: <url>")
    assert "myshopify" not in out["rows"][0]["error"]
    assert out["written"] == [] and "media_map" not in _twin(db)["ecom"]


def test_extension_case_is_not_folded():
    """VERIFIER 2 (2026-09-06): the rule is EXACT equality with no case
    folding. _stem_ext lowers the extension (and _IMAGE_EXT is re.I), so IMS
    'front.JPG' claims a media named 'front.jpg' -- a different file by name --
    and the reverse; the existing probe ('Front.JPG' vs 'front.jpg') is saved
    by the STEM, so the fold went untested. Stems are exact; the extension
    must be too."""
    for ims, cdn in [("front.JPG", "front.jpg"), ("front.jpg", "front.JPG"), ("front.Jpeg", "front.jpeg")]:
        out = match_media_to_photos(["https://a/i/" + ims], [{"id": _m(1), "image": {"url": CDN + cdn}}])
        assert out["map"] == [] and out["unmanaged"] == [_m(1)], (ims, cdn)
    # positive control: same case IS the file
    assert match_media_to_photos(["https://a/i/front.JPG"], [{"id": _m(1), "image": {"url": CDN + "front.JPG"}}])["map"] == [
        {"url": "https://a/i/front.JPG", "id": _m(1)}
    ]
