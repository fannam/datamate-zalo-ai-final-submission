import pytest
from datamate.intent import ground_frame,normalize_question,weekday_requested
from datamate.types import QueryFrame
from datamate.parsing import fold


def frame(**changes):
    data=dict(metric='revenue',operation='value',group_by='none',product_filter='',customer_filter='',group_filter='',customer_group_filter='',unit_filter='',payment_filter='',channel_filter='',size_filter='',k=1,threshold=0,threshold_operator='>=',weekday_filter=7,output_type='number',reason='test')
    return QueryFrame(**(data|changes))


def test_weekday_must_be_explicit_and_employee_is_preserved():
    result=ground_frame(frame(),'Doanh thu NV003 th7/2026 bao nhiêu?')
    assert result.weekday_filter==0 and result.employee_filter=='NV003'
    assert weekday_requested('Thứ Bảy tháng 7 bán bao nhiêu?')==6
    assert weekday_requested('Doanh thu Chủ nhật?')==7
    assert weekday_requested('Doanh thu từ đầu tháng đến nay?')==0
    assert weekday_requested('doanh thu tu khach hang trong thang nay')==0


def test_question_controls_percentage_month_and_series_types():
    assert ground_frame(frame(operation='difference'),'Ca chiều hơn ca sáng mấy %?').operation=='growth'
    assert ground_frame(frame(),'Trong 3 tháng, tháng nào doanh thu cao nhất?').output_type=='date'
    assert ground_frame(frame(),'Doanh thu 3 tháng 1/2026,2/2026,3/2026 lần lượt?').output_type=='list'


def test_unasked_payment_and_channel_constraints_are_removed():
    result=ground_frame(frame(payment_filter='ewallet',channel_filter='cửa hàng'),'Hôm qua cửa hàng bán được bao nhiêu tiền?')
    assert result.channel_filter==result.payment_filter==''
    result=ground_frame(frame(payment_filter='ewallet',channel_filter='điện thoại'),'Doanh thu đơn bán qua điện thoại?')
    assert result.payment_filter=='' and result.channel_filter=='phone'


def test_quantity_and_customer_ranking_are_not_reinterpreted_as_series_or_average():
    result=ground_frame(frame(metric='quantity',product_filter='cây',output_type='list',group_by='day'),'7 ngày gần nhất bán được tổng cộng mấy cây?')
    assert result.product_filter=='' and result.unit_filter=='cây' and result.output_type=='number'
    result=ground_frame(frame(operation='average_order',group_by='customer',k=3,output_type='list'),'3 khách có tổng tiền mua cao nhất tuần này là ai?')
    assert result.operation=='top' and result.group_by=='customer'


from datamate.intent import normalize_question, payments_in, channels_in, threshold_from_text, wants_percent, superlative, asks_table
from datamate.output import suspicious


def test_telex_typos_and_chat_abbreviations_are_repaired_without_touching_names():
    text = normalize_question('ngày naof trong thangs 6 dthu cao nhaats, so 7 ngay vs 7 ngay truwowcs')
    assert 'nao' in text and 'thang 6' in text and 'doanh thu' in text and 'nhat' in text and 'truoc' in text
    assert 'Plusssz Max' in normalize_question('Plusssz Max Multivitamin lên giá')
    assert normalize_question('so vs 7 ngay ngay trc') == 'so voi 7 ngay ngay truoc'


def test_order_phrase_is_not_wednesday():
    assert weekday_requested('Ghi 3 số theo thứ tự thời gian.') == 0
    assert weekday_requested('ghi lan luot theo thu tu do') == 0
    assert weekday_requested('Doanh thu các ngày thứ Tư tháng 5?') == 3
    assert weekday_requested('doanh thu thu 4 thang 5') == 3


def test_percent_and_payment_detection_use_word_boundaries():
    assert wants_percent('thang 8/2025 dthu thay doi bao nhiu % so voi thang lien trc')
    assert payments_in('Doanh thu hôm qua là bao nhiêu?') == []
    assert payments_in('dthu các đơn trả bằng ck chiếm bnhiu %') == ['transfer']
    assert payments_in('doanh thu các đơn trả bằng tiền tươi') == ['cash']
    assert payments_in('So doanh thu COD với doanh thu tiền mặt') == ['cod', 'cash']
    assert channels_in('dthu cac don ban tai quay') == ['store']


def test_top_needs_superlative_and_years_are_not_k():
    result = ground_frame(frame(operation='top', group_by='product', k=3, output_type='list', metric='quantity'), 'Tháng 1/2026 mặt hàng nào bán chạy nhất?')
    assert result.k == 1 and result.output_type == 'string'
    result = ground_frame(frame(operation='top', group_by='day', k=7, output_type='list'), 'xem giup 7 ngay gan nhat cua hang ban dc bnhiu tien')
    assert result.operation == 'value' and result.output_type == 'number'
    assert superlative('5 best seller thang 2/2026') and not superlative('7 ngày gần nhất bán được mấy đơn')


def test_table_only_when_question_asks_for_it():
    result = ground_frame(frame(group_by='product', output_type='table', group_filter='nhóm xi măng'), 'thang 7/2025 cac mat hang nhom xi mang thu duoc tong cong bao nhieu tien')
    assert result.output_type == 'number'
    assert asks_table('cho bảng 3 khách còn nợ nhiều nhất, kèm số tiền') and not asks_table('trả bằng tiền mặt bao nhiêu')


def test_explicit_count_thresholds_and_group_shares():
    assert threshold_from_text('khách mua từ 4 đơn trở lên') == (4.0, '>=', 'don')
    assert threshold_from_text('ít nhất 20 đơn')[:2] == (20.0, '>=')
    result = ground_frame(frame(operation='share', group_by='customer', output_type='percent', metric='purchase_days'), 'Tháng 6/2026 khách mua từ 4 đơn trở lên chiếm bao nhiêu % số khách đã mua nhỉ?')
    assert (result.operation, result.metric, result.threshold, result.threshold_operator) == ('threshold_share', 'orders', 4.0, '>=')
    result = ground_frame(frame(operation='value', product_filter='trà sữa', group_filter='nhóm trà sữa', output_type='percent'), 'Tỉ trọng của nhóm trà sữa trong dthu tháng 5/2025 là bao nhiêu %?')
    assert result.operation == 'share' and result.product_filter == ''


def test_zero_and_empty_results_are_flagged():
    assert suspicious({'type': 'number', 'answer': 0}) == 'zero_result'
    assert suspicious({'type': 'list', 'answer': [0, 0]}) == 'all_zero_list'
    assert suspicious({'type': 'list', 'answer': []}) == 'empty_list'
    assert suspicious({'type': 'number', 'answer': -5}) is None


def test_named_rankings_follow_rulebook_definitions():
    from datamate.intent import named_ranking
    cases = {
        'Tháng 8 khách nào trung thành nhất?': ('purchase_days', 'top', 'customer'),
        'Ai là khách sộp nhất tháng này?': ('revenue', 'average_order', 'customer'),
        'Khách hay đổi ý nhất tháng 8 là ai?': ('cancelled_orders', 'top', 'customer'),
        'Nhân viên nào chăm chỉ nhất quý 3?': ('purchase_days', 'top', 'employee'),
        'Ngày nào đắt khách nhất tháng 8?': ('orders', 'top', 'day'),
        'Giờ cao điểm của quán tháng 8 là mấy giờ?': ('orders', 'top', 'hour'),
    }
    for text, expected in cases.items():
        assert named_ranking(fold(text)) == expected, text
    for text in ['Doanh thu tháng 8 bao nhiêu?', 'Thứ mấy đông khách nhất?', 'Khách nào mua nhiều tiền nhất?']:
        assert named_ranking(fold(text)) is None, text


def test_named_rankings_override_planner_frame():
    result = ground_frame(frame(output_type='list'), 'Giờ cao điểm tháng 8/2026 là mấy giờ?')
    assert (result.metric, result.operation, result.group_by, result.output_type, result.k) == ('orders', 'top', 'hour', 'number', 1)
    result = ground_frame(frame(), 'Trung bình mỗi thứ Bảy tháng 8/2026 quán bán được bao nhiêu tiền?')
    assert (result.operation, result.weekday_filter) == ('daily_average', 6)


def test_spoken_and_abbreviated_phrasings():
    from datamate.intent import junk_filter
    assert fold(normalize_question('Hq dt bn?')) == 'hom qua doanh thu bao nhieu?'
    assert 'doanh thu' not in fold(normalize_question('tháng này bán được mấy dt Samsung?'))   # counting phones
    assert fold(normalize_question('thg 9 tb mỗi ngày bán dc bn, nói dzậy hông')) == 'thang 9 trung binh moi ngay ban duoc bao nhieu, noi vay khong'
    assert channels_in('Tháng 2 ngày nào số tiền thu về cao nhất tại cửa hàng?') == []          # location, not a channel
    assert channels_in('So doanh thu bán qua điện thoại với bán tại quầy') == ['phone', 'store']
    for value in ['cái hàng', 'hàng nào', 'cùng hàng', 'người mua']:
        assert junk_filter(value), value
    assert not junk_filter('Bia Tiger lon')


def test_pronoun_no_after_revenue_is_not_debt_collection():
    import re
    from datamate.parsing import fold as f
    text = f(normalize_question('tháng 12 có nhiêu ngày mà doanh thu nó trên 12.000.000 đồng hông?'))
    assert not re.search(r'(?<!doanh )thu no', text)
    assert re.search(r'(?<!doanh )thu no', f('tháng 12 thu nợ được bao nhiêu'))


def test_buyer_is_customer():
    assert fold(normalize_question('có bao nhiêu người mua còn nợ cửa hàng')) == 'co bao nhieu khach con no cua hang'


@pytest.mark.parametrize('text,expected', [
    ('ngay nao ban dat nhiet nhi?', 'ngay nao ban dat nhat nhi?'),
    ('thang 3 ngay nao ban duoc nhieu tienf nhit?', 'thang 3 ngay nao ban duoc nhieu tien nhat?'),
    ('khách nào mua nhiều nhứt', 'khách nào mua nhiều nhất'),
    ('nhiệt độ cao', 'nhiệt độ cao'),   # 'nhiệt' only after an adjective of amount is 'nhất'
])
def test_mistyped_superlative_is_nhat(text, expected):
    assert normalize_question(text) == expected


@pytest.mark.parametrize('question,k', [('liet ke 5 mang hangf ban chay nhat thangs nay', 5), ('top 3 sp ban chay nhat thang 4', 3), ('5 bua nao ban nhieu tien nhat thang 7', 5)])
def test_top_k_is_read_from_the_question(question, k):
    frame = QueryFrame(metric='quantity', operation='top', group_by='product', product_filter='', customer_filter='', group_filter='', customer_group_filter='',
                       unit_filter='', payment_filter='', channel_filter='', size_filter='', k=1, threshold=0, threshold_operator='>=', weekday_filter=0,
                       output_type='string', reason='t')
    assert ground_frame(frame, question).k == k
