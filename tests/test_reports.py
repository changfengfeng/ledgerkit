from __future__ import annotations

import csv
import io
import json
from datetime import date

import pytest

from ledgerkit.reports import (
    REPORTS,
    aging,
    balance_sheet,
    cash_flow,
    income_statement,
    register,
    trial_balance,
)
from ledgerkit.reports.base import Report
from ledgerkit.transactions import TransactionBuilder


def cell(report, label, column=1):
    row = report.find(label)
    assert row is not None, f"row {label!r} missing from {report.title}"
    return row.cells[column]


class TestReportBase:
    def make(self):
        report = Report("Demo", ["Name", "Value"], aligns=["left", "right"])
        report.add("a", "1")
        report.add_separator()
        report.add("Total", "1", kind="total")
        return report

    def test_text(self):
        text = self.make().to_text()
        assert text.splitlines()[0] == "Demo"
        assert "Total" in text

    def test_csv(self):
        rows = list(csv.reader(io.StringIO(self.make().to_csv())))
        assert rows[0] == ["Name", "Value"]
        assert ["a", "1"] in rows

    def test_json(self):
        data = json.loads(self.make().to_json())
        assert data["title"] == "Demo"
        assert data["columns"] == ["Name", "Value"]

    def test_render_unknown_format(self):
        with pytest.raises(ValueError):
            self.make().render("xml")

    def test_registry(self):
        assert {"trial-balance", "income-statement", "balance-sheet", "cash-flow", "register", "aging"} <= set(REPORTS)


class TestTrialBalance:
    def test_totals_match(self, populated):
        report = trial_balance(populated)
        total = report.find("Total")
        assert total is not None
        assert total.cells[1] == total.cells[2]

    def test_rows(self, populated):
        report = trial_balance(populated)
        assert "Expenses:Rent" in [r.cells[0] for r in report.data_rows()]

    def test_as_of(self, populated):
        report = trial_balance(populated, as_of=date(2024, 1, 10))
        names = [r.cells[0] for r in report.data_rows()]
        assert "Expenses:Food" not in names


class TestIncomeStatement:
    def test_net_income(self, populated):
        report = income_statement(populated, date(2024, 1, 1), date(2024, 2, 29))
        assert "6,000.00 USD" in str(cell(report, "Total Income"))
        assert "2,650.00 USD" in str(cell(report, "Total Expenses"))
        assert "3,350.00 USD" in str(cell(report, "Net Income"))

    def test_period_filter(self, populated):
        report = income_statement(populated, date(2024, 2, 1), date(2024, 2, 29))
        assert "1,700.25 USD" in str(cell(report, "Net Income"))

    def test_depth(self, populated):
        report = income_statement(populated, date(2024, 1, 1), date(2024, 2, 29), depth=1)
        labels = [r.cells[0].strip() for r in report.data_rows()]
        assert "Expenses:Rent" not in labels

    def test_single_rate_conversion(self, journal):
        journal.rates.add("EUR", "USD", date(2024, 1, 1), "2")
        journal.add(TransactionBuilder(date(2024, 1, 5)).debit("Expenses:Food", "10", "EUR").credit("Assets:Cash", "10", "EUR").build())
        report = income_statement(journal, date(2024, 1, 1), date(2024, 1, 31), currency="USD")
        assert "20.00 USD" in str(cell(report, "Total Expenses"))


class TestBalanceSheet:
    def test_balances(self, populated):
        report = balance_sheet(populated, date(2024, 2, 29))
        assert "8,449.75 USD" in str(cell(report, "Total Assets"))
        assert "99.75 USD" in str(cell(report, "Total Liabilities"))
        assert "8,449.75 USD" in str(cell(report, "Total Liabilities + Equity"))

    def test_current_earnings_line(self, populated):
        report = balance_sheet(populated, date(2024, 2, 29))
        labels = [r.cells[0].strip() for r in report.data_rows()]
        assert "Equity:CurrentEarnings" in labels


class TestCashFlow:
    def test_categories(self, populated):
        report = cash_flow(populated, date(2024, 1, 1), date(2024, 2, 29))
        text = report.to_text()
        assert "Operating" in text
        assert "Net change" in text or "Net Change" in text

    def test_net_change_matches_balance(self, populated):
        report = cash_flow(populated, date(2024, 1, 2), date(2024, 2, 29))
        net = [r for r in report.rows if r.cells and str(r.cells[0]).lower().startswith("net change")]
        assert net
        assert "3,449.75 USD" in str(net[0].cells[-1])


class TestRegister:
    def test_running_totals(self, populated):
        report = register(populated, "Assets:Bank:Checking", "USD")
        rows = report.data_rows()
        assert len(rows) == 6
        assert "8449.75" in str(rows[-1].cells[-1]).replace(",", "")

    def test_csv_output(self, populated):
        text = register(populated, "Expenses:Food", "USD").to_csv()
        assert "Grocer" in text


class TestAging:
    def test_receivables(self, journal):
        journal.chart.open("Assets:Receivable", date(2024, 1, 1))
        for day, amount in ((date(2024, 1, 5), "100"), (date(2024, 3, 1), "50")):
            journal.add(
                TransactionBuilder(day, "Client A").debit("Assets:Receivable", amount, "USD").credit("Income:Salary", amount, "USD").build()
            )
        journal.add(TransactionBuilder(date(2024, 3, 10), "Client A").debit("Assets:Bank:Checking", "40", "USD").credit("Assets:Receivable", "40", "USD").build())
        report = aging(journal, "Assets:Receivable", date(2024, 3, 15), "USD")
        row = report.find("Client A")
        assert row is not None
        text = " ".join(str(c) for c in row.cells)
        assert "60.00" in text
        assert "50.00" in text
