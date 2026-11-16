import asyncio
import json

import pytest

from datamate.config import ModelSettings
from datamate.runtime import run_questions
from datamate.types import Question, QueryFrame, SQLPlan
from datamate.cli import validate_submission


def test_harness_repairs_sql_without_reading_gold_and_resumes(tmp_path, monkeypatch):
    split = tmp_path / 'train'
    shop = split / 'data' / 'shop'
    shop.mkdir(parents=True)
    (shop / 'README.md').write_text('Dữ liệu có đến hết 31/01/2026.')
    (shop / 'ban_hang.csv').write_text('Mã đơn,Ngày,Tên hàng,Số lượng,Thành tiền,Trạng thái\nA,01/01/2026,Trà,1,150,Xong\n')
    (split / 'rulebook.md').write_text('Doanh thu bỏ đơn huỷ và trả.')
    (split / 'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')
    # Inference must succeed even when the adjacent gold file is unreadable JSON.
    (split / 'answers.jsonl').write_text('THIS MUST NEVER BE READ BY INFERENCE')

    class FakeProvider:
        total_calls = 0

        def __init__(self, settings):
            self.settings = settings

        async def generate(self, messages, schema):
            FakeProvider.total_calls += 1
            if schema is QueryFrame:
                parsed = QueryFrame(metric='other', operation='other', group_by='none', product_filter='', customer_filter='', group_filter='', customer_group_filter='', unit_filter='', payment_filter='', channel_filter='', size_filter='', k=1, threshold=0, threshold_operator='>=', weekday_filter=0, output_type='number', reason='specialist')
            else:
                sql = 'SELECT SUM(revenue) FROM nonexistent' if FakeProvider.total_calls == 2 else 'SELECT SUM(revenue) FROM v_orders'
                if FakeProvider.total_calls == 3:
                    assert 'Lỗi SQL/validator local' in messages[-1]['content']
                parsed = SQLPlan(action='query', sql=sql, answer_type='number', shape='scalar', rounding='money', date_granularity='day', reason='sum')
            return parsed, {'content': parsed.model_dump_json(), 'usage': {}, 'thinking': None}

        async def close(self):
            pass

    monkeypatch.setattr('datamate.runtime.Provider', FakeProvider)
    settings = ModelSettings(base_url='https://example.invalid/v1', model='fake', api_key='test-secret')
    questions = [Question(id='one', business_id='shop', question='Số đo nghiệp vụ tháng 1/2026?', as_of_date='2026-01-31')]
    out = tmp_path / 'run'
    result = asyncio.run(run_questions(split, questions, out, settings, tmp_path / 'cache', role_inference='off'))
    assert result['complete'] and FakeProvider.total_calls == 3
    assert json.loads((out / 'submission.jsonl').read_text()) == {'id': 'one', 'type': 'number', 'answer': 150}
    trace = json.loads((out / 'logs' / 'one.json').read_text())
    assert trace['state'] == 'completed' and 'error' in trace['executions'][0]
    assert 'test-secret' not in (out / 'manifest.json').read_text()
    assert validate_submission(out / 'questions.jsonl', out / 'submission.jsonl', True)['missing'] == 0
    asyncio.run(run_questions(split, questions, out, settings, tmp_path / 'cache', resume=True, role_inference='off'))
    assert FakeProvider.total_calls == 3
    (shop / 'README.md').write_text('Changed source')
    with pytest.raises(ValueError, match='input_hash'):
        asyncio.run(run_questions(split, questions, out, settings, tmp_path / 'cache', resume=True, role_inference='off'))



def test_repair_shop_without_sales_source_uses_planner_and_domain_compiler(tmp_path, monkeypatch):
    from test_domains import REPAIR
    split = tmp_path / 'public'
    shop = split / 'data' / 'garage'
    shop.mkdir(parents=True)
    for name, content in REPAIR.items():
        (shop / name).write_text(content)
    (split / 'rulebook.md').write_text('Sửa xe.')
    (split / 'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')

    class FakeProvider:
        def __init__(self, settings):
            self.settings = settings

        async def generate(self, messages, schema):
            from datamate.roles import ShopRoles
            if schema is ShopRoles:
                parsed = ShopRoles(domain='vehicle_repair', assignments=[])
                return parsed, {'content': parsed.model_dump_json(), 'usage': {'total_tokens': 7}, 'thinking': None}
            assert schema is QueryFrame, 'domain question must not reach the SQL specialist'
            parsed = QueryFrame(metric='other', operation='other', group_by='none', product_filter='', customer_filter='', group_filter='', customer_group_filter='', unit_filter='', payment_filter='', channel_filter='', size_filter='', k=1, threshold=0, threshold_operator='>=', weekday_filter=0, output_type='percent', reason='x')
            return parsed, {'content': parsed.model_dump_json(), 'usage': {}, 'thinking': None}

        async def close(self):
            pass

    monkeypatch.setattr('datamate.runtime.Provider', FakeProvider)
    settings = ModelSettings(base_url='https://example.invalid/v1', model='fake', api_key='k')
    questions = [Question(id='w', business_id='garage', question='Tỉ lệ bảo hành tháng 8/2026 là bao nhiêu?', as_of_date='2026-08-31')]
    result = asyncio.run(run_questions(split, questions, tmp_path / 'run', settings, tmp_path / 'cache', specialist='off'))
    assert result['complete']
    assert json.loads((tmp_path / 'run' / 'submission.jsonl').read_text()) == {'id': 'w', 'type': 'percent', 'answer': 40}


def test_rewrite_repair_resolves_misspelled_name_and_keeps_numbers(tmp_path, monkeypatch):
    from datamate.repair import Rewrite, acceptable, unresolved_value
    assert unresolved_value('entity not resolved: co ca co la 330ml') == 'co ca co la 330ml'
    names = {'mặt hàng': ['Coca-Cola lon 330ml']}
    assert acceptable('dthu co ca co la 330ml thang 5/2026', 'Doanh thu Coca-Cola lon 330ml tháng 5/2026', names)
    assert not acceptable('dthu co ca thang 5/2026', 'Doanh thu Coca-Cola tháng 6/2026', names)       # number changed
    assert not acceptable('dthu thang 5/2026', 'dthu thang 5/2026', names)                            # nothing rewritten
    split = tmp_path / 'train'
    shop = split / 'data' / 'shop'
    shop.mkdir(parents=True)
    (shop / 'README.md').write_text('Dữ liệu có đến hết 31/01/2026.')
    (shop / 'ban_hang.csv').write_text('Mã đơn,Ngày,Tên hàng,Số lượng,Thành tiền,Trạng thái\nA,02/01/2026,Coca-Cola lon 330ml,2,20000,Hoàn thành\nB,03/01/2026,Trà đá,1,5000,Hoàn thành\n')
    (split / 'rulebook.md').write_text('x')
    (split / 'calendar.csv').write_text('date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-01-01,4,13,11,2025,false,\n')
    seen = []

    class FakeProvider:
        def __init__(self, settings):
            self.settings = settings

        async def generate(self, messages, schema):
            seen.append(schema.__name__)
            if schema is Rewrite:
                assert 'Coca-Cola lon 330ml' in messages[-1]['content']        # names from the shop's data are offered
                parsed = Rewrite(question='Doanh thu Coca-Cola lon 330ml tháng 1/2026 là bao nhiêu?')
            else:
                name = 'Coca-Cola lon 330ml' if 'Coca-Cola' in messages[-1]['content'] else 'co ca co la 330ml'
                parsed = QueryFrame(metric='revenue', operation='value', group_by='none', product_filter=name, customer_filter='', group_filter='', customer_group_filter='', unit_filter='', payment_filter='', channel_filter='', size_filter='', k=1, threshold=0, threshold_operator='>=', weekday_filter=0, output_type='number', reason='x')
            return parsed, {'content': parsed.model_dump_json(), 'usage': {}, 'thinking': None}

        async def close(self):
            pass

    monkeypatch.setattr('datamate.runtime.Provider', FakeProvider)
    settings = ModelSettings(base_url='https://example.invalid/v1', model='fake', api_key='k')
    questions = [Question(id='one', business_id='shop', question='dthu co ca co la 330ml thang 1/2026 bn', as_of_date='2026-01-31')]
    out = tmp_path / 'run'
    asyncio.run(run_questions(split, questions, out, settings, tmp_path / 'cache', role_inference='off', specialist='off', repair='rewrite'))
    assert json.loads((out / 'submission.jsonl').read_text()) == {'id': 'one', 'type': 'number', 'answer': 20000}
    trace = json.loads((out / 'logs' / 'one.json').read_text())
    assert trace['state'] == 'completed_repair' and seen == ['QueryFrame', 'Rewrite', 'QueryFrame']
