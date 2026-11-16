import csv
import json

from openpyxl import Workbook

from datamate.engine import QueryEngine
from datamate.ingestion import prepare_shop, column_kind
import pytest


def test_headers_month_sheets_provenance_and_duplicate_flags(tmp_path):
    split = tmp_path / "split"
    shop = split / "data" / "example"
    shop.mkdir(parents=True)
    (split / "rulebook.md").write_text("Each line without order id is an event.")
    (split / "calendar.csv").write_text("date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n")
    (shop / "README.md").write_text("Quy đổi:\n  - Bia: 1 thùng = 24 lon\n")
    workbook = Workbook()
    first = workbook.active
    first.title = "01.2026"
    first.append(["SỔ BÁN HÀNG"])
    first.append([])
    first.append(["Ngày", "Tên hàng", "Số lượng", "Thành tiền"])
    first.append(["1/1", "Bia", "1 thùng", "240k"])
    first.append(["1/1", "Bia", "1 thùng", "240k"])
    second = workbook.create_sheet("T2_2026")
    second.append(["Ngày", "Tên hàng", "Số lượng", "Thành tiền"])
    second.append(["1/2", "Bia", 2, 20000])
    workbook.save(shop / "SoBanHang.xlsx")
    directory = prepare_shop(split, "example", tmp_path / "cache")
    catalog = json.loads((directory / "catalog.json").read_text())
    assert len(catalog["tables"]) == 1
    info = catalog["tables"][0]
    assert info["rows"] == 3
    assert len(info["sources"]) == 2
    engine = QueryEngine(directory / "store.duckdb", {info["name"]})
    try:
        rows = engine.query(f"SELECT SUM(thanh_tien), COUNT(*), SUM(CAST(_exact_duplicate AS INTEGER)) FROM {info['name']}")[1]
        # Both event rows are retained; callers only deduplicate where the rule allows it.
        assert rows[0] == (500000, 3, 1)
        assert engine.query("SELECT factor FROM unit_conversions")[1][0][0] == 24
        assert engine.query(f"SELECT MIN(CAST(ngay AS DATE)) FROM {info['name']}")[1][0][0].isoformat() == "2026-01-01"
    finally:
        engine.close()
    assert prepare_shop(split, "example", tmp_path / "cache") == directory


@pytest.mark.parametrize('name,description,values',[
    ('Tiến độ','trạng thái đơn',['Xong','Đã hủy']),
    ('CachHoanTien','',['cash','transfer']),
    ('Phương thức thanh toán','',['Tiền mặt','COD']),
])
def test_categorical_money_words_are_not_numeric(name,description,values):
    assert column_kind(name,description,values)=='text'


def test_unparseable_numeric_column_is_recovered_without_erasing_source(tmp_path):
    import duckdb
    from datamate.quality import inspect_and_recover
    con=duckdb.connect()
    con.execute("CREATE TABLE x(gia VARCHAR, gia__raw VARCHAR)")
    con.execute("INSERT INTO x VALUES (NULL,'cash'),(NULL,'transfer')")
    catalog={'tables':[{'name':'x','rows':2,'columns':[{'name':'gia','kind':'number','nonempty':2,'parse_errors':2}]}]}
    report=inspect_and_recover(con,catalog)
    assert con.execute('SELECT gia FROM x').fetchall()==[('cash',),('transfer',)]
    assert catalog['tables'][0]['columns'][0]['kind']=='text'
    assert report['transformations'][0]['action']=='restore_original_text'
    con.close()


def test_text_cells_typed_by_values_when_header_gives_no_hint():
    from datamate.ingestion import column_kind
    assert column_kind('Hạn kỳ', '', ['05/08/2026', '05/09/2026', '05/10/2026']) == 'datetime'
    assert column_kind('Phải đóng', '', ['1000000', '1.200.000', '950000']) == 'number'
    assert column_kind('Liên hệ', '', ['0901234567', '0912345678', '0987654321']) == 'text'   # leading zeros: codes/phones
    assert column_kind('Phiếu', '', [str(1000 + i) for i in range(12)]) == 'text'              # consecutive distinct integers: identifiers
    assert column_kind('Kỳ', '', ['1', '2']) == 'text'                                         # too few cells to decide
    assert column_kind('Ghi', '', ['12/08/2026', 'Tổng tháng', '13/08/2026']) == 'text'


def test_csv_title_rows_semicolons_and_utf16(tmp_path):
    from datamate.ingestion import sources
    folder = tmp_path / 'shop'
    folder.mkdir()
    (folder / 'a.csv').write_text('SỔ BÁN HÀNG THÁNG 8\n\nMã đơn;Ngày bán;Thành tiền\nA;01/08/2026;"35,5"\nB;02/08/2026;10\n')
    (folder / 'b.csv').write_bytes('Mã đơn\tNgày bán\nC\t03/08/2026\n'.encode('utf-16'))
    (folder / 'c.csv').write_text('Tien hang,TongTien,Mã bill\n"35,000","35,000",ORD1\n')
    got = {name: (header, [row for _, row in rows]) for name, _, header, rows in sources(folder)}
    assert got['a.csv'] == (['Mã đơn', 'Ngày bán', 'Thành tiền'], [['A', '01/08/2026', '35,5'], ['B', '02/08/2026', '10']])
    assert got['b.csv'] == (['Mã đơn', 'Ngày bán'], [['C', '03/08/2026']])
    assert got['c.csv'] == (['Tien hang', 'TongTien', 'Mã bill'], [['35,000', '35,000', 'ORD1']])
