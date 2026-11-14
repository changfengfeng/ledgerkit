from __future__ import annotations

from datetime import date
from decimal import Decimal

from ledgerkit.budget import Budget, BudgetPeriod, compare
from ledgerkit.money import Money


def usd(value):
    return Money(value, "USD")


def test_set_normalizes_to_period_start():
    budget = Budget("home", "USD")
    line = budget.set("Expenses:Food", date(2024, 1, 17), "200")
    assert line.period_start == date(2024, 1, 1)
    assert budget.get("Expenses:Food", date(2024, 1, 1)) == usd("200")


def test_set_range_and_totals():
    budget = Budget("home", "USD")
    lines = budget.set_range("Expenses:Food", date(2024, 1, 1), date(2024, 3, 31), "100")
    assert len(lines) == 3
    budget.set("Expenses:Rent", date(2024, 1, 1), "1200")
    assert budget.total(date(2024, 1, 1)) == usd("1300")
    assert budget.total() == usd("1500")
    assert budget.accounts() == ["Expenses:Food", "Expenses:Rent"]
    assert budget.periods() == [date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)]


def test_quarterly_periods():
    budget = Budget("q", "USD", period=BudgetPeriod.QUARTERLY)
    line = budget.set("Expenses:Food", date(2024, 5, 9), "300")
    assert line.period_start == date(2024, 4, 1)


def test_compare(populated):
    budget = Budget("home", "USD")
    budget.set_range("Expenses:Food", date(2024, 1, 1), date(2024, 2, 29), "120")
    budget.set_range("Expenses:Rent", date(2024, 1, 1), date(2024, 2, 29), "1200")
    results = compare(budget, populated, date(2024, 1, 1), date(2024, 2, 29))
    food = [r for r in results if r.account == "Expenses:Food"]
    assert [r.actual for r in food] == [usd("150.25"), usd("99.75")]
    assert food[0].over_budget
    assert not food[1].over_budget
    assert food[1].remaining == usd("20.25")
    rent = [r for r in results if r.account == "Expenses:Rent"]
    assert all(r.percent_used == Decimal("100.0") or r.percent_used == Decimal("100") for r in rent)


def test_rollover(populated):
    budget = Budget("home", "USD", rollover=True)
    budget.set_range("Expenses:Food", date(2024, 1, 1), date(2024, 2, 29), "200")
    jan, feb = compare(budget, populated, date(2024, 1, 1), date(2024, 2, 29))
    assert jan.carried_over.is_zero()
    assert feb.carried_over == usd("49.75")
    assert feb.available == usd("249.75")


def test_percent_used_without_budget(populated):
    budget = Budget("home", "USD")
    budget.set("Expenses:Food", date(2024, 1, 1), "0")
    result = compare(budget, populated, date(2024, 1, 1), date(2024, 1, 31))[0]
    assert result.percent_used is None
