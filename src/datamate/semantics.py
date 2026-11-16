"""Schema-driven, deterministic semantic tables. No business/question ID branches."""
from __future__ import annotations

from decimal import Decimal
import re

from .ingestion import quote
from .parsing import fold, identifier, name_words, parse_number


ALIASES = {
    "order_id": "order_id ma_don_hang ma_hoa_don ma_don ma_bill bill so_hoa_don ma_phieu so_phieu ma_gd ma_chung_tu so_ct so_don so_hd don_so hd ma_giao_dich",
    "date": "ngay_dat_hang ngay_ban ngay_lap ngay_giao_dich ngay_thang ngay_xuat ngay_hd ngay thoi_gian date thoi_gian_lap",
    "time": "thoi_gian_tao gio_tao_don gio_ban thoi_gian_lap gio",
    "status": "trang_thai_don_hang trang_thai_don trang_thai_xu_ly trang_thai_giao trang_thai tinh_trang_don_hang tinh_trang_don tinh_trang ket_qua status ghi_chu_tt tien_do xu_ly tt_don",
    "notes": "ghi_chu note ghi_chu_tt dien_giai",
    "product_id": "ma_hang ma_hang_hoa ma_sku ma_vach ma_sp ma_hh ma_hang_ban ma_mat_hang ma_san_pham_ban id_sp sku ma_h_hoa ma_mon ma_san_pham barcode",
    "product_name": "ten_san_pham ten_hang ten_hang_hoa ten_sp noi_dung mat_hang hang_ban san_pham chi_tiet_hang mon hang ten_goi hang_hoa product ten_mat_hang",
    "product_alias": "ghi_tat ten_goi_khac",
    "group": "nhom_hang nhom_hang_3_cap nhom_hang_hoa loai_san_pham loai_hang phan_loai danh_muc nhom",
    "quantity": "so_luong so_luong_san_pham sl sl_ban s_l slg sluong so_luong_ban so_luong_hang",
    "base_quantity": "so_luong_quy_doi quy_ra_dv_le so_luong_le sl_le sl_co_ban sl_quy_doi sl_quy_ra_le so_luong_da_quy_doi sl_dv_co_ban",
    "unit": "don_vi_tinh dvt unit don_vi dv_tinh d_vi dv dvt_nhap",
    "unit_price": "don_gia gia_san_pham gia_ban gia_le gia gia_don_vi gia_1_dv price gia_ban_le gia_niem_yet don_gia_d gia_ban_le",
    "revenue": "thanh_tien thanh_tien_d tong_tien tri_gia tien thanh_tien_dong t_tien tien_ban amount cong tong tt",
    "order_revenue": "tong_tien_hang tien_hang gia_tri_hang tien_hang_truoc_giam",
    "order_total": "tong_sau_giam_gia tong_thanh_toan tong_bill tong_tien tong_don phai_thu tong_phieu thanh_tien",
    "unit_cost": "gia_von_luc_ban gia_von",
    "customer_id": "ma_khach_hang ma_kh customer_id ma_the so_kh ma_khach_mua ma_kh_mua ma_nguoi_mua ma_khach_le_si ma_khach",
    "customer_name": "ten_khach_hang khach_hang doi_tac khach ten_kh ten_khach ben_mua kh khach_le_si khach_hang_ten ten_goi khach_hang_mua",
    "customer_group": "nhom_khach_hang loai_khach loai_khach_hang nhom hang_thanh_vien kieu_khach doi_tuong_kh",
    "payment_method": "phuong_thuc_thanh_toan hinh_thuc_thanh_toan thanh_toan_bang phuong_thuc_tt phuong_thuc pttt tra_bang httt",
    "channel": "kenh_ban kenh_ban_hang ban_qua loai_ban nguon_don kenh_dat nguon kenh",
    "employee": "nguoi_ban nhan_vien_ban ma_nhan_vien ma_nv_ban nhan_vien",
    "factor": "ti_le_quy_doi ty_le_quy_doi he_so he_so_qd quy_doi bang he_so_quy_doi",
    "large_unit": "don_vi_quy_doi dv_nhap dv_lon loai_dv quy_cach don_vi_tinh dvt don_vi",
    "base_unit": "don_vi_co_ban don_vi_co_so don_vi_goc dv_goc dv_nho dvt don_vi_tinh don_vi",
    "base_cost": "gia_von_hang gia_von gia_von_hien_tai",
    "balance": "con_no con_lai du_no con_phai_thu chua_tra",
    "receipt_date": "ngay_gio_thu thoi_gian_thu ngay_thu thoi_diem thoi_gian ngay",
    "receipt_amount": "so_tien_thu so_tien tien gia_tri",
    "purchase_id": "ma_phieu_nhap ma_nhap_hang ma_don_nhap so_phieu ma_phieu",
    "purchase_date": "ngay_nhap_kho ngay_nhap thoi_gian ngay",
    "purchase_quantity": "sl_nhap so_luong_nhap so_luong sl",
    "purchase_price": "don_gia_nhap gia_nhap don_gia",
    "purchase_amount": "thanh_tien tien_nhap",
    "stock_quantity": "ton_cuoi_ky ton_cuoi ton_thuc ton_kho ton",
    "event_date": "ngay_gio_phat_sinh",
    "prescription": "ban_theo_don ke_don thuoc_ke_don",
    "delivery_date": "ngay_giao_hang ngay_giao ngay_cho ngay_van_chuyen",
    "delivery_batch": "lan_giao luot_giao dot_giao",
    "delivery_quantity": "so_luong_giao sl_giao",
    "supplier": "nha_cung_cap ten_nha_cung_cap ncc ten_ncc ma_ncc ma_nha_cung_cap",
    "lot": "ma_lo so_lo lo",
    "expiry": "han_su_dung hsd han_dung",
    "lot_quantity": "so_luong_dvt_co_ban sl_nhap_dvt_co_ban sl_nhap_quy_doi sl_quy_doi sl_quy_ra_le so_luong_nhap so_luong sl_nhap sl",
    "lot_return": "so_luong_tra_dvt_co_ban sl_tra_dvt_co_ban sl_tra_quy_doi so_luong_tra sl_tra",
    "ingredient": "hoat_chat thanh_phan_chinh",
}


# README column descriptions use a closed vocabulary ('mã đơn', 'tên khách (Khách lẻ nếu
# không lưu)', 'số lượng đã đổi ra đơn vị cơ bản'). Their head phrase is a second alias
# source that does not depend on how the shop named its headers.
DESCRIBED_EXTRA = {
    "unit_cost": "gia_von_mot_cai gia_von_1_cai gia_von_mot_don_vi gia_von_don_vi gia_von_moi_cai",
    "order_id": "so_thu_tu_don",
    "status": "trang_thai_phieu_nhap",
    "time": "thoi_diem_dat_don gio_dat_don",
    "base_quantity": "so_luong_da_doi_ra_don_vi_co_ban",
    "unit_price": "gia_ban_hien_tai",
    "revenue": "thanh_tien_sau_giam",
    "order_revenue": "tong_tien_hang_cua_don tien_hang_cua_don",
    "order_total": "tong_tien_don so_khach_phai_tra_cho_ca_don thanh_tien_sau_giam",
    "customer_group": "nhom_khach",
    "payment_method": "hinh_thuc_thanh_toan_cua_don",
    "employee": "ma_nhan_vien_ban",
    "large_unit": "don_vi_lon don_vi_nhap",
    "balance": "so_khach_con_no_cua_don",
    "receipt_date": "thoi_diem_thu",
    "purchase_price": "gia_nhap_mot_don_vi",
    "purchase_amount": "tien_nhap_cua_ca_dong thanh_tien_sau_giam",
    "lot_quantity": "so_luong_ban_lay_tu_lo",
    "lot_return": "so_luong_khach_tra_lai",
}
DESCRIBED = {field: names.split() + DESCRIBED_EXTRA.get(field, "").split() for field, names in ALIASES.items()}


def description_head(column):
    """'tên khách (Khách lẻ nếu không lưu)' -> 'ten_khach'."""
    text = fold(column.get("description") or "").strip()
    return identifier(re.split(r"[:;,(]| - ", text)[0]) if text else ""


def described(table, field):
    heads = {}
    for column in table["columns"]:
        heads.setdefault(description_head(column), column["name"])
    return next((heads[name] for name in DESCRIBED[field] if name in heads), None)


def find(table, field):
    columns = {col["name"]: col for col in table["columns"]}
    # README labels resolve field semantics before label aliases.
    patterns = {"order_id": r"^ma don$|^ma don \(|^so thu tu don\b", "product_id": r"^ma hang$|^ma hang \(",
                "product_name": r"^ten hang$|^ten hang \(", "quantity": r"^so luong$", "base_quantity": r"^so luong da doi ra don vi co ban",
                "date": r"^ngay ban$", "customer_name": r"^ten khach \(", "customer_id": r"^ma khach \(",
                "revenue": r"^thanh tien sau giam$", "status": r"^trang thai don$", "unit": r"^don vi tinh$"}
    pattern = patterns.get(field)
    if pattern:
        for column in table["columns"]:
            if re.search(pattern, fold(column.get("description", ""))):
                return column["name"]
    if field == "order_id" and "stt" in columns and "imei" in columns:
        return "stt"
    if field=='customer_group' and 'khach' in table['name'] and 'hang' in columns:
        return 'hang'
    named = next((name for name in ALIASES[field].split() if name in columns), None)
    if field == 'customer_group' and named:
        # One value for every customer ('Đối tượng KH' = 'Cá nhân') groups nobody: take a column that splits customers.
        splitting = [name for name in ALIASES[field].split() if name in columns and len(columns[name].get('values') or {0: 0, 1: 0}) >= 2]
        named = splitting[0] if splitting else named
    if not named and field == 'group' and 'loai' in columns and not any(name in columns for name in ALIASES['quantity'].split()) \
            and not any(c.get('kind') == 'datetime' for c in table['columns']):
        # 'Loại' of a catalogue or abbreviation sheet without dates or quantities (máy mới, máy cũ, phụ kiện) is the product group.
        return 'loai'
    return named or described(table, field)


CONVERSION_TEXT = re.compile(r"(\d+(?:[.,]\d+)?)\s*([^\W\d_][^=;,\d]*?)\s*=\s*(\d+(?:[.,]\d+)?)\s*([^\W\d_][^;,\d]*?)\s*(?=$|[;,])")


SALES_EXCLUDED = ("nhap", "phieu_thu", "thanh_toan", "so_quy", "tra_hang", "chi_phi", "lo_hang", "ban_theo_lo", "don_lo", "don_hang_lo", "giao_hang", "so_han", "cong_no", "han_no", "so_no", "tra_gop", "bao_hanh", "thu_may", "sua_xe", "bao_cao_ca")


def status_values(column, minimum=0.8):
    """Rows covered by recognised order states when the column holds >= 2 distinct states, else 0."""
    from .parsing import status
    values = column.get("values") or {}
    if column["kind"] != "text" or not values:
        return 0
    known = {status(value) for value in values} - {"unknown"}
    covered = sum(count for value, count in values.items() if status(value) != "unknown")
    return covered if len(known) >= 2 and covered >= minimum * sum(values.values()) else 0


def status_column(table):
    """A text column whose recorded values are order states (by value, not by header)."""
    scored = [(status_values(column), column["name"]) for column in table["columns"]]
    best = max(scored, default=(0, None))
    return best[1] if best[0] else None


KEY_FIELDS = ("order_id", "product_id", "product_name", "customer_id", "customer_name", "unit", "employee")
# Fields that share header names in ALIASES ('don_vi_tinh' is unit, large_unit and base_unit;
# 'so_luong' is quantity, purchase_quantity and lot_quantity), derived the same way.
SHARED_FIELDS = {"large_unit": "unit", "base_unit": "unit", "purchase_quantity": "quantity", "lot_quantity": "quantity",
                 "purchase_price": "unit_price", "purchase_amount": "revenue", "purchase_date": "date", "receipt_date": "date"}


def complete_roles(tables, mapping):
    """Deterministic completion of a model-filled mapping; returns the number of fields added.

    A column name used for a key role in one table has that role wherever the same name
    appears in the shop (the model often labels a join key in only one of the two tables).
    """
    added = 0
    names = {}
    for table in tables:
        for field in KEY_FIELDS:
            column = mapping[table["name"]].get(field)
            if column:
                names.setdefault(column, set()).add(field)
    for table in tables:
        current = mapping[table["name"]]
        present = {c["name"] for c in table["columns"] if c.get("nonempty", 1)}
        for column, fields in names.items():
            if len(fields) == 1 and column in present and column not in current.values():
                field = next(iter(fields))
                if not current.get(field):
                    current[field] = column
                    added += 1
        for field, source in SHARED_FIELDS.items():
            if not current.get(field) and current.get(source):
                current[field] = current[source]
                added += 1
        if "nhap" in table["name"] and current.get("order_id") and not current.get("purchase_id"):
            current["purchase_id"] = current["order_id"]
            added += 1
    return added


def has_sales_source(tables, mapping):
    candidates = [t for t in tables if not any(word in name_words(t["name"]) for word in SALES_EXCLUDED)]
    lines = [t for t in candidates if mapping[t["name"]]["quantity"] and mapping[t["name"]]["revenue"] and (mapping[t["name"]]["product_name"] or mapping[t["name"]]["product_id"])]
    headers = [t for t in candidates if mapping[t["name"]]["order_id"] and mapping[t["name"]]["date"] and not mapping[t["name"]]["quantity"] and (mapping[t["name"]]["order_revenue"] or mapping[t["name"]]["order_total"])]
    return bool(lines or headers)


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


# 'Trà sữa (M) + 2 Trân châu đen (14.000đ)': a drink and its toppings written in one cell, topping money in brackets.
TOPPING_PART = r"^(?:(\d+)\s+)?(.+?)\s*\(([^)]*\d[^)]*)\)\s*$"


def split_toppings(con, source_sql, columns):
    """Rulebook §6 Cà phê: a topping written in the drink's cell is its own item (the same item as the topping sold on
    its own line): its portions ('2 Trân châu đen', none written = 1) and the money in brackets go to the topping, the
    rest of the line to the drink. Only cells whose every part after ' + ' carries its money in brackets are split."""
    combined = (f"list_bool_and(list_transform(string_split(product_name,' + ')[2:],lambda x: regexp_matches(trim(x),{literal(TOPPING_PART)})))"
                " AND len(string_split(product_name,' + '))>1")
    count = con.execute(f"SELECT COUNT(*) FROM ({source_sql}) WHERE product_name LIKE '% + %' AND {combined}").fetchone()[0]
    if not count:
        return source_sql
    cols = ",".join(columns)
    parts = (f"SELECT *,unnest(string_split(product_name,' + ')) part,generate_subscripts(string_split(product_name,' + '),1) idx "
             f"FROM (SELECT *,row_number() OVER () _line FROM s WHERE product_name LIKE '% + %' AND {combined})")
    tops = (f"SELECT *,COALESCE(TRY_CAST(NULLIF(regexp_extract(trim(part),{literal(TOPPING_PART)},1),'') AS INTEGER),1) portions,"
            f"trim(regexp_extract(trim(part),{literal(TOPPING_PART)},2)) top_name,to_num(regexp_extract(trim(part),{literal(TOPPING_PART)},3)) top_money FROM p WHERE idx>1")
    # The topping's own item: the name sold on its own line, with or without the word 'Topping'.
    known = ("SELECT norm(regexp_replace(product_name,'(?i)^\\s*topping\\s+','')) k,FIRST(product_name ORDER BY product_name) name,FIRST(product_id) pid,"
             "FIRST(product_group) grp,FIRST(unit) unit,FIRST(base_unit) bunit FROM plain GROUP BY 1")
    drinks = "SELECT norm(product_name) k,FIRST(product_group) grp FROM plain WHERE product_group IS NOT NULL GROUP BY 1"
    main = ("SELECT p.* REPLACE(trim(p.part) AS product_name,p.revenue-COALESCE(t.money,0) AS revenue,COALESCE(p.product_group,d.grp) AS product_group,"
            "COALESCE(p.quantity*unit_factor(p.product_id,trim(p.part),p.unit),p.base_quantity) AS base_quantity) "
            "FROM p LEFT JOIN (SELECT _line,SUM(top_money) money FROM tops GROUP BY 1) t USING(_line) LEFT JOIN drinks d ON d.k=norm(trim(p.part)) WHERE p.idx=1")
    topping = ("SELECT t.* REPLACE(COALESCE(k.name,t.top_name) AS product_name,k.pid AS product_id,k.grp AS product_group,COALESCE(k.unit,'phần') AS unit,"
               "COALESCE(k.bunit,k.unit,'phần') AS base_unit,t.portions::DECIMAL(28,6) AS quantity,t.portions::DECIMAL(28,6) AS base_quantity,t.top_money AS revenue,"
               "t.top_money/t.portions AS unit_price,NULL::DECIMAL(28,6) AS unit_cost) FROM tops t LEFT JOIN known k ON k.k=norm(t.top_name)")
    return (f"WITH s AS ({source_sql}),plain AS (SELECT * FROM s WHERE NOT (product_name LIKE '% + %' AND {combined}) OR product_name IS NULL),"
            f"p AS ({parts}),tops AS ({tops}),known AS ({known}),drinks AS ({drinks}) "
            f"SELECT {cols} FROM plain UNION ALL SELECT {cols} FROM ({main}) UNION ALL SELECT {cols} FROM ({topping})")


def attach_semantics(engine, catalog):
    con = engine.connection
    tables = catalog["tables"]
    mapping = {table["name"]: {field: find(table, field) for field in ALIASES} for table in tables}
    reports = {"tables": {}, "capabilities": {}, "warnings": []}
    roles = catalog.get("llm_roles") or {}
    if roles.get("tables") and roles.get("domain") != "vehicle_repair" and not has_sales_source(tables, mapping):
        # Unseen headers: the model's column roles fill only fields no alias matched.
        filled = 0
        for table in tables:
            current = mapping[table["name"]]
            for field, column in (roles["tables"].get(table["name"]) or {}).items():
                if field in ALIASES and not current.get(field) and column not in current.values():
                    if field == "status" and not status_values(next(c for c in table["columns"] if c["name"] == column)):
                        continue  # e.g. 'STT' (row number) proposed as status: values decide
                    current[field] = column
                    filled += 1
        filled += complete_roles(tables, mapping)
        for table in tables:
            if not mapping[table["name"]].get("status"):
                column = status_column(table)
                if column:
                    mapping[table["name"]]["status"] = column
                    filled += 1
            current = mapping[table["name"]]
            if current.get("quantity") and not current.get("base_quantity"):
                # A second quantity column labelled 'cơ bản/quy đổi' is the base-unit quantity.
                based = [c["name"] for c in table["columns"] if c["kind"] == "number" and c["name"] != current["quantity"] and c.get("nonempty")
                         and re.search(r"co ban|quy doi|quy ra|dv le|don vi le", fold(c["original"]) + " " + c["name"].replace("_", " "))]
                if len(based) == 1:
                    current["base_quantity"] = based[0]
                    filled += 1
        reports["warnings"].append(f"No alias-matched sales source; {filled} column roles filled from the model's assignment and value checks.")
        reports["role_fallback"] = filled

    def text(alias, table, field, default="NULL"):
        column = mapping[table["name"]].get(field)
        if column:
            info = next(c for c in table['columns'] if c['name']==column)
            # Labels, and keys typed only from their text cells, are read as recorded.
            if info['kind']!='text' and (field in {'status','notes'} or info.get('inferred')=='values'):
                column += '__raw'
        return f"NULLIF(TRIM(CAST({alias}.{quote(column)} AS VARCHAR)), '')" if column else default

    def numeric(alias, table, field, default="NULL"):
        column = mapping[table["name"]].get(field)
        if not column:
            return default
        info = next(c for c in table["columns"] if c["name"] == column)
        return f"{alias}.{quote(column)}" if info["kind"] == "number" else f"to_num(CAST({alias}.{quote(column)} AS VARCHAR))"

    def timestamp(alias, table, field):
        column = mapping[table["name"]].get(field)
        if not column:
            return "NULL::TIMESTAMP"
        info = next(c for c in table["columns"] if c["name"] == column)
        return f"{alias}.{quote(column)}" if info["kind"] == "datetime" else f"CAST(to_day(CAST({alias}.{quote(column)} AS VARCHAR)) AS TIMESTAMP)"

    def create(name, sql, source):
        con.execute(f"CREATE TEMP TABLE {name} AS {sql}")
        engine.tables.add(name)
        reports["tables"][name] = {"source": source, "rows": con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0],
                                   "columns": [(item[0], str(item[1])) for item in con.execute(f"SELECT * FROM {name} LIMIT 0").description]}

    # Product/customer master detection is structural and semantic, never by ID.
    # A pure conversion table (name, unit, factor) is not the product master; a catalogue with a conversion column is.
    products = [t for t in tables if mapping[t["name"]]["product_name"] and not mapping[t["name"]]["quantity"] and not (mapping[t["name"]]["factor"] and not (mapping[t["name"]]["group"] or mapping[t["name"]]["product_alias"])) and not mapping[t['name']]['date'] and not re.search(r'nhap|hoa_don|bang_gia|lich_su',t['name'])
                and any(word in name_words(t["name"]) for word in ("san_pham", "hang_hoa", "danh_muc", "hang", "ghi_tat"))]
    products.sort(key=lambda t: bool(mapping[t["name"]]["product_id"]) * 3 + bool(mapping[t["name"]]["group"]) * 2, reverse=True)
    product_master = products[0] if products else None
    if product_master:
        p = product_master
        create("v_products", f"SELECT {text('p',p,'product_id')} product_id, {text('p',p,'product_name')} product_name, {text('p',p,'product_alias')} product_alias, {text('p',p,'group')} product_group, {text('p',p,'base_unit')} base_unit,{numeric('p',p,'base_cost')} base_cost FROM {quote(p['name'])} p QUALIFY ROW_NUMBER() OVER(PARTITION BY COALESCE({text('p',p,'product_id')},{text('p',p,'product_name')}) ORDER BY _source_row)=1", [p["name"]])
    customers = [t for t in tables if "khach" in t["name"] and mapping[t["name"]]["customer_name"] and not mapping[t["name"]]["quantity"]]
    customer_master = customers[0] if customers else None
    if customer_master:
        c = customer_master
        create("v_customers", f"SELECT {text('c',c,'customer_id')} customer_id, {text('c',c,'customer_name')} customer_name, {text('c',c,'customer_group')} customer_group FROM {quote(c['name'])} c QUALIFY ROW_NUMBER() OVER(PARTITION BY COALESCE({text('c',c,'customer_id')},{text('c',c,'customer_name')}) ORDER BY _source_row)=1", [c["name"]])
    for staff in tables:
        if 'nhan_vien' in staff['name'] and mapping[staff['name']]['employee'] and mapping[staff['name']]['customer_name']:
            create('v_employees',f"SELECT {text('n',staff,'employee')} employee_id,{text('n',staff,'customer_name')} employee_name FROM {quote(staff['name'])} n",[staff['name']])
            break

    # Small unit graphs can come from README or a conversion table; resolve chained units.
    unit_edges = {}
    base_units = {}
    for name, unit, factor, base in con.execute("SELECT * FROM unit_conversions").fetchall():
        unit_edges[(fold(name).strip(), fold(unit).strip())] = (Decimal(str(factor)), fold(base).strip())
        base_units[fold(name).strip()] = base
    for t in tables:
        m = mapping[t["name"]]
        if m["factor"] and (m["product_id"] or m["product_name"]) and m["large_unit"]:
            key = text("u", t, "product_id") if m["product_id"] else text("u", t, "product_name")
            factor_col = next(c for c in t["columns"] if c["name"] == m["factor"])
            raw_factor = f"u.{quote(m['factor']+'__raw')}" if factor_col["kind"] != "text" else f"u.{quote(m['factor'])}"
            query = f"SELECT {key}, {text('u',t,'large_unit')}, {raw_factor}, {text('u',t,'base_unit')} FROM {quote(t['name'])} u"
            for product, unit, raw, base in con.execute(query).fetchall():
                pairs = CONVERSION_TEXT.findall(str(raw or ""))
                if product and pairs:
                    # 'Quy đổi' written out per product: '1 thùng = 4 bịch; 1 lốc = 4 gói'.
                    for big_count, big, small_count, small in pairs:
                        big_n, small_n = parse_number(big_count), parse_number(small_count)
                        if big_n and small_n:
                            unit_edges[(fold(product).strip(), fold(big).strip())] = (Decimal(str(small_n)) / Decimal(str(big_n)), fold(small).strip())
                    if base:
                        base_units[fold(product).strip()] = base
                    continue
                factor = parse_number(raw)
                if not product or not unit or factor is None:
                    continue
                tail = re.sub(r"^[\s\d.,]+", "", str(raw or "")).strip()
                target = tail or base or ""
                unit_edges[(fold(product).strip(), fold(unit).strip())] = (factor, fold(target).strip())
                if base:
                    base_units[fold(product).strip()] = base

    def factor_for(product_id, product_name, unit):
        for key in [fold(product_id).strip(), fold(product_name).strip()]:
            current, visited, factor = fold(unit).strip(), set(), Decimal(1)
            while (key, current) in unit_edges and current not in visited:
                visited.add(current)
                multiplier, target = unit_edges[key, current]
                factor *= multiplier
                current = target
            if visited:
                return factor
        return Decimal(1)

    def base_for(product_id, product_name, unit):
        return base_units.get(fold(product_id).strip(), base_units.get(fold(product_name).strip(), unit))

    con.create_function("unit_factor", factor_for, ["VARCHAR", "VARCHAR", "VARCHAR"], "DECIMAL(28,6)", null_handling="special")
    con.create_function("unit_base", base_for, ["VARCHAR", "VARCHAR", "VARCHAR"], "VARCHAR", null_handling="special")

    # Repair books (plate + job/mechanic, one row per visit) are not retail order sources.
    from .domains import attach_repair, attach_retail_domains
    try:
        repair_book = attach_repair(engine, catalog, create, reports)
    except Exception as error:
        repair_book = None
        reports["warnings"].append("Repair book normalization incomplete: " + str(error)[:1000])
    excluded = SALES_EXCLUDED
    candidates = [t for t in tables if not any(word in name_words(t["name"]) for word in excluded) and t["name"] != repair_book]
    headers = [t for t in candidates if mapping[t["name"]]["order_id"] and mapping[t["name"]]["date"] and not mapping[t["name"]]["quantity"] and (mapping[t["name"]]["order_revenue"] or mapping[t["name"]]["order_total"])]
    lines = [t for t in candidates if mapping[t["name"]]["quantity"] and mapping[t["name"]]["revenue"] and (mapping[t["name"]]["product_name"] or mapping[t["name"]]["product_id"])]
    lines.sort(key=lambda t: t["rows"], reverse=True)
    header = max(headers, key=lambda t: t["rows"], default=None)
    line = lines[0] if lines else None
    source = line or header
    if source is None:
        reports["warnings"].append("No supported retail order/line source; use raw specialist SQL.")
        catalog["semantic"] = reports
        return reports
    m = mapping[source["name"]]
    has_order_id = bool(m["order_id"])
    join_header = bool(line and not m["date"] and header and has_order_id)
    if not m["date"] and not join_header:
        reports["warnings"].append("No order date or supported header join; use raw SQL.")
        catalog["semantic"] = reports
        return reports
    alias = "l"
    joins = ""
    if join_header:
        joins += f" LEFT JOIN {quote(header['name'])} h ON {text('l',source,'order_id')}={text('h',header,'order_id')} AND NOT h._exact_duplicate"

    def inherited(field, numeric_field=False):
        getter = numeric if numeric_field else text
        left = getter(alias, source, field)
        if field == "order_total":
            if join_header:
                return getter('h', header, field)
            if line and m[field] == m['revenue']:
                return "NULL"
        return f"COALESCE({left},{getter('h',header,field)})" if join_header else left

    order_date = timestamp("h", header, "date") if join_header else timestamp(alias, source, "date")
    order_time = timestamp("h", header, "time") if join_header else timestamp(alias, source, "time")
    pid, pname = inherited("product_id"), inherited("product_name")
    group = inherited("group")
    base_unit = "NULL"
    if product_master:
        if m["product_id"]:
            condition = f"{pid}=p.product_id"
        else:
            condition = f"norm({pname})=norm(p.product_name) OR (p.product_alias IS NOT NULL AND norm({pname})=norm(p.product_alias))"
        joins += " LEFT JOIN v_products p ON " + "(" + condition + ")"
        pid, pname = f"COALESCE({pid},p.product_id)", f"COALESCE(p.product_name,{pname})"
        group, base_unit = f"COALESCE({group},p.product_group)", "p.base_unit"
    cid, cname, cgroup = inherited("customer_id"), inherited("customer_name"), inherited("customer_group")
    if customer_master and (m["customer_id"] or join_header):
        joins += f" LEFT JOIN v_customers c ON {cid}=c.customer_id"
        cname, cgroup = f"COALESCE(c.customer_name,{cname})", f"COALESCE(c.customer_group,{cgroup})"
    raw_status = inherited("status")
    if raw_status == "NULL" or not (m["status"] or join_header and mapping[header["name"]]["status"]):
        raw_status = inherited("notes")
    quantity = numeric(alias, source, "quantity") if line else "NULL"
    revenue = numeric(alias, source, "revenue") if line else numeric(alias, source, "order_revenue")
    unit = text(alias, source, "unit")
    base_quantity = numeric(alias, source, "base_quantity")
    if line:
        base_quantity = f"COALESCE({base_quantity}, {quantity}*unit_factor({pid},{pname},{unit}))"
        base_unit = f"COALESCE({base_unit},unit_base({pid},{pname},{unit}))"
    fields = {
        "order_id": inherited("order_id"), "order_date": order_date, "order_time": order_time,
        "status_rank": f"CASE WHEN ({quantity}<0 AND NOT regexp_matches(norm(COALESCE({pname},'')),'^(?:thu|tru|tro gia)\\s+(?:may cu|cu)\\b|^may cu\\b|^doi may cu\\b')) OR stat({raw_status})='returned' THEN 3 WHEN stat({raw_status})='cancelled' THEN 4 WHEN stat({raw_status})='shipping' THEN 2 ELSE 1 END",
        "customer_id": cid, "customer_name": cname, "customer_group": cgroup,
        "product_id": pid, "product_name": pname, "product_group": group, "unit": unit, "base_unit": base_unit,
        "quantity": quantity, "base_quantity": base_quantity, "revenue": revenue,
        "unit_price": numeric(alias, source, "unit_price"), "unit_cost": numeric(alias, source, "unit_cost"),
        "order_total": inherited("order_total", True), "payment_method": f"pay_method({inherited('payment_method')})",
        "channel": inherited("channel"), "employee_id": inherited("employee"),
    }
    source_sql = "SELECT " + ",".join(f"{value} AS {name}" for name, value in fields.items()) + f" FROM {quote(source['name'])} l" + joins
    if line:
        usable = f"COALESCE({text('l',source,'product_id')},{text('l',source,'product_name')}) IS NOT NULL AND NOT regexp_matches(norm({text('l',source,'product_name')}),'^(tong$|cong$|tong ngay|tong thang|tong cong|cong ngay|cong thang|ghi chu:)')"
    else:
        usable = f"{text('l',source,'order_id')} IS NOT NULL AND {timestamp('l',source,'date')} IS NOT NULL"
    segmented_source = f"SELECT *,SUM(CASE WHEN {usable} THEN 0 ELSE 1 END) OVER(PARTITION BY _source_file,_source_sheet ORDER BY _source_row ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS _carry_segment FROM {quote(source['name'])} l"
    filtered_source = f"SELECT * FROM ({segmented_source}) l WHERE {usable}"
    if has_order_id:
        # Blank order IDs carry the preceding ID within the same physical sheet.
        # Recompute duplicates AFTER that carry: identical blank lines from two
        # different orders must remain separate sales.
        order_column = quote(m['order_id'])
        filled_source = "SELECT * REPLACE(LAST_VALUE(NULLIF(TRIM(CAST(" + order_column + " AS VARCHAR)), '') IGNORE NULLS) OVER(PARTITION BY _source_file,_source_sheet,_carry_segment ORDER BY _source_row ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS " + order_column + ") FROM (" + filtered_source + ")"
        payload = [quote(c['name']) for c in source['columns']]
        normalized_source = "SELECT * EXCLUDE(_exact_duplicate),ROW_NUMBER() OVER(PARTITION BY " + ','.join(payload) + " ORDER BY _source_file,_source_sheet,_source_row)>1 AS _exact_duplicate FROM (" + filled_source + ")"
        source_sql = source_sql.replace(f"FROM {quote(source['name'])} l", "FROM (" + normalized_source + ") l")
        source_sql += " WHERE NOT l._exact_duplicate AND " + text('l',source,'order_id') + ' IS NOT NULL'
    else:
        source_sql = source_sql.replace(f"FROM {quote(source['name'])} l", "FROM (" + filtered_source + ") l")
    if line:
        source_sql = split_toppings(con, source_sql, list(fields))
    create("v_sales_source", source_sql, [source["name"]] + ([header["name"]] if join_header else []))
    filled = "SELECT * EXCLUDE(status_rank), status_rank FROM v_sales_source"
    if has_order_id:
        passthrough = [name for name in fields if name not in {"order_date", "order_time", "status_rank", "customer_id", "customer_name", "customer_group"}]
        filled = "SELECT " + ",".join(passthrough) + ",MIN(order_date) OVER(PARTITION BY order_id) order_date, MIN(order_time) OVER(PARTITION BY order_id) order_time,MAX(status_rank) OVER(PARTITION BY order_id) status_rank,"
        filled += ",".join(f"FIRST_VALUE({name} IGNORE NULLS) OVER(PARTITION BY order_id ORDER BY order_date ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) {name}" for name in ["customer_id", "customer_name", "customer_group"])
        filled += " FROM v_sales_source"
    create("v_sales_all", "SELECT *, CASE WHEN status_rank=4 THEN 'cancelled' WHEN status_rank=3 THEN 'returned' WHEN status_rank=2 THEN 'shipping' ELSE 'completed' END status, COALESCE(customer_id,CASE WHEN NOT is_guest(customer_name) THEN regexp_replace(norm(customer_name),'[^a-z0-9]+','','g') END) customer_key, norm(COALESCE(product_name,'')) NOT IN ('voucher','phi ship') AND NOT starts_with(norm(COALESCE(product_name,'')),'dung ') AND NOT regexp_matches(norm(COALESCE(product_name,'')),'^(?:giam gia |tien giam |giam |ma giam gia )?voucher\\b|^(?:phi|tien)\\s+(?:ship|giao hang|giao|van chuyen)\\b|^(?:tru|doi)\\s+(?:\\d+\\s+)?diem\\b') AND NOT regexp_matches(norm(COALESCE(product_name,'')),'^(?:thu|tru|tro gia)\\s+(?:may cu|cu)\\b|^may cu\\b|^doi may cu\\b') is_item FROM (" + filled + ") x", [source["name"]])
    create("v_sales", "SELECT * FROM v_sales_all WHERE status_rank<3 AND is_item", ["v_sales_all"])
    if has_order_id:
        query = "SELECT order_id,MIN(order_date) order_date, MIN(order_time) order_time,MAX(status_rank) status_rank,FIRST(status) status,FIRST(customer_id) customer_id,FIRST(customer_name) customer_name,FIRST(customer_key) customer_key,FIRST(customer_group) customer_group,SUM(CASE WHEN is_item THEN revenue ELSE 0 END) revenue,MAX(order_total) order_total,FIRST(payment_method) payment_method,FIRST(channel) channel,FIRST(employee_id) employee_id FROM v_sales_all GROUP BY order_id"
        create("v_orders_all", query, ["v_sales_all"])
        create("v_orders", "SELECT * FROM v_orders_all WHERE status_rank<3", ["v_orders_all"])
    reports["capabilities"] = {"sales": bool(line), "orders": has_order_id, "revenue": True, "quantity": bool(line),
                               "customers": bool(m["customer_name"] or m["customer_id"] or join_header and (mapping[header['name']]['customer_id'] or mapping[header['name']]['customer_name'])), "unit_cost": bool(m["unit_cost"]),
                               "order_time": bool(m["time"] or join_header and mapping[header["name"]]["time"]), "tickets": "v_tickets" in engine.tables}
    reports["mapping"] = {source["name"]: m}
    catalog["semantic"] = reports
    from .specialty import attach_business_tables
    try:
        attach_business_tables(engine,catalog,reports,create,text,numeric,timestamp,mapping,source,header,join_header)
        reports['specialty_ready']=True
    except Exception as error:
        reports['specialty_ready']=False
        reports['warnings'].append('Specialist normalization incomplete: '+str(error)[:1000])
    try:
        attach_retail_domains(engine, catalog, reports, create, mapping, source, header, join_header)
    except Exception as error:
        reports['warnings'].append('Domain normalization incomplete: '+str(error)[:1000])
    return reports
