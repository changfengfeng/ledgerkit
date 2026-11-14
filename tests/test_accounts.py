from __future__ import annotations

from datetime import date

import pytest

from ledgerkit.accounts import (
    Account,
    AccountType,
    ChartOfAccounts,
    account_type_of,
    ancestors,
    default_chart,
    depth_of,
    is_descendant,
    join_name,
    leaf_of,
    normalize_name,
    parent_of,
    split_name,
    validate_name,
)
from ledgerkit.errors import LedgerError

D = date(2024, 1, 1)


class TestNames:
    def test_split_join(self):
        assert split_name("Assets:Bank:Checking") == ["Assets", "Bank", "Checking"]
        assert join_name("Assets", "Bank") == "Assets:Bank"

    def test_parent_and_leaf(self):
        assert parent_of("Assets:Bank:Checking") == "Assets:Bank"
        assert parent_of("Assets") is None
        assert leaf_of("Assets:Bank:Checking") == "Checking"

    def test_ancestors(self):
        assert ancestors("Assets:Bank:Checking") == ["Assets", "Assets:Bank"]
        assert ancestors("Assets") == []

    def test_depth(self):
        assert depth_of("Assets") == 1
        assert depth_of("Expenses:Food:Groceries") == 3

    @pytest.mark.parametrize(
        "name, parent, expected",
        [
            ("Assets:Bank", "Assets", True),
            ("Assets:Bank", "Assets:Bank", True),
            ("Assets:Banking", "Assets:Bank", False),
            ("Expenses:Food", "Assets", False),
        ],
    )
    def test_is_descendant(self, name, parent, expected):
        assert is_descendant(name, parent) is expected

    @pytest.mark.parametrize(
        "name, kind",
        [
            ("Assets:Cash", AccountType.ASSETS),
            ("Liabilities:Loan", AccountType.LIABILITIES),
            ("Equity:Opening", AccountType.EQUITY),
            ("Income:Salary", AccountType.INCOME),
            ("Expenses:Rent", AccountType.EXPENSES),
        ],
    )
    def test_account_type(self, name, kind):
        assert account_type_of(name) is kind

    @pytest.mark.parametrize("name", ["Cash", "Assets:", "Assets::Bank", "Assets:bank account", "Foo:Bar", ""])
    def test_invalid_names(self, name):
        with pytest.raises(LedgerError):
            validate_name(name)

    @pytest.mark.parametrize("name", ["Assets:Bank:Checking", "Expenses:Food-Dining", "Assets:Broker:AAPL", "Income:2024"])
    def test_valid_names(self, name):
        assert validate_name(name) == name

    def test_normalize(self):
        assert normalize_name("revenue:Sales") == "Income:Sales"
        assert normalize_name(" Assets:Bank") == "Assets:Bank"


class TestAccountType:
    def test_normal_sign(self):
        assert AccountType.ASSETS.normal_sign == 1
        assert AccountType.EXPENSES.normal_sign == 1
        assert AccountType.INCOME.normal_sign == -1
        assert AccountType.LIABILITIES.normal_sign == -1

    def test_statement_classification(self):
        assert AccountType.ASSETS.is_balance_sheet
        assert AccountType.EQUITY.is_balance_sheet
        assert AccountType.INCOME.is_income_statement
        assert not AccountType.LIABILITIES.is_income_statement


class TestAccount:
    def test_properties(self):
        acc = Account("Assets:Bank:Checking", D)
        assert acc.type is AccountType.ASSETS
        assert acc.parent == "Assets:Bank"
        assert acc.leaf == "Checking"
        assert acc.depth == 3

    def test_open_window(self):
        acc = Account("Assets:Cash", D, closed=date(2024, 6, 30))
        assert not acc.is_open_on(date(2023, 12, 31))
        assert acc.is_open_on(date(2024, 3, 1))
        assert acc.is_open_on(date(2024, 6, 30))
        assert not acc.is_open_on(date(2024, 7, 1))

    def test_currency_constraints(self):
        acc = Account("Assets:Cash", D, currencies=frozenset({"USD"}))
        assert acc.allows("USD")
        assert not acc.allows("EUR")
        assert Account("Assets:Cash", D).allows("EUR")

    def test_dict_roundtrip(self):
        acc = Account("Assets:Cash", D, currencies=frozenset({"USD", "EUR"}), description="wallet", meta={"k": "v"})
        assert Account.from_dict(acc.to_dict()) == acc


class TestChart:
    def test_open_and_get(self):
        chart = ChartOfAccounts()
        chart.open("Assets:Cash", D)
        assert chart.get("Assets:Cash").opened == D
        assert "Assets:Cash" in chart
        assert len(chart) == 1

    def test_duplicate_open(self):
        chart = ChartOfAccounts()
        chart.open("Assets:Cash", D)
        with pytest.raises(LedgerError):
            chart.open("Assets:Cash", D)

    def test_get_unknown(self):
        with pytest.raises(LedgerError):
            ChartOfAccounts().get("Assets:Nope")

    def test_find_returns_none(self):
        assert ChartOfAccounts().find("Assets:Nope") is None

    def test_close_and_reopen(self):
        chart = ChartOfAccounts()
        chart.open("Assets:Cash", D)
        chart.close("Assets:Cash", date(2024, 2, 1))
        assert chart.get("Assets:Cash").closed == date(2024, 2, 1)
        chart.reopen("Assets:Cash")
        assert chart.get("Assets:Cash").closed is None

    def test_close_before_open(self):
        chart = ChartOfAccounts()
        chart.open("Assets:Cash", D)
        with pytest.raises(LedgerError):
            chart.close("Assets:Cash", date(2023, 1, 1))

    def test_children_and_descendants(self, chart):
        assert "Assets:Bank:Checking" in chart.children("Assets:Bank")
        names = [a.name for a in chart.descendants("Assets")]
        assert "Assets:Bank:Savings" in names
        assert all(n.startswith("Assets") for n in names)

    def test_by_type(self, chart):
        assert all(a.type is AccountType.EXPENSES for a in chart.by_type(AccountType.EXPENSES))
        assert chart.by_type(AccountType.INCOME)

    def test_match_glob(self, chart):
        names = [a.name for a in chart.match("Assets:Bank:*")]
        assert "Assets:Bank:Checking" in names
        assert "Assets:Cash" not in names

    def test_open_on(self, chart):
        assert chart.open_on(date(2023, 1, 1)) == []
        assert len(chart.open_on(date(2024, 1, 2))) == len(chart)

    def test_parents_included(self, chart):
        names = chart.all_names_with_parents()
        assert "Assets" in names and "Assets:Bank" in names

    def test_tree(self, chart):
        tree = chart.tree()
        assert "Checking" in tree["Assets"]["Bank"]

    def test_check_posting_ok(self, chart):
        assert chart.check_posting("Assets:Cash", "USD", date(2024, 1, 2)).name == "Assets:Cash"

    def test_check_posting_unknown(self, chart):
        with pytest.raises(LedgerError):
            chart.check_posting("Assets:Unknown", "USD", date(2024, 1, 2))

    def test_check_posting_closed(self):
        chart = ChartOfAccounts()
        chart.open("Assets:Cash", D)
        chart.close("Assets:Cash", date(2024, 1, 31))
        with pytest.raises(LedgerError):
            chart.check_posting("Assets:Cash", "USD", date(2024, 2, 1))

    def test_check_posting_currency(self):
        chart = ChartOfAccounts()
        chart.open("Assets:Cash", D, currencies=["USD"])
        with pytest.raises(LedgerError):
            chart.check_posting("Assets:Cash", "EUR", date(2024, 2, 1))

    def test_list_roundtrip(self, chart):
        again = ChartOfAccounts.from_list(chart.to_list())
        assert again.names() == chart.names()

    def test_default_chart_contents(self):
        chart = default_chart(D)
        for name in ("Assets:Cash", "Liabilities:CreditCard", "Equity:Opening-Balances", "Income:Salary", "Expenses:Food"):
            assert chart.is_known(name)
