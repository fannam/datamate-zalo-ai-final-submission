"""Rules on small generated shops (rulebook §4, §6 Cà phê / Nhà thuốc / Mẹ và bé / Sửa xe, §8).

- a topping written in the drink's cell ('Trà sữa (M) + 2 Trân châu đen (14.000đ)') is its own item, merged with the
  topping sold on its own line; the drink keeps the line quantity and the rest of the money;
- 'size M' of a diaper is not the café '(M)' name suffix;
- repair book: an engine-oil brand written alone is the oil change; model names written for the same plates are
  one line; a plate in the question is a filter; each mechanic as a table; 'đông xe' on a day is not 'dòng xe';
  jobs ranked; commission lowest and the roster rate;
- outstanding debt also reads 'dư nợ', 'còn phải thu', 'khoản nợ';
- expiry windows: 'N tháng tới', 'từ nay đến hết ngày D', lots already expired.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "training_code" / "data_generation"))
import synth_phone  # noqa: E402

from datamate.core import CLOSE_NAMES, compile_frame  # noqa: E402
from datamate.engine import QueryEngine  # noqa: E402
from datamate.ingestion import prepare_shop  # noqa: E402
from datamate.output import finalize  # noqa: E402
from datamate.semantics import attach_semantics  # noqa: E402
from datamate.types import QueryFrame, Question  # noqa: E402

FRAME = dict(metric="other", operation="value", group_by="none", product_filter="", customer_filter="", group_filter="", customer_group_filter="", unit_filter="",
             payment_filter="", channel_filter="", size_filter="", k=1, threshold=0, threshold_operator=">=", weekday_filter=0, output_type="number", reason="t")


def build(tmp, files, domain=None, cutoff="31/03/2026"):
    split = tmp / "split"
    folder = split / "data" / "shop"
    folder.mkdir(parents=True)
    shutil.copy(synth_phone.ROOT / "train" / "calendar.csv", split / "calendar.csv")
    (split / "rulebook.md").write_text("rulebook")
    described = {}
    for name, (header, desc, rows) in files.items():
        synth_phone.write_csv(folder / name, header, rows)
        described[name] = (header, desc)
    (folder / "README.md").write_text(synth_phone.readme(described).replace("20/03/2026", cutoff))
    directory = prepare_shop(split, "shop", tmp / "cache")
    catalog = json.loads((directory / "catalog.json").read_text())
    if domain:
        catalog["llm_roles"] = {"domain": domain, "tables": {}}
    engine = QueryEngine(directory / "store.duckdb", {t["name"] for t in catalog["tables"]})
    attach_semantics(engine, catalog)
    return catalog, engine


def ask(catalog, engine, text, as_of="2026-03-31", **frame):
    plan = compile_frame(QueryFrame(**(FRAME | frame)), Question(id="x", business_id="shop", question=text, as_of_date=as_of), catalog, engine)
    if plan.action == "unanswerable":
        return "unanswerable"
    return finalize(plan, *engine.query(plan.sql))["answer"]


CAFE = ["Mã đơn", "Ngày", "Tên hàng", "SL", "ĐVT", "Thành tiền", "Trạng thái"]
CAFE_DESC = {"Mã đơn": "mã đơn", "Ngày": "ngày bán", "Tên hàng": "tên hàng", "SL": "số lượng", "ĐVT": "đơn vị tính", "Thành tiền": "thành tiền sau giảm", "Trạng thái": "trạng thái đơn"}
CAFE_LINES = [("D1", "02/03/2026", "Trà sữa (M) + 2 Trân châu đen (14.000đ)", 1, "ly", 49000, "Hoàn thành"),
              ("D2", "02/03/2026", "Trà sữa (M)", 2, "ly", 70000, "Hoàn thành"),
              ("D3", "03/03/2026", "Topping Trân châu đen", 1, "phần", 7000, "Hoàn thành"),
              ("D4", "03/03/2026", "Latte (L) + Kem cheese (12.000đ) + Trân châu đen (7.000đ)", 2, "ly", 109000, "Hoàn thành"),
              ("D5", "04/03/2026", "Combo sáng + bánh mì", 1, "phần", 50000, "Hoàn thành")]


def test_toppings_in_the_drink_cell_are_their_own_items(tmp_path):
    catalog, engine = build(tmp_path, {"ban_hang.csv": (CAFE, CAFE_DESC, [dict(zip(CAFE, line)) for line in CAFE_LINES])})
    try:
        assert ask(catalog, engine, "Tháng 3/2026 bán được bao nhiêu ly Trà sữa (M)?", metric="quantity", product_filter="Trà sữa (M)") == 3
        assert ask(catalog, engine, "Tháng 3/2026 Trà sữa (M) bán được bao nhiêu tiền?", metric="revenue", product_filter="Trà sữa (M)") == 105000
        # 2 portions in D1, 1 on its own line, 1 in D4: one item with the topping sold on its own line.
        assert ask(catalog, engine, "Tháng 3/2026 bán được bao nhiêu phần trân châu đen?", metric="quantity", product_filter="Trân châu đen") == 4
        assert ask(catalog, engine, "Tháng 3/2026 trân châu đen thu về bao nhiêu tiền?", metric="revenue", product_filter="Trân châu đen") == 28000
        assert ask(catalog, engine, "Tháng 3/2026 Latte (L) bán được bao nhiêu tiền?", metric="revenue", product_filter="Latte (L)") == 90000
        assert ask(catalog, engine, "Tháng 3/2026 doanh thu bao nhiêu?", metric="revenue") == 285000   # line totals unchanged
        # No money in brackets: a combo name, not a drink with toppings.
        assert ask(catalog, engine, "Tháng 3/2026 bán được mấy phần Combo sáng + bánh mì?", metric="quantity", product_filter="Combo sáng + bánh mì") == 1
    finally:
        engine.close()


def test_diaper_size_is_not_the_cafe_size_suffix(tmp_path):
    lines = [("D1", "02/03/2026", "Tã quần Bobby size L 44 miếng", 2, "bịch", 400000, "Hoàn thành"), ("D2", "03/03/2026", "Tã quần Bobby size M 50 miếng", 1, "bịch", 200000, "Hoàn thành")]
    catalog, engine = build(tmp_path, {"ban_hang.csv": (CAFE, CAFE_DESC, [dict(zip(CAFE, line)) for line in lines])})
    try:
        assert ask(catalog, engine, "Tháng 3/2026 bán được bao nhiêu bịch tã quần Bobby size L?", metric="quantity", product_filter="Tã quần Bobby size L 44 miếng") == 2
    finally:
        engine.close()


BOOK = ["Ngày", "Xe", "Làm gì", "Thợ", "Tiền", "Ghi chú"]
BOOK_DESC = {"Ngày": "ngày sửa", "Xe": "dòng xe và biển số ghi chung một ô", "Làm gì": "các việc đã làm cho xe", "Thợ": "thợ làm phiếu, ghi tên gọi", "Tiền": "tổng tiền cả phiếu", "Ghi chú": "ghi chú"}
TICKETS = [("02/03/2026", "Vision 59X5-435.74", "thay nhớt", "a Đức", 120000, ""),
           ("02/03/2026", "Vision 110 59X5-435.74", "Motul", "Đức", 135000, ""),
           ("02/03/2026", "Vision 110 51A1-222.33", "Shell 1L, vá xe", "A.Đạt", 160000, ""),
           ("02/03/2026", "Lead 50B2-111.22", "vá xe", "Đạt", 25000, ""),
           ("03/03/2026", "vision 51A1-222.33", "bảo dưỡng", "Đạt", 150000, ""),
           ("03/03/2026", "Lead 50B2-111.22", "vá xe", "Đức", 25000, ""),
           ("03/03/2026", "Lead 50B2-111.22", "vá xe", "Đức", 0, "bảo hành"),
           ("04/03/2026", "Wave RSX 60C3-555.66", "bảo dưỡng", "Đạt", 150000, ""),
           ("04/03/2026", "Wave Alpha 61D4-777.88", "vá xe", "Đạt", 25000, "")]
ROSTER = ["Họ tên", "Ngày vào làm", "Hoa hồng (%)"]
ROSTER_DESC = {"Họ tên": "họ tên thợ", "Ngày vào làm": "ngày vào làm", "Hoa hồng (%)": "phần trăm hoa hồng của thợ, tính trên tổng Tiền các phiếu thợ làm trong tháng"}


@pytest.fixture
def repair(tmp_path):
    catalog, engine = build(tmp_path, {"so_sua_xe.csv": (BOOK, BOOK_DESC, [dict(zip(BOOK, t)) for t in TICKETS]),
                                       "danh_sach_tho.csv": (ROSTER, ROSTER_DESC, [{"Họ tên": "Lương Chi Đức", "Ngày vào làm": "01/01/2024", "Hoa hồng (%)": 25},
                                                                                  {"Họ tên": "Hà Loan Đạt", "Ngày vào làm": "01/01/2023", "Hoa hồng (%)": 32}])}, domain="vehicle_repair")
    yield catalog, engine
    engine.close()


def test_repair_book_rules(repair):
    catalog, engine = repair
    q = lambda text, **frame: ask(catalog, engine, text, **frame)
    assert q("Tháng 3/2026 có bao nhiêu lượt thay nhớt?") == 3            # 'Motul', 'Shell 1L' alone are oil changes
    assert q("Tháng 3/2026 dòng xe nào tới nhiều nhất?") == "Vision"      # Vision / Vision 110 / vision share plates
    assert q("Tháng 3/2026 có bao nhiêu lượt xe Vision?") == 4
    assert q("Tháng 3/2026 có bao nhiêu lượt xe Wave RSX?") == 1          # 'wave' inside 'wave rsx' is not a second model
    assert q("Tháng 3/2026 xe biển số 50B2-111.22 tới mấy lần?") == 2     # a warranty ticket is not a visit
    assert q("Tháng 3/2026 mỗi thợ làm bao nhiêu phiếu?") == {"columns": ["tho", "so_phieu"], "rows": [["Đạt", 5], ["Đức", 4]]}
    assert q("Ngày nào trong tháng 3/2026 tiệm đông xe nhất?") == "2026-03-02"
    assert q("Tháng 3/2026 việc nào được làm nhiều nhất?") == "vá xe"
    assert q("Tháng 3/2026 hoa hồng thợ nào thấp nhất?") == "Đức"
    assert q("Anh Đạt được hưởng bao nhiêu phần trăm hoa hồng?") == 32


def test_job_ranking_excludes_warranty_and_counts_tickets_once(repair):
    catalog, engine = repair
    assert ask(catalog, engine, "Top 2 việc làm nhiều nhất tháng 3/2026 là gì?") == ["vá xe", "thay nhớt"]


def test_closest_item_name_only_as_a_last_resort(tmp_path):
    lines = [("D1", "02/03/2026", "Sữa Similac 5G số 2 900g", 3, "lon", 1500000, "Hoàn thành"), ("D2", "03/03/2026", "Sữa Similac 5G số 3 900g", 1, "lon", 500000, "Hoàn thành")]
    catalog, engine = build(tmp_path, {"ban_hang.csv": (CAFE, CAFE_DESC, [dict(zip(CAFE, line)) for line in lines])})
    try:
        text = "Tháng 3/2026 bán được bao nhiêu lon Sua Similac 5G so 2 900gr?"
        with pytest.raises(Exception):
            ask(catalog, engine, text, metric="quantity", product_filter="Sua Similac 5G so 2 900gr")
        token = CLOSE_NAMES.set(True)
        try:
            assert ask(catalog, engine, text, metric="quantity", product_filter="Sua Similac 5G so 2 900gr") == 3
            # A number that is not in the name never matches ('số 4').
            with pytest.raises(Exception):
                ask(catalog, engine, "Tháng 3/2026 bán được bao nhiêu lon Similac 5G so 4 900g?", metric="quantity", product_filter="Similac 5G so 4 900g")
        finally:
            CLOSE_NAMES.reset(token)
    finally:
        engine.close()
