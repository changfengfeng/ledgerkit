from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ledgerkit import Journal, TransactionBuilder, default_chart
from ledgerkit.parser import load_file

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def chart():
    return default_chart(date(2024, 1, 1))


@pytest.fixture
def journal(chart):
    return Journal(chart)


@pytest.fixture
def populated(journal):
    """A small journal with a handful of USD transactions."""
    entries = [
        (date(2024, 1, 1), "Opening", [("Assets:Bank:Checking", "5000"), ("Equity:Opening-Balances", "-5000")]),
        (date(2024, 1, 5), "Employer", [("Assets:Bank:Checking", "3000"), ("Income:Salary", "-3000")]),
        (date(2024, 1, 7), "Landlord", [("Expenses:Rent", "1200"), ("Assets:Bank:Checking", "-1200")]),
        (date(2024, 1, 15), "Grocer", [("Expenses:Food", "150.25"), ("Liabilities:CreditCard", "-150.25")]),
        (date(2024, 2, 5), "Employer", [("Assets:Bank:Checking", "3000"), ("Income:Salary", "-3000")]),
        (date(2024, 2, 7), "Landlord", [("Expenses:Rent", "1200"), ("Assets:Bank:Checking", "-1200")]),
        (date(2024, 2, 20), "Grocer", [("Expenses:Food", "99.75"), ("Liabilities:CreditCard", "-99.75")]),
        (date(2024, 2, 25), "Card payment", [("Liabilities:CreditCard", "150.25"), ("Assets:Bank:Checking", "-150.25")]),
    ]
    for day, payee, legs in entries:
        builder = TransactionBuilder(day, payee)
        for account, amount in legs:
            builder.posting(account, amount, "USD")
        journal.add(builder.build())
    return journal


@pytest.fixture
def sample_path():
    return ROOT / "examples" / "sample.ledger"


@pytest.fixture
def sample(sample_path):
    return load_file(sample_path).journal
