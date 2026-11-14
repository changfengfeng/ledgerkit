from __future__ import annotations

from datetime import date

import pytest

from ledgerkit.balances import balance
from ledgerkit.errors import LedgerError
from ledgerkit.money import Money
from ledgerkit.periods import FiscalCalendar, Period, close_period, income_statement_balances


def test_period_contains_and_days():
    period = Period(date(2024, 1, 1), date(2024, 1, 31), "Jan")
    assert period.contains(date(2024, 1, 31))
    assert not period.contains(date(2024, 2, 1))
    assert period.days == 31


@pytest.mark.parametrize(
    "start_month, day, expected",
    [(1, date(2024, 6, 1), 2024), (4, date(2024, 3, 31), 2024), (4, date(2024, 4, 1), 2025), (7, date(2024, 12, 31), 2025)],
)
def test_fiscal_year_of(start_month, day, expected):
    assert FiscalCalendar(start_month).fiscal_year_of(day) == expected


def test_year_range_calendar():
    period = FiscalCalendar(1).year_range(2024)
    assert (period.start, period.end) == (date(2024, 1, 1), date(2024, 12, 31))


def test_year_range_april_start():
    period = FiscalCalendar(4).year_range(2025)
    assert (period.start, period.end) == (date(2024, 4, 1), date(2025, 3, 31))


def test_months_and_quarters():
    calendar = FiscalCalendar(4)
    months = calendar.months(2025)
    assert len(months) == 12
    assert months[0].start == date(2024, 4, 1)
    assert months[-1].end == date(2025, 3, 31)
    quarters = calendar.quarters(2025)
    assert len(quarters) == 4
    assert quarters[1].start == date(2024, 7, 1)
    assert quarters[3].end == date(2025, 3, 31)


def test_period_of():
    period = FiscalCalendar(1).period_of(date(2024, 2, 14))
    assert (period.start, period.end) == (date(2024, 2, 1), date(2024, 2, 29))


def test_invalid_start_month():
    with pytest.raises((LedgerError, ValueError)):
        FiscalCalendar(13)


def test_income_statement_balances(populated):
    sums = income_statement_balances(populated, date(2024, 1, 1), date(2024, 1, 31))
    assert sums["Income:Salary"]["USD"] == -3000
    assert sums["Expenses:Rent"]["USD"] == 1200
    assert "Assets:Bank:Checking" not in sums


def test_close_period(populated):
    period = FiscalCalendar(1).year_range(2024)
    result = close_period(populated, period)
    assert result.transaction is not None
    assert result.transaction.date == date(2024, 12, 31)
    assert result.net_income["USD"] == Money("3350.00", "USD")
    assert balance(populated, "Income").is_zero()
    assert balance(populated, "Expenses").is_zero()
    assert balance(populated, "Equity:RetainedEarnings").get("USD") == Money("-3350.00", "USD")
    assert populated.locked_through == date(2024, 12, 31)


def test_close_period_without_lock(populated):
    period = FiscalCalendar(1).year_range(2024)
    close_period(populated, period, lock=False)
    assert populated.locked_through is None


def test_close_empty_period(journal):
    result = close_period(journal, FiscalCalendar(1).year_range(2024), lock=False)
    assert result.transaction is None
    assert result.net_income == {}
