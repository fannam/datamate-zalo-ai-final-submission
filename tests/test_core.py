import duckdb
import pytest

from datamate.core import compile_frame
from datamate.engine import QueryEngine
from datamate.output import finalize
from datamate.types import QueryFrame, Question


@pytest.mark.parametrize('question,expected', [
    ('Tháng 1/2026 doanh thu đơn COD và ví điện tử mỗi loại bao nhiêu?', [20,30]),
    ('Tháng 1/2026 doanh thu qua điện thoại và Zalo OA mỗi loại bao nhiêu?', [30,30]),
])
def test_multiple_filters_return_ordered_values_even_if_frame_says_scalar(tmp_path, question, expected):
    path = tmp_path / 'data.duckdb'
    con = duckdb.connect(str(path))
    con.execute('CREATE TABLE v_orders(order_id VARCHAR,order_date TIMESTAMP,revenue INTEGER,payment_method VARCHAR,channel VARCHAR)')
    con.execute("INSERT INTO v_orders VALUES ('a','2026-01-01',10,'cash','phone'),('b','2026-01-01',20,'cod','phone'),('c','2026-01-01',30,'ewallet','zalo_oa')")
    con.close()
    engine = QueryEngine(path, {'v_orders'})
    frame = QueryFrame(metric='revenue',operation='value',group_by='none',product_filter='',customer_filter='',group_filter='',customer_group_filter='',unit_filter='',payment_filter='COD và ví điện tử',channel_filter='điện thoại và Zalo OA',size_filter='',k=1,threshold=0,threshold_operator='>=',weekday_filter=0,output_type='number',reason='two values')
    # Only one filter dimension is relevant in each question.
    if 'COD' in question:
        frame.channel_filter = ''
    else:
        frame.payment_filter = ''
    catalog = {'readme':'Dữ liệu có đến hết 31/01/2026.','semantic':{'capabilities':{'revenue':True,'orders':True}}}
    try:
        plan = compile_frame(frame,Question(id='test',business_id='shop',question=question,as_of_date='2026-01-31'),catalog,engine)
        assert finalize(plan,*engine.query(plan.sql)) == {'type':'list','answer':expected}
    finally:
        engine.close()


@pytest.mark.parametrize('question,expected', [
    ('So với tháng 3 năm 2025, doanh thu tháng 3 năm 2026 tăng bao nhiêu phần trăm?', 100.0),   # base = period after 'so với'
    ('Doanh thu tháng 3 năm 2026 tăng bao nhiêu phần trăm so với tháng 3 năm 2025?', 100.0),
    ('Tháng 3/2026 so với tháng 2/2026 doanh thu tăng bao nhiêu %?', 300.0),
])
def test_growth_base_is_the_period_after_so_voi(tmp_path, question, expected):
    path = tmp_path / 'data.duckdb'
    con = duckdb.connect(str(path))
    con.execute('CREATE TABLE v_orders(order_id VARCHAR,order_date TIMESTAMP,revenue INTEGER,payment_method VARCHAR,channel VARCHAR)')
    con.execute("INSERT INTO v_orders VALUES ('a','2025-03-05',100,'cash','store'),('b','2026-03-05',200,'cash','store'),('c','2026-02-05',50,'cash','store')")
    con.close()
    engine = QueryEngine(path, {'v_orders'})
    frame = QueryFrame(metric='revenue',operation='growth',group_by='none',product_filter='',customer_filter='',group_filter='',customer_group_filter='',unit_filter='',payment_filter='',channel_filter='',size_filter='',k=1,threshold=0,threshold_operator='>=',weekday_filter=0,output_type='percent',reason='growth')
    catalog = {'readme':'Dữ liệu có đến hết 31/03/2026.','semantic':{'capabilities':{'revenue':True,'orders':True}}}
    try:
        plan = compile_frame(frame,Question(id='t',business_id='shop',question=question,as_of_date='2026-03-31'),catalog,engine)
        assert finalize(plan,*engine.query(plan.sql)) == {'type':'percent','answer':expected}
    finally:
        engine.close()
