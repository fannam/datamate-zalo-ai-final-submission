"""Filter values in the wrong slot, or chat words taken for a name (rulebook §8 'Tên gọi, nhãn')."""
import duckdb
import pytest

from datamate.core import UnsupportedFrame, compile_frame
from datamate.engine import QueryEngine
from datamate.output import finalize
from datamate.types import QueryFrame, Question

ROWS = [  # order, date, revenue, item, item group, customer, customer group
    ("o1", "2026-01-05", 100, "Nghêu hấp sả", "hải sản", "Lan", "sỉ"),
    ("o2", "2026-01-06", 200, "Siro Tiffy chai 30ml", "ho cảm cúm", "Minh", "lẻ"),
    ("o3", "2026-01-07", 400, "Sữa tươi Vinamilk 180ml", "sữa", "Lan", "sỉ"),
    ("o4", "2026-01-08", 800, "Viên dầu cá Omega 3", "thực phẩm chức năng", "Hà", "lẻ"),
]
TOTAL = 1500


@pytest.fixture()
def engine(tmp_path):
    path = tmp_path / "shop.duckdb"
    con = duckdb.connect(str(path))
    columns = ("order_id VARCHAR, order_date TIMESTAMP, revenue INTEGER, product_name VARCHAR, product_group VARCHAR, customer_name VARCHAR, "
               "customer_group VARCHAR, customer_key VARCHAR, product_id VARCHAR, base_unit VARCHAR, base_quantity INTEGER, payment_method VARCHAR, channel VARCHAR")
    for view in ("v_sales", "v_orders"):
        con.execute(f"CREATE TABLE {view}({columns})")
        con.executemany(f"INSERT INTO {view} VALUES (?,?,?,?,?,?,?,?,NULL,'cái',1,'cash','store')", [row + (row[5].lower(),) for row in ROWS])
    con.close()
    engine = QueryEngine(path, {"v_sales", "v_orders"})
    yield engine
    engine.close()


CATALOG = {"readme": "Dữ liệu có đến hết 31/01/2026.", "semantic": {"capabilities": {"revenue": True, "orders": True, "sales": True, "customers": True}}}


def answer(engine, question, **filters):
    frame = QueryFrame(**(dict(metric="revenue", operation="value", group_by="none", product_filter="", customer_filter="", group_filter="",
                               customer_group_filter="", unit_filter="", payment_filter="", channel_filter="", size_filter="", k=1, threshold=0,
                               threshold_operator=">=", weekday_filter=0, output_type="number", reason="t") | filters))
    plan = compile_frame(frame, Question(id="t", business_id="s", question=question, as_of_date="2026-01-31"), CATALOG, engine)
    if plan.action == "unanswerable":
        return "unanswerable"
    return finalize(plan, *engine.query(plan.sql))["answer"]


@pytest.mark.parametrize("question,filters,expected", [
    ("Tháng 1/2026 chnhang bán được bao nhiêu tiền?", {"product_filter": "chnhang"}, TOTAL),            # 'cửa hàng' typed badly
    ("Tháng 1/2026 chương hàng bán được bao nhiêu tiền?", {"product_filter": "chương hàng"}, TOTAL),
    ("Tháng 1/2026 banf dewc bao nhiêu tiền?", {"product_filter": "dewc"}, TOTAL),                      # Telex 'được'
    ("Tháng 1/2026 nhóm ho cảm cúm bán bao nhiêu?", {"product_filter": "ho cảm cúm"}, 200),            # a product group in the item slot
    ("Tháng 1/2026 nhóm sỉ mua bao nhiêu tiền?", {"product_filter": "nhóm sỉ"}, 500),                   # a customer group in the item slot
    ("Tháng 1/2026 nhóm sữa bán bao nhiêu?", {"group_filter": "nhóm", "product_filter": "sữa"}, 400),   # 'nhóm sữa' split across slots
    ("Tháng 1/2026 khách nhóm sỉ mua bao nhiêu?", {"product_filter": "si", "group_filter": "nhom", "customer_group_filter": "si"}, 500),
    ("Tháng 1/2026 nhóm thực phẩm bán bao nhiêu?", {"group_filter": "thực phẩm"}, 800),                # first words of one group
    ("Tháng 1/2026 nghêu hấp sả thu về bn tiền?", {"product_filter": "nghêu hấp sả thu về bn tiền"}, 100),  # a name plus the question's words
    ("Tháng 1/2026 3 mặt hàng nào bán nhiều tiền nhất?", {"product_filter": "3 mặt hàng"}, TOTAL),
    ("Tháng 1/2026 doanh thu nhóm hàng sữa là bao nhiêu?", {}, 400),                                   # group named in the text, slot left empty
    ("Tháng 1/2026 doanh thu nhóm hàng hải sản là bao nhiêu?", {"product_filter": "hải sản"}, 100),    # group named in the text, put in the item slot
])
def test_filter_values_are_reslotted_or_dropped(engine, question, filters, expected):
    assert answer(engine, question, **filters) == expected


@pytest.mark.parametrize("question,filters", [
    ("Tháng 1/2026 xyzabc bán bao nhiêu sữa tươi vinamilk 180ml?", {"product_filter": "xyzabc"}),  # the question names an item: not chat text
    ("Tháng 1/2026 trả vì lỗi và vì mua nhầm bên nào nhiều hơn?", {"product_filter": "lỗi, mua nhầm"}),  # several things named
    ("Tháng 1/2026 bán được bao nhiêu nghêu hấp?", {"product_filter": "nghêu chiên"}),  # shares a word with a name: still a name
])
def test_names_are_not_dropped(engine, question, filters):
    # Never the shop total: either not compiled, or 'not recorded' (§7) when the shop has no such column.
    try:
        result = answer(engine, question, **filters)
    except UnsupportedFrame:
        return
    assert result is None or result == "unanswerable", result
