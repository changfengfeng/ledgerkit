from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ledgerkit.errors import LedgerError
from ledgerkit.money import Money
from ledgerkit.reconcile import Reconciler, StatementLine
from ledgerkit.tax import TaxRate, TaxRegistry, from_gross, from_net, taxed_purchase
from ledgerkit.transactions import TransactionBuilder

VAT = TaxRate("VAT20", "0.20", "Liabilities:Tax", "standard VAT")


class TestTax:
    def test_from_net(self):
        b = from_net(Money("100", "USD"), VAT)
        assert (b.net, b.tax, b.gross) == (Money("100", "USD"), Money("20.00", "USD"), Money("120.00", "USD"))

    def test_from_gross(self):
        b = from_gross(Money("120", "USD"), VAT)
        assert b.net == Money("100.00", "USD")
        assert b.tax == Money("20.00", "USD")

    def test_from_gross_rounding_preserves_total(self):
        b = from_gross(Money("9.99", "USD"), TaxRate("R7", "0.07", "Liabilities:Tax"))
        assert b.net + b.tax == Money("9.99", "USD")

    def test_rate_validation(self):
        with pytest.raises(LedgerError):
            TaxRate("BAD", "-0.1", "Liabilities:Tax")
        assert TaxRate("P", "0.07", "Liabilities:Tax").rate == Decimal("0.07")

    def test_taxed_purchase(self, journal):
        tx = taxed_purchase(date(2024, 1, 5), "Office Depot", "Expenses:Office", "Assets:Cash", Money("60", "USD"), VAT)
        journal.add(tx)
        amounts = {p.account: p.amount for p in tx.postings}
        assert amounts["Expenses:Office"] == Money("50.00", "USD")
        assert amounts["Liabilities:Tax"] == Money("10.00", "USD")
        assert amounts["Assets:Cash"] == Money("-60", "USD")

    def test_registry_summary(self, journal):
        registry = TaxRegistry()
        registry.add(VAT)
        for gross in ("60", "120"):
            journal.add(taxed_purchase(date(2024, 1, 5), "Shop", "Expenses:Office", "Assets:Cash", Money(gross, "USD"), VAT))
        summary = registry.summary(journal, date(2024, 1, 1), date(2024, 1, 31))
        assert summary == {"VAT20": {"USD": Money("30.00", "USD")}}
        assert registry.codes() == ["VAT20"]
        with pytest.raises(LedgerError):
            registry.get("NOPE")


class TestReconcile:
    @pytest.fixture
    def books(self, journal):
        for day, payee, amount in ((date(2024, 1, 3), "Coffee", "4.50"), (date(2024, 1, 5), "Power Co", "80.25"), (date(2024, 1, 9), "Books", "25.00")):
            journal.add(TransactionBuilder(day, payee).debit("Expenses:Office", amount, "USD").credit("Assets:Bank:Checking", amount, "USD").build())
        return journal

    def test_full_match(self, books):
        lines = [
            StatementLine(date(2024, 1, 4), Money("-4.50", "USD"), "COFFEE"),
            StatementLine(date(2024, 1, 5), Money("-80.25", "USD"), "POWER CO"),
            StatementLine(date(2024, 1, 10), Money("-25.00", "USD"), "BOOKS"),
        ]
        result = Reconciler(books, "Assets:Bank:Checking").reconcile(lines, statement_balance=Money("-109.75", "USD"))
        assert len(result.matches) == 3
        assert result.is_reconciled
        assert result.summary()["reconciled"] is True

    def test_unmatched(self, books):
        lines = [StatementLine(date(2024, 1, 4), Money("-4.50", "USD"), "COFFEE"), StatementLine(date(2024, 1, 6), Money("-9.99", "USD"), "MYSTERY")]
        result = Reconciler(books, "Assets:Bank:Checking").reconcile(lines, start=date(2024, 1, 1), end=date(2024, 1, 9))
        assert len(result.matches) == 1
        assert len(result.unmatched_statement) == 1
        assert len(result.unmatched_ledger) == 2
        assert not result.is_reconciled

    def test_date_tolerance(self, books):
        lines = [StatementLine(date(2024, 1, 20), Money("-25.00", "USD"), "BOOKS")]
        result = Reconciler(books, "Assets:Bank:Checking", date_tolerance=3).reconcile(lines, start=date(2024, 1, 9), end=date(2024, 1, 20))
        assert not result.matches

    def test_balance_difference(self, books):
        lines = [
            StatementLine(date(2024, 1, 3), Money("-4.50", "USD")),
            StatementLine(date(2024, 1, 5), Money("-80.25", "USD")),
            StatementLine(date(2024, 1, 9), Money("-25.00", "USD")),
        ]
        result = Reconciler(books, "Assets:Bank:Checking").reconcile(lines, statement_balance=Money("-100", "USD"))
        assert result.difference == Money("9.75", "USD")
        assert not result.is_reconciled
