from __future__ import annotations

import datetime as dt
import functools
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
import re
import unicodedata


@lru_cache(maxsize=1 << 20)
def _fold_text(text: str) -> str:
    text = unicodedata.normalize("NFD", text).replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def fold(value: object) -> str:
    # Pure function of the text; cached because DuckDB UDFs call it once per row on highly repetitive values.
    return _fold_text(str(value or ""))


def identifier(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^a-z0-9]+", "_", fold(text)).strip("_")
    return ("c_" + text if text[:1].isdigit() else text) or "column"


@lru_cache(maxsize=131072)
def parse_number_text(value: str) -> Decimal | None:
    text = value.strip().replace("\u00a0", " ").replace("−", "-")
    if not text or text.lower() in {"none", "null", "nan", "-", "—"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = fold(text)
    mixed = re.fullmatch(r"\s*([-+]?)(\d+)\s*(tr|trieu|k)\s*(\d{1,3})(?:\s*(?:k|d|dong))?\s*", text)
    if mixed:
        factor = Decimal(1000 if mixed[3] == "k" else 1000000)
        result = (Decimal(mixed[2]) + Decimal("0." + mixed[4])) * factor
        return -result if mixed[1] == "-" or negative else result
    match = re.search(r"[-+]?\d[\d.,\s]*", text)
    if not match:
        return None
    number = re.sub(r"\s", "", match.group())
    tail = text[match.end():].strip()
    factor = Decimal(1)
    if re.match(r"^(tr|trieu)\b", tail):
        factor = Decimal(1000000)
    elif re.match(r"^(k|nghin|ngan)\b", tail):
        factor = Decimal(1000)
    if "." in number and "," in number:
        if number.rfind(",") > number.rfind(".") and len(number.rsplit(",", 1)[1]) != 3:
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif "," in number or "." in number:
        separator = "," if "," in number else "."
        pieces = number.split(separator)
        if factor == 1 and all(len(part) == 3 for part in pieces[1:]):
            number = "".join(pieces)
        else:
            number = "".join(pieces[:-1]) + "." + pieces[-1]
    try:
        result = Decimal(number) * factor
        return -abs(result) if negative else result
    except InvalidOperation:
        return None


def parse_number(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, (int, float, Decimal)):
        result = Decimal(str(value))
        return result if result.is_finite() else None
    return parse_number_text(str(value))


@lru_cache(maxsize=65536)
def parse_datetime_text(value: str, year: int | None = None) -> dt.datetime | None:
    text = fold(value).strip()
    text = re.sub(r"\b(?:thg|thang)\s*", "/", text)
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"^(\d{1,2})/(\d{1,2})\s+(\d{4})", r"\1/\2/\3", text)
    if re.fullmatch(r"\d{5}(?:\.\d+)?", text):
        serial = float(text)
        if 20000 < serial < 80000:
            return dt.datetime(1899, 12, 30) + dt.timedelta(days=serial)
    try:
        return dt.datetime.fromisoformat(text.replace("z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%Y/%m/%d", "%H:%M:%S", "%H:%M"):
        try:
            result = dt.datetime.strptime(text, fmt)
            return result.replace(year=2000) if fmt.startswith("%H") else result
        except ValueError:
            pass
    if year:
        for fmt in ("%d/%m", "%d-%m"):
            try:
                return dt.datetime.strptime(text + "/" + str(year), fmt + "/%Y")
            except ValueError:
                pass
    return None


def parse_datetime(value: object, year: int | None = None) -> dt.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time())
    if isinstance(value, dt.time):
        return dt.datetime.combine(dt.date(2000, 1, 1), value)
    return parse_datetime_text(str(value), year)


def status(value: object) -> str:
    text = fold(value).strip()
    if re.search(r"\bhuy\b|cancel|void|khong giao duoc", text):
        return "cancelled"
    if re.search(r"tra hang|tra lai|return|hoan hang|hoan tra|doi tra|hang tra ve|tra may|khach tra|khach mang tra", text):
        return "returned"
    if re.search(r"dang giao|shipping|delivering|dang van chuyen|dang gui|cho giao|dang xu ly", text):
        return "shipping"
    if not text:
        return "unknown"
    if re.search(r"hoan thanh|hoan tat|completed|done|xong|success|da giao|thanh cong|da lay|^ok$", text):
        return "completed"
    return "unknown"


def guest(value: object) -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", fold(value)).strip()
    return text in {"", "khach le", "kl", "le", "khach vang lai", "vang lai", "khach khong luu", "walk in"}


def payment_method(value: object) -> str:
    text = fold(value).strip()
    if text in {"no", "debt"}:
        return "debt"
    if text == "ck":
        return "transfer"
    if text == "tm":
        return "cash"
    if re.search(r"ghi no|ban no|mua chiu|debt|con no|tra gop|installment", text):
        return "debt"
    if "cod" in text or "thu ho" in text:
        return "cod"
    if re.search(r"chuyen khoan|transfer|bank", text):
        return "transfer"
    if re.search(r"vi dien tu|ewallet|momo|zalopay", text):
        return "ewallet"
    if re.search(r"tien mat|cash", text):
        return "cash"
    return text


def channel(value: object) -> str:
    text = fold(value).strip().replace('_', ' ')
    if re.search(r'dien thoai|phone|telephone', text) or text == 'dt':
        return 'phone'
    if 'zalo' in text:
        return 'zalo_oa'
    if text in {'store', 'tai quay', 'tai cua hang', 'cua hang', 'truc tiep', 'tai tiem', 'tai quan', 'tai shop', 'offline', 'tai cho'}:
        return 'store'
    if re.search(r'san tmdt|san thuong mai|tmdt|marketplace|shopee|lazada|tiki|tiktok|sendo', text):
        return 'marketplace'
    return text


def round_decimal(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


# Business syllables for file/sheet names written glued ('danhmuc.csv', 'baohanh.csv', 'thuno.csv').
NAME_SYLLABLES = set("""ban hang don hoa chi tiet danh muc sach san pham khach nha cung cap nhan vien nhap phieu thu thanh toan cong no tra so quy phi khoan
ton kho the bao sua xe gop giao lo bang gia lich su doi vi quan ly cao may cu hanh dich vu thuoc tong hop nhat ky ca dat coc ghi tat""".split())


@functools.lru_cache(maxsize=4096)
def name_words(name: str) -> str:
    """'danhmuc' -> 'danh_muc', 'so_banhang' -> 'so_ban_hang'; a token that is not fully made of known syllables is kept."""
    def split(token):
        best = {0: []}
        for end in range(1, len(token) + 1):
            for start in range(max(0, end - 5), end):
                if start in best and token[start:end] in NAME_SYLLABLES and (end not in best or len(best[start]) + 1 < len(best[end])):
                    best[end] = best[start] + [token[start:end]]
        return "_".join(best[len(token)]) if len(token) in best and len(best[len(token)]) > 1 else token
    return "_".join(split(token) for token in name.split("_"))
