"""Ground model frames in explicit question constraints, independent of gold."""
import re
import unicodedata

from .lexicon import VOCAB
from .parsing import fold,parse_number

# Common chat abbreviations, matched on folded whole words. Values are folded text.
ABBREVIATIONS={'trc':'truoc','nhiu':'nhieu','hqua':'hom qua','hnay':'hom nay','dthu':'doanh thu','bnhiu':'bao nhieu','bnhieu':'bao nhieu',
               'dc':'duoc','ko':'khong','hok':'khong','vs':'voi','sp':'san pham','bnh':'bao nhieu',
               'hq':'hom qua','thg':'thang','tb':'trung binh','kh':'khach','sl':'so luong','dzay':'vay','dza':'vay','hong':'khong','hem':'khong',
               'ntn':'nhu the nao','bnhiu':'bao nhieu','bao nhiu':'bao nhieu','nhiu':'nhieu','đc':'duoc'}
# Brand-like tokens that look like Telex typos but are real words in product names.
TELEX_EXCEPTIONS={'max','air','lays','oreos'}


def telex_strip(token):
    """Remove Telex diacritic keystrokes left in an ASCII word (thangs->thang, truwowcs->truoc)."""
    value=token.replace('dd','d')
    value=re.sub(r'uow','uo',value)
    value=re.sub(r'(a|e|o)\1',r'\1',value)
    value=re.sub(r'([aou])w',r'\1',value)
    if len(value)>2 and value[-1] in 'sfrxj' and re.search(r'[aeiouy]',value[:-1]):
        value=value[:-1]
    return re.sub(r'([aeiouy])[sfrxj]([a-z])',r'\1\2',value)


# Abbreviations that are also another word once accented: only these accented spellings mean the abbreviation
# ('hông', 'hổng' = 'không'; 'hồng', and 'Hồng' in names, stay).
AMBIGUOUS_ABBREVIATIONS={'hong':{'hông','hổng'}}


def repair_word(word):
    folded=fold(word)
    if folded in ABBREVIATIONS and (folded not in AMBIGUOUS_ABBREVIATIONS or word.isascii() or unicodedata.normalize('NFC',word).lower() in AMBIGUOUS_ABBREVIATIONS[folded]):
        return ABBREVIATIONS[folded]
    if word.isascii() and word.isalpha() and len(folded)>1 and folded not in VOCAB and folded not in TELEX_EXCEPTIONS:
        stripped=telex_strip(folded)
        if stripped!=folded and stripped in VOCAB:
            return stripped
    return word


# Unaccented 'hong' is both slang for 'không' and the end of 'hoa hồng' (commission). The word before decides.
HONG_NOUN_PRECEDERS={'hoa'}


def repair_word_in_context(match,text):
    word=match.group()
    if fold(word)=='hong' and word.isascii():
        previous=re.search(r'([^\W\d_]+)\W*$',text[:match.start()])
        if previous and fold(previous.group(1)) in HONG_NOUN_PRECEDERS:
            return word
    return repair_word(word)


def normalize_question(text):
    """Repair keyboard typos/abbreviations in intent words; names and numbers are kept."""
    text=unicodedata.normalize('NFC',text)
    text=re.sub(r'(?i)\bo\s+đờ\b|\bo\s+do\b|\bđh\b|\bdh\b|\bđơng\b', 'đơn', text)
    text=re.sub(r'(?i)\bkòn(?=\s+(?:nợ|no|thiếu|thieu|hàng|hang)\b)', 'còn', text)
    text=re.sub(r'(?i)\bngừi\b', 'người', text)
    text=re.sub(r'(?i)\bmí(?=\s+(?:người|ngừi|phiếu|khách|kỳ|đơn))', 'mấy', text)
    text=re.sub(r'(?i)\bnhứt\b', 'nhất', text)
    text=re.sub(r'(?i)\bnv\b', 'nhân viên', text)
    text=re.sub(r'(?i)\bbìn quân\b', 'bình quân', text)
    text=re.sub(r'(?i)\bxi ô đi\b|\bxi o di\b', 'COD', text)
    text=re.sub(r'(?i)\b(hàng|hang|thợ|tho|khách|khach|ngày|ngay) mô\b', r'\1 nào', text)
    text=re.sub(r'(?i)\bvâu chơ\b|\bvau cho\b', 'voucher', text)
    text=re.sub(r'(?i)\bhuê hồng\b', 'hoa hồng', text)
    text=re.sub(r'(?i)\bcất hàng\b|\bcat hang\b', 'nhập hàng', text)
    text=re.sub(r'(?i)\bđánh hàng về\b|\bdanh hang ve\b', 'nhập hàng', text)
    text=re.sub(r'(?i)\bđứt hàng\b|\bdut hang\b', 'hết hàng', text)
    text=re.sub(r'(?i)\bnhìu\b', 'nhiều', text)
    text=re.sub(r'(?i)\bnhta\b', 'nhất', text)
    text=re.sub(r'(?i)\bbn\b', 'bao nhiêu', text)
    # 'dt' is revenue shorthand, except when counting phones ('mấy dt', 'chiếc dt').
    text=re.sub(r'(?i)(?<![\w])(mấy|may|máy|chiếc|chiec|cái|cai)?(\s*)\bdt\b(?![\w])',lambda m:m.group(0) if m.group(1) else m.group(2)+'doanh thu',text)
    # 'người mua' is a customer ('bao nhiêu người mua còn nợ' counts customers).
    text=re.sub(r'(?i)\b(?:người|nguoi)\s+(?:mua|mua hàng|mua hang)\b','khách',text)
    # A doctor's prescription is 'kê đơn' (rulebook §6 Nhà thuốc): 'không cần đơn bác sĩ' = không kê đơn.
    text=re.sub(r'(?i)\b(?:không|khong|ko|hok)\s+(?:cần|can|phải|phai|có|co)\s+(?:(?:đơn|don)(?:\s+(?:của|cua))?\s+(?:bác sĩ|bac si|bs)|toa(?:\s+(?:của\s+|cua\s+)?(?:bác sĩ|bac si|bs))?)\b','không kê đơn',text)
    text=re.sub(r'(?i)\b(?:theo|cần|can|có|co)\s+(?:đơn|don|toa)\s+(?:của\s+|cua\s+)?(?:bác sĩ|bac si|bs)\b|\b(?:kê|ke)\s+(?:toa)\b','kê đơn',text)
    # 'T2 tuần trước' is Monday; 'T7/2026' or 'T7 vừa rồi' may be July and stay as written.
    text=re.sub(r'(?i)\bt([2-7])(?=\s+(?:tuần|tuan)\b)',r'thứ \1',text)
    # Superlative 'nhất' mistyped or in dialect ('nhiều tiền nhit', 'đắt nhiệt', 'cao nhứt') after an adjective of amount.
    text=re.sub(r'(?i)\b((?:nhiều|nhieu|nhìu|nhiu|cao|đắt|dat|lớn|lon|ít|it|thấp|thap|chạy|chay|hot|đông|dong|tốt|tot|lời|loi|lãi|lai|khách|khach|tiền|tien|tiềnf|tienf|đơn|don)(?:\s+[^\W\d_]+){0,2}?\s+)(?:nhiệt|nhiet|nhit|nhứt|nhut|nhât|nhầt|nhát|nhaats|nhaast|nhats|nhaat|nhhat|nhatt|nhhaat)(?![\w])',lambda m:m[1]+('nhat' if m[0].isascii() else 'nhất'),text)
    return re.sub(r'[^\W\d_]+',lambda match:repair_word_in_context(match,text),text)


def weekday_requested(text):
    raw=unicodedata.normalize('NFC',str(text)).lower()
    # 'thứ tự' (order) is not 'thứ tư' (Wednesday); folding erases the difference.
    raw=re.sub(r'thứ\s*tự','trinh tu',raw)
    text=fold(raw)
    text=re.sub(r'(?:theo|dung|ghi|lan luot|xep)\s+(?:dung\s+)?thu tu|thu tu\s+(?:thoi gian|do|cau hoi|tren|nhu tren|sau|tang|giam|nhu)','trinh tu',text)
    for match in re.finditer(r'\bthu\s+(hai|ba|tu|nam|sau|bay|[2-7])\b',text):
        # 'doanh thu từ ...' is revenue FROM, not Wednesday.
        before=text[:match.start()].split()
        if before and before[-1] in {'doanh','tien'}:
            continue
        key=match[1]
        return {'hai':1,'ba':2,'tu':3,'nam':4,'sau':5,'bay':6}.get(key, int(key)-1 if key.isdigit() else 0)
    return 7 if re.search(r'chu nhat|\bcn\b',text) else 0


def wants_percent(text):
    return bool(re.search(r'phan tram|(?:bao nhieu|bao nhiu|bnhiu|bnhieu|bn|may|mi|nhieu|nhiu|nhieu|bnh)\s*%|ty le|ti le|chiem.*%|ti trong|ty trong',fold(text)))


PAYMENT_PATTERNS=[('cash',r'tien mat|\btm\b|tien tuoi'),('transfer',r'chuyen khoan|\bck\b|banking'),('ewallet',r'vi dien tu|momo|zalo ?pay|vnpay|shopee ?pay'),
                  ('cod',r'\bcod\b|\bthu ho\b|giao hang thu tien'),('debt',r'\bghi no\b|\bmua chiu\b|\bban (?:chiu|no)\b')]
CHANNEL_PATTERNS=[('store',r'tai quay|tai cua hang|tai tiem|tai quan|truc tiep tai|mua truc tiep'),('phone',r'qua dien thoai|goi dien'),('zalo_oa',r'zalo(?! ?pay)'),
                  ('marketplace',r'san tmdt|san thuong mai(?: dien tu)?|tren san\b|qua san\b|shopee|lazada|tiki\b|tiktok shop')]


def payments_in(text):
    text=fold(text)
    return [value for _,value in sorted((m.start(),value) for value,pattern in PAYMENT_PATTERNS for m in [re.search(pattern,text)] if m)]


def channels_in(text):
    text=fold(text)
    found=[value for _,value in sorted((m.start(),value) for value,pattern in CHANNEL_PATTERNS for m in [re.search(pattern,text)] if m)]
    if found==['store'] and not re.search(r'tai quay|truc tiep|kenh|online|ban qua|dat qua|don qua|ship|giao hang|zalo|dien thoai|san tmdt|tren san|(?:ban|mua|don|kenh|ban hang|mua hang) tai (?:tiem|quan|shop)\b',text):
        return []  # 'tại cửa hàng' alone is where the shop is, not a sales channel
    return found


def asks_table(question):
    raw=unicodedata.normalize('NFC',str(question)).lower()
    text=fold(raw)
    return bool('bảng' in raw or re.search(r'\bkem\b|cung voi so|cung so (?:tien|luong)|moi nguoi (?:bao nhieu|ban)|tung (?:nguoi|khach|mat hang|thang|ngay)|va so (?:tien|luong|don)',text))


def superlative(text):
    text=re.sub(r'gan nhat|it nhat|toi thieu|som nhat|muon nhat',' ',fold(text))
    return bool(re.search(r'\bnhat\b|\btop\b|cao diem|dung dau|hang dau|ban chay|ua chuong|ua thich|best ?seller|(?<![/\d])\d{1,2}\s+(?:mat hang|san pham|khach|mon|thuoc)\b',text))


TIME_OR_FILLER_FILTERS={'hom qua','hom nay','bua nay','bua ni','hom ni','ca chieu','ca sang','san pham','mat hang','hang','mon','cua hang','con lai','tat ca',
                        'quan','tiem','shop','cua','khach','khach hang','don','don hang','tuan truoc','tuan nay','thang nay','thang truoc','size m','size l'}


# Words that make up questions, never a product/customer name by themselves.
QUESTION_WORDS=set('''cua hang quan tiem shop mat san pham mon cai nao gi tat ca moi cac nhung nguoi mua ban duoc hom nay qua bua ni tuan thang nam ngay tong cong bao
nhieu tien doanh thu ve don hoa khach cho hoi xem giup voi va la co khong nhat chay hot loai thu toi minh anh chi em chu ban nha nhe ha vay roi trong cung luong so
bn nua mang lai duoc dc'''.split())


def junk_filter(value):
    key=re.sub(r'[^a-z0-9]+',' ',fold(normalize_question(value))).strip()
    if key and all(token in QUESTION_WORDS for token in key.split()):
        return True  # 'cái hàng', 'hàng nào', 'cùng hàng', 'người mua'
    return not key or key in TIME_OR_FILLER_FILTERS or bool(re.fullmatch(r'size\s*[ml]|(?:ca\s+)?(?:sang|chieu)|[\d\s]+|\d{1,2}\s+\d{2,4}',key)) \
        or bool(re.match(r'(?:moi|tat ca|cac loai|toan bo|ca)\s+(?:loai|mon|mat hang|do uong|hang)\b|(?:thang|tuan|quy|ngay)\s+\d|khach\s+(?:tra no|mua chiu|ghi no|co mua|da mua|con no)',key))


def threshold_from_text(text):
    """Explicit numeric threshold on a count ('từ 4 đơn trở lên', 'ít nhất 20 đơn', 'hơn 3 lần')."""
    match=re.search(r'(it nhat|toi thieu|tu|tren|hon|qua|duoi|it hon)\s*(\d+)\s*(don|lan|ngay|bill|hoa don)\b\s*(tro len|tro xuong)?',fold(text))
    if not match:
        return None
    operator={'it nhat':'>=','toi thieu':'>=','tu':'>=','tren':'>','hon':'>','qua':'>','duoi':'<','it hon':'<'}[match[1]]
    if match[4]=='tro len':
        operator='>='
    elif match[4]=='tro xuong':
        operator='<='
    return float(match[2]),operator,match[3]


def each_month(question,text):
    """'từng tháng', 'mỗi tháng', 'lần lượt': one number per period. 'mới' (new) folds like 'mỗi', so the accent decides
    when the question is written with accents."""
    raw=unicodedata.normalize('NFC',question).lower()
    accented=raw!=fold(raw)
    each=re.search(r'(?<!\w)(?:từng|mỗi) tháng',raw) if accented else re.search(r'tung thang|moi thang',text)
    return bool(each or re.search(r'lan luot|theo.*thu tu|dung thu tu',text))


def ground_frame(frame,question):
    frame=frame.model_copy(deep=True)
    question=normalize_question(question)
    text=fold(question)
    # A customer's name is not intent words: 'Đặng Bình Quân' is a customer, not 'bình quân' (average).
    for value in (frame.customer_filter,frame.customer_group_filter):
        name=re.sub(r'\s+',' ',re.sub(r'[^a-z0-9]+',' ',fold(value or ''))).strip()
        # Only what looks like a real name: two words or more, not a generic word ('mặt hàng', 'quầy') or a number ('93.000').
        if len(name.split())>=2 and not re.fullmatch(r'[\d ]+',name) and not re.match(r'(?:mat hang|san pham|hang hoa|mon|hang|khach|nhom|loai|cac|tat ca|moi)\b',name) \
                and re.search(r'(?<![a-z0-9])'+re.escape(name)+r'(?![a-z0-9])',text):
            text=re.sub(r'(?<![a-z0-9])'+re.escape(name)+r'(?![a-z0-9])',' ',text)
    text=re.sub(r'\s+',' ',text)
    if re.search(r'\b(?:lai|loi) gop\b|loi nhuan gop',text) and not re.search(r'\b(?:lai|loi) rong\b',text):
        frame.metric='gross_profit'   # 'lời gộp' = 'lãi gộp'
        if frame.operation=='other':
            frame.operation='value'
    if re.search(r'doanh thu|\bdthu\b|tien ban|tien hang|tien mua|\bthu ve\b|ban duoc (?:bao nhieu|bn|may|nhieu) tien',text) and not re.search(r'(?:lai|loi) gop|(?:lai|loi) rong|gia von|no them|\bso luong\b|(?:\bmay\b|bao nhieu|\bbn\b)\s+(?:dia|ly|chai|lon|cai|hop|goi|vien|tuyp|lo|phan|cay|bao|thung|con|chiec|kg|bich|vi|cuon|tam|bo|xe)\b',text):
        frame.metric='revenue'
        if frame.operation=='other':
            frame.operation='value'
    amount_threshold=re.search(r'\b(?:don|bill|hoa don)\b.*(?:tien hang|doanh thu|gia tri|dthu|thu ve|tien ban).*?(dat(?:\s+tu)?|tu\b|tren\b|hon\b|qua\b|duoi\b|>=|≥|>|<=|≤|<)\s*([\d.,]+(?:tr\d{1,3})?)\s*(?:(k|tr|trieu|nghin|cu)\b)?\s*(?:d|dong|vnd)?\s*(tro len|tro xuong|tro di)?',text)
    if amount_threshold:
        frame.metric,frame.operation,frame.group_by,frame.output_type='orders','threshold_count','none','number'
        frame.threshold=float(parse_number(amount_threshold[2]+('tr' if amount_threshold[3]=='cu' else amount_threshold[3] or '')))
        operator={'>=':'>=','≥':'>=','<=':'<=','≤':'<=','<':'<','>':'>','tu':'>=','dat':'>=','dat tu':'>=','duoi':'<'}.get(amount_threshold[1],'>')
        if amount_threshold[4] in {'tro len','tro di'}:
            operator='>='
        elif amount_threshold[4]=='tro xuong':
            operator='<='
        frame.threshold_operator=operator
    if re.search(r'trung binh.*(?:moi|mot|1).*(?:don|bill|hoa don)|binh quan.*(?:don|bill|hoa don)|\bbq\b.*(?:don|bill)',text):
        frame.metric,frame.operation,frame.group_by,frame.output_type='revenue','average_order','none','number'
    for name in ('product_filter','customer_filter','group_filter','customer_group_filter'):
        if getattr(frame,name) and junk_filter(getattr(frame,name)):
            setattr(frame,name,'')
    for name in ('product_filter','customer_filter','employee_filter','group_filter','customer_group_filter','unit_filter','payment_filter','channel_filter','size_filter'):
        # An empty value written out ('none', 'null', 'n/a', '-') is no filter.
        if re.fullmatch(r'\s*(?:none|null|nan|n/?a|-+|khong co|không có|trong|trống)?\s*',str(getattr(frame,name) or ''),re.I):
            setattr(frame,name,'')
    payments=payments_in(text)
    frame.payment_filter=payments[0] if len(payments)==1 else ''
    channels=channels_in(text)
    if len(channels)==1:
        frame.channel_filter=channels[0]
    elif channels or not re.search(r'kenh|ban qua|don qua|dat qua|san tmdt|shopee|lazada|tiktok|facebook|grab',text):
        frame.channel_filter=''
    if not re.search(r'\(([ml])\)|size\s*([ml])\b',text):
        frame.size_filter=''
    frame.weekday_filter=weekday_requested(question)
    if wants_percent(text):
        frame.output_type='percent'
        if frame.operation=='difference':
            frame.operation='growth'
    employees=re.findall(r'\bnv\d+\b',text)
    if len(employees)==1:
        frame.employee_filter=employees[0].upper()
    size=re.search(r'\(([ml])\)|size\s*([ml])\b',text)
    if size:
        frame.size_filter=(size[1] or size[2]).upper()
    if frame.group_filter and frame.product_filter:
        group,product=(re.sub(r'^(?:nhom hang|nhom)\s+','',re.sub(r'[^a-z0-9]+',' ',fold(v)).strip()) for v in (frame.group_filter,frame.product_filter))
        if product and group and (product in group or group in product):
            frame.product_filter=''
    if frame.group_filter and not frame.customer_group_filter and re.search(r'khach (?:hang )?(?:thuoc )?(?:nhom|hang)\b|nhom khach',text):
        frame.customer_group_filter,frame.group_filter=frame.group_filter,''
    if re.search(r'(?:ban )?chay nhat|hot nhat|ban nhieu nhat|best ?seller',text):
        frame.metric,frame.operation,frame.group_by='quantity','top','product'
    if 'ua chuong' in text:
        frame.metric,frame.operation,frame.group_by='product_order_count','top','product'
    if re.search(r'hang nao loi nhat|hang loi nhat',text):
        frame.metric,frame.operation,frame.group_by='gross_profit','top','product'
        if not re.search(r'kem|bao nhieu|bnhiu',text):
            frame.output_type='string'
    if frame.operation=='top' and frame.group_by=='customer' and re.search(r'nguoi ban|nhan vien|thu ngan|ai ban',text):
        frame.group_by='employee'
    explicit=threshold_from_text(text)
    if explicit and re.search(r'\bkhach\b',text) and frame.group_by in {'customer','none'} and frame.operation in {'share','threshold_share','threshold_count','value','other'} and not re.search(r'\bno\b|con thieu|chua tra|cong no|han thanh toan',text):
        frame.group_by='customer'
        frame.operation='threshold_share' if wants_percent(text) else 'threshold_count'
    if frame.operation in {'threshold_count','threshold_share'} and explicit:
        frame.threshold,frame.threshold_operator=explicit[0],explicit[1]
    if frame.operation in {'threshold_count','threshold_share'} and frame.group_by=='customer':
        if re.search(r'\d+\s*(?:don|lan mua|bill|hoa don)\b',text):
            frame.metric='orders'
        elif re.search(r'\d+\s*ngay\b',text):
            frame.metric='purchase_days'
    if re.search(r'ti trong|ty trong|\bchiem\b',text) and (frame.group_filter or frame.product_filter or frame.customer_group_filter or frame.payment_filter or frame.channel_filter) and not explicit and frame.operation!='top':
        frame.operation,frame.group_by,frame.output_type='share','none','percent'
    if frame.operation=='top' and frame.group_by in {'product','customer','employee','day','month','hour'} and not superlative(text):
        frame.operation,frame.group_by='value','none'
        if frame.output_type in {'list','table','string','date'}:
            frame.output_type='percent' if wants_percent(text) else 'number'
    if frame.operation=='top' and frame.group_by in {'product','customer','employee','day','month'}:
        # 'top 5', '5 mặt hàng', 'liệt kê 5 ...', '5 bữa nào' (also with the item word mistyped: '5 mang hàng')
        rank=re.search(r'\btop\s*(\d{1,2})\b|(?<![/\d.,])(\d{1,2})\s*(?:khach|mat hang|sp|san pham|mon|thuoc|best ?seller|ma?n?g? hang|loai hang)\b'
                       r'|\bliet ke\s+(?:ra\s+)?(\d{1,2})\b|(?<![/\d.,])(\d{1,2})\s+(?:ngay|bua|hom)\s+nao\b',text)
        if rank:
            frame.k=int(next(v for v in rank.groups() if v))
        elif not re.search(r'nhung (?:mat hang|khach|san pham|mon)|cac (?:mat hang|khach|san pham|mon)',text):
            frame.k=1
        if not re.search(r'kem|cung.*(?:so|tien)|va.*(?:so luong|doanh thu|so tien)|voi.*so no',text):
            frame.output_type='date' if frame.group_by in {'day','month'} and frame.k==1 else 'list' if frame.k>1 else 'string'
    if frame.operation=='average_order' and frame.group_by=='customer' and not re.search(r'trung binh|binh quan|\bbq\b|\bsop\b',text):
        frame.operation='top'
    if frame.operation=='daily_average' and not re.search(r'trung binh|binh quan|\bbq\b|moi ngay|(?:mot|1) ngay',text):
        frame.operation,frame.group_by='value','none'
    if re.search(r'ngay nao.*(?:cao|nhieu|dat|lon|tot).*nhat',text):
        frame.operation,frame.group_by,frame.output_type='top','day','date'
    if frame.metric=='quantity' and frame.operation not in {'top','threshold_count','threshold_share'} and not re.search(r'tung ngay|moi ngay|theo ngay|lan luot|cao nhat|nhieu nhat|ban chay|chay nhat|hot nhat|\btop\b|best ?seller',text):
        frame.operation,frame.group_by,frame.output_type='value','none','number'
        if fold(frame.product_filter) in {'ly','chai','goi','cay','vien','bich','cuon','lon','cai','phan'}:
            frame.unit_filter,frame.product_filter=frame.product_filter,''
        if fold(frame.product_filter) in {'cac mon','moi mon','tat ca cac mon','size m','size l','ly size m','ly size l'} or re.search(r'cong (?:tat ca|moi)|tat ca cac mon',text):
            frame.product_filter=''
    if re.search(r'\b\d+\s*ngay\s*(?:gan nhat|gan day|qua|vua qua|vua roi)\b',text) and frame.group_by=='day' and frame.operation in {'series','value'} \
            and not re.search(r'(?:moi|tung|cac|theo) ngay|lan luot|ngay nao|tung ngay mot',text):
        # 'doanh thu 7 ngày gần nhất': one total over the window (§2), not one figure per day.
        frame.operation,frame.group_by,frame.output_type='value','none','percent' if frame.output_type=='percent' else 'number'
    if frame.operation=='series' and frame.group_by in {'day','none'} \
            and re.search(r'luy ke|\btu dau (?:nam|thang|tuan|quy)\b.*\b(?:den|toi|nay)\b|\btinh (?:tu dau|den) (?:nam|thang|tuan|nay|hom nay|bay gio)\b',text) \
            and not re.search(r'(?:moi|tung|cac|theo) (?:ngay|thang|tuan)|lan luot|(?:ngay|thang|tuan) nao|tung ngay mot',text):
        # 'lũy kế từ đầu năm tới nay', 'tính từ đầu tuần đến nay': one running total, not one figure per period.
        frame.operation,frame.group_by,frame.output_type='value','none','percent' if frame.output_type=='percent' else 'number'
    if re.search(r'tang lien tuc|tang deu.*(?:khong|ko|k)|(?:luon|deu) cao hon.*(?:khong|ko|\bk\b|ha\b|hay khong)|co phai.*cao hon',text):
        frame.operation,frame.output_type='trend','boolean'
    if re.search(r'(?:thang nao|th nao).*(?:cao|nhieu|dat nhat|lon nhat|tot nhat)|(?:cao nhat|lon nhat|nhieu tien nhat).*thang nao',text):
        frame.operation,frame.group_by,frame.output_type='top','month','date'
    elif each_month(question,text) and re.search(r'thang|\bth\s*\d|\bt\d',text) and not re.search(r'co tang|tang lien tuc',text) and frame.operation!='trend':
        if frame.operation not in {'top','threshold_count','threshold_share'}:
            frame.operation,frame.output_type='series','list'
    if frame.output_type=='table' and not asks_table(question):
        if frame.operation=='top':
            frame.output_type='date' if frame.group_by in {'day','month'} and frame.k==1 else 'list' if frame.k>1 else 'string'
        elif frame.operation=='series':
            frame.output_type='list'
        else:
            frame.output_type='percent' if wants_percent(text) else 'number'
    if frame.output_type in {'string','list','boolean','date'} and frame.operation in {'value','average_order','daily_average','threshold_count'} and re.search(r'(?:bao nhieu|may|\bbn\b|tong cong)',text) \
            and not re.search(r'lan luot|moi loai|moi ben|tung loai|ca hai|\bva\b.*\bbao nhieu\b.*\bbao nhieu\b',text):
        frame.output_type='percent' if wants_percent(text) else 'number'
    if frame.weekday_filter and re.search(r'(?:trung binh|binh quan|\bbq\b)\s*(?:moi|mot|1)?\s*(?:ngay\s*)?(?:thu\s*(?:hai|ba|tu|nam|sau|bay|[2-7])|chu nhat|cn)\b',text) \
            and frame.operation in {'value','other'} and not re.search(r'\b(?:moi|mot|1)\s+(?:don|bill|hoa don)\b',text):
        # Rulebook §2: average per weekday X divides by the number of weekday-X days in the period.
        frame.operation,frame.group_by='daily_average','none'
        if frame.output_type not in {'number','percent'}:
            frame.output_type='number'
    # Explicit count object beats a planner's value/quantity guess.
    if explicit and re.search(r"(?:mat hang|san pham|mon).*?(?:nam trong|co mat|xuat hien)",text):
        frame.metric,frame.operation,frame.group_by,frame.output_type='product_order_count','threshold_count','product','number'
        frame.threshold,frame.threshold_operator=explicit[:2]
    # A chain of comparisons asks for a boolean, not the monthly revenue vector.
    if re.search(r"thang sau.*luon (?:cao )?hon thang truoc",text) or (
        len(re.findall(r"cao hon",text))>=2 and re.search(r"dung|phai|khong",text)):
        frame.metric,frame.operation,frame.group_by,frame.output_type='revenue','trend','none','boolean'
    daily_threshold=re.search(r"(?:bao nhieu|may) (?:ngay|bua|hom)\b.*?(?:tu|dat tu|it nhat)\s*(\d+(?:[.,]\d+)*(?:tr\d{1,3})?)\s*(trieu|tr|k|cu|nghin)?(?:\s*(?:dong|vnd|d))?(?!\w|[.,/\-]\d)",text)
    if daily_threshold and (daily_threshold[2] or re.search(r"doanh thu|tien",text)):
        token=daily_threshold[1]+('tr' if daily_threshold[2]=='cu' else daily_threshold[2] or '')
        frame.metric,frame.operation,frame.group_by,frame.output_type='revenue','threshold_count','day','number'
        frame.threshold,frame.threshold_operator=float(parse_number(token)),'>='
    if frame.output_type=='percent' and re.search(r"so (?:voi|vs)|cao hay thap hon|tang hay giam",text) and frame.operation in {'value','other'}:
        frame.operation='growth'
    if re.search(r"(?:mat hang|hang|san pham|mon) (?:nao|gi).*lai gop.*nhat",text):
        frame.metric,frame.operation,frame.group_by,frame.output_type='gross_profit','top','product','string'
    named=named_ranking(text)
    if named:
        frame.metric,frame.operation,frame.group_by=named
        frame.k=1
        frame.output_type='number' if frame.group_by=='hour' else 'date' if frame.group_by=='day' else 'string'
    return frame


# Rulebook §3 named rankings: the phrase fixes metric and object, whatever the planner guessed.
NAMED_RANKINGS=[
    (r'\bgio\b.*(?:cao diem|dong khach nhat|dat khach nhat|dong nhat|nhieu don nhat)|khung gio.*(?:cao diem|dong|dat khach|nhieu don)|\bcao diem\b.*\bgio\b',('orders','top','hour')),
    (r'\b(?:ngay|hom)\b.*(?:dat khach|dong khach) nhat|(?:dat khach|dong khach) nhat.*\bngay\b',('orders','top','day')),
    (r'(?:khach|ai)\b.*trung thanh nhat|trung thanh nhat',('purchase_days','top','customer')),
    (r'\bsop nhat\b|khach\b.*\bsop\b',('revenue','average_order','customer')),
    (r'(?:hay|de|thuong|chuyen) doi y|doi y nhieu nhat|doi y nhat',('cancelled_orders','top','customer')),
    (r'(?:nhan vien|\bnv\b|\btho\b|\bai\b|nguoi).*(?:cham chi|sieng nang|sieng) nhat|(?:cham chi|sieng nang) nhat',('purchase_days','top','employee')),
]


def named_ranking(text):
    """(metric, operation, group_by) for a rulebook-defined superlative, else None."""
    if re.search(r'\bthu\s+(?:may|nao)\b',text):
        return None  # weekday rankings are not one of these objects
    for pattern,result in NAMED_RANKINGS:
        if re.search(pattern,text):
            if result[2]=='hour' and re.search(r'doanh thu|\bdthu\b|tien',text) and not re.search(r'cao diem|dong khach|nhieu don',text):
                return None
            return result
    return None


# Business concepts a question touches (folded text). Used to label compiler rejections so that aggregate error
# counts say WHICH kind of question lacks a compiler path; never used to choose a formula.
CONCEPTS=[('cong_no',r'(?:con|tra|ghi|cong|thu|khoan|bot|so) no\b|mua chiu|\bno (?:cu|moi|qua han|lai)|tuoi no|\bdu no'),
          ('ton_kho',r'\bton\b|trong kho|\bkho\b|con hang|het hang|con lai bao nhieu'),
          ('nhap_hang',r'\bnhap\b|nha cung cap|\bncc\b'),
          ('han_dung_lo',r'han dung|het han|\blo\b|\bdate\b'),
          ('bao_hanh',r'bao hanh'),
          ('tra_gop',r'tra gop|(?<!lai )(?<!loi )\bgop\b'),
          ('may_cu',r'may cu|thu cu|doi may|len doi|thu lai may'),
          ('voucher_diem_ship',r'voucher|ma giam|\bdiem\b|phi ship|\bship\b|phi giao'),
          ('bao_cao_ca',r'tien mat|chot ca|ket tien|bao cao ca'),
          ('giao_hang',r'giao hang|\bdot\b|van chuyen|da giao|chua giao|giao nhieu'),
          ('thanh_vien',r'thanh vien|dang ky'),
          ('size_so',r'\bsize\b|\bso sua\b|\bbim\b|\bta (?:dan|quan)'),
          ('dong_may',r'dong may|dung luong|\bgb\b|imei|dien thoai'),
          ('sua_xe',r'\btho\b|bien so|\bxe\b|lam gi|thay nhot|va ruot|sua dien|thay ruot'),
          ('gia',r'gia ban|don gia|gia nhap|gia von|gia (?:mot|1|moi)\b|bao nhieu tien (?:mot|1)'),
          ('lai',r'\blai\b|\bloi\b|loi nhuan'),
          ('thue',r'thue|\bvat\b'),
          ('nhan_vien',r'nhan vien|\bnv\b|cham chi|hoa hong|\bluong\b'),
          ('topping',r'topping|tran chau|thach|kem cheese'),
          ('khung_gio',r'khung gio|cao diem|\bgio\b')]


def question_concepts(text):
    folded=fold(normalize_question(text))
    return [name for name,pattern in CONCEPTS if re.search(pattern,folded)] or ['khac']
