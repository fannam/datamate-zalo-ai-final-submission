"""Question-to-plan checks, complementary to SQL schema/type validation."""
import re

from .intent import normalize_question,wants_percent,weekday_requested
from .parsing import fold


def validate_plan_intent(plan,question):
    if plan.action in {'abstain','unanswerable'}:
        return
    # Chat spellings ('bnhiu', 'bao nhiu') read as written out, the way the planner reads them.
    text=fold(normalize_question(question.question))
    if wants_percent(text) and plan.answer_type not in {'percent','list','table'}:
        raise ValueError('Câu hỏi yêu cầu phần trăm: cần SQL tính đúng tử/mẫu và answer_type=percent, không chỉ đổi nhãn một số chênh lệch.')
    names=re.search(r'\b(?:nhung|cac)\s+(?:sp|san pham|mat hang|hang|mon|thuoc|khach|khach hang|nhan vien|tho|xe|lo)\s+nao\b|\bliet ke\b'
                    r'|\b(?:khach|khach hang|nhan vien|tho|mat hang|mon|san pham|sp|thuoc|nha cung cap|ncc|dong xe|hang nao)\s+nao\b|\bai\b',text)
    if names and plan.action=='query' and plan.answer_type in {'number','percent','boolean','date'} and not re.search(r'bao nhieu|\bbn\b|\bmay\b|so luong|\bdem\b|tong|kem|moi (?:nguoi|mon|mat hang)|con so|chiem|ti le|ty le|phan tram|%|\bnhieu\b(?!\s+(?:[a-z]+\s+)?(?:nhat|nhut|nhit)\b)',text):
        raise ValueError('Câu hỏi hỏi tên (những ... nào / liệt kê): đáp án là danh sách tên, không phải một số.')
    if re.search(r'\bngay nao\b',text) and plan.action=='query' and plan.answer_type in {'number','percent','boolean'} and not re.search(r'bao nhieu|\bbn\b|\bmay\b|kem|va (?:ban|thu|doanh)',text):
        raise ValueError('Câu hỏi ngày nào: đáp án là một ngày, không phải một số.')
    if re.search(r'(?:thang nao|th nao).*(?:cao|nhieu|dat nhat)|(?:cao nhat|nhieu tien nhat).*thang nao',text):
        if plan.action=='query' and (plan.answer_type!='date' or plan.date_granularity!='month'):
            raise ValueError('Câu hỏi tháng nào cần SQL trả tháng và answer_type=date,date_granularity=month.')
    if not weekday_requested(question.question) and re.search(r'isodow\([^)]*\)\s*=\s*[1-7]',plan.sql,re.I):
        raise ValueError('Câu hỏi không yêu cầu lọc thứ trong tuần; bỏ điều kiện isodow. T7/th7 là tháng 7.')
    ids=re.findall(r'\bnv\d+\b',text)
    if ids and not all(i in plan.sql.lower() for i in ids):
        raise ValueError('SQL thiếu điều kiện mã nhân viên nêu trong câu hỏi: '+','.join(ids))
