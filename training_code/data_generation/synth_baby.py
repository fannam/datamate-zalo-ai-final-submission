"""Synthetic mother-and-baby shops from the released rulebook (§1, §3, §6 Mẹ và bé, §8) with an independent
reference answer key. Nothing here reads public questions or public shop files.

Writes <out>/split/..., <out>/questions.jsonl, answers.jsonl and a held-out paraphrase set.
Usage: .venv/bin/python training_code/data_generation/synth_baby.py --out runs/synth_baby
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import shutil
from collections import defaultdict
from pathlib import Path

from synth_phone import ROOT, ddmmyyyy, in_period, month, readme, write_csv, write_xlsx

TODAY = dt.date(2026, 3, 20)
START = dt.date(2025, 12, 1)
# code, name, brand, 'Số / Size' (blank = not classified, even if the name has a number), unit, group, price
PRODUCTS = [
    ("SP01", "Bỉm Huggies quần M 42 miếng", "Huggies", "M", "bịch", "Bỉm tã", 329000), ("SP02", "Bỉm Huggies quần L 38 miếng", "Huggies", "L", "bịch", "Bỉm tã", 329000),
    ("SP03", "Bỉm Bobby dán S 56 miếng", "Bobby", "S", "bịch", "Bỉm tã", 259000), ("SP04", "Bỉm Bobby quần M 48 miếng", "Bobby", "M", "bịch", "Bỉm tã", 279000),
    ("SP05", "Tã Merries quần L 44 miếng", "Merries", "L", "bịch", "Bỉm tã", 389000), ("SP06", "Bỉm Moony XL 38 miếng", "Moony", "XL", "bịch", "Bỉm tã", 409000),
    ("SP07", "Bỉm Pampers quần M 60", "Pampers", "", "bịch", "Bỉm tã", 359000),                       # no size recorded
    ("SP08", "Sữa Meiji số 0 800g", "Meiji", "0", "hộp", "Sữa", 529000), ("SP09", "Sữa Meiji số 1 800g", "Meiji", "1", "hộp", "Sữa", 509000),
    ("SP10", "Sữa Aptamil số 2 800g", "Aptamil", "2", "hộp", "Sữa", 619000), ("SP11", "Sữa Friso Gold 2 850g", "Friso", "2", "hộp", "Sữa", 569000),
    ("SP12", "Sữa Similac 2 900g", "Similac", "", "hộp", "Sữa", 599000),                            # name says 2, column blank
    ("SP13", "Sữa Vinamilk Dielac Alpha 3 900g", "Vinamilk", "3", "hộp", "Sữa", 289000),
    ("SP14", "Khăn ướt Mamamy 100 tờ", "Mamamy", "", "gói", "Vệ sinh", 39000), ("SP15", "Bình sữa Pigeon 160ml", "Pigeon", "", "cái", "Bình sữa", 219000),
]
NAMES = ["Nguyễn Thu Hằng", "Trần Ngọc Ánh", "Lê Minh Thư", "Phạm Thảo Vy", "Hoàng Lan Chi", "Vũ Bảo Ngọc", "Đặng Hồng Nhung", "Bùi Thanh Mai",
         "Đỗ Kim Oanh", "Ngô Hà My", "Dương Phương Thảo", "Lý Thu Trang", "Mai Ngọc Hân", "Tạ Diệu Linh", "Châu Mỹ Duyên", "Phan Gia Hân",
         "Hồ Khánh Linh", "Trịnh Thảo Nguyên"]


def generate(seed=21):
    rng = random.Random(seed)
    customers = []
    for i, name in enumerate(NAMES, start=1):
        registered = START - dt.timedelta(days=rng.randint(0, 200)) if i <= 10 else dt.date(2026, rng.choice([1, 2, 2, 3]), rng.randint(1, 19))
        customers.append({"id": f"KH{i:03d}", "name": name, "phone": f"09{rng.randint(10**7, 10**8 - 1)}", "registered": registered})
    orders, day, number = [], START, 1
    while day <= TODAY:
        for _ in range(rng.choice([1, 2, 2, 3])):
            members = [c for c in customers if c["registered"] <= day]
            customer = rng.choice(members) if members and rng.random() < 0.8 else None
            lines = []
            for product in rng.sample(PRODUCTS, rng.choice([1, 1, 2, 3])):
                qty = rng.choice([1, 1, 2, 3])
                lines.append({"code": product[0], "product": product[1], "qty": qty, "price": product[6], "amount": qty * product[6]})
            subtotal = sum(line["amount"] for line in lines)
            voucher = rng.choice([0, 0, 0, 30000, 50000]) if subtotal > 300000 else 0
            points = rng.choice([0, 0, 0, 0, 10000, 25000]) if customer else 0
            ship = rng.choice([0, 0, 15000, 25000])
            status = rng.choices(["Hoàn thành", "Huỷ", "Trả hàng"], [90, 5, 5])[0]
            orders.append({"order_id": f"HD{number:05d}", "date": day, "customer": customer, "lines": lines, "voucher": voucher, "points": points, "ship": ship, "status": status})
            number += 1
        day += dt.timedelta(days=1)
    return customers, orders


def reference(customers, orders):
    feb, q1 = month(2026, 2), (dt.date(2026, 1, 1), dt.date(2026, 3, 31))
    info = {p[0]: p for p in PRODUCTS}
    valid = [o for o in orders if o["status"] == "Hoàn thành"]
    feb_valid = [o for o in valid if in_period(o["date"], *feb)]

    def packs(period, keep):
        return sum(line["qty"] for o in valid if in_period(o["date"], *period) for line in o["lines"] if info[line["code"]][5] == "Bỉm tã" and keep(info[line["code"]]))

    diapers = packs(feb, lambda p: True)
    by_size, by_brand, by_customer = defaultdict(int), defaultdict(int), defaultdict(int)
    for o in feb_valid:
        for line in o["lines"]:
            p = info[line["code"]]
            if p[5] == "Bỉm tã":
                if p[3]:
                    by_size[p[3]] += line["qty"]
                by_brand[p[2]] += line["qty"]
        if o["customer"]:
            by_customer[(o["customer"]["id"], o["customer"]["name"])] += sum(line["amount"] for line in o["lines"])

    def top(counter, key=lambda kv: kv[0]):
        ranked = sorted(counter.items(), key=lambda kv: (-kv[1], key(kv)))
        return ranked[0][0] if len(ranked) == 1 or ranked[0][1] > ranked[1][1] else None

    best_customer = top(by_customer)
    qa = [
        ("Tháng 2/2026 tổng tiền giảm voucher là bao nhiêu?", "number", sum(o["voucher"] for o in feb_valid)),
        ("Tháng 2/2026 tiền giảm nhờ dùng điểm là bao nhiêu?", "number", sum(o["points"] for o in feb_valid)),
        ("Tháng 2/2026 tổng phí ship là bao nhiêu?", "number", sum(o["ship"] for o in feb_valid)),
        ("Tháng 2/2026 có bao nhiêu khách thành viên mới?", "number", sum(1 for c in customers if in_period(c["registered"], *feb))),
        ("Tháng 2/2026 bán được bao nhiêu bịch bỉm size M?", "number", packs(feb, lambda p: p[3] == "M")),
        ("Bỉm size L chiếm bao nhiêu phần trăm số bịch bỉm bán ra tháng 2/2026?", "percent", round(100 * packs(feb, lambda p: p[3] == "L") / diapers, 2)),
        ("Bỉm hãng Huggies chiếm bao nhiêu phần trăm số bịch bỉm tháng 2/2026?", "percent", round(100 * packs(feb, lambda p: p[2] == "Huggies") / diapers, 2)),
        ("Tháng 2/2026 bán được bao nhiêu hộp sữa số 2?", "number", sum(line["qty"] for o in feb_valid for line in o["lines"] if info[line["code"]][5] == "Sữa" and info[line["code"]][3] == "2")),
        ("Doanh thu sữa Meiji tháng 2/2026 là bao nhiêu?", "number", sum(line["amount"] for o in feb_valid for line in o["lines"] if info[line["code"]][2] == "Meiji")),
        ("Size bỉm nào bán chạy nhất tháng 2/2026?", "string", top(by_size)),
        ("Hãng bỉm nào bán được nhiều bịch nhất tháng 2/2026?", "string", top(by_brand)),
        ("Doanh thu tháng 2/2026 là bao nhiêu?", "number", sum(line["amount"] for o in feb_valid for line in o["lines"])),
        ("Tháng 2/2026 có bao nhiêu hoá đơn dùng voucher?", "number", sum(1 for o in feb_valid if o["voucher"])),
        ("Tháng 2/2026 cửa hàng có bao nhiêu đơn?", "number", len(feb_valid)),
        ("Khách nào mua nhiều tiền nhất tháng 2/2026?", "string", best_customer[1] if best_customer else None),
        ("Quý 1/2026 có bao nhiêu khách đăng ký thành viên?", "number", sum(1 for c in customers if in_period(c["registered"], *q1))),
        ("Tháng 2/2026 bán được bao nhiêu bịch bỉm huggies M?", "number", sum(line["qty"] for o in feb_valid for line in o["lines"] if line["code"] == "SP01")),
        ("Doanh thu sữa aptamil 2 tháng 2/2026 là bao nhiêu?", "number", sum(line["amount"] for o in feb_valid for line in o["lines"] if line["code"] == "SP10")),
    ]
    return [(q, t, a) for q, t, a in qa if a is not None]


CATALOG = ["Mã hàng", "Tên hàng", "Tên gọi khác", "Hãng", "Số / Size", "ĐVT", "Nhóm hàng", "Giá bán"]
ALIASES = {"SP01": "huggies M, bỉm huggies M", "SP02": "huggies L, bỉm huggies L", "SP04": "bobby M, bỉm bobby M", "SP08": "meiji 0, sữa meiji số 0",
           "SP10": "aptamil 2, sữa aptamil 2", "SP13": "dielac 3"}
CATALOG_DESC = {"Mã hàng": "mã hàng", "Tên hàng": "tên hàng", "Tên gọi khác": "tên gọi khác hay dùng, nhiều cách cách nhau dấu phẩy", "Hãng": "hãng sản xuất", "Số / Size": "số sữa hoặc size bỉm", "ĐVT": "đơn vị tính", "Nhóm hàng": "nhóm hàng", "Giá bán": "giá bán lẻ"}
MEMBERS = ["Mã KH", "Tên KH", "SĐT", "Ngày đăng ký"]
MEMBERS_DESC = {"Mã KH": "mã khách", "Tên KH": "tên khách", "SĐT": "số điện thoại", "Ngày đăng ký": "ngày đăng ký thành viên"}
SALES = ["Mã HĐ", "Ngày", "Mã KH", "Khách hàng", "Mã hàng", "Tên hàng", "SL", "Đơn giá", "Thành tiền", "Giảm voucher", "Giảm điểm", "Phí ship", "Trạng thái", "Ghi chú"]
SALES_DESC = {"Mã HĐ": "mã hoá đơn", "Ngày": "ngày bán", "Mã KH": "mã khách", "Khách hàng": "tên khách (Khách lẻ nếu không lưu)", "Mã hàng": "mã hàng", "Tên hàng": "tên hàng",
              "SL": "số lượng", "Đơn giá": "đơn giá", "Thành tiền": "thành tiền dòng", "Giảm voucher": "tiền giảm voucher của hoá đơn", "Giảm điểm": "tiền giảm nhờ dùng điểm của hoá đơn",
              "Phí ship": "phí giao hàng của hoá đơn", "Trạng thái": "trạng thái đơn", "Ghi chú": "ghi chú"}


def catalog_rows():
    return [dict(zip(CATALOG, [p[0], p[1], ALIASES.get(p[0], ""), p[2], p[3], p[4], p[5], p[6]])) for p in PRODUCTS]


def member_rows(customers):
    return [{"Mã KH": c["id"], "Tên KH": c["name"], "SĐT": c["phone"], "Ngày đăng ký": ddmmyyyy(c["registered"])} for c in customers]


def sales_rows(orders, *, fee_lines=False, first_line_only=False, status_in_notes=False):
    rows = []
    for o in orders:
        lines = [dict(line) for line in o["lines"]]
        if fee_lines:
            if o["voucher"]:
                lines.append({"code": "", "product": "Voucher giảm giá", "qty": 1, "price": -o["voucher"], "amount": -o["voucher"]})
            if o["points"]:
                lines.append({"code": "", "product": f"Dùng {o['points'] // 100} điểm", "qty": 1, "price": -o["points"], "amount": -o["points"]})
            if o["ship"]:
                lines.append({"code": "", "product": "Phí ship", "qty": 1, "price": o["ship"], "amount": o["ship"]})
        note = {"Huỷ": "Khách huỷ đơn", "Trả hàng": "Khách trả hàng"}.get(o["status"], "") if status_in_notes else ""
        for index, line in enumerate(lines):
            head = index == 0 or not first_line_only
            c = o["customer"]
            rows.append({"Mã HĐ": o["order_id"] if head else "", "Ngày": ddmmyyyy(o["date"]) if head else "", "Mã KH": (c["id"] if c else "") if head else "",
                         "Khách hàng": (c["name"] if c else "Khách lẻ") if head else "", "Mã hàng": line["code"], "Tên hàng": line["product"], "SL": line["qty"],
                         "Đơn giá": line["price"], "Thành tiền": line["amount"],
                         "Giảm voucher": "" if fee_lines else (o["voucher"] or "") if head else "", "Giảm điểm": "" if fee_lines else (o["points"] or "") if head else "",
                         "Phí ship": "" if fee_lines else (o["ship"] or "") if head else "", "Trạng thái": "" if status_in_notes else (o["status"] if head else ""),
                         "Ghi chú": note if head else ""})
    return rows


def layouts(customers, orders):
    yield "baby_xlsx", lambda d: (write_xlsx(d / "QuanLyCuaHang.xlsx", {"DanhMuc": (CATALOG, catalog_rows()), "KhachHang": (MEMBERS, member_rows(customers)), "BanHang": (SALES, sales_rows(orders))}),
                                  {"QuanLyCuaHang.xlsx/DanhMuc": (CATALOG, CATALOG_DESC), "QuanLyCuaHang.xlsx/KhachHang": (MEMBERS, MEMBERS_DESC), "QuanLyCuaHang.xlsx/BanHang": (SALES, SALES_DESC)})[-1]
    plain = [c for c in SALES if c not in ("Giảm voucher", "Giảm điểm", "Phí ship")]
    yield "baby_fee_lines", lambda d: (write_csv(d / "danh_muc.csv", CATALOG, catalog_rows()), write_csv(d / "khach_hang.csv", MEMBERS, member_rows(customers)),
                                       write_csv(d / "ban_hang.csv", plain, sales_rows(orders, fee_lines=True)),
                                       {"danh_muc.csv": (CATALOG, CATALOG_DESC), "khach_hang.csv": (MEMBERS, MEMBERS_DESC),
                                        "ban_hang.csv": (plain, SALES_DESC | {"Tên hàng": "tên hàng; dòng Voucher, 'Dùng n điểm' ghi tiền âm, dòng Phí ship là phí giao"})})[-1]
    notes = [c for c in SALES if c != "Trạng thái"]
    yield "baby_first_line", lambda d: (write_xlsx(d / "SoBanHang.xlsx", {"DanhMuc": (CATALOG, catalog_rows()), "KhachHang": (MEMBERS, member_rows(customers)),
                                                                         "BanHang": (notes, sales_rows(orders, first_line_only=True, status_in_notes=True))}),
                                        {"SoBanHang.xlsx/DanhMuc": (CATALOG, CATALOG_DESC), "SoBanHang.xlsx/KhachHang": (MEMBERS, MEMBERS_DESC),
                                         "SoBanHang.xlsx/BanHang": (notes, SALES_DESC | {"Mã HĐ": "mã hoá đơn; chỉ ghi ở dòng đầu của hoá đơn"})})[-1]
    short = ["Số HĐ", "Ngày bán", "Mã KH", "Tên KH", "Mã SP", "Sản phẩm", "SL", "ĐG", "TT", "Voucher", "Điểm", "Ship", "TT đơn", "GC"]
    abbrev = dict(zip(SALES, short))
    renamed = [{abbrev[k]: v for k, v in r.items()} for r in sales_rows(orders)]
    yield "baby_abbrev", lambda d: (write_csv(d / "danhmuc.csv", CATALOG, catalog_rows()), write_csv(d / "khachhang.csv", MEMBERS, member_rows(customers)),
                                    write_csv(d / "banhang.csv", short, renamed),
                                    {"danhmuc.csv": (CATALOG, CATALOG_DESC), "khachhang.csv": (MEMBERS, MEMBERS_DESC), "banhang.csv": (short, {abbrev[k]: v for k, v in SALES_DESC.items()})})[-1]


HELDOUT = {"Tháng 2/2026 tổng tiền giảm voucher là bao nhiêu?": "T2/2026 khách được giảm bao nhiêu tiền nhờ voucher?",
           "Tháng 2/2026 tiền giảm nhờ dùng điểm là bao nhiêu?": "tháng 2 năm nay khách dùng điểm được giảm tổng cộng bao nhiêu",
           "Tháng 2/2026 tổng phí ship là bao nhiêu?": "Tháng 2/2026 thu phí giao hàng bao nhiêu tiền?",
           "Tháng 2/2026 có bao nhiêu khách thành viên mới?": "Có bao nhiêu khách đăng ký thành viên trong tháng 2/2026?",
           "Tháng 2/2026 bán được bao nhiêu bịch bỉm size M?": "Tháng 2/2026 bỉm size M bán ra mấy bịch?",
           "Bỉm size L chiếm bao nhiêu phần trăm số bịch bỉm bán ra tháng 2/2026?": "Tháng 2/2026 bỉm size L chiếm mấy % tổng số bịch bỉm?",
           "Tháng 2/2026 bán được bao nhiêu hộp sữa số 2?": "T2/2026 sữa số 2 bán được bao nhiêu hộp?",
           "Doanh thu sữa Meiji tháng 2/2026 là bao nhiêu?": "Tháng 2/2026 bán sữa Meiji được bao nhiêu tiền?",
           "Hãng bỉm nào bán được nhiều bịch nhất tháng 2/2026?": "Tháng 2/2026 hãng bỉm nào bán chạy nhất?"}


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
    customers, orders = generate()
    qa = reference(customers, orders)
    questions, answers, held_q, held_a = [], [], [], []
    for name, write in layouts(customers, orders):
        folder = split / "data" / name
        folder.mkdir()
        described = write(folder)
        text = readme(described).replace("cửa hàng điện thoại", "cửa hàng mẹ và bé")
        (folder / "README.md").write_text(text)
        for index, (question, kind, answer) in enumerate(qa):
            qid = f"{name}_{index:02d}"
            questions.append({"id": qid, "business_id": name, "question": question, "as_of_date": TODAY.isoformat()})
            answers.append({"id": qid, "type": kind, "answer": answer, "alias": []})
            if question in HELDOUT:
                held_q.append({"id": qid + "_p", "business_id": name, "question": HELDOUT[question], "as_of_date": TODAY.isoformat()})
                held_a.append({"id": qid + "_p", "type": kind, "answer": answer, "alias": []})
    for stem, rows in (("questions", questions), ("answers", answers), ("questions_heldout", held_q), ("answers_heldout", held_a)):
        (args.out / f"{stem}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(json.dumps({"shops": 4, "questions": len(questions), "heldout": len(held_q), "orders": len(orders)}))
    for question, kind, answer in qa:
        print(f"  {kind:7s} {answer!s:>12}  {question}")


if __name__ == "__main__":
    main()
