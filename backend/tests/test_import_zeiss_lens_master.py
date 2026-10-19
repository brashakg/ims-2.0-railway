"""
scripts/import_zeiss_lens_master.py -- the ZEISS SRP (April 2026) lens-master import.

Pins (each red when its rule is removed):
  * every CSV row passes the lens_catalog door's own validator + slug;
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
    db["lens_enum_config"].update_one({"enum_id": "coatings"}, {"$set": {"items": ["OWNER_EDIT"]}})
    out = script.revert(db, "run1")
    assert out["deleted"] == 734 and out["kept_with_stock"] == [stocked]
    assert "coatings" in out["enums_edited_since"]
    assert db["lens_enum_config"].find_one({"enum_id": "coatings"})["items"] == ["OWNER_EDIT"]
    assert db["lens_enum_config"].find_one({"enum_id": "brands"})["items"] == ["Essilor"]
    # an enum the run created from nothing (indexes was a code default) is removed again
    assert db["lens_enum_config"].find_one({"enum_id": "indexes"}) is None
