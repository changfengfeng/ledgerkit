"""Fiscal calendars and period closing."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .accounts import AccountType, account_type_of
from .errors import PeriodClosedError
from .journal import Journal
from .money import Money
from .transactions import Posting, Transaction
from .utils.dates import add_months, month_end


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


class FiscalCalendar:
    """Fiscal years that start on the first day of ``start_month``.

    A fiscal year is named after the calendar year in which it *ends*, so with
    ``start_month=4`` the fiscal year 2025 runs 2024-04-01..2025-03-31.
    """

    def __init__(self, start_month: int = 1) -> None:
        if not 1 <= start_month <= 12:
            raise ValueError("start_month must be between 1 and 12")
        self.start_month = start_month

    def fiscal_year_of(self, day: date) -> int:
        if self.start_month == 1:
            return day.year
        return day.year + 1 if day.month >= self.start_month else day.year

    def year_range(self, fiscal_year: int) -> Period:
        if self.start_month == 1:
            start = date(fiscal_year, 1, 1)
        else:
            start = date(fiscal_year - 1, self.start_month, 1)
        end = add_months(start, 12) - timedelta(days=1)
        return Period(start, end, f"FY{fiscal_year}")

    def months(self, fiscal_year: int) -> list[Period]:
        year = self.year_range(fiscal_year)
        periods = []
        current = year.start
        index = 1
        while current <= year.end:
            periods.append(Period(current, month_end(current), f"FY{fiscal_year}-P{index:02d}"))
            current = add_months(current, 1)
            index += 1
        return periods

    def quarters(self, fiscal_year: int) -> list[Period]:
        year = self.year_range(fiscal_year)
        periods = []
        for quarter in range(4):
            start = add_months(year.start, quarter * 3)
            end = add_months(start, 3) - timedelta(days=1)
            periods.append(Period(start, end, f"FY{fiscal_year}-Q{quarter + 1}"))
        return periods

    def period_of(self, day: date) -> Period:
        for period in self.months(self.fiscal_year_of(day)):
            if period.contains(day):
                return period
        raise ValueError(f"no fiscal period for {day}")  # pragma: no cover


@dataclass
class ClosingResult:
    period: Period
    transaction: Transaction | None
    net_income: dict[str, Money]


def income_statement_balances(journal: Journal, start: date, end: date) -> dict[str, dict[str, Decimal]]:
    """Per account, per currency sums of income and expense postings."""
    result: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for tx, posting in journal.postings(start=start, end=end):
        if tx.meta.get("closing") == "true":
            continue
        if account_type_of(posting.account) in (AccountType.INCOME, AccountType.EXPENSES):
            result[posting.account][posting.currency] += posting.amount.amount
    return {k: dict(v) for k, v in result.items()}


def close_period(
    journal: Journal,
    period: Period,
    retained_earnings: str = "Equity:RetainedEarnings",
    lock: bool = True,
) -> ClosingResult:
    """Zero all income and expense accounts into retained earnings.

    The closing entry is dated on the last day of the period.  Afterwards the
    journal is locked through the period end unless ``lock`` is false.
    """
    if journal.locked_through is not None and period.end <= journal.locked_through:
        raise PeriodClosedError(f"{period.label} is already closed")
    balances = income_statement_balances(journal, period.start, period.end)
    tx = Transaction(
        date=period.end,
        payee="",
        narration=f"Closing entry for {period.label}",
        tags={"closing"},
        meta={"closing": "true", "period": period.label},
    )
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for account in sorted(balances):
        for currency, amount in sorted(balances[account].items()):
            if amount == 0:
                continue
            tx.postings.append(Posting(account, Money(-amount, currency)))
            totals[currency] += amount
    net_income = {c: Money(-v, c) for c, v in totals.items()}
    for currency, amount in sorted(totals.items()):
        if amount != 0:
            tx.postings.append(Posting(retained_earnings, Money(amount, currency)))
    recorded: Transaction | None = None
    if tx.postings:
        if retained_earnings not in journal.chart and not journal.auto_open:
            journal.chart.open(retained_earnings, min(period.start, period.end))
        recorded = journal.add(tx)
    if lock:
        journal.lock_through(period.end)
    return ClosingResult(period=period, transaction=recorded, net_income=net_income)
