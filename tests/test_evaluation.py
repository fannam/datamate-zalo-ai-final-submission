import unicodedata
import pytest

from datamate.evaluation import score_answer


@pytest.mark.parametrize("prediction,reference,score", [
    (None, {"type": "number", "answer": 1}, 0),
    ({"type": "abstain"}, {"type": "unanswerable"}, 0),
    ({"type": "unanswerable", "reason": "missing"}, {"type": "unanswerable"}, 1),
    ({"type": "unanswerable", "reason": 42}, {"type": "unanswerable"}, 0),
    ({"type": "number", "answer": "100"}, {"type": "number", "answer": 100}, -1),
    ({"type": "number", "answer": True}, {"type": "number", "answer": 1}, -1),
    ({"type": "number", "answer": 10000001}, {"type": "number", "answer": 10000000}, -1),
    ({"type": "percent", "answer": 12.345}, {"type": "percent", "answer": 12.35}, 1),
    ({"type": "percent", "answer": .124}, {"type": "percent", "answer": 12.4}, -1),
    ({"type": "boolean", "answer": 1}, {"type": "boolean", "answer": True}, -1),
    ({"type": "string", "answer": "SP0001"}, {"type": "string", "answer": "Cà phê sữa", "alias": ["SP0001"]}, 1),
    ({"type": "string", "answer": "Ca phe sua"}, {"type": "string", "answer": "Cà phê sữa"}, -1),
    ({"type": "string", "answer": unicodedata.normalize("NFD", "CÀ PHÊ SỮA!")}, {"type": "string", "answer": "Cà phê sữa"}, 1),
    ({"type": "list", "answer": ["B", "A"]}, {"type": "list", "answer": ["A", "B"]}, -1),
    ({"type": "list", "answer": ["SP1", "B"]}, {"type": "list", "answer": ["A", "B"], "alias": [["SP1"], []]}, 1),
    ({"type": "date", "answer": "01/05/2026"}, {"type": "date", "answer": "2026-05-01"}, -1),
])
def test_scoring_contract(prediction, reference, score):
    assert score_answer(prediction, reference)[0] == score


def test_tables_match_rows_and_aliases_without_order():
    reference = {"type": "table", "answer": {"columns": ["khach_hang", "so_tien_no"], "rows": [["Chị Lan", 100], ["Anh Nam", 200]]}, "alias": [["KH1"], ["KH2"]]}
    prediction = {"type": "table", "answer": {"columns": ["so_tien_no", "khach_hang"], "rows": [[200, "KH2"], [100, "KH1"]]}}
    assert score_answer(prediction, reference)[0] == 1
    prediction["answer"]["rows"][1][0] = "100"
    assert score_answer(prediction, reference)[0] == -1
