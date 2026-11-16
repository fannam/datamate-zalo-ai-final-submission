"""Schema-driven local business tables and narrow deterministic specialist plans."""
from __future__ import annotations

import calendar
import datetime as dt
import re

from .ingestion import quote
from .parsing import fold, name_words
from .temporal import resolve_periods
from .types import SQLPlan


def literal(value):
    return "'"+str(value).replace("'","''")+"'"


def plan(sql='',kind='number',shape='scalar',reason='',rounding='money',action='query'):
    return SQLPlan(action=action,sql=sql,answer_type=kind if action=='query' else action,shape=shape,rounding=rounding,date_granularity='day',reason=reason[:300])


def missing(reason):
    return plan(action='unanswerable',reason=reason,rounding='none')


# Words in a table's name or columns that say it may hold this kind of record.
CONCEPTS={
    'debt':{'tables':r'(?:^|_)(?:no|cong_no|so_quy|phieu_thu|thu_chi|thanh_toan|tra_gop|gop|so_no)(?:_|$)','columns':r'(?:^|_)(?:con_no|du_no|so_tien_thu|tien_thu|da_tra|da_thu|chua_tra|tra_no|thu_no)(?:_|$)'},
    'stock':{'tables':r'(?:^|_)(?:ton|kho|ton_kho|the_kho)(?:_|$)','columns':r'^(?:ton|ton_kho|ton_cuoi|ton_cuoi_ky|ton_thuc|so_luong_ton|sl_ton)$'},
    'purchase':{'tables':r'(?:^|_)(?:nhap|nhap_hang|phieu_nhap|ncc)(?:_|$)','columns':r'(?:^|_)(?:gia_nhap|sl_nhap|so_luong_nhap|ngay_nhap)(?:_|$)'},
    'lots':{'tables':r'(?:^|_)(?:lo|lo_hang|han_dung|hsd)(?:_|$)','columns':r'(?:^|_)(?:han_su_dung|han_dung|hsd|ma_lo|so_lo)(?:_|$)'},
}


CONCEPT_VIEWS={'debt':('v_receipts','v_finance','v_installments'),'stock':('v_stock_snapshot','v_stock_events','v_lots','v_lot_moves'),'purchase':('v_purchases','v_lots'),'lots':('v_lots','v_lot_moves')}


def unread_tables(catalog,concept):
    """Non-empty tables that suggest `concept` but feed none of its normalized views.

    A stock list used only as the product master still has unread stock columns.
    """
    views=(catalog.get('semantic') or {}).get('tables') or {}
    used=set()
    for name in CONCEPT_VIEWS[concept]:
        used.update((views.get(name) or {}).get('source') or [])
    words=CONCEPTS[concept]

    def records(table):
        # Holds amounts/quantities, not only terms: 'số ngày được nợ' + 'hạn trả' is a credit-terms list, not payments.
        columns=[c for c in table['columns'] if c.get('nonempty')]
        amounts=any(c['kind']=='number' and not re.search(r'(?:^|_)(?:ngay|han|ky|stt|thu_tu)(?:_|$)',c['name']) for c in columns)
        return amounts and (concept!='debt' or any(c['kind']=='datetime' for c in columns))
    return [t['name'] for t in catalog['tables'] if t['name'] not in used and t.get('rows') and records(t) and
            (re.search(words['tables'],name_words(t['name'])) or any(re.search(words['columns'],c['name']) for c in t['columns'] if c.get('nonempty')))]


def missing_source(catalog,concept,reason):
    """'The shop does not record X' only when no unrecognised table could hold X; otherwise the source is unread, not absent."""
    unread=unread_tables(catalog,concept)
    if unread:
        from .domains import UnreadableSource
        raise UnreadableSource(f"{concept} may be recorded in unrecognised tables {unread[:4]}")
    return missing(reason)


MONEY=r'(\d[\d.,]*\s*(?:k|tr(?:ieu)?|trieu|nghin|ngan|d|dong)?\s*\d*)'


def note_balance(note,total):
    """Amount still owed written in a debt note: paid in full -> 0; 'còn thiếu / thiếu lại / nợ / ghi sổ Y' -> Y;
    'nợ cả đơn' -> the order total; 'đưa trước X, còn lại ghi sổ' -> total − X. Unreadable notes -> NULL."""
    from .parsing import parse_number
    if note is None:
        return None
    text=re.sub(r'\s+',' ',fold(note)).strip()
    amount=lambda m:float(parse_number(m.strip())) if m and parse_number(m.strip()) is not None else None
    owed=re.search(r'(?:con thieu|thieu lai|thieu|con no|con lai|no lai)\s+'+MONEY,text)
    if owed and amount(owed[1]) is not None:
        return amount(owed[1])
    if re.search(r'tra (?:du|het)|ck du|chuyen du|du roi|het roi|da thanh toan|tat toan|tra xong',text):
        return 0.0
    if re.search(r'no ca don|chua tra(?!.*\d)',text) and total is not None:
        return total
    prepaid=re.search(r'(?:dua truoc|tra truoc|ck|tra)\s+'+MONEY+r'.*con lai (?:ghi so|ghi no|no)',text)
    if prepaid and total is not None and amount(prepaid[1]) is not None:
        return max(total-amount(prepaid[1]),0.0)
    last=None
    for m in re.finditer(r'(?:ghi no|ghi so|\bno)\s+'+MONEY,text):
        last=amount(m[1])
    if last is not None:
        return last
    return None


def attach_business_tables(engine,catalog,reports,create,text,num,timestamp,mapping,sales_source,sales_header,join_header):
    tables=catalog['tables']

    def products(table,alias='s'):
        pid,pname=text(alias,table,'product_id'),text(alias,table,'product_name')
        joins='';cost='NULL';base=text(alias,table,'base_unit')
        if 'v_products' in engine.tables:
            condition=f"{pid}=p.product_id" if mapping[table['name']]['product_id'] else f"norm({pname})=norm(p.product_name)"
            joins=' LEFT JOIN v_products p ON '+condition
            pid,pname=f'COALESCE({pid},p.product_id)',f'COALESCE(p.product_name,{pname})'
            base,cost=f'COALESCE(p.base_unit,{base})','p.base_cost'
        return pid,pname,base,cost,joins

    def complete_receipts(table,m):
        """Payment book whose headers matched no alias: order key by its values, the only date, the only amount."""
        columns=[c for c in table['columns'] if c.get('nonempty')]

        def overlap(c):
            value=f"NULLIF(TRIM(CAST(r.{quote(c['name']+'__raw' if c['kind']!='text' else c['name'])} AS VARCHAR)),'')"
            _,rows=engine.lookup(f"SELECT COUNT(x),COUNT(x) FILTER(WHERE x IN (SELECT order_id FROM v_orders_all)) FROM (SELECT {value} x FROM {quote(table['name'])} r LIMIT 2000)")
            return rows[0][1]/rows[0][0] if rows[0][0]>=3 else 0
        if 'v_orders_all' in engine.tables:
            current=next((c for c in columns if c['name']==m['order_id']),None)
            # The key must hold sales order ids: a receipt number ('PT0001') proposed as the key matches nothing.
            if current is None or overlap(current)<0.5:
                best=max(((overlap(c),c['name']) for c in columns if c['kind']!='datetime'),default=(0,None))
                m['order_id']=best[1] if best[0]>=0.9 else (m['order_id'] if current is not None and overlap(current)>0 else None)
        if not m['receipt_date']:
            dates=[c['name'] for c in columns if c['kind']=='datetime']
            if len(dates)==1:
                m['receipt_date']=dates[0]
        if not m['receipt_amount']:
            amounts=[c['name'] for c in columns if c['kind']=='number' and c['name']!=m['order_id'] and not re.search(r'(?:^|_)(?:ngay|han|ky|stt|thu_tu|lan)(?:_|$)',c['name'])]
            named=[a for a in amounts if re.search(r'tien|thu|tri|gia|amount|value|paid',a)]
            pick=amounts if len(amounts)==1 else named if len(named)==1 else []
            if pick:
                m['receipt_amount']=pick[0]

    for table in tables:
        m=mapping[table['name']]
        # Receipt books: payment slips, cash book, and debt-collection books ('thu nợ', 'trả nợ').
        books=['thanh_toan','phieu_thu','so_quy','thu_no','thuno','tra_no','trano']
        if any(word in name_words(table['name']) for word in books):
            complete_receipts(table,m)
        if any(word in name_words(table['name']) for word in books) and m['order_id'] and m['receipt_date'] and m['receipt_amount']:
            create('v_receipts',f"SELECT {text('r',table,'order_id')} order_id,{timestamp('r',table,'receipt_date')} paid_at,{num('r',table,'receipt_amount')} amount FROM {quote(table['name'])} r",[table['name']])
            break
    for table in tables:
        m=mapping[table['name']]
        if 'thuoc' in table['name'] and (m['prescription'] or m['ingredient']) and (m['product_id'] or m['product_name']):
            pid,name,base,cost,joins=products(table,'s')
            rx=text('s',table,'prescription')
            create('v_drugs',f"SELECT {pid} product_id,{name} product_name,(norm({rx}) IN ('true','co','yes','ke don') OR COALESCE(TRY_CAST({rx} AS DOUBLE)=1,FALSE)) prescription,{text('s',table,'ingredient')} ingredient FROM {quote(table['name'])} s"+joins,[table['name']])
            break
    # Price list with change dates (rulebook 'Hiệu ứng tăng giá'): the new price applies from its date; the old price may be
    # written or is the previous row of the same item. An item may be named, or only coded and named by the catalog.
    for table in tables:
        if 'gia' not in name_words(table['name']).split('_'):
            continue
        cols={c['name']:c for c in table['columns'] if c.get('nonempty')}
        day=next((n for n,c in cols.items() if c['kind']=='datetime'),None)
        new=next((n for n in ('gia_moi','gia_ban_le','gia_ban','don_gia','gia') if n in cols and cols[n]['kind']=='number'),None)
        old=next((n for n in ('gia_cu','gia_truoc') if n in cols and cols[n]['kind']=='number'),None)
        named=next((n for n,c in cols.items() if c['kind']=='text' and re.search(r'ten_hang|san_pham|^item$|mat_hang|ten_sp|dien_giai|^hang_hoa$',n)
                    and not re.search(r'^ma_|^id_|_id$',n)),None)
        coded=next((n for n,c in cols.items() if c['kind']=='text' and n!=named and re.search(r'^ma_|_id$|^id_',n) and 'cua_hang' not in n),None)
        if not (day and new and (named or (coded and 'v_products' in engine.tables))):
            continue
        product=f"CAST(g.{quote(named)} AS VARCHAR)" if named else 'p.product_name'
        code=f"CAST(g.{quote(coded)} AS VARCHAR)" if coded else 'NULL'
        joins='' if named else f" LEFT JOIN v_products p ON p.product_id=CAST(g.{quote(coded)} AS VARCHAR)"
        key=f"COALESCE({code},norm({product}))"
        previous=f"LAG(g.{quote(new)}) OVER (PARTITION BY {key} ORDER BY g.{quote(day)})"
        create('v_price_changes',f"SELECT {code} product_id,{product} product_name,CAST(g.{quote(day)} AS DATE) effective_date,g.{quote(new)} new_price,"
               f"{'COALESCE(g.'+quote(old)+','+previous+')' if old else previous} old_price FROM {quote(table['name'])} g{joins} WHERE g.{quote(day)} IS NOT NULL",[table['name']])
        break
    # Dining (README 'Bàn', 'Số khách'): the table and the number of guests of each order, repeated on its lines.
    source=sales_header if join_header and sales_header else sales_source
    if source and 'v_orders_all' in engine.tables:
        cols={c['name']:c for c in source['columns'] if c.get('nonempty')}
        order=mapping[source['name']]['order_id']
        guests=next((n for n,c in cols.items() if c['kind']=='number' and re.fullmatch(r'so_khach|so_nguoi|so_khach_an|luot_khach|so_nguoi_an',n)),None)
        table_no=next((n for n,c in cols.items() if re.fullmatch(r'ban|ban_so|so_ban|ma_ban|ten_ban',n)),None)
        # README 'Khu vực: khu vực của bàn' (trong nhà, sân, phòng lạnh): the seating area of the order.
        area=next((n for n,c in cols.items() if re.fullmatch(r'khu_vuc|khu_vuc_ban|khu|vi_tri_ban|khu_ban',n)),None) if table_no else None
        if order and (guests or table_no):
            create('v_dining',f"SELECT CAST(x.{quote(order)} AS VARCHAR) order_id,{('FIRST(NULLIF(TRIM(CAST(x.'+quote(table_no)+' AS VARCHAR)),\'\'))') if table_no else 'NULL'} table_no,"
                   f"{('MAX(x.'+quote(guests)+')') if guests else 'NULL'} guests,{('FIRST(NULLIF(TRIM(CAST(x.'+quote(area)+' AS VARCHAR)),\'\'))') if area else 'NULL::VARCHAR'} area FROM {quote(source['name'])} x WHERE x.{quote(order)} IS NOT NULL GROUP BY 1",[source['name']])
    # Payment-terms book (README 'Hạn thanh toán'): order code, due date, optionally the term in days; no amounts or items.
    # Its orders are the credit sales; when the sales book records no payment method, they are marked 'debt'.
    for table in tables:
        cols={c['name']:c for c in table['columns'] if c.get('nonempty')}
        order=mapping[table['name']]['order_id']
        due=next((n for n,c in cols.items() if c['kind']=='datetime' and re.search(r'phai_tra|han_thanh_toan|han_tra|han_no|den_han|ngay_han|tra_truoc_ngay',n)),None)
        term=next((n for n,c in cols.items() if c['kind']=='number' and re.search(r'ky_han|so_ngay_(?:duoc_)?no|thoi_han',n)),None)
        amounts=[n for n,c in cols.items() if c['kind']=='number' and n!=term]
        m=mapping[table['name']]
        if not (order and due) or amounts or m['quantity'] or m['product_name'] or 'v_orders_all' not in engine.tables:
            continue
        create('v_due',f"SELECT DISTINCT CAST(d.{quote(order)} AS VARCHAR) order_id,CAST(d.{quote(due)} AS DATE) due_date,{('d.'+quote(term)) if term else 'NULL'} term_days FROM {quote(table['name'])} d WHERE d.{quote(due)} IS NOT NULL",[table['name']])
        con=engine.connection
        if not con.execute("SELECT COUNT(*) FROM v_orders_all WHERE NULLIF(TRIM(CAST(payment_method AS VARCHAR)),'') IS NOT NULL").fetchone()[0]:
            for view in ('v_sales_all','v_sales','v_orders_all','v_orders'):
                if view in engine.tables:
                    con.execute(f"UPDATE {view} SET payment_method='debt' WHERE order_id IN (SELECT order_id FROM v_due)")
            reports.setdefault('warnings',[]).append(f"credit sales taken from payment-terms book {table['name']}; other orders have no recorded payment method")
        break
    # Cash-by-shift report (rulebook §1): cash only, by collection time, debt repayments included; one row per day and shift.
    for table in tables:
        cols={c['name']:c for c in table['columns'] if c.get('nonempty')}
        shift=next((n for n in cols if re.fullmatch(r'(?:ca|ca_lam|ca_ban|ca_lam_viec)',n)),None)
        day=next((n for n,c in cols.items() if c['kind']=='datetime'),None)
        cash=[n for n,c in cols.items() if c['kind']=='number' and 'tien_mat' in n]
        if not (shift and day and cash):
            continue
        pick=lambda pattern:next((n for n in cash if re.search(pattern,n)),None)
        net,refund=pick(r'rong|cuoi_ca|con_lai'),pick(r'hoan|tra_lai|chi_ra')
        cash_in=pick(r'(?:^|_)thu(?:_|$)') or next((n for n in cash if n not in {net,refund}),None)
        staff=next((n for n in cols if re.fullmatch(r'(?:ma_nv|nhan_vien|ma_nhan_vien|nv|thu_ngan)',n)),None)
        sql=lambda n:f"e.{quote(n)}" if n else 'NULL::DECIMAL'
        create('v_shift_cash',f"SELECT e.{quote(day)} shift_day,norm(CAST(e.{quote(shift)} AS VARCHAR)) shift,{('CAST(e.'+quote(staff)+' AS VARCHAR)') if staff else 'NULL'} staff,"
               f"{sql(cash_in)} cash_in,{sql(refund)} cash_refund,{sql(net)} cash_net FROM {quote(table['name'])} e WHERE e.{quote(day)} IS NOT NULL",[table['name']])
        break
    # Expense book (rulebook §5 'Lãi ròng một tháng'): one dated amount per row.
    for table in tables:
        words_=name_words(table['name'])
        if not re.search(r'(?:^|_)(?:chi_phi|khoan_chi|chi_tieu)(?:_|$)|(?:^|_)so_chi(?:_(?!tiet)|$)',words_):   # not 'sổ chi tiết bán hàng'
            continue
        dates=[c['name'] for c in table['columns'] if c['kind']=='datetime' and c.get('nonempty')]
        amounts=[c['name'] for c in table['columns'] if c['kind']=='number' and c.get('nonempty')]
        named=[name for name in amounts if re.search(r'(?:^|_)(?:so_tien|tien|thanh_tien|chi|amount)(?:_|$)',name)]
        amount=(named or amounts)[0] if len(named or amounts)==1 or named else None
        if len(dates)==1 and amount:
            # Columns typed by ingestion: the date is already a TIMESTAMP, the amount a number.
            create('v_expenses',f"SELECT e.{quote(dates[0])} spent_at,e.{quote(amount)} amount FROM {quote(table['name'])} e WHERE e.{quote(dates[0])} IS NOT NULL",[table['name']])
            break
    if 'v_orders_all' in engine.tables:
        finance=sales_header if join_header else sales_source
        balance=num('f',finance,'balance')
        key=text('f',finance,'order_id')
        notes=mapping[finance['name']].get('notes')
        described=fold(next((c.get('description') or '' for c in finance['columns'] if c['name']==notes),''))
        if balance=='NULL' and notes and re.search(r'ghi no|con no|con thieu|no cua don',described):
            # README: the debt note of each credit order is its state at the last data day ('ck 913 nghìn, thiếu lại 456.500đ').
            engine.connection.create_function('note_balance',note_balance,['VARCHAR','DOUBLE'],'DOUBLE',null_handling='special')
            create('v_finance',f"SELECT o.*,CASE WHEN o.payment_method='debt' THEN note_balance(n.note,CAST(o.order_total AS DOUBLE)) END snapshot_balance FROM v_orders_all o "
                   f"LEFT JOIN (SELECT {key} k,FIRST(CAST(f.{quote(notes)} AS VARCHAR)) note FROM {quote(finance['name'])} f GROUP BY 1) n ON o.order_id=n.k",['v_orders_all',finance['name']])
        else:
            create('v_finance',f"SELECT o.*,MAX({balance}) snapshot_balance FROM v_orders_all o LEFT JOIN {quote(finance['name'])} f ON o.order_id={key} GROUP BY ALL",['v_orders_all',finance['name']])
    snapshot_tables=sorted((t for t in tables if mapping[t['name']]['stock_quantity'] and (mapping[t['name']]['product_name'] or mapping[t['name']]['product_id'])
                            and ('ton_kho' in name_words(t['name']) or not (mapping[t['name']]['event_date'] or mapping[t['name']]['date'] or mapping[t['name']]['quantity']))),
                           key=lambda t:'ton_kho' not in name_words(t['name']))
    for table in snapshot_tables:
        m=mapping[table['name']]
        if m['stock_quantity'] and (m['product_name'] or m['product_id']):
            pid,name,base,cost,joins=products(table)
            create('v_stock_snapshot',f"SELECT {pid} product_id,{name} product_name,{base} base_unit,{num('s',table,'stock_quantity')} stock_quantity,{cost} base_cost FROM {quote(table['name'])} s"+joins,[table['name']])
            break
    for table in tables:
        m=mapping[table['name']]
        if m['event_date'] and m['stock_quantity'] and m['product_id']:
            pid,name,base,cost,joins=products(table)
            create('v_stock_events',f"SELECT {pid} product_id,{name} product_name,{base} base_unit,{num('s',table,'stock_quantity')} stock_quantity,{cost} base_cost,{timestamp('s',table,'event_date')} event_at,s._source_row sequence FROM {quote(table['name'])} s"+joins,[table['name']])
            break
    for table in tables:
        m=mapping[table['name']]
        if 'nhap' not in table['name'] or not m['purchase_quantity'] or not (m['product_id'] or m['product_name']):
            continue
        header=None
        if not m['purchase_date'] and m['purchase_id']:
            header=next((t for t in tables if 'nhap' in t['name'] and mapping[t['name']]['purchase_id'] and mapping[t['name']]['purchase_date'] and not mapping[t['name']]['purchase_quantity']),None)
        if not m['purchase_date'] and not header:
            continue
        date=timestamp('h',header,'purchase_date') if header else timestamp('s',table,'purchase_date')
        status=text('h',header,'status') if header else text('s',table,'status')
        pid,name,base,cost,joins=products(table)
        if header:
            joins+=f" LEFT JOIN {quote(header['name'])} h ON {text('s',table,'purchase_id')}={text('h',header,'purchase_id')}"
        qty,price=num('s',table,'purchase_quantity'),num('s',table,'purchase_price')
        amount=f"COALESCE({num('s',table,'purchase_amount')},{qty}*{price})"
        unit=f"COALESCE({text('s',table,'unit')},{base})"
        supplier=f"COALESCE({text('s',table,'supplier')},{text('h',header,'supplier') if header else 'NULL'})"
        document=text('s',table,'purchase_id')
        create('v_purchases',f"SELECT {pid} product_id,{name} product_name,{base} base_unit,{unit} unit,{qty} quantity,{price} unit_price,{amount} cost,{date} purchase_date,{supplier} supplier,{document} purchase_id FROM {quote(table['name'])} s"+joins+f" WHERE stat({status})<>'cancelled' AND {qty} IS NOT NULL",[table['name']]+([header['name']] if header else []))
        break
    attach_lots(engine,tables,create,text,num,timestamp,mapping,products)
    for table in tables:
        m=mapping[table['name']]
        # One row per delivered item line; a batch is (order, batch number).
        if 'giao' in table['name'] and m['order_id'] and m['delivery_date'] and m['delivery_batch']:
            # Driver and truck plate of each batch, when the delivery book records them ('Lái xe', 'Biển số xe').
            columns={c['name'] for c in table['columns']}
            driver=next((c for c in sorted(columns) if re.search(r'(?:^|_)(?:lai_xe|tai_xe|nguoi_giao|nhan_vien_giao|shipper)(?:_|$)',c)),None)
            truck=next((c for c in sorted(columns) if re.search(r'(?:^|_)(?:bien_so|bks|so_xe)(?:_|$)',c)),None)
            extra=(f",NULLIF(TRIM(CAST(d.{quote(driver)} AS VARCHAR)),'') driver" if driver else ",NULL::VARCHAR driver")+(f",NULLIF(TRIM(CAST(d.{quote(truck)} AS VARCHAR)),'') truck" if truck else ",NULL::VARCHAR truck")
            extra+=f",{text('d',table,'product_name')} product_name,{text('d',table,'product_id')} product_id,{num('d',table,'delivery_quantity')} delivered_quantity,{text('d',table,'base_unit')} base_unit"
            create('v_deliveries',f"SELECT {text('d',table,'order_id')} order_id,{num('d',table,'delivery_batch')} batch,{timestamp('d',table,'delivery_date')} delivery_date{extra} FROM {quote(table['name'])} d WHERE {text('d',table,'order_id')} IS NOT NULL",[table['name']])
            break


def attach_lots(engine,tables,create,text,num,timestamp,mapping,products):
    """Lot receipts (lot, product, expiry, base quantity) and lot movements of sale lines.

    Selected structurally: receipt tables carry lot+expiry+quantity and no order id;
    movement tables carry lot+order id+quantity and no expiry. Base-unit quantity
    columns (names/README mentioning 'cơ bản'/'quy đổi') are preferred over purchase-unit ones.
    """
    def base_score(table):
        column=mapping[table['name']]['lot_quantity']
        info=next((c for c in table['columns'] if c['name']==column),{})
        return 2*bool(re.search(r'co_ban|quy_doi|quy_ra',column or '') or 'co ban' in fold(info.get('description',''))) - ('nhap_hang' in table['name'] and not re.search(r'co_ban|quy_doi',column or ''))
    receipts=[t for t in tables if mapping[t['name']]['lot'] and mapping[t['name']]['expiry'] and mapping[t['name']]['lot_quantity']
              and (mapping[t['name']]['product_id'] or mapping[t['name']]['product_name']) and not mapping[t['name']]['order_id']]
    moves=[t for t in tables if mapping[t['name']]['lot'] and mapping[t['name']]['order_id'] and mapping[t['name']]['lot_quantity'] and not mapping[t['name']]['expiry']]
    if not receipts or not moves or 'v_orders_all' not in engine.tables:
        return
    receipt=max(receipts,key=base_score)
    m=mapping[receipt['name']]
    pid,name,base,_,joins=products(receipt,'s')
    date=timestamp('s',receipt,'purchase_date')
    if not m['purchase_date'] and m['purchase_id']:
        header=next((t for t in tables if mapping[t['name']]['purchase_id'] and mapping[t['name']]['purchase_date'] and not mapping[t['name']]['lot'] and not mapping[t['name']]['lot_quantity']),None)
        if header:
            joins+=f" LEFT JOIN {quote(header['name'])} h ON {text('s',receipt,'purchase_id')}={text('h',header,'purchase_id')}"
            date=timestamp('h',header,'purchase_date')
    # Base-unit cost of a lot: purchase line price per its own unit, divided by that unit's factor.
    priced=next((t for t in tables if mapping[t['name']]['lot'] and mapping[t['name']]['purchase_price'] and mapping[t['name']]['unit']),None)
    cost='NULL'
    if priced:
        joins+=f" LEFT JOIN (SELECT {text('c',priced,'lot')} lot,{num('c',priced,'purchase_price')} price,{text('c',priced,'unit')} unit FROM {quote(priced['name'])} c) c ON c.lot={text('s',receipt,'lot')}"
        cost=f"c.price/NULLIF(unit_factor({pid},{name},c.unit),0)"
    create('v_lots',f"SELECT {text('s',receipt,'lot')} lot_id,{pid} product_id,{name} product_name,CAST({timestamp('s',receipt,'expiry')} AS DATE) expiry,{date} received_at,{num('s',receipt,'lot_quantity')} qty_in,{cost} base_cost FROM {quote(receipt['name'])} s"+joins+f" WHERE {text('s',receipt,'lot')} IS NOT NULL",[receipt['name']])
    move=max(moves,key=base_score)
    create('v_lot_moves',f"SELECT {text('l',move,'lot')} lot_id,o.order_id,o.order_date moved_at,COALESCE({num('l',move,'lot_quantity')},0) qty_out,COALESCE({num('l',move,'lot_return')},0) qty_back FROM {quote(move['name'])} l JOIN v_orders_all o ON o.order_id={text('l',move,'order_id')} WHERE o.status<>'cancelled'",[move['name'],'v_orders_all'])


def compile_expiry(frame,question,engine,text):
    """Expiry questions on lots still in stock: nearest expiry, expiring list/count, capital at risk."""
    if 'v_lots' not in engine.tables:
        return None
    today=question.as_of_date
    window=None
    dates=[dt.date(int(y),int(m),int(d)) for d,m,y in re.findall(r'(?<!\d)(\d{1,2})/(\d{1,2})/(20\d{2})(?!\d)',text)]
    if len(dates)>=2 and re.search(r'\btu\b.*\b(?:den|toi)\b',text):
        window=(dates[0],dates[1])
    elif ahead:=re.search(r'(\d+)\s*(ngay|tuan|thang)\s*(?:toi|sau|nua|ke tiep|sap toi|tiep theo)',text):
        start=dates[0] if len(dates)==1 else today
        count=int(ahead[1])
        if ahead[2]=='thang':
            # 'N tháng tới': the same day N months later (last day of a shorter month).
            month=start.month-1+count
            year,month=start.year+month//12,month%12+1
            window=(start,dt.date(year,month,min(start.day,calendar.monthrange(year,month)[1])))
        else:
            window=(start,start+dt.timedelta(days=count*(7 if ahead[2]=='tuan' else 1)))
    elif len(dates)==1 and dates[0]>today and re.search(r'\btu (?:nay|hom nay|bay gio)\b|\bden (?:het )?(?:ngay )?\d|\btruoc ngay\b|\btoi (?:het )?(?:ngay )?\d',text):
        # One date after today ('từ nay đến hết ngày 22/09/2026'): today through that day; 'trước ngày D' stops the day before.
        window=(today,dates[0]-dt.timedelta(days=1) if re.search(r'\btruoc ngay\b',text) else dates[0])
    if not window and re.search(r'(?:da|bi) (?:qua|het) han|qua han su dung|het han roi',text) and not re.search(r'sap|gan|se\b',text):
        # Lots still in stock whose expiry date is already past (before today).
        balances=lot_balances(today)
        selected=f"({balances}) b WHERE remaining>0 AND expiry<DATE {literal(today)}"
        if re.search(r'tien von|von dang|gia tri|bao nhieu tien|bnhiu tien',text):
            return plan(f"SELECT COALESCE(SUM(remaining*base_cost),0) FROM {selected}",reason='Remaining base quantity of expired in-stock lots times the lot purchase price.')
        if re.search(r'(?:bao nhieu|may|so)\s+lo\b',text):
            return plan(f"SELECT COUNT(*) FROM {selected}",rounding='integer',reason='Expired lots still in stock.')
        if re.search(r'(?:bao nhieu|may|so)\s+(?:mat hang|san pham|thuoc|loai)',text):
            return plan(f"SELECT COUNT(DISTINCT product_id) FROM {selected}",rounding='integer',reason='Products with an expired lot still in stock.')
        return plan(f"SELECT product_name FROM (SELECT product_id,FIRST(product_name) product_name,MIN(expiry) first_expiry FROM {selected} GROUP BY product_id) ORDER BY first_expiry,product_id",
                    kind='list',shape='column',rounding='none',reason='Products with expired lots still in stock, earliest expiry first, then code.')
    day=today
    if not window and len(dates)==1:
        day=dates[0]
    elif window:
        day=window[0]
    balances=lot_balances(day)
    if re.search(r'gan het han nhat|can date (?:nhat|nhut)|het han (?:som|gan) nhat|han (?:dung )?(?:som|gan) nhat|sap het han nhat',text):
        product=resolve_product(frame,question,engine,['v_lots'])
        if not product:
            return None
        return plan(f"SELECT MIN(expiry) FROM ({balances}) b WHERE norm(product_name)=norm({literal(product)}) AND remaining>0 AND expiry>=DATE {literal(day)}",
                    kind='date',rounding='none',reason='Earliest expiry among lots of the product still in stock and not yet expired.')
    if not window:
        return None
    selected=f"({balances}) b WHERE remaining>0 AND expiry>=DATE {literal(window[0])} AND expiry<=DATE {literal(window[1])}"
    if re.search(r'tien von|von dang|gia tri|bao nhieu tien|bnhiu tien',text):
        _,priced=engine.lookup(f"SELECT COUNT(*),COUNT(base_cost) FROM {selected}")
        if priced[0][0]!=priced[0][1]:
            return missing('Nguồn không ghi giá nhập của lô để tính tiền vốn.')
        return plan(f"SELECT COALESCE(SUM(remaining*base_cost),0) FROM {selected}",reason='Remaining base quantity of expiring lots times the lot purchase price per base unit.')
    if re.search(r'(?:bao nhieu|may|so)\s+lo\b',text):
        return plan(f"SELECT COUNT(*) FROM {selected}",rounding='integer',reason='Lots in stock expiring inside the window.')
    if re.search(r'(?:bao nhieu|may|so)\s+(?:mat hang|san pham|thuoc|loai)',text):
        return plan(f"SELECT COUNT(DISTINCT product_id) FROM {selected}",rounding='integer',reason='Products with an in-stock lot expiring inside the window.')
    return plan(f"SELECT product_name FROM (SELECT product_id,FIRST(product_name) product_name,MIN(expiry) first_expiry FROM {selected} GROUP BY product_id) ORDER BY first_expiry,product_id",
                kind='list',shape='column',rounding='none',reason='Products with in-stock lots expiring in the window, earliest expiry first, then product code.')


def lot_balances(day):
    """Remaining base quantity per lot at the end of `day`."""
    end=literal(day+dt.timedelta(days=1))
    return (f"SELECT l.lot_id,l.product_id,l.product_name,l.expiry,l.base_cost,l.qty_in-COALESCE(m.net_out,0) remaining FROM v_lots l "
            f"LEFT JOIN (SELECT lot_id,SUM(qty_out-qty_back) net_out FROM v_lot_moves WHERE moved_at<TIMESTAMP {end} GROUP BY lot_id) m ON m.lot_id=l.lot_id "
            f"WHERE l.received_at IS NULL OR l.received_at<TIMESTAMP {end}")


def resolve_product(frame,question,engine,views):
    names=[]
    for view in views:
        if view in engine.tables:
            _,rows=engine.lookup(f'SELECT DISTINCT product_name FROM {view} WHERE product_name IS NOT NULL')
            names += [r[0] for r in rows]
    names=list(dict.fromkeys(names))
    normalize=lambda s: re.sub(r'[^a-z0-9]+',' ',fold(s)).strip()
    query=normalize(frame.product_filter)
    text=normalize(question.question)
    exact=[name for name in names if normalize(name)==query] if query else []
    contained=[name for name in names if normalize(name) and normalize(name) in text]
    matches=exact or contained
    if not matches and query:
        words=set(query.split())
        matches=[name for name in names if words and words<=set(normalize(name).split())]
    if not matches:
        return None
    matches.sort(key=lambda s:len(normalize(s)),reverse=True)
    return matches[0]


# README 'giao 1–3 đợt (Lượt giao)': đợt, lượt, lần, chuyến giao name the same batch.
BATCH_WORDS=r'\b(?:dot|luot|lan|chuyen) (?:giao|cho|hang|van chuyen)\b|chuyen (?:giao|cho) hang|\bdot hang\b|\bgiao (?:hang )?(?:nhieu|tu|tren|it nhat|hon|dung|\d+|mot|hai|ba)\b(?: \d+)? (?:dot|luot|lan|chuyen)\b|\bgiao\b(?: [a-z]+){0,3} (?:bao nhieu|may|bn) (?:dot|luot|chuyen)\b|(?:tai xe|lai xe|nguoi giao)\b.*\b(?:dot|luot|chuyen)\b|\bcho (?:hang )?(?:nhieu|it|bao nhieu|may) (?:dot|luot|chuyen)\b'
COUNT_WORDS={'mot':1,'hai':2,'ba':3,'bon':4}


def without_item_names(text,engine):
    """The question with the item names it contains blanked out (longest first)."""
    names=set()
    for view in ('v_products','v_purchases'):
        if view in engine.tables:
            _,rows=engine.lookup(f'SELECT DISTINCT product_name FROM {view} WHERE product_name IS NOT NULL')
            names|={re.sub(r'\s+',' ',fold(r[0])).strip() for r in rows}
    for name in sorted((n for n in names if len(n)>=5),key=len,reverse=True):
        if name in text:
            text=text.replace(name,' ')
    return text


def delivery_batches_condition(frame,text):
    """'giao từ 2 đợt trở lên', 'nhiều đợt', 'đúng 3 lượt', '1 lần': a condition on an order's number of batches, else None."""
    unit=r'(?:dot|luot|lan|chuyen)'
    number=r'(\d+|mot|hai|ba|bon)'
    value=lambda token:int(token) if token.isdigit() else COUNT_WORDS[token]
    if re.search(r'\bnhieu (?:hon (?:mot|1) )?'+unit+r'\b|\bhon (?:mot|1) '+unit+r'\b',text):
        return '>=2'
    if m:=re.search(r'(?:tu|it nhat|toi thieu) '+number+r' '+unit+r'\b|\b'+number+r' '+unit+r' tro len\b',text):
        return f'>={value(m[1] or m[2])}'
    if m:=re.search(r'(?:tren|hon) '+number+r' '+unit+r'\b',text):
        return f'>{value(m[1])}'
    if m:=re.search(r'(?:duoi|it hon) '+number+r' '+unit+r'\b',text):
        return f'<{value(m[1])}'
    if m:=re.search(r'\bgiao (?:hang )?(?:dung |trong |chi |vong )?'+number+r' '+unit+r'\b',text):
        return f'={value(m[1])}'
    if frame.threshold and frame.metric=='orders':
        return {'>=':'>=','>':'>','<=':'<=','<':'<','=':'='}.get(frame.threshold_operator,'>=')+str(int(frame.threshold))
    return None


DINING=re.compile(r'\bso khach\b(?! hang)|luot khach|nguoi an|dong khach|vang khach|khach (?:ngoi|an) tai|moi ban\b|\bban (?:nao|so \d+|\d+)\b|\bcac ban\b|\bso ban\b')


def compile_dining(frame,question,engine,text,periods):
    """Guests and tables of valid orders (rulebook §1 order rules; README 'Bàn', 'Số khách'). Averages round to 2 places."""
    if re.search(r"\bno\b|con thieu|thieu tien|khach.*(?:mua|don).*nhat",text):
        return None  # debtor/customer identity is not the number of diners on tickets
    if not periods:
        return None
    period=periods[0]
    where=f" WHERE o.order_date>=TIMESTAMP {literal(period.start)} AND o.order_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))}"
    from .core import seating_area
    area=seating_area(engine,question.question)
    if area:
        where+=f" AND g.area={literal(area)}"
    joined="FROM v_orders o JOIN v_dining g ON g.order_id=o.order_id"+where
    guests_asked=bool(re.search(r'\bso khach\b(?! hang)|luot khach|nguoi an|dong khach|vang khach|bao nhieu khach|may khach|(?:nhieu|it) khach\b(?! hang)',text))
    table_asked=bool(re.search(r'\bban (?:nao|so \d+|\d+)\b|moi ban\b|\bcac ban\b|\bso ban\b',text))
    rank=re.search(r'\b(?:nhieu|dong|cao|it|vang)(?:\s+[a-z]+){0,2}?\s+nhat\b',text)
    low=bool(rank and re.match(r'(?:it|vang)\b',rank[0]))
    if table_asked and re.search(r'\bban nao\b',text) and rank:
        measure='SUM(g.guests)' if guests_asked else 'COUNT(*)'
        order='ASC' if low else 'DESC'
        return plan(f"SELECT table_no FROM (SELECT g.table_no,{measure} v {joined} AND g.table_no IS NOT NULL GROUP BY 1) ORDER BY v {order},table_no LIMIT 1",kind='string',rounding='none',
                    reason='Table with the most guests' if guests_asked else 'Table with the most valid orders')
    if re.search(r'ngay nao',text) and rank and guests_asked:
        return plan(f"SELECT d FROM (SELECT CAST(o.order_date AS DATE) d,SUM(g.guests) v {joined} GROUP BY 1) ORDER BY v DESC,d LIMIT 1",kind='date',rounding='none',reason='Day with the most guests.')
    if guests_asked and re.search(r'trung binh|binh quan|\bbq\b',text):
        per='g.table_no IS NOT NULL' if re.search(r'moi ban|tai quan|an tai|ngoi tai',text) else 'TRUE'
        return plan(f"SELECT AVG(g.guests) {joined} AND g.guests IS NOT NULL AND {per}",rounding='percent',reason='Average guests per valid order (dine-in orders when asked per table).')
    if guests_asked and not rank:
        per=' AND g.table_no IS NOT NULL' if re.search(r'tai quan|an tai|ngoi tai',text) else ''
        return plan(f"SELECT COALESCE(SUM(g.guests),0) {joined}{per}",rounding='integer',reason='Guests of valid orders in the period.')
    return None


def compile_due(frame,question,engine,text,periods):
    """Credit sales by payment due date (README payment-terms book). Overdue = due date before the day asked (rulebook §6:
    a due date on the day asked is not yet overdue); 'N ngày tới' = from today to today + N; a period attached to the due
    words filters due dates, otherwise order dates. No payment history: amounts are order totals of the credit sales."""
    today=question.as_of_date
    conditions=["o.order_date<TIMESTAMP "+literal(today+dt.timedelta(days=1))]
    term=re.search(r'ky han\s*(?:la\s*)?(\d+)\s*ngay|(\d+)\s*ngay\s*(?:ky han|duoc no)',text)
    if term:
        conditions.append(f"d.term_days={int(term[1] or term[2])}")
    ahead=re.search(r'(\d+)\s*ngay\s*(?:toi|sap toi|nua|ke tiep|tiep theo)',text)
    due_words=re.search(r'ngay phai tra|phai tra|den han|toi han|han (?:thanh toan|tra|no)\b',text)
    # 'tính đến hôm nay' is the cutoff, not a period of orders.
    period=periods[0] if periods and not (periods[0].start==periods[0].end==today and not due_words) else None
    if period and period.start==period.end==today and re.search(r'qua han',text):
        period=None
    if re.search(r"(?:sau|muon hon) (?:hom nay|bua ni)|han.*chua (?:den|toi)",text):
        conditions.append("d.due_date>DATE "+literal(today))
    elif re.search(r'qua han',text):
        conditions.append("d.due_date<DATE "+literal(today))
        if period:
            conditions.append(f"CAST(o.order_date AS DATE) BETWEEN DATE {literal(period.start)} AND DATE {literal(period.end)}")
    elif ahead and due_words:
        conditions.append(f"d.due_date BETWEEN DATE {literal(today)} AND DATE {literal(today+dt.timedelta(days=int(ahead[1])))}")
    elif period:
        # With due words (or a period after today, when no order can be placed) the period is the due date's; else the order date's.
        column="d.due_date" if due_words or period.start>today else "CAST(o.order_date AS DATE)"
        conditions.append(f"{column} BETWEEN DATE {literal(period.start)} AND DATE {literal(period.end)}")
    elif not term:
        return None
    joined="FROM v_due d JOIN v_orders o ON o.order_id=d.order_id WHERE "+" AND ".join(conditions)
    if re.search(r'bao nhieu tien|tong (?:so )?tien|gia tri|so tien|doanh thu',text):
        return plan(f"SELECT COALESCE(SUM(COALESCE(o.order_total,o.revenue)),0) {joined}",reason='Totals of credit sales selected by due date (payment-terms book).')
    if re.search(r'(?:bao nhieu|may|so|dem)\s+(?:nguoi\s+)?khach',text):
        return plan(f"SELECT COUNT(DISTINCT o.customer_key) {joined} AND o.customer_key IS NOT NULL",rounding='integer',reason='Distinct identified customers with credit sales selected by due date.')
    return plan(f"SELECT COUNT(*) {joined}",rounding='integer',reason='Credit sales (payment-terms book) selected by due date; cancelled and returned orders excluded (§1 order count).')


def compile_specialty(frame,question,catalog,engine):
    text=fold(question.question)
    cutoff=re.search(r'den het (\d{2})/(\d{2})/(\d{4})',fold(catalog['readme']))
    cutoff=dt.date(int(cutoff[3]),int(cutoff[2]),int(cutoff[1])) if cutoff else None
    periods=resolve_periods(question.question,question.as_of_date,engine,exclude=(frame.product_filter,frame.customer_filter))
    # Snapshot/date queries need an explicit cutoff rather than a flow period.
    as_of=periods[0].end if periods else question.as_of_date
    due_words=re.search(r'thanh toan|tra no|\bno\b|ky han|phai tra|han tra|han no|han thanh toan',text)
    if re.search(r'het han\b|han dung|han su dung|\bhsd\b|can date',text) and not ('v_due' in engine.tables and due_words):
        if 'v_lots' not in engine.tables and not due_words:
            # §7: expiry dates are recorded per lot; a shop without lots does not record them.
            return missing_source(catalog,'lots','Nguồn không ghi lô hàng và hạn dùng.')
        if 'v_lots' in engine.tables:
            return compile_expiry(frame,question,engine,text)
    # 'số khách mua từ 2 lần', 'tỉ lệ khách ...' count customers, not guests at a table.
    if 'v_dining' in engine.tables and DINING.search(text) and not re.search(r'khach (?:mua|hang|quen|than thiet|nao|moi|cu|da mua|dat hang)|so khach (?:mua|da mua|co mua|dat hang)|(?:ti|ty) le|phan tram|%|tro len|tro xuong|lan mua|\bdon tro\b',text):
        dining=compile_dining(frame,question,engine,text,periods)
        if dining is not None:
            return dining
    if 'v_due' in engine.tables and re.search(r'ngay phai tra|phai tra (?:truoc|trong|vao)|han (?:thanh toan|tra no|tra tien|tra|no)\b|qua han|den han|toi han|ky han',text):
        due=compile_due(frame,question,engine,text,periods)
        if due is not None:
            return due
    if 'v_shift_cash' in engine.tables and re.search(r'tien mat|\bket\b|ban giao ca|chot ca|bao cao ca|nop ca',text) and re.search(r'\bca\b',text) \
            and not re.search(r'doanh thu|\bdthu\b|so don|bao nhieu don',text):
        # Rulebook §1: cash by shift comes from the shift report, never from orders.
        if not periods:
            return None
        start,end=literal(periods[0].start),literal(periods[0].end+dt.timedelta(days=1))
        measure='cash_net' if re.search(r'\brong\b|cuoi ca|con lai|thuc nhan',text) else 'cash_refund' if re.search(r'hoan|tra lai khach',text) else 'cash_in'
        where=f" WHERE shift_day>=TIMESTAMP {start} AND shift_day<TIMESTAMP {end}"
        named=re.search(r'\bca (sang|chieu|toi)\b',text)
        if named:
            where+=f" AND shift={literal(named[1])}"
        staff=re.findall(r'\bnv\s*0*(\d+)\b',text)
        if staff:
            where+=" AND regexp_extract(norm(staff),'(\\d+)$',1)::INT IN ("+','.join(str(int(x)) for x in staff)+")"
        return plan(f"SELECT COALESCE(SUM({measure}),0) FROM v_shift_cash{where}",reason='Cash by shift from the shift report (cash only, by collection time, debt repayments included).')
    if re.search(r'\b(?:lai|loi) rong\b|loi nhuan rong|lai thuc te|tru chi phi.*(?:loi|lai)',text):
        # Rulebook §5: net profit of a month = gross profit (revenue − qty × cost on the sale line) − every expense of that month.
        if not periods or 'v_expenses' not in engine.tables or not catalog.get('semantic',{}).get('capabilities',{}).get('unit_cost'):
            return None
        start,end=literal(periods[0].start),literal(periods[0].end+dt.timedelta(days=1))
        gross=f"SELECT COALESCE(SUM(revenue-quantity*unit_cost),0) v FROM v_sales WHERE order_date>=TIMESTAMP {start} AND order_date<TIMESTAMP {end}"
        spent=f"SELECT COALESCE(SUM(amount),0) v FROM v_expenses WHERE spent_at>=TIMESTAMP {start} AND spent_at<TIMESTAMP {end}"
        return plan(f"WITH g AS ({gross}),e AS ({spent}) SELECT g.v-e.v FROM g,e",reason='Net profit: gross profit from the cost on each sale line, minus every expense dated in the month.')
    named_ingredient=False
    if 'v_drugs' in engine.tables and not re.search(r'ke don|hoat chat',text) and re.search(r'doanh thu|\bdthu\b|tien|thu ve|ban duoc',text):
        # 'thuốc chứa Loratadin': an ingredient named directly, without the word 'hoạt chất'.
        _,known=engine.lookup('SELECT DISTINCT ingredient FROM v_drugs WHERE ingredient IS NOT NULL')
        named_ingredient=any(len(fold(r[0]).strip())>=5 and re.search(r'(?<![a-z0-9])'+re.escape(fold(r[0]).strip())+r'(?![a-z0-9])',text) for r in known)
        if named_ingredient and frame.product_filter:
            # 'Doanh thu Vitamin C 500mg Quapharco hộp 10 vỉ': one named item whose name holds an ingredient, not every drug of it.
            from .core import match_names
            mention=re.sub(r'\s+',' ',fold(frame.product_filter)).strip()
            longer=all(mention!=fold(r[0]).strip() and not re.fullmatch(r'(?:thuoc |hoat chat )?'+re.escape(fold(r[0]).strip()),mention) for r in known)
            if longer and match_names(engine,'product_name',frame.product_filter):
                named_ingredient=False
    if (re.search(r'ke don|hoat chat',text) or named_ingredient) and (frame.metric=='revenue' or frame.output_type=='percent' or re.search(r'dthu|doanh thu|tien|thu dc|thu duoc',text)):
        if 'v_drugs' not in engine.tables:
            return None
        if not periods:
            return None
        period=periods[0]
        predicate='TRUE'
        if 'ke don' in text:
            predicate='prescription'
            negative=bool(re.search(r'(?:khong|\bko|\bk|hok|\bhong|\bhem)\s+(?:phai\s+|can\s+)?(?:thuoc\s+)?ke don|\btru\b.*ke don|ngoai (?:thuoc |hang )?ke don|ke don.*\bcon lai\b',text))
        else:
            _,ingredients=engine.lookup('SELECT DISTINCT ingredient FROM v_drugs WHERE ingredient IS NOT NULL')
            matches=[r[0] for r in ingredients if fold(r[0]) in text]
            if not matches:
                return None
            target=max(matches,key=len)
            predicate='norm(ingredient)=norm('+literal(target)+')'
            negative=False
        selected=f"EXISTS(SELECT 1 FROM v_drugs d WHERE (s.product_id=d.product_id OR norm(s.product_name)=norm(d.product_name)) AND {predicate})"
        if negative:
            selected='NOT '+selected
        where=f" WHERE s.order_date>=TIMESTAMP {literal(period.start)} AND s.order_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))}"
        numerator='SELECT COALESCE(SUM(s.revenue),0) value FROM v_sales s'+where+' AND '+selected
        if frame.output_type=='percent':
            denominator='SELECT SUM(s.revenue) value FROM v_sales s'+where
            return plan('WITH a AS ('+numerator+'),b AS ('+denominator+') SELECT 100.0*a.value/NULLIF(b.value,0) FROM a,b',kind='percent',rounding='percent',reason='Exact drug metadata predicate; denominator is whole-shop valid revenue.')
        return plan(numerator,reason='Revenue of exact prescription/ingredient metadata; combination ingredients remain separate.')
    if 'v_deliveries' in engine.tables and re.search(r"(?:da |da duoc )?(?:giao|cho)(?: di)?",text) and re.search(r"(?:khach |don )dat",text) and periods:
        p=periods[0]
        _, products=engine.lookup("SELECT DISTINCT product_name FROM v_deliveries WHERE product_name IS NOT NULL")
        names=[n for (n,) in products if re.search(r"(?<![a-z0-9])"+re.escape(fold(n))+r"(?![a-z0-9])",text)]
        if names:
            name=max(names,key=len)
            unit=re.search(r"(?:bao nhieu|may)\s+(thung|cuon|cay|bao|vien|kg|tan|m3)\b",text)
            factor=f"unit_factor(d.product_id,d.product_name,{literal(unit[1])})" if unit else "1"
            return plan(f"SELECT SUM(d.delivered_quantity/{factor}) FROM v_deliveries d JOIN v_orders o USING(order_id) "
                        f"WHERE o.order_date>=TIMESTAMP {literal(p.start)} AND o.order_date<TIMESTAMP {literal(p.end+dt.timedelta(days=1))} "
                        f"AND d.delivery_date<TIMESTAMP {literal(question.as_of_date+dt.timedelta(days=1))} AND norm(d.product_name)=norm({literal(name)})",
                        rounding='none',reason='Delivered quantities through the cutoff, restricted by purchase date and product; convert base units once.')
    if (re.search(BATCH_WORDS,text) or re.search(r'cho thanh \d+ dot',text)) and 'v_deliveries' in engine.tables:
        if not periods:
            return None
        period=periods[0]
        end=min(period.end,question.as_of_date)
        rank=re.search(r'\bnhat\b|\btop\b',text)
        who=('driver' if re.search(r'tai xe|lai xe|nguoi giao|\bai\b',text) else 'truck' if re.search(r'\bxe (?:nao|bien so nao)\b|bien so (?:nao|xe nao)',text) else None) if rank else None
        if who:
            # Driver / truck with the most batches (each batch of each order once) delivered in the period, up to today.
            _,present=engine.lookup(f"SELECT COUNT({who}) FROM v_deliveries")
            if not present[0][0]:
                return None
            order='ASC' if re.search(r'\b(?:it|thap) nhat\b',text) else 'DESC'
            return plan(f"SELECT {who} FROM (SELECT {who},COUNT(DISTINCT order_id||'#'||CAST(batch AS VARCHAR)) n FROM v_deliveries WHERE {who} IS NOT NULL AND delivery_date>=TIMESTAMP {literal(period.start)} AND delivery_date<TIMESTAMP {literal(end+dt.timedelta(days=1))} GROUP BY 1) ORDER BY n {order},{who} LIMIT 1",
                        kind='string',rounding='none',reason='Driver or truck with the most delivery batches in the period (each batch of each order once).')
        batches=delivery_batches_condition(frame,text)
        if batches and re.search(r'\bdon\b',text):
            # Orders of the period (order date, §1 valid orders) by their number of batches delivered up to today.
            return plan(f"SELECT COUNT(*) FROM v_orders o JOIN (SELECT order_id,COUNT(DISTINCT batch) n FROM v_deliveries WHERE delivery_date<TIMESTAMP {literal(question.as_of_date+dt.timedelta(days=1))} GROUP BY 1) d USING(order_id) "
                        f"WHERE o.order_date>=TIMESTAMP {literal(period.start)} AND o.order_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))} AND d.n{batches}",
                        rounding='integer',reason='Valid orders placed in the period (§1) by the number of delivery batches up to today.')
        return plan(f"SELECT COUNT(DISTINCT order_id||'#'||CAST(batch AS VARCHAR)) FROM v_deliveries WHERE delivery_date>=TIMESTAMP {literal(period.start)} AND delivery_date<TIMESTAMP {literal(end+dt.timedelta(days=1))}",
                    rounding='integer',reason='Each batch of each order with delivery date in the period, up to today; delivered batches of later-returned orders still count.')
    if re.search(r'nha cung cap|\bncc\b',text) and re.search(r'\bnao\b|\bai\b',text) and 'v_purchases' in engine.tables:
        _,known=engine.lookup('SELECT COUNT(supplier) FROM v_purchases')
        if not known[0][0]:
            return None
        if not periods:
            return None
        period=periods[0]
        main=bool(re.search(r'(?:nha cung cap|ncc) chinh|nhieu phieu|nhieu lan nhap|nhieu don nhap|nhap thuong xuyen',text))
        if main:
            _,documents=engine.lookup('SELECT COUNT(purchase_id) FROM v_purchases')
            if not documents[0][0]:
                return None
        measure='COUNT(DISTINCT purchase_id)' if main else 'SUM(cost)'
        grouped=f"SELECT supplier,{measure} value FROM v_purchases WHERE supplier IS NOT NULL AND purchase_date>=TIMESTAMP {literal(period.start)} AND purchase_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))} GROUP BY supplier"
        return plan(f"SELECT supplier FROM ({grouped}) ORDER BY value DESC,supplier ASC LIMIT 1",kind='string',rounding='none',
                    reason='Main supplier by number of purchase documents' if main else 'Supplier with highest purchase amount; cancelled documents excluded.')
    newly_words=r'no them|mua chiu them|chiu them|ghi no them'
    # 'dư nợ', 'số nợ', 'khoản nợ', 'tiền nợ', '(còn / nợ) phải thu', 'chưa thu (được) của khách' are the outstanding balance too.
    debt=bool(re.search(r'con no|con thieu|thieu tien|cong no|tra no|doi (?:duoc )?.*tien no|tra bot no|bot no|(?<!doanh )thu no|thu hoi.*no|no co tuoi|\bno chua (?:thu|tra)\b|khach (?:dang |con )?no\b|(?:nao|ai) (?:dang |con )?no\b|\bno nhieu (?:tien )?nhat\b|dang no\b|\btong no\b|\bno cua khach\b|'
                        r'\b(?:du|so|khoan|tien) no\b|\b(?:no|con) phai thu\b|\bchua thu (?:duoc )?(?:tien )?(?:cua |tu )?khach\b|\btien chua thu\b|\bchua tra (?:het |du )?(?:no|tien no)\b|\btuoi no\b|'+newly_words,text)) and not re.search(r'doanh thu.*(?:ghi no|mua chiu)',text)
    if debt:
        if 'v_finance' not in engine.tables:
            return missing_source(catalog,'debt','Nguồn không có mã đơn/tổng phải trả để dựng công nợ.')
        if 'v_receipts' not in engine.tables:
            # No payment history: only the closing balance on the last day of the data can be read (whoever the customer is).
            _,coverage=engine.lookup('SELECT COUNT(snapshot_balance) FROM v_finance')
            if not coverage[0][0] or as_of!=cutoff:
                return missing_source(catalog,'debt','Chỉ có số dư chốt cuối kỳ hoặc không có lịch sử thu tiền; không dựng được công nợ tại ngày hỏi.')
        customer_clause=''
        if not frame.customer_filter:
            # Customer named in the question but not extracted by the model.
            _,names=engine.lookup('SELECT DISTINCT customer_name FROM v_finance WHERE customer_key IS NOT NULL AND customer_name IS NOT NULL')
            named=[r[0] for r in names if len(fold(r[0]).split())>=2 and re.search(r'(?<!\w)'+re.escape(fold(r[0]).strip())+r'(?!\w)',text)]
            if named:
                frame=frame.model_copy(update={'customer_filter':max(named,key=len)})
        if frame.customer_filter:
            _,names=engine.lookup('SELECT DISTINCT customer_name,customer_key FROM v_finance WHERE customer_name IS NOT NULL')
            target=fold(frame.customer_filter).strip()
            spelled=re.sub(r'[^a-z0-9]','',target)
            matches=[r for r in names if fold(r[0]).strip()==target or re.sub(r'[^a-z0-9]','',fold(r[0]))==spelled]
            if not matches:
                matches=[r for r in names if target and target in fold(r[0])]
            if not matches:
                return None
            keys=sorted({key for _,key in matches if key})
            # §3: every order of the matched customers (codes, or names spellings aside), not one spelling of the name.
            customer_clause=(' AND customer_key IN ('+','.join(literal(k) for k in keys)+')') if keys else (' AND norm(customer_name) IN ('+','.join(literal(fold(name).strip()) for name,_ in matches)+')')
        if 'v_receipts' in engine.tables:
            _,totals=engine.lookup('SELECT COUNT(order_total) FROM v_finance')
            if not totals[0][0]:
                return None  # payments are recorded but amounts owed per order are not: a balance of 0 would be invented
            if re.search(r'tra no|doi (?:duoc )?.*tien no|tra bot no|bot no|(?<!doanh )thu no|thu hoi.*no|'+newly_words,text):
                if not periods:
                    return None
                period=periods[0]
                sql=f"SELECT COALESCE(SUM(r.amount),0) FROM v_receipts r JOIN v_finance o USING(order_id) WHERE o.status<>'cancelled' AND o.payment_method='debt' AND CAST(r.paid_at AS DATE)>CAST(o.order_date AS DATE) AND r.amount>0 AND r.paid_at>=TIMESTAMP {literal(period.start)} AND r.paid_at<TIMESTAMP {literal(period.end+dt.timedelta(days=1))}"
                sql+=customer_clause.replace('norm(customer_name)','norm(o.customer_name)').replace(' customer_key IN',' o.customer_key IN')
                if re.search(newly_words,text):
                    newly=f"WITH same_day AS (SELECT r.order_id,SUM(r.amount) paid FROM v_receipts r JOIN v_finance o USING(order_id) WHERE CAST(r.paid_at AS DATE)=CAST(o.order_date AS DATE) AND r.amount>0 GROUP BY r.order_id) SELECT COALESCE(SUM(GREATEST(o.order_total-COALESCE(p.paid,0),0)),0) FROM v_finance o LEFT JOIN same_day p USING(order_id) WHERE o.status<>'cancelled' AND o.payment_method='debt' AND o.order_date>=TIMESTAMP {literal(period.start)} AND o.order_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))}"
                    newly+=customer_clause.replace('norm(customer_name)','norm(o.customer_name)').replace(' customer_key IN',' o.customer_key IN')
                    if 'tra' in text:
                        return plan('SELECT (SELECT * FROM ('+newly+')),(SELECT * FROM ('+sql+'))',kind='list',shape='row',reason='New debt after purchase-day payments, then repayment after purchase day; same customer and period.')
                    return plan(newly,reason='New debt is amount not paid on the purchase day, not current balance.')
                return plan(sql,reason='Debt payments strictly after purchase day; cancelled excluded, returns retained.')
            # An order paid in cash/transfer with no receipt line at all was paid at purchase; only debt orders wait for receipts.
            balances=f"WITH paid AS (SELECT order_id,SUM(amount) amount FROM v_receipts WHERE amount>0 AND paid_at<TIMESTAMP {literal(as_of+dt.timedelta(days=1))} GROUP BY order_id),seen AS (SELECT DISTINCT order_id FROM v_receipts) SELECT o.*,GREATEST(o.order_total-COALESCE(p.amount,CASE WHEN o.payment_method<>'debt' AND o.order_id NOT IN (SELECT order_id FROM seen) THEN o.order_total END,0),0) balance FROM v_finance o LEFT JOIN paid p USING(order_id)"
        else:
            _,coverage=engine.lookup('SELECT COUNT(snapshot_balance) FROM v_finance')
            if not coverage[0][0] or as_of!=cutoff:
                return missing_source(catalog,'debt','Chỉ có số dư chốt cuối kỳ hoặc không có lịch sử thu tiền; không dựng được công nợ tại ngày hỏi.')
            balances='SELECT *,snapshot_balance balance FROM v_finance'
        where=f" WHERE status<>'cancelled' AND payment_method<>'cod' AND NOT regexp_matches(norm(COALESCE(channel,'')),'san tmdt|marketplace|shopee|lazada|tiktok') AND order_date<TIMESTAMP {literal(as_of+dt.timedelta(days=1))} AND balance>0"
        if 'v_installments' in engine.tables and re.search(r'(?:khong (?:tinh|ke)|tru|ngoai|bo) (?:cac )?(?:don )?(?:tra gop|gop)\b',text):
            where+=" AND order_id NOT IN (SELECT order_id FROM v_installments)"   # 'không tính trả góp': the debt book only
        buckets=bool(re.search(r'0\s*[-–—]\s*7',text) and re.search(r'8\s*[-–—]\s*15',text))
        age=re.search(r'(?:qua|hon|tren)\s*(\d+)\s*ngay',text) if not buckets else None
        if age:
            where+=f" AND date_diff('day',CAST(order_date AS DATE),DATE {literal(as_of)})>{int(age[1])}"
        where+=customer_clause
        if buckets:
            age_expr=f"date_diff('day',CAST(order_date AS DATE),DATE {literal(as_of)})"
            values=','.join(f'COALESCE(SUM(balance) FILTER(WHERE {age_expr} {condition}),0)' for condition in ['BETWEEN 0 AND 7','BETWEEN 8 AND 15','>15'])
            return plan('SELECT '+values+' FROM ('+balances+') b'+where,kind='list',shape='row',reason='Debt aging from purchase date: 0–7, 8–15, over 15 days, in that order.')
        if re.search(r'(?:bao nhieu|may|\bbn\b|so luong|dem(?:\s+\w+){0,2}?|tong so|so)\s+(?:nguoi\s+|vi\s+)?khach',text) and not re.search(r'top|nhieu nhat|lon nhat|bao nhieu tien|tong (?:so )?tien',text):
            # Count distinct identified customers with a positive balance; walk-ins are not customers.
            return plan('SELECT COUNT(DISTINCT customer_key) FROM ('+balances+') b'+where+' AND customer_key IS NOT NULL',rounding='integer',reason='Distinct identified customers with outstanding balance at the requested cutoff.')
        if re.search(r'top|nhieu nhat|nhiu nhat|lon nhat',text):
            count=re.search(r'top\s*(\d+)|\b(\d+)\s*khach',text)
            k=int(next(v for v in count.groups() if v)) if count else frame.k
            if k==1 and re.search(r'khach (?:hang )?nao|\bai\b',text) and not re.search(r'bang|bao nhieu|so tien',text):
                # One customer asked ('khách nào nợ nhiều nhất'): the name (rulebook §9: one object, string).
                sql=f"SELECT name FROM (SELECT FIRST(customer_name) name,SUM(balance) v FROM ({balances}) b"+where+" AND customer_key IS NOT NULL GROUP BY customer_key ORDER BY v DESC,customer_key ASC LIMIT 1)"
                return plan(sql,kind='string',rounding='none',reason='Customer with the largest outstanding balance; ties by customer key.')
            sql=f"SELECT FIRST(customer_name) khach_hang,SUM(balance) so_tien_no FROM ({balances}) b"+where+f" AND customer_key IS NOT NULL GROUP BY customer_key ORDER BY so_tien_no DESC,customer_key ASC LIMIT {k}"
            return plan(sql,kind='table',shape='table',reason='Outstanding order total less historical receipts; grouped by distinct customer key.')
        return plan('SELECT COALESCE(SUM(balance),0) FROM ('+balances+') b'+where,reason='Historical outstanding balance; returned orders retained, cancelled/COD/marketplace excluded.')
    out_of_stock=bool(re.search(r'het hang|chay hang|het sach|het veo|khong con hang|ton (?:kho )?(?:bang|=) ?0\b',text))
    stock=out_of_stock or bool(re.search(r'ton kho|trong kho|kho con|cuoi ngay.*\bcon\b|het ngay.*\bcon\b|kho.*con ton|\bton cuoi\b|\bcon ton\b',text))
    # 'Hôm nay Xi măng INSEE còn bao nhiêu bao?': what is left of one named item is its stock.
    left=not stock and bool(re.search(r'\bcon (?:lai )?(?:bao nhieu|bnhiu|bao nhiu|may|bn)\b(?! (?:tien|dong|don|khach|ngay|lan|phan tram|ky|thang|tuan|nam|gio|phut|so|diem|luot|phieu)\b)',without_item_names(text,engine))) \
        and not re.search(r'\bban\b|doanh thu|\bthu\b|\bno\b|\bgop\b|het han|han dung',without_item_names(text,engine))
    if left and not resolve_product(frame,question,engine,['v_stock_snapshot','v_stock_events','v_lots']):
        left=False
    stock=stock or left
    if stock:
        if 'v_stock_events' in engine.tables:
            source=f"(SELECT * FROM v_stock_events WHERE event_at<TIMESTAMP {literal(as_of+dt.timedelta(days=1))} QUALIFY ROW_NUMBER() OVER(PARTITION BY product_id ORDER BY event_at DESC,sequence DESC)=1)"
        elif 'v_stock_snapshot' in engine.tables and as_of==cutoff:
            source='v_stock_snapshot'
        elif 'v_lots' in engine.tables:
            source=f"(SELECT product_id,product_name,SUM(remaining) stock_quantity,NULL::DECIMAL base_cost FROM ({lot_balances(as_of)}) GROUP BY product_id,product_name)"
        else:
            return missing_source(catalog,'stock','Nguồn không có lịch sử tồn kho tại ngày hỏi; snapshot chỉ dùng được đúng ngày chốt README.')
        if re.search(r'gia tri|dang bao nhieu|gia von',text):
            _,valid=engine.lookup('SELECT COUNT(*),COUNT(base_cost) FROM '+source+' WHERE stock_quantity>0')
            if valid[0][0]!=valid[0][1]:
                return missing('Thiếu giá vốn đơn vị cơ bản trong danh mục để định giá tồn kho.')
            return plan('SELECT SUM(stock_quantity*base_cost) FROM '+source+' WHERE stock_quantity>0',reason='Closing stock per product times catalog base-unit cost.')
        listing=bool(re.search(r'\bnhung\b|\bcac\b|liet ke|\bnao\b|\bgi\b',text)) and not re.search(r'bao nhieu|\bmay\b|\bso (?:luong )?mat hang\b|dem',text) and not left
        keyed=f"(SELECT *,COALESCE(product_id,product_name) k FROM {source})"
        if out_of_stock:
            # Rulebook §5: items bought for resale whose stock at the end of that day is 0 (restocked before day end is not out).
            if listing:
                return plan(f"SELECT product_name FROM {keyed} WHERE stock_quantity<=0 ORDER BY k",kind='list',shape='column',rounding='none',reason='Items with zero stock at the end of the day, by code.')
            return plan(f"SELECT COUNT(*) FROM {keyed} WHERE stock_quantity<=0",rounding='integer',reason='Items with zero stock at the end of the day.')
        below=re.search(r'(?:duoi|it hon|nho hon|khong (?:den|toi)|<)\s*(\d+)',text)
        if below and re.search(r'mat hang|san pham|\bhang\b|\bmon\b|\bthuoc\b|\bsp\b',text) and not frame.product_filter:
            limit=int(below[1])
            if listing:
                return plan(f"SELECT product_name FROM {keyed} WHERE stock_quantity<{limit} ORDER BY stock_quantity DESC,k",kind='list',shape='column',rounding='none',reason='Items with closing stock below the threshold (threshold excluded), by stock then code.')
            return plan(f"SELECT COUNT(*) FROM {keyed} WHERE stock_quantity<{limit}",rounding='integer',reason='Items with closing stock below the threshold (threshold excluded).')
        if re.search(r'nhieu nhat|cao nhat|lon nhat|it nhat|thap nhat',text) and re.search(r'\bnao\b|\bgi\b|top|\bmon\b',text) and not frame.product_filter:
            order='ASC' if re.search(r'it nhat|thap nhat',text) else 'DESC'
            from .domains import top_k
            k=top_k(text)
            return plan(f"SELECT product_name FROM {keyed} ORDER BY stock_quantity {order},k LIMIT {k}",kind='list' if k>1 else 'string',shape='column' if k>1 else 'scalar',rounding='none',reason='Item ranking by closing stock in base units; ties by code.')
        product=resolve_product(frame,question,engine,['v_stock_snapshot','v_stock_events','v_lots'])
        if not product:
            return None
        where=' WHERE norm(product_name)=norm('+literal(product)+')'
        unit=re.search(r'(?:bao nhieu|bnhiu|bao nhiu|may)\s+([a-z]+(?:\s+\d+\s*m3?|\s+\d+m)?)\s+(?:chan|nguyen)\b',text) or re.search(r'(?:bao nhieu|bnhiu|bao nhiu|may)\s+(xe\s*4m3|thung|hop|tan|kien|cay|loc)\b',text)
        if unit and re.search(r'chan|nguyen|\ble\b',text):
            factor=f'unit_factor(product_id,product_name,{literal(unit[1])})'
            return plan(f'SELECT FLOOR(stock_quantity/{factor}),stock_quantity-FLOOR(stock_quantity/{factor})*{factor} FROM '+source+where,kind='list',shape='row',rounding='none',reason='Whole requested packs plus remainder in base units.')
        return plan('SELECT SUM(stock_quantity) FROM '+source+where,rounding='none',reason='Closing stock at requested cutoff, in base units.')
    if re.search(r'nhap.*tru|chenh.*nhap.*ban|lay so nhap',text) and 'v_purchases' in engine.tables and 'v_sales' in engine.tables:
        if not periods:
            return None
        product=resolve_product(frame,question,engine,['v_purchases','v_sales','v_products'])
        if not product:
            return None
        period=periods[0]
        common=f"norm(product_name)=norm({literal(product)})"
        purchase=f"SELECT COALESCE(SUM(quantity*unit_factor(product_id,product_name,unit)),0) value FROM v_purchases WHERE {common} AND purchase_date>=TIMESTAMP {literal(period.start)} AND purchase_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))}"
        sales=f"SELECT COALESCE(SUM(base_quantity),0) value FROM v_sales WHERE {common} AND order_date>=TIMESTAMP {literal(period.start)} AND order_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))}"
        return plan('WITH p AS ('+purchase+'),s AS ('+sales+') SELECT p.value-s.value FROM p,s',rounding='none',reason='Purchased minus valid sold quantity, both in base units, for the requested period.')
    # Item names can hold money-looking words ('Xi măng Hà Tiên'): money words are looked for outside them.
    bare=without_item_names(text,engine)
    if 'v_purchases' not in engine.tables and re.search(r'\bnhap (?:hang|ve|kho)\b|tien nhap|phieu nhap|gia tri nhap|nha cung cap|\bncc\b',bare) and not re.search(r'\bban\b|doanh thu',bare):
        # §7: purchases are not recorded by this shop (unless an unread table might hold them).
        return missing_source(catalog,'purchase','Nguồn không ghi phiếu nhập hàng.')
    asked=re.search(r'\bnhap\b(?! khau)(?: (?:ve|them|vao|hang|kho))* (?:duoc )?(?:tong cong |tong )?(?:bao nhieu|bnhiu|bao nhiu|may|bn) ([a-z0-9]+(?: \d+m3?)?)',text)
    if 'v_purchases' in engine.tables and asked and asked[1] not in {'tien','dong','trieu','nghin','phan'} and not re.search(r'\btien\b|gia nhap|gia tri|tri gia|nha cung cap|\bncc\b|binh quan|\btru\b|chenh',bare):
        if not periods:
            return None
        period=periods[0]
        within=f"purchase_date>=TIMESTAMP {literal(period.start)} AND purchase_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))}"
        if asked[1] in {'phieu','lan','don','dot','chuyen'}:
            _,documents=engine.lookup('SELECT COUNT(purchase_id) FROM v_purchases')
            if not documents[0][0]:
                return None
            return plan(f"SELECT COUNT(DISTINCT purchase_id) FROM v_purchases WHERE {within}",rounding='integer',reason='Purchase documents dated in the period, cancelled documents excluded.')
        product=resolve_product(frame,question,engine,['v_purchases','v_products','v_sales'])
        if not product:
            return None
        # Quantity received, in base units (rulebook §10), or in the larger unit asked when the item has that unit.
        base=f"SELECT COALESCE(SUM(quantity*unit_factor(product_id,product_name,unit)),0) v,FIRST(product_id) pid FROM v_purchases WHERE norm(product_name)=norm({literal(product)}) AND {within}"
        return plan(f"SELECT v/COALESCE(NULLIF(unit_factor(pid,{literal(product)},{literal(asked[1])}),0),1) FROM ({base})",rounding='none',reason='Quantity received in the period (base units, or the larger unit asked).')
    if re.search(r'(?:tien|tri gia|gia tri).*(?:nhap)|nhap.*(?:bao nhieu|bnhiu|bao nhiu|\bbn\b|may).*tien|nhap.*het.*tien',bare) and not re.search(r'binh quan|gia nhap|nha cung cap',text) and 'v_purchases' in engine.tables:
        if not periods:
            return None
        period=periods[0]
        return plan(f"SELECT SUM(cost) FROM v_purchases WHERE purchase_date>=TIMESTAMP {literal(period.start)} AND purchase_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))}",reason='Total recorded purchase line cost, cancelled purchase documents excluded.')
    # 'giá bình quân một hộp' is the purchase average (rulebook); sales averages are 'doanh thu bình quân', 'giá bán bình quân'.
    if re.search(r'gia nhap.*binh quan|nhap.*binh quan|(?<!ban )\bgia binh quan (?:mot|1|moi)\b',text):
        if 'v_purchases' not in engine.tables:
            return missing_source(catalog,'purchase','Nguồn không ghi phiếu nhập và giá nhập theo lượng để tính bình quân.')
        if not periods:
            return None
        product=resolve_product(frame,question,engine,['v_purchases','v_products','v_sales'])
        if not product:
            return None
        period=periods[0]
        where=f" WHERE norm(product_name)=norm({literal(product)}) AND purchase_date>=TIMESTAMP {literal(period.start)} AND purchase_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))}"
        _,coverage=engine.lookup('SELECT COUNT(*),COUNT(cost) FROM v_purchases'+where)
        if coverage[0][0] and coverage[0][0]!=coverage[0][1]:
            return missing('Phiếu nhập không ghi đơn giá/thành tiền để tính bình quân có trọng số.')
        unit=re.search(r'(?:mot|moi|1)\s+(hop|thung|tan|kien|cay|loc|bao|goi|chai|binh)\b',text)
        quantity='quantity'
        if unit:
            quantity=f"quantity*unit_factor(product_id,product_name,unit)/unit_factor(product_id,product_name,{literal(unit[1])})"
        return plan('SELECT SUM(cost)/NULLIF(SUM('+quantity+'),0) FROM v_purchases'+where,reason='Weighted purchase price: total purchase cost divided by received quantity in requested units.')
    if re.search(r'gia ban|don gia|\bban\b.*\bgia (?:bao nhieu|bn|may)|\bgia (?:bao nhieu|bn) (?:mot|1|moi)\b|\bban (?:bao nhieu|nhieu|bn)(?: tien)? (?:mot|1|moi) [a-z]+',text) and re.search(r'ngay|\d{1,2}/\d{1,2}/20\d{2}',text) and not re.search(r'tang|giam|thay doi|len gia|nhap',text):
        if 'v_sales_all' not in engine.tables or not periods:
            return None
        product=resolve_product(frame,question,engine,['v_sales_all','v_products'])
        if not product:
            return None
        period=periods[0]
        per=re.search(r'(?:mot|moi|1)\s+([a-z]+)\b',text)
        scale=f"*unit_factor(product_id,product_name,{literal(per[1])})" if per else ''
        query=f"SELECT DISTINCT unit_price*quantity/NULLIF(base_quantity,0){scale} FROM v_sales_all WHERE norm(product_name)=norm({literal(product)}) AND order_date>=TIMESTAMP {literal(period.start)} AND order_date<TIMESTAMP {literal(period.end+dt.timedelta(days=1))} AND unit_price IS NOT NULL"
        _,rows=engine.lookup(query)
        if len(rows)==1:
            return plan(query,reason='Recorded base-unit sale price before line discount; includes every order status.')
    return None
