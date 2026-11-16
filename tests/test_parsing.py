from datetime import date
from decimal import Decimal

import pytest

from datamate.parsing import guest, identifier, parse_datetime, parse_number, payment_method, round_decimal, status


@pytest.mark.parametrize("raw,expected", [
    ("90,000", "90000"), ("240.000đ", "240000"), ("4,38tr", "4380000"),
    ("270k", "270000"), ("1,01tr", "1010000"), ("5 chai", "5"),
    ("1.234.567,5", "1234567.5"), ("(10.000đ)", "-10000"), (12.5, "12.5"),
    ("1tr440", "1440000"), ("12tr500", "12500000"), ("-1tr250", "-1250000"),
])
def test_number_formats(raw, expected):
    assert parse_number(raw) == Decimal(expected)


@pytest.mark.parametrize("raw,year,expected", [
    ("1 thg 5 2025", None, date(2025, 5, 1)), ("01/02/2025", None, date(2025, 2, 1)),
    ("1/5", 2026, date(2026, 5, 1)), ("2026-09-30", None, date(2026, 9, 30)),
    ("45717", None, date(2025, 3, 1)),
    ("29/2", 2024, date(2024, 2, 29)),
])
def test_date_formats(raw, year, expected):
    assert parse_datetime(raw, year).date() == expected


def test_business_normalization():
    assert identifier("GiaVonLucBan") == "gia_von_luc_ban"
    assert status("Đơn khách trả lại") == "returned"
    assert status("Đã huỷ") == "cancelled"
    assert status("Đang giao") == "shipping"
    assert guest("Khách lẻ") and not guest("KH001")
    assert payment_method("COD") == "cod"
    assert payment_method("CK") == "transfer"
    assert round_decimal(Decimal("12.5"), 0) == Decimal("13")


@pytest.mark.parametrize('label',['Khách trả','khách mang trả','trả lại hàng','Đã hoàn hàng'])
def test_return_aliases(label):
    assert status(label)=='returned'


def test_shipping_does_not_match_cancel_substring():
    assert status('Đang vận chuyển')=='shipping'
    assert status('Đang giao dịch')=='shipping'
    assert status('Chờ giao')=='shipping'


@pytest.mark.parametrize("value,expected", [("Tại tiệm", "store"), ("tại quán", "store"), ("Sàn TMĐT", "marketplace"), ("Shopee", "marketplace"),
                                            ("Zalo", "zalo_oa"), ("điện thoại", "phone")])
def test_channel_labels(value, expected):
    from datamate.parsing import channel
    assert channel(value) == expected


@pytest.mark.parametrize("text,expected", [("doanh thu bán tại tiệm tháng 5", ["store"]), ("doanh thu tại cửa hàng tháng 5", []),
                                           ("doanh thu trên sàn TMĐT tháng 5", ["marketplace"])])
def test_channel_words_in_questions(text, expected):
    from datamate.intent import channels_in
    assert channels_in(text) == expected
