import pytest

from datamate.contracts import validate_plan_intent
from datamate.types import Question, SQLPlan


def plan(kind):
    return SQLPlan(action='query', sql='SELECT 1', answer_type=kind, shape='scalar', rounding='none', date_granularity='day', reason='x')


def question(text):
    return Question(id='q', business_id='b', question=text, as_of_date='2026-08-20')


@pytest.mark.parametrize('text', ['những sp nào đang còn hàng có hạn dùng từ 20/05/2026 đến hết 18/08/2026',
                                  'khách nào mua nhiều nhất tháng 7', 'ai bán được nhiều tiền nhất tháng 7', 'tháng 7 ngày nào bán đắt nhất'])
def test_name_or_day_questions_are_not_answered_with_a_number(text):
    with pytest.raises(ValueError):
        validate_plan_intent(plan('number'), question(text))


@pytest.mark.parametrize('text', ['có bao nhiêu khách nào còn nợ', 'con số khách nào còn nợ nhiều', 'khách nào mua từ 3 đơn chiếm nhiêu % số khách',
                                  'ngày nào bán nhiều nhất và bán được bao nhiêu tiền'])
def test_counts_and_shares_of_named_groups_are_numbers(text):
    validate_plan_intent(plan('percent' if '%' in text else 'number'), question(text))


# Core question-to-plan contract rules enforced by the validator.
def test_percent_question_needs_percent_plan():
    with pytest.raises(ValueError, match='phần trăm'):
        validate_plan_intent(plan('number'), question('Tháng 7 doanh thu tăng bao nhiêu phần trăm so với tháng 6?'))
    validate_plan_intent(plan('percent'), question('Tháng 7 doanh thu tăng bao nhiêu phần trăm so với tháng 6?'))


def test_which_month_needs_month_date():
    with pytest.raises(ValueError, match='tháng nào'):
        validate_plan_intent(plan('number'), question('Năm 2025 tháng nào có doanh thu cao nhất?'))
    month = SQLPlan(action='query', sql='SELECT 1', answer_type='date', shape='scalar', rounding='none', date_granularity='month', reason='x')
    validate_plan_intent(month, question('Năm 2025 tháng nào có doanh thu cao nhất?'))


def test_weekday_filter_only_when_asked():
    weekday = SQLPlan(action='query', sql='SELECT 1 WHERE isodow(d)=7', answer_type='number', shape='scalar', rounding='none', date_granularity='day', reason='x')
    with pytest.raises(ValueError, match='isodow'):
        validate_plan_intent(weekday, question('Doanh thu T7/2026 là bao nhiêu?'))
    validate_plan_intent(weekday, question('Doanh thu các ngày Chủ nhật tháng 7/2026 là bao nhiêu?'))


def test_employee_id_must_appear_in_sql():
    with pytest.raises(ValueError, match='mã nhân viên'):
        validate_plan_intent(plan('number'), question('Tháng 7 nhân viên NV03 bán được bao nhiêu?'))
    with_id = SQLPlan(action='query', sql="SELECT 1 WHERE employee_id='nv03'", answer_type='number', shape='scalar', rounding='none', date_granularity='day', reason='x')
    validate_plan_intent(with_id, question('Tháng 7 nhân viên NV03 bán được bao nhiêu?'))
