"""
scripts/import_zeiss_lens_master.py -- the ZEISS SRP + WSP (April 2026) lens-master import.

Pins (each red when its rule is removed):
  * every CSV row passes the lens_catalog door's own validator + slug;
  * mrp = SRP per pair, cost_price = WSP per pair (below MRP on every line),
    material = the ZEISS substrate of the index;
  * each line carries its add-on list, minus the combinations ZEISS rules out,
    and a nested upgrade cost is masked like the line's own;
  * a dry run writes NOTHING;
  * --apply creates one lens line per row, unions the enum values the rows
    need and never drops an owner value; a re-run creates nothing;
  * --revert deletes only that run's lines without stock and restores an
    enum list only when nobody edited it since.

StrictDB only -- no network, no production.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "scripts"))

from strict_fakes import StrictDB  # noqa: E402
import import_zeiss_lens_master as script  # noqa: E402

ROWS = script.load_rows()


def _db():
    db = StrictDB()
    db.seed("lens_enum_config", [
        {"enum_id": "brands", "items": ["Essilor"]},
        {"enum_id": "coatings", "items": ["ANTI_BLUE", "HC"]},
    ])
    return db


def test_every_row_passes_the_door_validator():
    docs, _, added, errors = script.build(ROWS, script.live_enums(_db()))
    assert errors == []
    assert len(docs) == len(ROWS) == 735
    assert added["brands"] == ["ZEISS"] and 1.59 in added["indexes"]
    # price is the SRP per pair, untouched
    first = next(d for d in docs if d["series"] == "Progressive ClearMind Individual 3"
                 and d["index"] == 1.5 and d["coating"] == "DVP_PLATINUM_UV")
    assert first["mrp"] == 89300.0 and first["hsn_code"] == "900150" and first["gst_rate"] == 5.0
    # cost = the WSP per pair (excl GST), present and below MRP everywhere
    assert first["cost_price"] == 31900.0
    assert all(0 < d["cost_price"] < d["mrp"] for d in docs)
    # material follows the index, never the old CLEAR / BLUEGUARD option
    assert {(d["index"], d["material"]) for d in docs} == {
        (1.5, "CR39"), (1.56, "KOC55"), (1.59, "POLY"), (1.6, "MR8"),
        (1.67, "MR7_MR10"), (1.74, "MR174")}
    assert added["materials"] == ["MR7_MR10", "KOC55"]
    koc = [d for d in docs if d["material"] == "KOC55"]
    assert len(koc) == 8 and all("Material: KOC 55 Plastic." in d["notes"] for d in koc)
    # DriveSafe: DuraVision DriveSafe UV (owner 10-19); SV DriveSafe keeps the IMS default range
    drive = [d for d in docs if "DriveSafe" in d["series"]]
    assert len(drive) == 16 and all(d["coating"] == "DV_DRIVESAFE_UV" for d in drive)
    sv = [d for d in drive if d["series"].startswith("Single Vision")]
    assert len(sv) == 8 and all(d["sph_range"] == {"min": -8.0, "max": 6.0, "step": 0.25}
                                and "IMS default range applied (owner 10-19)" in d["notes"] for d in sv)
    # the priced option lives in the series, so both lines survive
    assert any(d["series"] == "Progressive ClassicPlus V Photo Grey" for d in docs)


def _line(docs, series, index, coating):
    return next(d for d in docs if d["series"] == series and d["index"] == index and d["coating"] == coating)


def test_upgrade_lists_follow_zeiss_rules():
    docs, _, _, _ = script.build(ROWS, script.live_enums(_db()))
    names = lambda d: [u["name"] for u in d["upgrades"]]  # noqa: E731
    plat = _line(docs, "Progressive ClearMind Individual 3", 1.5, "DVP_PLATINUM_UV")
    mirror = next(u for u in plat["upgrades"] if u["name"] == "Mirror")
    assert (mirror["mrp"], mirror["cost_price"]) == (15000.0, 6500.0)
    # Mirror / Flash / AdaptiveSun are priced on Platinum only
    assert "Mirror" not in names(_line(docs, "Progressive ClearMind Individual 3", 1.5, "DVP_CHROME_UV"))
    # BlueProtect rules out BlueGuard, PhotoFusion X and Polarised
    bp = names(_line(docs, "Progressive ClearMind Individual 3", 1.5, "DVP_BLUEPROTECT_UV"))
    assert not {"BlueGuard", "PhotoFusion X", "Polarised"} & set(bp) and "Tint" in bp
    # a photochromic line takes no tint / photochromic upgrade
    pg = names(_line(docs, "Progressive ClassicPlus V Photo Grey", 1.5, "DVP_PLATINUM_UV"))
    assert "Tint" not in pg and "BlueGuard" not in pg and "Prism" in pg
    # index list: Polarised 1.5 / 1.6 / 1.67 only; "(3Dv only)" limits Light 2
    assert "Polarised" not in names(_line(docs, "Progressive ClearMind Individual 3", 1.59, "DVP_PLATINUM_UV"))
    assert "Mirror" in names(_line(docs, "Progressive Light 2 3Dv", 1.5, "DVP_PLATINUM_UV"))
    assert "Mirror" not in names(_line(docs, "Progressive Light 2 3D", 1.5, "DVP_PLATINUM_UV"))
    # "(Individual)" on Digital SmartLife covers Individual AND Individual 3 (owner 10-19)
    for series in ("Digital SmartLife Individual", "Digital SmartLife Individual 3"):
        assert {"Mirror", "Flash"} <= set(names(_line(docs, series, 1.5, "DVP_PLATINUM_UV")))
    assert "Mirror" not in names(_line(docs, "Digital SmartLife", 1.5, "DVP_PLATINUM_UV"))


def test_sv_clearmind_167_takes_the_wsp_upgrades():
    """Owner 10-19: Flash, PhotoFusion X, BlueGuard and Tint on SV ClearMind
    1.67 too, at the 1.5 / 1.6 MRP and the WSP cost printed for 1.67."""
    docs, _, _, _ = script.build(ROWS, script.live_enums(_db()))
    plat = {u["name"]: u for u in _line(docs, "Single Vision ClearMind", 1.67, "DVP_PLATINUM_UV")["upgrades"]}
    low = {u["name"]: u for u in _line(docs, "Single Vision ClearMind", 1.6, "DVP_PLATINUM_UV")["upgrades"]}
    for name, cost in (("Flash", 6500.0), ("PhotoFusion X", 4000.0), ("BlueGuard", 350.0), ("Tint", 400.0)):
        assert plat[name]["mrp"] == low[name]["mrp"] and plat[name]["cost_price"] == cost
    assert "Mirror" not in plat  # not part of the ruling (ZEISS lists Mirror for 1.5 / 1.6 only)


def test_upgrade_validation_rejects_bad_rows():
    from api.services.lens_catalog_validation import DEFAULT_ENUM_ITEMS, validate_lens_catalog_payload
    cfg = {**DEFAULT_ENUM_ITEMS, "brands": ["B"]}

    def v(payload):
        return validate_lens_catalog_payload(payload, enum_config=cfg)

    base = {"brand": "B", "series": "S", "index": 1.5, "material": "CR39",
            "lens_type": "SV", "coating": "HC", "mrp": 100}
    ok = v({**base, "upgrades": [{"name": "Tint", "mrp": 800, "cost_price": 400}]})
    assert ok["upgrades"] == [{"name": "Tint", "mrp": 800.0, "cost_price": 400.0}]
    bad = [
        [{"name": "Tint", "mrp": 800}, {"name": "tint", "mrp": 900}],  # same name twice
        [{"name": "Tint", "mrp": -1}],
        [{"name": "Tint", "mrp": float("nan")}],
        [{"name": "Tint", "mrp": float("inf")}],
        [{"name": "Tint", "mrp": 800, "cost_price": float("nan")}],
        [{"name": "Tint", "mrp": 800, "cost_price": float("-inf")}],
        [{"name": "", "mrp": 800}],
        "Tint",
    ]
    for ups in bad:
        try:
            v({**base, "upgrades": ups})
        except ValueError:
            continue
        raise AssertionError("accepted %r" % (ups,))
    # the same finite-number rule guards the line's own price
    for mrp in (float("nan"), float("inf")):
        try:
            v({**base, "mrp": mrp})
        except ValueError:
            continue
        raise AssertionError("accepted mrp %r" % mrp)


def test_upgrade_cost_is_masked_like_the_line_cost():
    from api.services.cost_mask import mask_cost
    docs, _, _, _ = script.build(ROWS, script.live_enums(_db()))
    shown = mask_cost(_line(docs, "Progressive ClearMind Individual 3", 1.5, "DVP_PLATINUM_UV"),
                      {"roles": ["SALES_STAFF"]}, "product")
    assert "cost_price" not in shown and shown["upgrades"]
    assert all("cost_price" not in u and u["mrp"] >= 0 for u in shown["upgrades"])


def test_dry_run_writes_nothing(monkeypatch):
    db = _db()
    monkeypatch.setattr("pymongo.MongoClient", lambda *a, **k: {"ims_2_0": db})
    assert script.main(["--mongo-uri", "mongodb://fake"]) == 0
    assert db["lens_catalog"].count_documents({}) == 0
    assert db["lens_import_backups"].count_documents({}) == 0
    assert db["lens_enum_config"].find_one({"enum_id": "brands"})["items"] == ["Essilor"]


def test_apply_rerun_and_revert():
    db = _db()
    docs, enums, added, _ = script.build(ROWS, script.live_enums(db))
    res = script.apply(db, docs, enums, added, "run1")
    assert res == {"created": 735, "skipped_existing": 0}
    assert db["lens_enum_config"].find_one({"enum_id": "brands"})["items"] == ["Essilor", "ZEISS"]
    assert {"ANTI_BLUE", "HC", "DVP_GOLD_UV"} <= set(db["lens_enum_config"].find_one({"enum_id": "coatings"})["items"])

    docs2, enums2, added2, _ = script.build(ROWS, script.live_enums(db))
    assert added2 == {}
    assert script.apply(db, docs2, enums2, added2, "run2") == {"created": 0, "skipped_existing": 735}

    stocked = docs[0]["lens_line_id"]
    db.seed("lens_stock_lines", [{"lens_line_id": stocked, "on_hand": 2}])
    out = script.revert(db, "run1")
    assert out["deleted"] == 734 and out["kept_with_stock"] == [stocked]
    # the kept line still uses ZEISS / 1.59 / the coatings: no enum list is put back
    assert out["enums_restored"] == [] and out["enums_not_restored"]
    assert db["lens_enum_config"].find_one({"enum_id": "brands"})["items"] == ["Essilor", "ZEISS"]
    assert 1.59 in db["lens_enum_config"].find_one({"enum_id": "indexes"})["items"]


def test_revert_after_a_run_that_died_part_way():
    """apply crashes on insert 400 (a network blip): revert must still find
    every line by import_run_id and put the enum lists back."""
    db = _db()
    docs, enums, added, _ = script.build(ROWS, script.live_enums(db))
    coll = db["lens_catalog"]
    real, n = coll.insert_one, [0]

    def flaky(doc, *a, **k):
        n[0] += 1
        if n[0] == 400:
            raise RuntimeError("network blip")
        return real(doc, *a, **k)

    coll.insert_one = flaky
    try:
        script.apply(db, docs, enums, added, "runX")
        raise AssertionError("apply should have crashed")
    except RuntimeError:
        pass
    coll.insert_one = real
    assert coll.count_documents({"import_run_id": "runX"}) == 399
    db["lens_enum_config"].update_one({"enum_id": "coatings"}, {"$set": {"items": ["OWNER_EDIT"]}})
    out = script.revert(db, "runX")
    assert out["deleted"] == 399 and out["kept_with_stock"] == []
    assert coll.count_documents({"brand": "ZEISS"}) == 0
    assert db["lens_enum_config"].find_one({"enum_id": "brands"})["items"] == ["Essilor"]
    # an enum the run created from nothing (indexes was a code default) is removed again
    assert db["lens_enum_config"].find_one({"enum_id": "indexes"}) is None
    # a list the owner edited since the run is left alone
    assert "coatings" in out["enums_edited_since"]
    assert db["lens_enum_config"].find_one({"enum_id": "coatings"})["items"] == ["OWNER_EDIT"]


def test_main_prints_the_run_id_before_the_first_write(monkeypatch, capsys):
    db = _db()
    seen = []
    real = db["lens_import_backups"].insert_one

    def spy(doc, *a, **k):
        seen.append(capsys.readouterr().out)
        return real(doc, *a, **k)

    db["lens_import_backups"].insert_one = spy
    monkeypatch.setattr("pymongo.MongoClient", lambda *a, **k: {"ims_2_0": db})
    assert script.main(["--mongo-uri", "mongodb://fake", "--apply"]) == 0
    assert "[RUN] zeiss-2026-04-" in seen[0]

