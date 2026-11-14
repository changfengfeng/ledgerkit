from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ledgerkit.balances import balance
from ledgerkit.errors import LedgerError
from ledgerkit.money import Money
from ledgerkit.parser import dump_journal, load_file, load_string, parse_string
from ledgerkit.parser.lexer import TokenType, tokenize_line
from ledgerkit.parser.parser import BalanceAssertion, OpenDirective, RateDirective

HEADER = """
2024-01-01 open Assets:Cash
2024-01-01 open Assets:Bank:Checking USD
2024-01-01 open Expenses:Food
2024-01-01 open Income:Salary
2024-01-01 open Equity:Opening-Balances
"""


class TestLexer:
    def test_transaction_header(self):
        tokens = tokenize_line('2024-01-02 * "Shop" "Food" #tag ^link', 1).tokens
        types = [t.type for t in tokens]
        assert types == [TokenType.DATE, TokenType.FLAG, TokenType.STRING, TokenType.STRING, TokenType.TAG, TokenType.LINK]

    def test_posting(self):
        tokens = tokenize_line("  Assets:Cash  -12.50 USD @ 1.1 EUR", 2).tokens
        types = [t.type for t in tokens]
        assert types == [TokenType.ACCOUNT, TokenType.NUMBER, TokenType.CURRENCY, TokenType.AT, TokenType.NUMBER, TokenType.CURRENCY]

    def test_total_price(self):
        tokens = tokenize_line("  Assets:Cash  2 AAPL @@ 300 USD", 3).tokens
        assert TokenType.ATAT in [t.type for t in tokens]

    def test_trailing_comment_ignored(self):
        tokens = tokenize_line("  Assets:Cash  1 USD ; memo here", 4).tokens
        assert [t.type for t in tokens][-1] is TokenType.CURRENCY

    def test_strings_with_escapes(self):
        tokens = tokenize_line('2024-01-02 * "Say \\"hi\\""', 1).tokens
        assert tokens[2].type is TokenType.STRING


class TestDirectives:
    def test_open_with_currencies(self):
        parsed = parse_string("2024-01-01 open Assets:Bank USD,EUR\n")
        directive = parsed.directives[0]
        assert isinstance(directive, OpenDirective)
        assert set(directive.currencies) == {"USD", "EUR"}

    def test_rate(self):
        parsed = parse_string("2024-01-01 rate EUR USD 1.10\n")
        directive = parsed.directives[0]
        assert isinstance(directive, RateDirective)
        assert directive.value == Decimal("1.10")

    def test_balance_assertion(self):
        parsed = parse_string("2024-01-01 balance Assets:Cash 10.00 USD\n")
        assert isinstance(parsed.directives[0], BalanceAssertion)

    def test_options(self):
        parsed = parse_string('option "title" "Books"\n')
        assert parsed.options == {"title": "Books"}

    def test_unknown_keyword(self):
        with pytest.raises(LedgerError):
            parse_string("2024-01-01 frobnicate Assets:Cash\n")

    def test_lenient_collects_errors(self):
        parsed = parse_string("2024-01-01 frobnicate Assets:Cash\n2024-01-01 open Assets:Cash\n", strict=False)
        assert len(parsed.errors) == 1
        assert len(parsed.directives) == 1


class TestTransactions:
    def test_basic(self):
        parsed = parse_string(
            '2024-01-02 * "Shop" "Groceries" #food ^r1\n'
            "  Expenses:Food   12.00 USD\n"
            "  Assets:Cash    -12.00 USD\n"
        )
        tx = parsed.transactions[0]
        assert tx.payee == "Shop"
        assert tx.narration == "Groceries"
        assert tx.tags == {"food"}
        assert tx.links == {"r1"}
        assert tx.is_balanced()

    def test_narration_only(self):
        tx = parse_string('2024-01-02 * "Groceries"\n  Expenses:Food 1 USD\n  Assets:Cash\n').transactions[0]
        assert tx.payee == ""
        assert tx.narration == "Groceries"

    def test_amount_inference(self):
        tx = parse_string('2024-01-02 * "x"\n  Expenses:Food 7.25 USD\n  Assets:Cash\n').transactions[0]
        assert tx.postings[1].amount == Money("-7.25", "USD")

    def test_inference_multiple_currencies(self):
        text = '2024-01-02 * "x"\n  Expenses:Food 7 USD\n  Expenses:Food 3 EUR\n  Assets:Cash\n'
        tx = parse_string(text).transactions[0]
        inferred = sorted((p.amount for p in tx.postings if p.account == "Assets:Cash"), key=lambda m: m.currency)
        assert inferred == [Money("-3", "EUR"), Money("-7", "USD")]

    def test_two_missing_amounts(self):
        with pytest.raises(LedgerError):
            parse_string('2024-01-02 * "x"\n  Expenses:Food\n  Assets:Cash\n')

    def test_unbalanced(self):
        with pytest.raises(LedgerError):
            load_string('2024-01-02 * "x"\n  Expenses:Food 1 USD\n  Assets:Cash -2 USD\n', auto_open=True)

    def test_per_unit_price(self):
        tx = parse_string('2024-01-02 * "x"\n  Assets:Cash 100 EUR @ 1.10 USD\n  Assets:Bank:Checking\n').transactions[0]
        assert tx.postings[1].amount == Money("-110.00", "USD")

    def test_total_price(self):
        tx = parse_string('2024-01-02 * "x"\n  Assets:Cash 100 EUR @@ 110 USD\n  Assets:Bank:Checking -110 USD\n').transactions[0]
        assert tx.postings[0].price == Money("1.1", "USD")
        assert tx.is_balanced()

    def test_metadata(self):
        text = '2024-01-02 * "x"\n  invoice: "A-17"\n  Expenses:Food 1 USD\n    note: "lunch"\n  Assets:Cash\n'
        tx = parse_string(text).transactions[0]
        assert tx.meta["invoice"] == "A-17"
        assert tx.postings[0].meta["note"] == "lunch"

    def test_pending_flag(self):
        tx = parse_string('2024-01-02 ! "x"\n  Expenses:Food 1 USD\n  Assets:Cash\n').transactions[0]
        assert tx.flag == "!"

    def test_posting_memo(self):
        tx = parse_string('2024-01-02 * "x"\n  Expenses:Food 1 USD ; snack\n  Assets:Cash\n').transactions[0]
        assert tx.postings[0].memo == "snack"


class TestLoader:
    def test_load_sample(self, sample_path):
        result = load_file(sample_path)
        assert result.ok
        assert len(result.journal) == 8
        assert result.journal.options["title"] == "Household 2024"

    def test_sample_balances(self, sample):
        assert balance(sample, "Assets:Bank:Checking").get("USD") == Money("5250.00", "USD")
        assert balance(sample, "Assets:Brokerage").get("AAPL") == Money("5", "AAPL")
        assert balance(sample, "Liabilities:CreditCard").get("EUR") == Money("-332.50", "EUR")

    def test_sample_rates(self, sample):
        assert sample.rates.get_rate("EUR", "USD", date(2024, 1, 15)) == Decimal("1.1050")

    def test_failed_balance_assertion(self):
        text = HEADER + '2024-01-02 * "x"\n  Assets:Cash 5 USD\n  Income:Salary\n2024-01-03 balance Assets:Cash 6 USD\n'
        result = load_string(text, strict=False)
        assert not result.ok
        assert "balance" in str(result.errors[0]).lower()

    def test_balance_assertion_passes(self):
        text = HEADER + '2024-01-02 * "x"\n  Assets:Cash 5 USD\n  Income:Salary\n2024-01-03 balance Assets:Cash 5 USD\n'
        assert load_string(text).ok

    def test_unknown_account_strict(self):
        with pytest.raises(LedgerError):
            load_string('2024-01-02 * "x"\n  Expenses:Food 1 USD\n  Assets:Cash\n')

    def test_auto_open(self):
        result = load_string('2024-01-02 * "x"\n  Expenses:Food 1 USD\n  Assets:Cash\n', auto_open=True)
        assert result.ok
        assert result.journal.chart.is_known("Expenses:Food")

    def test_close_directive(self):
        text = HEADER + "2024-01-31 close Assets:Cash\n" + '2024-02-02 * "x"\n  Expenses:Food 1 USD\n  Assets:Cash\n'
        result = load_string(text, strict=False)
        assert not result.ok

    def test_currency_constraint(self):
        text = HEADER + '2024-01-02 * "x"\n  Assets:Bank:Checking 1 EUR\n  Income:Salary\n'
        assert not load_string(text, strict=False).ok

    def test_include(self, tmp_path):
        (tmp_path / "accounts.ledger").write_text(HEADER, encoding="utf-8")
        main = tmp_path / "main.ledger"
        main.write_text('include "accounts.ledger"\n2024-01-02 * "x"\n  Expenses:Food 3 USD\n  Assets:Cash\n', encoding="utf-8")
        result = load_file(main)
        assert result.ok
        assert len(result.journal) == 1

    def test_include_cycle(self, tmp_path):
        a = tmp_path / "a.ledger"
        b = tmp_path / "b.ledger"
        a.write_text('include "b.ledger"\n', encoding="utf-8")
        b.write_text('include "a.ledger"\n', encoding="utf-8")
        with pytest.raises(LedgerError):
            load_file(a)

    def test_notes(self):
        result = load_string(HEADER + '2024-01-05 note Assets:Cash "counted drawer"\n')
        assert result.notes[0].text == "counted drawer"


class TestPrinter:
    def test_roundtrip_sample(self, sample):
        text = dump_journal(sample)
        again = load_string(text).journal
        assert len(again) == len(sample)
        for account in ("Assets", "Liabilities", "Income", "Expenses", "Equity"):
            assert balance(again, account) == balance(sample, account)

    def test_dump_contains_entries(self, sample):
        text = dump_journal(sample)
        assert "open Assets:Bank:Checking" in text
        assert "rate EUR USD" in text
        assert '"Landlord" "January rent"' in text
        assert "@ 182.50 USD" in text
