"""Typed question frames compiled to deterministic, auditable SQL."""
from __future__ import annotations

import contextvars
import datetime as dt
from decimal import Decimal
import difflib
import os
import re
import unicodedata

from .io import dumps
from .intent import channels_in,ground_frame,normalize_question,payments_in,question_concepts,telex_strip,wants_percent
from .parsing import channel, fold, payment_method
from .temporal import Period, resolve_periods
from .types import QueryFrame, SQLPlan
from .specialty import compile_specialty
from .domains import compile_domain


class UnsupportedFrame(ValueError):
    pass


FRAME_SYSTEM = """Phân tích câu hỏi thành QueryFrame theo schema; không viết SQL, không tính số, không tạo đáp án. Chuỗi filter rỗng nếu không có. Chỉ copy tên/nhóm trong câu, không thêm filter khách lẻ/trạng thái.
metric: revenue=tiền bán/doanh thu; orders=số đơn hoặc số lần mua (khác dòng hàng); quantity=bán bao nhiêu đơn vị cơ bản; gross_profit=lãi gộp; cancelled_orders=số đơn huỷ; cancel_rate=tỉ lệ huỷ; purchase_days=số ngày có mua/khách trung thành; product_order_count=số đơn có mặt hàng/hàng được ưa chuộng; other=công nợ,tồn,nhập hàng,giá bán,giá nhập,thuế,trả góp,giao hàng,lô,hạn dùng,lãi ròng,bảo hành,sửa xe,hoa hồng,voucher,điểm hoặc nghiệp vụ chưa biểu diễn.
operation: value=tổng/số đơn; average_order=giá trị bình quân 1 đơn; daily_average=trung bình mỗi ngày/thứ; top=đối tượng nhiều nhất/top k/ngày hoặc giờ cao điểm; share=tỉ trọng của filter trong toàn shop; growth=thay đổi phần trăm so kỳ trước; difference=chênh số tuyệt đối; series=danh sách giá trị các tháng theo thứ tự; trend=có tăng liên tục không; threshold_count=bao nhiêu khách/mặt hàng có số đo đạt ngưỡng; threshold_share=tỉ lệ đối tượng đạt ngưỡng; other=khi có nhiều điều kiện không biểu diễn rõ.
group_by chỉ định đối tượng: customer khi đếm/xếp khách; product khi xếp hàng; day/ngày hoặc month/tháng có số đo cao nhất; hour/giờ cao điểm. value/average_order bình thường group_by=none. Bán chạy/hot/bán nhiều nhất theo quantity; mua nhiều TIỀN theo revenue. Nhân viên bán giỏi theo revenue. Khách sộp=average_order, group_by=customer (ít nhất3đơn do compiler xử lý).
product_filter=tên mặt hàng, customer_filter=tên khách, group_filter=nhóm HÀNG, customer_group_filter=nhóm KHÁCH. unit_filter chỉ dùng khi hỏi tổng bao nhiêu ly/chai/viên/...; size_filter M hoặc L cho cà phê. payment_filter là hình thức ĐƠN (cash/transfer/ewallet/cod/debt). k=1 nếu không nói số lượng top. weekday_filter=1 thứ Hai,..7 Chủ nhật, 0 không lọc. threshold=0 nếu không có ngưỡng, operator >= mặc định, câu nói 'hơn/quá/trên' dùng >.
employee_filter là mã NV hoặc họ tên nhân viên cụ thể được hỏi. Không bỏ điều kiện nhân viên. T7/2026, th7/26 là THÁNG 7, không phải thứ trong tuần. Chỉ đặt weekday_filter khi câu có 'thứ ...' hoặc 'Chủ nhật'. 'Bữa ni', 'hôm ni', 'bữa nay' là hôm nay theo as_of_date, không phải bữa ăn.
output_type: hỏi một số là number, phần trăm percent, một tên string, một ngày/tháng date, nhiều tên/số list, hỏi tên kèm số đo table, có/không boolean. Giá trị trung bình 1 đơn chỉ là number, không tự tạo bảng có tháng. Câu chỉ hỏi tên hàng nhiều nhất là string, top3 tên là list. reason ngắn tối đa một câu. Đọc đúng câu, giữ lỗi gõ/thiếu dấu thành ý nghĩa, không suy thêm so sánh nếu không được hỏi."""


FRAME_SYSTEM_EN = """Parse the (Vietnamese) question into a QueryFrame following the schema. Do not write SQL, do not compute numbers, do not produce an answer. Filter strings are empty when absent. Copy names/groups exactly as written in the question (do not translate them); do not add walk-in-customer or status filters.
metric: revenue=sales money/"doanh thu"; orders=number of orders or number of purchases (not order lines); quantity=how many base units were sold; gross_profit=gross profit ("lãi gộp"); cancelled_orders=number of cancelled orders; cancel_rate=cancellation rate; purchase_days=number of days with a purchase / loyal customers; product_order_count=number of orders containing an item / popular items; other=debts ("công nợ"), stock ("tồn"), purchasing/restocking ("nhập hàng"), selling price, purchase price, tax, installments ("trả góp"), deliveries ("giao hàng"), batches ("lô"), expiry ("hạn dùng"), net profit ("lãi ròng"), warranty ("bảo hành"), vehicle repair ("sửa xe"), commission ("hoa hồng"/"huê hồng"), vouchers, points, or any business concept the schema cannot express.
operation: value=total/number of orders; average_order=average value of one order; daily_average=average per day/weekday; top=the single most/top k/peak day or hour; share=share of the filter within the whole shop; growth=percentage change versus the previous period; difference=absolute difference; series=list of monthly values in order; trend=whether it rises continuously; threshold_count=how many customers/items reach a threshold; threshold_share=proportion of objects reaching a threshold; other=when there are several conditions that cannot be expressed clearly.
group_by names the object: customer when counting/ranking customers; product when ranking items; day or month when asking which day/month has the highest value; hour for peak hour. A plain value/average_order uses group_by=none. Best-selling/hot/sells the most is ranked by quantity; spends the most MONEY is ranked by revenue. A good salesperson is ranked by revenue. A big-spending customer ("khách sộp") = average_order, group_by=customer (the compiler handles the minimum of 3 orders).
product_filter=item name, customer_filter=customer name, group_filter=product GROUP, customer_group_filter=customer GROUP. Use unit_filter only when asking for a total in cups/bottles/tablets/...; size_filter is M or L for coffee. payment_filter is the ORDER's payment form (cash/transfer/ewallet/cod/debt). k=1 when no top-N is stated. weekday_filter=1 for Monday ... 7 for Sunday, 0 for no filter. threshold=0 when there is no threshold, operator defaults to >=, wording like "hơn/quá/trên" (more than/over/above) uses >.
employee_filter is the specific employee ID or full name being asked about. Never drop an employee condition. "T7/2026" and "th7/26" mean MONTH 7, not a weekday. Set weekday_filter only when the question says "thứ ..." or "Chủ nhật". "Bữa ni", "hôm ni", "bữa nay" mean today relative to as_of_date, not a meal.
output_type: asking for one number is number, a percentage is percent, one name is string, a date/month is date, several names/numbers is list, a name together with a measure is table, yes/no is boolean. An average value of one order is only a number; do not invent a table with months. A question asking only for the top item name is string, top 3 names is list. reason is one short sentence, at most one. Read the question exactly: treat typos and missing diacritics as the intended meaning, and do not add comparisons that were not asked for."""

FRAME_PROMPTS = {"vi": FRAME_SYSTEM, "en": FRAME_SYSTEM_EN}


def frame_prompt_language():
    """Which instruction language the frame call uses; the question and the data stay Vietnamese either way."""
    value = os.environ.get("DATAMATE_FRAME_PROMPT", "vi").strip().lower()
    return value if value in FRAME_PROMPTS else "vi"


def frame_messages(question, catalog):
    capabilities = catalog.get("semantic", {}).get("capabilities", {})
    return [{"role": "system", "content": FRAME_PROMPTS[frame_prompt_language()]},
            {"role": "user", "content": dumps({"question": question.question, "normalized_question":normalize_question(question.question), "as_of_date": question.as_of_date.isoformat(), "capabilities": capabilities})}]


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def search_key(value):
    return re.sub(r"[^a-z0-9]+", " ", fold(value)).strip()


def match_names(engine, column, query):
    """Data names a product/customer filter refers to: exact, catalog alias, variant suffix, prefix, substring, all words, then without chat fillers."""
    view = "v_sales" if column == "product_name" else "v_orders"
    _, names = engine.lookup(f"SELECT DISTINCT {column} FROM {view} WHERE {column} IS NOT NULL")
    target = search_key(query)
    exact = [row[0] for row in names if search_key(row[0]) == target]
    if not exact and column == "product_name" and "v_products" in engine.tables:
        # Catalog 'tên gọi khác' / 'ghi tắt': several names a shop uses for one item, comma-separated.
        _, aliases = engine.lookup("SELECT product_name,product_alias FROM v_products WHERE product_alias IS NOT NULL AND product_name IS NOT NULL")
        sold = {search_key(row[0]): row[0] for row in names}
        exact = list(dict.fromkeys(sold[search_key(name)] for name, alias in aliases if search_key(name) in sold
                                   and any(search_key(item) == target for item in re.split(r"[,;/|]", str(alias)) if item.strip())))
    # 'Latte' means 'Latte (M)/(L)', not 'Matcha latte': prefer variant-suffix, then prefix, then substring matches.
    variant = [row[0] for row in names if search_key(re.sub(r"\s*\([^)]*\)\s*$", "", str(row[0]))) == target]
    prefix = [row[0] for row in names if search_key(row[0]).startswith(target + " ")]
    # A substring match keeps word boundaries: 'sỉ' (wholesale) is not 'Sinh tố' or 'Espresso (single)'.
    bounded = re.compile(r"(?<![a-z0-9])" + re.escape(target) + r"(?![a-z0-9])") if target else None
    matches = exact or variant or prefix or ([row[0] for row in names if bounded.search(search_key(row[0]))] if bounded else [])
    if not matches:
        words = set(target.split())
        matches = [row[0] for row in names if words and words <= set(search_key(row[0]).split())]
    if not matches:
        # Retry without chat fillers and leading unit/verb words ('đĩa rau củ', 'cơm chiên z').
        trimmed = re.sub(r"(?:\s+(?:z|ta|ha|nhi|vay|a|nha|nhe|the|do|roi|ah|hen|ne))+$", "", target)
        trimmed = re.sub(r"^(?:(?:ban|tien|so|dia|ly|chai|cai|phan|mon|mat hang|hang|san pham|lon|goi|hop|to|noi|ve|ke tu khi|tu khi|tu hoi|sau khi|"
                         r"coi gium|check gium|xem giup|xem gium|cho hoi|cho minh hoi)\s+)+", "", trimmed).strip()
        if trimmed and trimmed != target:
            matches = [row[0] for row in names if search_key(row[0]) == trimmed] or [row[0] for row in names if trimmed in search_key(row[0])]
    if not matches:
        # A full name followed by the question's own words ('Gaviscon hộp 24 gói x 10ml nhập', 'nghêu hấp sả thu về bn tiền'):
        # the longest name of two or more words the value starts with, when the words after it are not item words.
        vocabulary = {word for row in names for word in search_key(row[0]).split()}
        heads = [row[0] for row in names if len(search_key(row[0]).split()) >= 2 and target.startswith(search_key(row[0]) + " ")]
        if heads:
            longest = max(len(search_key(name)) for name in heads)
            heads = [name for name in heads if len(search_key(name)) == longest]
            rest = target[longest:].split()
            if all(word not in vocabulary or word in TRAILING_WORDS for word in rest):
                matches = heads
    if not matches and column == "product_name" and len(target) >= 8 and CLOSE_NAMES.get():
        matches = close_name(target, [row[0] for row in names])
    return matches


# Last resort only: set by the runtime after the planner's frame and its rewrite both failed on an unresolved name.
CLOSE_NAMES = contextvars.ContextVar("close_names", default=False)


# Friendly superlatives need rating data; ordinary customer-group typos remain resolvable.
# Do not treat 'lịch sự' blindly: it folds like 'lịch sử' (history).
SUBJECTIVE = re.compile(r"lanh nghe|tay nghe|tan tam|le phep|chu dao|nhiet tinh|de thuong|vui ve|(?:duoc|hay|bi) (?:khach )?(?:khen|che|phan nan)|khen ngoi|danh gia|hai long|quy men|yeu quy|uy tin|dang tin cay|trung thuc|than thien nhat|ngon nhat|chat luong.*tot nhat|kho tinh nhat|ba bau.*(?:dung|uong)"
                        r"|nen uong|lieu dung|lieu luong|uong (?:moi lan|bao nhieu vien|may vien)|cach dung|cong dung|tac dung phu|chong chi dinh")
DISTINCT_ITEMS = re.compile(r"(?:bao nhieu|\bmay|\bso|dem)\s+(?:mat hang|loai hang|san pham|mon|\bsp)\s+khac nhau|(?:bao nhieu|\bmay)\s+loai\s+(?:mat hang|san pham|hang|mon)\b")
# 'bao nhiêu khách (đã) mua', 'số khách hàng mua X': a count of customers, not of orders or units.
CUSTOMER_COUNT = re.compile(r"(?:bao nhieu|\bmay|so luong|tong so|tong cong|\bso)\s+(?:nguoi\s+)?khach(?:\s+hang)?\b(?!\s+(?:no|nao|an|cua|le|vang lai)\b)")
NOT_CUSTOMER_COUNT = re.compile(r"luot khach|so khach (?:cua|tren|moi|trung binh)|khach an|\bngoi\b|\bban (?:so|nao)\b|\bno\b|con no|ghi no|thanh vien|dang ky|khach (?:hang )?moi|lan dau|quay lai"
                                r"|tro len|tro xuong|it nhat|toi thieu|\btu \d+|tren \d+|duoi \d+|hon \d+|qua \d+|phan tram|%|(?:ti|ty) (?:le|trong)|chiem|trung binh|binh quan|moi khach")


def item_words_removed(text, engine):
    """The question without the item names it contains ('Sữa tắm … thân thiện với da' is a name, not an opinion)."""
    if "v_sales" not in engine.tables:
        return text
    _, rows = engine.lookup("SELECT DISTINCT product_name FROM v_sales WHERE product_name IS NOT NULL")
    for name in sorted({re.sub(r"\s+", " ", fold(r[0])).strip() for r in rows}, key=len, reverse=True):
        if len(name) >= 5 and name in text:
            text = text.replace(name, " ")
    return text


def contiguous(periods):
    """Periods that follow each other day after day (a run of months)."""
    return all(later.start == earlier.end + dt.timedelta(days=1) for earlier, later in zip(periods, periods[1:]))


def seating_area(engine, question_text):
    """The seating area named in the question ('khu phòng lạnh', 'ngồi ngoài sân'), as written in the data."""
    if "v_dining" not in engine.tables:
        return None
    _, rows = engine.lookup("SELECT DISTINCT area FROM v_dining WHERE area IS NOT NULL")
    raw = unicodedata.normalize("NFC", question_text).lower()
    named = [r[0] for r in rows if len(r[0].strip()) >= 3 and re.search(r"(?<!\w)" + re.escape(unicodedata.normalize("NFC", r[0]).lower().strip()) + r"(?!\w)", raw)]
    if len(named) != 1:
        return None
    # A bare place word needs the seating sense ('khu', 'ngồi', 'bàn'): 'trong nhà' alone may be something else.
    if not re.search(r"\bkhu\b|\bngồi\b|\bbàn\b|khu vực", raw) and len(named[0].split()) < 2:
        return None
    return named[0]


def cafe_sizes(engine):
    """The shop names drinks by size the café way ('Latte (M)', 'Trà đào (L)')."""
    if "v_sales" not in engine.tables:
        return False
    _, rows = engine.lookup("SELECT COUNT(*) FROM (SELECT DISTINCT product_name FROM v_sales WHERE regexp_matches(product_name,'(?i)\\((?:m|l)\\)\\s*$'))")
    return bool(rows[0][0])


def close_name(target, names):
    """One item name written a little differently ('similac so 2 lon 400g' for 'Sữa Similac số 2 lon 400g'): the single
    clearly closest name, when every number of the value is in that name (sizes and pack counts must agree)."""
    numbers = set(re.findall(r"\d+", target))
    agreeing = [name for name in names if numbers <= set(re.findall(r"\d+", search_key(name)))]
    scored = sorted(((difflib.SequenceMatcher(None, target, search_key(name)).ratio(), name) for name in agreeing), reverse=True)
    if not scored:
        return []
    best, name = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if best >= 0.85 and best - second >= 0.05:
        return [name]
    return []


# Words of the question that the model sometimes copies after a name: verbs, amounts asked, fillers.
TRAILING_WORDS = {"nhap", "ban", "thu", "ve", "duoc", "dc", "bn", "bao", "nhieu", "tien", "len", "xuong", "gia", "doi", "tang", "giam", "mua", "ra", "hang", "khong", "ko"}


# Where each entity slot's names live. A view a shop lacks gives an empty pool.
NAME_POOLS = (("product_filter", "SELECT DISTINCT product_name FROM v_sales WHERE product_name IS NOT NULL"),
              ("group_filter", "SELECT DISTINCT product_group FROM v_sales WHERE product_group IS NOT NULL"),
              ("customer_filter", "SELECT DISTINCT customer_name FROM v_orders WHERE customer_name IS NOT NULL"),
              ("customer_group_filter", "SELECT DISTINCT customer_group FROM v_orders WHERE customer_group IS NOT NULL"),
              ("employee_filter", "SELECT DISTINCT employee_name FROM v_employees WHERE employee_name IS NOT NULL"))
GROUP_PREFIX = r"^(?:nhom hang|nhom khach|nhom|hang)\s+"
ROLE_WORDS = r"^(?:(?:nhom|khach|hang|loai|nhan vien|nv|mat hang|san pham|mon|hang hoa|do uong|thuc uong|cac|moi|tat ca)\s+)+"
PHONE_WORDS = r"\b(?:dien thoai|dt|smartphone)\b"


def name_pools(engine):
    pools = {}
    for slot, sql in NAME_POOLS:
        try:
            _, rows = engine.lookup(sql)
        except Exception:
            rows = []
        pools[slot] = {search_key(row[0]): row[0] for row in rows if search_key(row[0])}
    extra = set()
    for sql in ("SELECT product_name, product_alias FROM v_products", "SELECT DISTINCT base_unit, NULL FROM v_sales"):
        try:
            _, rows = engine.lookup(sql)
        except Exception:
            rows = []
        extra |= {search_key(item) for row in rows for cell in row if cell for item in re.split(r"[,;/|]", str(cell)) if search_key(item)}
    return pools, extra


def resolves(slot, value, engine, pools):
    """The filter value names something in its own slot (the same matching the compiler applies later)."""
    key = search_key(value)
    if slot == "product_filter":
        if match_names(engine, "product_name", value):
            return True
        try:
            units = engine.lookup("SELECT DISTINCT base_unit FROM v_sales WHERE base_unit IS NOT NULL")[1]
        except Exception:
            units = []
        return key in {search_key(row[0]) for row in units}
    if slot == "customer_filter":
        stripped = re.sub(r"^(?:khach\s+)?(?:hang\s+)?(?:thuoc\s+)?(?:nhom|hang)\s+(?:khach\s+)?", "", key)
        return bool(match_names(engine, "customer_name", value)) or bool({key, stripped} & set(pools["customer_group_filter"]))
    if slot == "group_filter":
        return bool({key, re.sub(GROUP_PREFIX, "", key)} & (set(pools["group_filter"]) | set(pools["customer_group_filter"])))
    if slot == "customer_group_filter":
        return bool({key, re.sub(GROUP_PREFIX, "", key)} & set(pools["customer_group_filter"]))
    return True


# Words of the question itself, never a name on their own (Telex keystrokes removed): particles, time and question words.
CHAT_WORDS = {"ta", "nhi", "nha", "ha", "z", "vay", "a", "e", "em", "anh", "chi", "oi", "ti", "ty", "bn", "bnhiu", "tien", "dong", "gop", "chiem", "tu", "den",
              "toi", "quy", "thang", "nam", "ngay", "tuan", "nay", "qua", "roi", "cua", "cho", "minh", "voi", "va", "duoc", "dc", "bao", "nhieu", "la", "gi",
              "nao", "the", "khong", "ko", "k", "co", "trong", "tong", "cong", "doanh", "thu", "dthu", "hom", "bua"}


def foreign(key, vocabulary, names):
    """No word of the value occurs in this shop's names, and it is not a near spelling of one ('chnhang', 'dewc', 'tuwf 12')."""
    tokens = [token for token in key.split() if not token.isdigit()]
    if ({token for token in tokens} | {telex_strip(token) for token in tokens}) & vocabulary:
        return False
    squashed = key.replace(" ", "")
    if any(difflib.SequenceMatcher(None, squashed, name.replace(" ", "")).ratio() >= 0.7 for name in names):
        return False
    long_words = [word for word in vocabulary if len(word) >= 4]
    return not any(len(token) >= 4 and any(difflib.SequenceMatcher(None, token, word).ratio() >= 0.8 for word in long_words) for token in tokens)


def reslot_filters(frame, text, engine):
    """Rulebook §8: names that denote one thing are that thing, whichever slot the model put them in.

    A value that is not a name of its slot but is exactly a name of one other slot moves there
    ('ho cảm cúm' is a product group, 'nhóm sỉ' a customer group, a staff name an employee).
    A value sharing no word with any name in this shop, in a question that names nothing of the
    shop, is chat text taken for a name ('chnhang' = cửa hàng, 'dewc' = được) and is dropped.
    Returns the slots changed, for the trace.
    """
    pools, extra = name_pools(engine)
    changed = []
    slots = ("product_filter", "group_filter", "customer_filter", "customer_group_filter")
    for slot in slots:
        value = getattr(frame, slot)
        tokens = [telex_strip(token) for token in search_key(value).split()] if value else []
        if tokens and all(token in CHAT_WORDS for token in tokens) and search_key(value) not in pools[slot]:
            # Question words taken for a name ('tuwf' = từ, 'quys' = quý, 'tiền nhỉ', 'đóng góp', 'e tỉ'): not a filter.
            setattr(frame, slot, "")
            changed.append(f"{slot}:chat")
    # One name split over two slots ('bán mì hảo hảo' + 'tôm chua cay', 'thực phẩm' + 'chức năng'): the whole name.
    for first in slots:
        for second in slots:
            a, b = getattr(frame, first), getattr(frame, second)
            if first == second or not a or not b:
                continue
            trimmed = re.sub(r"^(?:(?:ban|mua|nhom|hang|mon|san pham|mat hang)\s+)+", "", search_key(a))
            for target in ("product_filter", "group_filter"):
                whole = pools[target].get(trimmed + " " + search_key(b))
                if whole and not resolves(first, a, engine, pools) and search_key(a) not in pools[first]:
                    setattr(frame, first, ""); setattr(frame, second, "")
                    setattr(frame, target, whole)
                    changed.append(f"{first}+{second}->{target}")
                    break
    for slot in slots:
        value = getattr(frame, slot)
        if not value or ("=" not in value and re.search(r"[,;/&+]|\b(?:va|hoac|voi)\b", fold(value))):
            continue  # several things named: a comparison, not one mistaken name
        key = search_key(value)
        # The same words in two slots ('sỉ' as item and as customer group): keep the slot where they are a name exactly.
        twin = next((other for other in slots if other != slot and search_key(getattr(frame, other)) == key and key in pools[other]), None)
        if twin and key not in pools[slot]:
            setattr(frame, slot, "")
            changed.append(f"{slot}:duplicate")
            continue
        if resolves(slot, value, engine, pools):
            continue
        if "=" in value or not re.sub(ROLE_WORDS, "", " ".join(word for word in key.split() if not word.isdigit()) + " ").strip():
            # Only a role word ('nhóm', 'nhóm hàng'): 'nhóm sữa' split as group 'nhóm' + item 'sữa' names the group 'sữa'.
            setattr(frame, slot, "")
            partner = next((other for other in slots if other != slot and search_key(getattr(frame, other)) in pools[slot]), None)
            if partner:
                setattr(frame, slot, pools[slot][search_key(getattr(frame, partner))])
                setattr(frame, partner, "")
                changed.append(f"{partner}->{slot}")
            else:
                changed.append(f"{slot}:dropped")
            continue
        forms = {key, re.sub(ROLE_WORDS, "", key), re.sub(GROUP_PREFIX, "", key)} - {""}
        if re.search(PHONE_WORDS, key) and any(name_key.split()[0] == "may" for name_key in pools["group_filter"]):
            # Phone shops name phones 'máy' (máy mới, máy cũ): 'điện thoại cũ', 'đt' are those groups.
            phone = " ".join(dict.fromkeys(re.sub(PHONE_WORDS, "may", key).split()))
            forms |= {phone, re.sub(ROLE_WORDS, "", phone)} - {""}
            if slot == "group_filter" and phone in pools[slot]:
                frame.group_filter = pools[slot][phone]
                changed.append("group_filter:phone")
                continue
        heads = [name for name_key, name in pools["group_filter"].items() if any(name_key.startswith(form + " ") for form in forms)]
        if slot in ("product_filter", "group_filter") and len(heads) >= 2 and not any(form in pools["group_filter"] for form in forms) \
                and not (slot == "product_filter" and frame.group_filter):
            # The first word shared by several groups names all of them ('máy' = máy mới + máy cũ) when no item is so named.
            head = min((form for form in forms if all(search_key(name).startswith(form + " ") for name in heads)), key=len, default=None)
            if head:
                setattr(frame, slot, "")
                frame.group_filter = head
                changed.append(f"{slot}->group_filter:head")
                continue
        if slot in ("group_filter", "customer_group_filter"):
            # A group named by its first words ('thực phẩm' for 'thực phẩm chức năng'), when only one group starts so.
            starts = [name for name_key, name in pools[slot].items() if any(name_key.startswith(form + " ") for form in forms)]
            if len(starts) == 1:
                setattr(frame, slot, starts[0])
                changed.append(f"{slot}:prefix")
                continue
        homes = {(other, pools[other][form]) for other in pools if other != slot for form in forms if form in pools[other]}
        if not homes and pools["employee_filter"]:
            staff = [name for name_key, name in pools["employee_filter"].items() if any(name_key.endswith(" " + form) or name_key == form for form in forms)]
            homes = {("employee_filter", staff[0])} if len(staff) == 1 else set()
        if len({other for other, _ in homes}) == 1 and len(homes) == 1:
            other, name = next(iter(homes))
            if not getattr(frame, other):
                setattr(frame, slot, "")
                setattr(frame, other, name)
                changed.append(f"{slot}->{other}")
            elif search_key(name) in {search_key(getattr(frame, other)), re.sub(ROLE_WORDS, "", search_key(getattr(frame, other)) + " ").strip()}:
                # 'khách thân thiết' in both the customer and the customer-group slots: it is the group.
                setattr(frame, slot, "")
                changed.append(f"{slot}:duplicate")
            continue
        names = {name for pool in pools.values() for name in pool} | extra
        vocabulary = {word for name in names for word in name.split() if not word.isdigit()}
        question = " " + text + " "
        mentioned = any(len(name) >= 4 and " " + name + " " in question for name in names)
        store = len(key.split()) <= 2 and difflib.SequenceMatcher(None, key.replace(" ", ""), "cuahang").ratio() >= 0.6
        if store and slot=="product_filter":
            product_words={word for name in pools["product_filter"] for word in name.split()}
            meaningful=(set(key.split()) & product_words)-{"cua","hang","quan","tiem","shop","mon","san","pham"}
            if meaningful:
                store=False  # keep a plausible item name for resolution/repair; not a fuzzy "cửa hàng" filler
        explicit_customer_group = slot=="customer_group_filter" and re.search(r"\bnhom khach\b|\bkhach (?:hang )?(?:thuoc )?nhom\b",text) and any(
            re.search(r"(?<![a-z0-9])"+re.escape(form)+r"(?![a-z0-9])",text) for form in forms)
        if not mentioned and not explicit_customer_group and (store or foreign(key, vocabulary, names)):
            setattr(frame, slot, "")
            changed.append(f"{slot}:dropped")
    # 'nhóm hàng X' written in the question, X a product group of the data: that is the group filter,
    # also when the model left the slot empty or put X in the item slot.
    named = [group for group in pools["group_filter"] if re.search(r"\bnhom (?:hang |mat hang |san pham )?" + re.escape(group) + r"\b", text)]
    if named and not frame.group_filter:
        group = max(named, key=len)
        frame.group_filter = pools["group_filter"][group]
        if search_key(frame.product_filter) == group:
            frame.product_filter = ""
        changed.append("group_filter:named")
    return changed


def price_increase_anchor(frame, text, engine):
    """'Từ khi X lên giá 3.200đ' with no date: the first day X's price rose to that amount (rulebook 'Hiệu ứng tăng giá':
    a rise is a day whose new price is above the previous one; several rises to the same price count from the first)."""
    if "v_price_changes" not in engine.tables or not re.search(r"\blen gia\b|\btang gia\b|\bgia len\b|\bdoi gia\b.*\blen\b|\blen \d", text):
        return None
    if re.search(r"\d{1,2}[/.-]\d{1,2}[/.-]20\d{2}", text):
        return None
    number = r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)(?:\s*(k|nghin|ngan|tr|trieu)\b)?"
    amount = re.search(r"\blen\s+(?:gia\s+)?(?:la\s+|thanh\s+)?" + number, text) or re.search(r"\b(?:tang|len) gia\s+(?:la\s+|thanh\s+)?" + number, text)
    if not amount:
        return None
    digits = amount[1].rstrip(".,")
    value = float(re.sub(r"[.,]", "", digits)) if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", digits) else float(digits.replace(",", "."))
    value *= {"k": 1000, "nghin": 1000, "ngan": 1000, "tr": 1e6, "trieu": 1e6}.get(amount[2] or "", 1)
    _, rows = engine.lookup("SELECT product_id, product_name, effective_date, new_price, old_price FROM v_price_changes")
    names = {search_key(name) for name in match_names(engine, "product_name", frame.product_filter)} if frame.product_filter else set()
    if not names:
        question = " " + text + " "
        mentioned = sorted({search_key(row[1]) for row in rows if row[1] and " " + search_key(row[1]) + " " in question}, key=len)
        names = {mentioned[-1]} if mentioned else set()
    if not names:
        return None
    codes = set()
    if "v_sales" in engine.tables:
        _, sold = engine.lookup("SELECT DISTINCT product_id, product_name FROM v_sales WHERE product_id IS NOT NULL")
        codes = {str(code) for code, name in sold if search_key(name) in names}
    days = [row[2] for row in rows if (search_key(row[1] or "") in names or (row[0] is not None and str(row[0]) in codes))
            and row[3] is not None and row[4] is not None and float(row[3]) == value and row[3] > row[4]]
    return min(days) if days else None


# 'nhóm khách có từ 4 đơn trở lên' names customers by what they did (a count threshold), not by a stored customer group.
# The group word must be followed directly by the behaviour ('nhóm khách có từ 4 đơn'), so a named group
# ('nhóm khách sỉ có từ 4 đơn') still counts as a customer-group question.
BEHAVIOUR_SEGMENT = re.compile(r"\bnhom khach (?:hang )?(?:co|mua|dat|tu|tren|tro len)\b[^?.]{0,25}?\b\d+\s*(?:don|lan|ngay|bill)\b")


def compile_frame(frame: QueryFrame, question, catalog, engine) -> SQLPlan:
    question=question.model_copy(update={'question':normalize_question(question.question)})
    frame = ground_frame(frame,question.question)
    text = fold(question.question)
    if CUSTOMER_COUNT.search(text) and not NOT_CUSTOMER_COUNT.search(text) and frame.operation in {"value", "other"} and not frame.threshold \
            and frame.metric in {"orders", "quantity", "purchase_days", "revenue", "other"} and not frame.customer_filter:
        # §1: customers who bought in the period = identified customers with at least one valid order there (§3 identity).
        frame = frame.model_copy(update={"metric": "orders", "operation": "threshold_count", "group_by": "customer", "threshold": 1, "threshold_operator": ">=", "output_type": "number"})
    if frame.operation in {"value", "average_order", "daily_average", "growth", "difference", "share", "series", "trend"} and frame.output_type in {"number", "percent", "boolean", "list"} and not (frame.operation=='average_order' and frame.group_by=='customer' and 'sop' in text):
        frame.group_by = "none"
    if not frame.product_filter and not frame.group_filter and "v_sales" in engine.tables:
        question_key=search_key(question.question)
        _, source_names=engine.lookup("SELECT DISTINCT product_name FROM v_sales WHERE product_name IS NOT NULL")
        candidates=[name for (name,) in source_names if len(search_key(name).split())>=2 and
                    re.search(r"(?<![a-z0-9])"+re.escape(search_key(name))+r"(?![a-z0-9])",question_key)]
        if candidates:
            longest=max(len(search_key(name)) for name in candidates)
            matches={search_key(name):name for name in candidates if len(search_key(name))==longest}
            if len(matches)==1:frame.product_filter=next(iter(matches.values()))
    if not frame.customer_group_filter and re.search(r"nhom khach|khach (?:hang )?(?:nhom |si\b|than thiet)",text) and "v_orders" in engine.tables:
        _, source_groups=engine.lookup("SELECT DISTINCT customer_group FROM v_orders WHERE customer_group IS NOT NULL")
        matches=[g for (g,) in source_groups if search_key(g) and
                 re.search(r"(?<![a-z0-9])"+re.escape(re.sub(r"^(?:khach hang|khach|nhom) ","",search_key(g)))+r"(?![a-z0-9])",text)]
        if len(matches)==1:
            frame.customer_group_filter=matches[0]
            frame=ground_frame(frame,question.question)
    if frame.metric != "quantity":
        frame.unit_filter = ""
    elif frame.unit_filter and not re.search(r"(?<!\w)"+re.escape(fold(frame.unit_filter))+r"(?!\w)", text):
        frame.unit_filter = ""
    if frame.metric == "quantity" and not frame.unit_filter and "v_sales" in engine.tables:
        # 'bao nhiêu gói', 'tổng số gói đã bán': the counted unit is part of the question even when the planner left it out;
        # without it the sum would mix every unit the shop sells. Only units the shop really records are taken.
        counted = [m[1] for m in re.finditer(r"(?:bao nhieu|\bmay\b|\bbn\b|tong so|\bso)\s+([a-z]+)\b(?!\s+tram)", text)]   # not 'bao nhiêu phần trăm'
        if counted:
            _, unit_rows = engine.lookup("SELECT DISTINCT norm(base_unit) FROM v_sales WHERE base_unit IS NOT NULL")
            known = {row[0] for row in unit_rows if row[0]}
            if len(known) > 1:
                matches = [word for word in counted if word in known]
                if len(set(matches)) == 1:
                    frame.unit_filter = matches[0]
    if search_key(frame.product_filter) in {"mat hang", "hang", "san pham", "mon", "ngay", "cua hang", "giup", "giumpf", "goi hang", "mon hang", "hang hoa"}:
        frame.product_filter = ""
    if frame.output_type == "percent" and frame.operation == "difference":
        frame.operation = "growth"
    if frame.output_type == "percent" and re.search(r"(?:ti le|ty le|phan tram|%).*\bhuy\b|\bhuy\b.*(?:ti le|ty le|phan tram|%)", text):
        frame.metric, frame.operation = "cancel_rate", "value"
    if frame.metric == "cancel_rate":
        frame.operation, frame.group_by, frame.output_type = "value", "none", "percent"
    if frame.operation == "threshold_count" and frame.group_by == "none" and re.search(r"\bdon\b|\bbill\b", text):
        frame.metric = "orders"
    if frame.operation == "trend":
        frame.output_type = "boolean"
    if re.search(r'gia|len \d',text) and re.search(r'\d+ ngay (?:ke tu|tu ngay|tu hom|tu bua|dau)',text) and re.search(r'phan tram|%',text):
        frame.metric,frame.operation,frame.output_type='quantity','growth','percent'
    if re.search(r"nhom", text) and frame.product_filter and not frame.group_filter:
        if catalog.get("semantic", {}).get("capabilities", {}).get("sales"):
            _, group_rows = engine.lookup("SELECT DISTINCT product_group FROM v_sales WHERE product_group IS NOT NULL")
            target = search_key(frame.product_filter)
            matches = [row[0] for row in group_rows if search_key(row[0]) and search_key(row[0]) in target]
            # 'trà sữa' contains group 'trà' too: prefer the exact, then the longest group name.
            exact = [name for name in matches if search_key(name) == target]
            if exact:
                matches = exact[:1]
            elif matches:
                longest = max(len(search_key(name)) for name in matches)
                matches = [name for name in matches if len(search_key(name)) == longest]
            if len(matches) == 1:
                frame.group_filter, frame.product_filter = matches[0], ""
    if re.fullmatch(r"\d{1,2}[/.-]\d{2,4}", frame.product_filter.strip()):
        frame.product_filter = ""
    if frame.metric == "quantity":
        size = re.search(r"size\s*([ml])\b", text)
        if size and cafe_sizes(engine):
            frame.size_filter = size[1].upper()
        if re.fullmatch(r"(?:ly|chai|lon|cai|phan|vien|goi)(?:\s+size\s*[ml])?", fold(frame.product_filter).strip()):
            frame.product_filter = ""
    if re.search(r"\bca (?:sang|chieu)\b", fold(frame.channel_filter)):
        frame.channel_filter = ""
    count_order_words = re.search(r"\b(?:so+s?|may|bao nhieu|bnhiu|bn)\s+(?:don(?: hang)?|bill|dh|order)\b|\b(?:tong cong|co|duoc)\s+nhieu\s+(?:don|bill|order)\b(?!\s+nhat)", text)
    if count_order_words and not re.search(r"doanh thu|dthu|revenue|tien|gia tri|trung binh|binh quan", text):
        frame.metric = "cancelled_orders" if re.search(r"\bhuy\b", text) else "orders"
    caps = catalog.get("semantic", {}).get("capabilities", {})
    if SUBJECTIVE.search(text) and SUBJECTIVE.search(item_words_removed(text, engine)):
        # §7: qualities, opinions and medical advice are not recorded in any shop file ('lành nghề', 'tận tâm', 'nên uống mấy viên').
        return SQLPlan(action='unanswerable',sql='',answer_type='unanswerable',shape='scalar',rounding='none',date_granularity='day',reason='Dữ liệu không ghi đánh giá, phẩm chất hay hướng dẫn dùng; không suy ra từ số bán.')
    if frame.group_by == "hour" and caps.get("order_time") is False:
        return SQLPlan(action='unanswerable',sql='',answer_type='unanswerable',shape='scalar',rounding='none',date_granularity='day',reason='Nguồn chỉ ghi ngày, không có giờ giao dịch.')
    domain = compile_domain(frame, question, catalog, engine)
    if domain is not None:
        return domain
    if catalog.get('semantic',{}).get('specialty_ready'):
        specialized=compile_specialty(frame,question,catalog,engine)
        if specialized is not None:
            return specialized
    if re.search(r"\bnhom\b",text) and not re.search(r"nhom khach|khach.*nhom",text) and "v_sales" in engine.tables:
        _, groups = engine.lookup("SELECT COUNT(product_group) FROM v_sales")
        if not groups[0][0]:
            return SQLPlan(action='unanswerable',sql='',answer_type='unanswerable',shape='scalar',rounding='none',date_granularity='day',reason='Nguồn không ghi nhóm hàng; không suy nhóm từ tên sản phẩm.')
    if re.search(r'khach (?:nu|nam)|gioi tinh',text):
        gender=[c for t in catalog.get('tables',[]) for c in t['columns'] if re.search(r'gioi_tinh|nam_nu|gender',c['name']) and c.get('nonempty',0)>0]
        if not gender:
            return SQLPlan(action='unanswerable',sql='',answer_type='unanswerable',shape='scalar',rounding='none',date_granularity='day',reason='Nguồn phát hành không có dữ liệu giới tính khách hàng.')
    if re.search(r'khach\s+(?:hang\s+)?(?:(?:o|nha o|song o|tai|ben)\s+)?(?:duong|pho|quan|phuong|xa|huyen)\s+[a-z0-9]|dia chi',text):
        # Address columns only ('phương thức thanh toán' is not a ward).
        address=[c for t in catalog.get('tables',[]) for c in t['columns'] if re.search(r'(?:^|_)(?:dia_chi|address|addr|ten_duong|so_nha|quan_huyen|tinh_thanh|phuong_xa)(?:_|$)|^(?:duong|phuong|quan)$',c['name']) and c.get('nonempty',0)>0]
        if not address:
            return SQLPlan(action='unanswerable',sql='',answer_type='unanswerable',shape='scalar',rounding='none',date_granularity='day',reason='Nguồn phát hành không ghi địa chỉ khách hàng.')
        raise UnsupportedFrame('address condition requires specialist')
    raw_lower=unicodedata.normalize('NFC',question.question).lower()
    # 'trả hàng vì lỗi' (a reason) is matched with its accents: 'trả lại đồ chơi', 'trà đỏ' fold to the same letters.
    if re.search(r'huy\s+(?:vi|do|boi)\b|ly do (?:huy|tra)|nguyen nhan (?:huy|tra)|tai sao.*huy|vi sao.*huy|\bhuy\b.*(?:vi sao|tai sao|ly do gi)',text) or re.search(r'(?<!\w)trả (?:hàng |lại |món )?(?:vì|do) ',raw_lower):
        reasons=[c for t in catalog.get('tables',[]) for c in t['columns'] if re.search(r'ly_do|nguyen_nhan|reason',c['name']) and c.get('nonempty',0)>0]
        if not reasons:
            return SQLPlan(action='unanswerable',sql='',answer_type='unanswerable',shape='scalar',rounding='none',date_granularity='day',reason='Nguồn không ghi lý do huỷ/trả đơn.')
        raise UnsupportedFrame('cancellation reason requires specialist')
    if re.search(r'bao cao (?:tien mat )?(?:theo )?ca\b|\b(?:ban giao|giao|chot|ket|nop) ca\b|tien (?:trong )?ket\b|nop ket\b',text):
        # Rulebook §1: the cash-by-shift report is by collection time and includes debt payments; never derive it from orders.
        raise UnsupportedFrame('cash shift report question')
    if re.search(r'\bdot (?:giao|cho|van chuyen)|chuyen (?:giao|cho) hang|\bdot hang\b',text):
        raise UnsupportedFrame('delivery batches are recorded outside order lines')
    if frame.metric=='revenue' and caps.get('sales') and not re.search(r'tien|doanh thu|gia tri|loi|lai|\bthu ve\b',text):
        words=[match[1] for match in re.finditer(r'(?:bao nhieu|\bmay\b|\bbn\b)\s+([a-z]+)\b(?!\s+tram)',text)]
        if words:
            _,units=engine.lookup("SELECT DISTINCT norm(base_unit) FROM v_sales WHERE base_unit IS NOT NULL")
            if set(words) & {row[0] for row in units if row[0]}:
                frame.metric='quantity'
                if frame.operation not in {'growth','difference','series','top','share'}:
                    frame.operation,frame.group_by,frame.output_type='value','none','number'
    if re.search(r'nhan vien|\bnv\d+\b',text) and frame.group_by!='employee' and not frame.employee_filter:
        if 'v_employees' in engine.tables:
            _,names=engine.lookup('SELECT employee_id,employee_name FROM v_employees')
            matches=[row[0] for row in names if fold(row[1]).strip() and fold(row[1]).strip() in text]
            if len(matches)==1:
                frame.employee_filter=matches[0]
        if not frame.employee_filter:
            raise UnsupportedFrame('Employee restriction could not be resolved; must not return the shop total.')
    stagnant = re.search(r"mat hang.*(?:\be\b|\bees\b|khong ban|ban khong)|\bmon (?:e|ees)\b|(?:khong|chua) ban (?:ra )?(?:duoc|cai nao|lan nao)|ban cham", text)
    if stagnant and caps.get('sales'):
        periods = resolve_periods(question.question, question.as_of_date, engine, exclude=(frame.product_filter, frame.customer_filter))
        if not periods:
            raise UnsupportedFrame('stagnation period not resolved')
        period = periods[0]
        key = "COALESCE(product_id,norm(product_name))"
        query = f"WITH p AS (SELECT {key} k,FIRST(product_name) name FROM v_sales WHERE order_date<TIMESTAMP {literal(period.start)} AND norm(product_name)<>'phi phuc vu' GROUP BY 1),s AS (SELECT DISTINCT {key} k FROM v_sales WHERE order_date>=TIMESTAMP {literal(period.start)} AND order_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))}) "
        count = bool(re.search(r'bao nhieu|may|bnhiu|bao nhiu|tong cong|\bso\b|total', text))
        query += "SELECT COUNT(*) FROM p ANTI JOIN s USING(k)" if count else "SELECT name FROM p ANTI JOIN s USING(k) ORDER BY k"
        return SQLPlan(action='query',sql=query,answer_type='number' if count else 'list',shape='scalar' if count else 'column',rounding='integer' if count else 'none',date_granularity='day',reason='Historically sold items absent from valid orders during requested period.')
    behaviour_segment = BEHAVIOUR_SEGMENT.search(text)
    if behaviour_segment:
        frame.customer_group_filter = ""   # a placeholder the model wrote ('customer_group'), not a recorded group
    if (frame.customer_group_filter or re.search(r"nhom khach|khach (?:hang )?(?:thuoc )?nhom|khach (?:hang )?(?:than thiet|vip)\b", text)) and not behaviour_segment and "v_orders" in engine.tables:
        _, grouped = engine.lookup("SELECT COUNT(customer_group) FROM v_orders")
        if not grouped[0][0]:
            # §7: the shop records no customer groups.
            return SQLPlan(action='unanswerable',sql='',answer_type='unanswerable',shape='scalar',rounding='none',date_granularity='day',reason='Nguồn không ghi nhóm khách hàng.')
    if frame.group_by == 'customer' and not caps.get('customers'):
        return SQLPlan(action='unanswerable',sql='',answer_type='unanswerable',shape='scalar',rounding='none',date_granularity='day',reason='Nguồn bán hàng không ghi mã hoặc tên khách để đếm/xếp khách.')
    if re.search(r"ton kho|trong kho|kho con|het han|gia nhap|tien nhap|phieu nhap|nhap .*binh quan|(?<!ban )\bgia binh quan (?:mot|1|moi)\b|mat hang e\b|khong ban (?:ra )?duoc|ban ko dc|cuoi ngay.*\bcon\b(?! no)|het ngay.*\bcon\b(?! no)", text):
        raise UnsupportedFrame("specialist stock/purchase/expiry/stagnation metric")
    if frame.metric == "other" or frame.operation == "other" or not caps.get("revenue"):
        raise UnsupportedFrame("specialist/raw SQL required" + " [concepts=" + ",".join(question_concepts(question.question)) + "]")
    anchor = price_increase_anchor(frame, text, engine)
    if anchor:
        question = question.model_copy(update={"question": f"{question.question} (ngày {anchor:%d/%m/%Y})"})
        text = fold(question.question)
    periods = resolve_periods(question.question, question.as_of_date, engine, exclude=(frame.product_filter, frame.customer_filter))
    if not periods:
        raise UnsupportedFrame("time expression not supported by deterministic resolver")
    if frame.operation == "trend":
        periods=sorted(periods,key=lambda p:p.start)
    if len(periods) > 1 and frame.operation not in {"growth", "difference", "series", "trend"} and frame.group_by != "month" and contiguous(periods) \
            and re.search(r"\b(?:tu|bat dau tu)\b.*\b(?:den|toi)\b|\b(?:thang|th|t)\s*\d{1,2}\b.*\b(?:den|toi)\s+(?:het\s+)?(?:thang|th|t)\s*\d", text) \
            and not re.search(r"\b(?:moi|tung|tung moi)\s+(?:thang|quy|ky)\b|theo (?:tung )?thang|lan luot|\brieng\b|tach (?:theo|tung)|so (?:voi|vs)", text):
        # §2: 'từ tháng 1 đến tháng 3' is one period, both ends included, when one figure is asked.
        periods = [Period(periods[0].start, periods[-1].end, "range")]
    if frame.group_by=='month' and frame.operation=='top' and len(periods)>1:
        periods=[Period(min(p.start for p in periods),max(p.end for p in periods),'range')]
    if frame.metric in {"orders", "cancelled_orders", "cancel_rate", "purchase_days", "product_order_count"} and not caps.get("orders"):
        if re.search(r'khong ghi ma don|khong co ma don|khong ghi theo don',fold(catalog['readme'])):
            return SQLPlan(action='unanswerable',sql='',answer_type='unanswerable',shape='scalar',rounding='none',date_granularity='day',reason='README và nguồn không ghi mã/quy tắc nhận diện đơn; số dòng sự kiện không phải số đơn.')
        raise UnsupportedFrame("no supported order identity")
    if frame.metric in {"quantity", "product_order_count"} and not caps.get("sales"):
        raise UnsupportedFrame("no item-level source")
    if frame.metric == "gross_profit" and not caps.get("unit_cost"):
        # Rulebook §5: gross profit uses the cost written on each sale line, never re-derived from purchases or lots; §7: not recorded.
        return SQLPlan(action='unanswerable',sql='',answer_type='unanswerable',shape='scalar',rounding='none',date_granularity='day',reason='Sổ bán không ghi giá vốn trên dòng bán (§5), nên không tính được lãi.')
    if frame.customer_filter and not caps.get("customers"):
        raise UnsupportedFrame("no customer identity")
    reslotted = reslot_filters(frame, search_key(question.question), engine)
    sales_based = frame.metric in {"quantity", "gross_profit", "product_order_count"} or bool(frame.product_filter or frame.group_filter or frame.unit_filter or frame.size_filter) or frame.group_by in {"product", "product_group"}
    if sales_based and not caps.get("sales"):
        raise UnsupportedFrame("item-level data required")
    table = "v_sales" if sales_based or not caps.get("orders") else "v_orders"
    if frame.metric in {"cancelled_orders", "cancel_rate"}:
        table = "v_orders_all"
    if frame.group_by in {"customer", "employee"} and not caps.get("orders"):
        raise UnsupportedFrame("grouping requires supported orders")
    # Coverage derived from observed source dates plus declared end-of-data.
    _, coverage = engine.lookup(f"SELECT MIN(CAST(order_date AS DATE)),MAX(CAST(order_date AS DATE)) FROM {table}")
    start, end = coverage[0]
    cutoff = re.search(r"den het (\d{2})/(\d{2})/(\d{4})", fold(catalog["readme"]))
    if cutoff:
        end = dt.date(int(cutoff[3]), int(cutoff[2]), int(cutoff[1]))
    if not start or not end:
        raise UnsupportedFrame("unknown coverage")
    if all(period.end < start or period.start > end for period in periods):
        return SQLPlan(action="unanswerable", sql="", answer_type="unanswerable", shape="scalar", rounding="none", date_granularity="day", reason="Kỳ hỏi hoàn toàn ngoài khoảng dữ liệu nguồn đã quan sát và ngày chốt README.")

    def entity_condition(column, query):
        if not query:
            return None
        if column not in {"product_name", "customer_name"}:
            _,values=engine.lookup(f'SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL')
            candidates=[query,re.sub(r'^(?:nhom hang|nhom khach|nhom|hang)\s+','',fold(query))]
            matches=[row[0] for candidate in candidates for row in values if search_key(row[0])==search_key(candidate)]
            if not matches and column=='customer_group':
                stripped=re.sub(r'^(?:nhom\s+)?khach(?:\s+hang)?(?:\s+nhom)?\s+','',fold(query)).strip()
                matches=[row[0] for row in values if search_key(row[0])==search_key(stripped)]
            if not matches and column=='product_group':
                # A named customer group is sometimes extracted as a product group.
                _,groups=engine.lookup(f'SELECT DISTINCT customer_group FROM {table} WHERE customer_group IS NOT NULL')
                customer=[row[0] for candidate in candidates for row in groups if search_key(row[0])==search_key(candidate)]
                if customer:
                    return f"norm(customer_group)=norm({literal(customer[0])})"
            if not matches and column=='base_unit' and (frame.product_filter or frame.group_filter or search_key(query) in {'mon','mat hang','san pham','mon hang'}):
                return None  # the named item or group fixes the unit; a mistyped/generic unit word ('chiếc') is not a filter
            if not matches and column=='product_group':
                # The first word shared by several groups names all of them ('máy' = máy mới + máy cũ).
                heads=sorted({row[0] for candidate in candidates for row in values if search_key(row[0]).startswith(search_key(candidate)+' ')})
                if len(heads)>=2:
                    return f"norm({column}) IN ("+",".join(f"norm({literal(name)})" for name in heads)+")"
            if not matches:
                raise UnsupportedFrame('Filter value not found in available source category: '+column+'='+query)
            return f"norm({column})=norm({literal(matches[0])})"
        target = search_key(query)
        matches = match_names(engine, column, query)
        if column == "customer_name":
            # §3: a customer is a customer code; without codes, the name written on the order, spellings aside
            # ('… 0704 626 776' and '… 0704626776' are one customer). Every order of the matched customers counts.
            spelled = re.sub(r"[^a-z0-9]", "", fold(query))
            _, rows = engine.lookup(f"SELECT DISTINCT customer_key,customer_name FROM {table} WHERE customer_key IS NOT NULL AND customer_name IS NOT NULL")
            keys = sorted({key for key, name in rows if key == spelled or re.sub(r"[^a-z0-9]", "", fold(name)) == spelled})
            if not keys and matches:
                chosen = set(matches)
                keys = sorted({key for key, name in rows if name in chosen})
            if keys:
                return "customer_key IN (" + ",".join(literal(key) for key in keys) + ")"
        if not matches and column == "product_name":
            _, units = engine.lookup("SELECT DISTINCT base_unit FROM v_sales WHERE base_unit IS NOT NULL")
            unit = [row[0] for row in units if search_key(row[0]) == target]
            if unit:
                return f"norm(base_unit)=norm({literal(unit[0])})"
        if not matches:
            raise UnsupportedFrame("entity not resolved: " + query)
        return column + " IN (" + ",".join(literal(name) for name in matches) + ")"

    if not frame.customer_filter and not frame.customer_group_filter and re.search(r"khach (?:hang )?(?:thuoc )?(?:nhom|hang)\b|nhom khach", text):
        # 'khách nhóm sỉ' read without diacritics: the group name may land in a product/group slot.
        for name in ("group_filter", "product_filter"):
            # A value the re-slot step matched to a product group in the data stays a product group.
            if getattr(frame, name) and not any(change.endswith("->" + name) or change == name + ":prefix" for change in reslotted):
                frame.customer_filter = getattr(frame, name)
                setattr(frame, name, "")
                break
    if frame.customer_filter and caps.get("customers"):
        # A customer group named as a customer ('khách nhóm thân thiết') filters the group;
        # it is not a customer restriction, so walk-in revenue stays in share denominators.
        target = search_key(frame.customer_filter)
        _, names = engine.lookup("SELECT DISTINCT customer_name FROM v_orders WHERE customer_name IS NOT NULL")
        if not any(target and target in search_key(row[0]) for row in names):
            _, groups = engine.lookup("SELECT DISTINCT customer_group FROM v_orders WHERE customer_group IS NOT NULL") if "v_orders" in engine.tables else ([], [])
            stripped = re.sub(r"^(?:khach\s+)?(?:hang\s+)?(?:thuoc\s+)?(?:nhom|hang)\s+(?:khach\s+)?", "", target)
            group = [row[0] for row in groups if search_key(row[0]) in {target, stripped}]
            if group:
                frame.customer_filter, frame.customer_group_filter = "", frame.customer_group_filter or group[0]
    base_filters = []
    shift_sql = {"sang": "EXTRACT(hour FROM order_time) BETWEEN 6 AND 13", "chieu": "EXTRACT(hour FROM order_time) BETWEEN 14 AND 21"}
    shifts = {match[1] for match in re.finditer(r"\bca (sang|chieu)\b", text)}
    if shifts and not caps.get("order_time"):
        raise UnsupportedFrame("shift requires order creation time")
    # Afternoon versus morning: same period, morning shift is the base (rulebook).
    shift_compare = len(shifts) == 2
    if shifts:
        # Shift words extracted as product/group names ('ca', 'sáng', 'chiều') are not entities.
        for name in ("product_filter", "group_filter", "channel_filter"):
            if set(search_key(getattr(frame, name)).split()) <= {"ca", "sang", "chieu", "sangs", "chieuf"} and getattr(frame, name):
                setattr(frame, name, "")
    if shift_compare:
        frame.operation = "growth" if frame.output_type == "percent" or wants_percent(text) else "difference"
        frame.group_by = "none"
    elif shifts:
        base_filters.append(shift_sql[shifts.pop()])
    if frame.weekday_filter:
        base_filters.append(f"isodow(order_date)={frame.weekday_filter}")
    if frame.group_by == "customer" or frame.customer_filter:
        base_filters.append("customer_key IS NOT NULL")
    filters = []
    if frame.employee_filter:
        value=frame.employee_filter
        if not re.fullmatch(r'NV\d+',value,re.I):
            if 'v_employees' not in engine.tables:
                raise UnsupportedFrame('No employee name to ID mapping available.')
            _,staff=engine.lookup('SELECT employee_id FROM v_employees WHERE norm(employee_name)=norm('+literal(value)+')')
            if len(staff)!=1:
                raise UnsupportedFrame('Employee name does not resolve uniquely.')
            value=staff[0][0]
        filters.append('norm(employee_id)=norm('+literal(value)+')')
    pairs = []
    requested_payments, requested_channels = payments_in(text), channels_in(text)
    if len(requested_payments) >= 2 or len(requested_channels) >= 2:
        if re.search(r'ben nao|nao (?:nhieu|cao|it|thap) hon|hon hay kem|chenh|cao hon|thap hon|nhieu hon|it hon', text) and not re.search(r'moi ben|moi loai|lan luot', text):
            raise UnsupportedFrame('comparison between payment methods/channels')
        if len(requested_payments) >= 2 and len(requested_channels) >= 2:
            raise UnsupportedFrame('mixed payment and channel series')
        pairs = [('payment_method', v) for v in requested_payments] if len(requested_payments) >= 2 else [('channel', v) for v in requested_channels]
    pair_column = pairs[0][0] if pairs else None
    for column, values in [('payment_method', requested_payments), ('channel', requested_channels)]:
        if not values:
            continue
        _, recorded = engine.lookup(f"SELECT DISTINCT {'norm_channel(channel)' if column=='channel' else 'payment_method'} FROM {table} WHERE NULLIF(TRIM(CAST({column} AS VARCHAR)),'') IS NOT NULL")
        recorded = {row[0] for row in recorded}
        if not recorded or any(value not in recorded for value in values):
            label = 'hình thức thanh toán' if column == 'payment_method' else 'kênh bán'
            return SQLPlan(action='unanswerable', sql='', answer_type='unanswerable', shape='scalar', rounding='none', date_granularity='day',
                           reason=f'Nguồn không ghi {label} được hỏi trong toàn bộ dữ liệu.')
    for column, value in [("product_name", frame.product_filter), ("customer_name", frame.customer_filter), ("product_group", frame.group_filter),
                          ("customer_group", frame.customer_group_filter), ("base_unit", frame.unit_filter), ("channel", frame.channel_filter)]:
        if column == pair_column:
            continue
        if column == 'channel' and value:
            filters.append(f"norm_channel(channel)={literal(channel(value))}")
            continue
        condition = entity_condition(column, value)
        if condition:
            filters.append(condition)
    if frame.payment_filter and pair_column != 'payment_method':
        filters.append(f"payment_method={literal(payment_method(frame.payment_filter))}")
    area = seating_area(engine, question.question)
    if area:
        # 'doanh thu khu phòng lạnh': orders seated in that area (README 'Khu vực'); matched on the accented words ('sân' is not 'sản').
        filters.append(f"order_id IN (SELECT order_id FROM v_dining WHERE area={literal(area)})")
    if frame.size_filter and cafe_sizes(engine):
        # Rulebook §6 Cà phê: size M/L = item names ending in (M)/(L). A diaper 'size M' is a catalog attribute, not this.
        filters.append("regexp_matches(product_name," + literal(r"(?i)\(" + frame.size_filter.upper() + r"\)$") + ")")
    if frame.metric == "quantity" or frame.group_by == "product":
        filters.append("norm(product_name)<>'phi phuc vu'")
    if frame.metric == "cancelled_orders":
        base_filters.append("status='cancelled'")
    elif frame.metric == "orders" and frame.operation == "value" and re.search(r"\bdon\b(?:\s+\w+){0,3}\s+(?:bi\s+)?(?:khach\s+)?tra (?:hang|lai)\b|\bdon tra hang\b|\btra (?:lai|hang) (?:bao nhieu|may) don\b", text) \
            and not re.search(r"doanh thu|\btien\b|tru (?:di )?don", text):
        # 'bao nhiêu đơn khách trả hàng': count returned orders (only in v_orders_all), not all valid orders.
        base_filters.append("status='returned'")
        table = "v_orders_all"

    def where(period, extra):
        parts = [f"order_date>=TIMESTAMP {literal(period.start)}", f"order_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))}"] + base_filters + extra
        return " WHERE " + " AND ".join(parts)

    aggregations = {
        "revenue": "COALESCE(SUM(revenue),0)", "quantity": "COALESCE(SUM(base_quantity),0)",
        "orders": "COUNT(DISTINCT order_id)", "cancelled_orders": "COUNT(DISTINCT order_id)",
        "gross_profit": "SUM(revenue-quantity*unit_cost)", "purchase_days": "COUNT(DISTINCT CAST(order_date AS DATE))",
        "product_order_count": "COUNT(DISTINCT order_id)",
        "cancel_rate": "100.0*COUNT(*) FILTER(WHERE status='cancelled')/NULLIF(COUNT(*),0)",
    }
    aggregation = aggregations[frame.metric]
    if frame.operation == "average_order":
        aggregation = "SUM(revenue)/NULLIF(COUNT(DISTINCT order_id),0)"
    if frame.metric == "quantity" and frame.operation == "value" and table == "v_sales" and DISTINCT_ITEMS.search(text):
        # 'bao nhiêu mặt hàng khác nhau': items sold, each counted once (§8: spellings of one name are one item).
        aggregation = "COUNT(DISTINCT regexp_replace(norm(product_name),'[^a-z0-9]+',' ','g'))"
    rounding = "percent" if frame.output_type == "percent" else "money" if frame.metric in {"revenue", "gross_profit"} else "integer" if frame.metric in {"orders", "cancelled_orders", "purchase_days", "product_order_count"} else "none"
    if frame.operation == "daily_average":
        period = periods[0]
        start_day, end_day = max(start, period.start), min(end, period.end)
        days = sum(not frame.weekday_filter or (start_day+dt.timedelta(days=i)).isoweekday() == frame.weekday_filter for i in range((end_day-start_day).days+1))
        aggregation = "(" + aggregation + f")/{days}" if days else "NULL"

    def scalar(period, extra=filters):
        return "SELECT " + aggregation + " AS value FROM " + table + where(period, extra)

    shape = "scalar"
    output_type = frame.output_type
    if pairs and frame.metric in {'revenue','orders','quantity'}:
        series = []
        for index,(column,value) in enumerate(pairs):
            condition = (f"norm_channel(channel)={literal(value)}" if column=='channel' else f"payment_method={literal(value)}")
            series.append(f"SELECT {index} seq,value FROM (" + scalar(periods[0],filters+[condition]) + ")")
        query,shape,output_type = "SELECT value FROM ("+' UNION ALL '.join(series)+") ORDER BY seq",'column','list'
    elif shift_compare:
        query = "WITH a AS (" + scalar(periods[0], filters + [shift_sql["chieu"]]) + "),b AS (" + scalar(periods[0], filters + [shift_sql["sang"]]) + ") SELECT "
        query += ("100.0*(a.value-b.value)/NULLIF(b.value,0)" if frame.operation == "growth" else "a.value-b.value") + " FROM a,b"
        if frame.operation == "growth":
            output_type, rounding = "percent", "percent"
        else:
            output_type = "number"
    elif frame.operation in {"growth", "difference"}:
        previous = periods[1] if len(periods) == 2 else periods[0].previous()
        lead = re.search(r'\bso (?:voi|vs)\b', text)
        first_time = re.search(r'\d', text)
        if len(periods) == 2 and lead and first_time and lead.start() < first_time.start():
            # 'So với tháng 3/2025, tháng 3/2026 tăng bao nhiêu %': the period after 'so với' is the base.
            periods, previous = [periods[1], periods[0]], periods[0]
        if re.search(r'nam (?:o )?(?:ngoai|truoc|roi)\b|cung ky nam\b',text):
            if 'tet' in text:
                year=periods[0].end.year-1
                _,lunar=engine.lookup(f"SELECT MIN(date) FILTER(WHERE lunar_year={year-1} AND lunar_month=12 AND lunar_day=23),MIN(date) FILTER(WHERE lunar_year={year} AND lunar_month=1 AND lunar_day=10) FROM calendar")
                if not lunar or not all(lunar[0]):
                    raise UnsupportedFrame('Previous lunar Tet coverage missing.')
                previous=Period(lunar[0][0],lunar[0][1],'tet')
            else:
                try:
                    previous = Period(periods[0].start.replace(year=periods[0].start.year-1), periods[0].end.replace(year=periods[0].end.year-1), periods[0].mode)
                except ValueError:
                    raise UnsupportedFrame("leap-year comparison requires specialist")
        if previous.end < start or previous.start > end:
            # §7: the compared period lies wholly outside the shop's data: not recorded, not zero.
            return SQLPlan(action='unanswerable',sql='',answer_type='unanswerable',shape='scalar',rounding='none',date_granularity='day',reason='Kỳ so sánh nằm hoàn toàn ngoài khoảng dữ liệu của shop.')
        query = "WITH a AS (" + scalar(periods[0]) + "),b AS (" + scalar(previous) + ") SELECT "
        query += "100.0*(a.value-b.value)/NULLIF(b.value,0)" if frame.operation == "growth" else "a.value-b.value"
        query += " FROM a,b"
        if frame.operation == "growth":
            output_type, rounding = "percent", "percent"
    elif frame.operation in {"series", "trend"}:
        if len(periods) < 2:
            raise UnsupportedFrame("multiple periods not resolved")
        series = " UNION ALL ".join("SELECT " + str(i) + " AS seq,value FROM (" + scalar(period) + ")" for i, period in enumerate(periods))
        if frame.operation == "series":
            query, shape, output_type = "SELECT value FROM (" + series + ") ORDER BY seq", "column", "list"
        else:
            query = "SELECT BOOL_AND(value>previous_value) FILTER(WHERE previous_value IS NOT NULL) FROM (SELECT *,LAG(value) OVER(ORDER BY seq) previous_value FROM (" + series + "))"
            output_type = "boolean"
    elif frame.operation == "share":
        if frame.group_by != "none" or not filters:
            raise UnsupportedFrame("share numerator/denominator requires specialist")
        query = "WITH a AS (" + scalar(periods[0], filters) + "),b AS (" + scalar(periods[0], []) + ") SELECT 100.0*a.value/NULLIF(b.value,0) FROM a,b"
        output_type, rounding = "percent", "percent"
    elif frame.group_by != "none":
        keys = {
            "product": ("COALESCE(product_id,trim(regexp_replace(norm(product_name),'\\s+',' ','g')))", "COALESCE(MODE(product_name),FIRST(product_id))"),
            "product_group": ("product_group", "product_group"), "customer": ("customer_key", "COALESCE(FIRST(customer_name),FIRST(customer_id))"),
            "employee": ("employee_id", "employee_id"), "day": ("CAST(order_date AS DATE)", "CAST(order_date AS DATE)"),
            "month": ("date_trunc('month',order_date)", "date_trunc('month',order_date)"),
            "hour": ("EXTRACT(hour FROM order_time)", "EXTRACT(hour FROM order_time)"),
            "payment": ("payment_method", "payment_method"), "channel": ("channel", "channel"),
        }
        key, label = keys[frame.group_by]
        having = " HAVING COUNT(DISTINCT order_id)>=3" if frame.group_by == "customer" and re.search(r"\bsop\b", fold(question.question)) else ""
        # An unrecorded employee/hour/group is not an object to rank.
        present = [f"{key} IS NOT NULL"] if frame.group_by in {"employee", "hour", "product_group", "payment", "channel"} else []
        grouped = f"SELECT {key} AS key,{label} AS name,{aggregation} AS value FROM {table}" + where(periods[0], filters + present) + f" GROUP BY {key}" + having
        if frame.operation in {"threshold_count", "threshold_share"}:
            test = f"value{frame.threshold_operator}{frame.threshold}"
            expression = f"COUNT(*) FILTER(WHERE {test})" if frame.operation == "threshold_count" else f"100.0*COUNT(*) FILTER(WHERE {test})/NULLIF(COUNT(*),0)"
            query, output_type = f"SELECT {expression} FROM ({grouped})", "number" if frame.operation == "threshold_count" else "percent"
            rounding = "integer" if output_type == "number" else "percent"
        else:
            selected = "name,value" if output_type == "table" else "name"
            query = f"SELECT {selected} FROM ({grouped}) ORDER BY value DESC,key ASC LIMIT {frame.k}"
            shape = "table" if output_type == "table" else "column" if output_type == "list" else "scalar"
    elif frame.operation == "threshold_count" and frame.metric == "orders":
        query = f"SELECT COUNT(*) FROM {table}" + where(periods[0], filters + [f"revenue{frame.threshold_operator}{frame.threshold}"])
    elif frame.operation in {"value", "average_order", "daily_average"}:
        if output_type in {"list", "table", "boolean", "date", "string"}:
            raise UnsupportedFrame("requested output is not a scalar aggregate" + " [concepts=" + ",".join(question_concepts(question.question)) + "]")
        query = scalar(periods[0])
    else:
        raise UnsupportedFrame("unsupported combination" + " [concepts=" + ",".join(question_concepts(question.question)) + "]")
    return SQLPlan(action="query", sql=query, answer_type=output_type, shape=shape, rounding=rounding,
                   date_granularity="month" if frame.group_by == "month" else "day", reason="Typed frame + deterministic time/business compiler: " + frame.reason + (" [filters: " + ", ".join(reslotted) + "]" if reslotted else ""))
