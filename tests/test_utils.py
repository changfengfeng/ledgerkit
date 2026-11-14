from __future__ import annotations

from datetime import date

import pytest

from ledgerkit.utils.dates import (
    DateParseError,
    add_business_days,
    add_months,
    add_years,
    days_in_month,
    format_period,
    is_business_day,
    iter_days,
    iter_months,
    iter_quarters,
    month_end,
    month_start,
    parse_date,
    parse_date_with_format,
    parse_period,
    quarter_end,
    quarter_of,
    quarter_start,
    week_start,
    year_end,
    year_start,
)
from ledgerkit.utils.text import (
    display_width,
    normalize_whitespace,
    pad,
    quote,
    render_table,
    similarity,
    slugify,
    truncate,
    unquote,
)


class TestParseDate:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("20240305", date(2024, 3, 5)),
            ("03/05/2024", date(2024, 3, 5)),
        ],
    )
    def test_formats(self, text, expected):
        assert parse_date(text) == expected

    def test_dayfirst(self):
        assert parse_date("03/05/2024", dayfirst=True) == date(2024, 5, 3)

    def test_passthrough(self):
        assert parse_date(date(2024, 1, 1)) == date(2024, 1, 1)

    @pytest.mark.parametrize("text", ["2024-02-30", "yesterday-ish", "", "2024-13-01"])
    def test_invalid(self, text):
        with pytest.raises(DateParseError):
            parse_date(text)

    def test_with_format(self):
        assert parse_date_with_format("05.03.2024", "%d.%m.%Y") == date(2024, 3, 5)
        with pytest.raises(DateParseError):
            parse_date_with_format("2024-03-05", "%d.%m.%Y")


class TestArithmetic:
    def test_days_in_month(self):
        assert days_in_month(2024, 2) == 29
        assert days_in_month(2023, 2) == 28

    @pytest.mark.parametrize(
        "start, months, expected",
        [
            (date(2024, 1, 31), 1, date(2024, 2, 29)),
            (date(2024, 1, 15), 13, date(2025, 2, 15)),
            (date(2024, 3, 31), -1, date(2024, 2, 29)),
            (date(2024, 12, 1), 1, date(2025, 1, 1)),
        ],
    )
    def test_add_months(self, start, months, expected):
        assert add_months(start, months) == expected

    def test_add_months_anchor(self):
        assert add_months(date(2024, 2, 29), 1, anchor_day=31) == date(2024, 3, 31)

    def test_add_years_leap(self):
        assert add_years(date(2024, 2, 29), 1) == date(2025, 2, 28)

    def test_boundaries(self):
        day = date(2024, 5, 17)
        assert month_start(day) == date(2024, 5, 1)
        assert month_end(day) == date(2024, 5, 31)
        assert quarter_of(day) == 2
        assert quarter_start(day) == date(2024, 4, 1)
        assert quarter_end(day) == date(2024, 6, 30)
        assert year_start(day) == date(2024, 1, 1)
        assert year_end(day) == date(2024, 12, 31)
        assert week_start(day) == date(2024, 5, 13)

    def test_iterators(self):
        assert len(list(iter_days(date(2024, 2, 1), date(2024, 2, 29)))) == 29
        months = list(iter_months(date(2024, 1, 15), date(2024, 3, 2)))
        assert months[0] == (date(2024, 1, 1), date(2024, 1, 31))
        assert len(months) == 3
        assert len(list(iter_quarters(date(2024, 1, 1), date(2024, 12, 31)))) == 4

    def test_business_days(self):
        assert not is_business_day(date(2024, 6, 1))
        assert add_business_days(date(2024, 5, 31), 1) == date(2024, 6, 3)
        assert add_business_days(date(2024, 6, 3), 1, holidays={date(2024, 6, 4)}) == date(2024, 6, 5)
        assert add_business_days(date(2024, 6, 3), -1) == date(2024, 5, 31)


class TestPeriods:
    TODAY = date(2024, 5, 17)

    @pytest.mark.parametrize(
        "spec, expected",
        [
            ("2024", (date(2024, 1, 1), date(2024, 12, 31))),
            ("2024-02", (date(2024, 2, 1), date(2024, 2, 29))),
            ("2024-Q3", (date(2024, 7, 1), date(2024, 9, 30))),
            ("2024-01-10..2024-01-20", (date(2024, 1, 10), date(2024, 1, 20))),
            ("this-month", (date(2024, 5, 1), date(2024, 5, 31))),
            ("last-month", (date(2024, 4, 1), date(2024, 4, 30))),
            ("this-year", (date(2024, 1, 1), date(2024, 12, 31))),
            ("last-year", (date(2023, 1, 1), date(2023, 12, 31))),
            ("ytd", (date(2024, 1, 1), date(2024, 5, 17))),
            ("this-quarter", (date(2024, 4, 1), date(2024, 6, 30))),
        ],
    )
    def test_parse_period(self, spec, expected):
        assert parse_period(spec, today=self.TODAY) == expected

    def test_reversed_range(self):
        with pytest.raises(DateParseError):
            parse_period("2024-02-01..2024-01-01")

    def test_unknown(self):
        with pytest.raises(DateParseError):
            parse_period("someday")

    def test_format_period(self):
        assert format_period(date(2024, 2, 1), date(2024, 2, 29)) == "2024-02"
        assert format_period(date(2024, 1, 1), date(2024, 12, 31)) == "2024"
        assert ".." in format_period(date(2024, 1, 3), date(2024, 1, 9))


class TestText:
    def test_normalize_whitespace(self):
        assert normalize_whitespace("  a \t b\n c ") == "a b c"

    def test_slugify(self):
        assert slugify("Café & Bar #1") == "cafe-bar-1"

    def test_similarity(self):
        assert similarity("AMAZON MKTPLACE", "amazon mktplace") == 1.0
        assert similarity("rent", "groceries") < 0.5
        assert similarity("", "") == 1.0

    def test_truncate(self):
        assert truncate("hello world", 5) == "hell…"
        assert truncate("hi", 5) == "hi"

    def test_display_width_wide_chars(self):
        assert display_width("ab") == 2
        assert display_width("中文") == 4

    def test_pad(self):
        assert pad("x", 3) == "x  "
        assert pad("x", 3, "right") == "  x"
        assert pad("x", 3, "center") == " x "

    def test_quote_roundtrip(self):
        text = 'say "hi"\\now'
        assert unquote(quote(text)) == text

    def test_render_table(self):
        out = render_table(["A", "Amount"], [["x", "1.00"], ["longer", "10.00"]], ["left", "right"])
        lines = out.splitlines()
        assert lines[0].startswith("A")
        assert lines[-1].endswith("10.00")
        assert len({len(line) for line in lines}) == 1
