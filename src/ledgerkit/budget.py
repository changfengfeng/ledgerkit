"""Budgets and budget-vs-actual comparison."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum

from .accounts import is_descendant, normalize_name, validate_name
from .errors import BudgetError
from .journal import Journal
from .money import Money
from .utils.dates import add_months, iter_months, iter_quarters, month_start, quarter_start


class BudgetPeriod(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


def _period_key(day: date, period: BudgetPeriod) -> date:
    if period is BudgetPeriod.MONTHLY:
        return month_start(day)
    if period is BudgetPeriod.QUARTERLY:
        return quarter_start(day)
    return date(day.year, 1, 1)


def _period_end(start: date, period: BudgetPeriod) -> date:
    months = {BudgetPeriod.MONTHLY: 1, BudgetPeriod.QUARTERLY: 3, BudgetPeriod.YEARLY: 12}[period]
    return add_months(start, months) - timedelta(days=1)


@dataclass
class BudgetLine:
    account: str
    period_start: date
    amount: Money


@dataclass
class Budget:
    name: str
    currency: str
    period: BudgetPeriod = BudgetPeriod.MONTHLY
    rollover: bool = False
    _lines: dict[tuple[str, date], Money] = field(default_factory=dict)

    def set(self, account: str, period_start: date, amount: Money | Decimal | str | int) -> BudgetLine:
        account = normalize_name(account)
        validate_name(account)
        money = amount if isinstance(amount, Money) else Money(amount, self.currency)
        if money.currency != self.currency:
            raise BudgetError(f"budget {self.name} is in {self.currency}, got {money.currency}")
        if money.is_negative():
            raise BudgetError("budget amounts must be non-negative")
        key = (account, _period_key(period_start, self.period))
        self._lines[key] = money
        return BudgetLine(account, key[1], money)

    def set_range(self, account: str, start: date, end: date, amount: Money | Decimal | str | int) -> list[BudgetLine]:
        iterator: Iterable[tuple[date, date]]
        if self.period is BudgetPeriod.MONTHLY:
            iterator = iter_months(start, end)
        elif self.period is BudgetPeriod.QUARTERLY:
            iterator = iter_quarters(start, end)
        else:
            iterator = ((date(y, 1, 1), date(y, 12, 31)) for y in range(start.year, end.year + 1))
        return [self.set(account, first, amount) for first, _last in iterator]

    def get(self, account: str, period_start: date) -> Money:
        key = (normalize_name(account), _period_key(period_start, self.period))
        return self._lines.get(key, Money.zero(self.currency))

    def lines(self) -> list[BudgetLine]:
        return [BudgetLine(a, d, m) for (a, d), m in sorted(self._lines.items())]

    def accounts(self) -> list[str]:
        return sorted({a for a, _ in self._lines})

    def periods(self) -> list[date]:
        return sorted({d for _, d in self._lines})

    def total(self, period_start: date | None = None) -> Money:
        total = Money.zero(self.currency)
        for (_, start), money in self._lines.items():
            if period_start is None or start == _period_key(period_start, self.period):
                total = total + money
        return total


@dataclass(frozen=True)
class BudgetComparison:
    account: str
    period_start: date
    budgeted: Money
    actual: Money
    carried_over: Money

    @property
    def available(self) -> Money:
        return self.budgeted + self.carried_over

    @property
    def remaining(self) -> Money:
        return self.available - self.actual

    @property
    def percent_used(self) -> Decimal | None:
        if self.available.is_zero():
            return None
        return (self.actual.amount / self.available.amount * 100).quantize(Decimal("0.1"))

    @property
    def over_budget(self) -> bool:
        return self.remaining.is_negative()


def compare(budget: Budget, journal: Journal, start: date, end: date) -> list[BudgetComparison]:
    """Compare budgeted amounts with actual postings per account and period.

    Actuals are the sum of postings in the budget currency to the account and
    its children.  With ``rollover`` unused budget (or overspend) carries into
    the next period of the same account.
    """
    periods = [p for p in budget.periods() if start <= p <= end]
    results: list[BudgetComparison] = []
    for account in budget.accounts():
        carry = Money.zero(budget.currency)
        for period_start in periods:
            period_end = _period_end(period_start, budget.period)
            actual = Money.zero(budget.currency)
            for tx in journal.transactions(start=period_start, end=period_end):
                for posting in tx.postings:
                    if posting.currency == budget.currency and is_descendant(posting.account, account):
                        actual = actual + posting.amount
            budgeted = budget.get(account, period_start)
            comparison = BudgetComparison(account, period_start, budgeted, actual, carry)
            results.append(comparison)
            if budget.rollover:
                carry = comparison.remaining
    return results
