from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ledgerkit.balances import balance
from ledgerkit.errors import LedgerError
from ledgerkit.importers import CsvImporter, CsvMapping, OfxImporter, RuleSet, import_into
from ledgerkit.importers.base import ImportedLine, fingerprint, line_to_transaction
from ledgerkit.importers.rules import CategorizationRule
from ledgerkit.money import Money

CSV = """date,description,amount
2024-01-03,COFFEE SHOP 123,-4.50
2024-01-04,ACME PAYROLL,2500.00
2024-01-05,CITY POWER CO,-80.25
2024-01-06,Unknown merchant,-12.00
"""

OFX = """OFXHEADER:100
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS>
<CURDEF>EUR
<BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20240110120000<TRNAMT>-23.50<FITID>A1<NAME>BAKERY<MEMO>bread</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20240111<TRNAMT>100.00<FITID>A2<NAME>REFUND</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>1076.50<DTASOF>20240111</LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""


def rules():
    return RuleSet(
        [
            CategorizationRule("coffee", "Expenses:Food", payee="Coffee Shop"),
            CategorizationRule("payroll", "Income:Salary", direction="in"),
            CategorizationRule("power", "Expenses:Utilities", tags=("bills",)),
        ]
    )


class TestRules:
    def line(self, description, amount="-10"):
        return ImportedLine(date(2024, 1, 1), Money(amount, "USD"), description)

    def test_pattern_case_insensitive(self):
        assert CategorizationRule("coffee", "Expenses:Food").matches(self.line("COFFEE"))

    def test_direction(self):
        rule = CategorizationRule("x", "Income:Salary", direction="in")
        assert rule.matches(self.line("x", "5"))
        assert not rule.matches(self.line("x", "-5"))

    def test_amount_range(self):
        rule = CategorizationRule(".", "Expenses:Food", min_amount=Decimal("5"), max_amount=Decimal("20"))
        assert rule.matches(self.line("a", "-10"))
        assert not rule.matches(self.line("a", "-50"))

    def test_currency(self):
        rule = CategorizationRule(".", "Expenses:Food", currency="EUR")
        assert not rule.matches(self.line("a"))

    def test_priority(self):
        ruleset = RuleSet([CategorizationRule("shop", "Expenses:Shopping"), CategorizationRule("coffee shop", "Expenses:Food", priority=10)])
        assert ruleset.match(self.line("coffee shop")).account == "Expenses:Food"

    def test_invalid_pattern(self):
        with pytest.raises(LedgerError):
            CategorizationRule("(", "Expenses:Food")

    def test_invalid_direction(self):
        with pytest.raises(LedgerError):
            CategorizationRule("x", "Expenses:Food", direction="sideways")

    def test_json_roundtrip(self, tmp_path):
        ruleset = rules()
        path = tmp_path / "rules.json"
        path.write_text(ruleset.to_json(), encoding="utf-8")
        loaded = RuleSet.load(path)
        assert len(loaded) == 3
        assert loaded.match(self.line("City Power Co")).account == "Expenses:Utilities"


class TestCsvImporter:
    def test_parse(self):
        lines = CsvImporter().parse(CSV)
        assert len(lines) == 4
        assert lines[0].date == date(2024, 1, 3)
        assert lines[0].amount == Money("-4.50", "USD")

    def test_debit_credit_columns(self):
        text = "Date;Text;Out;In\n03.01.2024;Shop;4,50;\n04.01.2024;Pay;;2.500,00\n"
        mapping = CsvMapping(date="Date", description="Text", amount=None, debit="Out", credit="In", delimiter=";", decimal_comma=True, date_format="%d.%m.%Y", default_currency="EUR")
        lines = CsvImporter(mapping).parse(text)
        assert [line.amount for line in lines] == [Money("-4.50", "EUR"), Money("2500.00", "EUR")]

    def test_invert(self):
        lines = CsvImporter(CsvMapping(invert=True)).parse(CSV)
        assert lines[0].amount == Money("4.50", "USD")

    def test_no_header(self):
        mapping = CsvMapping(date="0", description="1", amount="2", has_header=False)
        lines = CsvImporter(mapping).parse("2024-01-03,Shop,-1.00\n")
        assert lines[0].description == "Shop"

    def test_missing_column(self):
        with pytest.raises(LedgerError):
            CsvImporter(CsvMapping(amount="value")).parse(CSV)

    def test_bad_amount(self):
        with pytest.raises(LedgerError):
            CsvImporter().parse("date,description,amount\n2024-01-01,x,abc\n")

    def test_parse_file(self, tmp_path):
        path = tmp_path / "bank.csv"
        path.write_text(CSV, encoding="utf-8")
        assert len(CsvImporter().parse_file(path)) == 4


class TestOfxImporter:
    def test_parse(self):
        lines = OfxImporter().parse(OFX)
        assert len(lines) == 2
        assert lines[0].amount == Money("-23.50", "EUR")
        assert lines[0].date == date(2024, 1, 10)
        assert lines[0].reference == "A1"
        assert "BAKERY" in lines[0].description

    def test_currency(self):
        assert OfxImporter().statement_currency(OFX) == "EUR"
        assert OfxImporter("GBP").statement_currency("<OFX></OFX>") == "GBP"

    def test_ledger_balance(self):
        amount, _when = OfxImporter().ledger_balance(OFX)
        assert amount == Money("1076.50", "EUR")


class TestImportInto:
    def test_creates_balanced_transactions(self, journal):
        result = import_into(journal, CsvImporter().parse(CSV), "Assets:Bank:Checking", rules())
        assert result.summary() == {"created": 4, "duplicates": 0, "errors": 0, "uncategorized": 1}
        assert all(tx.is_balanced() for tx in result.created)
        assert balance(journal, "Assets:Bank:Checking").get("USD") == Money("2403.25", "USD")
        assert balance(journal, "Expenses:Uncategorized").get("USD") == Money("12.00", "USD")

    def test_rule_payee_and_tags(self, journal):
        result = import_into(journal, CsvImporter().parse(CSV), "Assets:Bank:Checking", rules())
        coffee = result.created[0]
        assert coffee.payee == "Coffee Shop"
        power = result.created[2]
        assert "bills" in power.tags

    def test_duplicates_skipped(self, journal):
        lines = CsvImporter().parse(CSV)
        import_into(journal, lines, "Assets:Bank:Checking", rules())
        again = import_into(journal, lines, "Assets:Bank:Checking", rules())
        assert again.summary()["duplicates"] == 4
        assert len(journal) == 4

    def test_dry_run(self, journal):
        result = import_into(journal, CsvImporter().parse(CSV), "Assets:Bank:Checking", rules(), dry_run=True)
        assert len(result.created) == 4
        assert len(journal) == 0

    def test_fingerprint_stable(self):
        line = ImportedLine(date(2024, 1, 1), Money("-1", "USD"), "x", reference="R1")
        assert fingerprint("Assets:Cash", line) == fingerprint("Assets:Cash", line)
        assert fingerprint("Assets:Cash", line) != fingerprint("Assets:Bank:Checking", line)

    def test_line_to_transaction(self):
        line = ImportedLine(date(2024, 1, 1), Money("-9.99", "USD"), "Thing")
        tx, categorized = line_to_transaction(line, "Assets:Cash", None, "Expenses:Uncategorized")
        assert not categorized
        amounts = {p.account: p.amount for p in tx.postings}
        assert amounts["Expenses:Uncategorized"] == Money("9.99", "USD")

    def test_ofx_import(self, journal):
        result = import_into(journal, OfxImporter().parse(OFX), "Assets:Cash")
        assert len(result.created) == 2
        assert balance(journal, "Assets:Cash").get("EUR") == Money("76.50", "EUR")
