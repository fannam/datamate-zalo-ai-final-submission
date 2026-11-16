"""Domain views and compilers for repair shops, mother-and-baby stores and phone stores.

Rules come from the released rulebook's domain sections (§6). Tables and columns are
found structurally by column roles (aliases, original headers, value sets), never by
business or question ID. Every compiler returns None when its inputs are not
unambiguous, so the question falls back instead of being answered from a guess.
"""
from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from decimal import Decimal
import datetime as dt
import re
import unicodedata

from .ingestion import quote
from .parsing import fold, name_words
from .intent import wants_percent
from .temporal import Period, resolve_periods
from .types import SQLPlan
from .roles import fallback


ROLES = {
    # Repair book (one row = one visit/ticket).
    "plate": "bien_so bien_so_xe bien_kiem_soat bks bsx bien_xe so_xe",
    "job": "lam_gi viec_lam cong_viec noi_dung_sua noi_dung_sua_chua hang_muc hang_muc_sua dich_vu sua_gi chi_tiet_sua cong_viec_sua noi_dung",
    "mechanic": "tho tho_sua tho_lam tho_chinh tho_phu_trach nguoi_sua nguoi_lam ky_thuat_vien ktv ky_thuat",
    "model": "dong_xe loai_xe ten_xe hieu_xe mau_xe xe",
    "ticket_date": "ngay ngay_sua ngay_vao ngay_nhan ngay_lap ngay_gio ngay_thang thoi_gian ngay_vao_xuong",
    "ticket_id": "so_phieu ma_phieu phieu ma_luot so_thu_tu stt",
    "total": "tong_tien thanh_tien tong_cong tong so_tien tien_thu phai_thu tong_thu khach_tra tien",
    "labor": "tien_cong cong tien_sua phi_cong",
    "parts": "tien_phu_tung phu_tung tien_vat_tu tien_linh_kien",
    "kind": "loai_phieu loai hinh_thuc phan_loai loai_dich_vu",
    "notes": "ghi_chu note",
    "commission": "hoa_hong ti_le_hoa_hong ty_le_hoa_hong hoa_hong_phan_tram phan_tram_hoa_hong",
    "staff_name": "ho_ten ho_va_ten ten_tho ten_nhan_vien ten",
    # Catalog attributes (mother and baby).
    "size": "so_size size kich_co kich_thuoc",
    "stage": "so_size so so_sua giai_doan",
    "brand": "thuong_hieu nhan_hieu hang_sx hang_san_xuat nha_san_xuat brand hang",
    "registered": "ngay_dang_ky ngay_dk ngay_tham_gia ngay_lam_the ngay_tao_the ngay_vao_hoi_vien",
    "points_balance": "diem_hien_co diem_hien_tai diem_con_lai so_diem_hien_co diem_con diem_tich_luy_hien_co",
    # Invoice-level amounts.
    "voucher": "giam_voucher tien_voucher tien_giam_voucher giam_gia_voucher voucher",
    "points": "giam_diem tien_giam_diem tien_diem tru_diem dung_diem diem_quy_doi giam_gia_diem doi_diem tien_doi_diem tien_giam_nho_dung_diem tien_giam_nho_diem",
    "ship": "phi_ship phi_giao_hang phi_van_chuyen tien_ship phi_giao tien_giao_hang ship",
    # Phone store.
    "tradein": "thu_may_cu tien_thu_may_cu tri_gia_may_cu gia_thu_cu tien_may_cu tru_may_cu gia_tri_may_cu gia_thu_may_cu thu_cu may_cu gia_thu",
    "order_discount": "giam_gia_hd giam_gia_don giam_hd ck_hd chiet_khau_hd chiet_khau_don giam_gia_hoa_don khuyen_mai_hd km_hd giam_tren_don giam_gia_tren_ca_hoa_don bot tien_bot bot_gia tien_bot_cho_ca_don",
    "prepaid": "tra_truoc tien_tra_truoc da_tra_truoc khach_tra_truoc tra_ngay tien_tra_ngay dat_coc tien_coc khach_dua_truoc tien_khach_tra_ngay khach_tra_ngay",
    "payable": "khach_phai_tra khach_can_tra phai_tra",
    "imei": "imei so_imei ma_imei serial so_serial",
    "receive_date": "ngay_nhan ngay_nhan_may ngay_tiep_nhan ngay_bao_hanh ngay",
    # Warranty claims: the phone line brought in, its fault and how it was handled.
    "claim_model": "may dong_may ten_may model may_bao_hanh",
    "claim_fault": "loi loi_may tinh_trang_loi hong_gi benh",
    "claim_action": "xu_ly cach_xu_ly huong_xu_ly ket_qua",
    "order_ref": "ma_don ma_don_hang ma_hoa_don so_hoa_don ma_hd so_hd ma_don_ban tru_vao_stt_so_ban tru_vao_stt stt_so_ban tru_vao_don tru_vao_hd stt_don so_don_ban",
    "due_date": "han_gop han_thanh_toan ngay_den_han ngay_han han_tra han",
    "due_amount": "so_tien_gop tien_gop so_tien_phai_gop tien_phai_gop phai_gop so_tien_ky tien_moi_ky so_tien",
    "paid_amount": "da_gop so_tien_da_gop tien_da_gop da_tra so_tien_da_tra",
    "paid_date": "ngay_gop ngay_tra ngay_dong ngay_thanh_toan ngay_da_gop",
    "installment_no": "ky ky_gop ky_so lan_gop",
    # Contract-style installment book: one header row per contract (periods, amount per month, due day), payment rows below.
    "installment_count": "so_ky so_ky_gop so_thang_gop ky_han_gop so_lan_gop tong_so_ky",
    "installment_amount": "gop_thang gop_moi_thang tien_gop_thang so_tien_gop_thang tien_gop_moi_ky gop_ky gop_hang_thang",
    "due_day": "han_gop_ngay han_gop_hang_thang ngay_gop_hang_thang han_ngay ngay_han_gop han_hang_thang",
    # Debt ledger: a debt row (unpaid part of an order) and repayment rows, the order written in a note ('STT 34').
    "debt_amount": "no so_tien_no tien_no no_moi ghi_no",
    "repay_amount": "da_tra tien_tra so_tien_tra tra_no",
    "repay_date": "ngay_tra ngay_tra_no",
}
ROLES = {role: names.split() for role, names in ROLES.items()}


def missing(reason):
    return SQLPlan(action="unanswerable", sql="", answer_type="unanswerable", shape="scalar", rounding="none", date_granularity="day", reason=reason[:300])


def plan(sql, kind="number", shape="scalar", rounding="integer", reason="", granularity="day"):
    return SQLPlan(action="query", sql=sql, answer_type=kind, shape=shape, rounding=rounding, date_granularity=granularity, reason=reason[:300])


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def words(value):
    return " ".join(re.findall(r"[a-z0-9]+", fold(value)))


def info(table, name):
    return next(c for c in table["columns"] if c["name"] == name)


def accented_brand(column):
    """Header or README description written 'Hãng' (brand), which folds to the same letters as 'hàng' (goods)."""
    texts = [unicodedata.normalize("NFC", column.get(key) or "").strip().lower() for key in ("original", "description")]
    return any(re.match(r"hãng(?:\s|$|\b)", text) for text in texts)


def role_column(table, role, exclude=(), kind=None):
    """First alias present in the table; long aliases also match as a prefix ('bien_so_xe')."""
    columns = [c for c in table["columns"] if c["name"] not in exclude and (kind is None or c["kind"] == kind) and c.get("nonempty", 1) > 0]
    names = [c["name"] for c in columns]
    for alias in ROLES[role]:
        if alias in names:
            return alias
    for alias in ROLES[role]:
        if len(alias) >= 5:
            for name in names:
                if name.startswith(alias + "_"):
                    return name
    # README description head ('biển số xe', 'thợ sửa (tên)') when the header itself is unseen.
    from .semantics import description_head
    heads = {}
    for column in columns:
        heads.setdefault(description_head(column), column["name"])
    for alias in ROLES[role]:
        if alias in heads:
            return heads[alias]
    for alias in ROLES[role]:
        if len(alias) >= 5:
            for head, name in heads.items():
                if head.startswith(alias + "_"):
                    return name
    return None


def date_column(table, role, exclude=()):
    return role_column(table, role, exclude, "datetime") or role_column(table, role, exclude)


def number_column(table, role, exclude=()):
    return role_column(table, role, exclude, "number") or role_column(table, role, exclude)


def picker(catalog, domain):
    """Alias match first; else the model's assignment, only when the model classified the shop as `domain`."""
    def pick(table, role, exclude=(), kind=None):
        found = {"datetime": date_column, "number": number_column}.get(kind, role_column)(table, role, exclude)
        if found:
            return found
        column = fallback(catalog, table["name"], role, domain)
        if not column or column in exclude:
            return None
        # A model-assigned date role must be a date column ('Kỳ' 1,2,3 is not a due date).
        return None if kind == "datetime" and info(table, column)["kind"] != "datetime" else column
    return pick


def ref_key(sql):
    """An order reference read from a number cell ('30.000000') matches the order code written '30'."""
    return f"regexp_replace({sql},'^(-?[0-9]+)\\.0+$','\\1')"


def txt(alias, name, table=None):
    """Text of a column; typed columns inferred from text cells are read from their recorded string."""
    if name and table is not None and info(table, name)["kind"] != "text" and info(table, name).get("inferred") == "values":
        name += "__raw"
    return f"NULLIF(TRIM(CAST({alias}.{quote(name)} AS VARCHAR)),'')" if name else "NULL"


def num(alias, table, name):
    if not name:
        return "NULL"
    return f"{alias}.{quote(name)}" if info(table, name)["kind"] == "number" else f"to_num(CAST({alias}.{quote(name)} AS VARCHAR))"


def ts(alias, table, name):
    if not name:
        return "NULL::TIMESTAMP"
    return f"{alias}.{quote(name)}" if info(table, name)["kind"] == "datetime" else f"CAST(to_day(CAST({alias}.{quote(name)} AS VARCHAR)) AS TIMESTAMP)"


def register(engine, name, columns, rows):
    con = engine.connection
    con.execute(f"CREATE TEMP TABLE {name} ({','.join(columns)})")
    if rows:
        con.executemany(f"INSERT INTO {name} VALUES ({','.join('?' for _ in columns)})", rows)
    engine.tables.add(name)


def add_receipts(engine, create, sql, source):
    """Append payment rows (order_id, paid_at, amount) to v_receipts, creating it when absent."""
    con = engine.connection
    if "v_receipts" in engine.tables:
        create("v_receipts_more", "SELECT order_id,paid_at,amount FROM v_receipts UNION ALL " + sql, ["v_receipts"] + source)
        con.execute("DROP TABLE v_receipts")
        con.execute("ALTER TABLE v_receipts_more RENAME TO v_receipts")
    else:
        create("v_receipts", sql, source)


def mark_debt(engine, orders):
    """Rulebook §6: an installment order is a debt order (§4) even when the sales book records another payment method."""
    if not orders:
        return
    register(engine, "debt_orders_tmp", ["order_id VARCHAR"], [(o,) for o in sorted(orders)])
    for view in ("v_finance", "v_orders_all", "v_orders", "v_sales_all", "v_sales"):
        if view in engine.tables:
            engine.connection.execute(f"UPDATE {view} SET payment_method='debt' WHERE order_id IN (SELECT order_id FROM debt_orders_tmp)")
    engine.connection.execute("DROP TABLE debt_orders_tmp")
    engine.tables.discard("debt_orders_tmp")


def contract_schedule(engine, table, ref, start, count, each, due_day, prepaid, paid_date, paid_amount):
    """Contract header rows (start date, periods, amount per period, due day) and payment rows -> one row per period.
    Payments fill periods in order (the oldest unpaid first); a period is paid when its amount is covered."""
    cols = [ref_key(txt("g", ref, table)), ts("g", table, start), num("g", table, count), num("g", table, each),
            num("g", table, due_day) if due_day else "NULL", num("g", table, prepaid) if prepaid else "NULL",
            ts("g", table, paid_date) if paid_date else "NULL", num("g", table, paid_amount) if paid_amount else "NULL"]
    rows = engine.connection.execute(f"SELECT {','.join(cols)} FROM {quote(table['name'])} g ORDER BY g._source_file,g._source_sheet,g._source_row").fetchall()
    contracts, pays = {}, defaultdict(list)
    for key, day, n, amount, dd, down, when, value in rows:
        if not key:
            continue
        if n and amount and day and key not in contracts:
            contracts[key] = (day.date() if hasattr(day, "date") else day, int(n), Decimal(amount), int(dd) if dd else None, Decimal(down or 0))
        if when and value:
            pays[key].append((when.date() if hasattr(when, "date") else when, Decimal(value)))
    schedule, payments, upfront = [], [], []
    for key, (day, n, amount, dd, down) in contracts.items():
        if down:
            upfront.append((key, dt.datetime.combine(day, dt.time()), down))
        periods = []
        for k in range(1, n + 1):
            index = day.year * 12 + day.month - 1 + k
            year, month = index // 12, index % 12 + 1
            periods.append([dt.date(year, month, min(dd or day.day, calendar.monthrange(year, month)[1])), amount, Decimal(0), None])
        for when, value in sorted(pays[key]):
            payments.append((key, dt.datetime.combine(when, dt.time()), value))
            for period in periods:
                if value <= 0:
                    break
                room = period[1] - period[2]
                if room <= 0:
                    continue
                used = min(room, value)
                period[2] += used
                period[3] = when
                value -= used
        schedule += [(key, dt.datetime.combine(due, dt.time()), amount_due, paid or None, dt.datetime.combine(at, dt.time()) if at else None) for due, amount_due, paid, at in periods]
    return schedule, payments, upfront


def period_bounds(period):
    return f"TIMESTAMP {literal(period.start)}", f"TIMESTAMP {literal(period.end + dt.timedelta(days=1))}"


def top_k(text, default=1):
    match = re.search(r"\btop\s*(\d{1,2})\b|(?<![/\d.,])(\d{1,2})\s+(?:tho|dong|xe|hang|size|khach|mau|may)\b", text)
    return int(next(v for v in match.groups() if v)) if match else default


# ---------------------------------------------------------------- repair shops

WARRANTY = re.compile(r"\bbao hanh\b|\bbh\b")
WARRANTY_TERM = re.compile(r"\b(?:bao hanh|bh)\s*(?:them\s*)?\d+\s*(?:thang|ngay|nam|km|tuan)\b")
REPAIR_STOP = {"xe", "tho", "khach", "luot", "phieu", "lan", "sua", "lam", "thay", "kiem tra", "bao hanh", "tien", "cong"}


# Rulebook §8: names of one thing are one thing. Shorthands and synonyms of the same job in repair books
# (checked on the data: variants of one job never share a ticket, different jobs do).
JOB_SYNONYMS = [
    (r"\b(?:bdtq|bd|bao duong tong quat|bao duong dinh ky|bao duong xe)\b", "bao duong"),
    (r"\bvs\b(?= (?:noi|kim phun|nhong|sen|xich|buong dot|bugi|loc|may|xe|dau|chet))", "ve sinh"),   # 'vs' before a part; 'so vs' is versus
    (r"\blam noi\b", "ve sinh noi"),
    (r"\brua kim phun\b", "ve sinh kim phun"),
    (r"\bbu gi\b", "bugi"),
    (r"\b(?:bo nhong (?:xich|sen dia)|nhong xich|nsd)\b", "nhong sen dia"),
    (r"\b(?:can|tang) (?:sen|xich)\b|\bchinh xich\b", "chinh sen"),
    (r"\bnuoc mat\b", "nuoc lam mat"),
    (r"\b(?:day )?cu ?roa\b", "day curoa"),
    (r"\b(?:bo thang|ma thang)\b", "ma phanh"),
    (r"\bbo (truoc|sau)\b", r"ma phanh \1"),
]


def job_synonyms(text):
    for pattern, replacement in JOB_SYNONYMS:
        text = re.sub(pattern, replacement, text)
    return text


def job_key(item):
    """Canonical job of one 'Làm gì' item, following the rulebook's plain-word defaults."""
    t = words(WARRANTY.sub(" ", words(item)))
    if not t:
        return None
    if re.search(r"\bnhot\b|\bdau (?:may|lap|nhot)\b|thay dau\b", t) or t in ("lap", "thay lap"):
        return "nhot lap" if re.search(r"\blap\b|hop so|\bnhot so\b", t) else "nhot may"
    if OIL_BRAND_ITEM.fullmatch(t):
        return "nhot may"   # 'Motul', 'Shell 1L', 'Castrol': an engine-oil brand written alone is the oil change
    if re.search(r"\b(?:ruot|sam)\b", t):
        return "va xe" if re.search(r"\bva\b", t) else "thay ruot"   # 'vá ruột' is the patch job ('vá', 'vá xe')
    if re.search(r"\bva\b.*\b(?:lop|vo)\b", t):
        return "va lop"
    if re.search(r"bong den|\bden\b|\bbong\b", t):
        return "thay den"
    if re.search(r"ac ?quy|\bbinh\b", t):
        return "ac quy"
    if re.search(r"\bdien\b", t):
        return "sua dien"
    t = job_synonyms(re.sub(r"^thay\s+", "", t))
    if t in ("va", "va xe"):
        return "va xe"
    if t in ("rua", "rua xe"):
        return "rua xe"
    if re.match(r"vo\b", t):
        return "vo xe"   # 'vỏ', 'vỏ xe', 'vỏ Casumina'
    if t == "bo":
        return "ma phanh"
    return t


def split_jobs(value):
    """Items of a 'Làm gì' cell. Split on the raw text: 'và' (and) differs from 'vá' (patch) only by tone."""
    raw = unicodedata.normalize("NFC", str(value or "")).lower()
    return [words(part) for part in re.split(r"[,;+/\n]|\s+và\s+|\s+-\s+", raw) if words(part)]


def raw_jobs(value):
    """Items of a 'Làm gì' cell as written (accents and case kept), for answers that name a job."""
    raw = unicodedata.normalize("NFC", str(value or ""))
    return [part for part in re.split(r"[,;+/\n]|\s+[vV]à\s+|\s+-\s+", raw) if words(part)]


def question_job(text, keys):
    """Job named in the question, resolved against jobs recorded in the book."""
    text = job_synonyms(text)
    # Folded 'va' is both 'và' (and) and 'vá' (patch): only 'vá xe' / 'vá ruột' written out name the patch job.
    text = re.sub(r"\bthay (?:vo|lop)\b(?! xe)", "vo xe", text)
    special = None
    # Everyday names of the same jobs: 'thay dầu' is the oil change, 'thay phanh / bố' every brake job,
    # 'thay sên / xích / dĩa' the chain-and-sprocket set, 'vá lốp' the patch job when the book has no separate one.
    if re.search(r"\bthay dau\b", text) and not re.search(r"\bnhot\b", text):
        text = re.sub(r"\bthay dau\b", "thay nhot", text)
    if re.search(r"\bthay (?:bo|phanh|thang)\b(?! (?:truoc|sau))", text) and any(k.startswith("ma phanh") for k in keys):
        return "ma phanh%"
    if re.search(r"\bthay (?:sen|xich|dia|nhong)\b", text) and "nhong sen dia" in keys:
        return "nhong sen dia"
    if re.search(r"\bva (?:lop|vo)\b", text) and "va lop" not in keys and "va xe" in keys:
        return "va xe"
    if re.search(r"\bnhot\b", text) or re.search(r"\b(?:thay|lam) lap\b", text):
        special = "nhot lap" if re.search(r"\blap\b|hop so", text) else "nhot may"
    elif re.search(r"\b(?:ruot|sam)\b", text):
        special = ("va xe" if "va xe" in keys else "va ruot") if re.search(r"\bva\b", text) else "thay ruot"
    elif re.search(r"\bva\b\s+(?:lop|vo)\b", text):
        special = "va lop"
    elif re.search(r"bong den|thay den|\bden (?:pha|xi nhan|hau|truoc|sau)\b", text):
        special = "thay den"
    elif re.search(r"ac ?quy|thay binh\b", text):
        special = "ac quy"
    elif re.search(r"sua dien|he thong dien|\bdien\b(?! thoai)", text):
        special = "sua dien"
    if special:
        return special if special in keys else UNRESOLVED_JOB
    candidates = [key for key in keys if key and len(key) >= 4 and key not in REPAIR_STOP
                  and re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", text)]
    if candidates:
        best = max(candidates, key=len)
        # 'thay má phanh' without trước/sau: every brake job ('má phanh trước', 'má phanh sau', 'má phanh').
        if best == "ma phanh" and not re.search(r"ma phanh (?:truoc|sau)", text) and any(k.startswith("ma phanh ") for k in keys):
            return "ma phanh%"
        return best
    if re.search(r"\bma phanh\b", text) and any(k.startswith("ma phanh ") for k in keys) and not re.search(r"ma phanh (?:truoc|sau)", text):
        return "ma phanh%"
    # A job is asked ('thay má phanh', 'vá lốp') but none recorded matches: counting every visit would be wrong.
    return UNRESOLVED_JOB if JOB_ASKED.search(text) else None


UNRESOLVED_JOB = "\0unresolved"
OIL_BRAND_ITEM = re.compile(r"(?:thay\s+)?(?:motul|castrol|shell|liqui moly|liquimoly|repsol|mobil|petrolimex|idemitsu|eneos|fuchs|valvoline)(?:\s+[a-z0-9]+){0,2}")
# A job verb followed by a job word; 'làm ra' (earn), 'thay đổi' (change) and the like are not jobs.
JOB_ASKED = re.compile(r"\b(?:thay|va|ve sinh|bao duong|chinh|cang|bom|lam)\s+(?!xe\b|tiem\b|cho\b|bao\b|duoc\b|nhieu\b|may\b|gi\b|viec\b|it\b|o\b|tai\b|tu\b|trong\b|thang\b|ngay\b|tuan\b|nam\b|hom\b|lai\b|the\b|nhu\b|nhat\b"
                       r"|ra\b|doi\b|phieu\b|tien\b|cong\b|so\b|bn\b|mot\b|hai\b|nhung\b|cac\b|khach\b|luot\b|xong\b|het\b|sao\b|vay\b|nua\b)[a-z]{2,}")


def model_keys(names):
    """Same model when names differ only by case/spacing or by abbreviation (initials or word prefixes)."""
    base = {name: re.sub(r"[^a-z0-9]", "", fold(name)) for name in names}
    tokens = {name: re.findall(r"[a-z0-9]+", fold(name)) for name in names}
    mapping = {}
    for name in names:
        key = base[name]
        if len(tokens[name]) == 1:
            initials = {base[o] for o in names if len(tokens[o]) >= 2 and "".join(w[0] for w in tokens[o]) == key}
            if len(initials) == 1 and key not in {base[o] for o in names if len(tokens[o]) >= 2}:
                key = next(iter(initials))
        longer = {base[o] for o in names if o != name and len(tokens[o]) == len(tokens[name]) and base[o] != key
                  and all(b.startswith(a) for a, b in zip(tokens[name], tokens[o])) and any(len(b) > len(a) for a, b in zip(tokens[name], tokens[o]))}
        if len(longer) == 1 and key == base[name]:
            key = next(iter(longer))
        mapping[name] = key
    return mapping


def merge_models_by_plate(models, pairs):
    """Rulebook §6 Sửa xe: a vehicle is its plate. Model names written for the same plates ('Vision' / 'Vision 110',
    'Alpha' / 'Wave α' / 'Wave Alpha') are one model line; one or two stray plates do not merge two lines."""
    plate_models = defaultdict(set)
    for plate_value, model_value in pairs:
        plate_key = re.sub(r"[^a-z0-9]", "", fold(plate_value)) if plate_value else ""
        if plate_key and models.get(model_value):
            plate_models[plate_key].add(models[model_value])
    plates = Counter(key for keys in plate_models.values() for key in keys)
    shared = Counter((a, b) for keys in plate_models.values() for a in keys for b in keys if a < b)
    parent = {key: key for key in models.values()}
    def root(key):
        while parent[key] != key:
            key = parent[key]
        return key
    for (a, b), count in sorted(shared.items()):
        if count >= 2 and count >= 0.05 * min(plates[a], plates[b]):
            ra, rb = root(a), root(b)
            if ra != rb:
                keep, other = sorted((ra, rb), key=lambda key: (-plates[key], key))
                parent[other] = keep
    return {name: root(key) for name, key in models.items()}


def labels(pairs, prefer=None):
    """One written label per key: the spelling written most often (case and spacing aside), then the shorter;
    with `prefer`, labels sharing a word with the key come first ('thay nhớt' over 'Motul' for the oil change)."""
    counts = defaultdict(Counter)
    for key, raw in pairs:
        if key and raw:
            counts[key][raw] += 1
    out = []
    for key, counter in counts.items():
        forms = defaultdict(Counter)
        for raw, n in counter.items():
            forms[words(raw)][raw] += n
        def rank(form):
            shared = prefer and bool(set(form.split()) & set(str(key).split()))
            return (not shared if prefer else False, -sum(forms[form].values()), len(form), form)
        best = min(forms, key=rank)
        out.append((key, sorted(forms[best].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]))
    return out


# Vietnamese plate inside free text ('Air Blade 59A1-123.45'); RE2 has no lookaround, so group 1 is the plate.
PLATE_SQL = r"(?:^|[^0-9A-Za-z])([0-9]{2}\s?-?\s?[A-Za-z]{1,2}[0-9]?\s?[-.]?\s?[0-9]{3}\.?[0-9]{1,2})(?:[^0-9]|$)"


def plate_by_values(engine, table, exclude=()):
    """A text column whose values carry a plate, when no column is named as one (plate and model in one cell)."""
    for column in table["columns"]:
        if column["kind"] != "text" or column["name"] in exclude or not column.get("nonempty"):
            continue
        _, rows = engine.lookup(f"SELECT {quote(column['name'])} FROM {quote(table['name'])} WHERE NULLIF(TRIM({quote(column['name'])}),'') IS NOT NULL LIMIT 200")
        values = [str(r[0]) for r in rows]
        if len(values) >= 3 and sum(bool(re.search(PLATE_SQL, v)) for v in values) >= 0.8 * len(values):
            return column["name"]
    return None


def plate_with_model(engine, table, column):
    """The 'plate' column actually holds 'Air Blade 59A1-123.45': a plate plus other words in most cells."""
    _, rows = engine.lookup(f"SELECT {quote(column)} FROM {quote(table['name'])} WHERE NULLIF(TRIM({quote(column)}),'') IS NOT NULL LIMIT 200")
    values = [str(r[0]) for r in rows]
    extra = [v for v in values if (m := re.search(PLATE_SQL, v)) and re.search(r"[^\W\d_]{2,}", v.replace(m[1], ""))]
    return len(values) >= 3 and len(extra) >= 0.5 * len(values)


HONORIFIC = r"^\s*(?:(?:a|c|e)\s*\.\s*|(?:a|c|e|anh|chị|em|chú|cô|bác)\s+)"


def person(sql):
    """'a Đạt', 'A.Đạt', 'chị Lan' -> 'Đạt', 'Lan': a kinship honorific before a nickname is not part of the name."""
    return f"NULLIF(TRIM(regexp_replace({sql},{literal('(?i)' + HONORIFIC)},'')),'')"


def attach_repair(engine, catalog, create, reports):
    best = None
    pick = picker(catalog, "vehicle_repair")
    repair_shop = (catalog.get("llm_roles") or {}).get("domain") == "vehicle_repair"
    for table in catalog["tables"]:
        if "giao" in table["name"] and not repair_shop:
            continue  # delivery logs carry truck plates
        plate, job, mechanic = pick(table, "plate"), pick(table, "job"), pick(table, "mechanic")
        embedded = None if plate or not (job or mechanic) else plate_by_values(engine, table, {job, mechanic})
        date = pick(table, "ticket_date", {plate}, "datetime")
        # Alias evidence needs a plate; a shop the model classified as a repair shop may keep a book without plates.
        book = (plate or embedded) and (job or mechanic) or repair_shop and job and (mechanic or pick(table, "total", {plate, job, date}, "number"))
        if book and date and (best is None or table["rows"] > best[0]["rows"]):
            best = (table, plate, job, mechanic, date, embedded)
    if not best:
        return None
    table, plate, job, mechanic, date, embedded = best
    if plate and not embedded and plate_with_model(engine, table, plate):
        plate, embedded = None, plate   # extract the plate from the cell; the rest is the model line
    used = {plate, job, mechanic, date, embedded}
    model = pick(table, "model", used)
    total = pick(table, "total", used | {model}, "number")
    labor, parts = pick(table, "labor", used | {total}, "number"), pick(table, "parts", used | {total}, "number")
    kind, notes, ticket = pick(table, "kind", used), pick(table, "notes", used), role_column(table, "ticket_id", used)
    amount = num("r", table, total) if total else (f"COALESCE({num('r', table, labor)},0)+COALESCE({num('r', table, parts)},0)" if labor or parts else "NULL")
    # A day written once for a group of rows applies to the rows below it (§8).
    day = f"LAST_VALUE({ts('r', table, date)} IGNORE NULLS) OVER(PARTITION BY r._source_file,r._source_sheet ORDER BY r._source_row ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
    plate_sql, model_sql = txt("r", plate, table), txt("r", model, table)
    if embedded:
        cell = txt("r", embedded, table)
        plate_sql = f"NULLIF(regexp_extract({cell},{literal(PLATE_SQL)},1),'')"
        if model in (None, embedded):
            # The rest of the cell is the model line ('Air Blade 59A1-123.45' -> 'Air Blade').
            model_sql = f"NULLIF(TRIM(regexp_replace(replace({cell},COALESCE({plate_sql},''),''),'^[\\s,;:()-]+|[\\s,;:()-]+$','','g')),'')"
    raw = (f"SELECT ROW_NUMBER() OVER(ORDER BY r._source_file,r._source_sheet,r._source_row) ticket_no,{txt('r', ticket, table)} ticket_id,{day} ticket_date,"
           f"{plate_sql} plate,{model_sql} model,{person(txt('r', mechanic, table))} mechanic,{txt('r', job, table)} jobs,{amount} amount,{num('r', table, labor)} labor,"
           f"{num('r', table, parts)} parts,{txt('r', kind, table)} kind_text,{txt('r', notes, table)} notes FROM {quote(table['name'])} r")
    create("v_tickets_raw", f"SELECT * FROM ({raw}) WHERE ticket_date IS NOT NULL AND (plate IS NOT NULL OR jobs IS NOT NULL) AND NOT regexp_matches(norm(COALESCE(plate,''))||' '||norm(COALESCE(jobs,'')),'^\\s*(tong|cong)\\b')", [table["name"]])
    rows = engine.connection.execute("SELECT ticket_no,plate,model,jobs,kind_text,notes FROM v_tickets_raw").fetchall()
    models = merge_models_by_plate(model_keys(sorted({r[2] for r in rows if r[2]})), [(r[1], r[2]) for r in rows])
    attrs, jobs, job_names = [], [], []
    for number, plate_value, model_value, job_value, kind_value, note_value in rows:
        warranty = bool(kind_value and WARRANTY.search(words(kind_value)))
        for value in (job_value, note_value):
            warranty = warranty or bool(value and WARRANTY.search(WARRANTY_TERM.sub(" ", words(value))))
        plate_key = re.sub(r"[^a-z0-9]", "", fold(plate_value)) or None
        attrs.append((number, plate_key, models.get(model_value), warranty))
        jobs += [(number, key) for key in {job_key(item) for item in split_jobs(job_value)} if key]
        job_names += [(job_key(words(part)), part.strip()) for part in raw_jobs(job_value)]
    register(engine, "ticket_attrs", ["ticket_no BIGINT", "plate_key VARCHAR", "model_key VARCHAR", "warranty BOOLEAN"], attrs)
    register(engine, "ticket_jobs", ["ticket_no BIGINT", "job_key VARCHAR"], jobs)
    register(engine, "job_labels", ["job_key VARCHAR", "job_label VARCHAR"], labels(job_names, prefer=True))
    register(engine, "plate_labels", ["plate_key VARCHAR", "plate_label VARCHAR"], labels((a[1], r[1]) for a, r in zip(attrs, rows)))
    register(engine, "model_labels", ["model_key VARCHAR", "model_label VARCHAR"], labels((models.get(r[2]), r[2]) for r in rows))
    create("v_tickets", "SELECT r.*,a.plate_key,a.model_key,a.warranty,NOT a.warranty AND COALESCE(r.amount,1)>0 paid,p.plate_label,m.model_label "
           "FROM v_tickets_raw r JOIN ticket_attrs a USING(ticket_no) LEFT JOIN plate_labels p USING(plate_key) LEFT JOIN model_labels m USING(model_key)", ["v_tickets_raw"])
    reports.setdefault("capabilities", {})["tickets"] = True
    # Mechanic roster with a commission rate (% of the ticket totals a mechanic made in the month, per the README).
    for other in catalog["tables"]:
        if other is table:
            continue
        name_col = role_column(other, "staff_name") or role_column(other, "mechanic")
        rate = role_column(other, "commission", {name_col})
        if name_col and rate:
            # Tickets write the given name ('Đạt', 'a Đạt'); the roster the full name ('Hà Loan Đạt').
            create("v_mechanics", f"SELECT {txt('m', name_col, other)} full_name,regexp_extract(norm({txt('m', name_col, other)}),'(\\S+)$',1) mechanic_key,"
                   f"{num('m', other, rate)} pct FROM {quote(other['name'])} m WHERE {txt('m', name_col, other)} IS NOT NULL", [other["name"]])
            reports.setdefault("domain", {})["mechanics"] = other["name"]
            break
    reports["domain"] = {"repair_book": table["name"], "columns": {"plate": plate or embedded and f"{embedded} (plate in text)", "job": job, "mechanic": mechanic, "date": date, "model": model,
                                                                  "total": total, "labor": labor, "parts": parts, "kind": kind, "notes": notes},
                         # Money columns of the book: one means labor and parts are not recorded separately (§7).
                         "money_columns": sum(1 for c in table["columns"] if c["kind"] == "number" and c.get("nonempty") and c["name"] not in {ticket, plate, embedded}
                                              and not re.search(r"(?:^|_)(?:stt|so_thu_tu|so_phieu|ma)(?:_|$)", c["name"]))}
    return table["name"]


def compile_repair(question, text, raw, engine, has_sales, catalog=None):
    if "v_tickets" not in engine.tables:
        return None
    _, keys = engine.lookup("SELECT DISTINCT job_key FROM ticket_jobs")
    job = question_job(text, {row[0] for row in keys})
    if job == UNRESOLVED_JOB:
        return None
    repair_words = re.search(r"\bxe\b|\bphieu\b|\bluot\b|\btho\b|\bsua\b|bao hanh|bien so|dong xe|tien cong|phu tung|\btiem\b", text)
    if has_sales and not (repair_words or job):
        return None
    if "v_mechanics" in engine.tables and re.search(r"hoa hong", text) and COMMISSION_RATE.search(text):
        # The roster's commission rate ('anh Phúc được bao nhiêu % hoa hồng'): a percent, no period needed.
        _, mechanics = engine.lookup("SELECT DISTINCT mechanic FROM v_tickets WHERE mechanic IS NOT NULL")
        named = sorted({words(r[0]).split()[-1] for r in mechanics if r[0].strip() and re.search(r"(?<!\w)" + re.escape(unicodedata.normalize("NFC", r[0]).lower().strip()) + r"(?!\w)", raw)})
        if len(named) == 1:
            return plan(f"SELECT FIRST(pct) FROM v_mechanics WHERE mechanic_key={literal(named[0])}", "percent", rounding="percent", reason="Commission rate of the named mechanic, from the roster.")
        if not named and re.search(r"\bnhat\b", text) and re.search(r"\b(?:nao|ai)\b", text):
            order = "ASC" if re.search(r"\b(?:thap|it|nho) nhat\b", text) else "DESC"
            return plan(f"SELECT full_name FROM v_mechanics ORDER BY pct {order},full_name LIMIT 1", "string", rounding="none", reason="Mechanic with the highest (lowest) roster commission rate.")
        return None
    periods = resolve_periods(question.question, question.as_of_date, engine)
    if not periods:
        return None
    if len(periods)>1 and re.search(r"\b(?:den|toi)\b",text) and not re.search(r"so voi|lan luot|tung thang|moi thang",text):
        if all(a.end+dt.timedelta(days=1)==b.start for a,b in zip(periods,periods[1:])):
            periods=[Period(periods[0].start,periods[-1].end,"range")]
    if len(periods) > 2:
        return None
    asked, base = periods[0], None
    single = lambda period: _repair_single(question, text, raw, engine, has_sales, catalog, job, period)
    if len(periods) == 2:
        base = periods[1]
        lead, first_time = re.search(r"\bso (?:voi|vs)\b", text), re.search(r"\d", text)
        if lead and first_time and lead.start() < first_time.start():
            asked, base = periods[1], periods[0]  # 'So với tháng 1, tháng 2 ...': the period after 'so với' is the base
    elif REPAIR_COMPARE.search(text):
        if re.search(r"nam (?:o )?(?:ngoai|truoc|roi)\b|cung ky nam\b", text):
            try:
                base = Period(asked.start.replace(year=asked.start.year - 1), asked.end.replace(year=asked.end.year - 1), asked.mode)
            except ValueError:
                return None
        else:
            base = asked.previous()
    if base is None:
        return single(asked)
    # Two periods (rulebook §1): growth % vs the compared period (negative when lower); otherwise asked − compared.
    current, previous = single(asked), single(base)
    if not current or not previous or current.action != "query" or previous.action != "query" or current.answer_type != "number" or current.shape != "scalar":
        return None
    if wants_percent(text):
        return plan(f"SELECT 100.0*(a.v-b.v)/NULLIF(b.v,0) FROM ({current.sql}) a(v),({previous.sql}) b(v)", "percent", rounding="percent",
                    reason="Change in percent versus the compared period: " + current.reason)
    if not REPAIR_COMPARE.search(text) and not re.search(r"\bhon\b|\bkem\b|\bit hon\b|\btang\b|\bgiam\b", text):
        if len(periods) == 2 and re.search(r"\bmoi\b|lan luot|\btung\b|\brieng\b", text):
            # 'tháng 1 và tháng 2 mỗi tháng bao nhiêu': several numbers, listed in the order the question names them (§9).
            first, second = single(periods[0]), single(periods[1])
            return plan(f"SELECT v FROM (SELECT 0 seq,v FROM ({first.sql}) a(v) UNION ALL SELECT 1,v FROM ({second.sql}) b(v)) ORDER BY seq", "list", "column",
                        current.rounding, "Each named period, in the order asked: " + current.reason)
        return None
    return plan(f"SELECT a.v-b.v FROM ({current.sql}) a(v),({previous.sql}) b(v)", rounding=current.rounding,
                reason="Asked period minus compared period: " + current.reason)


# A plate in the folded question text ('59x5 435 74' from '59X5-435.74').
PLATE_TEXT = re.compile(r"(?<![a-z0-9])(\d{2}\s?[a-z]{1,2}\d?\s?\d{3}\s?\d{2})(?![a-z0-9])")
COMMISSION_RATE = re.compile(r"(?:phan tram|ti le|ty le|muc|%)\s*(?:huong\s*)?hoa hong|hoa hong\s*(?:la\s*|duoc\s*)?(?:bao nhieu|may)\s*(?:phan tram|%)|(?:bao nhieu|may)\s*(?:phan tram|%)")
EACH_MECHANIC = re.compile(r"\b(?:moi|tung) (?:anh |nguoi |ong )?tho\b|bang.*hoa hong.*tho|\bcac tho\b.*\b(?:moi nguoi|tung nguoi|lan luot)\b")
# 'tăng bao nhiêu %' with one period compares with the period before it (§1: change versus the previous period).
REPAIR_COMPARE = re.compile(r"\bso (?:voi|vs)\b|tang truong|thay doi|chenh lech|tang hay giam|tang giam|tang hoac giam|\b(?:tang|giam)\b(?: [a-z]+){0,3} ?(?:%|phan tram)|\bso (?:thang|tuan|quy|nam|ky|cung ky)\b")


def _repair_single(question, text, raw, engine, has_sales, catalog, job, period):
    start, end = period_bounds(period)
    conditions = [f"ticket_date>={start}", f"ticket_date<{end}"]
    _, mechanics = engine.lookup("SELECT DISTINCT mechanic FROM v_tickets WHERE mechanic IS NOT NULL")
    named = [r[0] for r in mechanics if r[0].strip() and re.search(r"(?<![a-z0-9])" + re.escape(words(r[0])) + r"(?![a-z0-9])", text)]
    if "v_mechanics" in engine.tables:
        _, roster = engine.lookup("SELECT full_name,mechanic_key FROM v_mechanics")
        keys = {key for name,key in roster if name and re.search(r"(?<![a-z0-9])"+re.escape(words(name))+r"(?![a-z0-9])",text)}
        if keys:
            if len(keys)!=1 or sum(key in keys for _,key in roster)!=1:
                return None
            named = [name for (name,) in mechanics if words(name).split()[-1] in keys]

    if named:
        conditions.append("norm(mechanic) IN (" + ",".join(literal(words(n)) for n in named) + ")")
    _, model_rows = engine.lookup("SELECT DISTINCT model_key,model_label FROM v_tickets WHERE model_key IS NOT NULL")
    _, raw_models = engine.lookup("SELECT DISTINCT model,model_key FROM v_tickets WHERE model IS NOT NULL")
    spans = [(m.start(), m.end(), key) for name, key in raw_models + [(label, key) for key, label in model_rows]
             if len(words(name)) >= 3 and words(name) not in REPAIR_STOP
             for m in re.finditer(r"(?<![a-z0-9])" + re.escape(words(name)) + r"(?![a-z0-9])", text)]
    # A name inside a longer matched name ('wave' in 'wave rsx') is part of that name, not a second model.
    mentioned = {key for start, stop, key in spans
                 if not any(s2 <= start and stop <= e2 and e2 - s2 > stop - start and k2 != key for s2, e2, k2 in spans)}
    if len(mentioned) > 1:
        return None
    if mentioned:
        conditions.append(f"model_key={literal(next(iter(mentioned)))}")
    plate_named = PLATE_TEXT.search(text)
    if plate_named:
        plate_key = re.sub(r"[^a-z0-9]", "", plate_named[1])
        _, known = engine.lookup(f"SELECT COUNT(*) FROM v_tickets WHERE plate_key={literal(plate_key)}") if _has(engine, "plate_key") else (None, [(0,)])
        if not known[0][0]:
            return None   # a plate the book never recorded: not the whole shop
        conditions.append(f"plate_key={literal(plate_key)}")
    if job:
        match = f"job_key LIKE {literal(job)}" if job.endswith("%") else f"job_key={literal(job)}"
        conditions.append(f"ticket_no IN (SELECT ticket_no FROM ticket_jobs WHERE {match})")
    where = " WHERE " + " AND ".join(conditions)
    rank = re.search(r"\bnhat\b|\btop\b", text)
    lowest = re.search(r"\b(?:thap|it|nho|kem) nhat\b", text)
    if re.search(r"hoa hong", text):
        if "v_mechanics" not in engine.tables:
            return None
        joined = f"FROM v_tickets t JOIN v_mechanics m ON m.mechanic_key=regexp_extract(norm(t.mechanic),'(\\S+)$',1){where}"
        if EACH_MECHANIC.search(text) and not named:
            return plan(f"SELECT FIRST(t.mechanic) tho,SUM(t.amount*m.pct/100) hoa_hong {joined} GROUP BY m.mechanic_key ORDER BY hoa_hong DESC,tho", "table", "table", "money",
                        "Commission of each mechanic: rate x ticket totals in the period.")
        if rank and not named:
            return plan(f"SELECT name FROM (SELECT FIRST(t.mechanic) name,SUM(t.amount*m.pct/100) v {joined} GROUP BY m.mechanic_key) ORDER BY v {'ASC' if lowest else 'DESC'},name LIMIT 1", "string", rounding="none",
                        reason="Mechanic with the lowest commission." if lowest else "Mechanic with the highest commission: rate x ticket totals in the period.")
        return plan(f"SELECT SUM(t.amount*m.pct/100) {joined}", rounding="money", reason="Commission = roster rate x total of the tickets the mechanic made in the period.")
    pct = re.search(r"(?:ti|ty) le|phan tram|%", text)
    plates = _has(engine, "plate_key")  # vehicle identity questions need recorded plates

    if re.search(r"bao hanh", text):
        _, evidence = engine.lookup("SELECT COUNT(jobs)+COUNT(kind_text)+COUNT(notes) FROM v_tickets")
        if not evidence[0][0]:
            return None  # warranty tickets are recognised from job/kind/notes text; none was read
    if re.search(r"bao hanh", text) and pct:
        return plan(f"SELECT 100.0*COUNT(*) FILTER(WHERE warranty)/NULLIF(COUNT(*) FILTER(WHERE paid),0) FROM v_tickets{where}", "percent", rounding="percent",
                    reason="Warranty tickets dated in the period over paid tickets in the same period (§6 Sửa xe).")
    excludes_warranty = bool(re.search(r"khong (?:ke|tinh).*bao hanh", text))
    average = bool(re.search(r"trung binh|binh quan|\bbq\b", text))
    if re.search(r"bao hanh", text) and not excludes_warranty and not average and re.search(r"bao nhieu|\bmay\b|so (?:phieu|luot)", text) and not job and not re.search(r"(?:ke|tinh|gom) ca (?:phieu )?bao hanh|khong (?:ke|tinh) bao hanh", text):
        return plan(f"SELECT COUNT(*) FILTER(WHERE warranty) FROM v_tickets{where}", reason="Warranty tickets in the period.")
    if re.search(r"khach quen|xe quen|quay lai|(?:toi|den|ghe|vao|sua)\s+(?:tiem\s+)?(?:tu\s+)?(?:2|hai)\s+lan|nhieu lan|(?:toi|den|ghe|vao)(?:\s+\w+){0,2}\s+nhieu (?:lan )?nhat", text) and not excludes_warranty and not average and not re.search(r"\b(?:dong|loai|hieu) xe\b|\btho\b", text):
        if not plates:
            return None
        grouped = f"SELECT plate_key,FIRST(plate_label) label,COUNT(*) FILTER(WHERE paid) visits FROM v_tickets{where} AND plate_key IS NOT NULL GROUP BY plate_key"
        if re.search(r"(?:xe|bien so)\s+nao|\bxe gi\b", text) and rank:
            return plan(f"SELECT label FROM ({grouped}) WHERE visits>0 ORDER BY visits DESC,plate_key LIMIT 1", "string", rounding="none",
                        reason="Vehicle (plate) with most paid tickets; warranty tickets are not visits.")
        if re.search(r"bao nhieu|\bmay\b|\bso\b", text):
            return plan(f"SELECT COUNT(*) FROM ({grouped}) WHERE visits>=2", reason="Regular vehicles: plates with at least 2 paid tickets in the period.")
        return None
    money_words = re.search(r"doanh thu|\btien\b|thu ve|thu duoc", text)
    if EACH_MECHANIC.search(text) and not rank and not named:
        measure, column = ("SUM(amount)", "so_tien") if money_words else ("COUNT(*)", "so_phieu")
        return plan(f"SELECT mechanic tho,{measure} {column} FROM v_tickets{where} AND mechanic IS NOT NULL GROUP BY mechanic ORDER BY {column} DESC,tho", "table", "table",
                    "money" if money_words else "integer", "Each mechanic's tickets (warranty tickets included) or ticket amounts in the period.")
    if re.search(r"\btho\b", text) and rank and (re.search(r"\b(?:nao|ai)\b", text) or not named):
        if re.search(r"cham chi|sieng", text):
            measure = "COUNT(DISTINCT CAST(ticket_date AS DATE))"
        elif money_words:
            measure = "SUM(amount) FILTER(WHERE paid)"
        elif re.search(r"(?:phieu|luot|xe) bao hanh", text) and not re.search(r"(?:ke|tinh|gom) ca", text):
            measure = "COUNT(*) FILTER(WHERE warranty)"
        else:
            measure = "COUNT(*)"
        k = top_k(fold(question.question))
        query = f"SELECT mechanic FROM (SELECT mechanic,{measure} value FROM v_tickets{where} AND mechanic IS NOT NULL GROUP BY mechanic) ORDER BY value {'ASC' if lowest else 'DESC'},mechanic LIMIT {k}"
        return plan(query, "list" if k > 1 else "string", "column" if k > 1 else "scalar", "none", "Mechanic ranking by visits incl. warranty tickets, or working days / paid amount when asked.")
    if re.search(r"\b(?:dong|loai|hieu) xe\b|\bxe (?:nao|gi)\b", text) and rank and not mentioned and not re.search(r"\b(?:ngay|thang|tuan|gio|thu|hom|buoi|ca) nao\b", text):
        k = top_k(fold(question.question))
        query = f"SELECT label FROM (SELECT model_key,FIRST(model_label) label,COUNT(*) value FROM v_tickets{where} AND model_key IS NOT NULL GROUP BY model_key) ORDER BY value DESC,label LIMIT {k}"
        return plan(query, "list" if k > 1 else "string", "column" if k > 1 else "scalar", "none", "Model line with most visits; case/abbreviation variants merged.")
    if re.search(r"ngay nao", text) and rank:
        query = f"SELECT d FROM (SELECT CAST(ticket_date AS DATE) d,COUNT(*) value FROM v_tickets{where} GROUP BY 1) ORDER BY value DESC,d LIMIT 1"
        return plan(query, "date", rounding="none", reason="Day with most tickets.")
    if rank and not job and re.search(r"\b(?:viec|dich vu|hang muc|cong viec)\b", text):
        # Jobs by the number of tickets doing them; warranty tickets are not a visit for that job (§6 Sửa xe).
        k = top_k(fold(question.question))
        query = (f"SELECT label FROM (SELECT j.job_key,FIRST(l.job_label) label,COUNT(DISTINCT t.ticket_no) value FROM v_tickets t JOIN ticket_jobs j USING(ticket_no) "
                 f"LEFT JOIN job_labels l USING(job_key){where} AND NOT t.warranty GROUP BY j.job_key) ORDER BY value DESC,label LIMIT {k}")
        return plan(query, "list" if k > 1 else "string", "column" if k > 1 else "scalar", "none", "Jobs ranked by tickets doing them, warranty tickets excluded.")
    if rank:
        return None
    if plate_named and re.search(r"\blan\b", text) and re.search(r"bao nhieu|\bmay\b|\bso\b", text):
        # 'xe 59X5-435.74 tới mấy lần': a warranty ticket is not a visit (§6 Sửa xe).
        return plan(f"SELECT COUNT(*) FILTER(WHERE paid) FROM v_tickets{where}", reason="Paid tickets of the named plate; warranty tickets are not visits.")
    from .intent import weekday_requested
    weekday = weekday_requested(question.question)
    if average and weekday and not re.search(r"tien|doanh thu", text):
        days = sum((period.start+dt.timedelta(days=i)).isoweekday()==weekday
                   for i in range((period.end-period.start).days+1))
        denominator=f"(SELECT COUNT(*) FROM calendar WHERE date>=DATE {literal(period.start)} AND date<=DATE {literal(period.end)} AND isodow(date)={weekday})"
        return plan(f"SELECT COUNT(*)*1.0/NULLIF({denominator},0) FROM v_tickets{where} AND isodow(ticket_date)={weekday}",
                    rounding="percent", reason="Visits on the requested weekday divided by all occurrences of that weekday in the period.")
    if re.search(r"trung binh|binh quan|\bbq\b", text) and re.search(r"phieu|luot|\bxe\b|\blan\b", text):
        return plan(f"SELECT SUM(amount) FILTER(WHERE paid)/NULLIF(COUNT(*) FILTER(WHERE paid),0) FROM v_tickets{where}", rounding="money",
                    reason="Average per paid ticket; warranty tickets excluded from the denominator.")
    single_amount = ((catalog or {}).get("semantic", {}).get("domain") or {}).get("money_columns") == 1
    if re.search(r"tien cong", text):
        if _has(engine, "labor"):
            return plan(f"SELECT SUM(labor) FROM v_tickets{where}", rounding="money", reason="Labor charges of tickets in the period.")
        return missing("Sổ sửa xe chỉ ghi một khoản tiền mỗi phiếu, không tách tiền công.") if single_amount else None
    if re.search(r"phu tung", text) and re.search(r"tien|doanh thu", text):
        if _has(engine, "parts"):
            return plan(f"SELECT SUM(parts) FROM v_tickets{where}", rounding="money", reason="Parts charges of tickets in the period.")
        return missing("Sổ sửa xe chỉ ghi một khoản tiền mỗi phiếu, không tách tiền phụ tùng.") if single_amount else None
    if money_words:
        if job:
            # A ticket's amounts cover all its jobs: money per job is not recorded (rulebook §7).
            return missing("Sổ ghi tiền theo phiếu, không ghi tiền từng việc.")
        return plan(f"SELECT SUM(amount) FROM v_tickets{where}", rounding="money", reason="Ticket amounts dated in the period.") if _has(engine, "amount") else None
    if job and re.search(r"bao nhieu|\bmay\b|\bso\b|dem", text):
        return plan(f"SELECT COUNT(*) FROM v_tickets{where} AND NOT warranty", reason="Tickets containing the job, counted once each; warranty tickets excluded.")
    if re.search(r"(?:bao nhieu|\bmay\b|tong so|\bso\b)\s+(?:luot|phieu|lan)\b|(?:luot|phieu) (?:xe|sua)", text):
        return plan(f"SELECT COUNT(*) FROM v_tickets{where}", reason="Each book row is one visit, warranty tickets included.")
    if mentioned and plates and re.search(r"khac nhau",text):
        return plan(f"SELECT COUNT(DISTINCT plate_key) FROM v_tickets{where}",reason="Distinct recorded vehicles of the named model.")
    if re.search(r"(?:bao nhieu|\bmay\b|tong so|\bso\b)\s+(?:chiec\s+)?xe\b|xe khac nhau|bien so khac", text):
        if not plates:
            return None
        return plan(f"SELECT COUNT(DISTINCT plate_key) FROM v_tickets{where}", reason="Distinct plates, including vehicles that came only for warranty.")
    return None


def _has(engine, column):
    _, rows = engine.lookup(f"SELECT COUNT({column}) FROM v_tickets")
    return bool(rows[0][0])


# ---------------------------------------------------- retail domain attributes

def attach_retail_domains(engine, catalog, reports, create, mapping, sales_source, sales_header, join_header):
    tables = catalog["tables"]
    con = engine.connection
    attached = {}
    baby, phone = picker(catalog, "mother_baby"), picker(catalog, "phone_store")
    domain = (catalog.get("llm_roles") or {}).get("domain")
    any_domain = phone if domain == "phone_store" else baby
    # Catalog attributes: size / milk stage / brand columns of a product master.
    for table in tables:
        m = mapping[table["name"]]
        if not (m["product_id"] or m["product_name"]) or m["quantity"] or m["date"]:
            continue
        taken = {m["product_id"], m["product_name"], m["group"]}
        size, stage = baby(table, "size", taken), baby(table, "stage", taken)
        brand = baby(table, "brand", taken)
        if brand == "hang" and not info(table, "hang").get("values") and not accented_brand(info(table, "hang")):
            brand = None  # 'hang' (hàng/hãng) is a brand only when it is a short category list or written 'Hãng'
        if not (size or stage or brand):
            continue
        pid, pname, group = txt("p", m["product_id"], table), txt("p", m["product_name"], table), txt("p", m["group"], table)
        combined = size and size == stage
        size_value = f"upper(trim(regexp_replace({txt('p', size, table)},'(?i)^size\\s*','')))" if size else "NULL"
        if combined:
            size_value = f"CASE WHEN regexp_matches({txt('p', size, table)},'^\\s*(?:so\\s*|số\\s*)?\\d+\\s*$') THEN NULL ELSE {size_value} END"
        stage_value = f"NULLIF(regexp_extract({txt('p', stage, table)},'(\\d+)',1),'')" if stage else "NULL"
        create("v_product_attrs", f"SELECT {pid} product_id,{pname} product_name,{group} product_group,{size_value} size,{stage_value} stage,{txt('p', brand, table)} brand,"
               # Diaper products: the item group when the catalogue has one ('bỉm tã'), else the item name.
               f"CASE WHEN {group} IS NOT NULL THEN regexp_matches(norm({group}),'\\bbim\\b|^ta\\b') "
               f"ELSE regexp_matches(norm(COALESCE({pname},'')),'\\bbim\\b|^ta\\b|\\bta (?:dan|quan|giay)\\b') END is_diaper FROM {quote(table['name'])} p "
               f"WHERE COALESCE({pid},{pname}) IS NOT NULL", [table["name"]])
        attached["attributes"] = table["name"]
        break
    if "v_product_attrs" in engine.tables and "v_sales" in engine.tables:
        create("v_sales_attrs", "SELECT s.*,a.size,a.stage,a.brand,COALESCE(a.is_diaper,FALSE) is_diaper FROM v_sales s LEFT JOIN v_product_attrs a ON "
               "CASE WHEN s.product_id IS NOT NULL AND a.product_id IS NOT NULL THEN s.product_id=a.product_id ELSE norm(s.product_name)=norm(a.product_name) END", ["v_sales", "v_product_attrs"])
    # Members registered (customer master), including customers who never bought.
    for table in tables:
        m = mapping[table["name"]]
        registered = baby(table, "registered", (), "datetime")
        balance = baby(table, "points_balance", {registered}, "number")
        if "khach" in table["name"] and (m["customer_id"] or m["customer_name"]) and registered:
            create("v_members", f"SELECT COALESCE({txt('c', m['customer_id'], table)},norm({txt('c', m['customer_name'], table)})) member_key,{txt('c', m['customer_name'], table)} member_name,"
                   f"{ts('c', table, registered)} registered_at,{num('c', table, balance) if balance else 'NULL::DECIMAL'} points_balance FROM {quote(table['name'])} c WHERE COALESCE({txt('c', m['customer_id'], table)},{txt('c', m['customer_name'], table)}) IS NOT NULL", [table["name"]])
            attached["members"] = table["name"]
            break
    if "v_orders_all" not in engine.tables:
        reports["domain_retail"] = attached
        return
    # Invoice-level money: voucher, points, shipping fee, trade-in, explicit payable.
    order_table = sales_header if join_header else sales_source
    order_key = mapping[order_table["name"]]["order_id"] if order_table else None
    column_parts, roles = [], {}
    if order_table and order_key:
        # One column, one role: 'TT trước' (paid upfront) must not also be read as the amount payable.
        for role in ("voucher", "points", "ship", "tradein", "prepaid", "order_discount", "payable"):
            column = (phone if role in {"tradein", "payable"} else any_domain)(order_table, role, {order_key} | set(roles.values()), "number")
            if role == "payable" and column and re.search(r"(?:^|_)(?:khach_tra|khach_da_tra|da_tra|da_thanh_toan|da_thu|khach_dua|tien_khach_dua|thuc_thu)(?:_|$)", column):
                column = None   # what the customer PAID is not what the customer OWES (debt and installment orders differ)
            if column:
                roles[role] = column
                column_parts.append(f"MAX(ABS({num('f', order_table, column)})) {role}")
    lines = {"voucher": "regexp_matches(norm(product_name),'^(?:giam gia |tien giam |giam )?voucher\\b')",
             "points": "regexp_matches(norm(product_name),'^(?:dung|tru|doi|giam gia|giam)\\s+(?:tien\\s+)?(?:\\d+\\s+)?diem\\b')",
             "ship": "regexp_matches(norm(product_name),'^(?:phi|tien)\\s+(?:ship|giao hang|giao|van chuyen)\\b')",
             "tradein": "regexp_matches(norm(product_name),'^(?:thu|tru|tro gia)\\s+(?:may cu|cu)\\b|^may cu\\b|^doi may cu\\b')"}
    line_parts = [f"SUM(ABS(revenue)) FILTER(WHERE {condition}) {role}" for role, condition in lines.items()]
    line_parts.append(f"SUM(TRY_CAST(regexp_extract(norm(product_name),'(\\d+)\\s+diem',1) AS BIGINT)) FILTER(WHERE {lines['points']}) points_used")
    line_sql = "SELECT order_id," + ",".join(line_parts) + " FROM v_sales_all WHERE NOT is_item GROUP BY order_id" if "v_sales_all" in engine.tables else None
    separate = None
    for table in tables:
        m = mapping[table["name"]]
        ref = m["order_id"] or role_column(table, "order_ref")
        amount = number_column(table, "tradein", {ref}) or number_column(table, "total", {ref})
        if re.search(r"thu_may|thu_cu|may_cu|doi_may", name_words(table["name"])) and ref and amount:
            separate = f"SELECT {ref_key(txt('t', ref, table))} order_id,SUM(ABS({num('t', table, amount)})) tradein FROM {quote(table['name'])} t GROUP BY 1"
            attached["tradein_book"] = table["name"]
            # The phones taken in (README 'Máy thu'): one row per phone, for 'máy cũ nào được thu nhiều nhất'.
            model = next((c["name"] for c in table["columns"] if c["kind"] == "text" and c["name"] != ref and re.search(r"(?:^|_)(?:may_thu|may|dong_may|ten_may|model)(?:_|$)", c["name"])), None)
            if model:
                create("v_tradein_book", f"SELECT {ref_key(txt('t', ref, table))} order_id,{txt('t', model, table)} model,ABS({num('t', table, amount)}) amount FROM {quote(table['name'])} t WHERE {txt('t', model, table)} IS NOT NULL", [table["name"]])
            break
    select = ["o.order_id", "o.order_date", "o.status_rank", "o.revenue", "o.order_total", "o.customer_key", "o.customer_name"]
    joins = ""
    if column_parts:
        joins += f" LEFT JOIN (SELECT {txt('f', order_key, order_table)} order_id," + ",".join(column_parts) + f" FROM {quote(order_table['name'])} f GROUP BY 1) c ON c.order_id=o.order_id"
    if line_sql:
        joins += f" LEFT JOIN ({line_sql}) l ON l.order_id=o.order_id"
    if separate:
        joins += f" LEFT JOIN ({separate}) t ON t.order_id=o.order_id"
    for role in ("voucher", "points", "ship", "tradein"):
        sources = ([f"c.{role}"] if role in roles else []) + ([f"l.{role}"] if line_sql else []) + (["t.tradein"] if role == "tradein" and separate else [])
        select.append(f"COALESCE({','.join(sources)}) {role}" if sources else f"NULL::DECIMAL {role}")
    select.append("l.points_used points_used" if line_sql else "NULL::BIGINT points_used")
    select.append("c.payable payable" if "payable" in roles else "NULL::DECIMAL payable")
    for role in ("order_discount", "prepaid"):
        select.append(f"c.{role} {role}" if role in roles else f"NULL::DECIMAL {role}")
    create("v_order_money_base", "SELECT " + ",".join(select) + " FROM v_orders_all o" + joins, ["v_orders_all"])
    if order_table:
        # Unrecognised invoice amounts that could hold a fee: 'not recorded' cannot be concluded.
        used = set(mapping[order_table["name"]].values()) | set(roles.values())
        attached["unmapped_fee_like"] = [c["name"] for c in order_table["columns"] if c["name"] not in used and c.get("nonempty")
                                         and re.search(r"giam|km|khuyen mai|voucher|ship|van chuyen|giao|diem|phi", words(c["original"]) + " " + c["name"].replace("_", " "))]
    _, present = engine.lookup("SELECT COUNT(voucher),COUNT(points),COUNT(ship),COUNT(tradein),COUNT(payable) FROM v_order_money_base")
    attached["order_money"] = dict(zip(("voucher", "points", "ship", "tradein", "payable"), present[0]))
    # Phone rule: the customer owes the order total AFTER the trade-in deduction. Whether the
    # recorded total already nets the trade-in is read from fully paid non-debt orders.
    nets = False
    if attached["order_money"]["tradein"] and not attached["order_money"]["payable"]:
        nets = True
        if "v_receipts" in engine.tables:
            _, evidence = engine.lookup("WITH paid AS (SELECT order_id,SUM(amount) amount FROM v_receipts GROUP BY 1) SELECT COUNT(*) FILTER(WHERE abs(p.amount-(m.order_total-m.tradein))<1),"
                                       "COUNT(*) FILTER(WHERE abs(p.amount-m.order_total)<1) FROM v_order_money_base m JOIN paid p USING(order_id) JOIN v_orders_all o USING(order_id) "
                                       "WHERE m.tradein>0 AND o.payment_method NOT IN ('debt','cod')")
            nets = evidence[0][0] >= evidence[0][1]
        attached["total_before_tradein"] = nets
    _, totals = engine.lookup("SELECT COUNT(order_total) FROM v_order_money_base")
    # Rulebook §4/§6: what the customer owes = line totals after the invoice discount, voucher and points, plus
    # shipping, minus the trade-in. Built from the lines only when the book records no order total at all.
    computed = ("revenue-COALESCE(order_discount,0)-COALESCE(voucher,0)-COALESCE(points,0)+COALESCE(ship,0)-COALESCE(tradein,0)"
                if not totals[0][0] else "NULL")
    attached["order_total_from_lines"] = not totals[0][0]
    create("v_order_money", f"SELECT *,COALESCE(payable,order_total-{'COALESCE(tradein,0)' if nets else '0'},{computed}) due FROM v_order_money_base", ["v_order_money_base"])
    if (nets or not totals[0][0]) and "v_finance" in engine.tables:
        create("v_finance_adjusted", "SELECT f.* REPLACE(COALESCE(m.due,f.order_total) AS order_total) FROM v_finance f LEFT JOIN v_order_money m USING(order_id)", ["v_finance", "v_order_money"])
        con.execute("DROP TABLE v_finance")
        con.execute("ALTER TABLE v_finance_adjusted RENAME TO v_finance")
    # Phone attributes from product names (rulebook: model line before '…GB', brand = first word, iPhone → Apple).
    if "v_sales" in engine.tables:
        imei = None
        if sales_source:
            imei = phone(sales_source, "imei")
        phone_sql = ("regexp_matches(product_name,'(?i)\\d+\\s*(?:gb|tb)\\b') OR regexp_matches(norm(COALESCE(product_group,'')),'dien thoai|smartphone') OR "
                     "regexp_matches(norm(product_name),'^(?:iphone|samsung galaxy|galaxy|xiaomi|redmi|poco|oppo|vivo|realme|nokia|huawei|honor|vsmart|itel|masstel|tecno|infinix|google pixel|pixel|motorola|oneplus)\\b')")
        name_column = mapping[sales_source["name"]]["product_name"] if sales_source else None
        if imei and name_column:
            # README 'Máy có IMEI, phụ kiện không': a product ever sold with an IMEI is a phone ('Nokia 105 4G' has no '…GB'),
            # and one never sold with an IMEI is not ('Thẻ nhớ 64GB').
            phone_sql = f"norm(product_name) IN (SELECT DISTINCT norm({txt('s', name_column, sales_source)}) FROM {quote(sales_source['name'])} s WHERE NULLIF(trim({txt('s', imei, sales_source)}),'') IS NOT NULL)"
        # Groups of the catalogue: 'máy mới', 'máy cũ' are phones, 'phụ kiện' never is.
        phone_sql = f"(({phone_sql}) OR regexp_matches(norm(COALESCE(product_group,'')),'^(?:may|dien thoai)\\b')) AND NOT regexp_matches(norm(COALESCE(product_group,'')),'phu kien')"
        _, counts = engine.lookup(f"SELECT COUNT(*) FILTER(WHERE {phone_sql}) FROM v_sales")
        if counts[0][0] or imei:
            create("v_phone_sales", "SELECT *,COALESCE(NULLIF(trim(regexp_replace(product_name,'(?i)\\s*\\d+\\s*(?:gb|tb)\\b.*$','')),''),product_name) model_line,"
                   "CASE WHEN regexp_matches(norm(product_name),'^iphone') THEN 'Apple' ELSE regexp_extract(trim(product_name),'^(\\S+)',1) END brand,"
                   f"({phone_sql}) is_phone FROM v_sales", ["v_sales"])
            attached["phones"] = True
    # Warranty claims book (phone store): one row per claim, dated by receive date.
    for table in tables:
        if "bao_hanh" not in name_words(table["name"]):
            continue
        received = phone(table, "receive_date", (), "datetime")
        if received and table["rows"]:
            line_of = lambda sql: f"COALESCE(NULLIF(trim(regexp_replace({sql},'(?i)\\s*\\d+\\s*(?:gb|tb)\\b.*$','')),''),{sql})"   # §6 dòng máy
            extra = "".join(f",{line_of(txt('w', column, table)) if name == 'model' else txt('w', column, table)} {name}" if column else f",NULL::VARCHAR {name}"
                            for name, column in (("model", phone(table, "claim_model")), ("imei", phone(table, "imei")),
                                                 ("fault", phone(table, "claim_fault")), ("action", phone(table, "claim_action"))))
            purchased = next((c['name'] for c in table['columns'] if c['kind']=='datetime' and re.search(r'ngay_mua|ngay_ban|purchase_date',c['name'])),None)
            extra += f",{ts('w',table,purchased)} purchased_at"
            create("v_warranty_claims", f"SELECT {ts('w', table, received)} received_at{extra} FROM {quote(table['name'])} w WHERE {ts('w', table, received)} IS NOT NULL", [table["name"]])
            attached["warranty_claims"] = table["name"]
            break
    # Installment schedule: rows per order and period with due date, amount, payment.
    for table in tables:
        if "gop" not in table["name"]:
            continue
        m = mapping[table["name"]]
        ref = m["order_id"] or role_column(table, "order_ref") or fallback(catalog, table["name"], "order_id")
        count, each = role_column(table, "installment_count"), role_column(table, "installment_amount")
        if count and each and not ref and "v_orders_all" in engine.tables:
            # 'STT' of a contract book is the sales book's order number when its values are order codes.
            for column in table["columns"]:
                if re.fullmatch(r"stt|so_thu_tu|so_tt", column["name"]) and column.get("nonempty"):
                    _, overlap = engine.lookup(f"SELECT AVG(CASE WHEN {ref_key(txt('g', column['name'], table))} IN (SELECT order_id FROM v_orders_all) THEN 1.0 ELSE 0 END) "
                                               f"FROM {quote(table['name'])} g WHERE {txt('g', column['name'], table)} IS NOT NULL")
                    if overlap[0][0] and overlap[0][0] >= 0.8:
                        ref = column["name"]
                        break
        if ref and count and each:
            paid_date, paid_amount = phone(table, "paid_date", (), "datetime"), phone(table, "paid_amount", {count, each}, "number")
            due_day, prepaid = role_column(table, "due_day", {count, each}), role_column(table, "prepaid", {count, each, paid_amount})
            starts = [c["name"] for c in table["columns"] if c["kind"] == "datetime" and c.get("nonempty") and c["name"] != paid_date]
            if starts:
                schedule, payments, upfront = contract_schedule(engine, table, ref, starts[0], count, each, due_day, prepaid, paid_date, paid_amount)
                register(engine, "v_installments", ["order_id VARCHAR", "due_date TIMESTAMP", "amount_due DECIMAL(18,2)", "paid_amount DECIMAL(18,2)", "paid_at TIMESTAMP"], schedule)
                register(engine, "v_installment_payments", ["order_id VARCHAR", "paid_at TIMESTAMP", "amount DECIMAL(18,2)"], payments)
                register(engine, "installment_upfront", ["order_id VARCHAR", "paid_at TIMESTAMP", "amount DECIMAL(18,2)"], upfront)
                add_receipts(engine, create, "SELECT order_id,paid_at,amount FROM v_installment_payments UNION ALL SELECT order_id,paid_at,amount FROM installment_upfront", [table["name"]])
                mark_debt(engine, {row[0] for row in schedule})
                attached["installments"] = table["name"] + " (contract rows)"
                attached.pop("installments_unread", None)
                break
        due, amount = phone(table, "due_date", (), "datetime"), phone(table, "due_amount", (), "number")
        paid_amount, paid_date = phone(table, "paid_amount", {amount}, "number"), phone(table, "paid_date", {due}, "datetime")
        dates = [c["name"] for c in table["columns"] if c["kind"] == "datetime" and c.get("nonempty")]
        if not due and paid_date and len(set(dates) - {paid_date}) == 1:
            due = next(iter(set(dates) - {paid_date}))  # the schedule's only other date is the due date
        if not (ref and due and amount):
            attached["installments_unread"] = table["name"]
            continue
        create("v_installments", f"SELECT {txt('g', ref, table)} order_id,{ts('g', table, due)} due_date,{num('g', table, amount)} amount_due,"
               f"{num('g', table, paid_amount) if paid_amount else 'NULL::DECIMAL'} paid_amount,{ts('g', table, paid_date) if paid_date else 'NULL::TIMESTAMP'} paid_at "
               f"FROM {quote(table['name'])} g WHERE {txt('g', ref, table)} IS NOT NULL", [table["name"]])
        attached["installments"] = table["name"]
        attached.pop("installments_unread", None)
        if paid_date:
            paid = "COALESCE(paid_amount,amount_due)" if paid_amount else "amount_due"
            payments = f"SELECT order_id,paid_at,{paid} amount FROM v_installments WHERE paid_at IS NOT NULL"
            if "v_receipts" in engine.tables:
                create("v_receipts_all", "SELECT order_id,paid_at,amount FROM v_receipts UNION ALL " + payments, ["v_receipts", "v_installments"])
                con.execute("DROP TABLE v_receipts")
                con.execute("ALTER TABLE v_receipts_all RENAME TO v_receipts")
            else:
                create("v_receipts", payments, ["v_installments"])
        break
    # Debt ledger: 'Nợ' is the unpaid part of an order at purchase, repayment rows below, the order written in the note ('STT 34').
    for table in tables:
        words_ = name_words(table["name"])
        if not re.search(r"(?:^|_)no(?:_|$)", words_) or "gop" in words_ or "v_order_money" not in engine.tables:
            continue
        debt, ref_col, notes = role_column(table, "debt_amount"), role_column(table, "order_ref"), role_column(table, "notes")
        repay = role_column(table, "repay_amount", {debt})
        repay_day = date_column(table, "repay_date")
        if not (debt and repay and repay_day and (ref_col or notes)):
            continue
        order = ref_key(txt("d", ref_col, table)) if ref_col else \
            f"NULLIF(regexp_extract(norm({txt('d', notes, table)}),'(?:stt|hd|don|bill|so)\\s*[:#.]?\\s*([0-9a-z-]+)',1),'')"
        base = f"(SELECT {order} order_id,{num('d', table, debt)} debt,{ts('d', table, repay_day)} paid_at,{num('d', table, repay)} paid FROM {quote(table['name'])} d) l"
        _, orders = engine.lookup(f"SELECT DISTINCT l.order_id FROM {base} JOIN v_order_money m USING(order_id) WHERE l.order_id IS NOT NULL")
        if not orders:
            continue
        # Paid at purchase = what the order is owed minus the debt recorded for it; repayments are dated rows.
        at_purchase = (f"SELECT m.order_id,m.order_date paid_at,GREATEST(m.due-SUM(l.debt),0) amount FROM {base} JOIN v_order_money m USING(order_id) "
                       "WHERE l.debt IS NOT NULL GROUP BY m.order_id,m.order_date,m.due")
        repaid = f"SELECT l.order_id,l.paid_at,l.paid amount FROM {base} WHERE l.order_id IS NOT NULL AND l.paid>0 AND l.paid_at IS NOT NULL"
        add_receipts(engine, create, at_purchase + " UNION ALL " + repaid, [table["name"], "v_order_money"])
        mark_debt(engine, {row[0] for row in orders})
        attached["debt_ledger"] = table["name"]
        break
    _, prepaid = engine.lookup("SELECT COUNT(*) FILTER(WHERE prepaid>0) FROM v_order_money")
    if prepaid[0][0]:
        # Paid on the purchase day (deposit / down payment): lowers the debt; not a later repayment, not an installment.
        upfront = "SELECT order_id,order_date paid_at,prepaid amount FROM v_order_money WHERE prepaid>0"
        if "v_receipts" in engine.tables:
            create("v_receipts_with_upfront", "SELECT order_id,paid_at,amount FROM v_receipts UNION ALL " + upfront, ["v_receipts", "v_order_money"])
            con.execute("DROP TABLE v_receipts")
            con.execute("ALTER TABLE v_receipts_with_upfront RENAME TO v_receipts")
        else:
            create("v_receipts", upfront, ["v_order_money"])
        attached["prepaid_receipts"] = prepaid[0][0]
    reports["domain_retail"] = attached


TRADE_IN = r"(?<!doanh )\bthu (?:lai |mua |vao )?(?:may )?cu\b|(?<!doanh )\bthu\b[^.?,]{0,20}\bmay cu\b|(?<!doanh )\bthu may\b|\btro gia\b|\bdoi (?:may )?cu\b|\blen doi\b|\bmay cu (?:khach|cua khach|duoc thu|thu vao|doi)\b"
SIZE = re.compile(r"\bsize\s*(xxxl|xxl|xl|nb|newborn|s|m|l|\d)\b|\bco\s+(xxxl|xxl|xl|nb|s|m|l)\b")


def compile_retail_domain(frame, question, text, raw, engine, catalog):
    periods = resolve_periods(question.question, question.as_of_date, engine, exclude=(frame.product_filter, frame.customer_filter))
    if "v_members" in engine.tables and re.search(r"thanh vien moi|hoi vien moi|khach (?:hang )?moi (?:dang ky|lam the)|dang ky (?:thanh vien|hoi vien|moi|lam the)|(?:bao nhieu|may) (?:khach|nguoi) dang ky", text):
        if not periods:
            return None
        start, end = period_bounds(periods[0])
        return plan(f"SELECT COUNT(DISTINCT member_key) FROM v_members WHERE registered_at>={start} AND registered_at<{end}",
                    reason="Customers whose registration date falls in the period, including those who never bought.")
    if "v_members" in engine.tables and re.search(r"\bdiem\b", text) and not re.search(r"\b(?:dung|tru|doi|giam|quy doi)\b", text) \
            and re.search(r"(?:nhieu|cao|it) (?:diem )?nhat|(?:bao nhieu|may|so|tong(?: so)?) diem|diem hien (?:co|tai)|con (?:bao nhieu|may) diem", text):
        # Members' current points (customer list, as of the end of the data): only answerable on that day.
        _, present = engine.lookup("SELECT COUNT(points_balance) FROM v_members")
        cutoff = re.search(r"den het (\d{2})/(\d{2})/(\d{4})", fold(catalog.get("readme", "")))
        if not present[0][0] or not cutoff or question.as_of_date != dt.date(int(cutoff[3]), int(cutoff[2]), int(cutoff[1])):
            return None
        if re.search(r"\bnhat\b", text) and re.search(r"\b(?:nao|ai)\b", text):
            order = "ASC" if re.search(r"\bit (?:diem )?nhat\b", text) else "DESC"
            return plan(f"SELECT member_name FROM v_members WHERE points_balance IS NOT NULL ORDER BY points_balance {order},member_key LIMIT 1", "string", rounding="none",
                        reason="Member with the most current points (customer list, end of data).")
        return None
    payable_asked = bool(re.search(r"khach (?:phai|can) tra", text) or
                         re.search(r"khach tra.*(?:tong cong|bao nhieu)", text) and not re.search(r"\bno\b|\bgop\b|tra (?:hang|lai|ve)", text))
    if payable_asked and "v_order_money" in engine.tables and periods:
        start, end = period_bounds(periods[0])
        aggregate = "AVG(due)" if re.search(r"trung binh|binh quan", text) else "SUM(due)"
        return plan(f"SELECT {aggregate} FROM v_order_money WHERE status_rank<3 AND order_date>={start} AND order_date<{end}",
                    rounding="money", reason="Customer payable after invoice discount and trade-in, over all valid orders.")
    fee = None
    if "voucher" in text:
        fee = "voucher"
    elif re.search(r"\bdiem\b", text) and re.search(r"giam|tru|dung|doi|quy doi", text) and re.search(r"tien|bao nhieu|tong", text):
        fee = "points"
    elif re.search(r"phi ship|tien ship|\bship\b|phi giao|phi van chuyen|tien giao hang", text):
        fee = "ship"
    elif re.search(TRADE_IN, text) or re.search(r"\bmay cu\b", text) and not re.search(r"\bban\b|doanh thu|\bdthu\b", text):
        # 'thu máy cũ' is the trade-in; 'bán (được) máy cũ', 'doanh thu máy cũ' are sales of the used-phone group.
        fee = "tradein"
    elif (re.search(r"(?<!\w)bớt(?!\w)(?! (?:nợ|góp))", raw) or re.search(r"tien giam (?:gia )?(?:cua |tren |cho )?(?:ca )?(?:don|hoa don)|giam gia (?:cua |tren |cho )?(?:ca )?(?:don|hoa don)", text)) \
            and not re.search(r"doanh thu|\bdthu\b|khach (?:phai |can |da )?tra|sau (?:khi )?(?:bot|giam|tru)|da (?:bot|tru)", text):
        # 'bớt' (discount) is matched with its accent: 'Bột ngọt', 'Bột trét' fold to the same letters.
        # The discount written for the whole invoice ('Bớt', 'Giảm giá HĐ'): not taken off revenue (§1), asked on its own.
        fee = "order_discount"
    if fee and "v_order_money" in engine.tables:
        if not periods:
            return None
        _, present = engine.lookup(f"SELECT COUNT({fee}) FROM v_order_money")
        if not present[0][0]:
            # Explicit fee words on a shop that records no such amount: §7 unanswerable, unless an unrecognised amount column might be it.
            unmapped = (catalog.get("semantic", {}).get("domain_retail") or {}).get("unmapped_fee_like")
            return missing("Nguồn không ghi khoản tiền được hỏi trên hoá đơn.") if fee != "tradein" and not unmapped else None
        start, end = period_bounds(periods[0])
        where = f" WHERE status_rank<3 AND order_date>={start} AND order_date<{end}"
        if fee == "tradein" and "v_tradein_book" in engine.tables and re.search(r"\bnhat\b|\btop\b", text) and re.search(r"\b(?:may|dong may|dien thoai)\b(?: cu)?\b.*\bnao\b", text):
            # Phones taken in most often (or for the most money) on valid orders of the period.
            measure = "SUM(b.amount)" if re.search(r"\btien\b|gia (?:thu )?cao", text) else "COUNT(*)"
            k = top_k(text)
            return plan(f"SELECT model FROM (SELECT b.model,{measure} v FROM v_tradein_book b JOIN v_order_money m USING(order_id){where.replace('status_rank', 'm.status_rank').replace('order_date', 'm.order_date')} GROUP BY 1) ORDER BY v DESC,model LIMIT {k}",
                        "list" if k > 1 else "string", "column" if k > 1 else "scalar", "none", "Phones taken in most often on valid orders of the period.")
        if re.search(r"bao nhieu (?:phan tram|%)|may phan tram|(?:ti|ty) (?:le|trong)|\bchiem\b", text):
            # Share of invoices with the amount, or of the amount in the period's revenue (§1 valid invoices).
            if re.search(r"(?:ti|ty) le (?:so )?(?:don|hoa don|bill)|(?:so )?(?:don|hoa don|bill) co\b|(?:bao nhieu|may) phan tram (?:so )?(?:don|hoa don)|% (?:so )?(?:don|hoa don)", text):
                return plan(f"SELECT 100.0*COUNT(DISTINCT order_id) FILTER (WHERE {fee}>0)/NULLIF(COUNT(DISTINCT order_id),0) FROM v_order_money{where}", "percent", rounding="percent",
                            reason="Share of valid invoices carrying the amount (§1 valid invoices of the period).")
            return plan(f"SELECT 100.0*COALESCE(SUM({fee}),0)/NULLIF(SUM(revenue),0) FROM v_order_money{where}", "percent", rounding="percent",
                        reason="Amount over the revenue (§1) of the same valid invoices of the period.")
        if fee == "points" and re.search(r"(?:bao nhieu|may|so|tong(?: cong)?(?: so)?)\s+diem\b", text) and not re.search(r"\btien\b", text):
            # 'dùng bao nhiêu điểm': the number of points written on the lines, not their money value.
            _, counted = engine.lookup("SELECT COUNT(points_used) FROM v_order_money")
            if not counted[0][0]:
                return None
            return plan(f"SELECT COALESCE(SUM(points_used),0) FROM v_order_money{where}", reason="Points used, as written on the invoice lines ('Dùng n điểm').")
        if re.search(r"trung binh|binh quan|\btb\b", text):
            return plan(f"SELECT AVG({fee}) FROM v_order_money{where} AND {fee}>0", rounding="money",
                        reason="Average amount over the valid invoices that carry it.")
        if re.search(r"(?:bao nhieu|may|so)\s+(?:don|hoa don|bill)|(?:bao nhieu|so|may)\s+(?:chiec\s+|cai\s+)?may cu", text) and not re.search(r"bao nhieu tien|tong tien|so tien", text):
            return plan(f"SELECT COUNT(DISTINCT order_id) FROM v_order_money{where} AND {fee}>0", reason="Valid invoices with a positive amount of the requested kind.")
        return plan(f"SELECT COALESCE(SUM({fee}),0) FROM v_order_money{where}", rounding="money",
                    reason="Positive amounts summed by invoice date; cancelled and returned invoices excluded (§6).")
    if "v_order_money" in engine.tables and (re.search(r"khach (?:phai|can) tra", text) or re.search(r"\bkhach (?:da )?tra\b(?! (?:lai|no|hang|ve|gop|truoc|bot|tien no))", text) and re.search(r"tong|bao nhieu|\bbn\b", text) and not re.search(r"\bno\b|gop", text)):
        if not periods:
            return None
        start, end = period_bounds(periods[0])
        _, present = engine.lookup("SELECT COUNT(due) FROM v_order_money")
        if not present[0][0]:
            return None
        return plan(f"SELECT SUM(due) FROM v_order_money WHERE status_rank<3 AND order_date>={start} AND order_date<{end}", rounding="money",
                    reason="Amount after discounts and trade-in deduction, including unpaid debt; valid invoices only.")
    if "v_phone_sales" in engine.tables:
        result = compile_phone(frame, question, text, raw, engine, periods)
        if result is not None:
            return result
    if "v_sales_attrs" in engine.tables:
        return compile_attributes(frame, question, text, raw, engine, periods)
    return None


def named_alias(engine, text):
    """A catalog alias ('huggies M', 'aptamil 2') written in the question names one item, not a size/brand aggregate."""
    if "v_products" not in engine.tables:
        return False
    _, rows = engine.lookup("SELECT product_name,product_alias FROM v_products WHERE product_alias IS NOT NULL")
    targets=defaultdict(set)
    for product,aliases in rows:
        for item in re.split(r"[,;/|]",str(aliases)):
            if len(words(item))>=5:targets[words(item)].add(product)
    return any(len(products)==1 and re.search(r"(?<![a-z0-9])"+re.escape(name)+r"(?![a-z0-9])",text) for name,products in targets.items())


def compile_attributes(frame, question, text, raw, engine, periods):
    if named_alias(engine, text):
        return None
    # One whole item named ('Phấn rôm Johnson's Baby 200g', 'Sữa Nan Optipro Plus 2 800g'): that item, not its brand, số or size.
    _, item_rows = engine.lookup("SELECT DISTINCT product_name FROM v_sales_attrs WHERE product_name IS NOT NULL")
    items = {words(r[0]) for r in item_rows if len(words(r[0]).split()) >= 2}
    if words(frame.product_filter) in items or any(re.search(r"(?<![a-z0-9])" + re.escape(item) + r"(?![a-z0-9])", text) for item in items):
        return None
    size = SIZE.search(text)
    size_value = None
    if size:
        size_value = (size[1] or size[2]).upper()
        size_value = "NB" if size_value == "NEWBORN" else size_value
    stage = re.search(r"\bsua\b[^?.,]*?\bso\s*(\d)\b|\bso\s*(\d)\b[^?.,]*\bsua\b", text) if re.search(r"\bsua\b", text) else None
    stage_value = (stage[1] or stage[2]) if stage else None
    _, brand_rows = engine.lookup("SELECT DISTINCT brand FROM v_sales_attrs WHERE brand IS NOT NULL")
    brands = [r[0] for r in brand_rows if words(r[0]) and re.search(r"(?<![a-z0-9])" + re.escape(words(r[0])) + r"(?![a-z0-9])", text)]
    asks_brand = bool(re.search(r"hãng|thương hiệu|nhãn hiệu", raw))
    asks_size = bool(re.search(r"\bsize\b|\bco\b (?:nao|gi)", text))
    rank = re.search(r"\bnhat\b|\btop\b|ban chay|chay nhat", text)
    if not (size_value or stage_value or brands or (rank and (asks_brand or asks_size))):
        return None
    _, available = engine.lookup("SELECT COUNT(size),COUNT(stage),COUNT(brand) FROM v_sales_attrs")
    if size_value and not available[0][0] or stage_value and not available[0][1]:
        return None
    if not periods:
        return None
    start, end = period_bounds(periods[0])
    period = f"order_date>={start} AND order_date<{end}"
    conditions = []
    if size_value:
        conditions.append(f"size={literal(size_value)}")
    if stage_value:
        conditions.append(f"stage={literal(stage_value)}")
    if len(brands) == 1:
        conditions.append(f"norm(brand)=norm({literal(brands[0])})")
    elif len(brands) > 1:
        return None
    diaper = bool(re.search(r"\bbim\b|\bta\b", text)) or bool(size_value) and not stage_value
    revenue = bool(re.search(r"doanh thu|\bdthu\b|\btien\b", text))
    measure = "SUM(revenue)" if revenue else "SUM(base_quantity)"
    rounding = "money" if revenue else "none"
    if re.search(r"\bbim\b|\bta\b",text):
        conditions.append("is_diaper")
    if re.search(r"\bsua bot\b",text):
        _,groups=engine.lookup("SELECT DISTINCT product_group FROM v_sales_attrs WHERE product_group IS NOT NULL")
        milk=[g for (g,) in groups if words(g)=="sua bot"]
        if not milk:return None
        conditions.append("norm(product_group) IN ("+",".join("norm("+literal(g)+")" for g in milk)+")")
    if rank and (not conditions or asks_brand and not brands or asks_size and not size_value):
        column = "brand" if asks_brand else "size" if asks_size else None
        if not column:
            return None
        scope = (" AND "+" AND ".join(conditions)) if conditions else (" AND is_diaper" if diaper else "")
        named_group = re.search(r"\b(?:hang|thuong hieu|nhan hieu|size|co)\s+([a-z]+(?: [a-z]+)?)", text)
        if named_group and not diaper:
            # 'hãng sữa nào bán chạy nhất': rank brands within the groups that word names ('sữa' = sữa bột + sữa pha sẵn).
            _, groups = engine.lookup("SELECT DISTINCT product_group FROM v_sales_attrs WHERE product_group IS NOT NULL")
            for candidate in (named_group[1], named_group[1].split()[0]):
                chosen = [g[0] for g in groups if words(g[0]) == candidate or words(g[0]).startswith(candidate + " ")]
                if chosen:
                    scope = " AND norm(product_group) IN (" + ",".join(f"norm({literal(g)})" for g in chosen) + ")"
                    break
        k = top_k(re.sub(r"\bso\s*\d+\b","",text) if stage_value else text)
        query = f"SELECT {column} FROM (SELECT {column},{measure} value FROM v_sales_attrs WHERE {period}{scope} AND {column} IS NOT NULL GROUP BY {column}) ORDER BY value DESC,{column} LIMIT {k}"
        return plan(query, "list" if k > 1 else "string", "column" if k > 1 else "scalar", "none", "Catalog attribute ranking from the size/brand column only.")
    if rank:
        return None
    where = f" WHERE {period} AND " + " AND ".join(conditions)
    if re.search(r"phan tram|%|(?:ti|ty) le|chiem|ti trong|ty trong", text):
        if not diaper:
            return None
        numerator = f"SELECT SUM(base_quantity) v FROM v_sales_attrs{where} AND is_diaper"
        denominator = f"SELECT SUM(base_quantity) v FROM v_sales_attrs WHERE {period} AND is_diaper"
        return plan(f"WITH a AS ({numerator}),b AS ({denominator}) SELECT 100.0*a.v/NULLIF(b.v,0) FROM a,b", "percent", rounding="percent",
                    reason="Share of a size/brand over all diaper packs sold in the same period (§6 Mẹ và bé).")
    if not re.search(r"bao nhieu|\bmay\b|tong|\bbn\b", text):
        return None
    if not revenue:
        unit = re.search(r"(?:bao nhieu|\bmay\b|\bbn\b)\s+([a-z]+)\b", text)
        if unit and unit[1] not in {"tien", "san pham", "mat hang"}:
            _, units = engine.lookup(f"SELECT DISTINCT norm(base_unit) FROM v_sales_attrs{where}")
            if {r[0] for r in units} != {unit[1]}:
                return None  # requested unit is not the base unit of every matched item
    return plan(f"SELECT {measure} FROM v_sales_attrs{where}", rounding=rounding, reason="Items selected by catalog Số/Size/brand columns; blank attributes are not assigned.")


def compile_phone(frame, question, text, raw, engine, periods):
    if not periods:
        return None
    start, end = period_bounds(periods[0])
    period = f"order_date>={start} AND order_date<{end}"
    if re.search(r"bao hanh", text) and "v_warranty_claims" in engine.tables:
        warranty = compile_warranty_claims(text, engine, start, end, period, question.as_of_date)
        if warranty is not None:
            return warranty
    # A question naming one whole item ('Samsung Galaxy A36 5G 128GB Tím', 'Ốp lưng chống sốc Samsung') is about that item,
    # not its model line or brand; only 'dòng máy nào', 'hãng nào' rank lines and brands.
    _, product_rows = engine.lookup("SELECT DISTINCT product_name FROM v_phone_sales WHERE product_name IS NOT NULL")
    items = {words(r[0]) for r in product_rows if len(words(r[0]).split()) >= 2}
    named_item = any(re.search(r"(?<![a-z0-9])" + re.escape(item) + r"(?![a-z0-9])", text) for item in items) or words(frame.product_filter) in items
    if named_item and not re.search(r"dong (?:may|dien thoai)(?: [a-z0-9]+){0,3} nao|\btop\b.*dong (?:may|dien thoai)", text) and not re.search(r"(?:hãng|thương hiệu)(?: \S+){0,2} nào", raw):
        return None
    _, line_rows = engine.lookup("SELECT DISTINCT model_line FROM v_phone_sales WHERE is_phone")
    lines = [r[0] for r in line_rows if words(r[0]) and re.search(r"(?<![a-z0-9])" + re.escape(words(r[0])) + r"(?![a-z0-9])", text)]
    # A named product with its capacity ('iPhone 16 Pro Max 256GB') is that product (every colour), not the model line:
    # the item names answer it, also when a shorter line ('iPhone 16') is inside the name.
    if any(re.search(re.escape(words(name)) + r"\s*\d+\s*(?:gb|tb)\b", text) for name in lines) and not re.search(r"dong (?:may|dien thoai)(?: [a-z0-9]+){0,3} nao|\btop\b|\bnhat\b", text):
        return None
    lines = [name for name in lines if not re.search(re.escape(words(name)) + r"\s*\d+\s*(?:gb|tb)\b", text)]
    _, brand_rows = engine.lookup("SELECT DISTINCT brand FROM v_phone_sales WHERE is_phone")
    brands_named = [r[0] for r in brand_rows if words(r[0]) and re.search(r"(?<![a-z0-9])" + re.escape(words(r[0])) + r"(?![a-z0-9])", text)]
    if not brands_named and re.search(r"\biphone\b", text) and any(r[0] == "Apple" for r in brand_rows):
        brands_named = ["Apple"]   # rulebook §6: an iPhone is the Apple brand
    if frame.product_filter and not named_item:
        target=words(frame.product_filter)
        generic={"may","dt","may dt","may dien thoai","dien thoai","may moi","may cu","dien thoai moi","dien thoai cu","phu kien"}
        known={words(r[0]) for r in line_rows+brand_rows}
        if any(r[0]=="Apple" for r in brand_rows):known.add("iphone")
        stripped=re.sub(r"^(?:may|dien thoai)\s+","",target)
        if target not in generic and target not in known and stripped not in known:
            return None  # resolve the requested item/alias; never broaden an accessory to its phone brand
    # Rulebook §6: the model line is the name before '…GB', so 'máy iPhone 15' is the iPhone 15 line (not Pro Max).
    line = bool(re.search(r"dong (?:may|dien thoai)", text) or lines)
    # Brands are the first words of phones only, so accessory words ('Ốp', 'Cáp') never read as a brand. A line named with its
    # capacity ('iPhone 15 128GB') is one product: it does not fall back to the brand either.
    capacity_named = bool(re.search(r"\d+\s*(?:gb|tb)\b", text))
    brand = bool(re.search(r"hãng|thương hiệu", raw)) or (not lines and bool(brands_named) and not capacity_named)
    if not (line or brand):
        if re.search(r"dien thoai|\bmay moi\b|\bmay cu\b|khong tinh phu kien",text):
            scope = f" WHERE {period} AND is_phone"
            new_phone=bool(re.search(r"(?:may|dien thoai|dt) moi\b",text))
            used_phone=bool(re.search(r"(?:may|dien thoai|dt) cu\b",text))
            if new_phone and not used_phone:
                scope += " AND norm(product_group) IN ('may moi','dien thoai moi')"
            elif used_phone and not new_phone:
                scope += " AND norm(product_group) IN ('may cu','dien thoai cu')"
            if re.search(r"phan tram|%|chiem",text) and (new_phone != used_phone):
                measure="SUM(revenue)" if re.search(r"tien|doanh thu",text) else "SUM(base_quantity)"
                return plan(f"SELECT 100.0*(SELECT {measure} FROM v_phone_sales{scope})/NULLIF((SELECT {measure} FROM v_phone_sales WHERE {period} AND is_phone),0)",
                            "percent",rounding="percent",reason="Recorded new/used phone group share among all phones, excluding accessories.")
            if re.search(r"trung binh|binh quan",text) and re.search(r"moi may|mot may|1 may|moi chiec",text):
                return plan("SELECT SUM(revenue)/NULLIF(SUM(base_quantity),0) FROM v_phone_sales"+scope,rounding="money",
                            reason="Revenue of phones divided by number of phones, excluding accessories.")
            if re.search(r"mat hang nao|san pham nao",text) and re.search(r"nhieu.*nhat|chay nhat",text):
                return plan("SELECT product_name FROM v_phone_sales"+scope+" GROUP BY product_name ORDER BY SUM(base_quantity) DESC,product_name LIMIT 1",
                            "string",rounding="none",reason="Top phone product in the recorded phone group.")
            if re.search(r"(?:bao nhieu|may) (?:chiec |cai )?(?:dien thoai|may)\b",text) and not re.search(r"tien|doanh thu|nhat",text):
                return plan("SELECT SUM(base_quantity) FROM v_phone_sales"+scope,reason="Phones sold in valid orders, accessories excluded.")
        return None
    column = "model_line" if line else "brand"
    rank = re.search(r"\bnhat\b|\btop\b|chay nhat|ban chay", text)
    revenue = bool(re.search(r"doanh thu|\bdthu\b|\btien\b", text))
    measure = "SUM(revenue)" if revenue else "SUM(base_quantity)"
    if re.search(r"lãi|lợi nhuận|\blời\b", raw):
        # Rulebook §5: gross profit uses the cost written on each sale line, never re-derived from purchases.
        _, costed = engine.lookup("SELECT COUNT(unit_cost) FROM v_phone_sales WHERE is_phone")
        if not costed[0][0]:
            return missing("Sổ bán không ghi giá vốn trên dòng bán (§5), nên không tính được lãi.")
        measure, revenue = "SUM(revenue-quantity*unit_cost)", True
    _, values = engine.lookup(f"SELECT DISTINCT {column} FROM v_phone_sales WHERE is_phone")
    named = [r[0] for r in values if words(r[0]) and re.search(r"(?<![a-z0-9])" + re.escape(words(r[0])) + r"(?![a-z0-9])", text)]
    if column == "model_line" and named:
        longest = max(len(words(name)) for name in named)
        named = [name for name in named if len(words(name)) == longest]   # 'iPhone 15 Pro Max' also names 'iPhone 15' 
    if column == "brand" and not named and re.search(r"\biphone\b", text):
        named = ["Apple"]
    if rank:
        k = top_k(text)
        target, scope = column, ""
        asks_group = (re.search(r"(?:hãng|thương hiệu)(?: \S+){0,2} nào", raw) if column == "brand"
                      else re.search(r"dong (?:may|dien thoai)(?: [a-z0-9]+){0,3} nao|\btop\b.*dong (?:may|dien thoai)", text))
        if not asks_group:
            # 'máy Samsung nào', 'iPhone 15 bản nào bán chạy nhất': the products of the named brand/line, not the brand/line itself.
            if len(named) != 1:
                return None
            target, scope = "product_name", f" AND {column}={literal(named[0])}"
        elif column == "model_line" and len(brands_named) == 1:
            scope = f" AND brand={literal(brands_named[0])}"   # 'dòng máy Samsung nào'
        query = f"SELECT {target} FROM (SELECT {target},{measure} value FROM v_phone_sales WHERE {period} AND is_phone{scope} GROUP BY {target}) ORDER BY value DESC,{target} LIMIT {k}"
        return plan(query, "list" if k > 1 else "string", "column" if k > 1 else "scalar", "none", "Model line = name before capacity GB; brand = first word, iPhone is Apple.")
    if len(named) == 1 and re.search(r"(?:bao nhieu|may|so|dem) (?:don|hoa don|bill)\b", text) and not re.search(r"(?:bao nhieu|may) (?:chiec|cai|may)\b", text):
        return plan(f"SELECT COUNT(DISTINCT order_id) FROM v_phone_sales WHERE {period} AND is_phone AND {column}={literal(named[0])}",
                    reason="Valid orders (§1) with a phone of the named model line/brand.")
    if len(named) == 1 and re.search(r"phan tram|%|(?:ti|ty) (?:le|trong)|chiem", text):
        # Share of the named brand/line: of phones sold (counts), of the whole shop's revenue (money, §1) unless phones are named.
        whole = revenue and not re.search(r"doanh thu (?:ban )?(?:may|dien thoai|dt)\b|(?:cua|trong) (?:cac )?(?:may|dien thoai) ban", text)
        scope = "" if whole else " AND is_phone"
        return plan(f"WITH a AS (SELECT {measure} v FROM v_phone_sales WHERE {period} AND is_phone AND {column}={literal(named[0])}),"
                    f"b AS (SELECT {measure} v FROM v_phone_sales WHERE {period}{scope}) SELECT 100.0*a.v/NULLIF(b.v,0) FROM a,b", "percent", rounding="percent",
                    reason="Share of the named model line/brand among phones sold (revenue: of the whole shop) in valid orders.")
    if len(named) != 1 or not re.search(r"bao nhieu|\bmay\b|tong|\bbn\b", text):
        return None
    return plan(f"SELECT {measure} FROM v_phone_sales WHERE {period} AND is_phone AND {column}={literal(named[0])}", rounding="money" if revenue else "none",
                reason="Phones of the named model line/brand in valid orders.")


def compile_warranty_claims(text, engine, start, end, period, as_of_date):
    """Warranty book (README: one row per visit, the phone line, fault and handling): counts, rates and rankings.
    Rate = claims received in the period over phones sold in it (§6 Điện thoại); per line, over that line's phones."""
    claims = f"FROM v_warranty_claims WHERE received_at>={start} AND received_at<{end}"
    if re.search(r"may (?:ban|mua)|ban (?:ra )?thang|may ban thang",text) and re.search(r"toi|den|tinh",text):
        _, coverage=engine.lookup("SELECT COUNT(*),COUNT(purchased_at),COUNT(imei) FROM v_warranty_claims")
        if coverage[0][0] != coverage[0][1] or coverage[0][0] != coverage[0][2]:
            return None
        return plan(f"SELECT COUNT(DISTINCT imei) FROM v_warranty_claims WHERE purchased_at>={start} AND purchased_at<{end} AND received_at<TIMESTAMP {literal(as_of_date+dt.timedelta(days=1))}",
                    reason="Distinct IMEIs sold in the requested period and received for warranty by the cutoff.")
    brand_of = "CASE WHEN regexp_matches(norm(model),'^iphone') THEN 'Apple' ELSE regexp_extract(trim(model),'^(\\S+)',1) END"
    def named(column, sql):
        _, rows = engine.lookup(sql)
        found = [r[0] for r in rows if r[0] and words(r[0]) and re.search(r"(?<![a-z0-9])" + re.escape(words(r[0])) + r"(?![a-z0-9])", text)]
        longest = max((len(words(x)) for x in found), default=0)
        return [x for x in found if len(words(x)) == longest]
    # A line named in the question is that line even when none of it came for warranty in the period (answer 0, not its brand).
    models = named("model", "SELECT DISTINCT model FROM v_warranty_claims UNION SELECT DISTINCT model_line FROM v_phone_sales WHERE is_phone")
    faults = named("fault", "SELECT DISTINCT fault FROM v_warranty_claims")
    actions = named("action", "SELECT DISTINCT action FROM v_warranty_claims")
    brands = named("brand", f"SELECT DISTINCT {brand_of} FROM v_warranty_claims") if not models else []
    if not models and not brands and re.search(r"\biphone\b", text):
        brands = ["Apple"]
    conditions = []
    if len(models) == 1:
        conditions.append(f"norm(model)=norm({literal(models[0])})")
    if len(brands) == 1:
        conditions.append(f"{brand_of}={literal(brands[0])}")
    if len(faults) == 1:
        conditions.append(f"norm(fault)=norm({literal(faults[0])})")
    if len(actions) == 1:
        conditions.append(f"norm(action)=norm({literal(actions[0])})")
    if len(models) > 1 or len(faults) > 1 or len(actions) > 1:
        return None
    where = claims + "".join(" AND " + c for c in conditions)
    rate = re.search(r"(?:ti|ty) le|phan tram|%", text)
    rank = re.search(r"\bnhat\b|\btop\b", text) and re.search(r"\bnao\b|\bgi\b|\btop\b", text)
    if rank:
        target = ("fault" if re.search(r"\bloi\b|\bbenh\b|hong (?:gi|nao)", text) else "action" if re.search(r"xu ly", text)
                  else brand_of if re.search(r"\bhang\b|thuong hieu", text) and not re.search(r"dong may", text) else "model" if re.search(r"dong may|\bmay\b|dien thoai|\bdt\b", text) else None)
        if not target:
            return None
        k = top_k(text)
        if rate and target in ("model", brand_of):
            sold_key = "model_line" if target == "model" else "brand"
            query = (f"WITH c AS (SELECT {target} k,COUNT(*) n {where} GROUP BY 1),s AS (SELECT {sold_key} k,SUM(quantity) n FROM v_phone_sales WHERE {period} AND is_phone GROUP BY 1) "
                     f"SELECT k FROM (SELECT c.k,100.0*c.n/s.n v FROM c JOIN s ON norm(c.k)=norm(s.k)) ORDER BY v DESC,k LIMIT {k}")
        else:
            query = f"SELECT k FROM (SELECT {target} k,COUNT(*) n {where} AND {target} IS NOT NULL GROUP BY 1) ORDER BY n DESC,k LIMIT {k}"
        return plan(query, "list" if k > 1 else "string", "column" if k > 1 else "scalar", "none", "Warranty visits ranked by phone line, brand, fault or handling in the period.")
    if rate:
        sold = f"SELECT SUM(quantity) v FROM v_phone_sales WHERE {period} AND is_phone"
        if len(models) == 1:
            sold += f" AND norm(model_line)=norm({literal(models[0])})"
        elif len(brands) == 1:
            sold += f" AND brand={literal(brands[0])}"
        return plan(f"WITH a AS (SELECT COUNT(*) v {where}),b AS ({sold}) SELECT 100.0*a.v/NULLIF(b.v,0) FROM a,b", "percent", rounding="percent",
                    reason="Warranty claims received in the period over phones sold in it (§6 Điện thoại).")
    if re.search(r"bao nhieu|\bmay\b|so luot|\bdem\b", text):
        if re.search(r"(?:bao nhieu|may) (?:chiec |cai )?(?:may|dien thoai)\b(?! (?:nao|gi))", text) and not re.search(r"luot|\blan\b", text):
            return plan(f"SELECT COUNT(DISTINCT COALESCE(imei,model)) {where}", reason="Distinct phones (IMEI) brought for warranty in the period.")
        return plan(f"SELECT COUNT(*) {where}", reason="Warranty visits received in the period (one row per visit).")
    return None


def compile_installments(question, text, engine):
    if "v_installments" not in engine.tables:
        return None
    raw = unicodedata.normalize("NFC", question.question).lower()
    if "gộp" in raw and "góp" not in raw or re.search(r"\b(?:lai|loi) gop\b|loi nhuan gop", text):
        return None   # 'lãi gộp' (gross) folds to the same letters as 'góp' (installment)
    if re.search(r"(?:khong (?:tinh|ke)|tru|ngoai|bo) (?:cac )?(?:don )?(?:tra gop|gop)\b", text):
        return None   # 'không tính trả góp': the debt book only
    asks_count = re.search(r"\b(?:bao nhieu|may|so luong|so)\s+(?:don|hoa don|hd|khach|nguoi|ky|lan|khoan|hop dong)\b", text)
    if not re.search(r"qua han|tre han|tre gop|cham gop", text):
        if not re.search(r"tra gop|\bgop\b", text):
            return None
        periods = resolve_periods(question.question, question.as_of_date, engine)
        valid_debt = "JOIN v_orders_all o USING(order_id) WHERE o.status<>'cancelled'"
        _, customer_rows = engine.lookup("SELECT DISTINCT customer_key,customer_name FROM v_orders_all WHERE customer_key IS NOT NULL")
        def customer_words(name):
            # Source customer labels may append a phone; the identity key remains unchanged.
            return words(re.sub(r"\s*[-(]?\s*\d[\d\s.()-]{7,}$", "", name or ""))
        matches = [(key,name) for key,name in customer_rows if name and len(customer_words(name).split())>=2
                   and re.search(r"(?<![a-z0-9])"+re.escape(customer_words(name))+r"(?![a-z0-9])", text)]
        if matches:
            longest=max(len(customer_words(name)) for _,name in matches)
            keys={key for key,name in matches if len(customer_words(name))==longest}
            if len(keys)!=1:
                return None
            valid_debt += " AND o.customer_key="+literal(next(iter(keys)))
        # Still to pay on installments at a day (rulebook §6: the unpaid part is debt), whatever the due dates.
        # 'tiền góp khách còn nợ', 'góp còn nợ bao nhiêu', 'còn nợ tiền góp': the balance, not the payments of that day.
        balance = re.search(r"con (?:phai )?gop|chua gop|con lai (?:phai )?gop|du no (?:tra )?gop|no (?:tien )?(?:tra )?gop|tien gop con (?:no|lai|thieu)|con thieu (?:tien )?gop", text) \
            or re.search(r"\bgop\b", text) and re.search(r"\b(?:con|dang) (?:no|thieu)\b|chua (?:tra|thu|gop)", text) and not re.search(r"\bgop duoc\b|\bda gop\b|thu (?:duoc|ve)|\bnhan duoc\b", text)
        if balance or re.search(r"\bky\b.*\bcon lai\b|\bcon lai\b.*\bky\b", text):
            day = periods[0].end if periods else question.as_of_date
            cutoff = f"TIMESTAMP {literal(day + dt.timedelta(days=1))}"
            paid = f"CASE WHEN paid_at IS NOT NULL AND paid_at<{cutoff} THEN COALESCE(paid_amount,amount_due) ELSE 0 END"
            owed = f"SELECT i.order_id,o.customer_key,o.customer_name,GREATEST(amount_due-({paid}),0) unpaid FROM v_installments i {valid_debt} AND o.order_date<{cutoff}"
            if re.search(r"\bnhat\b|\btop\b", text) and re.search(r"\b(?:khach|nguoi)\b.*\b(?:nao|ai)\b|\bai\b", text):
                return plan(f"SELECT name FROM (SELECT FIRST(customer_name) name,SUM(unpaid) v FROM ({owed}) WHERE customer_key IS NOT NULL GROUP BY customer_key) ORDER BY v DESC,name LIMIT 1",
                            "string", rounding="none", reason="Customer owing the most on installments as at the asked day.")
            if asks_count:
                unit = asks_count[0].split()[-1]
                if unit in {"ky", "lan", "khoan"}:
                    return plan(f"SELECT COUNT(*) FROM ({owed}) WHERE unpaid>0", reason="Installment periods not yet fully paid as at the asked day.")
                if unit in {"khach", "nguoi"}:
                    return plan(f"SELECT COUNT(DISTINCT customer_key) FROM ({owed}) WHERE unpaid>0 AND customer_key IS NOT NULL", reason="Identified customers still owing on installments.")
                return plan(f"SELECT COUNT(DISTINCT order_id) FROM ({owed}) WHERE unpaid>0", reason="Installment orders not yet fully paid as at the asked day.")
            return plan(f"SELECT COALESCE(SUM(unpaid),0) FROM ({owed})", rounding="money", reason="Unpaid part of every installment of non-cancelled orders, as at the asked day.")
        if re.search(r"(?:bao nhieu|may) (?:khach|nguoi)\b.*(?:dang|con) (?:tra gop|gop|mua tra gop)|(?:khach|nguoi) dang (?:tra gop|gop)", text):
            cutoff = f"TIMESTAMP {literal(question.as_of_date + dt.timedelta(days=1))}"
            paid = f"CASE WHEN paid_at IS NOT NULL AND paid_at<{cutoff} THEN COALESCE(paid_amount,amount_due) ELSE 0 END"
            return plan(f"SELECT COUNT(DISTINCT o.customer_key) FROM v_installments i {valid_debt} AND o.customer_key IS NOT NULL AND ({paid})<amount_due",
                        reason="Identified customers with an installment not yet fully paid today.")
        if not periods:
            return None
        start, end = period_bounds(periods[0])
        if re.search(r"den han|toi han", text) and re.search(r"(?:tinh |het |den |toi )?(?:hom nay|bua ni)|tinh ca ky|tinh luon ky",text):
            return plan(f"SELECT COUNT(*) FROM v_installments i {valid_debt} AND i.due_date<={literal(question.as_of_date)} AND o.order_date<={literal(question.as_of_date)}",
                        reason="Cumulative installment periods due through today inclusive for the named customer.")
        if re.search(r"den han|toi han", text):
            # Installments falling due in the period: how many, or how much.
            if re.search(r"(?:bao nhieu|may|so)\s+ky\b", text):
                return plan(f"SELECT COUNT(*) FROM v_installments i {valid_debt} AND i.due_date>={start} AND i.due_date<{end}", reason="Installments due in the period.")
            if re.search(r"bao nhieu tien|so tien|tong (?:so )?tien|tien gop", text):
                return plan(f"SELECT COALESCE(SUM(amount_due),0) FROM v_installments i {valid_debt} AND i.due_date>={start} AND i.due_date<{end}", rounding="money",
                            reason="Amount of the installments due in the period.")
            return None
        if re.search(r"tra truoc|dat coc|tien coc|dua truoc", text) and not asks_count:
            # Rulebook §6: the down payment is not an installment; it is paid on the purchase day of valid orders.
            source = ("SELECT order_id,amount FROM installment_upfront" if "installment_upfront" in engine.tables
                      else "SELECT order_id,prepaid amount FROM v_order_money WHERE prepaid>0" if "v_order_money" in engine.tables else None)
            if source is None:
                return None
            return plan(f"SELECT COALESCE(SUM(u.amount),0) FROM ({source}) u JOIN v_orders o USING(order_id) WHERE o.order_date>={start} AND o.order_date<{end} AND o.order_id IN (SELECT order_id FROM v_installments)",
                        rounding="money", reason="Down payments of valid orders bought on installment in the period (not installments).")
        if re.search(r"doanh thu|tien ban|tien hang|ban duoc", text) and not re.search(r"tien (?:tra )?gop", text):
            # Rulebook §6: revenue counts the full price on the purchase day; installments are not revenue.
            return plan(f"SELECT COALESCE(SUM(revenue),0) FROM v_orders WHERE order_date>={start} AND order_date<{end} AND order_id IN (SELECT order_id FROM v_installments)",
                        rounding="money", reason="Revenue of valid orders bought on installment, by order date.")
        # Rulebook §6: an installment is a debt repayment, dated when paid; cancelled orders carry no debt.
        if re.search(r"tien (?:tra )?gop|gop duoc|khach gop|thu (?:duoc |ve )?(?:bao nhieu )?(?:tien )?(?:tra )?gop", text) and not asks_count:
            if "v_installment_payments" in engine.tables:
                # Actual payment rows of a contract book; a payment may straddle two periods.
                return plan(f"SELECT COALESCE(SUM(p.amount),0) FROM v_installment_payments p JOIN v_orders_all o USING(order_id) WHERE o.status<>'cancelled' AND p.paid_at>={start} AND p.paid_at<{end}",
                            rounding="money", reason="Installment payments dated in the period (debt repayments); the down payment is not an installment.")
            paid = "CASE WHEN paid_amount IS NOT NULL THEN paid_amount ELSE amount_due END"
            return plan(f"SELECT COALESCE(SUM({paid}),0) FROM v_installments i JOIN v_orders_all o USING(order_id) WHERE o.status<>'cancelled' AND i.paid_at>={start} AND i.paid_at<{end}",
                        rounding="money", reason="Installment payments dated in the period (debt repayments); the down payment is not an installment.")
        bought = f"SELECT order_id FROM v_orders WHERE order_date>={start} AND order_date<{end} AND order_id IN (SELECT order_id FROM v_installments)"
        if re.search(r"trung binh|binh quan", text) and re.search(r"\bky\b", text):
            # Periods per installment order bought in the period.
            return plan(f"SELECT AVG(n) FROM (SELECT order_id,COUNT(*) n FROM v_installments WHERE order_id IN ({bought}) GROUP BY 1)", rounding="percent",
                        reason="Average number of installment periods per order bought in the period.")
        if re.search(r"(?:ti|ty) le|phan tram|%|chiem", text):
            phones = re.search(r"\b(?:ban|mua|co) (?:may|dien thoai)\b|\bdon (?:ban |mua )?may\b", text) and "v_phone_sales" in engine.tables
            whole = (f"SELECT DISTINCT order_id FROM v_phone_sales WHERE is_phone AND order_date>={start} AND order_date<{end}" if phones
                     else f"SELECT order_id FROM v_orders WHERE order_date>={start} AND order_date<{end}")
            return plan(f"SELECT 100.0*(SELECT COUNT(*) FROM ({bought}))/NULLIF((SELECT COUNT(*) FROM ({whole})),0)", "percent", rounding="percent",
                        reason="Installment orders over valid orders of the period (orders with a phone when phones are named).")
        if re.search(r"\bnhat\b|\btop\b", text) and re.search(r"\b(?:may|dien thoai|dong may|san pham|mat hang)\b.*\bnao\b", text) and "v_phone_sales" in engine.tables:
            k = top_k(text)
            column = "model_line" if re.search(r"dong (?:may|dien thoai)", text) else "product_name"
            return plan(f"SELECT {column} FROM (SELECT {column},SUM(base_quantity) v FROM v_phone_sales WHERE is_phone AND order_id IN ({bought}) GROUP BY 1) ORDER BY v DESC,{column} LIMIT {k}",
                        "list" if k > 1 else "string", "column" if k > 1 else "scalar", "none", "Phones most bought on installment in the period (units, §3).")
        if asks_count and re.search(r"\b(?:don|hoa don|hd|hop dong)\b", text):
            return plan(f"SELECT COUNT(DISTINCT order_id) FROM v_orders WHERE order_date>={start} AND order_date<{end} AND order_id IN (SELECT order_id FROM v_installments)",
                        reason="Valid orders bought on installment (orders with an installment schedule), by order date.")
        return None
    periods = resolve_periods(question.question, question.as_of_date, engine)
    day = periods[0].end if periods else question.as_of_date
    cutoff = f"TIMESTAMP {literal(day + dt.timedelta(days=1))}"
    paid = f"CASE WHEN paid_at IS NOT NULL AND paid_at<{cutoff} THEN COALESCE(paid_amount,amount_due) ELSE COALESCE(CASE WHEN paid_at IS NULL THEN paid_amount END,0) END"
    overdue = f"SELECT i.*,o.customer_key,amount_due-({paid}) unpaid FROM v_installments i LEFT JOIN v_orders_all o USING(order_id) WHERE CAST(due_date AS DATE)<DATE {literal(day)} AND ({paid})<amount_due"
    if re.search(r"\bnhat\b|\btop\b", text) and re.search(r"\b(?:khach|nguoi)\b.*\b(?:nao|ai)\b|\bai\b", text):
        return plan(f"SELECT name FROM (SELECT FIRST(o.customer_name) name,SUM(x.unpaid) v FROM ({overdue}) x JOIN v_orders_all o USING(order_id) WHERE x.customer_key IS NOT NULL GROUP BY x.customer_key) ORDER BY v DESC,name LIMIT 1",
                    "string", rounding="none", reason="Customer with the largest overdue installment amount.")
    if re.search(r"bao nhieu tien|so tien|tong tien|bao nhieu (?:dong|d)\b", text) or re.search(r"\btien\b", text) and re.search(r"bao nhieu|\bbn\b|\btong\b", text) and not re.search(r"(?:bao nhieu|may)\s+(?:nguoi|khach|don|hop dong|ky|lan|khoan)\b", text):
        return plan(f"SELECT SUM(unpaid) FROM ({overdue})", rounding="money", reason="Unpaid part of installments due before the asked day.")
    if re.search(r"(?:bao nhieu|may)\s+(?:nguoi|khach)", text):
        return plan(f"SELECT COUNT(DISTINCT customer_key) FROM ({overdue}) WHERE customer_key IS NOT NULL", reason="Customers with an installment due before the asked day and not fully paid.")
    if re.search(r"(?:bao nhieu|may)\s+(?:don|hop dong)", text):
        return plan(f"SELECT COUNT(DISTINCT order_id) FROM ({overdue})", reason="Orders with an overdue installment.")
    if re.search(r"(?:bao nhieu|may)\s+(?:ky|lan|khoan)", text):
        return plan(f"SELECT COUNT(*) FROM ({overdue})", reason="Installment periods due before the asked day and not fully paid; due today is not overdue.")
    return None


class UnreadableSource(ValueError):
    pass


DEBT = re.compile(r"con no|cong no|tra no|(?<!doanh )thu no|khach no|mua chiu|ghi no|tra gop|(?<!lai )(?<!loi )\bgop\b|qua han")


def compile_domain(frame, question, catalog, engine):
    text = fold(question.question)
    text = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%]+", " ", text)).strip()
    unread = (catalog.get("semantic", {}).get("domain_retail") or {}).get("installments_unread")
    if unread and DEBT.search(text):
        # Rulebook: debt includes installment orders even when kept in a separate book; an unread book means unknown payments.
        raise UnreadableSource(f"installment book {unread} present but its due dates/amounts were not recognised")
    raw = unicodedata.normalize("NFC", question.question).lower()
    has_sales = "v_sales" in engine.tables or "v_orders" in engine.tables
    for compiler in (lambda: compile_repair(question, text, raw, engine, has_sales, catalog),
                     lambda: compile_installments(question, text, engine),
                     lambda: compile_retail_domain(frame, question, text, raw, engine, catalog)):
        result = compiler()
        if result is not None:
            return result
    return None
