from __future__ import annotations

from collections import Counter, defaultdict
import csv
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import io
import itertools
import fcntl
from pathlib import Path
import re
import sqlite3
import unicodedata
import zipfile

import duckdb
from openpyxl import load_workbook
try:  # fast Rust xlsx reader; openpyxl stays the reference and the fallback
    from python_calamine import CalamineWorkbook
except ImportError:  # pragma: no cover
    CalamineWorkbook = None
import pyarrow as pa

from .io import fingerprint, write_json
from .parsing import fold, identifier, parse_datetime, parse_number

INGEST_VERSION = "clean-v10"


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def normalized_filename(name: str) -> str:
    value = identifier(Path(name).stem)
    value = re.sub(r"_(?:t|thang)?\d{1,2}_20\d{2}$", "", value)
    value = re.sub(r"_(?:20\d{2}|\d+)$", "", value)
    return value


def references(readme: str) -> dict[tuple[str, str], dict[str, str]]:
    result = defaultdict(dict)
    current = None
    for line in readme.splitlines():
        if line.startswith("## "):
            names = re.findall(r"`([^`]+)`", line)
            current = (normalized_filename(names[0]), identifier(names[1]) if len(names) > 1 else "") if names and "cot trong" in fold(line) else None
        match = re.match(r"- \*\*(.+?)\*\*:\s*(.*)", line)
        if current and match:
            result[current][identifier(match[1])] = match[2]
    return dict(result)


def header_index(rows: list[list]) -> int:
    def score(row):
        terms = ("ma", "ten", "ngay", "thoi", "gio", "so", "sl", "stt", "gia", "thanh", "tong", "don", "khach", "hang", "nhom", "trang", "tinh", "phone", "date", "amount", "price", "product", "customer", "unit", "bill", "qty", "no", "tt", "ck", "dvt", "phi", "ghi", "lo", "han", "ton", "da", "con", "chiet", "xu", "nguon", "nhan", "ban", "kenh", "doi", "du", "phai", "dien", "thu", "hoa", "xe", "lam", "tho", "ho", "dv", "he", "quy", "bo", "imei", "hđ", "hd", "dg")
        values = [identifier(x) for x in row if x is not None and str(x).strip()]
        hits = sum(any(x == term or x.startswith(term + "_") for term in terms) for x in values)
        return hits * 4 + min(len(values), 40) * 0.1
    return max(range(len(rows)), key=lambda i: (score(rows[i]), -i))


def column_kind(name: str, description: str, examples: list) -> str:
    key, desc = identifier(name), fold(description)
    # Business labels containing words such as 'tiền' are not money amounts.
    if re.search(r"trang_thai|tinh_trang|^status$|^tien_do$|^xu_ly$|ket_qua|ghi_chu|dien_giai|^note$|phuong_thuc|hinh_thuc|cach_hoan|kenh|nguon|^pttt$|^httt$|^tra_bang$", key) or re.search(r"^(trang thai|hinh thuc|phuong thuc|ghi chu|kenh ban|loai giao dich)", desc):
        return "text"
    if re.search(r"^ma(?:_|$)|(?:^|_)id$|^sku$|^imei$|^stt$|^no$|so_thu_tu|dien_thoai|phone|barcode|ma_vach|so_dt|^sdt$|^so_ct$|^so_phieu$|^so_hoa_don$|^so_don$|^so_hd$|^so_kh$", key) or re.search(r"^ma (?:don|hang|khach|phieu|nhan|nha|san)|so dien thoai|so thu tu", desc):
        return "text"
    if re.search(r"ngay|^date$|thoi_gian|thoi_diem|^time$|^gio$|^gio_ban$|^gio_tao|^ngayban$|^taoluc$|^han_su_dung$|^hsd$|^han_thanh_toan$|^han_tra$|han_dung", key) or re.search(r"^(ngay |thoi diem |thoi gian )", desc):
        # 'Số ngày được nợ' and due-day-of-month are durations/numbers.
        if re.search(r"so_ngay|ngay_duoc_no|han_no_ngay|han_gop_ngay|ky_han|han_no", key) or "so ngay" in desc:
            return "number"
        return "datetime"
    if re.search(r"^(?:so_luong|sl|slg|sluong|soluong)(?:_|$)|(?:^|_)(?:tien|gia|price|amount|quantity|qty|ton|cost|doanh_thu|revenue|chiet_khau|giam|no|discount|paid|debt|tong|tri_gia)(?:_|$)|^tongtien$|^so_tien$|^tt$|^dg$|^he_so|^ti_le|^ty_le|^bang$|^quy_doi$|hoa_hong|so_khach|so_ky|tra_truoc|gop_thang|^chua_tra$|^khach_tra$|^da_thu$|^con_lai$", key) or re.search(r"^(don gia|gia |thanh tien|tien |tong tien|so luong|con no|da thanh toan|khach da tra|he so|phan tram|ton kho|giam gia|so tien|so khach con no)", desc):
        return "number"
    valid = [x for x in examples if x is not None and str(x).strip()]
    if valid and all(isinstance(x, (dt.date, dt.datetime, dt.time)) for x in valid):
        return "datetime"
    if valid and all(isinstance(x, (int, float, Decimal)) and not isinstance(x, bool) for x in valid):
        return "number"
    return value_kind(valid) or "text"


DATE_TEXT = re.compile(r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{2}-\d{2})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?")
NUMBER_TEXT = re.compile(r"-?(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d+)?\s*(?:đ|d|vnd|vnđ|k)?", re.I)


def value_kind(valid):
    """Kind read from text cells when the header gives no hint (CSV, unseen headers).

    Codes stay text: leading zeros (phones, codes) and runs of distinct consecutive
    integers (row numbers, order numbers) are identifiers, not amounts.
    """
    strings = [str(x).strip() for x in valid if isinstance(x, str)]
    if len(strings) < 3 or len(strings) != len(valid):
        return None
    if all(DATE_TEXT.fullmatch(s) for s in strings) and all(parse_datetime(s) for s in strings):
        return "datetime"
    if all(NUMBER_TEXT.fullmatch(s) for s in strings) and not any(re.match(r"-?0\d", s) for s in strings):
        integers = [int(s) for s in strings if re.fullmatch(r"-?\d+", s)]
        if len(integers) == len(strings) and len(set(integers)) == len(integers) and max(integers) - min(integers) < 3 * len(integers):
            return None
        return "number"
    return None


def csv_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1258", errors="replace")


def csv_rows(path: Path):
    """Rows of a CSV export: UTF-8/UTF-16/cp1258; ';' or tab delimiters when they clearly dominate ','."""
    text = csv_text(path)
    lines = [line for line in text.splitlines()[:20] if line.strip()]

    def count(delimiter):
        return sum(len(next(csv.reader([line], delimiter=delimiter))) - 1 for line in lines)
    delimiter = max((",", ";", "\t", "|"), key=lambda d: (count(d), d == ","))
    return csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)


def csv_header_index(leading: list[list]) -> int:
    """Row 0 unless it looks like a title (few filled cells); then the header-like row among the first 12."""
    width = max(sum(1 for x in row if str(x).strip()) for row in leading)
    filled = sum(1 for x in leading[0] if str(x).strip())
    return 0 if filled >= max(2, width // 2) else header_index(leading)


class _NotExact(Exception):
    """A cell python-calamine may not reproduce exactly as openpyxl does: read the workbook with openpyxl."""


def _calamine_cell(value):
    # Mirror openpyxl(read_only, data_only) cell values: whole numbers are int, empty cells None, dates are datetime.
    kind = type(value)
    if kind is float:
        if value.is_integer():
            if abs(value) >= 2 ** 53:  # beyond exact float range: openpyxl parses the text exactly, calamine may not
                raise _NotExact
            return int(value)
        return value
    if kind is str:
        return value if value else None
    if kind is dt.date:
        return dt.datetime(value.year, value.month, value.day)
    if value is None or kind in (int, bool, dt.datetime):
        return value
    raise _NotExact  # time / timedelta / anything unexpected


def calamine_sheets(path: Path):
    """[(sheet title, rows)] read with python-calamine (~8x faster than openpyxl), or None to use openpyxl. Checked
    cell-for-cell against openpyxl on all train + public workbooks. Workbooks with error cells (#N/A, #VALUE!: calamine
    drops them) or the 1904 date system, and any cell _calamine_cell cannot mirror, fall back to openpyxl."""
    if CalamineWorkbook is None:
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            if re.search(rb'date1904="(?:1|true)"', archive.read("xl/workbook.xml")):
                return None
            for name in archive.namelist():
                if name.startswith("xl/worksheets/") and name.endswith(".xml") and re.search(rb'\bt="e"', archive.read(name)):
                    return None
        workbook = CalamineWorkbook.from_path(str(path))
        try:
            return [(title, [tuple(_calamine_cell(value) for value in row) for row in workbook.get_sheet_by_name(title).to_python(skip_empty_area=False)])
                    for title in workbook.sheet_names]
        finally:
            workbook.close()
    except Exception:  # _NotExact, unreadable package parts, calamine errors: the reference reader decides
        return None


def sources(folder: Path):
    """Only business file readers. Never opens questions, answers, .env or reports."""
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() == ".csv":
            reader = csv_rows(path)
            leading = list(itertools.islice(reader, 12))
            if not leading:
                continue
            index = csv_header_index(leading)
            header = leading[index]
            rows = itertools.chain(enumerate(leading[index + 1:], index + 2), enumerate(reader, len(leading) + 1))
            yield path.name, "", header, rows
        elif path.suffix.lower() == ".xlsx" and (fast := calamine_sheets(path)) is not None:
            for title, values in fast:
                iterator = iter(values)
                leading = list(itertools.islice(iterator, 12))
                if not leading:
                    continue
                index = header_index(leading)
                header = list(leading[index])
                rows = itertools.chain(enumerate(leading[index + 1:], index + 2), enumerate(iterator, len(leading) + 1))
                yield path.name, title, header, rows
        elif path.suffix.lower() == ".xlsx":
            workbook = load_workbook(path, read_only=True, data_only=True)
            try:
                for sheet in workbook:
                    iterator = sheet.iter_rows(values_only=True)
                    leading = list(itertools.islice(iterator, 12))
                    if not leading:
                        continue
                    index = header_index(leading)
                    header = list(leading[index])
                    rows = itertools.chain(enumerate(leading[index + 1:], index + 2), enumerate(iterator, len(leading) + 1))
                    yield path.name, sheet.title, header, rows
            finally:
                workbook.close()
        elif path.suffix.lower() == ".sqlite":
            connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
                    cursor = connection.execute("SELECT * FROM " + quote(table))
                    header = [item[0] for item in cursor.description]
                    yield path.name, table, header, enumerate(cursor, 1)
            finally:
                connection.close()


def table_base(filename: str, sheet: str) -> str:
    sheet_id = identifier(sheet) if sheet else ""
    if re.fullmatch(r"\s*(?:t|thang)?\s*\d{1,2}\s*[_/.\- ]\s*20\d{2}\s*", fold(sheet)):
        return normalized_filename(filename)
    if sheet_id and not re.fullmatch(r"(?:sheet\d*|t\d+_20\d+|\d+_20\d+|thang_?\d+_20\d+)", sheet_id):
        return sheet_id
    return normalized_filename(filename)


def prepare_shop(split: Path, business_id: str, cache_root: Path, *, force=False) -> Path:
    split=split.resolve()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", business_id):
        raise ValueError('Invalid business_id')
    folder=split/'data'/business_id
    files=[p for p in folder.iterdir() if p.suffix.lower() in {'.csv','.xlsx','.sqlite','.md'}]
    digest=fingerprint(files+[split/'rulebook.md',split/'calendar.csv'],version=INGEST_VERSION)
    locks=cache_root/'locks'
    locks.mkdir(parents=True,exist_ok=True)
    with (locks/(digest+'.lock')).open('a') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        return _prepare_shop_locked(split,business_id,cache_root,force=force)


def _prepare_shop_locked(split: Path, business_id: str, cache_root: Path, *, force=False) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", business_id):
        raise ValueError("Invalid business_id")
    folder = (split / "data" / business_id).resolve()
    if folder.parent != (split / "data").resolve():
        raise ValueError("Business directory is outside split")
    files = [p for p in folder.iterdir() if p.suffix.lower() in {".csv", ".xlsx", ".sqlite", ".md"}]
    digest = fingerprint(files + [split / "rulebook.md", split / "calendar.csv"], version=INGEST_VERSION)
    destination = cache_root / "data" / digest
    catalog_path = destination / "catalog.json"
    if not force and catalog_path.exists() and (destination / "store.duckdb").exists():
        return destination
    destination.mkdir(parents=True, exist_ok=True)
    temp_db = destination / "building.duckdb"
    if temp_db.exists():
        temp_db.unlink()
    readme = (folder / "README.md").read_text()
    annotation = references(readme)
    database = duckdb.connect(str(temp_db))
    database.execute("SET threads=2")
    database.execute("SET memory_limit='1GB'")
    catalog = {"version": INGEST_VERSION, "business_id": business_id, "fingerprint": digest, "readme": readme, "tables": []}
    groups, signatures, stats, collisions = {}, {}, {}, Counter()
    try:
        for filename, sheet, header, iterator in sources(folder):
            # Trailing empty cells are not columns; internal blanks retain position.
            while header and header[-1] is None:
                header.pop()
            if not header:
                continue
            leading = list(itertools.islice(iterator, 24))
            description = annotation.get((normalized_filename(filename), identifier(sheet)), annotation.get((normalized_filename(filename), ""), {}))
            names, seen = [], Counter()
            for original in header:
                base = identifier(original)
                seen[base] += 1
                names.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
            key = (table_base(filename, sheet), tuple(names))
            if key not in groups:
                base = key[0]
                collisions[base] += 1
                table = base if collisions[base] == 1 else f"{base}_{collisions[base]}"
                columns = []
                for i, (name, original) in enumerate(zip(names, header)):
                    desc = description.get(identifier(original), "")
                    examples = [row[i] for _, row in leading if i < len(row)]
                    kind = column_kind(str(original or ""), desc, examples)
                    column = {"name": name, "original": str(original or ""), "description": desc, "kind": kind}
                    if kind != "text" and column_kind(str(original or ""), desc, [x for x in examples if not isinstance(x, str)]) == "text" \
                            and value_kind([x for x in examples if x is not None and str(x).strip()]) == kind:
                        column["inferred"] = "values"  # typed from text cells; read keys/labels from __raw
                    columns.append(column)
                schema_fields = []
                for col in columns:
                    kind = col["kind"]
                    schema_fields.append(pa.field(col["name"], pa.decimal128(28, 6) if kind == "number" else pa.timestamp("us") if kind == "datetime" else pa.string()))
                    if kind != "text":
                        schema_fields.append(pa.field(col["name"] + "__raw", pa.string()))
                schema_fields += [pa.field("_source_file", pa.string()), pa.field("_source_sheet", pa.string()), pa.field("_source_row", pa.int64()), pa.field("_exact_duplicate", pa.bool_())]
                schema = pa.schema(schema_fields)
                empty = pa.Table.from_batches([], schema=schema)
                database.register("_incoming", empty)
                database.execute("CREATE TABLE " + quote(table) + " AS SELECT * FROM _incoming")
                database.unregister("_incoming")
                info = {"name": table, "columns": columns, "sources": [], "rows": 0}
                groups[key] = (info, schema)
                catalog["tables"].append(info)
                signatures[table] = set()
                stats[table] = [{"nonempty": 0, "parse_errors": 0, "samples": [], "values": Counter(), "date_min": None, "date_max": None} for _ in columns]
            info, schema = groups[key]
            table = info["name"]
            info["sources"].append({"file": filename, "sheet": sheet})
            year_match = re.findall(r"20\d{2}", sheet + " " + filename)
            year = int(year_match[0]) if year_match else None
            batches = []

            def flush():
                if batches:
                    arrow = pa.Table.from_pylist(batches, schema=schema)
                    database.register("_incoming", arrow)
                    database.execute("INSERT INTO " + quote(table) + " SELECT * FROM _incoming")
                    database.unregister("_incoming")
                    batches.clear()

            for row_number, row in itertools.chain(leading, iterator):
                if not any(value is not None and str(value).strip() for value in row):
                    continue
                if [str(x or "") for x in row[:len(header)]] == [str(x or "") for x in header]:
                    continue
                output = {}
                raw_values = []
                for i, column in enumerate(info["columns"]):
                    value = row[i] if i < len(row) else None
                    raw = None if value is None else str(value)
                    raw_values.append(raw)
                    item_stats = stats[table][i]
                    if raw is not None and raw.strip():
                        item_stats["nonempty"] += 1
                        if len(item_stats["samples"]) < 4 and raw not in item_stats["samples"]:
                            item_stats["samples"].append(raw)
                        if len(item_stats["values"]) <= 32:
                            item_stats["values"][raw] += 1
                    kind = column["kind"]
                    if kind == "number":
                        parsed = parse_number(value)
                        try:
                            parsed = parsed.quantize(Decimal("0.000001")) if parsed is not None else None
                        except InvalidOperation:
                            parsed = None
                    elif kind == "datetime":
                        parsed = parse_datetime(value, year)
                        if parsed is not None:
                            iso = parsed.isoformat()
                            item_stats["date_min"] = min(iso, item_stats["date_min"] or iso)
                            item_stats["date_max"] = max(iso, item_stats["date_max"] or iso)
                    else:
                        parsed = unicodedata.normalize('NFC', raw) if raw is not None else None
                    if kind != "text":
                        output[column["name"] + "__raw"] = raw
                        if raw and raw.strip() and parsed is None:
                            item_stats["parse_errors"] += 1
                    output[column["name"]] = parsed
                signature = hashlib.blake2b(repr(raw_values).encode(), digest_size=16).digest()
                duplicate = signature in signatures[table]
                signatures[table].add(signature)
                output.update(_source_file=filename, _source_sheet=sheet, _source_row=row_number, _exact_duplicate=duplicate)
                batches.append(output)
                info["rows"] += 1
                if len(batches) >= 10000:
                    flush()
            flush()
        # Calendar is trusted input, registered locally, available to SQL for Tet/weekday questions.
        calendar = []
        with (split / "calendar.csv").open() as handle:
            for row in csv.DictReader(handle):
                calendar.append({"date": dt.date.fromisoformat(row["date"]), "weekday": int(row["weekday"]),
                                 "lunar_day": int(row["lunar_day"]), "lunar_month": int(row["lunar_month"]),
                                 "lunar_year": int(row["lunar_year"]), "lunar_leap": row["lunar_leap"] == "true", "holiday": row["holiday"]})
        database.register("_calendar", pa.Table.from_pylist(calendar))
        database.execute("CREATE TABLE calendar AS SELECT * FROM _calendar")
        database.unregister("_calendar")
        conversion_rows = []
        for line in readme.splitlines():
            match = re.match(r"\s+- (.+?):\s*1\s+(.+?)\s*=\s*([\d.,]+)\s+(.+?)\s*$", line)
            if match:
                conversion_rows.append({"product_name": match[1], "selling_unit": match[2], "factor": parse_number(match[3]), "base_unit": match[4]})
                continue
            # '- **Hàng theo bó**: A, B, C bán theo bó, 1 bó = 10 cây; ...': one factor for each listed item.
            listed = re.match(r"\s*-\s*(?:\*\*[^*]+\*\*\s*:\s*)?(.+?)\s+bán theo\s+(\S+?)\s*,\s*1\s+(\S+)\s*=\s*([\d.,]+)\s+([^\s;.,]+)", unicodedata.normalize("NFC", line))
            if listed and listed[2] == listed[3]:
                for name in re.split(r"\s*,\s*|\s+và\s+", listed[1]):
                    if name.strip():
                        conversion_rows.append({"product_name": name.strip(), "selling_unit": listed[3], "factor": parse_number(listed[4]), "base_unit": listed[5]})
        unit_schema = pa.schema([pa.field("product_name", pa.string()), pa.field("selling_unit", pa.string()), pa.field("factor", pa.decimal128(28, 6)), pa.field("base_unit", pa.string())])
        database.register("_units", pa.Table.from_pylist(conversion_rows, schema=unit_schema))
        database.execute("CREATE TABLE unit_conversions AS SELECT * FROM _units")
        database.unregister("_units")
        for info in catalog["tables"]:
            for column, item in zip(info["columns"], stats[info["name"]]):
                values = item.pop("values")
                item["values"] = dict(values) if len(values) <= 32 else None
                column.update(item)
        from .quality import inspect_and_recover
        catalog['quality'] = inspect_and_recover(database, catalog)
        database.execute("CHECKPOINT")
    finally:
        database.close()
    temp_db.replace(destination / "store.duckdb")
    write_json(catalog_path, catalog)
    write_json(destination / 'data_quality.json', {'business_id':business_id,'input_fingerprint':digest,**catalog['quality']})
    return destination
