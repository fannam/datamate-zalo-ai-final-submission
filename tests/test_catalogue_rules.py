"""Catalogue and stock conventions read from shop files (README §Quy ước; rulebook §3, §5, §6 Mẹ và bé, §10).

- a catalogue with a written-out conversion column ('1 thùng = 4 bịch; 1 lốc = 4 gói') is still the product master:
  groups, aliases and factors reach the sales lines;
- README 'A, B bán theo bó, 1 bó = 10 cây' gives each listed item the factor;
- a brand column headed 'Hãng' (folds like 'hàng') is the brand; diapers follow the catalogue group;
- closing stock kept in the catalogue ('Tồn cuối') answers stock questions: out of stock, below a threshold, most stock.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "training_code" / "data_generation"))
import synth_phone  # noqa: E402

from datamate.core import compile_frame  # noqa: E402
from datamate.engine import QueryEngine  # noqa: E402
from datamate.ingestion import prepare_shop  # noqa: E402
from datamate.output import finalize  # noqa: E402
from datamate.semantics import attach_semantics  # noqa: E402
from datamate.types import QueryFrame, Question  # noqa: E402

FRAME = dict(metric="other", operation="value", group_by="none", product_filter="", customer_filter="", group_filter="", customer_group_filter="", unit_filter="",
             payment_filter="", channel_filter="", size_filter="", k=1, threshold=0, threshold_operator=">=", weekday_filter=0, output_type="number", reason="t")
SALES = ["Mã đơn", "Ngày", "Tên hàng", "SL", "ĐVT", "Thành tiền"]
SALES_DESC = {"Mã đơn": "mã đơn", "Ngày": "ngày bán", "Tên hàng": "tên hàng", "SL": "số lượng", "ĐVT": "đơn vị tính", "Thành tiền": "thành tiền sau giảm"}
LINES = [("D1", "02/03/2026", "Tã quần Bobby size L 44 miếng", 1, "thùng", 800000), ("D2", "03/03/2026", "Tã quần Bobby size L 44 miếng", 2, "bịch", 400000),
         ("D3", "04/03/2026", "Sữa Nan Optipro số 2 800g", 3, "lon", 1500000), ("D4", "05/03/2026", "Tã chéo vải xô gói 10 cái", 1, "gói", 50000),
         ("D5", "06/03/2026", "Khăn ướt Bobby 100 tờ", 1, "lốc", 120000)]


def build(tmp, files, extra_readme=""):
    split = tmp / "split"
    folder = split / "data" / "shop"
    folder.mkdir(parents=True)
    shutil.copy(synth_phone.ROOT / "train" / "calendar.csv", split / "calendar.csv")
    (split / "rulebook.md").write_text("rulebook")
    described = {}
    for name, (header, desc, rows) in files.items():
        synth_phone.write_csv(folder / name, header, rows)
        described[name] = (header, desc)
    (folder / "README.md").write_text(synth_phone.readme(described).replace("20/03/2026", "31/03/2026") + extra_readme)
    directory = prepare_shop(split, "shop", tmp / "cache")
    catalog = json.loads((directory / "catalog.json").read_text())
    engine = QueryEngine(directory / "store.duckdb", {t["name"] for t in catalog["tables"]})
    attach_semantics(engine, catalog)
    return catalog, engine


def ask(catalog, engine, text, **frame):
    plan = compile_frame(QueryFrame(**(FRAME | frame)), Question(id="x", business_id="shop", question=text, as_of_date="2026-03-31"), catalog, engine)
    return finalize(plan, *engine.query(plan.sql))["answer"]


def sales_rows():
    return [dict(zip(SALES, line)) for line in LINES]


def test_catalogue_with_written_conversions_is_the_product_master(tmp_path):
    catalogue = ["Tên hàng", "Tên gọi khác", "Nhóm", "Hãng", "Số / Size", "ĐVT", "Quy đổi"]
    desc = {"Tên hàng": "tên hàng", "Tên gọi khác": "tên gọi khác hay dùng, nhiều cách cách nhau dấu phẩy", "Nhóm": "nhóm hàng", "Hãng": "hãng",
            "Số / Size": "số của sữa hoặc size bỉm", "ĐVT": "đơn vị cơ bản", "Quy đổi": 'quy đổi đơn vị lớn ra ĐVT, vd "1 thùng = 4 bịch"'}
    items = [{"Tên hàng": "Tã quần Bobby size L 44 miếng", "Tên gọi khác": "bỉm bobby L", "Nhóm": "bỉm tã", "Hãng": "Bobby", "Số / Size": "Size L", "ĐVT": "bịch", "Quy đổi": "1 thùng = 4 bịch"},
             {"Tên hàng": "Sữa Nan Optipro số 2 800g", "Tên gọi khác": "nan 2, nan số 2", "Nhóm": "sữa bột", "Hãng": "Nan", "Số / Size": "Số 2", "ĐVT": "lon", "Quy đổi": ""},
             {"Tên hàng": "Tã chéo vải xô gói 10 cái", "Tên gọi khác": "", "Nhóm": "quần áo sơ sinh", "Hãng": "", "Số / Size": "", "ĐVT": "gói", "Quy đổi": ""},
             {"Tên hàng": "Khăn ướt Bobby 100 tờ", "Tên gọi khác": "", "Nhóm": "khăn ướt", "Hãng": "Bobby", "Số / Size": "", "ĐVT": "gói", "Quy đổi": "1 lốc = 4 gói; 1 thùng = 24 gói"}]
    catalog, engine = build(tmp_path, {"ban_hang.csv": (SALES, SALES_DESC, sales_rows()), "danh_muc.csv": (catalogue, desc, items)})
    try:
        con = engine.connection
        assert con.execute("SELECT COUNT(*), COUNT(product_alias), COUNT(product_group) FROM v_products").fetchone() == (4, 2, 4)
        quantities = dict(con.execute("SELECT order_id, base_quantity FROM v_sales").fetchall())
        assert (quantities["D1"], quantities["D2"], quantities["D5"]) == (4, 2, 4)          # 1 thùng = 4 bịch, 1 lốc = 4 gói
        assert ask(catalog, engine, "Tháng 3/2026 nhóm bỉm tã bán được bao nhiêu bịch?", metric="quantity", group_filter="bỉm tã") == 6
        assert ask(catalog, engine, "Tháng 3/2026 bán được bao nhiêu lon nan 2?", metric="quantity", product_filter="nan 2") == 3   # alias
        brands = dict(con.execute("SELECT product_name, brand FROM v_product_attrs").fetchall())
        assert brands["Tã quần Bobby size L 44 miếng"] == "Bobby"                               # column headed 'Hãng'
        diapers = {row[0] for row in con.execute("SELECT product_name FROM v_product_attrs WHERE is_diaper").fetchall()}
        assert diapers == {"Tã quần Bobby size L 44 miếng"}                                     # cloth 'Tã chéo' is in another group
    finally:
        engine.close()


def test_readme_listed_items_sold_by_bundle(tmp_path):
    rows = [{"Mã đơn": "D1", "Ngày": "02/03/2026", "Tên hàng": "Ống PVC D32 cây 4m", "SL": 2, "ĐVT": "bó", "Thành tiền": 1000000},
            {"Mã đơn": "D2", "Ngày": "03/03/2026", "Tên hàng": "Ống PVC D50 cây 4m", "SL": 3, "ĐVT": "cây", "Thành tiền": 300000}]
    note = "\n## Quy ước\n- **Hàng theo bó**: Ống PVC D32 cây 4m, Ống PVC D50 cây 4m bán theo bó, 1 bó = 10 cây; khách lấy nguyên bó thì sổ ghi số bó.\n"
    catalog, engine = build(tmp_path, {"ban_hang.csv": (SALES, SALES_DESC, rows)}, note)
    try:
        assert dict(engine.connection.execute("SELECT order_id, base_quantity FROM v_sales").fetchall()) == {"D1": 20, "D2": 3}
    finally:
        engine.close()


@pytest.mark.parametrize("question,expected", [
    ("Tính đến hôm nay có bao nhiêu mặt hàng hết hàng?", 1),
    ("Những mặt hàng nào đã hết hàng tính đến hôm nay?", ["Nước suối 500ml"]),
    ("Có bao nhiêu mặt hàng tồn kho dưới 10 tính đến hôm nay?", 2),
    ("Mặt hàng nào tồn kho nhiều nhất hôm nay?", "Bia Tiger lon"),
])
def test_closing_stock_kept_in_the_catalogue(tmp_path, question, expected):
    catalogue = ["Mã hàng", "Tên hàng", "Nhóm", "ĐVT", "Giá vốn", "Tồn cuối"]
    desc = {"Mã hàng": "mã hàng", "Tên hàng": "tên hàng", "Nhóm": "nhóm hàng", "ĐVT": "đơn vị cơ bản", "Giá vốn": "giá vốn", "Tồn cuối": "tồn kho (đơn vị cơ bản)"}
    items = [{"Mã hàng": "SP01", "Tên hàng": "Bia Tiger lon", "Nhóm": "bia", "ĐVT": "lon", "Giá vốn": 15000, "Tồn cuối": 150},
             {"Mã hàng": "SP02", "Tên hàng": "Nước suối 500ml", "Nhóm": "nước", "ĐVT": "chai", "Giá vốn": 4000, "Tồn cuối": 0},
             {"Mã hàng": "SP03", "Tên hàng": "Coca-Cola lon", "Nhóm": "nước", "ĐVT": "lon", "Giá vốn": 8000, "Tồn cuối": 7}]
    rows = [{"Mã đơn": "D1", "Ngày": "02/03/2026", "Tên hàng": "Bia Tiger lon", "SL": 2, "ĐVT": "lon", "Thành tiền": 40000}]
    catalog, engine = build(tmp_path, {"ban_hang.csv": (SALES, SALES_DESC, rows), "danh_muc_hang.csv": (catalogue, desc, items)})
    try:
        assert ask(catalog, engine, question) == expected
    finally:
        engine.close()


@pytest.mark.parametrize("question,frame,expected", [
    ("Tháng 3/2026 doanh thu bán chịu là bao nhiêu?", {"metric": "revenue", "payment_filter": "debt"}, 1100000),  # credit sales = orders in the terms book; D5 is not
    ("Có bao nhiêu đơn bán chịu có ngày phải trả trong tháng 4/2026?", {"metric": "orders"}, 1),
    ("Tính đến hôm nay có bao nhiêu đơn bán nợ đã quá hạn thanh toán?", {"metric": "orders"}, 1),                  # due before today
    ("Hôm nay có bao nhiêu đơn đến hạn thanh toán?", {"metric": "orders"}, 1),                                   # due today: not overdue
    ("Tháng 3/2026 có bao nhiêu đơn bán chịu kỳ hạn 60 ngày?", {"metric": "orders"}, 1),
    ("Có bao nhiêu đơn phải trả trong tháng 5/2026?", {"metric": "orders"}, 1),                                   # a period after today is a due period
])
def test_payment_terms_book(tmp_path, question, frame, expected):
    rows = [{"Mã đơn": "D1", "Ngày": "01/03/2026", "Tên hàng": "Xi măng", "SL": 1, "ĐVT": "bao", "Thành tiền": 100000},
            {"Mã đơn": "D2", "Ngày": "01/03/2026", "Tên hàng": "Xi măng", "SL": 2, "ĐVT": "bao", "Thành tiền": 200000},
            {"Mã đơn": "D3", "Ngày": "02/03/2026", "Tên hàng": "Cát", "SL": 1, "ĐVT": "m3", "Thành tiền": 400000},
            {"Mã đơn": "D4", "Ngày": "05/03/2026", "Tên hàng": "Cát", "SL": 1, "ĐVT": "m3", "Thành tiền": 400000},
            {"Mã đơn": "D5", "Ngày": "06/03/2026", "Tên hàng": "Cát", "SL": 1, "ĐVT": "m3", "Thành tiền": 50000}]
    terms = ["Mã đơn hàng", "Kỳ hạn", "Ngày phải trả"]
    desc = {"Mã đơn hàng": "mã đơn", "Kỳ hạn": "số ngày được nợ", "Ngày phải trả": "hạn thanh toán"}
    book = [{"Mã đơn hàng": "D1", "Kỳ hạn": 30, "Ngày phải trả": "31/03/2026"},      # due on the day asked: not overdue
            {"Mã đơn hàng": "D3", "Kỳ hạn": 30, "Ngày phải trả": "01/04/2026"},
            {"Mã đơn hàng": "D4", "Kỳ hạn": 60, "Ngày phải trả": "04/05/2026"},
            {"Mã đơn hàng": "D2", "Kỳ hạn": 25, "Ngày phải trả": "26/03/2026"}]
    catalog, engine = build(tmp_path, {"so_ban_hang.csv": (SALES, SALES_DESC, rows), "so_han_no.csv": (terms, desc, book)})
    try:
        assert ask(catalog, engine, question, **frame) == expected
    finally:
        engine.close()


@pytest.mark.parametrize("question,expected", [
    ("Tháng 3/2026 quán phục vụ bao nhiêu lượt khách?", 9),                 # guests of valid orders (D3 cancelled)
    ("Tháng 3/2026 bàn nào có nhiều đơn nhất?", "B2"),
    ("Tháng 3/2026 bàn nào có nhiều khách nhất?", "B1"),
    ("Tháng 3/2026 trung bình mỗi bàn có mấy khách?", 3),                   # dine-in valid orders: 6, 2, 1 -> 3
    ("Tháng 3/2026 ngày nào đông khách nhất?", "2026-03-01"),
])
def test_tables_and_guests(tmp_path, question, expected):
    header = SALES + ["Bàn", "Số khách", "Trạng thái"]
    desc = SALES_DESC | {"Bàn": "bàn", "Số khách": "số khách của đơn", "Trạng thái": "trạng thái đơn"}
    rows = [("D1", "01/03/2026", "Lẩu", 1, "nồi", 300000, "B1", 6, "Xong"), ("D2", "02/03/2026", "Cơm", 2, "đĩa", 80000, "B2", 2, "Xong"),
            ("D2", "02/03/2026", "Trà đá", 2, "ly", 10000, "B2", 2, "Xong"), ("D3", "02/03/2026", "Lẩu", 1, "nồi", 300000, "B1", 8, "Đã hủy"),
            ("D4", "03/03/2026", "Cơm", 1, "đĩa", 40000, "B2", 1, "Xong"), ("D5", "03/03/2026", "Cơm", 1, "đĩa", 40000, "", 0, "Xong")]
    catalog, engine = build(tmp_path, {"chi_tiet_hoa_don.csv": (header, desc, [dict(zip(header, r)) for r in rows])})
    try:
        assert ask(catalog, engine, question) == expected
    finally:
        engine.close()


@pytest.mark.parametrize("note,expected", [("ghi nợ 1.199.500đ, đã trả đủ", 0), ("nợ 2118k, khách ck đủ rồi", 0), ("ghi sổ, khách trả hết rồi", 0),
                                           ("ck 913 nghìn, thiếu lại 456.500đ", 456500), ("khách đưa trước 300k, còn thiếu 200k", 200000), ("trả 500k, nợ 300k", 300000),
                                           ("nợ 1tr267", 1267000), ("nợ cả đơn", 1000000), ("đưa trước 200k, còn lại ghi sổ", 800000), ("chưa trả, ghi nợ 1tr645", 1645000)])
def test_debt_note_balance(note, expected):
    from datamate.specialty import note_balance
    assert note_balance(note, 1000000.0) == expected


def test_debt_from_notes_at_the_last_data_day(tmp_path):
    header = SALES + ["Thanh toán bằng", "Tong tien", "Note", "Khách"]
    desc = SALES_DESC | {"Thanh toán bằng": "hình thức thanh toán của đơn: cash tiền mặt, debt ghi nợ", "Tong tien": "khách cần trả cho cả đơn",
                         "Note": "ghi chú của đơn: đơn ghi nợ thì ghi khách đã đưa / còn thiếu bao nhiêu", "Khách": "tên khách"}
    rows = [("D1", "02/03/2026", "Lẩu", 1, "nồi", 300000, "debt", 300000, "nợ 300k, đã trả đủ", "Lan Anh"),
            ("D2", "03/03/2026", "Lẩu", 2, "nồi", 600000, "debt", 600000, "ck 400k, thiếu lại 200k", "Minh Tuấn"),
            ("D3", "04/03/2026", "Cơm", 1, "đĩa", 50000, "cash", 50000, "", "Lan Anh")]
    catalog, engine = build(tmp_path, {"chi_tiet_hoa_don.csv": (header, desc, [dict(zip(header, r)) for r in rows])})
    try:
        assert ask(catalog, engine, "Tính đến hôm nay tổng tiền khách còn nợ là bao nhiêu?") == 200000      # as_of is the last data day
        assert ask(catalog, engine, "Tính đến hôm nay khách nào nợ nhiều nhất?") == "Minh Tuấn"
    finally:
        engine.close()


@pytest.mark.parametrize("question,frame,expected", [
    ("Tháng 3/2026 doanh thu phụ kiện là bao nhiêu?", dict(metric="revenue", product_filter="phụ kiện"), 300000),
    ("Tháng 3/2026 bán được bao nhiêu máy?", dict(metric="quantity", product_filter="máy"), 3),               # máy mới + máy cũ
    ("Doanh thu điện thoại cũ tháng 3/2026?", dict(metric="revenue", product_filter="điện thoại cũ"), 6000000),
    ("Tháng 3/2026 bán bao nhiêu đt cũ?", dict(metric="quantity", group_filter="đt cũ"), 1),
    ("Doanh thu đt tháng 3/2026?", dict(metric="revenue", product_filter="đt"), 46000000),
])
def test_abbreviation_sheet_type_is_the_product_group(tmp_path, question, frame, expected):
    """README 'sheet Ghi tắt cho tên đầy đủ và loại hàng (máy mới, máy cũ, phụ kiện)': 'Loại' is the product group;
    'máy' (and 'điện thoại', 'đt' in a shop whose groups are 'máy …') names every 'máy …' group."""
    rows = [{"Mã đơn": "D1", "Ngày": "02/03/2026", "Tên hàng": "IP13 128", "SL": 2, "ĐVT": "cái", "Thành tiền": 40000000},
            {"Mã đơn": "D2", "Ngày": "03/03/2026", "Tên hàng": "IP11 cũ", "SL": 1, "ĐVT": "cái", "Thành tiền": 6000000},
            {"Mã đơn": "D3", "Ngày": "04/03/2026", "Tên hàng": "Cáp C", "SL": 3, "ĐVT": "cái", "Thành tiền": 300000}]
    short = ["Ghi tắt", "Tên hàng", "Loại"]
    desc = {"Ghi tắt": "cách ghi tắt trong sổ bán", "Tên hàng": "tên đầy đủ", "Loại": "máy mới, máy cũ hoặc phụ kiện"}   # no column words, as in shop READMEs
    items = [{"Ghi tắt": "IP13 128", "Tên hàng": "iPhone 13 128GB", "Loại": "máy mới"},
             {"Ghi tắt": "IP11 cũ", "Tên hàng": "iPhone 11 64GB (cũ)", "Loại": "máy cũ"},
             {"Ghi tắt": "Cáp C", "Tên hàng": "Cáp Type-C 1m", "Loại": "phụ kiện"}]
    catalog, engine = build(tmp_path, {"ban_hang.csv": (SALES, SALES_DESC, rows), "ghi_tat.csv": (short, desc, items)})
    try:
        groups = dict(engine.connection.execute("SELECT order_id, product_group FROM v_sales").fetchall())
        assert groups == {"D1": "máy mới", "D2": "máy cũ", "D3": "phụ kiện"}
        assert ask(catalog, engine, question, **frame) == expected
    finally:
        engine.close()


def test_group_head_word_needs_several_groups_and_no_item(tmp_path):
    """A first word shared by several groups is all of them only when no item is so named: 'sữa' names the item
    'Sữa tươi Vinamilk 1L' when one exists, and one group starting with the word stays the single-group rule."""
    rows = [{"Mã đơn": "D1", "Ngày": "02/03/2026", "Tên hàng": "Sữa tươi Vinamilk 1L", "SL": 2, "ĐVT": "hộp", "Thành tiền": 60000},
            {"Mã đơn": "D2", "Ngày": "03/03/2026", "Tên hàng": "Sữa bột Nan 800g", "SL": 1, "ĐVT": "lon", "Thành tiền": 500000},
            {"Mã đơn": "D3", "Ngày": "04/03/2026", "Tên hàng": "Bánh quy Cosy", "SL": 3, "ĐVT": "gói", "Thành tiền": 90000}]
    catalogue = ["Tên hàng", "Nhóm", "ĐVT"]
    desc = {"Tên hàng": "tên hàng", "Nhóm": "nhóm hàng", "ĐVT": "đơn vị cơ bản"}
    items = [{"Tên hàng": "Sữa tươi Vinamilk 1L", "Nhóm": "sữa tươi", "ĐVT": "hộp"}, {"Tên hàng": "Sữa bột Nan 800g", "Nhóm": "sữa bột", "ĐVT": "lon"},
             {"Tên hàng": "Bánh quy Cosy", "Nhóm": "bánh kẹo", "ĐVT": "gói"}]
    catalog, engine = build(tmp_path, {"ban_hang.csv": (SALES, SALES_DESC, rows), "danh_muc.csv": (catalogue, desc, items)})
    try:
        assert ask(catalog, engine, "Tháng 3/2026 doanh thu nhóm bánh?", metric="revenue", group_filter="bánh") == 90000   # one group: prefix rule
        assert ask(catalog, engine, "Tháng 3/2026 doanh thu nhóm sữa?", metric="revenue", group_filter="sữa") == 560000    # two groups
    finally:
        engine.close()


@pytest.mark.parametrize("question,frame,expected", [
    ("Tháng 3/2026 có bao nhiêu đơn được giao từ 2 đợt trở lên?", dict(metric="orders", threshold=2), 2),   # D3's third batch is after today
    ("Tháng 3/2026 có bao nhiêu đơn giao nhiều đợt?", dict(metric="orders"), 2),
    ("Tháng 3/2026 bao nhiêu đơn giao đúng 1 lượt?", dict(metric="orders"), 1),
    ("Tháng 3/2026 có bao nhiêu lượt giao hàng?", dict(metric="orders"), 5),                           # 'Lượt giao' is the batch
])
def test_delivery_batches(tmp_path, question, frame, expected):
    """Rulebook 'Giao nhiều đợt' (up to today; each batch of each order counts once) and README '1–3 đợt (Lượt giao)':
    orders of a period are counted by order date (§1) by their batches delivered up to today."""
    rows = [{"Mã đơn": order, "Ngày": day, "Tên hàng": "Xi măng Hà Tiên bao 50kg", "SL": 10, "ĐVT": "bao", "Thành tiền": 900000}
            for order, day in (("D1", "02/03/2026"), ("D2", "05/03/2026"), ("D3", "10/03/2026"), ("D4", "30/03/2026"))]
    header = ["Số đơn", "Lượt giao", "Thời gian giao", "Hàng hóa", "Số lượng giao"]
    desc = {"Số đơn": "mã đơn ở sổ bán", "Lượt giao": "đợt giao thứ mấy của đơn", "Thời gian giao": "ngày giao", "Hàng hóa": "tên hàng", "Số lượng giao": "số lượng giao, đơn vị cơ bản"}
    batches = [("D1", 1, "03/03/2026"), ("D1", 2, "06/03/2026"), ("D2", 1, "06/03/2026"), ("D3", 1, "11/03/2026"), ("D3", 2, "20/03/2026"),
               ("D3", 3, "02/04/2026"), ("D4", 1, "01/04/2026"), ("D4", 2, "02/04/2026")]
    deliveries = [{"Số đơn": o, "Lượt giao": b, "Thời gian giao": day, "Hàng hóa": "Xi măng Hà Tiên bao 50kg", "Số lượng giao": 5} for o, b, day in batches]
    catalog, engine = build(tmp_path, {"ban_hang.csv": (SALES, SALES_DESC, rows), "so_giao_hang.csv": (header, desc, deliveries)})
    try:
        assert "v_deliveries" in engine.tables
        assert ask(catalog, engine, question, **frame) == expected
    finally:
        engine.close()


def test_repair_job_shorthands_are_one_job():
    """Rulebook §8: names of one thing are one thing. Shorthands of a repair job (never on the same ticket) count as that job."""
    from datamate.domains import job_key, question_job
    same = [("BD", "bảo dưỡng", "bdtq", "bảo dưỡng xe"), ("bố thắng trước", "má phanh trước", "bố trước"), ("vá ruột", "vá", "vá xe"),
            ("nsd", "bộ nhông xích", "nhông sên dĩa"), ("cân sên", "tăng sên", "chỉnh sên"), ("vs nồi", "làm nồi", "vệ sinh nồi"),
            ("rửa kim phun", "vs kim phun"), ("nước mát", "nước làm mát"), ("curoa", "dây cu roa"), ("vỏ Casumina", "vỏ xe", "vỏ"),
            ("láp", "nhớt láp"), ("bu gi", "bugi"), ("rửa", "rửa xe")]   # book items; a folded 'rua' in a question is not read as a job
    for names in same:
        assert len({job_key(name) for name in names}) == 1, names
    assert job_key("thay ruột") != job_key("vá ruột")                 # 'ruột' alone is a new tube, not a patch
    assert job_key("nhớt") == "nhot may" and job_key("nhớt láp") == "nhot lap"
    assert job_key("má phanh trước") != job_key("má phanh sau")
    keys = {"ma phanh truoc", "ma phanh sau", "ma phanh", "bao duong", "va xe", "nhot may"}
    assert question_job("tháng 7 bao nhiêu lượt thay ma phanh", keys) == "ma phanh%"    # front, rear and unspecified
    assert question_job("tháng 7 bao nhiêu lượt thay bo thang truoc", keys) == "ma phanh truoc"
    assert question_job("tháng 7 bao nhiêu lượt bd", keys) == "bao duong"
    assert question_job("tháng 7 bao nhiêu lượt va ruot", keys) == "va xe"
