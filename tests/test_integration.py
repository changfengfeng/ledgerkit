"""End-to-end flows across parser, importers, reports and storage."""

from __future__ import annotations

from datetime import date

from ledgerkit.balances import balance
from ledgerkit.budget import Budget, compare
from ledgerkit.importers import CsvImporter, CsvMapping, RuleSet, import_into
from ledgerkit.money import Money
from ledgerkit.parser import dump_journal, load_string
from ledgerkit.periods import FiscalCalendar, close_period
from ledgerkit.recurring import Frequency, RecurrenceRule, RecurringScheduler, RecurringTemplate
from ledgerkit.reports import balance_sheet, income_statement, trial_balance
from ledgerkit.storage import JsonlStore, SqliteStore
from ledgerkit.transactions import TransactionBuilder


def test_sample_import_and_report(sample):
    from pathlib import Path

    examples = Path(__file__).resolve().parent.parent / "examples"
    for name in ("Expenses:Subscriptions", "Expenses:Fees"):
        sample.chart.open(name, date(2024, 1, 1))
    lines = CsvImporter(CsvMapping(reference="reference")).parse_file(examples / "bank.csv")
    result = import_into(sample, lines, "Assets:Bank:Checking", RuleSet.load(examples / "rules.json"))
    assert result.summary() == {"created": 10, "duplicates": 0, "errors": 0, "uncategorized": 0}
    assert balance(sample, "Assets:Bank:Checking", as_of=date(2024, 4, 30)).get("USD") == Money("7287.96", "USD")
    report = income_statement(sample, date(2024, 4, 1), date(2024, 4, 30))
    assert "Income:Salary" in report.to_text()


def test_year_end_close_and_roundtrip(populated, tmp_path):
    close_period(populated, FiscalCalendar(1).year_range(2024))
    sheet = balance_sheet(populated, date(2024, 12, 31))
    assert sheet.find("Equity:RetainedEarnings") is not None or "RetainedEarnings" in sheet.to_text()
    text = dump_journal(populated)
    again = load_string(text).journal
    assert balance(again, "Income").is_zero()
    trial = trial_balance(again)
    total = trial.find("Total")
    assert total.cells[1] == total.cells[2]


def test_recurring_budget_flow(journal):
    template = (
        TransactionBuilder(date(2024, 1, 1), "Landlord", "Rent")
        .debit("Expenses:Rent", "1000", "USD")
        .credit("Assets:Bank:Checking", "1000", "USD")
        .build()
    )
    scheduler = RecurringScheduler()
    scheduler.register(RecurringTemplate("rent", RecurrenceRule(Frequency.MONTHLY, date(2024, 1, 1)), template))
    scheduler.materialize(journal, date(2024, 6, 30))
    budget = Budget("rent", "USD")
    budget.set_range("Expenses:Rent", date(2024, 1, 1), date(2024, 6, 30), "1000")
    results = compare(budget, journal, date(2024, 1, 1), date(2024, 6, 30))
    assert len(results) == 6
    assert all(r.remaining.is_zero() for r in results)


def test_storage_backends_agree(populated, tmp_path):
    sqlite = SqliteStore(tmp_path / "books.db")
    jsonl = JsonlStore(tmp_path / "books.jsonl")
    sqlite.save(populated)
    jsonl.save(populated)
    a, b = sqlite.load(), jsonl.load()
    for name in ("Assets", "Liabilities", "Equity", "Income", "Expenses"):
        assert balance(a, name) == balance(b, name) == balance(populated, name)
    sqlite.close()
