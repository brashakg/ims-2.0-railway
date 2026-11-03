"""ONE rule links a credit-note ledger row to its return (RET- in ref/reason):
reports.gst_itc._ledger_row_return_ids. Tripwire: no other report module may
re-parse RET- tokens itself."""
import pathlib

from api.routers.reports.gst_itc import _ledger_row_return_doc, _ledger_row_return_ids


class _Col:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, q):
        return next((d for d in self.docs if d["return_id"] == q["return_id"]), None)


class _Db:
    def __init__(self, docs):
        self.col = _Col(docs)

    def get_collection(self, name):
        assert name == "returns"
        return self.col


def test_ids_from_ref_reason_and_neither():
    assert _ledger_row_return_ids({"ref": "RET-1"}) == ["RET-1"]
    assert _ledger_row_return_ids({"reason": "Return RET-9, damaged."}) == ["RET-9"]
    assert _ledger_row_return_ids({"ref": "RET-1", "reason": "Return RET-2."}) == ["RET-1", "RET-2"]
    assert _ledger_row_return_ids({"ref": "CN-7", "reason": "goodwill"}) == []
    assert _ledger_row_return_ids({}) == []


def test_doc_lookup_ref_reason_neither():
    db = _Db([{"return_id": "RET-9", "store_id": "S1"}])
    assert _ledger_row_return_doc(db, {"reason": "Return RET-9, x"})["store_id"] == "S1"
    assert _ledger_row_return_doc(db, {"ref": "RET-404", "reason": "Return RET-9"})["return_id"] == "RET-9"
    row = {"ref": "CN-1", "order_id": "O1"}
    assert _ledger_row_return_doc(db, row) is row
    assert _ledger_row_return_doc(db, {"ref": "CN-1"}) is None


def test_no_second_copy_of_the_rule():
    reports = pathlib.Path(__file__).resolve().parents[1] / "api" / "routers" / "reports"
    hits = [
        p.name
        for p in reports.glob("*.py")
        if 'startswith("RET-")' in p.read_text(encoding="utf-8") and p.name != "gst_itc.py"
    ]
    assert hits == [], f"second copy of the credit-note->return rule in {hits}; use _ledger_row_return_ids"
    assert (reports / "gst_itc.py").read_text(encoding="utf-8").count('startswith("RET-")') == 1
