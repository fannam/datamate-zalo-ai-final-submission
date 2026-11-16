"""One rewrite of a question the compiler could not resolve, then a normal re-plan.

Used only after the deterministic compiler rejected the model's frame for a reason a
clearer wording can fix: an unresolved name, a filter value absent from the data, or
an unreadable time expression. The model sees the question, the compiler's error and
names that exist in this shop's data; it returns a rewritten question. Answers are
still computed by the compiler from SQL, never by the model. A rewrite that loses or
alters a number from the original question is discarded.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import difflib
import re

from pydantic import BaseModel, ConfigDict, Field

from .io import dumps
from .parsing import fold

REWRITE_VERSION = "rewrite-v3"

REPAIRABLE = re.compile(r"entity not resolved|Filter value not found|time expression not supported|multiple periods not resolved|"
                        r"stagnation period not resolved|Employee (?:name|restriction)|unsupported combination|requested output is not a scalar|"
                        r"specialist/raw SQL required|Câu hỏi yêu cầu phần trăm|share numerator/denominator")

SYSTEM = ("Bạn viết lại MỘT câu hỏi của chủ cửa hàng về số liệu bán hàng để hệ thống hiểu đúng. Viết bằng tiếng Việt chuẩn, đủ dấu: "
          "sửa lỗi gõ (telex, thiếu dấu), viết tắt, từ địa phương và mốc thời gian gõ sai (vd 'hoang qua' = hôm qua, 'hoy' = hôm nay, 'tuần tr c' = tuần trước). "
          "Giữ NGUYÊN ý hỏi, mọi con số, ngày tháng, mốc thời gian, điều kiện và kiểu đáp án (một số, phần trăm, danh sách, tên...). "
          "Nếu câu nhắc một mặt hàng, khách, nhóm hay nhân viên: thay bằng ĐÚNG một tên trong danh sách tên có trong dữ liệu khi chắc chắn là cùng một thứ "
          "(chỉ khác cách viết, viết tắt, thiếu dấu); nếu không chắc thì giữ nguyên chữ của câu gốc. "
          "Chữ không phải tên (vd 'cửa hàng', 'quán' gõ sai thành 'cnhg', 'cwng') thì sửa thành từ đúng, không biến thành tên hàng. "
          "Mốc thời gian phải viết theo MỘT trong các dạng: 'hôm nay', 'hôm qua', 'hôm kia', 'ngày dd/mm/yyyy', 'từ dd/mm/yyyy đến dd/mm/yyyy', "
          "'tuần này', 'tuần trước', 'tháng M/yyyy', 'tháng này', 'tháng trước', 'quý Q/yyyy', 'năm yyyy', 'từ đầu năm đến hôm nay', 'N ngày gần nhất'; "
          "tính ngày cụ thể từ as_of_date (hôm nay) khi câu gốc nói tương đối, tuần bắt đầu thứ Hai. "
          "Không thêm điều kiện mới. Chỉ trả JSON.")


class Rewrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(max_length=500)


def repairable(error: str) -> bool:
    return bool(REPAIRABLE.search(error or ""))


def key(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", fold(value)).strip()


def unresolved_value(error: str) -> str:
    match = re.search(r"entity not resolved: (.+)$|Filter value not found in available source category: [a-z_]+=(.+)$", error or "")
    return (match[1] or match[2]).strip() if match else ""


POOLS = (("mặt hàng", "SELECT DISTINCT product_name FROM v_sales WHERE product_name IS NOT NULL"),
         ("khách", "SELECT DISTINCT customer_name FROM v_orders WHERE customer_name IS NOT NULL"),
         ("nhóm hàng", "SELECT DISTINCT product_group FROM v_sales WHERE product_group IS NOT NULL"),
         ("nhóm khách", "SELECT DISTINCT customer_group FROM v_orders WHERE customer_group IS NOT NULL"),
         ("nhân viên", "SELECT DISTINCT employee_name FROM v_employees WHERE employee_name IS NOT NULL"),
         ("mặt hàng trong danh mục", "SELECT DISTINCT product_name FROM v_products WHERE product_name IS NOT NULL"),
         ("thợ", "SELECT DISTINCT mechanic FROM v_tickets WHERE mechanic IS NOT NULL"),
         ("dòng xe", "SELECT DISTINCT model_label FROM v_tickets WHERE model_label IS NOT NULL"),
         ("nhà cung cấp", "SELECT DISTINCT supplier FROM v_purchases WHERE supplier IS NOT NULL"),
         ("hãng", "SELECT DISTINCT brand FROM v_sales_attrs WHERE brand IS NOT NULL"),
         ("dòng máy", "SELECT DISTINCT model_line FROM v_phone_sales WHERE model_line IS NOT NULL"),
         ("hoạt chất", "SELECT DISTINCT ingredient FROM v_drugs WHERE ingredient IS NOT NULL"))


def candidates(engine, question: str, error: str, per_pool: int = 6) -> dict[str, list[str]]:
    """Names in this shop's data closest to the unresolved text (or to the question when none is named)."""
    target = key(unresolved_value(error)) or key(question)
    if not target:
        return {}
    words = set(target.split())
    result = {}
    for label, sql in POOLS:
        try:
            _, rows = engine.lookup(sql)
        except Exception:
            continue
        scored = []
        for (name,) in rows:
            name_key = key(name)
            if not name_key:
                continue
            ratio = difflib.SequenceMatcher(None, target, name_key).ratio()
            overlap = len(words & set(name_key.split())) / max(len(set(name_key.split())), 1)
            # A short name fully contained in the question counts as a strong match.
            contained = 1.0 if len(name_key) >= 4 and re.search(r"(?<!\w)" + re.escape(name_key) + r"(?!\w)", key(question)) else 0.0
            scored.append((max(ratio, overlap, contained), str(name)))
        scored.sort(key=lambda item: (-item[0], item[1]))
        top = [name for score, name in scored[:per_pool] if score >= 0.35]
        if top:
            result[label] = top
    return result


def numbers(text: str) -> Counter:
    return Counter(re.findall(r"\d+", fold(text)))


def date_spelling_numbers(text, names):
    """Canonicalize only explicit date/month formatting; retain identifier/amount tokens."""
    value=fold(text)
    signatures=[]
    patterns=[
        (r"(?<!\w)(ngay|hom|bua)\s*(\d{1,2})/(\d{1,2})/(20\d{2})(?![\d/])","day"),
        (r"(?<!\w)(thang|th)\s*(\d{1,2})/(20\d{2})(?![\d/])","month")
    ]
    for pattern,kind in patterns:
        protected=[(m.start(),m.end()) for items in names.values() for name in items if name
                   for m in re.finditer(re.escape(fold(name)),value)]
        def replace(m):
            if any(a<m.end() and m.start()<b for a,b in protected):
                return m[0]
            try:
                if kind=="day":
                    day,month,year=map(int,m.group(2,3,4));date(year,month,day)
                    signatures.append(("day",year,month,day))
                    return f"{m[1]} {day}/{month}/{year}"
                month,year=map(int,m.group(2,3));date(year,month,1)
                signatures.append(("month",year,month))
                return f"{m[1]} {month}/{year}"
            except ValueError:
                return m[0]
        value=re.sub(pattern,replace,value)
    return value,Counter(signatures)


def acceptable(original: str, rewritten: str, names: dict[str, list[str]]) -> bool:
    """Every number of the original survives; new numbers may only come from a listed name ('330ml')."""
    if not rewritten.strip() or key(rewritten) == key(original):
        return False
    original_numbers, before_dates=date_spelling_numbers(original,names)
    rewritten_numbers, after_dates=date_spelling_numbers(rewritten,names)
    if before_dates and after_dates:
        # Some list members omit the month/day prefix; adding those prefixes is
        # cosmetic. Compare the recognized subset, while preserving all numbers.
        smaller,larger=(before_dates,after_dates) if sum(before_dates.values())<=sum(after_dates.values()) else (after_dates,before_dates)
        if smaller-larger:return False
    before, after = numbers(original_numbers), numbers(rewritten_numbers)
    if before - after:
        return False
    allowed = Counter()
    for values in names.values():
        for value in values:
            allowed |= numbers(value)
    return not (after - before - allowed)


def rewrite_messages(question, error: str, names: dict[str, list[str]]):
    payload = {"question": question.question, "as_of_date": question.as_of_date.isoformat(),
               "compiler_error": re.sub(r"\s+", " ", re.sub(r" \[concepts=[^\]]*\]", "", error))[:300], "names_in_data": names}
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": dumps(payload)}]


ENTITY_FIELDS = ("product_filter", "customer_filter", "group_filter", "customer_group_filter", "employee_filter")


def grounded(first: dict, second: dict, error: str, original: str, minimum: float = 0.6) -> bool:
    """A name the re-plan filters on must come from the original question, not from the candidate list alone.

    Each new entity filter must appear verbatim in the original question, or be close to the
    unresolved text / the first frame's filter ('co ca co la 330ml' -> 'Coca-Cola lon 330ml').
    A time-only error ('hoang qua') never licenses a new name.
    """
    question = " " + key(original) + " "
    sources = [key(unresolved_value(error))] + [key(first.get(field) or "") for field in ENTITY_FIELDS]
    sources = [s for s in sources if s]
    for field in ENTITY_FIELDS:
        value = key(second.get(field) or "")
        if not value or value == key(first.get(field) or ""):
            continue
        if " " + value + " " in question:
            continue
        if not any(difflib.SequenceMatcher(None, value, source).ratio() >= minimum for source in sources):
            return False
    return True
