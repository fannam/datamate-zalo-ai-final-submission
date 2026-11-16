"""Synthetic phone-store shops built from the released rulebook (§1, §4, §6 Điện thoại, §8), with an
independent reference answer key. Nothing here reads public questions or public shop files.

Writes <out>/split/{rulebook.md,calendar.csv,data/<shop>/...}, <out>/questions.jsonl, <out>/answers.jsonl.
Usage: .venv/bin/python training_code/data_generation/synth_phone.py --out runs/synth_phone
"""
from __future__ import annotations

import argparse
import calendar as cal
import csv
import datetime as dt
import json
import random
import shutil
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[2]  # repository root (training_code/data_generation/ -> ../..)
TODAY = dt.date(2026, 3, 20)
START = dt.date(2025, 10, 1)
PHONES = [("iPhone 15 128GB", 18990000), ("iPhone 15 Pro Max 256GB", 30990000), ("iPhone 13 128GB", 12990000), ("Samsung Galaxy A55 128GB", 9490000),
          ("Samsung Galaxy S24 256GB", 21990000), ("Xiaomi Redmi Note 13 128GB", 4690000), ("OPPO Reno11 256GB", 10490000), ("Nokia 105 4G", 690000)]
ACCESSORIES = [("Ốp lưng iPhone 15", 150000), ("Cáp sạc Type-C 1m", 120000), ("Tai nghe AirPods Pro 2", 5990000), ("Cường lực Samsung A55", 100000)]
CUSTOMERS = [(f"KH{i:03d}", name, f"09{random.Random(i).randint(10000000, 99999999)}") for i, name in enumerate(
    ["Nguyễn Văn An", "Trần Thị Bình", "Lê Hoàng Cường", "Phạm Minh Dũng", "Hoàng Thu Hà", "Vũ Đức Huy", "Đặng Mỹ Linh", "Bùi Quốc Nam",
     "Đỗ Thanh Phương", "Ngô Gia Quân", "Dương Bảo Sơn", "Lý Kim Trang", "Mai Anh Tuấn", "Tạ Hồng Vân", "Châu Ngọc Yến"], start=1)]
TRADE_INS = ["iPhone 11", "Samsung A51", "iPhone XR", "OPPO A57"]


def add_months(day, k):
    index = day.year * 12 + day.month - 1 + k
    year, month = index // 12, index % 12 + 1
    return dt.date(year, month, min(day.day, cal.monthrange(year, month)[1]))


def model_line(name):
    import re
    match = re.match(r"(.*?)\s*\d+\s*(?:GB|TB)\b", name, re.I)
    return match[1].strip() if match else name


def brand(name):
    first = name.split()[0]
    return "Apple" if first.lower() == "iphone" else first


def is_phone(name):
    return any(name == p for p, _ in PHONES)


# ------------------------------------------------------------------ data

def generate(seed=11):
    rng = random.Random(seed)
    orders, installments, debt_payments, claims = [], [], [], []
    day, number = START, 1
    while day <= TODAY:
        for _ in range(rng.choice([0, 1, 1, 2])):
            customer = rng.choice(CUSTOMERS) if rng.random() < 0.85 else ("", "Khách lẻ", "")
            lines = []
            phone_order = rng.random() < 0.8
            if phone_order:
                name, price = rng.choice(PHONES)
                discount = rng.choice([0, 0, 0, 200000, 500000]) if price > 3000000 else 0
                lines.append({"product": name, "qty": 1, "price": price, "discount": discount, "imei": f"35{rng.randint(10**12, 10**13 - 1)}"})
            if not phone_order or rng.random() < 0.4:
                name, price = rng.choice(ACCESSORIES)
                lines.append({"product": name, "qty": rng.choice([1, 1, 2]), "price": price, "discount": 0, "imei": ""})
            for line in lines:
                line["amount"] = line["qty"] * line["price"] - line["discount"]
                line["cost"] = int(line["price"] * (0.86 if is_phone(line["product"]) else 0.55)) // 1000 * 1000   # cost of one unit sold
            subtotal = sum(line["amount"] for line in lines)
            order_discount = rng.choice([0, 0, 0, 0, 0, 200000]) if subtotal > 3000000 else 0
            tradein = rng.choice([1500000, 2500000, 4000000]) if phone_order and subtotal > 8000000 and rng.random() < 0.25 else 0
            status = rng.choices(["Hoàn thành", "Huỷ", "Trả hàng"], [88, 6, 6])[0]
            payment = "Tiền mặt"
            if phone_order and customer[0]:
                payment = rng.choices(["Tiền mặt", "Chuyển khoản", "Trả góp", "Ghi nợ"], [35, 30, 25, 10])[0]
            elif rng.random() < 0.5:
                payment = "Chuyển khoản"
            order = {"order_id": f"HD{number:04d}", "date": day, "customer": customer, "lines": lines, "order_discount": order_discount,
                     "tradein": tradein, "tradein_model": rng.choice(TRADE_INS) if tradein else "", "payment": payment, "status": status, "prepay": 0, "paid_now": 0}
            order["payable"] = subtotal - order_discount - tradein
            number += 1
            if payment == "Trả góp":
                periods = rng.choice([6, 9, 12])
                each = round(order["payable"] * 0.7 / periods / 100000) * 100000
                order["prepay"] = order["payable"] - each * periods
                order["periods"], order["each"] = periods, each
                if status != "Huỷ":
                    remaining = order["payable"] - order["prepay"]
                    for k in range(1, periods + 1):
                        amount = each if k < periods else remaining - each * (periods - 1)
                        due = add_months(day, k)
                        paid_date, paid = None, 0
                        if due <= TODAY:
                            roll = rng.random()
                            if roll < 0.75:
                                paid_date, paid = min(due + dt.timedelta(days=rng.randint(-3, 2)), TODAY), amount
                            elif roll < 0.85:
                                late = due + dt.timedelta(days=12)
                                paid_date, paid = (late, amount) if late <= TODAY else (None, 0)
                            elif roll < 0.92:
                                paid_date, paid = min(due, TODAY), amount // 2
                        if paid_date and paid_date <= day:
                            paid_date = day + dt.timedelta(days=1)
                        installments.append({"order_id": order["order_id"], "k": k, "due": due, "amount": amount, "paid_date": paid_date, "paid": paid})
            if payment == "Ghi nợ":
                order["paid_now"] = rng.choice([0, order["payable"] // 2 // 1000 * 1000])
                if status != "Huỷ":
                    left = order["payable"] - order["paid_now"]
                    for _ in range(rng.choice([0, 1, 2])):
                        when = day + dt.timedelta(days=rng.randint(5, 60))
                        if when <= TODAY and left > 0:
                            amount = min(left, rng.choice([1000000, 2000000, left]))
                            debt_payments.append({"order_id": order["order_id"], "date": when, "amount": amount})
                            left -= amount
            orders.append(order)
        day += dt.timedelta(days=1)
    sold = [(o, line) for o in orders if o["status"] == "Hoàn thành" for line in o["lines"] if line["imei"]]
    for _ in range(28):
        o, line = rng.choice(sold)
        received = o["date"] + dt.timedelta(days=rng.randint(10, 120))
        if received <= TODAY:
            claims.append({"date": received, "imei": line["imei"], "product": line["product"], "issue": rng.choice(["Lỗi màn hình", "Pin chai", "Không nhận sim", "Loa rè"])})
    claims.sort(key=lambda c: c["date"])
    expenses = []
    month_start = dt.date(START.year, START.month, 1)
    while month_start <= TODAY:
        for item, amount in (("Tiền mặt bằng", 15000000), ("Lương nhân viên", 24000000), ("Điện nước", rng.choice([1800000, 2100000, 2500000]))):
            expenses.append({"date": month_start + dt.timedelta(days=rng.randint(0, 9)), "item": item, "amount": amount})
        if rng.random() < 0.4:
            expenses.append({"date": month_start + dt.timedelta(days=rng.randint(10, 25)), "item": "Sửa máy lạnh", "amount": 1200000})
        month_start = add_months(month_start, 1)
    globals()["EXPENSES"] = [e for e in expenses if e["date"] <= TODAY]
    return orders, installments, debt_payments, claims


# ------------------------------------------------------------------ reference answers (rulebook, independent of the solver)

def in_period(day, start, end):
    return start <= day <= end


def month(y, m):
    return dt.date(y, m, 1), dt.date(y, m, cal.monthrange(y, m)[1])


def reference(orders, installments, debt_payments, claims):
    valid = [o for o in orders if o["status"] == "Hoàn thành"]
    debt_orders = [o for o in orders if o["payment"] in ("Ghi nợ", "Trả góp") and o["status"] != "Huỷ"]
    debt_ids = {o["order_id"] for o in debt_orders}
    later = [(p["order_id"], p["date"], p["amount"]) for p in debt_payments if p["order_id"] in debt_ids]
    later += [(i["order_id"], i["paid_date"], i["paid"]) for i in installments if i["paid_date"] and i["order_id"] in debt_ids]
    feb, mar, q1 = month(2026, 2), month(2026, 3), (dt.date(2026, 1, 1), dt.date(2026, 3, 31))

    def revenue(period, keep=lambda line: True):
        return sum(line["amount"] for o in valid if in_period(o["date"], *period) for line in o["lines"] if keep(line))

    def outstanding(day, who=None):
        total = 0
        for o in debt_orders:
            if o["date"] > day or (who and o["customer"][1] != who):
                continue
            paid = o["paid_now"] + o["prepay"] + sum(a for oid, d, a in later if oid == o["order_id"] and d <= day)
            total += max(0, o["payable"] - paid)
        return total

    overdue = [i for i in installments if i["order_id"] in debt_ids and i["due"] < TODAY and (i["paid"] if i["paid_date"] and i["paid_date"] <= TODAY else 0) < i["amount"]]
    customer_of = {o["order_id"]: o["customer"] for o in orders}
    phones_q1 = sum(line["qty"] for o in valid if in_period(o["date"], *q1) for line in o["lines"] if is_phone(line["product"]))
    claims_q1 = sum(1 for c in claims if in_period(c["date"], *q1))
    by_line, by_brand = defaultdict(int), defaultdict(int)
    for o in valid:
        for line in o["lines"]:
            if is_phone(line["product"]):
                if in_period(o["date"], *q1):
                    by_line[model_line(line["product"])] += line["qty"]
                if in_period(o["date"], *q1):
                    by_brand[brand(line["product"])] += line["qty"]

    def top(counter):
        ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[0][0] if len(ranked) == 1 or ranked[0][1] > ranked[1][1] else None   # ties are excluded like the official test

    inst_orders = {i["order_id"] for i in installments if i["order_id"] in debt_ids}
    paid_by_today = lambda i: i["paid"] if i["paid_date"] and i["paid_date"] <= TODAY else 0
    gross_feb = sum(line["amount"] - line["qty"] * line["cost"] for o in valid if in_period(o["date"], *feb) for line in o["lines"])
    feb_valid = [o for o in valid if in_period(o["date"], *feb)]
    samsung_q1 = Counter(line["product"] for o in valid if in_period(o["date"], *q1) for line in o["lines"] for _ in range(line["qty"]) if is_phone(line["product"]) and brand(line["product"]) == "Samsung")
    money = lambda x: int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    debtor = max((o["customer"][1] for o in debt_orders if o["customer"][0]), key=lambda name: (outstanding(TODAY, name), name))
    qa = [
        ("Doanh thu tháng 2/2026 là bao nhiêu?", "number", revenue(feb)),
        ("Tháng 2/2026 tiền thu máy cũ là bao nhiêu?", "number", sum(o["tradein"] for o in valid if in_period(o["date"], *feb))),
        ("Tổng tiền khách phải trả tháng 2/2026 là bao nhiêu?", "number", sum(o["payable"] for o in valid if in_period(o["date"], *feb))),
        ("Tháng 2/2026 có bao nhiêu đơn trả góp?", "number", sum(1 for o in valid if o["payment"] == "Trả góp" and in_period(o["date"], *feb))),
        ("Tháng 3/2026 tiền góp thu được là bao nhiêu?", "number", sum(i["paid"] for i in installments if i["order_id"] in debt_ids and i["paid_date"] and in_period(i["paid_date"], *mar))),
        ("Tháng 3/2026 thu nợ được bao nhiêu tiền?", "number", sum(a for _, d, a in later if in_period(d, *mar))),
        ("Tính đến hết ngày 28/02/2026 khách còn nợ tổng cộng bao nhiêu?", "number", outstanding(dt.date(2026, 2, 28))),
        ("Đến hôm nay có bao nhiêu kỳ góp quá hạn?", "number", len(overdue)),
        ("Tổng số tiền góp quá hạn tính đến hôm nay là bao nhiêu?", "number", sum(i["amount"] - (i["paid"] if i["paid_date"] and i["paid_date"] <= TODAY else 0) for i in overdue)),
        ("Hiện có bao nhiêu khách có kỳ góp quá hạn?", "number", len({customer_of[i["order_id"]][0] for i in overdue if customer_of[i["order_id"]][0]})),
        ("Tháng 2/2026 khách mua chịu thêm bao nhiêu tiền?", "number", sum(o["payable"] - o["paid_now"] - o["prepay"] for o in debt_orders if in_period(o["date"], *feb))),
        ("Tỉ lệ bảo hành quý 1/2026 là bao nhiêu phần trăm?", "percent", round(100 * claims_q1 / phones_q1, 2)),
        ("Quý 1/2026 có bao nhiêu lượt bảo hành?", "number", claims_q1),
        ("Dòng máy nào bán chạy nhất quý 1/2026?", "string", top(by_line)),
        ("Hãng nào bán được nhiều máy nhất quý 1/2026?", "string", top(by_brand)),
        ("Doanh thu máy Apple tháng 3/2026 là bao nhiêu?", "number", revenue(mar, lambda line: is_phone(line["product"]) and brand(line["product"]) == "Apple")),
        ("Quý 1/2026 bán được bao nhiêu máy iPhone 15?", "number", sum(line["qty"] for o in valid if in_period(o["date"], *q1) for line in o["lines"] if model_line(line["product"]) == "iPhone 15")),
        ("Quý 1/2026 có bao nhiêu đơn bị huỷ?", "number", sum(1 for o in orders if o["status"] == "Huỷ" and in_period(o["date"], *q1))),
        ("Quý 1/2026 có bao nhiêu đơn khách trả hàng?", "number", sum(1 for o in orders if o["status"] == "Trả hàng" and in_period(o["date"], *q1))),
        ("Tháng 2/2026 cửa hàng có bao nhiêu đơn?", "number", sum(1 for o in valid if in_period(o["date"], *feb))),
        (f"Khách {debtor} còn nợ bao nhiêu tính đến hôm nay?", "number", outstanding(TODAY, debtor)),
        ("Tính đến hôm nay khách còn phải góp bao nhiêu tiền?", "number", sum(i["amount"] - paid_by_today(i) for i in installments if i["order_id"] in debt_ids)),
        ("Tháng 3/2026 có bao nhiêu kỳ góp đến hạn?", "number", sum(1 for i in installments if i["order_id"] in debt_ids and in_period(i["due"], *mar))),
        ("Hiện có bao nhiêu khách đang trả góp?", "number", len({customer_of[i["order_id"]][0] for i in installments if i["order_id"] in debt_ids and customer_of[i["order_id"]][0] and paid_by_today(i) < i["amount"]})),
        ("Tháng 2/2026 cửa hàng thu bao nhiêu máy cũ?", "number", sum(1 for o in valid if o["tradein"] and in_period(o["date"], *feb))),
        ("Doanh thu tháng 2/2026 từ các đơn trả góp là bao nhiêu?", "number", sum(line["amount"] for o in valid if o["order_id"] in inst_orders and in_period(o["date"], *feb) for line in o["lines"])),
        ("Lãi gộp tháng 2/2026 là bao nhiêu?", "number", gross_feb),
        ("Lãi ròng tháng 2/2026 là bao nhiêu?", "number", gross_feb - sum(e["amount"] for e in EXPENSES if in_period(e["date"], *feb))),
        # Trade-in shares and average (§6 Thu máy cũ; valid invoices of the period, §1).
        ("Tháng 2/2026 tỉ lệ đơn có thu máy cũ là bao nhiêu phần trăm?", "percent", round(100 * sum(1 for o in feb_valid if o["tradein"]) / len(feb_valid), 2)),
        ("Tháng 2/2026 tiền thu máy cũ chiếm bao nhiêu phần trăm doanh thu?", "percent", round(100 * sum(o["tradein"] for o in feb_valid) / revenue(feb), 2)),
        ("Tháng 2/2026 trung bình mỗi đơn có thu máy cũ được trừ bao nhiêu tiền?", "number", money(sum(o["tradein"] for o in feb_valid) / max(1, sum(1 for o in feb_valid if o["tradein"])))),
        # Brand share, products of a brand, orders of a brand.
        ("Quý 1/2026 máy Apple chiếm bao nhiêu phần trăm số máy bán ra?", "percent", round(100 * by_brand["Apple"] / phones_q1, 2)),
        ("Quý 1/2026 máy Samsung nào bán chạy nhất?", "string", top(samsung_q1)),
        ("Quý 1/2026 có bao nhiêu đơn mua máy Apple?", "number", sum(1 for o in valid if in_period(o["date"], *q1) and any(is_phone(l["product"]) and brand(l["product"]) == "Apple" for l in o["lines"]))),
        # Down payment is not an installment; installments still owed.
        ("Tháng 2/2026 tiền trả trước của các đơn trả góp là bao nhiêu?", "number", sum(o["prepay"] for o in feb_valid if o["payment"] == "Trả góp")),
        ("Tính đến hôm nay khách còn nợ tiền góp bao nhiêu?", "number", sum(i["amount"] - paid_by_today(i) for i in installments if i["order_id"] in debt_ids)),
        # Warranty book: by phone line and by fault.
        ("Quý 1/2026 dòng máy nào có nhiều lượt bảo hành nhất?", "string", top(Counter(model_line(c["product"]) for c in claims if in_period(c["date"], *q1)))),
        ("Quý 1/2026 lỗi nào bảo hành nhiều nhất?", "string", top(Counter(c["issue"] for c in claims if in_period(c["date"], *q1)))),
        ("Quý 1/2026 có bao nhiêu lượt bảo hành máy iPhone 15?", "number", sum(1 for c in claims if in_period(c["date"], *q1) and model_line(c["product"]) == "iPhone 15")),
    ]
    return [(q, t, a) for q, t, a in qa if a is not None]


# ------------------------------------------------------------------ layouts

SALES = ["Mã HĐ", "Ngày bán", "Mã KH", "Khách hàng", "SĐT", "Tên hàng", "IMEI", "SL", "Đơn giá", "Giá vốn", "Giảm giá", "Thành tiền", "Giảm giá HĐ", "Thu máy cũ", "Hình thức TT", "Trả trước", "Trạng thái", "Ghi chú"]
SALES_DESC = {"Mã HĐ": "mã hoá đơn", "Ngày bán": "ngày bán", "Mã KH": "mã khách", "Khách hàng": "tên khách (Khách lẻ nếu không lưu)", "SĐT": "số điện thoại khách",
              "Tên hàng": "tên máy hoặc phụ kiện", "IMEI": "số IMEI của máy", "SL": "số lượng", "Đơn giá": "giá bán một cái", "Giá vốn": "giá vốn một cái",
              "Giảm giá": "giảm giá trên dòng",
              "Thành tiền": "thành tiền dòng sau giảm", "Giảm giá HĐ": "giảm giá trên cả hoá đơn", "Thu máy cũ": "tiền thu máy cũ của khách, trừ vào hoá đơn",
              "Hình thức TT": "hình thức thanh toán (tiền mặt, chuyển khoản, trả góp, ghi nợ)", "Trả trước": "tiền khách trả ngay khi mua (đơn trả góp, đơn ghi nợ)",
              "Trạng thái": "trạng thái đơn", "Ghi chú": "ghi chú"}
GOP = ["Mã HĐ", "Kỳ", "Hạn góp", "Số tiền góp", "Ngày góp", "Đã góp"]
GOP_DESC = {"Mã HĐ": "mã hoá đơn trả góp", "Kỳ": "kỳ góp thứ mấy", "Hạn góp": "ngày đến hạn của kỳ", "Số tiền góp": "số tiền phải góp kỳ đó",
            "Ngày góp": "ngày khách góp", "Đã góp": "số tiền khách đã góp kỳ đó"}
THU = ["Mã HĐ", "Ngày thu", "Số tiền thu"]
THU_DESC = {"Mã HĐ": "mã hoá đơn ghi nợ", "Ngày thu": "ngày khách trả nợ", "Số tiền thu": "số tiền khách trả"}
BH = ["Ngày nhận", "IMEI", "Tên máy", "Lỗi"]
BH_DESC = {"Ngày nhận": "ngày nhận máy bảo hành", "IMEI": "IMEI máy", "Tên máy": "tên máy", "Lỗi": "lỗi khách báo"}


def ddmmyyyy(day):
    return day.strftime("%d/%m/%Y") if day else ""


def sales_rows(orders, *, status_column=True, tradein_line=False, first_line_only=False):
    rows = []
    for o in orders:
        note = {"Huỷ": "Huỷ đơn", "Trả hàng": "Khách trả máy"}.get(o["status"], "") if not status_column else ""
        lines = list(o["lines"])
        if tradein_line and o["tradein"]:
            lines.append({"product": f"Thu máy cũ {o['tradein_model']}", "qty": 1, "price": -o["tradein"], "discount": 0, "amount": -o["tradein"], "imei": ""})
        for index, line in enumerate(lines):
            head = index == 0 or not first_line_only
            row = {"Mã HĐ": o["order_id"] if head else "", "Ngày bán": ddmmyyyy(o["date"]) if head else "", "Mã KH": o["customer"][0] if head else "",
                   "Khách hàng": o["customer"][1] if head else "", "SĐT": o["customer"][2] if head else "", "Tên hàng": line["product"], "IMEI": line["imei"],
                   "SL": line["qty"], "Đơn giá": line["price"], "Giá vốn": line.get("cost", ""), "Giảm giá": line["discount"], "Thành tiền": line["amount"],
                   "Giảm giá HĐ": o["order_discount"] if head else "", "Thu máy cũ": "" if tradein_line else (o["tradein"] if head else ""),
                   "Hình thức TT": o["payment"] if head else "", "Trả trước": (o["prepay"] or o["paid_now"] or "") if head else "",
                   "Trạng thái": o["status"] if head else "", "Ghi chú": note if head else ""}
            rows.append(row)
    return rows


def readme(files):
    text = "# Dữ liệu cửa hàng điện thoại\n\nDữ liệu có đến hết 20/03/2026. Mỗi dòng sổ bán hàng là một dòng hàng của hoá đơn.\n\n## File\n"
    text += "".join(f"- `{name}` ({name.rsplit('.', 1)[-1]})\n" for name in dict.fromkeys(key.split("/")[0] for key in files))
    for name, (columns, desc) in files.items():
        title = f"`{name.split('/')[0]}`, sheet `{name.split('/')[1]}`" if "/" in name else f"`{name}`"
        text += f"\n## Cột trong {title}\n" + "".join(f"- **{c}**: {desc.get(c, c)}\n" for c in columns)
    return text


def write_csv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow([row.get(c, "") if isinstance(row, dict) else row[i] for i, c in enumerate(header)])


def write_xlsx(path, sheets):
    book = Workbook()
    book.remove(book.active)
    for name, (header, rows) in sheets.items():
        sheet = book.create_sheet(name)
        sheet.append(header)
        for row in rows:
            sheet.append([row.get(c, "") for c in header])
    book.save(path)


def gop_rows(installments):
    return [{"Mã HĐ": i["order_id"], "Kỳ": i["k"], "Hạn góp": ddmmyyyy(i["due"]), "Số tiền góp": i["amount"], "Ngày góp": ddmmyyyy(i["paid_date"]), "Đã góp": i["paid"] or ""} for i in installments]


def thu_rows(payments):
    return [{"Mã HĐ": p["order_id"], "Ngày thu": ddmmyyyy(p["date"]), "Số tiền thu": p["amount"]} for p in payments]


def bh_rows(claims):
    return [{"Ngày nhận": ddmmyyyy(c["date"]), "IMEI": c["imei"], "Tên máy": c["product"], "Lỗi": c["issue"]} for c in claims]


def layouts(orders, installments, payments, claims):
    std = sales_rows(orders)
    yield "phone_xlsx", lambda d: (write_xlsx(d / "QuanLyBanHang.xlsx", {"BanHang": (SALES, std), "TraGop": (GOP, gop_rows(installments)), "ThuNo": (THU, thu_rows(payments)), "BaoHanh": (BH, bh_rows(claims))}),
                                   {"QuanLyBanHang.xlsx/BanHang": (SALES, SALES_DESC), "QuanLyBanHang.xlsx/TraGop": (GOP, GOP_DESC), "QuanLyBanHang.xlsx/ThuNo": (THU, THU_DESC), "QuanLyBanHang.xlsx/BaoHanh": (BH, BH_DESC)})
    notes = [c for c in SALES if c != "Trạng thái"]
    yield "phone_notes_status", lambda d: (write_csv(d / "ban_hang.csv", notes, sales_rows(orders, status_column=False)), write_csv(d / "tra_gop.csv", GOP, gop_rows(installments)),
                                           write_csv(d / "thu_no.csv", THU, thu_rows(payments)), write_csv(d / "bao_hanh.csv", BH, bh_rows(claims)),
                                           {"ban_hang.csv": (notes, SALES_DESC), "tra_gop.csv": (GOP, GOP_DESC), "thu_no.csv": (THU, THU_DESC), "bao_hanh.csv": (BH, BH_DESC)})[-1]
    snake = {c: c for c in SALES}
    plain = ["ma_hd", "ngay_ban", "ma_kh", "ten_khach", "sdt", "ten_hang", "imei", "so_luong", "don_gia", "gia_von", "giam_gia", "thanh_tien", "giam_gia_hd", "thu_may_cu", "hinh_thuc_tt", "tra_truoc", "trang_thai", "ghi_chu"]
    rename = dict(zip(SALES, plain))
    def renamed(rows, mapping):
        return [{mapping[k]: v for k, v in r.items()} for r in rows]
    gop_plain = dict(zip(GOP, ["ma_hd", "ky", "han_gop", "so_tien_gop", "ngay_gop", "da_gop"]))
    yield "phone_csv_plain", lambda d: (write_csv(d / "ban_hang.csv", plain, renamed(std, rename)), write_csv(d / "tra_gop.csv", list(gop_plain.values()), renamed(gop_rows(installments), gop_plain)),
                                        write_csv(d / "thu_no.csv", ["ma_hd", "ngay_thu", "so_tien_thu"], renamed(thu_rows(payments), dict(zip(THU, ["ma_hd", "ngay_thu", "so_tien_thu"])))),
                                        write_csv(d / "bao_hanh.csv", ["ngay_nhan", "imei", "ten_may", "loi"], renamed(bh_rows(claims), dict(zip(BH, ["ngay_nhan", "imei", "ten_may", "loi"])))),
                                        {"ban_hang.csv": (plain, {rename[k]: v for k, v in SALES_DESC.items()}), "tra_gop.csv": (list(gop_plain.values()), {gop_plain[k]: v for k, v in GOP_DESC.items()}),
                                         "thu_no.csv": (["ma_hd", "ngay_thu", "so_tien_thu"], {}), "bao_hanh.csv": (["ngay_nhan", "imei", "ten_may", "loi"], {})})[-1]
    tradein_cols = [c for c in SALES if c != "Thu máy cũ"]
    yield "phone_tradein_line", lambda d: (write_xlsx(d / "SoBanHang.xlsx", {"BanHang": (tradein_cols, sales_rows(orders, tradein_line=True)), "TraGop": (GOP, gop_rows(installments)),
                                                                              "ThuNo": (THU, thu_rows(payments)), "BaoHanh": (BH, bh_rows(claims))}),
                                           {"SoBanHang.xlsx/BanHang": (tradein_cols, SALES_DESC | {"Tên hàng": "tên máy, phụ kiện; dòng 'Thu máy cũ …' ghi tiền âm là máy cũ khách đổi"}),
                                            "SoBanHang.xlsx/TraGop": (GOP, GOP_DESC), "SoBanHang.xlsx/ThuNo": (THU, THU_DESC), "SoBanHang.xlsx/BaoHanh": (BH, BH_DESC)})[-1]
    yield "phone_grouped_rows", lambda d: (write_xlsx(d / "QuanLy.xlsx", {"BanHang": (SALES, sales_rows(orders, first_line_only=True)), "TraGop": (GOP, gop_rows(installments)),
                                                                          "ThuNo": (THU, thu_rows(payments)), "BaoHanh": (BH, bh_rows(claims))}),
                                           {"QuanLy.xlsx/BanHang": (SALES, SALES_DESC | {"Mã HĐ": "mã hoá đơn; chỉ ghi ở dòng đầu, các dòng sau để trống là cùng hoá đơn"}),
                                            "QuanLy.xlsx/TraGop": (GOP, GOP_DESC), "QuanLy.xlsx/ThuNo": (THU, THU_DESC), "QuanLy.xlsx/BaoHanh": (BH, BH_DESC)})[-1]
    yield "phone_contract_books", lambda d: contract_books(orders, installments, payments, claims, d)
    short = ["Số HĐ", "Ng.bán", "Mã KH", "Tên KH", "ĐT", "Máy/PK", "IMEI", "SL", "ĐG", "GV", "CK", "TT", "CK HĐ", "Máy cũ", "HTTT", "TT trước", "TT đơn", "GC"]
    abbrev = dict(zip(SALES, short))
    yield "phone_abbrev", lambda d: (write_csv(d / "sobanhang.csv", short, renamed(std, abbrev)), write_csv(d / "sogop.csv", GOP, gop_rows(installments)),
                                     write_csv(d / "thuno.csv", THU, thu_rows(payments)), write_csv(d / "baohanh.csv", BH, bh_rows(claims)),
                                     {"sobanhang.csv": (short, {abbrev[k]: v for k, v in SALES_DESC.items()}), "sogop.csv": (GOP, GOP_DESC), "thuno.csv": (THU, THU_DESC), "baohanh.csv": (BH, BH_DESC)})[-1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        shutil.rmtree(args.out)
    split = args.out / "split"
    (split / "data").mkdir(parents=True)
    shutil.copy(ROOT / "public_test" / "rulebook.md", split / "rulebook.md")   # the released rulebook is the spec
    shutil.copy(ROOT / "train" / "calendar.csv", split / "calendar.csv")
    orders, installments, payments, claims = generate()
    qa = reference(orders, installments, payments, claims)
    questions, answers = [], []
    for name, write in layouts(orders, installments, payments, claims):
        folder = split / "data" / name
        folder.mkdir()
        described = write(folder)
        described = described[-1] if isinstance(described, tuple) else described
        write_csv(folder / "chi_phi.csv", ["Ngày", "Khoản chi", "Số tiền"], [{"Ngày": ddmmyyyy(e["date"]), "Khoản chi": e["item"], "Số tiền": e["amount"]} for e in EXPENSES])
        described["chi_phi.csv"] = (["Ngày", "Khoản chi", "Số tiền"], {"Ngày": "ngày chi", "Khoản chi": "nội dung khoản chi", "Số tiền": "số tiền chi"})
        (folder / "README.md").write_text(readme(described))
        for index, (question, kind, answer) in enumerate(qa):
            qid = f"{name}_{index:02d}"
            questions.append({"id": qid, "business_id": name, "question": question, "as_of_date": TODAY.isoformat()})
            answers.append({"id": qid, "type": kind, "answer": answer, "alias": []})
    para = {"Doanh thu tháng 2/2026 là bao nhiêu?": "tháng 2 năm nay bán được bao nhiêu tiền hàng",
            "Tháng 2/2026 tiền thu máy cũ là bao nhiêu?": "T2/2026 thu máy cũ của khách hết bao nhiêu tiền?",
            "Tổng tiền khách phải trả tháng 2/2026 là bao nhiêu?": "Tháng 2/2026 khách phải trả tổng cộng bao nhiêu?",
            "Tháng 2/2026 có bao nhiêu đơn trả góp?": "Bao nhiêu đơn mua trả góp trong tháng 2/2026?",
            "Tháng 3/2026 tiền góp thu được là bao nhiêu?": "tháng 3/2026 khách góp được bao nhiêu tiền",
            "Tháng 3/2026 thu nợ được bao nhiêu tiền?": "Tháng 3/2026 khách trả nợ tổng cộng bao nhiêu?",
            "Tính đến hết ngày 28/02/2026 khách còn nợ tổng cộng bao nhiêu?": "Đến hết 28/02/2026 tổng nợ của khách là bao nhiêu?",
            "Đến hôm nay có bao nhiêu kỳ góp quá hạn?": "Bao nhiêu kỳ trả góp đang trễ hạn tính đến hôm nay?",
            "Tỉ lệ bảo hành quý 1/2026 là bao nhiêu phần trăm?": "Q1/2026 tỉ lệ máy bảo hành là bao nhiêu %?",
            "Hãng nào bán được nhiều máy nhất quý 1/2026?": "Quý 1/2026 hãng điện thoại nào bán chạy nhất?",
            "Quý 1/2026 bán được bao nhiêu máy iPhone 15?": "quý 1 năm nay bán mấy chiếc iPhone 15",
            "Quý 1/2026 có bao nhiêu đơn khách trả hàng?": "Q1/2026 khách trả lại bao nhiêu đơn hàng?",
            "Lãi gộp tháng 2/2026 là bao nhiêu?": "Tháng 2/2026 lời gộp được bao nhiêu?"}
    held_q, held_a = [], []
    for q, a in zip(questions, answers):
        if q["question"] in para:
            held_q.append(q | {"id": q["id"] + "_p", "question": para[q["question"]]})
            held_a.append(a | {"id": a["id"] + "_p"})
    (args.out / "questions_heldout.jsonl").write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in held_q))
    (args.out / "answers_heldout.jsonl").write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in held_a))
    (args.out / "questions.jsonl").write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions))
    (args.out / "answers.jsonl").write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in answers))
    print(json.dumps({"shops": len({q['business_id'] for q in questions}), "questions": len(questions), "orders": len(orders), "installment_rows": len(installments),
                      "debt_payments": len(payments), "claims": len(claims)}, ensure_ascii=False))
    for question, kind, answer in qa:
        print(f"  {kind:7s} {answer!s:>14}  {question}")



# ------------------------------------------------------------------ contract-style books (one header row per contract / debt, payment rows below)

ABBREVIATIONS = {"Ốp lưng iPhone 15": "Ốp IP15", "Cáp sạc Type-C 1m": "Cáp C", "Tai nghe AirPods Pro 2": "APP2", "Cường lực Samsung A55": "CL A55",
                 "iPhone 15 128GB": "IP15 128", "iPhone 15 Pro Max 256GB": "IP15PM 256", "iPhone 13 128GB": "IP13 128"}


def money_text(value, rng):
    if not value:
        return ""
    styles = [str(value), f"{value:,}đ".replace(",", ".")]
    if value % 1000 == 0:
        styles.append(f"{value // 1000}k")
    if value % 100000 == 0 and value >= 1000000:
        styles.append(f"{value / 1000000:g}tr".replace(".", ","))
    return rng.choice(styles)


def contract_books(orders, installments, payments, claims, folder, seed=5):
    rng = random.Random(seed)
    def who(customer):
        if not customer[0]:
            return "lẻ"
        phone = customer[2]
        return rng.choice([f"{customer[1]} {phone}", f"{customer[1]} - {phone[:4]} {phone[4:7]} {phone[7:]}", f"{customer[1]} ({phone})"])
    sales = []
    for o in orders:
        note = {"Huỷ": "huỷ", "Trả hàng": "khách trả lại máy"}.get(o["status"], "")
        paid = o["prepay"] if o["payment"] == "Trả góp" else o["paid_now"] if o["payment"] == "Ghi nợ" else o["payable"]
        for line in o["lines"]:
            sales.append({"Ngày": ddmmyyyy(o["date"]), "STT": o["order_id"][2:].lstrip("0"), "Khách": who(o["customer"]), "Hàng": ABBREVIATIONS.get(line["product"], line["product"]),
                          "IMEI": line["imei"], "SL": line["qty"], "Giá": line["price"], "Giá vốn": line["cost"], "Thành tiền": line["amount"],
                          "Bớt": money_text(o["order_discount"], rng), "Khách trả": paid, "Ghi chú": note})
    by_order = {o["order_id"]: o for o in orders}
    gop = []
    for order_id in dict.fromkeys(i["order_id"] for i in installments):
        o = by_order[order_id]
        stt = order_id[2:].lstrip("0")
        paid_rows = [i for i in installments if i["order_id"] == order_id and i["paid_date"]]
        head = {"Ngày": ddmmyyyy(o["date"]), "STT": stt, "Khách": o["customer"][1], "SĐT": o["customer"][2], "Máy": o["lines"][0]["product"], "Giá": o["payable"],
                "Trả trước": o["prepay"], "Số kỳ": o["periods"], "Góp/tháng": o["each"], "Hạn góp (ngày)": o["date"].day}
        if not paid_rows:
            gop.append(head)
        for index, i in enumerate(paid_rows):
            row = head if index == 0 else {"STT": stt}
            gop.append(row | {"Ngày góp": ddmmyyyy(i["paid_date"]), "Đã góp": money_text(i["paid"], rng)})
    no = []
    for o in orders:
        if o["payment"] != "Ghi nợ" or o["status"] == "Huỷ":
            continue
        stt = f"STT {o['order_id'][2:].lstrip('0')}"
        rows = [p for p in payments if p["order_id"] == o["order_id"]]
        head = {"Ngày": ddmmyyyy(o["date"]), "Khách": o["customer"][1], "SĐT": o["customer"][2], "Nợ": money_text(o["payable"] - o["paid_now"], rng), "Ghi chú": stt}
        if not rows:
            no.append(head)
        for index, p in enumerate(rows):
            no.append((head if index == 0 else {"Ghi chú": stt}) | {"Đã trả": p["amount"], "Ngày trả": ddmmyyyy(p["date"])})
    trade = [{"Ngày": ddmmyyyy(o["date"]), "Khách": o["customer"][1], "SĐT": o["customer"][2], "Máy thu": o["tradein_model"], "Tình trạng": "trầy nhẹ",
              "Giá thu": money_text(o["tradein"], rng), "Trừ vào (STT sổ bán)": o["order_id"][2:].lstrip("0")} for o in orders if o["tradein"]]
    warranty = [{"Ngày nhận": ddmmyyyy(c["date"]), "Máy": c["product"], "IMEI": c["imei"], "Lỗi": c["issue"]} for c in claims]
    short = [{"Ghi tắt": v, "Tên hàng": k, "Loại": "máy" if is_phone(k) else "phụ kiện"} for k, v in ABBREVIATIONS.items()]
    sheets = {"SoBan": (["Ngày", "STT", "Khách", "Hàng", "IMEI", "SL", "Giá", "Giá vốn", "Thành tiền", "Bớt", "Khách trả", "Ghi chú"], sales),
              "Ghi tắt": (["Ghi tắt", "Tên hàng", "Loại"], short)}
    write_xlsx(folder / "SoBanHang.xlsx", sheets)
    write_xlsx(folder / "SoNo.xlsx", {"SoNo": (["Ngày", "Khách", "SĐT", "Nợ", "Đã trả", "Ngày trả", "Ghi chú"], no),
                                      "TraGop": (["Ngày", "STT", "Khách", "SĐT", "Máy", "Giá", "Trả trước", "Số kỳ", "Góp/tháng", "Hạn góp (ngày)", "Ngày góp", "Đã góp"], gop),
                                      "BaoHanh": (["Ngày nhận", "Máy", "IMEI", "Lỗi"], warranty)})
    write_xlsx(folder / "ThuMayCu.xlsx", {"ThuMayCu": (["Ngày", "Khách", "SĐT", "Máy thu", "Tình trạng", "Giá thu", "Trừ vào (STT sổ bán)"], trade)})
    return {"SoBanHang.xlsx": ([], {}), "SoNo.xlsx": ([], {}), "ThuMayCu.xlsx": ([], {})}


if __name__ == "__main__":
    main()
