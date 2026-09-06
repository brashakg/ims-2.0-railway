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

ROUND 2 (owner rulings 2026-09-06), both OPT-IN: R3b (--rule connector-prefix)
claims a media named '<the product's OWN Shopify id>__<nn>__<basename>' when
<basename> equals the IMS file stem-for-stem, extension ignored -- a foreign
id / no prefix never match, stems are exact, still 1:1, and the default rules
exclude it; --replace-photos-from-shopify writes the ONE Shopify image as the
photo list through the spine door (product_master.update_product, the mirror,
mark_dirty=False so nothing queues) then adopts it, refusing media count != 1,
an existing map and a twin the door cannot clear; dry-run writes nothing.

No emoji (Windows cp1252).
"""

import asyncio
import copy
import json
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
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_push.media import match_media_to_photos  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
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


def _run(db, apply, ids=("P1",), **kw):
    return asyncio.run(script.run(db, list(ids), apply=apply, **kw))


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


# ---------------------------------------------------------------------------
# ROUND 2 -- R3b, the connector-prefix rule (opt-in)
# ---------------------------------------------------------------------------

OWN_ID = "10153760784633"
OWN = "gid://shopify/Product/" + OWN_ID
RB = "https://india.ray-ban.com/media/catalog/product/cache/9c388a/4/_/4_1.jpeg"
R3B = ("exact", "connector_prefix")


def _conn(pid, nn, base):
    return "%s__%02d__%s" % (pid, nn, base)


def _cdn(n, name):
    return {"id": _m(n), "image": {"url": CDN + name}}


def test_connector_prefix_claims_only_the_own_id_media():
    """R3b: '<own id>__00__4_1.png' IS the IMS photo 4_1.jpeg (stem equal,
    extension ignored); the other connector media on the product carry other
    basenames and stay unmanaged. The index is not identity (__04__ on the
    prod product 0d0f04f9 is the match, not __00__)."""
    media = [
        _cdn(1, _conn(OWN_ID, 1, "5.png")),
        _cdn(2, _conn(OWN_ID, 4, "4_1.png") + "?v=1785323542"),
        _cdn(3, _conn(OWN_ID, 2, "2.png")),
    ]
    out = match_media_to_photos([RB], media, rules=R3B, product_gid=OWN)
    assert out["map"] == [{"url": RB, "id": _m(2)}] and out["unmatched_photos"] == []
    assert out["unmanaged"] == [_m(1), _m(3)]
    # a bare numeric id names the product just as well as its gid
    assert match_media_to_photos([RB], media, rules=R3B, product_gid=OWN_ID)["map"] == out["map"]


def test_connector_prefix_never_matches_a_foreign_id_or_no_prefix():
    """The prefix MUST be this product's own id: another product's id, a
    truncated / extended own id, no prefix at all (R3b silent; exact refuses
    the jpeg->png change), an empty prefix and a single-underscore spelling
    all leave the media unmanaged."""
    for cdn in [
        _conn("8840328872185", 0, "4_1.png"),
        _conn(OWN_ID[:-1], 0, "4_1.png"),
        _conn(OWN_ID + "1", 0, "4_1.png"),
        "4_1.png",
        "__00__4_1.png",
        "%s_00_4_1.png" % OWN_ID,
    ]:
        out = match_media_to_photos([RB], [_cdn(1, cdn)], rules=R3B, product_gid=OWN)
        assert out["map"] == [] and out["unmanaged"] == [_m(1)], cdn


def test_connector_prefix_stem_is_exact_extension_is_not():
    """Stem-for-stem, nothing folded: a size suffix, a longer stem, case,
    Shopify's uuid or a non-image tail is a different name. Positive
    controls: jpeg->png, an extension-less IMS name, the same extension, a
    case-only extension change."""
    for ims, base in [
        ("4_1.jpeg", "4_1_600x600.png"),
        ("4_1.jpeg", "4_10.png"),
        ("Front.jpeg", "front.png"),
        ("front.jpeg", "front_89ffbc2c-6001-45c1-804a-2e4d838a4627.png"),
        ("4_1.jpeg", "4_1.png.bak"),
    ]:
        out = match_media_to_photos(["https://a/i/" + ims], [_cdn(1, _conn(OWN_ID, 0, base))], rules=R3B, product_gid=OWN)
        assert out["map"] == [] and out["unmanaged"] == [_m(1)], (ims, base)
    for ims, base in [("4_1.jpeg", "4_1.png"), ("front", "front.webp"), ("4_1.jpeg", "4_1.jpeg"), ("front.JPG", "front.jpg")]:
        out = match_media_to_photos(["https://a/i/" + ims], [_cdn(1, _conn(OWN_ID, 0, base))], rules=R3B, product_gid=OWN)
        assert out["map"] == [{"url": "https://a/i/" + ims, "id": _m(1)}], (ims, base)


def test_default_rules_exclude_the_connector_prefix():
    """rules=('exact',) is the default: the own-id media is NOT claimed unless
    the caller opted in, even when the product gid is handed over."""
    media = [_cdn(1, _conn(OWN_ID, 0, "4_1.png"))]
    assert match_media_to_photos([RB], media, product_gid=OWN)["map"] == []
    assert match_media_to_photos([RB], media, rules=("exact",), product_gid=OWN)["map"] == []
    assert match_media_to_photos([RB], media, rules=("connector_prefix",), product_gid=OWN)["map"] == [{"url": RB, "id": _m(1)}]


def test_exact_stays_extension_strict_when_the_connector_rule_is_on():
    """Extension-agnostic ONLY under R3b: with both rules on, a prefix-less
    name still needs the same extension (exact), and the same name with the
    same extension is still claimed by exact."""
    out = match_media_to_photos(["https://a/i/front.jpeg"], [_cdn(1, "front.png")], rules=R3B, product_gid=OWN)
    assert out["map"] == [] and out["unmanaged"] == [_m(1)]
    out = match_media_to_photos(["https://a/i/front.png"], [_cdn(1, "front.png")], rules=R3B, product_gid=OWN)
    assert out["map"] == [{"url": "https://a/i/front.png", "id": _m(1)}]


def test_connector_rule_needs_the_own_gid_and_unknown_rules_are_refused():
    media = [_cdn(1, _conn(OWN_ID, 0, "4_1.png"))]
    with pytest.raises(ValueError):
        match_media_to_photos([RB], media, rules=R3B)
    with pytest.raises(ValueError):
        match_media_to_photos([RB], media, rules=R3B, product_gid="gid://shopify/Product/")
    with pytest.raises(ValueError):
        match_media_to_photos([RB], media, rules=("exact", "position"), product_gid=OWN)
    # the exact rules never needed the gid and still do not
    assert match_media_to_photos([RB], media)["map"] == []


def test_connector_prefix_is_one_to_one():
    """Two own-id media with the same basename fit the one photo: ambiguous,
    neither is claimed; two photos that fit one media claim nothing."""
    out = match_media_to_photos(
        [RB], [_cdn(1, _conn(OWN_ID, 0, "4_1.png")), _cdn(2, _conn(OWN_ID, 1, "4_1.jpg"))], rules=R3B, product_gid=OWN
    )
    assert out["map"] == [] and out["unmatched_photos"] == [RB] and out["unmanaged"] == [_m(1), _m(2)]
    out = match_media_to_photos(
        [RB, "https://b/4_1.png"], [_cdn(1, _conn(OWN_ID, 0, "4_1.png"))], rules=R3B, product_gid=OWN
    )
    assert out["map"] == [] and len(out["unmatched_photos"]) == 2


def test_script_rule_connector_prefix_adopts_and_leaves_the_rest_unmanaged(db, monkeypatch, capsys):
    """--rule connector-prefix end to end through the fake Shopify (a query,
    never a mutation): the same product adopts nothing under the default
    rule, then adopts the one own-id media under R3b -- map written through
    the one writer, the 4 other connector media unmanaged and PRINTED,
    locally_modified untouched, an audit row."""
    nodes = [_node(1, _conn(OWN_ID, 0, "4_1.png"), alt="Ray-Ban front")] + [
        _node(n, _conn(OWN_ID, n - 1, base)) for n, base in ((2, "5.png"), (3, "2.png"), (4, "3.png"), (5, "1st.png"))
    ]
    shop = _wire(monkeypatch, nodes)
    db["catalog_products"].insert_one(
        {"id": "P1", "sku": "SKU1", "images": [RB], "ecom": {"shopify_product_id": OWN, "locally_modified": False}}
    )
    out = _run(db, apply=True)
    assert out["rows"][0]["status"] == "unmatched" and out["written"] == []
    assert "media_map" not in _twin(db)["ecom"]
    out = _run(db, apply=True, rules=R3B)
    row = out["rows"][0]
    assert row["status"] == "adopt" and row["map"] == [{"url": RB, "id": _m(1)}]
    assert row["unmanaged"] == [_m(2), _m(3), _m(4), _m(5)] and out["written"] == ["P1"]
    twin = _twin(db)
    assert twin["ecom"]["media_map"] == [{"url": RB, "id": _m(1)}]
    assert twin["ecom"]["locally_modified"] is False and twin["images"] == [RB]
    assert shop.calls == [{"id": OWN}, {"id": OWN}]
    audit = list(db["audit_logs"].find({}))
    assert len(audit) == 1 and audit[0]["action"] == "MEDIA_MAP_ADOPT"
    printed = capsys.readouterr().out
    assert "+ %s -> %s (%s)" % (RB, _m(1), _conn(OWN_ID, 0, "4_1.png")) in printed
    assert "- unmanaged %s (%s)" % (_m(5), _conn(OWN_ID, 4, "1st.png")) in printed


def test_parse_args_rule_and_replace_doors():
    assert script.parse_args(["--ids", "P1"]).rule == "exact"
    assert script.RULES["exact"] == ("exact",)
    args = script.parse_args(["--ids", "P1", "--rule", "connector-prefix"])
    assert script.RULES[args.rule] == R3B and args.replace_photos_from_shopify is False
    assert script.parse_args(["--ids", "P1", "--replace-photos-from-shopify"]).replace_photos_from_shopify is True
    for argv in (
        ["--rule", "connector-prefix"],
        ["--replace-photos-from-shopify"],
        ["--ids", "P1", "--rule", "position"],
        ["--ids", "P1", "--replace-photos-from-shopify", "--rule", "connector-prefix"],
    ):
        with pytest.raises(SystemExit):
            script.parse_args(argv)


# ---------------------------------------------------------------------------
# ROUND 2 -- --replace-photos-from-shopify (the six bvi_import twins)
# ---------------------------------------------------------------------------

BVI_ID = "8922161348857"
BVI = "gid://shopify/Product/" + BVI_ID
STALE = [
    CDN + "Screenshot_2025-08-20_170151.png?v=1755689540",
    CDN + "Screenshot_2025-08-20_170156.png?v=1755689539",
    CDN + "Screenshot_2025-08-20_170201.png?v=1755689540",
]
ONE_NAME = _conn(BVI_ID, 0, "0rw4006__601_71__p21__shad__fr.png")
ONE = CDN + ONE_NAME + "?v=1785322878"
ONE_NODE = {"id": _m(1), "alt": "front view", "image": {"url": ONE}, "originalSource": {"url": "https://x/y.png"}}


def _seed_bvi(db, photos=STALE, spine=True, media_map=None, **twin_extra):
    ecom = {"shopify_product_id": BVI, "locally_modified": False, "source": "bvi_import", "status": "PUBLISHED"}
    if media_map is not None:
        ecom["media_map"] = media_map
    db["catalog_products"].insert_one({"id": "P1", "sku": "SKU1", "images": list(photos), "ecom": ecom, **twin_extra})
    if spine:
        db["products"].insert_one(
            {
                "product_id": "SP1",
                "id": "SP1",
                "pim_product_id": "P1",
                "sku": "SKU1",
                "brand": "Ray-Ban",
                "category": "SMARTGLASSES",
                "mrp": 29900.0,
                "offer_price": 29900.0,
                "images": list(photos),
                "is_active": True,
                "catalog_status": "ACTIVE",
            }
        )


def _spine(db):
    return copy.deepcopy(db["products"].find_one({"product_id": "SP1"}))


def _no_reversal_file(tmp_path):
    return not [p for p in os.listdir(tmp_path) if p.startswith("adopt_replace_reversal_")]


def test_replace_writes_through_the_spine_door_without_queuing(db, monkeypatch, tmp_path, capsys):
    """The one Shopify image becomes the photo list ON THE SPINE (the door),
    the mirror copies it to the twin, the twin is NOT queued
    (mark_dirty=False), the map lands as {url: cdn url, id: gid}, an audit
    row carries the before list, and the reversal is printed AND saved."""
    monkeypatch.chdir(tmp_path)
    shop = _wire(monkeypatch, [ONE_NODE])
    _seed_bvi(db)
    out = _run(db, apply=True, replace=True)
    row = out["rows"][0]
    assert row["status"] == "replace" and row["spine_id"] == "SP1" and row["photos"] == STALE
    assert row["map"] == [{"url": ONE, "id": _m(1)}] and out["written"] == ["P1"]
    assert _spine(db)["images"] == [ONE], "the spine must move -- a twin-only write is overwritten by the next edit"
    twin = _twin(db)
    assert twin["images"] == [ONE], "the mirror must have copied it"
    assert twin["ecom"]["locally_modified"] is False, "nothing may queue: Shopify already shows this image"
    assert twin["ecom"]["media_map"] == [{"url": ONE, "id": _m(1)}]
    assert twin["ecom"]["status"] == "PUBLISHED"
    assert shop.calls == [{"id": BVI}]
    audit = list(db["audit_logs"].find({}))
    assert len(audit) == 1 and audit[0]["action"] == "PHOTOS_REPLACED_FROM_SHOPIFY"
    assert audit[0]["before_state"] == {"photos": STALE, "spine_images": STALE, "media_map": None}
    assert audit[0]["after_state"] == {"photos": [ONE], "media_map": [{"url": ONE, "id": _m(1)}]}
    printed = capsys.readouterr().out
    for u in STALE:
        assert "before " + u in printed
    assert "after  %s -> %s (%s)" % (ONE, _m(1), ONE_NAME) in printed
    assert "previous photos P1: %s" % STALE in printed
    path = out["reversal_path"]
    assert path and os.path.isfile(path) and os.path.basename(path).startswith("adopt_replace_reversal_")
    assert "REVERSAL saved to " + path in printed
    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["products"] == [
        {
            "product_id": "P1",
            "spine_id": "SP1",
            "spine_images": STALE,
            "twin_photos": STALE,
            "media_map_before": None,
            "photo_after": ONE,
            "media_map_after": [{"url": ONE, "id": _m(1)}],
        }
    ]


def test_the_dirty_opt_out_is_what_keeps_the_twin_unqueued(db):
    """Positive control for mark_dirty: the SAME spine door with its default
    queues the twin (a human photo edit must), so the explicit False in the
    script is the one thing standing between the replace and a push. The
    bare mirror helper honours the same opt-out."""
    _seed_bvi(db)
    pm.update_product(
        product_id="SP1", patch={"images": [ONE]}, actor="t", product_repo=ProductRepository(db["products"]), db=script._Conn(db)
    )
    assert _twin(db)["images"] == [ONE] and _twin(db)["ecom"]["locally_modified"] is True
    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"ecom.locally_modified": False}})
    pm.update_product(
        product_id="SP1",
        patch={"images": STALE},
        actor="t",
        product_repo=ProductRepository(db["products"]),
        db=script._Conn(db),
        mark_dirty=False,
    )
    assert _twin(db)["images"] == STALE and _twin(db)["ecom"]["locally_modified"] is False
    pm.mirror_update_to_catalog_twin(product_id="P1", current={}, patch={"images": [ONE]}, db=script._Conn(db), mark_dirty=False)
    assert _twin(db)["images"] == [ONE] and _twin(db)["ecom"]["locally_modified"] is False


def test_replace_without_a_spine_uses_the_same_mirror_helper(db, monkeypatch, tmp_path):
    """A twin with no spine row (the archived sixth) is written through the
    SAME mirror helper with an empty spine -- never a second twin writer,
    never an upsert of a spine row."""
    monkeypatch.chdir(tmp_path)
    _wire(monkeypatch, [ONE_NODE])
    _seed_bvi(db, spine=False)
    calls = []
    real = pm.mirror_update_to_catalog_twin

    def spy(**kw):
        calls.append({k: v for k, v in kw.items() if k != "db"})
        return real(**kw)

    monkeypatch.setattr(pm, "mirror_update_to_catalog_twin", spy)
    out = _run(db, apply=True, replace=True)
    row = out["rows"][0]
    assert row["status"] == "replace" and row["spine_id"] is None and out["written"] == ["P1"]
    assert calls == [{"product_id": "P1", "current": {}, "patch": {"images": [ONE]}, "mark_dirty": False}]
    twin = _twin(db)
    assert twin["images"] == [ONE] and twin["ecom"]["locally_modified"] is False
    assert twin["ecom"]["media_map"] == [{"url": ONE, "id": _m(1)}]
    assert db["products"].find_one({"pim_product_id": "P1"}) is None
    with open(out["reversal_path"], encoding="utf-8") as fh:
        assert json.load(fh)["products"][0]["spine_id"] is None


def test_replace_refuses_a_media_count_other_than_one(monkeypatch, tmp_path, capsys):
    """The ruling covers ONE connector image per product: zero or two media
    is not that product -- reported (each media's name printed), nothing
    written on the spine, the twin or the map."""
    monkeypatch.chdir(tmp_path)
    two = [ONE_NODE, _node(2, _conn(BVI_ID, 1, "0rw4006__601_71__p21__shad__lt.png"))]
    for nodes in ([], two):
        db = _DB()
        _wire(monkeypatch, nodes)
        _seed_bvi(db)
        out = _run(db, apply=True, replace=True)
        row = out["rows"][0]
        assert row["status"] == "not_one_media" and row["media"] == len(nodes) and row["map"] == []
        assert out["written"] == [] and out["reversal_path"] is None
        assert _spine(db)["images"] == STALE and _twin(db)["images"] == STALE
        assert "media_map" not in _twin(db)["ecom"] and list(db["audit_logs"].find({})) == []
    assert _no_reversal_file(tmp_path)
    printed = capsys.readouterr().out
    assert "- media %s (%s)" % (_m(2), _conn(BVI_ID, 1, "0rw4006__601_71__p21__shad__lt.png")) in printed


def test_replace_refuses_an_existing_map_and_a_twin_the_door_cannot_clear(monkeypatch, tmp_path):
    """A map already there means the attach (or round 1) owns the answer; a
    singular image_url would survive the door's images[] write and the list
    would not be the one photo. Both are refused BEFORE the Shopify query."""
    monkeypatch.chdir(tmp_path)
    db = _DB()
    shop = _wire(monkeypatch, [ONE_NODE])
    _seed_bvi(db, media_map=[{"url": STALE[0], "id": _m(9)}])
    out = _run(db, apply=True, replace=True)
    assert out["rows"][0]["status"] == "already_mapped" and out["written"] == []
    assert _twin(db)["ecom"]["media_map"] == [{"url": STALE[0], "id": _m(9)}] and _spine(db)["images"] == STALE
    db = _DB()
    _seed_bvi(db, image_url=STALE[0])
    out = _run(db, apply=True, replace=True)
    assert out["rows"][0]["status"] == "twin_has_image_url" and out["written"] == []
    assert _twin(db)["images"] == STALE and "media_map" not in _twin(db)["ecom"]
    assert shop.calls == []
    assert _no_reversal_file(tmp_path)


def test_replace_dry_run_writes_nothing(db, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    _wire(monkeypatch, [ONE_NODE])
    _seed_bvi(db)
    out = _run(db, apply=False, replace=True)
    row = out["rows"][0]
    assert row["status"] == "replace" and row["map"] == [{"url": ONE, "id": _m(1)}] and row["photos"] == STALE
    assert out["written"] == [] and out["reversal_path"] is None
    assert _spine(db)["images"] == STALE and _twin(db)["images"] == STALE
    assert "media_map" not in _twin(db)["ecom"] and _twin(db)["ecom"]["locally_modified"] is False
    assert list(db["audit_logs"].find({})) == [] and _no_reversal_file(tmp_path)
    assert "DRY RUN - nothing written" in capsys.readouterr().out


def test_replace_never_adopts_when_the_twin_did_not_follow(db, monkeypatch, tmp_path, capsys):
    """The post-write gate: a spine whose pim_product_id points elsewhere
    mirrors to a twin that is not this one, so the twin's photo list is not
    the one image -- the map is NOT adopted (it would name a photo the pass
    cannot see) and the product is reported."""
    monkeypatch.chdir(tmp_path)
    _wire(monkeypatch, [ONE_NODE])
    _seed_bvi(db, spine=False)
    db["products"].insert_one(
        {"product_id": "P1", "id": "P1", "pim_product_id": "ELSEWHERE", "sku": "SKU1", "mrp": 1.0, "offer_price": 1.0, "images": list(STALE), "is_active": True}
    )
    out = _run(db, apply=True, replace=True)
    assert out["rows"][0]["spine_id"] == "P1" and out["written"] == [] and out["reversal_path"] is None
    assert _twin(db)["images"] == STALE and "media_map" not in _twin(db)["ecom"]
    assert list(db["audit_logs"].find({})) == []
    assert "PHOTOS NOT REPLACED P1" in capsys.readouterr().out
