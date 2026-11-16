import json

from datamate.core import compile_frame
from datamate.engine import QueryEngine
from datamate.ingestion import prepare_shop
from datamate.output import finalize
from datamate.semantics import attach_semantics
from datamate.types import QueryFrame,Question


def test_historical_debt_returns_cod_weighted_purchase_and_stock_units(tmp_path):
    split=tmp_path/'split'
    shop=split/'data'/'example'
    shop.mkdir(parents=True)
    (split/'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')
    (split/'rulebook.md').write_text('Returned orders retain debt, COD excluded. Weighted purchases.')
    (shop/'README.md').write_text('Dữ liệu có đến hết 31/01/2026.\n  - Trà: 1 hộp = 6 cái\n')
    (shop/'san_pham.csv').write_text('Mã hàng,Tên hàng,Đơn vị cơ bản,Giá vốn\nP,Trà,cái,3\n')
    (shop/'thuoc.csv').write_text('Mã hàng,Kê đơn,Hoạt chất\nP,1,Chất thử\n')
    (shop/'don_hang.csv').write_text('Mã đơn,Ngày,Tổng tiền hàng,Tổng thanh toán,Còn nợ,Phương thức thanh toán,Trạng thái,Khách hàng\nA,01/01/2026,100,110,60,debt,Trả hàng,Lan\nB,02/01/2026,80,85,0,cash,Xong,Lan\nC,03/01/2026,50,50,0,debt,Huỷ,Lan\nD,04/01/2026,70,70,70,cod,Xong,Lan\n')
    (shop/'chi_tiet_don.csv').write_text('Mã đơn,Mã hàng,Tên hàng,Số lượng,Thành tiền,Đơn giá,ĐVT\nA,P,Trà,1,100,100,cái\nB,P,Trà,1,80,80,cái\nC,P,Trà,1,50,50,cái\nD,P,Trà,1,70,70,cái\n')
    (shop/'thanh_toan.csv').write_text('Mã đơn,Ngày thu,Tiền\nA,01/01/2026,20\nA,05/01/2026,30\nB,02/01/2026,85\n')
    (shop/'ton_kho.csv').write_text('Mã hàng,Tên hàng,Đơn vị cơ bản,Tồn cuối\nP,Trà,cái,14\n')
    (shop/'so_nhap_hang.csv').write_text('Số phiếu,Ngày nhập,Tên hàng,SL nhập,Giá nhập,Thành tiền,ĐVT\nN1,01/01/2026,Trà,2,60,120,hộp\nN2,02/01/2026,Trà,3,90,270,hộp\n')
    directory=prepare_shop(split,'example',tmp_path/'cache')
    catalog=json.loads((directory/'catalog.json').read_text())
    engine=QueryEngine(directory/'store.duckdb',{t['name'] for t in catalog['tables']})
    base=dict(metric='other',operation='other',group_by='none',product_filter='',customer_filter='',group_filter='',customer_group_filter='',unit_filter='',payment_filter='',channel_filter='',size_filter='',k=1,threshold=0,threshold_operator='>=',weekday_filter=0,output_type='number',reason='specialty')
    try:
        report=attach_semantics(engine,catalog)
        assert report['specialty_ready'], report['warnings']
        examples=[
            ('Tính đến ngày 06/01/2026 khách còn nợ bao nhiêu?', '', {'type':'number','answer':60}),
            # 'dư nợ', 'còn phải thu', 'khoản nợ' are the same outstanding balance (§4).
            ('Dư nợ khách hàng đến hết ngày 06/01/2026 là bao nhiêu?', '', {'type':'number','answer':60}),
            ('Tính đến ngày 06/01/2026 tiền khách còn phải thu là bao nhiêu?', '', {'type':'number','answer':60}),
            ('Đến hết ngày 06/01/2026 khoản nợ chưa thu của cửa hàng là bao nhiêu?', '', {'type':'number','answer':60}),
            ('Tháng 1/2026 thu nợ được bao nhiêu?', '', {'type':'number','answer':30}),
            ('Tháng 1/2026 giá nhập bình quân một hộp Trà?', 'Trà', {'type':'number','answer':78}),
            ('Cuối ngày hôm nay Trà còn mấy hộp chẵn, mấy cái lẻ?', 'Trà', {'type':'list','answer':[2,2]}),
            ('Cuối ngày hôm nay tồn kho đáng bao nhiêu tiền theo giá vốn?', '', {'type':'number','answer':42}),
            ('Tháng 1/2026 doanh thu thuốc kê đơn bao nhiêu?', '', {'type':'number','answer':150}),
            ('Tháng 1/2026 doanh thu thuốc kê đơn chiếm bao nhiêu phần trăm?', '', {'type':'percent','answer':100}),
            ('Đến hết ngày 10/01/2026 số còn nợ nhóm 0–7, 8–15 và trên 15 ngày?', '', {'type':'list','answer':[0,60,0]}),
        ]
        for text,product,expected in examples:
            frame=QueryFrame(**(base|{'product_filter':product}))
            q=Question(id='x',business_id='example',question=text,as_of_date='2026-01-31')
            plan=compile_frame(frame,q,catalog,engine)
            assert finalize(plan,*engine.query(plan.sql))==expected,(text,plan.sql)
        q=Question(id='x',business_id='example',question='Cuối ngày hôm nay Trà trong kho còn mấy cái?',as_of_date='2026-01-20')
        assert compile_frame(QueryFrame(**base),q,catalog,engine).action=='unanswerable'
    finally:
        engine.close()


def test_lot_stock_expiry_capital_and_delivery_batches(tmp_path):
    split=tmp_path/'split'
    shop=split/'data'/'pharma'
    shop.mkdir(parents=True)
    (split/'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')
    (split/'rulebook.md').write_text('Lots.')
    (shop/'README.md').write_text('Dữ liệu có đến hết 31/01/2026.\n  - Thuốc A: 1 hộp = 10 viên\n')
    (shop/'san_pham.csv').write_text('Mã hàng,Tên hàng,Đơn vị cơ bản\nP,Thuốc A,viên\n')
    (shop/'don_hang.csv').write_text('Mã đơn,Ngày,Tổng tiền hàng,Trạng thái\nA,02/01/2026,100,Xong\nB,06/01/2026,50,Trả hàng\nC,07/01/2026,40,Huỷ\n')
    (shop/'chi_tiet_don.csv').write_text('Mã đơn,Mã hàng,Tên hàng,Số lượng,Thành tiền,ĐVT\nA,P,Thuốc A,12,100,viên\nB,P,Thuốc A,5,50,viên\nC,P,Thuốc A,4,40,viên\n')
    (shop/'nhap_hang.csv').write_text('Mã nhập hàng,Thời gian,Mã NCC,Trạng thái\nN1,01/01/2026,NCC1,Hoàn thành\nN2,05/01/2026,NCC2,Hoàn thành\n')
    (shop/'chi_tiet_nhap_hang.csv').write_text('Mã nhập hàng,Mã hàng,ĐVT,Số lượng,Đơn giá,Lô,Hạn sử dụng\nN1,P,hộp,3,50000,L1,10/02/2026\nN2,P,hộp,2,60000,L2,01/06/2026\n')
    (shop/'lo_hang.csv').write_text('Mã nhập hàng,Mã hàng,Lô,Hạn sử dụng,Số lượng (ĐVT cơ bản)\nN1,P,L1,10/02/2026,30\nN2,P,L2,01/06/2026,20\n')
    (shop/'hoa_don_chi_tiet_lo.csv').write_text('Mã hóa đơn,Lô,Số lượng (ĐVT cơ bản),Số lượng trả (ĐVT cơ bản)\nA,L1,12,0\nB,L1,5,5\nC,L2,4,0\n')
    (shop/'giao_hang.csv').write_text('Ngày giao hàng,Mã đơn hàng,Lần giao,Tên hàng hóa,Số lượng giao\n02/01/2026,A,1,Thuốc A,6\n02/01/2026,A,1,Thuốc A,1\n03/01/2026,A,2,Thuốc A,5\n06/01/2026,B,1,Thuốc A,5\n03/02/2026,B,2,Thuốc A,1\n')
    directory=prepare_shop(split,'pharma',tmp_path/'cache')
    catalog=json.loads((directory/'catalog.json').read_text())
    engine=QueryEngine(directory/'store.duckdb',{t['name'] for t in catalog['tables']})
    base=dict(metric='other',operation='other',group_by='none',product_filter='',customer_filter='',group_filter='',customer_group_filter='',unit_filter='',payment_filter='',channel_filter='',size_filter='',k=1,threshold=0,threshold_operator='>=',weekday_filter=0,output_type='number',reason='lots')
    try:
        report=attach_semantics(engine,catalog)
        assert {'v_lots','v_lot_moves','v_deliveries'} <= engine.tables, report['warnings']
        examples=[
            # L1: 30 in, 12 sold, 5 sold then returned -> 18; L2: 20 in, cancelled sale ignored -> 20.
            ('Cuối ngày hôm nay Thuốc A trong kho còn mấy hộp chẵn, mấy viên lẻ?', 'Thuốc A', '2026-01-31', {'type':'list','answer':[3,8]}),
            ('Đến hết ngày 03/01/2026 Thuốc A còn mấy hộp chẵn, mấy viên lẻ trong kho?', 'Thuốc A', '2026-01-31', {'type':'list','answer':[1,8]}),
            ('Lô Thuốc A gần hết hạn nhất có hạn dùng ngày nào?', 'Thuốc A', '2026-01-31', {'type':'date','answer':'2026-02-10'}),
            ('Hàng nào hết hạn trong 30 ngày tới mà còn tồn kho?', '', '2026-01-31', {'type':'list','answer':['Thuốc A']}),
            ('Tiền vốn đang nằm ở các lô còn tồn có hạn dùng trong 30 ngày tới là bao nhiêu?', '', '2026-01-31', {'type':'number','answer':90000}),
            # 'N tháng tới' (same day N months later), one date after today ('từ nay đến hết ngày D'), lots already expired.
            ('Có bao nhiêu lô còn tồn sẽ hết hạn trong 1 tháng tới?', '', '2026-01-31', {'type':'number','answer':1}),
            ('Có mấy lô còn tồn sẽ hết hạn từ nay đến hết ngày 15/02/2026?', '', '2026-01-31', {'type':'number','answer':1}),
            ('Có mấy lô còn tồn sẽ hết hạn từ nay đến hết ngày 05/02/2026?', '', '2026-01-31', {'type':'number','answer':0}),
            ('Hiện có bao nhiêu lô đã hết hạn mà vẫn còn tồn?', '', '2026-03-01', {'type':'number','answer':1}),
            # Batches are (order, batch): A has 2, B has 1 in January; B's second batch is in February.
            ('Tháng 1/2026 xe đã giao tổng cộng mấy đợt hàng?', '', '2026-01-31', {'type':'number','answer':3}),
        ]
        for text,product,today,expected in examples:
            q=Question(id='x',business_id='pharma',question=text,as_of_date=today)
            plan=compile_frame(QueryFrame(**(base|{'product_filter':product})),q,catalog,engine)
            assert finalize(plan,*engine.query(plan.sql))==expected,(text,plan.sql)
    finally:
        engine.close()
