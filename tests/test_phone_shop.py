"""Phone-store rules (rulebook §4, §5, §6 Điện thoại) on a generated shop with an independent reference.

The domain/specialty compilers decide from the question text, so a neutral 'other' frame is used here;
the live pipeline (planner frames) is measured with training_code/data_generation/synth_phone.py.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "training_code" / "data_generation"))
import synth_phone  # noqa: E402

from datamate.core import compile_frame  # noqa: E402
from datamate.engine import QueryEngine  # noqa: E402
from datamate.ingestion import prepare_shop  # noqa: E402
from datamate.output import finalize  # noqa: E402
from datamate.semantics import attach_semantics  # noqa: E402
from datamate.types import QueryFrame, Question  # noqa: E402

FRAME = dict(metric='other', operation='value', group_by='none', product_filter='', customer_filter='', group_filter='', customer_group_filter='', unit_filter='',
             payment_filter='', channel_filter='', size_filter='', k=1, threshold=0, threshold_operator='>=', weekday_filter=0, output_type='number', reason='phone')
DOMAIN = [  # questions answered by the phone/debt compilers whatever the planner's metric
    "Tháng 2/2026 tiền thu máy cũ là bao nhiêu?", "Tổng tiền khách phải trả tháng 2/2026 là bao nhiêu?", "Tháng 2/2026 có bao nhiêu đơn trả góp?",
    "Tháng 3/2026 tiền góp thu được là bao nhiêu?", "Tháng 3/2026 thu nợ được bao nhiêu tiền?", "Tính đến hết ngày 28/02/2026 khách còn nợ tổng cộng bao nhiêu?",
    "Đến hôm nay có bao nhiêu kỳ góp quá hạn?", "Tổng số tiền góp quá hạn tính đến hôm nay là bao nhiêu?", "Tháng 2/2026 khách mua chịu thêm bao nhiêu tiền?",
    "Tỉ lệ bảo hành quý 1/2026 là bao nhiêu phần trăm?", "Doanh thu máy Apple tháng 3/2026 là bao nhiêu?", "Quý 1/2026 bán được bao nhiêu máy iPhone 15?",
    "Tính đến hôm nay khách còn phải góp bao nhiêu tiền?", "Tháng 3/2026 có bao nhiêu kỳ góp đến hạn?", "Hiện có bao nhiêu khách đang trả góp?",
    "Tháng 2/2026 cửa hàng thu bao nhiêu máy cũ?", "Doanh thu tháng 2/2026 từ các đơn trả góp là bao nhiêu?", "Lãi ròng tháng 2/2026 là bao nhiêu?",
    "Tháng 2/2026 tỉ lệ đơn có thu máy cũ là bao nhiêu phần trăm?", "Tháng 2/2026 tiền thu máy cũ chiếm bao nhiêu phần trăm doanh thu?",
    "Tháng 2/2026 trung bình mỗi đơn có thu máy cũ được trừ bao nhiêu tiền?", "Quý 1/2026 máy Apple chiếm bao nhiêu phần trăm số máy bán ra?",
    "Quý 1/2026 máy Samsung nào bán chạy nhất?", "Quý 1/2026 có bao nhiêu đơn mua máy Apple?", "Tháng 2/2026 tiền trả trước của các đơn trả góp là bao nhiêu?",
    "Tính đến hôm nay khách còn nợ tiền góp bao nhiêu?", "Quý 1/2026 dòng máy nào có nhiều lượt bảo hành nhất?", "Quý 1/2026 lỗi nào bảo hành nhiều nhất?",
    "Quý 1/2026 có bao nhiêu lượt bảo hành máy iPhone 15?",
]


@pytest.fixture(scope="module")
def phone_shops(tmp_path_factory):
    root = tmp_path_factory.mktemp("phone")
    split = root / "split"
    (split / "data").mkdir(parents=True)
    shutil.copy(synth_phone.ROOT / "train" / "calendar.csv", split / "calendar.csv")
    (split / "rulebook.md").write_text("rulebook")
    data = synth_phone.generate()
    reference = {q: a for q, _, a in synth_phone.reference(*data)}
    shops = {}
    for name, write in synth_phone.layouts(*data):
        if name not in {"phone_xlsx", "phone_notes_status", "phone_tradein_line", "phone_contract_books"}:
            continue
        folder = split / "data" / name
        folder.mkdir()
        described = write(folder)
        described = described[-1] if isinstance(described, tuple) else described
        synth_phone.write_csv(folder / "chi_phi.csv", ["Ngày", "Khoản chi", "Số tiền"],
                              [{"Ngày": synth_phone.ddmmyyyy(e["date"]), "Khoản chi": e["item"], "Số tiền": e["amount"]} for e in synth_phone.EXPENSES])
        described["chi_phi.csv"] = (["Ngày", "Khoản chi", "Số tiền"], {})
        (folder / "README.md").write_text(synth_phone.readme(described))
        directory = prepare_shop(split, name, root / "cache")
        catalog = json.loads((directory / "catalog.json").read_text())
        catalog["llm_roles"] = {"domain": "phone_store", "tables": {}}
        engine = QueryEngine(directory / "store.duckdb", {t["name"] for t in catalog["tables"]})
        attach_semantics(engine, catalog)
        shops[name] = (catalog, engine)
    yield shops, reference
    for _, engine in shops.values():
        engine.close()


@pytest.mark.parametrize("layout", ["phone_xlsx", "phone_notes_status", "phone_tradein_line", "phone_contract_books"])
def test_phone_rules_match_the_reference(phone_shops, layout):
    shops, reference = phone_shops
    catalog, engine = shops[layout]
    wrong = []
    for text in DOMAIN:
        plan = compile_frame(QueryFrame(**FRAME), Question(id="x", business_id=layout, question=text, as_of_date=synth_phone.TODAY), catalog, engine)
        got = finalize(plan, *engine.query(plan.sql))["answer"] if plan.action == "query" else plan.action
        if got != reference[text]:
            wrong.append((text, got, reference[text]))
    assert not wrong, wrong


def test_gross_profit_wording_is_not_an_installment(phone_shops):
    shops, _ = phone_shops
    catalog, engine = shops["phone_xlsx"]
    plan = compile_frame(QueryFrame(**FRAME | {"metric": "gross_profit"}), Question(id="x", business_id="phone_xlsx", question="Tháng 2/2026 lời gộp được bao nhiêu?", as_of_date=synth_phone.TODAY), catalog, engine)
    assert "v_installments" not in plan.sql
