"""Mother-and-baby and repair-shop rules (rulebook §6, §7) on generated shops with independent references.

Only questions decided by the domain compilers are asked, with a neutral frame; the live pipeline is
measured with training_code/data_generation/synth_baby.py and synth_repair.py.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "training_code" / "data_generation"))
import synth_baby  # noqa: E402
import synth_phone  # noqa: E402
import synth_repair  # noqa: E402

from datamate.core import compile_frame  # noqa: E402
from datamate.engine import QueryEngine  # noqa: E402
from datamate.ingestion import prepare_shop  # noqa: E402
from datamate.output import finalize  # noqa: E402
from datamate.parsing import name_words  # noqa: E402
from datamate.semantics import attach_semantics  # noqa: E402
from datamate.types import QueryFrame, Question  # noqa: E402

FRAME = dict(metric='other', operation='value', group_by='none', product_filter='', customer_filter='', group_filter='', customer_group_filter='', unit_filter='',
             payment_filter='', channel_filter='', size_filter='', k=1, threshold=0, threshold_operator='>=', weekday_filter=0, output_type='number', reason='domain')


def build(tmp, name, write, domain):
    split = tmp / "split"
    (split / "data" / name).mkdir(parents=True, exist_ok=True)
    shutil.copy(synth_phone.ROOT / "train" / "calendar.csv", split / "calendar.csv")
    (split / "rulebook.md").write_text("rulebook")
    folder = split / "data" / name
    (folder / "README.md").write_text(synth_phone.readme(write(folder)))
    directory = prepare_shop(split, name, tmp / "cache")
    catalog = json.loads((directory / "catalog.json").read_text())
    catalog["llm_roles"] = {"domain": domain, "tables": {}}
    engine = QueryEngine(directory / "store.duckdb", {t["name"] for t in catalog["tables"]})
    attach_semantics(engine, catalog)
    return catalog, engine


def answer(catalog, engine, text, today):
    plan = compile_frame(QueryFrame(**FRAME), Question(id="x", business_id="b", question=text, as_of_date=today), catalog, engine)
    return finalize(plan, *engine.query(plan.sql))["answer"] if plan.action == "query" else plan.action


BABY = ["Tháng 2/2026 tổng tiền giảm voucher là bao nhiêu?", "Tháng 2/2026 tiền giảm nhờ dùng điểm là bao nhiêu?", "Tháng 2/2026 tổng phí ship là bao nhiêu?",
        "Tháng 2/2026 có bao nhiêu khách thành viên mới?", "Tháng 2/2026 bán được bao nhiêu bịch bỉm size M?",
        "Bỉm size L chiếm bao nhiêu phần trăm số bịch bỉm bán ra tháng 2/2026?", "Tháng 2/2026 bán được bao nhiêu hộp sữa số 2?",
        "Tháng 2/2026 có bao nhiêu hoá đơn dùng voucher?"]


@pytest.mark.parametrize("layout", ["baby_xlsx", "baby_fee_lines", "baby_abbrev"])
def test_baby_rules_match_reference(tmp_path, layout):
    customers, orders = synth_baby.generate()
    reference = {q: a for q, _, a in synth_baby.reference(customers, orders)}
    write = dict(synth_baby.layouts(customers, orders))[layout]
    catalog, engine = build(tmp_path, layout, write, "mother_baby")
    try:
        wrong = [(q, answer(catalog, engine, q, synth_baby.TODAY), reference[q]) for q in BABY]
        assert not [w for w in wrong if w[1] != w[2]], wrong
    finally:
        engine.close()


REPAIR = ["Tháng 2/2026 tiệm có bao nhiêu lượt xe?", "Tiền trung bình một phiếu tháng 2/2026 là bao nhiêu?", "Tỉ lệ bảo hành tháng 2/2026 là bao nhiêu?",
          "Tháng 2/2026 tiệm thay nhớt cho bao nhiêu lượt xe?", "Tháng 2/2026 có bao nhiêu lượt thay ruột?", "Tháng 2/2026 có bao nhiêu lượt sửa điện?",
          "Tháng 2/2026 có bao nhiêu chiếc xe khác nhau mang tới tiệm?", "Quý 1/2026 có bao nhiêu xe khách quen?", "Quý 1/2026 xe nào tới tiệm nhiều lần nhất?",
          "Tổng tiền công tháng 2/2026 là bao nhiêu?", "Tháng 2/2026 thợ Tuấn được bao nhiêu tiền hoa hồng?",
          "Tháng 2/2026 thợ Tuấn làm bao nhiêu lượt xe?", "Tháng 2/2026 thợ nào làm ra nhiều tiền nhất?", "Tháng 2/2026 thợ Tuấn làm ra bao nhiêu tiền?",
          "Tháng 2/2026 tiền thay nhớt thu được là bao nhiêu?",
          # two periods: growth % and difference (rulebook §1)
          "Doanh thu tháng 2/2026 tăng hay giảm bao nhiêu % so với tháng 1/2026?", "Doanh thu tháng 2/2026 chênh lệch bao nhiêu so với tháng trước?",
          "Số lượt xe tháng 2/2026 thay đổi bao nhiêu phần trăm so với tháng 1/2026?", "So với tháng 1/2026, tháng 2/2026 tiệm nhận nhiều hơn bao nhiêu lượt xe?",
          "Tháng 1/2026 và tháng 2/2026 mỗi tháng tiệm có bao nhiêu lượt xe?", "Tháng 2/2026 thợ làm được nhiều lượt xe nhất?"]


@pytest.mark.parametrize("layout", ["repair_csv", "repair_combined_vehicle", "repair_total_only"])
def test_repair_rules_match_reference(tmp_path, layout):
    tickets = synth_repair.generate()
    reference = {q: (a if t != "unanswerable" else "unanswerable") for q, t, a in synth_repair.reference(tickets, layout)}
    write = dict(synth_repair.layouts(tickets))[layout]

    def with_roster(folder):
        described = write(folder)
        synth_phone.write_csv(folder / "danh_sach_tho.csv", ["Họ tên", "Ngày vào làm", "Hoa hồng (%)"],
                              [{"Họ tên": full, "Ngày vào làm": "01/03/2024", "Hoa hồng (%)": pct} for full, pct in synth_repair.ROSTER.values()])
        return described
    catalog, engine = build(tmp_path, layout, with_roster, "vehicle_repair")
    try:
        wrong = [(q, answer(catalog, engine, q, synth_repair.TODAY), reference[q]) for q in REPAIR if q in reference]
        assert not [w for w in wrong if w[1] != w[2]], wrong
    finally:
        engine.close()


@pytest.mark.parametrize("name,expected", [("danhmuc", "danh_muc"), ("baohanh", "bao_hanh"), ("thuno", "thu_no"), ("sobanhang", "so_ban_hang"),
                                           ("danh_sach_hang_hoa", "danh_sach_hang_hoa"), ("thuoc", "thuoc"), ("chi_nhanh", "chi_nhanh")])
def test_glued_file_names_are_split_into_known_words(name, expected):
    assert name_words(name) == expected
