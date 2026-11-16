import asyncio
import json

import pytest

from datamate.config import ModelSettings
from datamate.runtime import run_questions
from datamate.types import Question, QueryFrame
from datamate.verify import POLICY, Verdict, vetoed, year_mismatch

SQL = "SELECT COUNT(DISTINCT order_id) AS value FROM v_orders WHERE order_date>=TIMESTAMP '2025-01-13' AND order_date<TIMESTAMP '2025-01-20'"


def verdict(problem='loc', answer='sai', phrase='buổi chiều', fragment=''):
    return Verdict(asked='x', computed='y', question_phrase=phrase, sql_fragment=fragment, problem=problem, verdict=answer, reason='r')


def test_veto_needs_real_evidence_and_never_uses_period_judgement():
    question = 'Tuần trước, buổi chiều đã thu về bao nhiêu bill?'
    assert vetoed(verdict(), question, SQL, POLICY)
    assert vetoed(verdict(phrase='buoi chieu'), question, SQL, POLICY)                         # accents/typing do not matter
    assert not vetoed(verdict(phrase='ca tối'), question, SQL, POLICY)                         # phrase not in the question
    assert not vetoed(verdict(fragment="status='cancelled'"), question, SQL, POLICY)          # fragment not in the SQL
    assert vetoed(verdict(fragment="COUNT(DISTINCT  order_id)"), question, SQL, POLICY)        # whitespace-insensitive quote
    assert not vetoed(verdict(answer='dung'), question, SQL, POLICY)
    assert not vetoed(verdict(problem='none'), question, SQL, POLICY)
    assert not vetoed(verdict(problem='ky', phrase='tuần trước'), question, SQL, POLICY)       # date arithmetic stays with temporal.py
    assert not vetoed(None, question, SQL, POLICY)
    assert not vetoed(verdict(problem='so_do', phrase='bao nhiêu bill'), question, SQL, POLICY)    # glossary-blind metric claims


def test_filters_drop_spelling_and_view_choice_complaints():
    sql = "SELECT SUM(revenue) FROM v_sales WHERE product_name IN ('Bia Tiger Bạc lon') AND norm(product_group)=norm('hoàn thiện')"
    assert not vetoed(verdict(phrase='Bia Tiger Bắc Lon', fragment="product_name IN ('Bia Tiger Bạc lon')"), 'tháng 1 Bia Tiger Bắc Lon bán bao nhiêu', sql, POLICY)
    assert not vetoed(verdict(phrase='nhóm Hoàn Thiên', fragment="norm('hoàn thiện')"), 'nhóm Hoàn Thiên chiếm bao nhiêu %', sql, POLICY)
    names = "SELECT COUNT(*) FROM v_orders WHERE customer_name IN ('Huỳnh Phúc Nhung','Nghiêm Huy Hằng')"
    assert vetoed(verdict(phrase='người mua Huy', fragment="customer_name IN ('Huỳnh Phúc Nhung','Nghiêm Huy Hằng')"), 'Số đơn của người mua Huy', names, POLICY)
    drugs = "SELECT SUM(revenue) FROM v_sales s WHERE EXISTS(SELECT 1 FROM v_drugs d WHERE norm(ingredient)=norm('Vitamin C'))"
    assert vetoed(verdict(phrase='kudos vitamin c 1000mg vien sui', fragment="norm(ingredient)=norm('Vitamin C')"), 'dt kudos vitamin c 1000mg vien sui tuyp', drugs, POLICY)
    price = "SELECT DISTINCT unit_price FROM v_sales_all WHERE norm(product_name)=norm('Xi măng')"
    assert not vetoed(verdict(phrase='giá bán một bao', fragment='FROM v_sales_all'), 'giá bán một bao Xi măng', price, POLICY)


@pytest.mark.parametrize('question,sql,expected', [
    ('Trong quy 2 nm 2025 co tng dt sp ko?', "WHERE order_date>=TIMESTAMP '2026-04-01' AND order_date<TIMESTAMP '2026-07-01'", True),
    ('Doanh thu tháng 12/2025?', "WHERE order_date>=TIMESTAMP '2025-12-01' AND order_date<TIMESTAMP '2026-01-01'", False),
    ('So tháng 3/2025 với tháng 3/2026', "a AS (... TIMESTAMP '2026-03-01' ...), b AS (... TIMESTAMP '2025-03-01' ...)", False),
    ('Hôm nay bán bao nhiêu?', "WHERE order_date>=TIMESTAMP '2026-04-01'", False),                 # no year asked
    ('Khách còn nợ đến hết 2025?', "SELECT SUM(balance) FROM b", False),                           # no date literal to compare
])
def test_year_mismatch(question, sql, expected):
    assert year_mismatch(question, sql) is expected


def shop(tmp_path):
    split = tmp_path / 'train'
    folder = split / 'data' / 'shop'
    folder.mkdir(parents=True)
    (folder / 'README.md').write_text('Dữ liệu có đến hết 31/01/2026.')
    (folder / 'ban_hang.csv').write_text('Mã đơn,Ngày,Tên hàng,Số lượng,Thành tiền,Trạng thái\nA,02/01/2026,Trà đá,2,10000,Hoàn thành\nB,03/01/2026,Trà đá,1,5000,Hoàn thành\n')
    (split / 'rulebook.md').write_text('x')
    (split / 'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')
    return split


FRAME = QueryFrame(metric='revenue', operation='value', group_by='none', product_filter='', customer_filter='', group_filter='', customer_group_filter='', unit_filter='', payment_filter='', channel_filter='', size_filter='', k=1, threshold=0, threshold_operator='>=', weekday_filter=0, output_type='number', reason='x')


ANSWER = {'id': 'one', 'type': 'number', 'answer': 15000}


@pytest.mark.parametrize('mode,expected', [
    ('veto', {'id': 'one', 'type': 'abstain'}),        # both calls veto
    ('veto,pass', ANSWER),                             # the second call does not confirm the veto
    ('veto,error', {'id': 'one', 'type': 'abstain'}),  # no second opinion: the first veto stands
    ('pass', ANSWER),
    ('error', ANSWER),
])
def test_runtime_verifier_vetoes_to_abstain_and_keeps_answer_on_failure(tmp_path, monkeypatch, mode, expected):
    split = shop(tmp_path)
    calls, seeds = [], []

    class FakeProvider:
        def __init__(self, settings):
            self.settings = settings

        async def generate(self, messages, schema, **options):
            calls.append((schema.__name__, options.get('thinking')))
            seeds.append(options.get('seed'))
            if schema is QueryFrame:
                return FRAME, {'content': FRAME.model_dump_json(), 'usage': {}, 'thinking': None}
            payload = json.loads(messages[-1]['content'])
            assert payload['question'] == 'tháng 1/2026 bán được bao nhiêu ly trà đá?' and 'SUM(revenue)' in payload['sql'] and payload['answer']['value'] == 15000
            step = mode.split(',')[min(sum(1 for name, _ in calls if name == 'Verdict') - 1, mode.count(','))]
            if step == 'error':
                raise TimeoutError
            check = verdict(problem='loc', phrase='trà đá', fragment='') if step == 'veto' else verdict(problem='none', answer='dung', phrase='')
            return check, {'content': check.model_dump_json(), 'usage': {}, 'thinking': 'suy luận của model'}

        async def close(self):
            pass

    monkeypatch.setattr('datamate.runtime.Provider', FakeProvider)
    settings = ModelSettings(base_url='https://example.invalid/v1', model='fake', api_key='k')
    questions = [Question(id='one', business_id='shop', question='tháng 1/2026 bán được bao nhiêu ly trà đá?', as_of_date='2026-01-31')]
    out = tmp_path / 'run'
    asyncio.run(run_questions(split, questions, out, settings, tmp_path / 'cache', role_inference='off', specialist='off', verify='check'))
    assert json.loads((out / 'submission.jsonl').read_text()) == expected
    trace = json.loads((out / 'logs' / 'one.json').read_text())
    checks = 2 if mode.startswith('veto') else 1                                               # only a veto is checked twice
    assert calls == [('QueryFrame', None)] + [('Verdict', True)] * checks                     # only the verifier thinks
    assert seeds[1:] == [42, 43][:checks]                                                     # the second call is independent
    if mode == 'error':
        assert trace['verify']['error'] == 'TimeoutError'
    else:
        assert trace['verify']['vetoed'] is (expected['type'] == 'abstain')
        assert trace['thinking'] == ['suy luận của model'] * (1 + (mode == 'veto' or mode == 'veto,pass'))  # logged as generated
    assert json.loads((out / 'manifest.json').read_text())['verify'] == 'check'
