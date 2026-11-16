"""Domain rules (§6 of the released rulebook) on small hand-computed shops."""
import json

import pytest

from datamate.core import compile_frame
from datamate.engine import QueryEngine
from datamate.ingestion import prepare_shop
from datamate.output import finalize
from datamate.semantics import attach_semantics
from datamate.types import QueryFrame, Question

BASE = dict(metric='other', operation='other', group_by='none', product_filter='', customer_filter='', group_filter='', customer_group_filter='', unit_filter='',
            payment_filter='', channel_filter='', size_filter='', k=1, threshold=0, threshold_operator='>=', weekday_filter=0, output_type='number', reason='domain')


def shop(tmp_path, name, files):
    split = tmp_path / 'split'
    folder = split / 'data' / name
    folder.mkdir(parents=True)
    (split / 'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')
    (split / 'rulebook.md').write_text('Domain rules.')
    for filename, content in files.items():
        (folder / filename).write_text(content)
    directory = prepare_shop(split, name, tmp_path / 'cache')
    catalog = json.loads((directory / 'catalog.json').read_text())
    engine = QueryEngine(directory / 'store.duckdb', {t['name'] for t in catalog['tables']})
    report = attach_semantics(engine, catalog)
    return catalog, engine, report


def ask(catalog, engine, text, today, **frame):
    plan = compile_frame(QueryFrame(**(BASE | frame)), Question(id='x', business_id='b', question=text, as_of_date=today), catalog, engine)
    if plan.action != 'query':
        return {'type': plan.action}
    return finalize(plan, *engine.query(plan.sql))


REPAIR = {
    'README.md': 'Sổ sửa xe của tiệm. Dữ liệu có đến hết 31/08/2026.\n',
    'so_sua_xe.csv': (
        'Ngày,Biển số,Dòng xe,Làm gì,Thợ,Tiền công,Tiền phụ tùng,Tổng tiền,Ghi chú\n'
        '01/08/2026,59A1-123.45,Air Blade,"Thay nhớt, vá ruột",Tuấn,50000,120000,170000,\n'
        ',59A1 12345,AB,Nhớt láp,Hùng,20000,80000,100000,\n'
        '02/08/2026,51F-888.88,Vision,"Thay ruột, sửa điện",Tuấn,100000,90000,190000,\n'
        '02/08/2026,51F-888.88,vision,Bảo hành sửa điện,Tuấn,0,0,0,Bảo hành\n'
        '03/08/2026,29B-555.55,Exciter 150,"Thay bóng đèn, thay ắc quy",Hùng,30000,400000,430000,bảo hành 6 tháng\n'
        '03/08/2026,59A1-12345,Air blade,Nhớt,Hùng,20000,90000,110000,\n'
        '05/08/2026,29B-55555,Ex 150,Bảo hành thay nhớt,Tuấn,0,0,0,BH\n'
        '06/07/2026,51F-888.88,Vision,Thay nhớt,Tuấn,20000,90000,110000,\n'),
}


def test_repair_book_rules(tmp_path):
    catalog, engine, report = shop(tmp_path, 'repair', REPAIR)
    try:
        assert report['capabilities'].get('tickets'), report['warnings']
        today = '2026-08-31'
        cases = [
            ('Tháng 8/2026 tiệm có bao nhiêu lượt xe?', 7),                          # every row, warranty included
            ('Tháng 8/2026 có bao nhiêu chiếc xe khác nhau mang tới tiệm?', 3),        # distinct plates, spelling variants merged
            ('Tỉ lệ bảo hành tháng 8/2026 là bao nhiêu?', 40),                         # 2 warranty / 5 paid ('bảo hành 6 tháng' is a term)
            ('Tháng 8/2026 tiệm thay nhớt cho bao nhiêu lượt xe?', 2),                 # plain 'nhớt' = engine oil; gear oil and warranty excluded
            ('Tháng 8/2026 có mấy lượt thay ruột?', 1),                                 # 'vá ruột' is not a tube replacement
            ('Tháng 8/2026 có bao nhiêu lượt sửa điện?', 1),                            # bulbs/battery are not electrical repair; warranty excluded
            ('Tháng 8/2026 có bao nhiêu xe khách quen?', 1),                            # plates with >= 2 paid tickets
            ('Doanh thu tháng 8/2026 của tiệm là bao nhiêu?', 1000000),
            ('Trung bình mỗi phiếu tháng 8/2026 thu bao nhiêu tiền?', 200000),          # divided by paid tickets only
            ('Tiền công tháng 8/2026 là bao nhiêu?', 220000),
            ('Tháng 8/2026 thợ Tuấn làm bao nhiêu lượt xe?', 4),
            ('Tháng 8/2026 xe Exciter 150 vào tiệm mấy lượt?', 2),                      # 'Ex 150' is the same model line
        ]
        for text, expected in cases:
            answer = ask(catalog, engine, text, today)
            assert answer.get('answer') == expected, (text, answer)
        assert ask(catalog, engine, 'Tháng 8/2026 thợ nào làm nhiều xe nhất?', today)['answer'] == 'Tuấn'
        assert ask(catalog, engine, 'Tháng 8/2026 thợ nào chăm chỉ nhất?', today)['answer'] == 'Tuấn'
        assert ask(catalog, engine, 'Tháng 8/2026 dòng xe nào vào tiệm nhiều nhất?', today)['answer'] in {'Air Blade', 'AB', 'Air blade'}
        assert ask(catalog, engine, 'Tháng 8/2026 xe nào tới tiệm nhiều lần nhất?', today)['answer'] in {'59A1-123.45', '59A1 12345', '59A1-12345'}
    finally:
        engine.close()


MOTHER_BABY = {
    'README.md': 'Cửa hàng mẹ và bé. Dữ liệu có đến hết 31/08/2026.\n',
    'san_pham.csv': (
        'Mã hàng,Tên hàng,Nhóm hàng,Size,Số,Thương hiệu,Đơn vị cơ bản\n'
        'B1,Bỉm quần Bobby L 54 miếng,Bỉm tã,L,,Bobby,bịch\n'
        'B2,Bỉm quần Bobby XL 48 miếng,Bỉm tã,XL,,Bobby,bịch\n'
        'B3,Tã dán Huggies L 68,Bỉm tã,L,,Huggies,bịch\n'
        'B4,Tã dán Huggies M 76,Bỉm tã,,,Huggies,bịch\n'
        'S1,Sữa Meiji số 2 800g,Sữa,,2,Meiji,hộp\n'
        'S2,Sữa Similac 3 900g,Sữa,,3,Similac,hộp\n'
        'S3,Sữa Meiji 0-1 800g,Sữa,,,Meiji,hộp\n'),
    'khach_hang.csv': 'Mã khách hàng,Tên khách hàng,Ngày đăng ký\nK1,Lan,20/07/2026\nK2,Mai,03/08/2026\nK3,Hoa,25/08/2026\nK4,Tú,01/09/2026\n',
    'hoa_don.csv': (
        'Mã hóa đơn,Ngày,Tổng tiền hàng,Giảm voucher,Giảm điểm,Phí ship,Trạng thái,Mã khách hàng\n'
        'H1,02/08/2026,1100000,50000,20000,30000,Hoàn thành,K1\n'
        'H2,05/08/2026,580000,0,0,25000,Hoàn thành,K2\n'
        'H3,06/08/2026,900000,100000,0,0,Huỷ,K1\n'
        'H4,10/08/2026,1300000,30000,0,0,Trả hàng,K2\n'
        'H5,15/08/2026,1700000,20000,10000,0,Hoàn thành,K1\n'),
    'chi_tiet_hoa_don.csv': (
        'Mã hóa đơn,Mã hàng,Tên hàng,Số lượng,Thành tiền\n'
        'H1,B1,Bỉm quần Bobby L 54 miếng,2,600000\nH1,S1,Sữa Meiji số 2 800g,1,500000\n'
        'H2,B3,Tã dán Huggies L 68,1,300000\nH2,B4,Tã dán Huggies M 76,1,280000\n'
        'H3,B2,Bỉm quần Bobby XL 48 miếng,3,900000\n'
        'H4,B2,Bỉm quần Bobby XL 48 miếng,1,300000\nH4,S2,Sữa Similac 3 900g,2,1000000\n'
        'H5,B1,Bỉm quần Bobby L 54 miếng,1,300000\nH5,S1,Sữa Meiji số 2 800g,2,1000000\nH5,S3,Sữa Meiji 0-1 800g,1,400000\n'),
}


def test_mother_and_baby_rules(tmp_path):
    catalog, engine, report = shop(tmp_path, 'mebe', MOTHER_BABY)
    try:
        assert {'v_sales_attrs', 'v_members', 'v_order_money'} <= engine.tables, report['warnings']
        today = '2026-08-31'
        cases = [
            ('Tháng 8/2026 tổng tiền giảm voucher là bao nhiêu?', 70000),           # cancelled/returned invoices excluded
            ('Tháng 8/2026 khách được giảm bao nhiêu tiền nhờ dùng điểm?', 30000),
            ('Tháng 8/2026 phí ship thu được bao nhiêu?', 55000),
            ('Tháng 8/2026 có bao nhiêu đơn dùng voucher?', 2),
            ('Size L chiếm bao nhiêu phần trăm số bịch bỉm bán ra tháng 8/2026?', 80),  # blank-size Huggies M stays in the denominator only
            ('Hãng Huggies chiếm bao nhiêu % số bịch bỉm tã tháng 8/2026?', 40),
            ('Tháng 8/2026 bán được bao nhiêu hộp sữa số 2?', 3),                     # stage from the Số column, not the name
            ('Tháng 8/2026 có bao nhiêu khách thành viên mới?', 2),                   # includes a member who never bought
        ]
        for text, expected in cases:
            answer = ask(catalog, engine, text, today)
            assert answer.get('answer') == expected, (text, answer)
        assert ask(catalog, engine, 'Tháng 8/2026 size bỉm nào bán chạy nhất?', today)['answer'] == 'L'
        assert ask(catalog, engine, 'Tháng 8/2026 hãng bỉm nào bán chạy nhất?', today)['answer'] == 'Bobby'
    finally:
        engine.close()


PHONES = {
    'README.md': 'Cửa hàng điện thoại. Dữ liệu có đến hết 30/09/2026.\n',
    'don_hang.csv': (
        'Mã đơn,Ngày,Tổng tiền hàng,Tổng thanh toán,Thu máy cũ,Phương thức thanh toán,Trạng thái,Khách hàng\n'
        'D1,01/07/2026,30000000,30000000,5000000,Tiền mặt,Hoàn thành,An\n'
        'D2,05/07/2026,5000000,5000000,0,Trả góp,Hoàn thành,Bình\n'
        'D3,10/08/2026,2000000,2000000,0,Tiền mặt,Huỷ,An\n'
        'D4,12/08/2026,25000000,25000000,3000000,Chuyển khoản,Hoàn thành,Chi\n'
        'D5,20/08/2026,2000000,2000000,0,Tiền mặt,Hoàn thành,An\n'),
    'chi_tiet_don.csv': (
        'Mã đơn,Tên hàng,Nhóm hàng,Số lượng,Thành tiền\n'
        'D1,iPhone 15 Pro Max 256GB,Điện thoại,1,30000000\n'
        'D2,Samsung Galaxy A15 128GB,Điện thoại,1,4500000\nD2,Ốp lưng,Phụ kiện,1,500000\n'
        'D3,Nokia 105 4G,Điện thoại,1,2000000\n'
        'D4,iPhone 15 Pro Max 512GB,Điện thoại,1,25000000\n'
        'D5,Nokia 105 4G,Điện thoại,1,2000000\n'),
    'thanh_toan.csv': 'Mã đơn,Ngày thu,Tiền\nD1,01/07/2026,25000000\nD2,05/07/2026,2000000\nD4,12/08/2026,22000000\nD5,20/08/2026,2000000\n',
    'tra_gop.csv': 'Mã đơn,Kỳ,Hạn góp,Số tiền góp,Ngày góp\nD2,1,05/08/2026,1000000,04/08/2026\nD2,2,05/09/2026,1000000,\nD2,3,05/10/2026,1000000,\n',
    'bao_hanh.csv': 'Ngày nhận,Tên máy,Lỗi\n15/07/2026,iPhone 15 Pro Max 256GB,Lỗi loa\n20/08/2026,Samsung Galaxy A15 128GB,Lỗi pin\n',
}


def test_phone_store_rules(tmp_path):
    catalog, engine, report = shop(tmp_path, 'phones', PHONES)
    try:
        assert {'v_phone_sales', 'v_installments', 'v_warranty_claims', 'v_order_money'} <= engine.tables, report['warnings']
        cases = [
            ('2026-09-30', 'Quý 3/2026 tổng tiền thu máy cũ là bao nhiêu?', 8000000),      # not deducted from revenue
            ('2026-09-30', 'Quý 3/2026 khách phải trả tổng cộng bao nhiêu?', 54000000),     # after trade-in, incl. unpaid installment debt
            ('2026-09-30', 'Doanh thu quý 3/2026 là bao nhiêu?', 62000000),                 # full phone price; trade-in not deducted
            ('2026-09-30', 'Tỉ lệ bảo hành quý 3/2026 là bao nhiêu?', 50),                  # 2 claims / 4 phones sold (cancelled excluded)
            ('2026-09-10', 'Tính đến hôm nay khách còn nợ bao nhiêu?', 2000000),            # installment order is a debt order
            ('2026-09-10', 'Tháng 9/2026 thu nợ được bao nhiêu?', 0),                       # next installment not paid yet
            ('2026-08-31', 'Tháng 8/2026 thu nợ được bao nhiêu?', 1000000),                 # installment paid counts as debt collection
            ('2026-09-10', 'Đến hôm nay có bao nhiêu kỳ góp quá hạn?', 1),
            ('2026-09-05', 'Đến hôm nay có bao nhiêu kỳ góp quá hạn?', 0),                  # due today is not overdue
        ]
        for today, text, expected in cases:
            answer = ask(catalog, engine, text, today)
            assert answer.get('answer') == expected, (text, answer)
        assert ask(catalog, engine, 'Quý 3/2026 hãng nào bán được nhiều máy nhất?', '2026-09-30')['answer'] == 'Apple'
        assert ask(catalog, engine, 'Quý 3/2026 dòng máy nào bán chạy nhất?', '2026-09-30')['answer'] == 'iPhone 15 Pro Max'
    finally:
        engine.close()


@pytest.mark.parametrize('text', ['so vs cung thang nam truoc, dthu thang 4 nam 2026 thay doi may %', 'Doanh thu tuần trước là bao nhiêu?'])
def test_domain_compilers_ignore_ordinary_questions(tmp_path, text):
    catalog, engine, _ = shop(tmp_path, 'phones', PHONES)
    try:
        from datamate.domains import compile_domain
        assert compile_domain(QueryFrame(**BASE), Question(id='x', business_id='b', question=text, as_of_date='2026-09-30'), catalog, engine) is None
    finally:
        engine.close()


def test_model_role_fallback_for_unseen_headers(tmp_path):
    from datamate.roles import ShopRoles, validated
    # Headers that no alias matches; the (stubbed) model assignment maps them.
    files = {'README.md': 'Tiệm sửa xe. Dữ liệu có đến hết 31/08/2026.\n',
             'nhat_ky.csv': ('Ngày tiếp nhận,Xe số,Loại phương tiện,Việc đã làm,Người phụ trách,Thu của khách\n'
                             '01/08/2026,59A1-12345,Vision,Thay nhớt,An,100000\n02/08/2026,59A1-12345,Vision,Bảo hành thay nhớt,An,0\n'
                             '03/08/2026,51F-88888,Wave,Vá ruột,Bình,30000\n')}
    split = tmp_path / 'split'
    folder = split / 'data' / 'garage'
    folder.mkdir(parents=True)
    (split / 'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')
    (split / 'rulebook.md').write_text('x')
    for name, content in files.items():
        (folder / name).write_text(content)
    directory = prepare_shop(split, 'garage', tmp_path / 'cache')
    catalog = json.loads((directory / 'catalog.json').read_text())
    roles = ShopRoles.model_validate({'domain': 'vehicle_repair', 'assignments': [
        {'table': 'nhat_ky', 'column': 'xe_so', 'role': 'plate'}, {'table': 'nhat_ky', 'column': 'viec_da_lam', 'role': 'job'},
        {'table': 'nhat_ky', 'column': 'nguoi_phu_trach', 'role': 'mechanic'}, {'table': 'nhat_ky', 'column': 'ngay_tiep_nhan', 'role': 'visit_date'},
        {'table': 'nhat_ky', 'column': 'loai_phuong_tien', 'role': 'vehicle_model'}, {'table': 'nhat_ky', 'column': 'thu_cua_khach', 'role': 'ticket_amount'},
        {'table': 'nhat_ky', 'column': 'not_a_column', 'role': 'labor_amount'}]})
    catalog['llm_roles'] = validated(roles, catalog)
    assert 'labor' not in catalog['llm_roles']['tables']['nhat_ky']   # invented columns are dropped
    engine = QueryEngine(directory / 'store.duckdb', {t['name'] for t in catalog['tables']})
    try:
        attach_semantics(engine, catalog)
        assert ask(catalog, engine, 'Tỉ lệ bảo hành tháng 8/2026 là bao nhiêu?', '2026-08-31')['answer'] == 50
        assert ask(catalog, engine, 'Doanh thu tháng 8/2026 của tiệm là bao nhiêu?', '2026-08-31')['answer'] == 130000
    finally:
        engine.close()
    # Without the model's assignment the same headers build no repair book.
    catalog.pop('llm_roles')
    engine = QueryEngine(directory / 'store.duckdb', {t['name'] for t in catalog['tables']})
    try:
        attach_semantics(engine, catalog)
        assert 'v_tickets' not in engine.tables
    finally:
        engine.close()


def test_model_role_fallback_builds_sales_source_only_when_aliases_fail(tmp_path):
    from datamate.roles import ShopRoles, validated
    split = tmp_path / 'split'
    folder = split / 'data' / 'odd'
    folder.mkdir(parents=True)
    (split / 'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')
    (split / 'rulebook.md').write_text('x')
    (folder / 'README.md').write_text('Sổ bán. Dữ liệu có đến hết 31/01/2026.\n')
    (folder / 'nhat_ky_ban.csv').write_text('Phiếu,Hôm,Món bán,Bao nhiêu,Tiền thu dòng,Tình hình\nA,02/01/2026,Trà,2,40000,ok\nB,03/01/2026,Cà phê,1,25000,huỷ\n')
    directory = prepare_shop(split, 'odd', tmp_path / 'cache')
    catalog = json.loads((directory / 'catalog.json').read_text())
    catalog['llm_roles'] = validated(ShopRoles.model_validate({'domain': 'cafe', 'assignments': [
        {'table': 'nhat_ky_ban', 'column': c, 'role': r} for c, r in [('phieu', 'order_id'), ('hom', 'order_date'), ('mon_ban', 'product_name'),
                                                                    ('bao_nhieu', 'quantity'), ('tien_thu_dong', 'line_amount'), ('tinh_hinh', 'status')]]}), catalog)
    engine = QueryEngine(directory / 'store.duckdb', {t['name'] for t in catalog['tables']})
    try:
        report = attach_semantics(engine, catalog)
        # 6 model roles + 5 shared fields (purchase/lot quantity, purchase amount, purchase/receipt date).
        assert report.get('role_fallback') == 11 and report['capabilities']['revenue']
        assert ask(catalog, engine, 'Doanh thu tháng 1/2026 là bao nhiêu?', '2026-01-31', metric='revenue', operation='value')['answer'] == 40000
    finally:
        engine.close()


def test_repair_book_without_plates_when_model_says_repair_shop(tmp_path):
    from datamate.roles import ShopRoles, validated
    split = tmp_path / 'split'
    folder = split / 'data' / 'garage'
    folder.mkdir(parents=True)
    (split / 'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')
    (split / 'rulebook.md').write_text('x')
    (folder / 'README.md').write_text('Tiệm sửa xe. Dữ liệu có đến hết 31/08/2026.\n')
    (folder / 'so_ghi.csv').write_text('Ngày,Xe,Làm gì,Thợ,Tiền\n01/08/2026,Vision,Thay nhớt,An,100000\n02/08/2026,Vision,Bảo hành thay nhớt,An,0\n03/08/2026,Wave,Vá ruột,Bình,30000\n')
    directory = prepare_shop(split, 'garage', tmp_path / 'cache')
    catalog = json.loads((directory / 'catalog.json').read_text())
    engine = QueryEngine(directory / 'store.duckdb', {t['name'] for t in catalog['tables']})
    try:
        attach_semantics(engine, catalog)
        assert 'v_tickets' not in engine.tables          # aliases alone never build a book without plates
    finally:
        engine.close()
    catalog['llm_roles'] = validated(ShopRoles.model_validate({'domain': 'vehicle_repair', 'assignments': [
        {'table': 'so_ghi', 'column': c, 'role': r} for c, r in [('ngay', 'visit_date'), ('xe', 'vehicle_model'), ('lam_gi', 'job'), ('tho', 'mechanic'), ('tien', 'ticket_amount')]]}), catalog)
    engine = QueryEngine(directory / 'store.duckdb', {t['name'] for t in catalog['tables']})
    try:
        attach_semantics(engine, catalog)
        assert ask(catalog, engine, 'Tỉ lệ bảo hành tháng 8/2026 là bao nhiêu?', '2026-08-31')['answer'] == 50
        assert ask(catalog, engine, 'Tháng 8/2026 tiệm có bao nhiêu lượt xe?', '2026-08-31')['answer'] == 3
        with pytest.raises(Exception):  # no plates: vehicle counts are not guessed
            ask(catalog, engine, 'Tháng 8/2026 có bao nhiêu chiếc xe khác nhau?', '2026-08-31')
    finally:
        engine.close()


def test_readme_descriptions_map_unseen_headers_without_model(tmp_path):
    split = tmp_path / 'split'
    folder = split / 'data' / 'odd2'
    folder.mkdir(parents=True)
    (split / 'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')
    (split / 'rulebook.md').write_text('x')
    (folder / 'README.md').write_text("""# Dữ liệu odd2

## Cột trong `nhat_ky.csv`
- **Phiếu**: mã đơn
- **Hôm**: ngày bán
- **Món bán**: tên hàng
- **Bao nhiêu**: số lượng
- **Tiền thu dòng**: thành tiền sau giảm
- **Tình hình**: trạng thái đơn
- **Người mua**: tên khách (Khách lẻ nếu không lưu)

## Quy ước
- Dữ liệu có đến hết 31/01/2026.
""")
    (folder / 'nhat_ky.csv').write_text('Phiếu,Hôm,Món bán,Bao nhiêu,Tiền thu dòng,Tình hình,Người mua\nA,02/01/2026,Trà,2,40000,Hoàn thành,Chị Lan\nB,03/01/2026,Cà phê,1,25000,Huỷ,Khách lẻ\nC,04/01/2026,Trà,1,20000,Hoàn thành,Chị Lan\n')
    directory = prepare_shop(split, 'odd2', tmp_path / 'cache')
    catalog = json.loads((directory / 'catalog.json').read_text())
    engine = QueryEngine(directory / 'store.duckdb', {t['name'] for t in catalog['tables']})
    try:
        report = attach_semantics(engine, catalog)
        mapped = report['mapping']['nhat_ky']
        assert (mapped['order_id'], mapped['date'], mapped['product_name'], mapped['quantity'], mapped['revenue'], mapped['status'], mapped['customer_name']) == \
            ('phieu', 'hom', 'mon_ban', 'bao_nhieu', 'tien_thu_dong', 'tinh_hinh', 'nguoi_mua')
        assert ask(catalog, engine, 'Doanh thu tháng 1/2026 là bao nhiêu?', '2026-01-31', metric='revenue', operation='value')['answer'] == 60000
    finally:
        engine.close()


def test_key_role_spreads_by_column_name_across_tables():
    from datamate.semantics import complete_roles
    tables = [{'name': 'don', 'columns': [{'name': 'so_ct'}, {'name': 'ngay_lap'}]},
              {'name': 'dong_don', 'columns': [{'name': 'so_ct'}, {'name': 'sl'}, {'name': 'dvt'}]},
              {'name': 'phieu_nhap', 'columns': [{'name': 'so_ct'}, {'name': 'sl'}]}]
    mapping = {'don': {'order_id': 'so_ct', 'date': 'ngay_lap'}, 'dong_don': {'quantity': 'sl', 'unit': 'dvt'}, 'phieu_nhap': {}}
    complete_roles(tables, mapping)
    assert mapping['dong_don']['order_id'] == 'so_ct' and mapping['dong_don']['base_unit'] == 'dvt' and mapping['dong_don']['lot_quantity'] == 'sl'
    assert mapping['don']['purchase_date'] == 'ngay_lap' and mapping['phieu_nhap']['purchase_id'] == 'so_ct'


def test_debt_is_not_called_unrecorded_when_a_payment_book_is_unread(tmp_path):
    from datamate.domains import UnreadableSource
    files = {'README.md': 'Sổ bán. Dữ liệu có đến hết 31/01/2026.\n',
             'ban_hang.csv': 'Mã đơn,Ngày bán,Tên hàng,Số lượng,Thành tiền,Khách hàng,Trạng thái\nA,02/01/2026,Trà,2,40000,Chị Lan,Hoàn thành\nB,03/01/2026,Cà phê,1,25000,Anh Nam,Hoàn thành\n'}
    catalog, engine, _ = shop(tmp_path, 'plain', files)
    try:
        assert ask(catalog, engine, 'Tính đến hết ngày 31/01/2026 khách còn nợ bao nhiêu?', '2026-01-31', metric='revenue', operation='value')['type'] == 'unanswerable'
    finally:
        engine.close()
    files['so_quy.csv'] = 'Chứng từ,Lúc,Giá trị nhận\nPT1,04/01/2026,10000\nPT2,05/01/2026,5000\nPT3,06/01/2026,7000\n'
    catalog, engine, _ = shop(tmp_path / 'b', 'withbook', files)
    try:
        with pytest.raises(UnreadableSource):
            ask(catalog, engine, 'Tính đến hết ngày 31/01/2026 khách còn nợ bao nhiêu?', '2026-01-31', metric='revenue', operation='value')
    finally:
        engine.close()


def test_repair_book_from_readme_descriptions_with_unseen_headers(tmp_path):
    rows = REPAIR['so_sua_xe.csv'].splitlines()[1:]
    files = {'README.md': """# Dữ liệu garage

## Cột trong `nhat_ky_xuong.csv`
- **Hôm**: ngày sửa
- **Số đăng ký**: biển số xe
- **Xe gì**: dòng xe
- **Hạng mục làm**: công việc đã làm
- **Người làm**: thợ sửa
- **Công**: tiền công
- **Đồ thay**: tiền phụ tùng
- **Thu khách**: tổng tiền
- **Lưu ý**: ghi chú

## Quy ước
- Dữ liệu có đến hết 31/08/2026.
""", 'nhat_ky_xuong.csv': 'Hôm,Số đăng ký,Xe gì,Hạng mục làm,Người làm,Công,Đồ thay,Thu khách,Lưu ý\n' + '\n'.join(rows) + '\n'}
    catalog, engine, report = shop(tmp_path, 'garage2', files)
    try:
        assert report['capabilities'].get('tickets'), report['warnings']
        assert ask(catalog, engine, 'Tháng 8/2026 có bao nhiêu chiếc xe khác nhau mang tới tiệm?', '2026-08-31')['answer'] == 3
        assert ask(catalog, engine, 'Tỉ lệ bảo hành tháng 8/2026 là bao nhiêu?', '2026-08-31')['answer'] == 40
        assert ask(catalog, engine, 'Tháng 8/2026 thợ Tuấn làm bao nhiêu lượt xe?', '2026-08-31')['answer'] == 4
    finally:
        engine.close()


def test_repair_plate_inside_vehicle_text_and_unrecorded_job(tmp_path):
    import csv as _csv, io as _io
    rows = list(_csv.reader(_io.StringIO(REPAIR['so_sua_xe.csv'])))
    header = ['Ngày', 'Xe', 'Làm gì', 'Thợ', 'Tiền công', 'Tiền phụ tùng', 'Tổng tiền', 'Ghi chú']
    body = [[r[0], f'{r[2]} {r[1]}'] + r[3:] for r in rows[1:]]
    out = _io.StringIO()
    _csv.writer(out).writerows([header] + body)
    catalog, engine, report = shop(tmp_path, 'garage3', {'README.md': REPAIR['README.md'], 'so_sua_xe.csv': out.getvalue()})
    try:
        assert report['capabilities'].get('tickets')
        assert ask(catalog, engine, 'Tháng 8/2026 có bao nhiêu chiếc xe khác nhau mang tới tiệm?', '2026-08-31')['answer'] == 3
        assert ask(catalog, engine, 'Tháng 8/2026 xe Exciter 150 vào tiệm mấy lượt?', '2026-08-31')['answer'] == 2
        with pytest.raises(Exception):
            # 'thay má phanh' is not recorded in the book: not the count of every visit.
            ask(catalog, engine, 'Tháng 8/2026 tiệm thay má phanh cho bao nhiêu lượt xe?', '2026-08-31')
    finally:
        engine.close()


def test_repair_book_with_more_than_1000_distinct_jobs(tmp_path):
    # Internal lookups (job keys, names) are not answers: a large free-text book must not hit the answer row cap.
    lines = ['Ngày,Biển số,Dòng xe,Làm gì,Thợ,Tổng tiền']
    for i in range(1200):
        lines.append(f'{1 + i % 28:02d}/08/2026,59A1-{10000 + i},Vision,Thay nhông sên dĩa loại {i},{"Tuấn" if i % 2 else "Hùng"},{100000 + i}')
    catalog, engine, report = shop(tmp_path, 'bigbook', {'README.md': REPAIR['README.md'], 'so_sua_xe.csv': '\n'.join(lines) + '\n'})
    try:
        assert report['capabilities'].get('tickets')
        assert ask(catalog, engine, 'Tháng 8/2026 tiệm có bao nhiêu lượt xe?', '2026-08-31')['answer'] == 1200
        assert ask(catalog, engine, 'Tháng 8/2026 thợ Tuấn làm bao nhiêu lượt xe?', '2026-08-31')['answer'] == 600
    finally:
        engine.close()


def test_customer_filter_with_more_than_1000_customers(tmp_path):
    lines = ['Mã đơn,Ngày bán,Tên hàng,Số lượng,Thành tiền,Khách hàng,Trạng thái']
    for i in range(1300):
        lines.append(f'D{i},{1 + i % 28:02d}/01/2026,Trà,1,{1000 + i},Khách Số {i},Hoàn thành')
    catalog, engine, _ = shop(tmp_path, 'manycustomers', {'README.md': 'Dữ liệu có đến hết 31/01/2026.\n', 'ban_hang.csv': '\n'.join(lines) + '\n'})
    try:
        assert ask(catalog, engine, 'Tháng 1/2026 khách Khách Số 1250 mua bao nhiêu tiền?', '2026-01-31', metric='revenue', operation='value', customer_filter='Khách Số 1250')['answer'] == 2250
    finally:
        engine.close()


def test_payment_book_with_unseen_headers_is_read_by_structure(tmp_path):
    files = {'README.md': 'Sổ bán. Dữ liệu có đến hết 31/01/2026.\n',
             'ban_hang.csv': 'Mã đơn,Ngày bán,Tên hàng,Số lượng,Thành tiền,Tổng thanh toán,Khách hàng,Trạng thái,Hình thức thanh toán\n'
                             'A,02/01/2026,Trà,2,40000,40000,Chị Lan,Hoàn thành,Ghi nợ\nB,03/01/2026,Cà phê,1,25000,25000,Anh Nam,Hoàn thành,Tiền mặt\n',
             'phieu_thu.csv': 'Chứng từ,Đơn gốc,Lúc,Khoản nhận\nPT1,A,05/01/2026,10000\nPT2,B,03/01/2026,25000\nPT3,A,08/01/2026,5000\n'}
    catalog, engine, _ = shop(tmp_path, 'paybook', files)
    try:
        assert 'v_receipts' in engine.tables
        answer = ask(catalog, engine, 'Tính đến hết ngày 31/01/2026 khách còn nợ bao nhiêu?', '2026-01-31', metric='other', operation='value')
        assert answer.get('answer') == 25000, answer      # 40000 - 10000 - 5000
    finally:
        engine.close()


SHIFT = {
    'README.md': 'Quán cà phê. Dữ liệu có đến hết 31/03/2026.\n',
    'ban_hang.csv': 'Mã đơn,Ngày,Tên hàng,Số lượng,Thành tiền,Hình thức thanh toán,Trạng thái\nA,01/03/2026,Cà phê,2,40000,Tiền mặt,Hoàn thành\nB,01/03/2026,Trà,1,30000,Chuyển khoản,Hoàn thành\n',
    'bao_cao_ca.csv': ('Ngày,Ca,Mã NV,Tiền mặt thu,Tiền mặt hoàn khách,Tiền mặt ròng của ca\n'
                       '01/03/2026,sáng,NV002,2403000,0,2403000\n01/03/2026,chiều,NV003,1719000,19000,1700000\n02/03/2026,sáng,NV003,2548000,0,2548000\n'),
}


@pytest.mark.parametrize('text,expected', [
    ('Tiền mặt thu được ca sáng ngày 01/03/2026 là bao nhiêu?', 2403000),        # from the shift report, not from orders (40000)
    ('Ngày 01/03/2026 ca chiều tiền mặt ròng của ca là bao nhiêu?', 1700000),
    ('Tháng 3/2026 tiền mặt hoàn khách của các ca là bao nhiêu?', 19000),
    ('Tháng 3/2026 nhân viên NV003 thu tiền mặt các ca được bao nhiêu?', 4267000),
])
def test_cash_by_shift_comes_from_the_shift_report(tmp_path, text, expected):
    catalog, engine, _ = shop(tmp_path, 'cafe', SHIFT)
    try:
        assert 'v_shift_cash' in engine.tables
        assert ask(catalog, engine, text, '2026-03-31') == {'type': 'number', 'answer': expected}
    finally:
        engine.close()
