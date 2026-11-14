from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ledgerkit.errors import LedgerError
from ledgerkit.money import Money, register_currency
from ledgerkit.transactions import (
    Posting,
    Transaction,
    TransactionBuilder,
    split_transaction,
    transfer,
)

D = date(2024, 3, 1)
register_currency("AAPL", decimals=4, name="Apple Inc.")


def simple(amount="10", currency="USD"):
    return (
        TransactionBuilder(D, "Shop", "stuff")
        .debit("Expenses:Food", amount, currency)
        .credit("Assets:Cash", amount, currency)
        .build()
    )


class TestPosting:
    def test_weight_without_price(self):
        p = Posting("Assets:Cash", Money("5", "USD"))
        assert p.weight() == Money("5", "USD")

    def test_weight_with_price(self):
        p = Posting("Assets:Broker:AAPL", Money("10", "AAPL"), price=Money("150", "USD"))
        assert p.weight() == Money("1500", "USD")

    def test_debit_credit(self):
        assert Posting("Assets:Cash", Money("1", "USD")).is_debit
        assert Posting("Assets:Cash", Money("-1", "USD")).is_credit

    def test_dict_roundtrip(self):
        p = Posting("Assets:Cash", Money("-2.50", "EUR"), price=Money("1.1", "USD"), memo="m", meta={"a": "b"})
        assert Posting.from_dict(p.to_dict()) == p


class TestTransaction:
    def test_balanced(self):
        tx = simple()
        assert tx.is_balanced()
        assert tx.residual().is_zero()

    def test_unbalanced(self):
        tx = Transaction(D, postings=[Posting("Expenses:Food", Money("10", "USD")), Posting("Assets:Cash", Money("-9", "USD"))])
        assert not tx.is_balanced()
        with pytest.raises(LedgerError):
            tx.validate()

    def test_needs_two_postings(self):
        tx = Transaction(D, postings=[Posting("Expenses:Food", Money("0", "USD"))])
        with pytest.raises(LedgerError):
            tx.validate()

    def test_tolerance_from_precision(self):
        tx = Transaction(
            D,
            postings=[
                Posting("Assets:Broker:AAPL", Money("3", "AAPL"), price=Money("101.333", "USD")),
                Posting("Assets:Cash", Money("-304.00", "USD")),
            ],
        )
        assert tx.is_balanced()

    def test_plain_postings_must_balance_exactly(self):
        tx = Transaction(
            D,
            postings=[Posting("Expenses:Food", Money("10.004", "USD")), Posting("Assets:Cash", Money("-10.00", "USD"))],
        )
        assert not tx.is_balanced()

    def test_price_currency_must_differ(self):
        with pytest.raises(LedgerError):
            Posting("Assets:Cash", Money("1", "USD"), price=Money("1", "USD"))

    def test_multicurrency_with_price(self):
        tx = Transaction(
            D,
            postings=[
                Posting("Assets:Bank:Checking", Money("-110", "USD")),
                Posting("Assets:Cash", Money("100", "EUR"), price=Money("1.10", "USD")),
            ],
        )
        assert tx.is_balanced()

    def test_accounts_and_currencies(self):
        tx = simple()
        assert tx.accounts() == ["Expenses:Food", "Assets:Cash"]
        assert tx.currencies() == ["USD"]

    def test_touches_parent(self):
        tx = simple()
        assert tx.touches("Expenses")
        assert tx.touches("Assets:Cash")
        assert not tx.touches("Income")

    def test_amount_for(self):
        tx = simple("12.50")
        assert tx.amount_for("Assets").get("USD") == Money("-12.50", "USD")

    def test_total_debits(self):
        assert simple("7").total_debits().get("USD") == Money("7", "USD")

    def test_description(self):
        assert simple().description == "Shop | stuff"
        assert Transaction(D, narration="only").description == "only"

    def test_copy_is_deep(self):
        tx = simple()
        other = tx.copy()
        other.postings[0].memo = "changed"
        other.tags.add("x")
        assert tx.postings[0].memo == ""
        assert "x" not in tx.tags

    def test_dict_roundtrip(self):
        tx = TransactionBuilder(D, "P", "N").debit("Expenses:Food", "3", "USD").credit("Assets:Cash", "3", "USD").tag("t").link("l").meta(k="v").build()
        again = Transaction.from_dict(tx.to_dict())
        assert again.tags == {"t"}
        assert again.links == {"l"}
        assert again.meta == {"k": "v"}
        assert again.postings == tx.postings

    def test_add_posting(self):
        tx = Transaction(D)
        tx.add_posting("Expenses:Food", "4", "USD")
        tx.add_posting("Assets:Cash", "-4", "USD")
        assert tx.is_balanced()


class TestBuilder:
    def test_debit_credit_signs(self):
        tx = simple("5")
        amounts = {p.account: p.amount for p in tx.postings}
        assert amounts["Expenses:Food"] == Money("5", "USD")
        assert amounts["Assets:Cash"] == Money("-5", "USD")

    def test_debit_credit_ignore_input_sign(self):
        tx = TransactionBuilder(D).debit("Expenses:Food", "-5", "USD").credit("Assets:Cash", "-5", "USD").build()
        assert tx.postings[0].amount == Money("5", "USD")
        assert tx.postings[1].amount == Money("-5", "USD")

    def test_unbalanced_build(self):
        with pytest.raises(LedgerError):
            TransactionBuilder(D).debit("Expenses:Food", "5", "USD").credit("Assets:Cash", "4", "USD").build()

    def test_build_without_validation(self):
        tx = TransactionBuilder(D).debit("Expenses:Food", "5", "USD").build(validate=False)
        assert len(tx.postings) == 1

    def test_tags_and_links(self):
        tx = TransactionBuilder(D).tag("a", "b").link("inv-1").debit("Expenses:Food", "1", "USD").credit("Assets:Cash", "1", "USD").build()
        assert tx.tags == {"a", "b"}
        assert tx.links == {"inv-1"}


class TestHelpers:
    def test_transfer(self):
        tx = transfer(D, "Assets:Bank:Checking", "Assets:Bank:Savings", Money("250", "USD"))
        amounts = {p.account: p.amount.amount for p in tx.postings}
        assert amounts == {"Assets:Bank:Checking": Decimal("-250"), "Assets:Bank:Savings": Decimal("250")}

    def test_transfer_is_balanced(self):
        tx = transfer(D, "Assets:Cash", "Assets:Bank:Checking", Money("12.25", "USD"), payee="ATM")
        assert tx.is_balanced()
        assert tx.payee == "ATM"

    def test_split_transaction(self):
        tx = split_transaction(
            D,
            "Assets:Cash",
            [("Expenses:Food", Money("25.00", "USD")), ("Expenses:Household", Money("75.00", "USD"))],
            payee="Store",
        )
        amounts = {p.account: p.amount for p in tx.postings}
        assert amounts["Expenses:Food"] == Money("25.00", "USD")
        assert amounts["Expenses:Household"] == Money("75.00", "USD")
        assert amounts["Assets:Cash"] == Money("-100", "USD")
        assert tx.is_balanced()

    def test_split_requires_splits(self):
        with pytest.raises(LedgerError):
            split_transaction(D, "Assets:Cash", [])
