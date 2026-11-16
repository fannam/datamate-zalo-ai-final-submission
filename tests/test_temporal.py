import datetime as dt

import pytest

from datamate.temporal import resolve_periods


@pytest.mark.parametrize("question,start,end", [
    ("doanh thu hôm qua", "2026-05-09", "2026-05-09"),
    ("soos bill hqua", "2026-05-09", "2026-05-09"),
    ("tháng 3 năm 2026 thay đổi bao nhiêu %", "2026-03-01", "2026-03-31"),
    ("tháng này", "2026-05-01", "2026-05-10"),
    ("quý 1/2026", "2026-01-01", "2026-03-31"),
    ("T4/26", "2026-04-01", "2026-04-30"),
    ("tuần trước", "2026-04-27", "2026-05-03"),
    ("7 ngày gần nhất", "2026-05-04", "2026-05-10"),
    ("từ đầu năm tới nay", "2026-01-01", "2026-05-10"),
])
def test_periods(question, start, end):
    result = resolve_periods(question, dt.date(2026, 5, 10))[0]
    assert result.start.isoformat() == start
    assert result.end.isoformat() == end


def test_previous_partial_month_and_quarter():
    period = resolve_periods("tháng này", dt.date(2026, 3, 31))[0]
    assert period.previous().end == dt.date(2026, 2, 28)
    quarter = resolve_periods("quý 1/2026", dt.date(2026, 5, 10))[0]
    assert quarter.previous().start == dt.date(2025, 10, 1)
    assert quarter.previous().end == dt.date(2025, 12, 31)


def test_series_with_one_month_prefix():
    periods = resolve_periods("Doanh thu 3 tháng 4/2026, 5/2026, 6/2026 lần lượt?", dt.date(2026, 7, 1))
    assert [p.start.month for p in periods] == [4, 5, 6]


def test_dialect_and_price_comparison_windows():
    today=dt.date(2026,3,4)
    assert resolve_periods('Bữa ni bán bao nhiêu tiền?',today)[0].start==today
    assert resolve_periods('30 ngày qua kể cả hôm nay',today)[0].start==dt.date(2026,2,3)
    periods=resolve_periods('Từ ngày 15/02/2026 tăng giá; so 7 ngày kể từ ngày đó với 7 ngày liền trước tăng bao nhiêu %?',today)
    assert [(p.start,p.end) for p in periods]==[(dt.date(2026,2,15),dt.date(2026,2,21)),(dt.date(2026,2,8),dt.date(2026,2,14))]


def test_month_order_comes_from_question_not_prefix_precedence():
    periods=resolve_periods('4/2026 so với tháng 3/2026',dt.date(2026,6,1))
    assert [p.start.month for p in periods]==[4,3]


def test_entity_names_and_words_ending_in_t_are_not_months():
    import datetime as dt
    from datamate.temporal import resolve_periods
    today = dt.date(2026, 5, 20)
    periods = resolve_periods('doanh thu của bộ đèn tuýp led T8 1.2m 18W trong tháng 4 năm 2025', today, exclude=('bộ đèn tuýp led T8 1.2m 18W',))
    assert [(p.start, p.end) for p in periods] == [(dt.date(2025, 4, 1), dt.date(2025, 4, 30))]
    periods = resolve_periods('xét 3 tháng 3/2025, 4/2025 và 5/2025, doanh thu tháng nào lớn nhất', today)
    assert [p.start.month for p in periods] == [3, 4, 5] and all(p.start.year == 2025 for p in periods)


def test_spoken_relative_periods():
    import datetime as dt
    from datamate.temporal import resolve_periods
    today = dt.date(2026, 3, 18)
    assert resolve_periods('dthu tuần qua bao nhiêu', today)[0].start == dt.date(2026, 3, 9)
    assert resolve_periods('tháng qua bán được bao nhiêu', today)[0].start == dt.date(2026, 2, 1)
    period = resolve_periods('30 bữa qua thu về nhiêu', today)[0]
    assert (period.start, period.end) == (dt.date(2026, 2, 17), today)


def test_quarter_typo_without_y():
    periods = resolve_periods('xem giup tong dt qu2/2026 la bn ha', dt.date(2026, 8, 1))
    assert (periods[0].start, periods[0].end) == (dt.date(2026, 4, 1), dt.date(2026, 6, 30))


@pytest.mark.parametrize('text,expected', [
    ('doanh thu hôm kia', ('2026-08-18', '2026-08-18')),
    ('doanh thu từ ngày 1/8 đến 15/8', ('2026-08-01', '2026-08-15')),
    ('doanh thu từ 1/8 tới 15/8/2026', ('2026-08-01', '2026-08-15')),
    ('doanh thu ngày 15 tháng 8 năm 2026', ('2026-08-15', '2026-08-15')),     # a day, not the whole month
    ('doanh thu mùng 2 tháng 9', ('2025-09-02', '2025-09-02')),             # no year and after today: last year (§2)
    ('doanh thu năm 2025', ('2025-01-01', '2025-12-31')),
    ('doanh thu năm ngoái', ('2025-01-01', '2025-12-31')),
    ('doanh thu quý này', ('2026-07-01', '2026-08-20')),
    ('doanh thu quý trước', ('2026-04-01', '2026-06-30')),
    ('doanh thu quý II năm 2026', ('2026-04-01', '2026-06-30')),
    ('doanh thu tháng tám', ('2026-08-01', '2026-08-31')),
    ('doanh thu tháng vừa rồi', ('2026-07-01', '2026-07-31')),
    ('doanh thu tháng trước nữa', ('2026-06-01', '2026-06-30')),
    ('doanh thu tuần trước nữa', ('2026-08-03', '2026-08-09')),
    ('doanh thu từ đầu tháng đến nay', ('2026-08-01', '2026-08-20')),
    ('doanh thu từ đầu tuần', ('2026-08-17', '2026-08-20')),
    ('doanh thu ba ngày qua', ('2026-08-18', '2026-08-20')),
    ('doanh thu 6 tháng đầu năm', ('2026-01-01', '2026-06-30')),
    ('doanh thu 10 ngày đầu tháng 8', ('2026-08-01', '2026-08-10')),
    ('doanh thu sau đó 7 ngày từ ngày 3/8', ('2026-08-03', '2026-08-03')),
])
def test_rulebook_time_expressions(text, expected):
    periods = resolve_periods(text, dt.date(2026, 8, 20))
    assert (periods[0].start.isoformat(), periods[0].end.isoformat()) == expected


@pytest.mark.parametrize('text', ['doanh thu cuối tháng 7', 'doanh thu nửa đầu tháng 7', 'doanh thu 3 tháng qua', 'doanh thu mấy hôm nay', 'doanh thu tuần đầu tháng 8'])
def test_ambiguous_time_expressions_are_not_guessed(text):
    assert resolve_periods(text, dt.date(2026, 8, 20)) == []


def test_product_fraction_is_not_a_date():
    periods = resolve_periods('Ống PVC 1/2 bán bao nhiêu tháng 7', dt.date(2026, 8, 20))
    assert [(p.start, p.end) for p in periods] == [(dt.date(2026, 7, 1), dt.date(2026, 7, 31))]


def test_from_a_date_to_today():
    today = dt.date(2026, 2, 13)
    for text in ['Tính từ ngày 1/1 đến hết hôm nay, doanh thu năm nay là bao nhiêu?', 'từ mùng 1/1 năm nay cho tới bữa giờ bán được nhiêu đơn']:
        assert [(p.start, p.end) for p in resolve_periods(text, today)] == [(dt.date(2026, 1, 1), today)]
    assert resolve_periods('so vs cung thang nam truoc, dthu thang 4 nam 2026 thay doi may %', dt.date(2026, 6, 16))[0].start == dt.date(2026, 4, 1)


# Found by scripts/time_stress.py: typed variants and written forms of time expressions.
@pytest.mark.parametrize("question,start,end", [
    ("Doanh thu quí 4/2025 là bao nhiêu?", "2025-10-01", "2025-12-31"),          # was read as April
    ("quys 2/2025 bán bao nhiêu đơn", "2025-04-01", "2025-06-30"),
    ("doanh thu hôm wa", "2026-03-17", "2026-03-17"),
    ("thangs truoc bán được bao nhiêu", "2026-02-01", "2026-02-28"),
    ("tháng 1 năm ngoái thu về nhiêu", "2025-01-01", "2025-01-31"),              # was this year's January
    ("quý 1 năm trước doanh thu", "2025-01-01", "2025-03-31"),
    ("Q1 năm ngoái bán bao nhiêu", "2025-01-01", "2025-03-31"),
    ("tháng 2 năm nay bán bao nhiêu", "2026-02-01", "2026-02-28"),
    ("từ mùng 1 đến mùng 10 tháng 1/2026", "2026-01-01", "2026-01-10"),          # was only 10/1
    ("từ 1 đến 10/1/2026 bán bao nhiêu", "2026-01-01", "2026-01-10"),
    ("doanh thu 5-3-2026", "2026-03-05", "2026-03-05"),
    ("doanh thu 5.3.2026", "2026-03-05", "2026-03-05"),
    ("doanh thu 2026-03-05", "2026-03-05", "2026-03-05"),
    ("ngày 5/3/26 bán bao nhiêu", "2026-03-05", "2026-03-05"),
    ("doanh thu 07-2025", "2025-07-01", "2025-07-31"),                           # month without 'tháng'
    ("doanh thu 7.2025", "2025-07-01", "2025-07-31"),
    ("thứ 2 tuần trước bán bao nhiêu", "2026-03-09", "2026-03-15"),              # week; weekday is the frame filter
])
def test_typed_and_written_time_forms(question, start, end):
    from datamate.intent import normalize_question
    periods = resolve_periods(normalize_question(question), dt.date(2026, 3, 18))
    assert (periods[0].start.isoformat(), periods[0].end.isoformat()) == (start, end)


def test_written_forms_do_not_touch_product_codes_or_prices():
    assert resolve_periods("gạch 30x60-2025 bán bao nhiêu", dt.date(2026, 3, 18)) == [] or \
        resolve_periods("gạch 30x60-2025 bán bao nhiêu", dt.date(2026, 3, 18))[0].start.month != 60
    assert not resolve_periods("giá 1.250.000 là bao nhiêu", dt.date(2026, 3, 18))


def test_weekday_abbreviation_only_before_tuan():
    from datamate.intent import normalize_question, weekday_requested
    assert weekday_requested(normalize_question("doanh thu t2 tuần trước")) == 1
    assert normalize_question("doanh thu T7/2026") == "doanh thu T7/2026"                # month with a year stays July
    assert normalize_question("t7 vừa rồi bán bao nhiêu") == "t7 vừa rồi bán bao nhiêu"  # could be July: not rewritten


@pytest.mark.parametrize("text,expected", [
    ("doanh thu từ đầu tháng 3/2026 đến nay", (dt.date(2026, 3, 1), dt.date(2026, 6, 15))),
    ("doanh thu 10 ngày cuối tháng 2/2026", (dt.date(2026, 2, 19), dt.date(2026, 2, 28))),
    ("doanh thu ngày cuối cùng của tháng 2/2026", (dt.date(2026, 2, 28), dt.date(2026, 2, 28))),   # not the whole month
    ("doanh thu ngày đầu tiên của tháng 3", (dt.date(2026, 3, 1), dt.date(2026, 3, 1))),
    ("doanh thu ngày 30/4 năm 2025", (dt.date(2025, 4, 30), dt.date(2025, 4, 30))),               # not the whole year
])
def test_exact_parts_of_a_month(text, expected):
    period = resolve_periods(text, dt.date(2026, 6, 15))[0]
    assert (period.start, period.end) == expected


def test_vague_parts_of_a_month_are_not_guessed():
    assert resolve_periods("doanh thu nửa đầu tháng 3/2026", dt.date(2026, 6, 15)) == []


@pytest.mark.parametrize("text,expected", [
    ("doanh thu ngày 5-10/3/2026", (dt.date(2026, 3, 5), dt.date(2026, 3, 10))),                  # §2 'nhóm X–Y' includes both ends
    ("doanh thu ngày 10 đến ngày 20/4/2026", (dt.date(2026, 4, 10), dt.date(2026, 4, 20))),
    ("doanh thu từ đầu tháng 5 đến 15/5/2026", (dt.date(2026, 5, 1), dt.date(2026, 5, 15))),
    ("doanh thu từ ngày 1 đến ngày 15 tháng này", (dt.date(2026, 6, 1), dt.date(2026, 6, 15))),
    ("doanh thu ngày 20 tháng trước", (dt.date(2026, 5, 20), dt.date(2026, 5, 20))),
    ("doanh thu tháng này năm ngoái", (dt.date(2025, 6, 1), dt.date(2025, 6, 30))),                # not this year's month
    ("doanh thu quý này năm ngoái", (dt.date(2025, 4, 1), dt.date(2025, 6, 30))),
    ("doanh thu từ đầu quý đến nay", (dt.date(2026, 4, 1), dt.date(2026, 6, 18))),
    ("doanh thu từ đầu quý 1 đến nay", (dt.date(2026, 1, 1), dt.date(2026, 6, 18))),
    ("doanh thu chủ nhật vừa rồi", (dt.date(2026, 6, 14), dt.date(2026, 6, 14))),                 # 18/06/2026 is a Thursday
    ("doanh thu thứ 7 vừa rồi", (dt.date(2026, 6, 13), dt.date(2026, 6, 13))),
    ("doanh thu thứ hai vừa qua", (dt.date(2026, 6, 15), dt.date(2026, 6, 15))),
])
def test_day_ranges_and_relative_days(text, expected):
    period = resolve_periods(text, dt.date(2026, 6, 18))[0]
    assert (period.start, period.end) == expected


def test_seven_days_are_not_a_weekday():
    assert resolve_periods("doanh thu 7 ngày vừa rồi", dt.date(2026, 6, 18))[0].start == dt.date(2026, 6, 12)
