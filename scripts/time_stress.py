"""Stress-test temporal.resolve_periods against rulebook §2, with an independent reference.

Every template knows its own period (computed here, not by temporal.py). Surface variants add the noise
seen in real shop questions: no accents, typos, abbreviations, roman numerals, different sentence frames.
Outcomes per case: ok, WRONG (a different period -> a wrong answer), unresolved (-> abstain, safe).
Usage: .venv/bin/python scripts/time_stress.py [--show 40]
"""
from __future__ import annotations

import argparse
import calendar
import collections
import csv
import datetime as dt
from pathlib import Path
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datamate.intent import normalize_question  # noqa: E402
from datamate.temporal import resolve_periods  # noqa: E402

D = dt.date
TODAYS = [D(2026, 3, 18), D(2026, 1, 5), D(2025, 12, 31), D(2026, 7, 31), D(2026, 5, 1), D(2025, 11, 30)]
FRAMES = ["Doanh thu {t} là bao nhiêu?", "{t} cửa hàng bán được bao nhiêu đơn?", "cho hỏi {t} thu về nhiêu tiền vậy", "Tổng số lượng bán ra {t}?"]
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV"}


def strip(text):
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D")


def month_end(y, m):
    return D(y, m, calendar.monthrange(y, m)[1])


def shift_month(y, m, k):
    index = y * 12 + (m - 1) + k
    return index // 12, index % 12 + 1


def year_for(month, today):
    """Rulebook: month/quarter without year is this year unless it starts after today."""
    return today.year if D(today.year, month, 1) <= today else today.year - 1


def cases(today):
    y, m = today.year, today.month
    monday = today - dt.timedelta(days=today.weekday())
    py, pm = shift_month(y, m, -1)
    ppy, ppm = shift_month(y, m, -2)
    q = (m - 1) // 3 + 1
    qs = D(y, 3 * q - 2, 1)
    pqy, pqm = shift_month(y, 3 * q - 2, -3)
    out = []

    def add(name, variants, start, end):
        out.append((name, variants, start, end))

    add("hom nay", ["hôm nay", "hnay", "bữa nay", "hôm ni"], today, today)
    add("hom qua", ["hôm qua", "hqua", "hôm wa", "bữa qua"], today - dt.timedelta(1), today - dt.timedelta(1))
    add("hom kia", ["hôm kia", "hôm kìa"], today - dt.timedelta(2), today - dt.timedelta(2))
    add("tuan nay", ["tuần này", "tuan nay", "tuần ni"], monday, today)
    add("tuan truoc", ["tuần trước", "tuần rồi", "tuần trc", "tuần vừa rồi", "tuan truoc"], monday - dt.timedelta(7), monday - dt.timedelta(1))
    add("tuan truoc nua", ["tuần trước nữa"], monday - dt.timedelta(14), monday - dt.timedelta(8))
    add("thang nay", ["tháng này", "thang nay", "tháng ni"], D(y, m, 1), today)
    add("thang truoc", ["tháng trước", "tháng rồi", "tháng trc", "tháng vừa rồi", "tháng vừa qua", "thangs truoc"], D(py, pm, 1), month_end(py, pm))
    add("thang truoc nua", ["tháng trước nữa"], D(ppy, ppm, 1), month_end(ppy, ppm))
    add("tu dau thang", ["từ đầu tháng đến nay", "từ đầu tháng tới giờ", "từ đầu tháng đến hết hôm nay"], D(y, m, 1), today)
    add("tu dau tuan", ["từ đầu tuần đến nay", "từ đầu tuần tới giờ"], monday, today)
    add("tu dau nam", ["từ đầu năm đến nay", "từ đầu năm tới giờ", "từ đầu năm đến hết hôm nay"], D(y, 1, 1), today)
    add("nam ngoai", ["năm ngoái", "năm trước", "năm rồi", "nam ngoai"], D(y - 1, 1, 1), D(y - 1, 12, 31))
    add("quy nay", ["quý này", "quy nay"], qs, today)
    add("quy truoc", ["quý trước", "quý rồi", "quy truoc"], D(pqy, pqm, 1), month_end(*shift_month(pqy, pqm, 2)))
    for n in (3, 7, 14, 30):
        add(f"{n} ngay gan nhat", [f"{n} ngày gần nhất", f"{n} ngày gần đây", f"{n} ngay gan nhat"], today - dt.timedelta(n - 1), today)
    # Explicit months / quarters / years (past, so data exists).
    for k in (2, 5, 13):
        ey, em = shift_month(y, m, -k)
        yy = str(ey)[2:]
        add(f"thang M/Y (-{k})", [f"tháng {em}/{ey}", f"tháng {em:02d}/{ey}", f"tháng {em} năm {ey}", f"thang {em} nam {ey}", f"thg {em}/{ey}",
                                  f"T{em}/{ey}", f"th{em}/{yy}", f"tháng{em}/{ey}", f"thangs {em}/{ey}", f"tháng {em}/{yy}"], D(ey, em, 1), month_end(ey, em))
    for month in (1, 6, 11):
        ey = year_for(month, today)
        add(f"thang {month} khong nam", [f"tháng {month}", f"thang {month}"], D(ey, month, 1), month_end(ey, month))
    for k in (1, 2, 5):
        qy, qm = shift_month(y, 3 * q - 2, -3 * k)
        qq = (qm - 1) // 3 + 1
        add(f"quy Q/Y (-{k})", [f"quý {qq}/{qy}", f"quý {qq} năm {qy}", f"quy {qq} nam {qy}", f"Q{qq}/{qy}", f"q{qq} {qy}", f"quý {ROMAN[qq]}/{qy}",
                                f"quý {ROMAN[qq]} năm {qy}", f"quí {qq}/{qy}", f"qúy {qq}/{qy}", f"quys {qq}/{qy}"], D(qy, qm, 1), month_end(*shift_month(qy, qm, 2)))
    for qq in (1, 2, 3, 4):
        qy = year_for(3 * qq - 2, today)
        add(f"quy {qq} khong nam", [f"quý {qq}", f"quy {qq}", f"Q{qq}"], D(qy, 3 * qq - 2, 1), month_end(qy, 3 * qq))
    add("nam Y", [f"năm {y - 1}", f"nam {y - 1}", f"cả năm {y - 1}"], D(y - 1, 1, 1), D(y - 1, 12, 31))
    # Days and ranges.
    day = today - dt.timedelta(days=40)
    add("ngay D/M/Y", [f"ngày {day.day}/{day.month}/{day.year}", f"{day.day:02d}/{day.month:02d}/{day.year}", f"ngày {day.day} tháng {day.month} năm {day.year}",
                       f"hôm {day.day}/{day.month}/{day.year}", f"ngay {day.day}/{day.month}/{day.year}"], day, day)
    add("ngay D/M khong nam", [f"ngày {day.day}/{day.month}", f"hôm {day.day}/{day.month}", f"mùng {day.day} tháng {day.month}"] if day.year == year_for(day.month, today) or True else [],
        *(lambda d: (d, d))(D(today.year, day.month, day.day) if D(today.year, day.month, day.day) <= today else D(today.year - 1, day.month, day.day)))
    a, b = today - dt.timedelta(days=60), today - dt.timedelta(days=46)
    add("tu X den Y", [f"từ {a.day}/{a.month}/{a.year} đến {b.day}/{b.month}/{b.year}", f"từ ngày {a.day}/{a.month}/{a.year} đến hết ngày {b.day}/{b.month}/{b.year}",
                       f"từ {a.day}/{a.month}/{a.year} tới {b.day}/{b.month}/{b.year}", f"tu {a.day}/{a.month}/{a.year} den {b.day}/{b.month}/{b.year}"], a, b)
    if a.year == b.year:
        add("tu X den Y (nam o cuoi)", [f"từ {a.day}/{a.month} đến {b.day}/{b.month}/{b.year}"], a, b)
    for n in (5, 10):
        ey, em = shift_month(y, m, -2)
        add(f"{n} ngay dau thang", [f"{n} ngày đầu tháng {em}/{ey}"], D(ey, em, 1), D(ey, em, n))
    # Wider phrasing families, each with a definite meaning under the rulebook.
    add("nam nay", ["năm nay", "nam nay", "năm ni"], D(y, 1, 1), today)
    for month in (1, 2):
        if D(y, month, 1) <= today:
            add(f"thang {month} nam nay", [f"tháng {month} năm nay", f"thang {month} nam nay"], D(y, month, 1), month_end(y, month))
        add(f"thang {month} nam ngoai", [f"tháng {month} năm ngoái", f"tháng {month} năm trước", f"thang {month} nam ngoai"], D(y - 1, month, 1), month_end(y - 1, month))
    add("quy 1 nam ngoai", ["quý 1 năm ngoái", "quý 1 năm trước", "Q1 năm ngoái"], D(y - 1, 1, 1), D(y - 1, 3, 31))
    for wd, names in ((0, ["thứ Hai tuần trước", "thứ 2 tuần trước", "t2 tuần trước"]), (6, ["chủ nhật tuần trước", "CN tuần trước"])):
        # The period is the whole week; the weekday itself is the frame's weekday_filter (isodow) downstream.
        add(f"thu {wd} tuan truoc", names, monday - dt.timedelta(7), monday - dt.timedelta(1))
    ey, em = shift_month(y, m, -2)
    add("tu mung d1 den mung d2", [f"từ mùng 1 đến mùng 10 tháng {em}/{ey}", f"từ ngày 1 đến ngày 10 tháng {em}/{ey}", f"từ 1 đến 10/{em}/{ey}"], D(ey, em, 1), D(ey, em, 10))
    add("dang ngay khac", [f"{day.day}-{day.month}-{day.year}", f"{day.day}.{day.month}.{day.year}", f"{day.isoformat()}", f"ngày {day.day}/{day.month}/{str(day.year)[2:]}"], day, day)
    ey, em = shift_month(y, m, -3)
    add("dang thang khac", [f"{em:02d}/{ey}", f"tháng {em}-{ey}", f"tháng {em}.{ey}", f"{em:02d}-{ey}", f"{em}-{ey}", f"{em:02d}.{ey}", f"{em}.{ey}",
                            f"T{em:02d}-{ey}", f"tháng {em:02d}-{ey}"], D(ey, em, 1), month_end(ey, em))
    for n in (7, 10):
        add(f"{n} ngay qua", [f"{n} ngày qua", f"{n} ngày vừa qua", f"{n} ngày trở lại đây"], today - dt.timedelta(n - 1), today)
    add("6 thang dau nam", [f"6 tháng đầu năm {y - 1}", f"nửa đầu năm {y - 1}"], D(y - 1, 1, 1), D(y - 1, 6, 30))
    add("6 thang cuoi nam", [f"6 tháng cuối năm {y - 1}", f"nửa cuối năm {y - 1}"], D(y - 1, 7, 1), D(y - 1, 12, 31))
    return out


def tet_cases():
    rows = list(csv.DictReader(open(Path(__file__).resolve().parents[1] / "train" / "calendar.csv")))
    out = []
    for year in (2025, 2026):
        start = next(D.fromisoformat(r["date"]) for r in rows if int(r["lunar_year"]) == year - 1 and int(r["lunar_month"]) == 12 and int(r["lunar_day"]) == 23)
        end = next(D.fromisoformat(r["date"]) for r in rows if int(r["lunar_year"]) == year and int(r["lunar_month"]) == 1 and int(r["lunar_day"]) == 10)
        out.append((f"tet {year}", [f"Tết {year}", f"dịp Tết {year}", f"mùa Tết {year}", f"tet {year}"], start, end))
    return out


class CalendarEngine:
    def __init__(self):
        import duckdb
        self.connection = duckdb.connect()
        self.connection.execute(f"CREATE TABLE calendar AS SELECT * FROM read_csv_auto('{Path(__file__).resolve().parents[1] / 'train' / 'calendar.csv'}')")

    def lookup(self, sql, max_rows=500000):
        cursor = self.connection.execute(sql)
        return [d[0] for d in cursor.description], cursor.fetchall()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", type=int, default=40)
    args = parser.parse_args()
    engine = CalendarEngine()
    stats = collections.Counter()
    by_template = collections.defaultdict(collections.Counter)
    shown = []
    for today in TODAYS:
        for name, variants, start, end in cases(today) + tet_cases():
            if start > today:
                continue
            for variant in variants:
                for accents in (True, False):
                    text = variant if accents else strip(variant)
                    for frame in FRAMES:
                        question = frame.format(t=text)
                        periods = resolve_periods(normalize_question(question), today, engine)   # as compile_frame does
                        if not periods:
                            outcome = "unresolved"
                        else:
                            got = periods[0]
                            same = got.start == start and min(got.end, today) == min(end, today)
                            outcome = "ok" if same else "WRONG"
                        stats[outcome] += 1
                        by_template[name][outcome] += 1
                        if outcome != "ok" and len(shown) < args.show and (question, outcome) not in {(s[1], s[0]) for s in shown}:
                            shown.append((outcome, question, today, (start, end), (periods[0].start, periods[0].end) if periods else None))
    total = sum(stats.values())
    print(f"cases {total}: ok {stats['ok']} ({stats['ok'] / total:.1%}), WRONG {stats['WRONG']}, unresolved {stats['unresolved']}")
    for name, counter in sorted(by_template.items(), key=lambda item: -(item[1]["WRONG"] * 1000 + item[1]["unresolved"])):
        if counter["WRONG"] or counter["unresolved"]:
            print(f"  {name:28s} ok {counter['ok']:4d}  WRONG {counter['WRONG']:4d}  unresolved {counter['unresolved']:4d}")
    for outcome, question, today, want, got in shown:
        print(f"[{outcome}] {today} | {question} | want {want[0]}..{want[1]} | got {got}")


if __name__ == "__main__":
    main()
