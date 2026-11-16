import json
from pathlib import Path

from datamate.core import compile_frame
from datamate.engine import QueryEngine
from datamate.ingestion import prepare_shop
from datamate.output import finalize
from datamate.semantics import attach_semantics
from datamate.types import QueryFrame, Question


def test_semantic_orders_returns_duplicates_guests_and_compiler(tmp_path):
    split = tmp_path / "split"
    shop = split / "data" / "arbitrary_shop"
    shop.mkdir(parents=True)
    (shop / "README.md").write_text("Dữ liệu có đến hết 28/02/2026.\n")
    (split / "rulebook.md").write_text("Returned orders excluded entirely; shipping included.")
    (split / "calendar.csv").write_text("date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n")
    (shop / "ban_hang.csv").write_text("Mã đơn,Ngày,Tên hàng,Số lượng,Thành tiền,Trạng thái,Khách\nA,01/01/2026,Trà,1,100,Hoàn thành,Khách lẻ\nA,01/02/2026,Trà,-1,-100,Trả hàng,Khách lẻ\nB,01/01/2026,Trà,1,20,Đang giao,Khách lẻ\nB,01/01/2026,Trà,1,20,Đang giao,Khách lẻ\nC,02/01/2026,Trà,1,30,Hoàn thành,Chị Lan\n")
    directory = prepare_shop(split, "arbitrary_shop", tmp_path / "cache")
    catalog = json.loads((directory / "catalog.json").read_text())
    engine = QueryEngine(directory / "store.duckdb", {t["name"] for t in catalog["tables"]})
    try:
        semantics = attach_semantics(engine, catalog)
        assert semantics["capabilities"]["orders"]
        assert engine.query("SELECT SUM(revenue),COUNT(*) FROM v_orders")[1] == [(50, 2)]
        assert engine.query("SELECT COUNT(DISTINCT customer_key) FROM v_orders")[1] == [(1,)]
        frame = QueryFrame(metric="revenue", operation="average_order", group_by="none", product_filter="", customer_filter="", group_filter="", customer_group_filter="", unit_filter="", payment_filter="", channel_filter="", size_filter="", k=1, threshold=0, threshold_operator=">=", weekday_filter=0, output_type="number", reason="average")
        question = Question(id="q_test", business_id="arbitrary_shop", question="Tháng 1/2026 trung bình mỗi đơn bao nhiêu tiền?", as_of_date="2026-01-20")
        plan = compile_frame(frame, question, catalog, engine)
        columns, rows = engine.query(plan.sql)
        assert finalize(plan, columns, rows) == {"type": "number", "answer": 25}
    finally:
        engine.close()


def test_blank_order_ids_carry_within_sheet_and_do_not_deduplicate_other_orders(tmp_path):
    split = tmp_path / 'split'
    shop = split / 'data' / 'example'
    shop.mkdir(parents=True)
    (shop / 'README.md').write_text('Dữ liệu có đến hết 31/01/2026.')
    (split / 'rulebook.md').write_text('Blank order cells inherit the group head.')
    (split / 'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')
    (shop / 'ban_hang.csv').write_text('Mã đơn,Ngày,Tên hàng,Số lượng,Thành tiền,Trạng thái\nA,01/01/2026,Trà,1,20,Xong\n,,Cà phê,1,40,\n,,,TỔNG NGÀY,60,\n,,Dòng không xác định đơn,1,999,\nB,02/01/2026,Trà,1,30,Đổi trả\n,,Cà phê,1,40,\nC,03/01/2026,Trà,1,50,Xong\n,,Cà phê,1,40,\n,,,TỔNG THÁNG,220,\n')
    directory = prepare_shop(split, 'example', tmp_path / 'cache')
    catalog = json.loads((directory / 'catalog.json').read_text())
    engine = QueryEngine(directory / 'store.duckdb', {t['name'] for t in catalog['tables']})
    try:
        attach_semantics(engine, catalog)
        assert engine.query('SELECT order_id,revenue FROM v_orders ORDER BY order_id')[1] == [('A', 60), ('C', 90)]
        assert engine.query('SELECT SUM(revenue) FROM v_sales')[1] == [(150,)]
    finally:
        engine.close()


def test_invoice_summary_rows_are_not_inherited_into_last_order(tmp_path):
    split = tmp_path / 'split'
    shop = split / 'data' / 'example'
    shop.mkdir(parents=True)
    (shop / 'README.md').write_text('Dữ liệu có đến hết 31/01/2026.')
    (split / 'rulebook.md').write_text('One invoice per row.')
    (split / 'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')
    (shop / 'hoa_don.csv').write_text('Mã bill,Ngày giao dịch,Tiền hàng,Tổng tiền,Bên mua,Status\nA,01/01/2026,100,100,Khách lẻ,Xong\n,,100,100,Cộng ngày 01/01/2026,\n')
    directory = prepare_shop(split, 'example', tmp_path / 'cache')
    catalog = json.loads((directory / 'catalog.json').read_text())
    engine = QueryEngine(directory / 'store.duckdb', {t['name'] for t in catalog['tables']})
    try:
        attach_semantics(engine, catalog)
        assert engine.query('SELECT SUM(revenue),COUNT(*) FROM v_orders')[1] == [(100,1)]
    finally:
        engine.close()
