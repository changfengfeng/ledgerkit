from __future__ import annotations

from datetime import date
from decimal import Decimal

from ledgerkit.balances import (
    average_monthly,
    balance,
    balance_in,
    balances_by_account,
    monthly_activity,
    net_worth,
    rollup,
    running_balance,
    signed_for_display,
    turnover,
)
from ledgerkit.currency import RateTable
from ledgerkit.money import Money, MoneyBag
from ledgerkit.transactions import TransactionBuilder

USD = "USD"


def usd(value):
    return Money(value, USD)


def test_balance_of_leaf(populated):
    assert balance(populated, "Assets:Bank:Checking").get(USD) == usd("8449.75")


def test_balance_includes_children(populated):
    assert balance(populated, "Expenses").get(USD) == usd("2650.00")


def test_balance_excluding_children(populated):
    assert balance(populated, "Expenses", include_children=False).is_zero()


def test_balance_as_of(populated):
    assert balance(populated, "Assets:Bank:Checking", as_of=date(2024, 1, 31)).get(USD) == usd("6800")


def test_balance_with_start(populated):
    assert balance(populated, "Income", start=date(2024, 2, 1)).get(USD) == usd("-3000")


def test_whole_journal_sums_to_zero(populated):
    total = MoneyBag()
    for bag in balances_by_account(populated).values():
        total = total.merge(bag)
    assert total.is_zero()


def test_balances_by_account_range(populated):
    result = balances_by_account(populated, start=date(2024, 2, 1), end=date(2024, 2, 28))
    assert result["Expenses:Food"].get(USD) == usd("99.75")
    assert "Equity:Opening-Balances" not in result


def test_balances_by_account_filter(populated):
    result = balances_by_account(populated, accounts=["Expenses"])
    assert set(result) == {"Expenses:Food", "Expenses:Rent"}


def test_rollup(populated):
    rolled = rollup(balances_by_account(populated))
    assert rolled["Assets"].get(USD) == rolled["Assets:Bank"].get(USD)
    assert rolled["Liabilities"].get(USD) == usd("-99.75")
    assert rolled["Expenses"].get(USD) == usd("2650.00")


def test_signed_for_display():
    bag = MoneyBag.of(usd("-100"))
    assert signed_for_display("Income:Salary", bag).get(USD) == usd("100")
    assert signed_for_display("Assets:Cash", bag).get(USD) == usd("-100")


def test_running_balance(populated):
    lines = running_balance(populated, "Liabilities:CreditCard", USD)
    assert [line.running.amount for line in lines] == [Decimal("-150.25"), Decimal("-250.00"), Decimal("-99.75")]


def test_running_balance_with_start_carries_opening(populated):
    lines = running_balance(populated, "Assets:Bank:Checking", USD, start=date(2024, 2, 1))
    assert lines[0].running == usd("9800")
    assert lines[-1].running == usd("8449.75")


def test_balance_in_single_currency(populated):
    assert balance_in(populated, "Expenses", USD) == usd("2650.00")


def test_balance_in_converts(journal):
    rates = RateTable()
    rates.add("EUR", "USD", date(2024, 1, 1), "1.5")
    journal.add(TransactionBuilder(date(2024, 1, 2)).debit("Expenses:Food", "10", "EUR").credit("Assets:Cash", "10", "EUR").build())
    journal.add(TransactionBuilder(date(2024, 1, 3)).debit("Expenses:Food", "5", USD).credit("Assets:Cash", "5", USD).build())
    assert balance_in(journal, "Expenses", USD, rates, as_of=date(2024, 1, 3)) == usd("20.00")


def test_net_worth(populated):
    assert net_worth(populated, USD) == usd("8350.00")


def test_monthly_activity(populated):
    activity = monthly_activity(populated, "Expenses", date(2024, 1, 1), date(2024, 3, 31))
    assert [first for first, _ in activity] == [date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)]
    assert activity[0][1].get(USD) == usd("1350.25")
    assert activity[2][1].is_zero()


def test_average_monthly(populated):
    assert average_monthly(populated, "Expenses", USD, date(2024, 1, 1), date(2024, 2, 29)) == usd("1325.00")


def test_turnover(populated):
    debits, credits = turnover(populated, "Liabilities:CreditCard")
    assert debits.get(USD) == usd("150.25")
    assert credits.get(USD) == usd("-250.00")
