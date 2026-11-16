"""Rules on small generated shops (rulebook §1, §2, §3, §6, §8).

- 'từ tháng 1 đến tháng 3' asked as one figure is one period, both ends included; 'từng tháng' stays a list;
- 'bao nhiêu khách (đã) mua' counts identified customers with a valid order, not orders;
- 'bớt' (discount) is matched with its accent: 'Bột ngọt' is an item;
- a drug named in full is that item, not every drug of its ingredient;
- the seating area named in the question ('khu phòng lạnh') filters the orders;
- repair book: everyday job names ('thay dầu', 'thay phanh', 'thay sên'); 'tăng bao nhiêu %' compares with the period before;
- phone shop: an item named in full is that item, not its model line or brand.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_phone_shop import FRAME as PHONE_FRAME, phone_shops  # noqa: E402,F401
from test_rules_items_and_domains import BOOK, BOOK_DESC, ROSTER, ROSTER_DESC, TICKETS, ask, build  # noqa: E402

from datamate.core import compile_frame  # noqa: E402
from datamate.domains import question_job  # noqa: E402
from datamate.output import finalize  # noqa: E402
from datamate.types import QueryFrame, Question  # noqa: E402

SALES = ["Mã đơn", "Ngày", "Khách hàng", "Tên hàng", "SL", "ĐVT", "Thành tiền", "Trạng thái"]
SALES_DESC = {"Mã đơn": "mã đơn", "Ngày": "ngày bán", "Khách hàng": "tên khách (Khách lẻ nếu không lưu)", "Tên hàng": "tên hàng", "SL": "số lượng",
              "ĐVT": "đơn vị tính", "Thành tiền": "thành tiền sau giảm", "Trạng thái": "trạng thái đơn"}
LINES = [("D1", "05/01/2026", "Lan", "Bột ngọt Ajinomoto 400g", 2, "gói", 60000, "Hoàn thành"),
         ("D2", "10/02/2026", "Lan", "Bột ngọt Ajinomoto 400g", 1, "gói", 30000, "Hoàn thành"),
         ("D3", "11/02/2026", "Minh", "Nước mắm Nam Ngư 750ml", 1, "chai", 40000, "Hoàn thành"),
         ("D4", "12/03/2026", "Khách lẻ", "Nước mắm Nam Ngư 750ml", 2, "chai", 80000, "Hoàn thành"),
         ("D5", "20/03/2026", "Hoa", "Bột ngọt Ajinomoto 400g", 1, "gói", 30000, "Huỷ"),
         ("D6", "25/03/2026", "Minh", "Bột ngọt Ajinomoto 400g", 1, "gói", 30000, "Hoàn thành")]


@pytest.fixture
def grocery(tmp_path):
    catalog, engine = build(tmp_path, {"ban_hang.csv": (SALES, SALES_DESC, [dict(zip(SALES, line)) for line in LINES])})
    yield catalog, engine
    engine.close()


def test_month_runs_and_customer_counts(grocery):
    catalog, engine = grocery
    assert ask(catalog, engine, "Doanh thu từ tháng 1 đến tháng 3/2026 là bao nhiêu?", metric="revenue") == 240000
    assert ask(catalog, engine, "Doanh thu tháng 1 đến tháng 3/2026 là bao nhiêu?", metric="revenue") == 240000
    assert ask(catalog, engine, "Doanh thu từng tháng từ tháng 1 đến tháng 3/2026?", metric="revenue", operation="series", output_type="list") == [60000, 70000, 110000]
    assert ask(catalog, engine, "Doanh thu từ đầu tháng 2 đến cuối tháng 3/2026 là bao nhiêu?", metric="revenue") == 180000
    # Lan, Minh: the walk-in and the cancelled order are not customers who bought (§1, §3).
    assert ask(catalog, engine, "Từ tháng 1 đến tháng 3/2026 có bao nhiêu khách đã mua hàng?", metric="orders") == 2
    assert ask(catalog, engine, "Từ tháng 1 đến tháng 3/2026 có bao nhiêu khách mua Bột ngọt Ajinomoto 400g?", metric="quantity",
               product_filter="Bột ngọt Ajinomoto 400g") == 2
    assert ask(catalog, engine, "Tháng 2/2026 bán Bột ngọt Ajinomoto 400g được bao nhiêu tiền?", metric="revenue", product_filter="Bột ngọt Ajinomoto 400g") == 30000


DRUGS = ["Mã hàng", "Tên hàng", "Hoạt chất", "Kê đơn"]
DRUG_DESC = {"Mã hàng": "mã hàng", "Tên hàng": "tên hàng", "Hoạt chất": "hoạt chất", "Kê đơn": "thuốc kê đơn"}
DRUG_SALES = ["Mã đơn", "Ngày", "Mã hàng", "Tên hàng", "SL", "ĐVT", "Thành tiền", "Trạng thái"]


def test_named_drug_is_not_every_drug_of_its_ingredient(tmp_path):
    catalog, engine = build(tmp_path, {
        "danh_sach_thuoc.csv": (DRUGS, DRUG_DESC, [{"Mã hàng": "P1", "Tên hàng": "Vitamin C 500mg Quapharco hộp 10 vỉ", "Hoạt chất": "Vitamin C", "Kê đơn": 0},
                                                    {"Mã hàng": "P2", "Tên hàng": "UPSA-C 1g viên sủi tuýp 10 viên", "Hoạt chất": "Vitamin C", "Kê đơn": 0}]),
        "ban_hang.csv": (DRUG_SALES, {**SALES_DESC, "Mã hàng": "mã hàng"}, [
            {"Mã đơn": "A", "Ngày": "02/03/2026", "Mã hàng": "P1", "Tên hàng": "Vitamin C 500mg Quapharco hộp 10 vỉ", "SL": 1, "ĐVT": "hộp", "Thành tiền": 30000, "Trạng thái": "Xong"},
            {"Mã đơn": "B", "Ngày": "03/03/2026", "Mã hàng": "P2", "Tên hàng": "UPSA-C 1g viên sủi tuýp 10 viên", "SL": 1, "ĐVT": "tuýp", "Thành tiền": 70000, "Trạng thái": "Xong"}])})
    try:
        assert ask(catalog, engine, "Tháng 3/2026 doanh thu Vitamin C 500mg Quapharco hộp 10 vỉ là bao nhiêu?", metric="revenue",
                   product_filter="Vitamin C 500mg Quapharco hộp 10 vỉ") == 30000
        assert ask(catalog, engine, "Tháng 3/2026 doanh thu thuốc chứa Vitamin C là bao nhiêu?", metric="revenue", product_filter="Vitamin C") == 100000
    finally:
        engine.close()


DINING = ["Mã đơn", "Ngày", "Tên hàng", "SL", "Thành tiền", "Trạng thái", "Bàn", "Số khách", "Khu vực"]
DINING_DESC = {"Mã đơn": "mã đơn", "Ngày": "ngày bán", "Tên hàng": "tên hàng", "SL": "số lượng", "Thành tiền": "thành tiền sau giảm",
               "Trạng thái": "trạng thái đơn", "Bàn": "bàn", "Số khách": "số khách của đơn", "Khu vực": "khu vực của bàn"}


def test_seating_area_filters_orders(tmp_path):
    rows = [("B1", "02/03/2026", "Lẩu thái", 1, 300000, "Xong", "B01", 4, "phòng lạnh"),
            ("B2", "02/03/2026", "Lẩu thái", 1, 300000, "Xong", "S02", 2, "sân"),
            ("B3", "03/03/2026", "Cơm chiên", 2, 100000, "Xong", "B03", 3, "phòng lạnh")]
    catalog, engine = build(tmp_path, {"ban_hang.csv": (DINING, DINING_DESC, [dict(zip(DINING, r)) for r in rows])})
    try:
        assert ask(catalog, engine, "Tháng 3/2026 doanh thu khu phòng lạnh là bao nhiêu?", metric="revenue") == 400000
        assert ask(catalog, engine, "Tháng 3/2026 khách ngồi ngoài sân mua bao nhiêu đơn?", metric="orders") == 1
        assert ask(catalog, engine, "Tháng 3/2026 khu phòng lạnh có bao nhiêu lượt khách?") == 7
        assert ask(catalog, engine, "Tháng 3/2026 doanh thu bao nhiêu?", metric="revenue") == 700000
    finally:
        engine.close()


@pytest.mark.parametrize("phrase,job", [("thay dầu", "nhot may"), ("thay dầu láp", "nhot lap"), ("thay phanh", "ma phanh%"), ("thay bố", "ma phanh%"),
                                        ("thay sên", "nhong sen dia"), ("thay xích", "nhong sen dia"), ("vá lốp", "va xe"), ("thay bố trước", "ma phanh truoc")])
def test_everyday_job_names(phrase, job):
    import re
    from datamate.parsing import fold
    keys = {"nhot may", "nhot lap", "ma phanh truoc", "ma phanh sau", "nhong sen dia", "va xe", "chinh sen"}
    text = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%]+", " ", fold(f"Tháng 3/2026 có bao nhiêu lượt {phrase}?"))).strip()
    assert question_job(text, keys) == job


def test_repair_growth_without_a_named_base(tmp_path):
    tickets = TICKETS + [("05/02/2026", "Lead 50B2-111.22", "vá xe", "Đức", 100000, "")]
    catalog, engine = build(tmp_path, {"so_sua_xe.csv": (BOOK, BOOK_DESC, [dict(zip(BOOK, t)) for t in tickets]),
                                       "danh_sach_tho.csv": (ROSTER, ROSTER_DESC, [{"Họ tên": "Lương Chi Đức", "Ngày vào làm": "01/01/2024", "Hoa hồng (%)": 25}])},
                            domain="vehicle_repair")
    try:
        march = sum(t[4] for t in TICKETS)
        assert ask(catalog, engine, "Doanh thu tháng 3/2026 tăng bao nhiêu %?") == round(100 * (march - 100000) / 100000, 2)
    finally:
        engine.close()


@pytest.mark.parametrize("layout", ["phone_xlsx", "phone_tradein_line"])
def test_phone_item_named_in_full_is_that_item(phone_shops, layout):  # noqa: F811
    shops, _ = phone_shops
    catalog, engine = shops[layout]
    _, rows = engine.query("SELECT product_name, SUM(revenue) FROM v_sales WHERE order_date>='2026-02-01' AND order_date<'2026-03-01' GROUP BY 1")
    wrong = []
    for name, revenue in rows:
        plan = compile_frame(QueryFrame(**(PHONE_FRAME | {"metric": "revenue", "product_filter": name})),
                             Question(id="x", business_id=layout, question=f"Tháng 2/2026 doanh thu {name} là bao nhiêu?", as_of_date="2026-03-20"), catalog, engine)
        got = finalize(plan, *engine.query(plan.sql))["answer"]
        if abs(float(got) - float(revenue)) > 0.5:
            wrong.append((name, got, float(revenue)))
    assert not wrong, wrong


def test_unrecorded_information_is_unanswerable(grocery):
    catalog, engine = grocery
    for text in ("Ai là người lành nghề nhất trong số nhân viên?", "Nhân viên nào được khách khen nhiều nhất?", "Thuốc này người lớn nên uống mỗi lần mấy viên?",
                 "Tháng 1/2026 doanh thu thay đổi bao nhiêu % so với tháng liền trước?", "Tháng 3/2026 doanh thu khách nhóm thân thiết là bao nhiêu?"):
        frame = {"operation": "growth", "output_type": "percent"} if "%" in text else {}
        assert ask(catalog, engine, text, metric="revenue", **frame) == "unanswerable", text


def test_planner_slot_noise(grocery):
    catalog, engine = grocery
    # 'N ngày gần nhất' is one total even when the frame asks per day.
    assert ask(catalog, engine, "Cho tôi biết doanh thu 7 ngày gần nhất.", as_of="2026-03-31", metric="revenue", operation="series", group_by="day", output_type="list") == 30000
    # Question words taken for a name ('quys' = quý), written-out empty values ('none').
    assert ask(catalog, engine, "số đơn quys 1/2026 là nhiêu", metric="orders", customer_filter="quys") == 5
    assert ask(catalog, engine, "Tháng 2/2026 doanh thu là bao nhiêu?", metric="revenue", group_filter="none", unit_filter="none") == 70000
    # One item name split over two slots.
    assert ask(catalog, engine, "thang 2/2026 ban bot ngot ajinomoto 400g dc bn tien", metric="revenue", customer_filter="ban bot ngot", product_filter="ajinomoto 400g") == 30000


def test_prescription_words():
    from datamate.intent import normalize_question
    assert "không kê đơn" in normalize_question("thang 3/2026 ban hang khong can don bac si duoc bao nhieu tien")
    assert normalize_question("Ngày nào không có đơn?") == "Ngày nào không có đơn?"
