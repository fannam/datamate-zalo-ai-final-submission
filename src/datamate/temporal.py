from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
import datetime as dt
import re

from .parsing import fold


@dataclass(frozen=True)
class Period:
    start: dt.date
    end: dt.date
    mode: str

    def previous(self):
        if self.mode in {"month", "mtd"}:
            previous = self.start - dt.timedelta(days=1)
            end_day = min(self.end.day, monthrange(previous.year, previous.month)[1]) if self.mode == "mtd" else previous.day
            return Period(previous.replace(day=1), previous.replace(day=end_day), self.mode)
        if self.mode == "quarter":
            year, month = shift_month(self.start.year, self.start.month, -3)
            return Period(dt.date(year, month, 1), self.start-dt.timedelta(days=1), "quarter")
        if self.mode == "wtd":
            return Period(self.start-dt.timedelta(days=7), self.start-dt.timedelta(days=1), "week")
        length = (self.end - self.start).days + 1
        return Period(self.start - dt.timedelta(days=length), self.start - dt.timedelta(days=1), self.mode)


def full_month(year, month):
    return Period(dt.date(year, month, 1), dt.date(year, month, monthrange(year, month)[1]), "month")


def shift_month(year, month, offset):
    value = year * 12 + month - 1 + offset
    return value // 12, value % 12 + 1


NUMBER_WORDS = {"mot": 1, "hai": 2, "ba": 3, "bon": 4, "tu": 4, "nam": 5, "sau": 6, "bay": 7, "tam": 8, "chin": 9, "muoi": 10, "muoi mot": 11, "muoi hai": 12}
# 'từ'/'sau' fold to 'tu'/'sau' (from/after), so they are not counts; 'tháng sau' is next month.
# 'tháng năm' is too often 'tháng [của] năm trước' to read as May.
MONTH_WORD = r"muoi mot|muoi hai|mot|hai|ba|bon|bay|tam|chin|muoi"
COUNT_WORD = r"muoi mot|muoi hai|mot|hai|ba|bon|nam|bay|tam|chin|muoi"


def infer_year(day: int, month: int, today: dt.date):
    """Rulebook §2: a date without a year is this year unless that is after today, then last year."""
    try:
        value = dt.date(today.year, month, day)
    except ValueError:
        return None
    return value if value <= today else (value.replace(year=today.year - 1) if not (month == 2 and day == 29) else None)


def canonical_time(text: str, today: dt.date) -> str:
    """Spoken time words to the forms the resolver reads ('ngày 15 tháng 8' -> '15/8/2026', 'ba ngày' -> '3 ngày')."""
    # Typed variants of time words (scripts/time_stress.py): 'quí 4/2025' was read as April, not Q4.
    text = re.sub(r"\bqu(?:i|y)[sfrxj]?\b", "quy", text)
    # '30/4 năm 2025' is one day written with its year apart.
    text = re.sub(r"(?<![\d/])(\d{1,2})/(\d{1,2})\s+(?:nam|năm)\s+(20\d{2})\b", r"\1/\2/\3", text)
    text = re.sub(r"\b(thang|tuan|ngay|nam|hom|bua|quy)[sfrxj]\b", r"\1", text)
    text = re.sub(r"\b(hom|bua|tuan|thang|nam) wa\b", r"\1 qua", text)
    # ASCII "thang tu" is April unless "tu" introduces the following range.
    text = re.sub(r"\bthang tu\b(?!\s+(?:\d|(?:thang|th|t)\s*\d|dau\b|cuoi\b|ngay\b))", "thang 4", text)
    text = re.sub(r"\bthang (" + MONTH_WORD + r")\b(?! (?:ngay|tuan|thang|nam|lan|don|hom|bua)\b)", lambda m: f"thang {NUMBER_WORDS[m[1]]}", text)
    def count(match):
        before = text[:match.start()].split()[-2:]
        # 'thứ hai ngày ...' is a weekday; 'doanh thu ba ngày' is a count.
        if before and before[-1] == "thu" and (len(before) < 2 or before[-2] not in {"doanh", "tien"}):
            return match[0]
        return f"{NUMBER_WORDS[match[1]]} {match[2]}"
    text = re.sub(r"\b(" + COUNT_WORD + r")\s+(ngay|tuan|thang|hom|bua|quy)\b", count, text)
    text = re.sub(r"\bquy (iv|iii|ii|i)\b", lambda m: "quy " + str({"i": 1, "ii": 2, "iii": 3, "iv": 4}[m[1]]), text)
    text = re.sub(r"\b(tuan|thang|quy|nam) vua (?:roi|qua)\b", r"\1 truoc", text)
    # Other written date forms -> d/m/yyyy: 5-3-2026, 5.3.2026, 2026-03-05, 'ngày 5/3/26'.
    def written(day, month, year):
        return f"{int(day)}/{int(month)}/{year}" if 1 <= int(day) <= 31 and 1 <= int(month) <= 12 else None
    text = re.sub(r"(?<![\d.\-/])(\d{1,2})[.\-](\d{1,2})[.\-](20\d{2})(?![\d.\-/])", lambda m: written(m[1], m[2], m[3]) or m[0], text)
    text = re.sub(r"(?<![\d.\-/])(20\d{2})-(\d{1,2})-(\d{1,2})(?![\d\-/])", lambda m: written(m[3], m[2], m[1]) or m[0], text)
    # Month written '07-2025' / '7.2025' without 'tháng' -> '7/2025' (month 1-12 only; '30x60-2025' stays).
    text = re.sub(r"(?<![\d.\-/])(\d{1,2})[.\-](20\d{2})(?![\d.\-/])", lambda m: f"{int(m[1])}/{m[2]}" if 1 <= int(m[1]) <= 12 else m[0], text)
    text = re.sub(r"\b(ngay|mung|hom|bua)\s+(\d{1,2})/(\d{1,2})/(\d{2})(?![\d/])", lambda m: f"{m[1]} " + (written(m[2], m[3], "20" + m[4]) or f"{m[2]}/{m[3]}/{m[4]}"), text)
    text = re.sub(r"\b(ngay|mung|hom)\s+(\d{1,2})\s+thang\s+(\d{1,2})(?:\s+nam\s+(20\d{2}))?", lambda m: f"{m[1]} {m[2]}/{m[3]}" + (f"/{m[4]}" if m[4] else ""), text)
    # 'tháng 2 năm ngoái' is February of LAST year (was read as this year's February); 'năm nay' pins this year.
    text = re.sub(r"\b(thang|quy)\s*(\d{1,2})\s+(?:cua\s+)?nam\s+(?:ngoai|truoc|roi)\b", lambda m: f"{m[1]} {m[2]}/{today.year - 1}", text)
    text = re.sub(r"\b(thang|quy)\s*(\d{1,2})\s+(?:cua\s+)?nam\s+(?:nay|ni)\b", lambda m: f"{m[1]} {m[2]}/{today.year}", text)
    text = re.sub(r"\bq([1-4])\s+nam\s+(?:ngoai|truoc|roi)\b", lambda m: f"quy {m[1]}/{today.year - 1}", text)
    text = re.sub(r"\bq([1-4])\s+nam\s+(?:nay|ni)\b", lambda m: f"quy {m[1]}/{today.year}", text)
    # 'tháng này năm ngoái' is this month of last year; 'quý này năm ngoái' this quarter of last year.
    text = re.sub(r"\bthang (?:nay|ni) (?:cua )?nam (?:ngoai|truoc|roi)\b", f"thang {today.month}/{today.year - 1}", text)
    text = re.sub(r"\bquy (?:nay|ni) (?:cua )?nam (?:ngoai|truoc|roi)\b", f"quy {(today.month - 1) // 3 + 1}/{today.year - 1}", text)
    # 'ngày 20 tháng này / tháng trước': one day of this or last month.
    def month_day(match):
        year, month = (today.year, today.month) if match[3] in ("nay", "ni") else shift_month(today.year, today.month, -1)
        day = int(match[2])
        if day == 1 and match[3] in ("nay", "ni"):
            return match[0]   # 'từ ngày 1 tháng này' is 'tháng này' (§2: compared with the same days of last month)
        return f"{match[1]} {day}/{month}/{year}" if day <= monthrange(year, month)[1] else match[0]
    text = re.sub(r"\b(ngay|mung)\s+(\d{1,2})\s+(?:cua\s+)?thang\s+(nay|ni|truoc|trc|roi)\b", month_day, text)
    # Day ranges (§2 'Từ X đến Y, nhóm X–Y' include both ends): 'ngày 5-10/3', 'ngày 10 đến ngày 20/4', 'từ đầu tháng 5 đến 15/5'.
    text = re.sub(r"\b(?:tu\s+)?(?:ngay|mung)\s+(\d{1,2})\s*[-\u2013]\s*(\d{1,2})/(\d{1,2})(/20\d{2})?(?![\d/])",
                  lambda m: f"tu {m[1]}/{m[3]}{m[4] or ''} den {m[2]}/{m[3]}{m[4] or ''}", text)
    text = re.sub(r"(?<!tu )\b(ngay|mung)\s+(\d{1,2})\s+(den|toi)\s+(?=(?:het\s+)?(?:(?:ngay|mung)\s+)?\d{1,2}/\d{1,2})", r"tu \1 \2 \3 ", text)
    text = re.sub(r"\btu\s+dau\s+thang\s+(\d{1,2})(/20\d{2})?\s+(den|toi)\s+(?=(?:het\s+)?(?:(?:ngay|mung)\s+)?\d{1,2}/\d{1,2})",
                  lambda m: f"tu 1/{m[1]}{m[2] or ''} {m[3]} ", text)
    # 'từ (mùng) 1 đến (mùng) 10/1/2026': the first day takes the month/year written once at the end.
    text = re.sub(r"\btu\s+(?:ngay\s+|mung\s+|hom\s+)?(\d{1,2})\s+(den|toi)\s+(het\s+)?((?:ngay|mung|hom)\s+)?(\d{1,2})/(\d{1,2})(/20\d{2})?(?![\d/])",
                  lambda m: f"tu {m[1]}/{m[6]}{m[7] or ''} {m[2]} {m[3] or ''}{m[4] or ''}{m[5]}/{m[6]}{m[7] or ''}", text)
    explicit = re.search(r"(?<!\d)\d{1,2}/\d{1,2}/(20\d{2})(?!\d)", text)

    def dated(match):
        day, month = int(match[2]), int(match[3])
        if explicit:
            year = int(explicit[1])
        else:
            value = infer_year(day, month, today)
            if value is None:
                return match[0]
            year = value.year
        try:
            dt.date(year, month, day)
        except ValueError:
            return match[0]
        return f"{match[1]} {day}/{month}/{year}"
    # Only with a day word in front: '1/2' in a product name is not a date.
    return re.sub(r"\b(ngay|mung|hom|bua|tu|den|toi)\s+(\d{1,2})/(\d{1,2})(?![\d/])", dated, text)


def quarter_of(day: dt.date):
    first = (day.month - 1) // 3 * 3 + 1
    return dt.date(day.year, first, 1)


def resolve_periods(question: str, today: dt.date, engine=None, exclude=()):
    question = re.sub(r"(?i)\btháng tư\b", "thang 4", question)
    text = fold(question)
    # Entity names can look like dates ('đèn tuýp LED T8' is not August): drop them first.
    for name in exclude:
        key = fold(name).strip()
        if len(key) >= 3:
            text = text.replace(key, " ")
    text = canonical_time(text, today)
    # Shared year in a month enumeration: "th7, th8, th9/2025", including
    # numeric-only months after the first label. Do not inherit across unrelated dates.
    chain = re.compile(r"(?<![a-z])(?:thang|th|t)\s*\d{1,2}(?:(?:\s*,\s*|\s+va\s+)(?:(?:thang|th|t)\s*)?\d{1,2})+\s*(?:/|nam\s*)(20\d{2}|\d{2})\b")
    def expand_months(m):
        year = int(m[1]); year += 2000 if year < 100 else 0
        head = re.sub(r"(?:/|nam\s*)"+re.escape(m[1])+r"\s*$", "", m[0])
        return ", ".join(f"thang {int(n)}/{year}" for n in re.findall(r"\d{1,2}", head))
    text = chain.sub(expand_months, text)
    # From the first of THIS month through today is MTD, including the comparison window.
    if re.search(r"\btu (?:mung|ngay) 1 (?:toi|den) (?:het )?(?:hom nay|nay)\b", text):
        return [Period(today.replace(day=1), today, "mtd")]
    dates = re.findall(r"(?<!\d)(\d{1,2})/(\d{1,2})/(20\d{2})(?!\d)", text)
    if dates:
        values = [dt.date(int(y), int(m), int(d)) for d, m, y in dates]
        window = re.search(r'(\d+)\s*ngay\s*(?:ke tu|tu ngay|tu hom|tu bua|dau)',text)
        if window and re.search(r'gia|len \d',text) and re.search(r'so voi|so vs|so.*ngay.*truoc|lien truoc|ngay ngay truoc',text):
            count=int(window[1]);anchor=values[0]
            return [Period(anchor,anchor+dt.timedelta(days=count-1),'range'),Period(anchor-dt.timedelta(days=count),anchor-dt.timedelta(days=1),'range')]
        if len(values) >= 2 and re.search(r"\btu\b|\bden\b|\btoi\b", text):
            return [Period(values[0], values[1], "range")]
        if len(values) == 1 and re.search(r"\btu\b.*\b(?:den|toi)\s+(?:het\s+)?(?:hom nay|hnay|bua nay|hom ni|bua ni|nay|gio|bay gio|bua gio|hien tai)\b", text) and values[0] <= today:
            return [Period(values[0], today, "range")]
        return [Period(values[0], values[0], "day")]
    if re.search(r"tet", text) and engine:
        year = re.search(r"tet(?:\s+am\s+lich)?\s*(20\d{2})", text)
        year = int(year[1]) if year else today.year
        _, rows = engine.lookup(f"SELECT MIN(date) FILTER(WHERE lunar_year={year-1} AND lunar_month=12 AND lunar_day=23),MIN(date) FILTER(WHERE lunar_year={year} AND lunar_month=1 AND lunar_day=10) FROM calendar")
        if rows and all(rows[0]):
            return [Period(rows[0][0], rows[0][1], "tet")]
    first_days = re.search(r"(\d+) ngay dau (?:cua )?thang (\d{1,2})(?:\s*(?:/|nam)\s*(20\d{2}))?", text)
    if first_days:
        month, count = int(first_days[2]), int(first_days[1])
        year = int(first_days[3]) if first_days[3] else today.year - (month > today.month)
        if 1 <= month <= 12 and 1 <= count <= monthrange(year, month)[1]:
            return [Period(dt.date(year, month, 1), dt.date(year, month, count), "range")]
    first_months = re.search(r"(\d+|nua) thang (?:dau|cuoi) (?:cua )?nam(?:\s*(20\d{2}))?|nua (dau|cuoi) nam(?:\s*(20\d{2}))?", text)
    if first_months:
        year = int(first_months[2] or first_months[4] or today.year)
        if first_months[3] or first_months[1] == "nua":
            months = range(7, 13) if (first_months[3] == "cuoi" or "thang cuoi" in first_months[0]) else range(1, 7)
        else:
            count = int(first_months[1])
            months = range(13 - count, 13) if "cuoi" in first_months[0] else range(1, count + 1)
        if 1 <= len(months) <= 12:
            return [Period(dt.date(year, months[0], 1), full_month(year, months[-1]).end, "range")]
    def month_of(month_text, year_text):
        month = int(month_text)
        year = int(year_text) if year_text else today.year - (month > today.month)
        return (year, month) if 1 <= month <= 12 else None
    # Parts of a month with exact days: from the 1st to today, the last N days, the first or last day.
    since = re.search(r"\btu dau thang (\d{1,2})(?:\s*(?:/|nam)\s*(20\d{2}))? (?:den|toi) (?:het )?(?:nay|hom nay|hnay|gio|bay gio|hien tai)\b", text)
    if since and month_of(since[1], since[2]):
        year, month = month_of(since[1], since[2])
        if dt.date(year, month, 1) <= today:
            return [Period(dt.date(year, month, 1), today, "range")]
    last_days = re.search(r"(\d+) ngay cuoi (?:cung )?(?:cua )?thang (\d{1,2})(?:\s*(?:/|nam)\s*(20\d{2}))?", text)
    if last_days and month_of(last_days[2], last_days[3]):
        year, month = month_of(last_days[2], last_days[3])
        length, count = monthrange(year, month)[1], int(last_days[1])
        if 1 <= count <= length:
            return [Period(dt.date(year, month, length - count + 1), dt.date(year, month, length), "range")]
    edge_day = re.search(r"\bngay (cuoi cung|cuoi|dau tien) (?:cua )?thang (\d{1,2})(?:\s*(?:/|nam)\s*(20\d{2}))?", text)
    if edge_day and month_of(edge_day[2], edge_day[3]):
        year, month = month_of(edge_day[2], edge_day[3])
        day = 1 if edge_day[1] == "dau tien" else monthrange(year, month)[1]
        return [Period(dt.date(year, month, day), dt.date(year, month, day), "day")]
    # 'từ đầu tháng 3 đến (hết / cuối) tháng 4/2026': whole months, both ends included (§2).
    span = re.search(r"\btu dau thang (\d{1,2})(?:\s*(?:/|nam)\s*(20\d{2}))? (?:den|toi) (?:het |cuoi )?thang (\d{1,2})(?:\s*(?:/|nam)\s*(20\d{2}))?", text)
    if span and month_of(span[3], span[4] or span[2]):
        end_year, end_month = month_of(span[3], span[4] or span[2])
        start = month_of(span[1], span[2] or str(end_year))
        if start and dt.date(start[0], start[1], 1) <= dt.date(end_year, end_month, 1):
            return [Period(dt.date(start[0], start[1], 1), full_month(end_year, end_month).end, "range")]
    if re.search(r"nua (?:dau|cuoi|sau) thang|(?:cuoi|dau|giua) thang \d|tuan (?:dau|cuoi|thu \d)(?: tien)? (?:cua )?thang", text):
        return []  # part of a month without exact days: not guessed
    month_matches = []
    # Word boundary: 'xet 3 thang' must not read as 't 3' (month 3).
    pattern = r"(?<![a-z])(?:thang|th|t)\s*(\d{1,2})\s*(?:(?:/|nam\s*|[-])\s*(20\d{2}|\d{2}))?"
    for match in re.finditer(pattern, text):
        month = int(match[1])
        if not 1 <= month <= 12:
            continue
        if match[2]:
            year = int(match[2])
            year += 2000 if year < 100 else 0
        else:
            year = today.year - (month > today.month)
        month_matches.append((match.start(),full_month(year, month)))
    if not re.search(r"(?:quy|\bqu?)\s*[1-4]", text):
        for match in re.finditer(r"(?<![\d/])(\d{1,2})/(20\d{2}|\d{2})(?![\d/])", text):
            month, year = int(match[1]), int(match[2])
            year += 2000 if year < 100 else 0
            if 1 <= month <= 12:
                month_matches.append((match.start(),full_month(year, month)))
    if month_matches:
        # Deduplicate repeated mentions without reordering the requested sequence.
        unique = list(dict.fromkeys(p for _,p in sorted(month_matches,key=lambda item:item[0])))
        if len(unique) == 1 and re.search(r"3 thang lien|ba thang lien|3 thang gan|3 thang.*ket thuc", text):
            ending = unique[0].start
            return [full_month(*shift_month(ending.year, ending.month, offset)) for offset in (-2, -1, 0)]
        if len(unique) == 2 and re.search(r"\btu\b.*\b(?:den|toi)\b|\bt\d.*\btoi\b|\bt\d.*\bden\b|\b(?:thang|th|t)\s*\d{1,2}\b.*\b(?:den|toi)\s+(?:het\s+)?(?:thang|th|t)\s*\d", text) and not re.search(r'so voi|so vs',text) and unique[0].start <= unique[1].start:
            periods = []
            year, month = unique[0].start.year, unique[0].start.month
            while dt.date(year, month, 1) <= unique[1].start:
                periods.append(full_month(year, month))
                year, month = shift_month(year, month, 1)
            return periods
        return unique
    quarter = re.search(r"(?:quy|\bqu?)\s*([1-4])\s*(?:[/\-]|nam\s*)?\s*(20\d{2}|\d{2})?", text)
    if quarter and re.search(r"\btu dau quy\s*[1-4]\b.*\b(?:den|toi)\s+(?:het\s+)?(?:nay|hom nay|hnay|gio|bay gio|hien tai)\b", text):
        q = int(quarter[1])
        year = int(quarter[2]) if quarter[2] else today.year - ((q-1)*3+1 > today.month)
        year += 2000 if year < 100 else 0
        start = dt.date(year, (q-1)*3+1, 1)
        if start <= today:
            return [Period(start, today, "range")]
    if quarter:
        q = int(quarter[1])
        year = int(quarter[2]) if quarter[2] else today.year - ((q-1)*3+1 > today.month)
        year += 2000 if year < 100 else 0
        first, last = full_month(year, (q-1)*3+1), full_month(year, q*3)
        return [Period(first.start, last.end, "quarter")]
    if re.search(r"quy\s+(?:nay|ni)\b|\btu dau quy\b(?! \d)|\bdau quy (?:den|toi) (?:nay|gio|hom nay)", text):
        return [Period(quarter_of(today), today, "qtd")]
    if re.search(r"quy\s+(?:truoc|trc|roi)\b", text):
        end = quarter_of(today) - dt.timedelta(days=1)
        return [Period(quarter_of(end), end, "quarter")]
    if re.search(r"\b(\d+) (?:thang|quy|nam) (?:qua|gan nhat|gan day|truoc|roi)\b", text) or re.search(r"(?<!thu )\b(?:[2-9]|\d{2}) tuan (?:truoc|roi)\b", text):
        return []  # 'N tháng qua' may or may not include this month: not guessed
    weeks = re.search(r"\b(\d+) tuan (?:gan nhat|gan day|qua)\b", text)
    if weeks:
        return [Period(today - dt.timedelta(days=7 * int(weeks[1]) - 1), today, "range")]
    if re.search(r"thang\s+(?:truoc|trc)\s+nua\b", text):
        return [full_month(*shift_month(today.year, today.month, -2))]
    if re.search(r"tuan\s+(?:truoc|trc)\s+nua\b", text):
        monday = today - dt.timedelta(days=today.weekday())
        return [Period(monday - dt.timedelta(days=14), monday - dt.timedelta(days=8), "week")]
    if re.search(r"thang\s+(?:nay|ni)|\bth\s*(?:nay|ni)\b|tu dau thang|dau thang (?:den|toi) (?:nay|gio|hom nay)", text):
        return [Period(today.replace(day=1), today, "mtd")]
    if re.search(r"tu dau tuan|dau tuan (?:den|toi)|tu thu (?:hai|2) (?:den|toi) (?:nay|gio|hom nay)", text):
        return [Period(today - dt.timedelta(days=today.weekday()), today, "wtd")]
    if re.search(r'thang\s+(?:truoc|trc|roi|qua)\b',text):
        return [full_month(*shift_month(today.year,today.month,-1))]
    if re.search(r"tuan\s+(?:truoc|trc|roi|r|qua)\b", text):
        monday = today - dt.timedelta(days=today.weekday())
        return [Period(monday-dt.timedelta(days=7), monday-dt.timedelta(days=1), "week")]
    if re.search(r"tuan\s+(?:nay|ni)\b", text):
        return [Period(today-dt.timedelta(days=today.weekday()), today, "wtd")]
    days = re.search(r"(\d+)\s*(?:ngay|bua|hom)\s*(?:gan nhat|gan day|vua qua|vua roi|qua\b|roi\b|nay\b|tro lai day)", text)
    if days:
        return [Period(today-dt.timedelta(days=int(days[1])-1), today, "range")]
    years = [int(y) for y in re.findall(r"\bnam\s+(20\d{2})\b", text)]
    if years and re.search(r"dau nam", text):
        start = dt.date(years[0], 1, 1)
        return [Period(start, min(today, dt.date(years[0], 12, 31)), "ytd")]
    if years:
        return [Period(dt.date(y, 1, 1), dt.date(y, 12, 31), "year") for y in dict.fromkeys(years)]
    if re.search(r"dau nam|nam nay|nam ni", text):
        return [Period(today.replace(month=1, day=1), today, "ytd")]
    if re.search(r"\bnam\s+(?:ngoai|truoc|trc|roi)\b", text):
        return [Period(dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31), "year")]
    if re.search(r"\bmay (?:hom|bua|ngay) (?:nay|qua)\b|\bhom bua\b|\bbua truoc\b", text):
        return []  # 'these few days' has no fixed length
    weekday = re.search(r"\b(?:(?:ngay\s+)?thu\s*(2|3|4|5|6|7|hai|ba|tu|nam|sau|bay)|chu nhat|\bcn)\s+(?:vua roi|vua qua|gan nhat)\b", text)
    if weekday:
        # 'chủ nhật vừa rồi': the latest such weekday before today.
        number = {"hai": 2, "ba": 3, "tu": 4, "nam": 5, "sau": 6, "bay": 7}.get(weekday[1], weekday[1])
        target = 6 if number is None else int(number) - 2
        day = today - dt.timedelta(days=(today.weekday() - target - 1) % 7 + 1)
        return [Period(day, day, "day")]
    if re.search(r"\b(?:hom|bua) kia\b", text):
        day = today - dt.timedelta(days=2)
        return [Period(day, day, "day")]
    if re.search(r"hom qua|\bhqua\b|bua qua|bua hom qua", text):
        yesterday = today-dt.timedelta(days=1)
        return [Period(yesterday, yesterday, "day")]
    if re.search(r"hom nay|\bhnay\b|\bhn\b|bua nay|bua ni|hom ni", text):
        return [Period(today, today, "day")]
    return []
