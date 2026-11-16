"""Object, measure and time scope, and missing-source cases, on generated shops."""
import datetime as dt
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_rules_items_and_domains import build, ask, BOOK, BOOK_DESC, ROSTER, ROSTER_DESC, TICKETS, FRAME
from test_rules_periods_and_customers import SALES, SALES_DESC
from datamate.temporal import resolve_periods
from datamate.intent import normalize_question, ground_frame
from datamate.types import QueryFrame, Question
from datamate.specialty import compile_due
from datamate.domains import compile_installments
from datamate.output import finalize

@pytest.mark.parametrize("text,months",[
 ("Dthu từng tháng từ 02/2025 tới 04/2025 lần lượt?",["2025-02","2025-03","2025-04"]),
 ("Doanh thu 3 tháng từ th3/2025 đến th5/2025 tháng nào cao nhất?",["2025-03","2025-04","2025-05"]),
 ("Dthu th7, th8, th9/2025 lần lượt?",["2025-07","2025-08","2025-09"]),
 ("Dthu tháng 3, tháng 4, tháng 5 năm 2025?",["2025-03","2025-04","2025-05"]),
 ("Dthu tháng 5, 6, 7/2025?",["2025-05","2025-06","2025-07"]),
 ("Doanh thu tháng Tư năm 2025?",["2025-04"]),
 ("Doanh thu thang tu nam 2025?",["2025-04"]),
])
def test_month_scopes(text,months):
 periods=resolve_periods(normalize_question(text),dt.date(2026,8,21))
 assert [p.start.strftime('%Y-%m') for p in periods]==months

def test_mtd_from_first():
 p=resolve_periods(normalize_question("Từ mùng 1 tới hết hôm nay so với cùng kỳ tháng trước"),dt.date(2025,8,8))[0]
 assert (p.start,p.end,p.mode)==(dt.date(2025,8,1),dt.date(2025,8,8),'mtd')
 assert p.previous().end==dt.date(2025,7,8)

def test_explicit_object_beats_wrong_frame():
 f=QueryFrame(**FRAME)
 t=ground_frame(f,"Mấy mặt hàng nằm trong ít nhất 12 đơn khác nhau?")
 assert (t.metric,t.operation,t.group_by,t.threshold)==('product_order_count','threshold_count','product',12)
 t=ground_frame(f,"Trong khách mua tháng 2/2026, mấy % mua từ 3 đh trở lên?")
 assert (t.metric,t.operation,t.group_by,t.threshold)==('orders','threshold_share','customer',3)
 t=ground_frame(f,"Mấy đơn doanh thu đạt từ 3tr7 trở lên?")
 assert (t.operation,t.threshold,t.threshold_operator)==('threshold_count',3700000,'>=')
 t=ground_frame(f,"Từ th2/2025 đến th4/2025 doanh thu tháng sau có luôn hơn tháng trước không?")
 assert (t.operation,t.output_type)==('trend','boolean')

@pytest.fixture
def small_shop(tmp_path):
 lines=[("D1","05/02/2026","Lan","Gạch A",1,"thùng",100,"Xong"),
        ("D2","08/02/2026","Minh","Gạch A",2,"thùng",200,"Xong"),
        ("D3","09/02/2026","Hoa","Gạch A",1,"thùng",300,"Huỷ")]
 catalog,engine=build(tmp_path,{"ban_hang.csv":(SALES,SALES_DESC,[dict(zip(SALES,r)) for r in lines])})
 yield catalog,engine
 engine.close()

def test_subjective_precedes_supplier_ranking(small_shop):
 catalog,engine=small_shop
 engine.connection.execute("CREATE TEMP TABLE v_purchases(supplier VARCHAR,purchase_date TIMESTAMP,cost DECIMAL,product_name VARCHAR)")
 engine.connection.execute("INSERT INTO v_purchases VALUES ('Cty A','2026-02-01',900,'Gạch A')")
 engine.tables.add('v_purchases')
 for question in ["Tháng 2/2026 nhà cung cấp uy tín là ai?", "Nhà cung cấp đáng tin cậy tháng 2/2026 là ai?",
                  "Tháng 2/2026 khách trung thực nhất là ai?", "Tháng 2/2026 khách thân thiện nhất là ai?"]:
  assert ask(catalog,engine,question,metric='revenue',operation='top',group_by='customer',output_type='string')=='unanswerable'
 # Objective supplier question still computes.
 assert ask(catalog,engine,"Tháng 2/2026 nhà cung cấp nào nhiều tiền nhất?")=='Cty A'
 assert ask(catalog,engine,"Tháng 2/2026 nhóm sơn doanh thu bao nhiêu?",metric='revenue',group_filter='sơn')=='unanswerable'

def test_future_due_boundary(small_shop):
 catalog,engine=small_shop
 engine.connection.execute("CREATE TEMP TABLE v_due(order_id VARCHAR,due_date DATE,term_days INTEGER)")
 engine.connection.execute("INSERT INTO v_due VALUES ('D1','2026-03-31',50),('D2','2026-04-01',52),('D3','2026-04-02',52)")
 engine.tables.add('v_due')
 assert ask(catalog,engine,"Tới hôm nay, mấy đơn bán chịu ngày phải trả muộn hơn hôm nay?")==1

def test_payable_is_not_tradein_fee(small_shop):
 catalog,engine=small_shop
 engine.connection.execute("CREATE OR REPLACE TEMP TABLE v_order_money(order_id VARCHAR,order_date TIMESTAMP,status_rank INTEGER,due DECIMAL,tradein DECIMAL)")
 engine.connection.execute("INSERT INTO v_order_money VALUES ('D1','2026-02-05',1,800,200),('D2','2026-02-08',1,400,0)")
 engine.tables.add('v_order_money')
 assert ask(catalog,engine,"Tháng 2/2026 khách phải trả tổng cộng bao nhiêu sau khi trừ tiền thu máy cũ?")==1200
 assert ask(catalog,engine,"Tháng 2/2026 trung bình mỗi đơn khách phải trả bao nhiêu sau khi trừ máy cũ thu lại?")==600

def test_repair_exclusion_and_weekday(tmp_path):
 rows=[("03/03/2026","Lead 50B2-111.22","vá xe","Đức",100000,""),
       ("10/03/2026","Lead 50B2-111.22","vá xe","Đức",200000,""),
       ("17/03/2026","Lead 50B2-111.22","vá xe","Đức",0,"bảo hành")]
 catalog,engine=build(tmp_path,{"so_sua_xe.csv":(BOOK,BOOK_DESC,[dict(zip(BOOK,r)) for r in rows]),
    "danh_sach_tho.csv":(ROSTER,ROSTER_DESC,[{"Họ tên":"Lương Chi Đức","Ngày vào làm":"01/01/2024","Hoa hồng (%)":25}])},domain='vehicle_repair')
 try:
  assert ask(catalog,engine,"Tháng 3/2026 không tính xe quay lại bảo hành, bình quân 1 phiếu sửa hết bao nhiêu tiền?")==150000
  assert ask(catalog,engine,"Tháng 3/2026 trung bình mỗi thứ Ba tiệm sửa mấy lượt xe?")==0.6
  assert ask(catalog,engine,"Tháng 3/2026 thợ Đức lãnh bao nhiêu tiền huê hồng?")==75000
  assert ask(catalog,engine,"Tháng 3/2026 ai là thợ nhiệt tình nhất?")=='unanswerable'
 finally:engine.close()

def test_named_installment_balance_and_due(small_shop):
 catalog,engine=small_shop
 engine.connection.execute("CREATE TEMP TABLE v_installments(order_id VARCHAR,due_date TIMESTAMP,amount_due DECIMAL,paid_amount DECIMAL,paid_at TIMESTAMP)")
 engine.connection.execute("INSERT INTO v_installments VALUES ('D1','2026-03-01',100,50,'2026-03-01'),('D1','2026-03-31',100,NULL,NULL),('D1','2026-04-30',100,NULL,NULL),('D2','2026-03-01',500,NULL,NULL)")
 engine.tables.add('v_installments')
 # Names intentionally contain two words, like real contract customers.
 engine.connection.execute("UPDATE v_orders_all SET customer_name='Chị Lan (0901234567)' WHERE order_id='D1'")
 engine.connection.execute("UPDATE v_orders_all SET customer_name='Anh Minh' WHERE order_id='D2'")
 assert ask(catalog,engine,"Đến hôm nay khách Chị Lan còn thiếu bao nhiêu tiền góp, tính cả kỳ chưa tới hạn?")==250
 assert ask(catalog,engine,"Hết hôm nay khách Chị Lan góp tới hạn mấy kỳ rồi, tính luôn kỳ hạn hôm nay?")==2

def test_transfer_share_is_not_cancellation_rate(small_shop):
 catalog,engine=small_shop
 for table in ['v_orders','v_orders_all','v_sales']:
  engine.connection.execute(f"UPDATE {table} SET payment_method=CASE WHEN order_id='D1' THEN 'transfer' ELSE 'cash' END")
 assert ask(catalog,engine,"Đơn chuyển khoản tháng 2/2026: doanh thu chiếm bao nhiêu % tổng doanh thu?",metric='revenue',operation='share',output_type='percent')==33.33

def test_debtors_are_not_diners(small_shop):
 catalog,engine=small_shop
 engine.connection.execute("CREATE OR REPLACE TEMP TABLE v_dining(order_id VARCHAR,guests INTEGER,area VARCHAR,table_no VARCHAR)")
 engine.connection.execute("INSERT INTO v_dining VALUES ('D1',99,NULL,NULL),('D2',77,NULL,NULL)")
 engine.tables.add('v_dining')
 engine.connection.execute("CREATE OR REPLACE TEMP TABLE v_finance AS SELECT * REPLACE(CAST(snapshot_balance AS DECIMAL(18,2)) AS snapshot_balance) FROM v_finance")
 engine.connection.execute("UPDATE v_finance SET payment_method='debt',snapshot_balance=CASE WHEN order_id='D1' THEN 50 ELSE 0 END")
 assert ask(catalog,engine,"Đến hết ngày 31/03/2026 số khách còn công nợ là bao nhiêu?")==1

def test_price_effect_uses_units_not_money(tmp_path):
 rows=[("D1","01/02/2026","Lan","Gạch A",1,"thùng",100,"Xong"),
       ("D2","02/02/2026","Minh","Gạch A",1,"thùng",200,"Xong")]
 catalog,engine=build(tmp_path,{"ban_hang.csv":(SALES,SALES_DESC,[dict(zip(SALES,r)) for r in rows])})
 try:
  assert ask(catalog,engine,"Gạch A lên 200đ từ 02/02/2026, số thùng bán 1 ngày từ hôm đó so với 1 ngày trước tăng bao nhiêu %?",metric='revenue',operation='growth',output_type='percent')==0
 finally:engine.close()

def test_date_does_not_set_top_k(tmp_path):
 rows=[("03/03/2026","Lead 50B2-111.22","vá xe","Đức",100000,""),
       ("04/03/2026","Lead 50B2-111.22","vá xe","An",200000,"")]
 catalog,engine=build(tmp_path,{"so_sua_xe.csv":(BOOK,BOOK_DESC,[dict(zip(BOOK,r)) for r in rows])},domain='vehicle_repair')
 try:
  assert ask(catalog,engine,"3/26 thợ mô làm ra nhiều tiền nhất?")=='An'
 finally:engine.close()

def test_repayment_not_customer_payable(small_shop):
 catalog,engine=small_shop
 engine.connection.execute("CREATE OR REPLACE TEMP TABLE v_receipts(order_id VARCHAR,paid_at TIMESTAMP,amount DECIMAL)")
 engine.connection.execute("INSERT INTO v_receipts VALUES ('D1','2026-03-01',35)")
 engine.tables.add('v_receipts')
 engine.connection.execute("UPDATE v_finance SET payment_method='debt'")
 assert ask(catalog,engine,"Tháng 3/2026 khách trả bớt nợ mua chịu được bao nhiêu tiền?")==35

def test_trend_checks_chronology_not_mention_order(tmp_path):
 rows=[("D1","01/01/2026","Lan","Gạch A",1,"thùng",100,"Xong"),
       ("D2","01/02/2026","Minh","Gạch A",1,"thùng",200,"Xong"),
       ("D3","01/03/2026","Minh","Gạch A",1,"thùng",300,"Xong")]
 catalog,engine=build(tmp_path,{"ban_hang.csv":(SALES,SALES_DESC,[dict(zip(SALES,r)) for r in rows])})
 try:
  assert ask(catalog,engine,"Doanh thu T2/2026 cao hơn T1/2026, rồi T3/2026 lại cao hơn T2/2026, đúng vậy không?",metric='revenue') is True
  assert ask(catalog,engine,"Doanh thu tháng 2/2026, tháng 1/2026 và tháng 3/2026 lần lượt bao nhiêu?",metric='revenue',operation='series',output_type='list')==[200,100,300]
 finally:engine.close()

def test_verifier_receives_source_not_just_sql(small_shop):
 import json
 from datamate.verify import build_verification_context,verify_messages
 catalog,engine=small_shop
 context=build_verification_context(catalog,engine)
 assert context["product_name"]["values"]==["Gạch A"]
 assert context["product_name"]["truncated"] is False
 q=Question(id="synthetic",business_id="shop",question="Doanh thu quan hom nay?",as_of_date="2026-03-31")
 payload=json.loads(verify_messages(q,"SELECT SUM(revenue) FROM v_orders",{"type":"number","answer":300},context)[1]["content"])
 assert payload["source_context"]==context
 assert "reference" not in payload and "gold" not in payload

@pytest.mark.parametrize("amount",["1.500d","1.500đ","1,500 VND","1tr5","0,0015tr"])
def test_daily_threshold_preserves_currency_tail(amount):
 t=ground_frame(QueryFrame(**FRAME),"Tháng 2/2026 bao nhiêu ngày doanh thu từ "+amount+" trở lên?")
 assert (t.operation,t.group_by,t.threshold)==("threshold_count","day",1500000 if amount=="1tr5" else 1500)

def test_quantity_top_keeps_unit_between_adjective_and_superlative():
 f=QueryFrame(**(FRAME|{"metric":"quantity","operation":"top","group_by":"product","group_filter":"gạo","unit_filter":"kg","output_type":"string"}))
 t=ground_frame(f,"Tháng 2/2026 nhóm gạo có mặt hàng nào bán được nhiều kg nhất?")
 assert (t.operation,t.group_by,t.output_type)==("top","product","string")

def test_quality_is_not_a_sales_ranking(small_shop):
 catalog,engine=small_shop
 assert ask(catalog,engine,"Mặt hàng có chất lượng tốt nhất tháng 2/2026 là gì?",metric="other",operation="top",group_by="product",output_type="string")=="unanswerable"

def test_debt_age_not_purchase_frequency():
 t=ground_frame(QueryFrame(**FRAME),"Tiền khách kòn nợ ở những đơn đặt hơn 7 ngày chưa trả hết là bao nhiêu?")
 assert t.group_by=="none"
 assert t.metric=="other"
 assert "còn nợ" in normalize_question("khách kòn nợ")

def test_cancellation_reason_after_status(small_shop):
 catalog,engine=small_shop
 assert ask(catalog,engine,"Tháng 2/2026 khách huỷ đơn chủ yếu vì sao?",metric="cancelled_orders",operation="top",output_type="string")=="unanswerable"
 assert ask(catalog,engine,"Tháng 2/2026 có bao nhiêu đơn huỷ?",metric="cancelled_orders")==1

def test_daily_threshold_does_not_read_date_as_money():
 f=QueryFrame(**(FRAME|{"metric":"revenue","operation":"threshold_count","group_by":"day","threshold":5000}))
 t=ground_frame(f,"Có bao nhiêu ngày từ 1/2/2026 đến 5/2/2026 doanh thu hơn 5000?")
 assert t.threshold==5000

def test_missing_hour_checked_before_missing_period(small_shop):
 catalog,engine=small_shop
 assert ask(catalog,engine,"Tiệm thường đông khách nhất vào mấy giờ?",metric="orders",operation="top",group_by="hour",output_type="string")=="unanswerable"

def test_chat_note_requires_actual_filter_correction():
 from datamate.verify import question_verification_context
 q=Question(id="synthetic",business_id="shop",question="Bán bao nhiêu gói z?",as_of_date="2026-03-31")
 context={"product_name":{"values":["Gạo A"],"truncated":False}}
 assert question_verification_context(q,context)==context
 assert "language_notes" in question_verification_context(q,context,chat_filter_removed=True)
 real={"product_name":{"values":["Z"],"truncated":False}}
 assert question_verification_context(q,real,chat_filter_removed=True)==real
 assert "language_notes" not in context

def test_customer_group_role_prefix_matches_recorded_value(tmp_path):
 header=SALES+["Nhóm khách hàng"]
 rows=[("D1","01/02/2026","Lan","Gạch A",1,"thùng",100,"Xong","sỉ"),
       ("D2","02/02/2026","Minh","Gạch A",2,"thùng",200,"Xong","thân thiết"),
       ("D3","03/02/2026","Hoa","Gạch A",3,"thùng",300,"Huỷ","sỉ")]
 catalog,engine=build(tmp_path,{"ban_hang.csv":(header,{**SALES_DESC,"Nhóm khách hàng":"nhóm khách"},[dict(zip(header,r)) for r in rows])})
 try:
  assert ask(catalog,engine,"Tháng 2/2026 doanh thu nhóm khách sỉ chiếm bao nhiêu % tổng doanh thu?",metric="revenue",operation="share",customer_group_filter="khách sỉ",output_type="percent")==33.33
  assert ask(catalog,engine,"Tháng 2/2026 doanh thu nhóm khách thân thiết chiếm bao nhiêu %?",metric="revenue",operation="share",customer_group_filter="nhóm khách hàng thân thiết",output_type="percent")==66.67
  assert ask(catalog,engine,"Tháng 2/2026 doanh thu nhóm khách thân thiện bao nhiêu?",metric="revenue",customer_group_filter="thân thiết")==200
  from datamate.core import UnsupportedFrame
  with pytest.raises(UnsupportedFrame):
   ask(catalog,engine,"Tháng 2/2026 doanh thu nhóm khách VIP bao nhiêu?",metric="revenue",customer_group_filter="khách VIP")
 finally:engine.close()

def test_spoken_procurement_is_not_sales(small_shop):
 catalog,engine=small_shop
 engine.connection.execute("CREATE TEMP TABLE v_purchases(supplier VARCHAR,purchase_date TIMESTAMP,cost DECIMAL,product_name VARCHAR)")
 engine.connection.execute("INSERT INTO v_purchases VALUES ('Cty A','2026-02-01',900,'Gạch A')")
 engine.tables.add("v_purchases")
 assert ask(catalog,engine,"Tháng 2/2026 quán đánh hàng về hết bao nhiêu tiền?",metric="revenue")==900
 assert ask(catalog,engine,"Tháng 2/2026 quán bán hàng được bao nhiêu tiền?",metric="revenue")==300
 assert "đánh giá hàng" in normalize_question("đánh giá hàng có chất lượng tốt")

def test_named_item_not_dropped_as_store_typo(tmp_path):
 from datamate.core import UnsupportedFrame
 rows=[("D1","01/02/2026","Lan","Cháo nghêu",2,"nồi",100,"Xong"),
       ("D2","02/02/2026","Minh","Lẩu cá",5,"nồi",200,"Xong")]
 catalog,engine=build(tmp_path,{"ban_hang.csv":(SALES,SALES_DESC,[dict(zip(SALES,r)) for r in rows])})
 try:
  with pytest.raises(UnsupportedFrame,match="entity not resolved"):
   ask(catalog,engine,"Tháng 2/2026 Cháo ngao bán được mấy nồi?",metric="quantity",product_filter="Cháo ngao",unit_filter="nồi")
  assert ask(catalog,engine,"Tháng 2/2026 Cháo nghêu bán được mấy nồi?",metric="quantity",product_filter="Cháo nghêu",unit_filter="nồi")==2
 finally:engine.close()

def test_customer_personality_not_in_transactions(small_shop):
 catalog,engine=small_shop
 assert ask(catalog,engine,"Tháng 2/2026 trong các khách hàng ai khó tính nhất?",metric="other",operation="top",group_by="customer",output_type="string")=="unanswerable"

def test_returned_dish_reasons_need_source(small_shop):
 catalog,engine=small_shop
 assert ask(catalog,engine,"Tháng 2/2026 khách trả món vì món lỗi hay vì gọi nhầm nhiều hơn?",metric="quantity",operation="top",output_type="string")=="unanswerable"

def test_verifier_sees_original_and_proposed_name():
 from datamate.verify import question_verification_context
 q=Question(id="synthetic",business_id="shop",question="Cháo ngao tháng 2/2026 bán bao nhiêu?",as_of_date="2026-03-31")
 source={"product_name":{"values":["Cháo nghêu"]}}
 ctx=question_verification_context(q,source,normalization_proposal="Cháo nghêu tháng 2/2026 bán bao nhiêu?")
 assert ctx["normalization_proposal"]["original_question"]==q.question
 assert ctx["normalization_proposal"]["proposed_question"]!="Cháo ngao tháng 2/2026 bán bao nhiêu?"
 assert "normalization_proposal" not in source

def test_credit_purchase_not_afternoon_glossary():
 from datamate.verify import question_verification_context
 source={"domain":"grocery"}
 q=Question(id="synthetic",business_id="shop",question="Thu hồi tiền nợ từ khách mua chiu tháng 2/2026?",as_of_date="2026-03-31")
 assert "debt" in question_verification_context(q,source)["language_notes"][0]
 evening=q.model_copy(update={"question":"Doanh thu ca chiều tháng 2/2026?"})
 assert "language_notes" not in question_verification_context(evening,source)
 assert "language_notes" not in source

def test_abbreviated_month_span():
 periods=resolve_periods("Th9/2025 đến hết th11/2025",dt.date(2026,8,31))
 assert [p.start.month for p in periods]==[9,10,11]

def test_rewrite_date_padding_only():
 from datamate.repair import acceptable
 names={"mặt hàng":["Giá đỡ điện thoại để bàn"]}
 assert acceptable("Tháng 1/2026 bán Kệ điện thoại để bàn bao nhiêu tiền?","Tháng 01/2026 bán Giá đỡ điện thoại để bàn bao nhiêu tiền?",names)
 assert not acceptable("Tháng 1/2026 bán 128 máy?","Tháng 01/2026 bán 256 máy?",{})
 assert not acceptable("Ngày 3/4/2026 bán bao nhiêu?","Ngày 04/03/2026 doanh thu bao nhiêu?",{})
 assert not acceptable("Mã 001 bán bao nhiêu?","Mã 1 bán được bao nhiêu?",{})

def test_used_phone_phrase_keeps_used_filter(tmp_path):
 header=SALES+["Nhóm hàng"]
 rows=[("D1","01/02/2026","Lan","Phone A",2,"cái",200,"Xong","máy mới"),
       ("D2","02/02/2026","Minh","Phone B",3,"cái",150,"Xong","máy cũ"),
       ("D3","03/02/2026","Hoa","Cable",10,"cái",50,"Xong","phụ kiện")]
 c,e=build(tmp_path,{"ban_hang.csv":(header,{**SALES_DESC,"Nhóm hàng":"nhóm hàng"},[dict(zip(header,r)) for r in rows])},domain="phone_store")
 try:
  assert ask(c,e,"Điện thoại cũ tháng 2/2026 bán ra mấy máy?",metric="quantity",product_filter="Điện thoại cũ")==3
  assert ask(c,e,"Tháng 2/2026 bán bao nhiêu chiếc điện thoại, cả máy mới và máy cũ?",metric="quantity")==5
  assert ask(c,e,"Tháng 2/2026 không tính phụ kiện, trung bình 1 máy đt bán được bao nhiêu tiền?",metric="revenue",operation="average_order",product_filter="máy đt",group_filter="phụ kiện")==70
  assert "doanh thu" not in normalize_question("trung bình một máy dt")
 finally:e.close()

def test_repeat_vehicle_span_and_mechanic_binding(tmp_path):
 rows=[("03/01/2026","Lead 50B2-111.22","vá xe","Đức",100000,""),
       ("10/03/2026","Lead 50B2-111.22","vá xe","Đức",200000,""),
       ("17/02/2026","Lead 50B2-333.44","vá xe","Đức",300000,"")]
 c,e=build(tmp_path,{"so_sua_xe.csv":(BOOK,BOOK_DESC,[dict(zip(BOOK,r)) for r in rows]),
    "danh_sach_tho.csv":(ROSTER,ROSTER_DESC,[{"Họ tên":"Lương Chi Đức","Ngày vào làm":"01/01/2024","Hoa hồng (%)":25}])},domain="vehicle_repair")
 try:
  assert ask(c,e,"Th1/2026 đến hết th3/2026 có bao nhiêu xe khách quen, tức xe vô sửa từ 2 lần trở lên?",metric="other",operation="threshold_count",threshold=2)==1
  from datamate.verify import build_verification_context
  binding=build_verification_context(c,e)["mechanic_bindings"]
  assert binding==[{"mechanic_key":"duc","full_name":"Lương Chi Đức","ticket_names":["Đức"]}]
 finally:e.close()

def test_baby_attribute_scope_and_nonunique_alias(tmp_path):
 header=["Tên hàng","Nhóm","Hãng","Số","Size","ĐVT","Tên gọi khác"]
 products=[("Sữa A số 2","sữa bột","A",2,None,"lon",""),
           ("Sữa B số 2","sữa bột","B",2,None,"lon",""),
           ("Sữa C pha sẵn","sữa pha sẵn","C",2,None,"lon",""),
           ("Tã A M","bỉm tã","A",None,"M","bịch","bỉm size M"),
           ("Tã B M","bỉm tã","B",None,"M","bịch","bỉm size M"),
           ("Áo C M","quần áo","C",None,"M","cái",""),
           ("Tã A L","bỉm tã","A",None,"L","bịch","")]
 quantities=[5,2,100,3,4,100,10]
 sales=[(str(i),"02/02/2026","Lan",p[0],q,p[5],q*10,"Xong") for i,(p,q) in enumerate(zip(products,quantities))]
 desc={"Tên hàng":"tên hàng","Nhóm":"nhóm hàng","Hãng":"hãng","Số":"số sữa","Size":"size","ĐVT":"đơn vị cơ bản","Tên gọi khác":"tên gọi khác"}
 c,e=build(tmp_path,{"danh_muc.csv":(header,desc,[dict(zip(header,p)) for p in products]),"ban_hang.csv":(SALES,SALES_DESC,[dict(zip(SALES,r)) for r in sales])},domain="mother_baby")
 try:
  assert ask(c,e,"Tháng 2/2026 sữa bột số 2 của hãng nào bán nhiều lon nhất?",metric="quantity",operation="top",group_by="product",product_filter="sữa bột số 2",output_type="string")=="A"
  assert ask(c,e,"Tháng 2/2026 sữa bột số 2 bán bao nhiêu lon?",metric="quantity")==7
  assert ask(c,e,"Tháng 2/2026 bỉm size M bán được mấy bịch, gộp mọi hãng?",metric="quantity",unit_filter="bịch",size_filter="M")==7
 finally:e.close()

def test_accessory_variant_does_not_become_phone_brand(tmp_path):
 from datamate.core import UnsupportedFrame
 header=SALES+["Nhóm hàng"]
 rows=[("D1","01/02/2026","Lan","Samsung A36 128GB",1,"cái",9000,"Xong","máy mới"),
       ("D2","02/02/2026","Minh","Củ sạc nhanh 25W Samsung",2,"cái",300,"Xong","phụ kiện")]
 c,e=build(tmp_path,{"ban_hang.csv":(header,{**SALES_DESC,"Nhóm hàng":"nhóm hàng"},[dict(zip(header,r)) for r in rows])},domain="phone_store")
 try:
  with pytest.raises(UnsupportedFrame,match="entity not resolved"):
   ask(c,e,"Tháng 2/2026 mặt hàng Cốc sạc nhanh 25W Samsung thu về bao nhiêu tiền?",metric="revenue",product_filter="Cốc sạc nhanh 25W Samsung")
  assert ask(c,e,"Tháng 2/2026 Củ sạc nhanh 25W Samsung thu về bao nhiêu tiền?",metric="revenue",product_filter="Củ sạc nhanh 25W Samsung")==300
 finally:e.close()

def test_rewrite_month_list_can_repeat_month_prefix():
 from datamate.repair import acceptable
 assert acceptable("Giữa tháng 6/2025, 7/2025 và 8/2025 tháng nào cao nhất?",
                   "So sánh doanh thu tháng 6/2025, tháng 7/2025 và tháng 8/2025, tháng nào cao nhất?",{})
 assert not acceptable("Giữa tháng 6/2025, 7/2025 và 8/2025 tháng nào cao nhất?",
                       "Tháng 6/2025, tháng 7/2025 và tháng 9/2025 tháng nào cao nhất?",{})

def test_finance_verifier_receives_exact_rulebook_section():
 from datamate.verify import question_verification_context
 q=Question(id="synthetic",business_id="shop",question="Thu nợ được bao nhiêu?",as_of_date="2026-03-31")
 source={"domain":"grocery"}
 rules="## 4. Công nợ\nThu nợ không gồm tiền trả cùng ngày mua.\n## 5. Kho\nTồn kho."
 c=question_verification_context(q,source,sql="SELECT SUM(amount) FROM v_receipts",rulebook_text=rules)
 assert "không gồm tiền trả cùng ngày mua" in c["finance_rulebook"]
 assert "Tồn kho" not in c["finance_rulebook"]
 assert "finance_rulebook" not in question_verification_context(q,source,sql="SELECT SUM(revenue) FROM v_sales",rulebook_text=rules)
