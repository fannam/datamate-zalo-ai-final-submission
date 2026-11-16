"""Synthetic motorbike-repair shops from the released rulebook (§6 Sửa xe, §7, §8) with an independent
reference answer key. Nothing here reads public questions or public shop files.

Usage: .venv/bin/python training_code/data_generation/synth_repair.py --out runs/synth_repair
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import shutil
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook

from synth_phone import ROOT, in_period, month, readme, write_csv

TODAY = dt.date(2026, 3, 20)
START = dt.date(2025, 12, 1)
MODELS = [("Air Blade", ["Air Blade", "air blade", "AB"]), ("Vision", ["Vision", "vision"]), ("Exciter 150", ["Exciter 150", "exciter 150"]),
          ("Wave Alpha", ["Wave Alpha", "wave alpha"]), ("SH Mode", ["SH Mode", "SH mode"]), ("Lead", ["Lead", "LEAD"])]
# job text -> canonical job (rulebook: plain 'Nhớt' is engine oil; plain 'ruột' is a new tube; 'sửa điện' excludes bulbs and batteries)
JOBS = [("Thay nhớt", "nhot_may"), ("Nhớt", "nhot_may"), ("Nhớt láp", "nhot_lap"), ("Vá ruột", "va_ruot"), ("Thay ruột", "thay_ruot"), ("Ruột", "thay_ruot"),
        ("Sửa điện", "sua_dien"), ("Thay bóng đèn", "bong_den"), ("Thay ắc quy", "ac_quy"), ("Thay má phanh", "ma_phanh"), ("Bảo dưỡng", "bao_duong"), ("Thay bugi", "bugi")]
MECHANICS = ["Tuấn", "Hùng", "Long", "Phát"]
ROSTER = {"Tuấn": ("Nguyễn Văn Tuấn", 30), "Hùng": ("Trần Quốc Hùng", 25), "Long": ("Lê Hải Long", 28), "Phát": ("Phạm Tấn Phát", 32)}


def plate(rng):
    return f"{rng.choice(['59', '51', '29', '30', '43'])}{rng.choice('ABCDFGHK')}{rng.randint(1, 9)}-{rng.randint(100, 999)}.{rng.randint(10, 99)}"


def generate(seed=31):
    rng = random.Random(seed)
    bikes = [(plate(rng), rng.choice(MODELS)) for _ in range(70)]
    regulars = bikes[:15]
    tickets, day = [], START
    while day <= TODAY:
        for _ in range(rng.choice([3, 4, 5, 6])):
            bike = rng.choice(regulars) if rng.random() < 0.45 else rng.choice(bikes)
            model_text = rng.choice(bike[1][1])
            mechanic = rng.choice(MECHANICS)
            mechanic_text = rng.choice([mechanic, mechanic, f"a {mechanic}", f"A.{mechanic}"])   # kinship honorifics before nicknames
            if rng.random() < 0.08 and tickets:
                job = rng.choice(JOBS[:7])
                tickets.append({"date": day, "plate": bike[0], "model": bike[1][0], "model_text": model_text, "jobs": [f"Bảo hành {job[0].lower()}"], "kinds": [],
                                "mechanic": mechanic, "mechanic_text": mechanic_text, "labor": 0, "parts": 0, "warranty": True})
                continue
            jobs = rng.sample(JOBS, rng.choice([1, 1, 2, 3]))
            labor = sum(rng.choice([20000, 30000, 50000]) for _ in jobs)
            parts = sum(rng.choice([0, 40000, 90000, 150000, 350000]) for _ in jobs)
            tickets.append({"date": day, "plate": bike[0], "model": bike[1][0], "model_text": model_text, "jobs": [j[0] for j in jobs], "kinds": [j[1] for j in jobs],
                            "mechanic": mechanic, "mechanic_text": mechanic_text, "labor": labor, "parts": parts, "warranty": False})
        day += dt.timedelta(days=1)
    for t in tickets:
        t["total"] = t["labor"] + t["parts"]
    return tickets


def growth(current, previous):
    return round(100 * (current - previous) / previous, 2)


def reference(tickets, layout):
    feb, q1 = month(2026, 2), (dt.date(2026, 1, 1), dt.date(2026, 3, 31))
    f = [t for t in tickets if in_period(t["date"], *feb)]
    jan = [t for t in tickets if in_period(t["date"], *month(2026, 1))]
    paid = [t for t in f if not t["warranty"]]
    by_mech, by_model, visits = defaultdict(int), defaultdict(int), defaultdict(int)
    for t in f:
        by_mech[t["mechanic"]] += 1
        by_model[t["model"]] += 1
    for t in tickets:
        if in_period(t["date"], *q1) and not t["warranty"]:
            visits[t["plate"]] += 1

    def top(counter):
        ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[0][0] if len(ranked) == 1 or ranked[0][1] > ranked[1][1] else None

    def jobs(kind):
        return sum(1 for t in paid if kind in t["kinds"])

    money = layout != "repair_total_only"
    qa = [
        ("Tháng 2/2026 tiệm có bao nhiêu lượt xe?", "number", len(f)),
        ("Doanh thu tháng 2/2026 của tiệm là bao nhiêu?", "number", sum(t["total"] for t in f)),
        ("Tiền trung bình một phiếu tháng 2/2026 là bao nhiêu?", "number", int(sum(t["total"] for t in f) / len(paid) + 0.5)),
        ("Tỉ lệ bảo hành tháng 2/2026 là bao nhiêu?", "percent", round(100 * (len(f) - len(paid)) / len(paid), 2)),
        ("Tháng 2/2026 thợ nào làm nhiều nhất?", "string", top(by_mech)),
        ("Tháng 2/2026 thợ Tuấn làm bao nhiêu lượt xe?", "number", by_mech["Tuấn"]),
        ("Tháng 2/2026 tiệm thay nhớt cho bao nhiêu lượt xe?", "number", jobs("nhot_may")),
        ("Tháng 2/2026 có bao nhiêu lượt thay ruột?", "number", jobs("thay_ruot")),
        ("Tháng 2/2026 có bao nhiêu lượt vá ruột?", "number", jobs("va_ruot")),
        ("Tháng 2/2026 có bao nhiêu lượt sửa điện?", "number", jobs("sua_dien")),
        ("Tháng 2/2026 có bao nhiêu chiếc xe khác nhau mang tới tiệm?", "number", len({t["plate"] for t in f})),
        ("Quý 1/2026 có bao nhiêu xe khách quen?", "number", sum(1 for n in visits.values() if n >= 2)),
        ("Quý 1/2026 xe nào tới tiệm nhiều lần nhất?", "string", top(visits)),
        ("Tháng 2/2026 dòng xe nào được sửa nhiều nhất?", "string", top(by_model)),
        ("Tổng tiền công tháng 2/2026 là bao nhiêu?", "number" if money else "unanswerable", sum(t["labor"] for t in f) if money else None),
        ("Tổng tiền phụ tùng tháng 2/2026 là bao nhiêu?", "number" if money else "unanswerable", sum(t["parts"] for t in f) if money else None),
        ("Tháng 2/2026 thợ Hùng có bao nhiêu phiếu bảo hành?", "number", sum(1 for t in f if t["warranty"] and t["mechanic"] == "Hùng")),
        ("Tháng 2/2026 thợ Tuấn được bao nhiêu tiền hoa hồng?", "number", int(sum(t["total"] for t in f if t["mechanic"] == "Tuấn") * ROSTER["Tuấn"][1] / 100 + 0.5)),
        ("Tháng 2/2026 tổng tiền hoa hồng của các thợ là bao nhiêu?", "number", int(sum(t["total"] * ROSTER[t["mechanic"]][1] / 100 for t in f) + 0.5)),
        ("Tháng 2/2026 thợ nào làm ra nhiều tiền nhất?", "string", top({m: sum(t["total"] for t in f if t["mechanic"] == m) for m in by_mech})),
        ("Tháng 2/2026 thợ Tuấn làm ra bao nhiêu tiền?", "number", sum(t["total"] for t in f if t["mechanic"] == "Tuấn")),
        ("Tháng 2/2026 tiền thay nhớt thu được là bao nhiêu?", "unanswerable", None),
        # Comparisons of two periods (rulebook §1: growth vs the earlier period, negative when lower; difference = asked − compared).
        ("Doanh thu tháng 2/2026 tăng hay giảm bao nhiêu % so với tháng 1/2026?", "percent", growth(sum(t["total"] for t in f), sum(t["total"] for t in jan))),
        ("Doanh thu tháng 2/2026 chênh lệch bao nhiêu so với tháng trước?", "number", sum(t["total"] for t in f) - sum(t["total"] for t in jan)),
        ("Số lượt xe tháng 2/2026 thay đổi bao nhiêu phần trăm so với tháng 1/2026?", "percent", growth(len(f), len(jan))),
        ("So với tháng 1/2026, tháng 2/2026 tiệm nhận nhiều hơn bao nhiêu lượt xe?", "number", len(f) - len(jan)),
        ("Tháng 1/2026 và tháng 2/2026 mỗi tháng tiệm có bao nhiêu lượt xe?", "list", [len(jan), len(f)]),
        ("Tháng 2/2026 thợ làm được nhiều lượt xe nhất?", "string", top(by_mech)),
    ]
    return [(q, t, a) for q, t, a in qa if a is not None or t == "unanswerable"]


HEAD = ["Ngày", "Biển số", "Dòng xe", "Làm gì", "Thợ", "Tiền công", "Tiền phụ tùng", "Tổng tiền", "Ghi chú"]
DESC = {"Ngày": "ngày nhận xe", "Biển số": "biển số xe", "Dòng xe": "dòng xe", "Làm gì": "các việc đã làm", "Thợ": "thợ sửa", "Tiền công": "tiền công",
        "Tiền phụ tùng": "tiền phụ tùng", "Tổng tiền": "tổng tiền khách trả", "Ghi chú": "ghi chú; phiếu bảo hành ghi Bảo hành"}


def rows(tickets, *, year=True, money_text=False, combined=False, kind=False):
    out = [{"Ngày": "Ghi chú: tiền tính bằng đồng"}] if year else []
    for t in tickets:
        fmt = (lambda v: f"{v:,}đ".replace(",", ".") if v else "0") if money_text else (lambda v: v)
        row = {"Ngày": t["date"].strftime("%d/%m/%Y" if year else "%d/%m"), "Biển số": t["plate"], "Dòng xe": t["model_text"], "Làm gì": ", ".join(t["jobs"]),
               "Thợ": t["mechanic_text"], "Tiền công": fmt(t["labor"]), "Tiền phụ tùng": fmt(t["parts"]), "Tổng tiền": fmt(t["total"]),
               "Ghi chú": "Bảo hành" if t["warranty"] and not kind else ""}
        if combined:
            row["Xe"] = f"{t['model_text']} {t['plate']}"
        if kind:
            row["Loại phiếu"] = "Bảo hành" if t["warranty"] else "Sửa"
        out.append(row)
    return out


def write_month_sheets(path, tickets, header):
    book = Workbook()
    book.remove(book.active)
    for (y, m), group in sorted(_by_month(tickets).items()):
        sheet = book.create_sheet(f"Tháng {m}-{y}")
        sheet.append(header)
        for row in rows(group, year=False):
            sheet.append([row.get(c, "") for c in header])
    book.save(path)


def _by_month(tickets):
    groups = defaultdict(list)
    for t in tickets:
        groups[(t["date"].year, t["date"].month)].append(t)
    return groups


def layouts(tickets):
    yield "repair_csv", lambda d: (write_csv(d / "so_sua_xe.csv", HEAD, rows(tickets)), {"so_sua_xe.csv": (HEAD, DESC)})[-1]
    yield "repair_month_sheets", lambda d: (write_month_sheets(d / "SoSuaXe.xlsx", tickets, HEAD), {"SoSuaXe.xlsx": (HEAD, DESC | {"Ngày": "ngày nhận xe (không ghi năm; năm theo tên sheet)"})})[-1]
    combined = ["Ngày", "Xe", "Làm gì", "Thợ", "Tiền công", "Tiền phụ tùng", "Tổng tiền", "Ghi chú"]
    yield "repair_combined_vehicle", lambda d: (write_csv(d / "so_sua_xe.csv", combined, rows(tickets, combined=True)), {"so_sua_xe.csv": (combined, DESC | {"Xe": "dòng xe và biển số"})})[-1]
    yield "repair_money_text", lambda d: (write_csv(d / "so_sua_xe.csv", HEAD, rows(tickets, money_text=True)), {"so_sua_xe.csv": (HEAD, DESC)})[-1]
    kind = HEAD[:-1] + ["Loại phiếu"]
    yield "repair_kind_column", lambda d: (write_csv(d / "sosuaxe.csv", kind, rows(tickets, kind=True)), {"sosuaxe.csv": (kind, DESC | {"Loại phiếu": "Sửa hoặc Bảo hành"})})[-1]
    short = ["Ngày vào", "BS", "Loại xe", "ND sửa", "Thợ chính", "Công", "Phụ tùng", "Thành tiền", "GC"]
    rename = dict(zip(HEAD, short))
    yield "repair_abbrev", lambda d: (write_csv(d / "so_sua_xe.csv", short, [{rename[k]: v for k, v in r.items()} for r in rows(tickets)]),
                                      {"so_sua_xe.csv": (short, {rename[k]: v for k, v in DESC.items()})})[-1]
    total = ["Ngày", "Biển số", "Dòng xe", "Làm gì", "Thợ", "Tiền", "Ghi chú"]
    yield "repair_total_only", lambda d: (write_csv(d / "so_sua_xe.csv", total, [r | {"Tiền": r.get("Tổng tiền", "")} for r in rows(tickets)]),
                                          {"so_sua_xe.csv": (total, DESC | {"Tiền": "số tiền khách trả"})})[-1]


HELDOUT = {"Tháng 2/2026 tiệm có bao nhiêu lượt xe?": "T2/2026 tiệm nhận bao nhiêu xe vào sửa?",
           "Tiền trung bình một phiếu tháng 2/2026 là bao nhiêu?": "Tháng 2/2026 trung bình mỗi phiếu thu bao nhiêu tiền?",
           "Tỉ lệ bảo hành tháng 2/2026 là bao nhiêu?": "tháng 2 năm nay tỉ lệ xe quay lại bảo hành là bao nhiêu %",
           "Tháng 2/2026 thợ nào làm nhiều nhất?": "Thợ nào làm nhiều xe nhất trong tháng 2/2026?",
           "Tháng 2/2026 tiệm thay nhớt cho bao nhiêu lượt xe?": "Tháng 2/2026 có mấy lượt thay nhớt máy?",
           "Tháng 2/2026 có bao nhiêu lượt sửa điện?": "T2/2026 tiệm sửa điện cho bao nhiêu xe?",
           "Tháng 2/2026 có bao nhiêu chiếc xe khác nhau mang tới tiệm?": "Tháng 2/2026 có bao nhiêu xe khác nhau tới tiệm?",
           "Quý 1/2026 có bao nhiêu xe khách quen?": "Q1/2026 tiệm có mấy xe của khách quen?",
           "Quý 1/2026 xe nào tới tiệm nhiều lần nhất?": "Quý 1/2026 xe nào đến sửa nhiều lần nhất?"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        shutil.rmtree(args.out)
    split = args.out / "split"
    (split / "data").mkdir(parents=True)
    shutil.copy(ROOT / "public_test" / "rulebook.md", split / "rulebook.md")
    shutil.copy(ROOT / "train" / "calendar.csv", split / "calendar.csv")
    tickets = generate()
    out = {"questions": [], "answers": [], "questions_heldout": [], "answers_heldout": []}
    for name, write in layouts(tickets):
        folder = split / "data" / name
        folder.mkdir()
        described = write(folder)
        write_csv(folder / "danh_sach_tho.csv", ["Họ tên", "Ngày vào làm", "Hoa hồng (%)"],
                  [{"Họ tên": full, "Ngày vào làm": "01/03/2024", "Hoa hồng (%)": pct} for full, pct in ROSTER.values()])
        described["danh_sach_tho.csv"] = (["Họ tên", "Ngày vào làm", "Hoa hồng (%)"], {"Họ tên": "họ tên thợ", "Hoa hồng (%)": "phần trăm hoa hồng của thợ, tính trên tổng Tiền các phiếu thợ làm trong tháng"})
        text = readme(described).replace("# Dữ liệu cửa hàng điện thoại", "# Sổ sửa xe của tiệm").replace("Mỗi dòng sổ bán hàng là một dòng hàng của hoá đơn.", "Mỗi dòng là một lượt xe.")
        (folder / "README.md").write_text(text)
        for index, (question, kind, answer) in enumerate(reference(tickets, name)):
            qid = f"{name}_{index:02d}"
            gold = {"id": qid, "type": kind, "answer": answer, "alias": []}
            out["questions"].append({"id": qid, "business_id": name, "question": question, "as_of_date": TODAY.isoformat()})
            out["answers"].append(gold)
            if question in HELDOUT:
                out["questions_heldout"].append({"id": qid + "_p", "business_id": name, "question": HELDOUT[question], "as_of_date": TODAY.isoformat()})
                out["answers_heldout"].append(gold | {"id": qid + "_p"})
    for stem, records in out.items():
        (args.out / f"{stem}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    print(json.dumps({"shops": 7, "questions": len(out["questions"]), "heldout": len(out["questions_heldout"]), "tickets": len(tickets)}))
    for question, kind, answer in reference(tickets, "repair_csv"):
        print(f"  {kind:7s} {answer!s:>12}  {question}")


if __name__ == "__main__":
    main()
