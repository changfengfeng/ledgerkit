from __future__ import annotations

from datetime import date

import pytest

from ledgerkit.errors import LedgerError
from ledgerkit.query import compile_query, matching_transactions, run_query, tokenize


def accounts(journal, text):
    return sorted({p.account for _, p in run_query(journal, text)})


def test_tokenize():
    kinds = [t.kind for t in tokenize('account:Expenses and (payee~"gro" or not tag:x)')]
    assert kinds.count("lparen") == 1 and kinds.count("rparen") == 1


def test_empty_query_matches_everything(populated):
    assert len(list(run_query(populated, ""))) == 16


def test_account_prefix(populated):
    assert accounts(populated, "account:Expenses") == ["Expenses:Food", "Expenses:Rent"]


def test_account_exact(populated):
    assert accounts(populated, "account=Expenses:Rent") == ["Expenses:Rent"]


def test_payee_regex(populated):
    txs = matching_transactions(populated, "payee~^land")
    assert {t.payee for t in txs} == {"Landlord"}


def test_amount_comparison(populated):
    rows = list(run_query(populated, "amount>1000 and account:Expenses"))
    assert {p.account for _, p in rows} == {"Expenses:Rent"}


def test_date_comparison(populated):
    txs = matching_transactions(populated, "date>=2024-02-01")
    assert all(t.date >= date(2024, 2, 1) for t in txs)
    assert len(txs) == 4


def test_and_or_not(populated):
    a = matching_transactions(populated, "payee:Grocer or payee:Landlord")
    assert len(a) == 4
    b = matching_transactions(populated, "not payee:Employer and account:Assets:Bank")
    assert all(t.payee != "Employer" for t in b)


def test_parentheses(populated):
    txs = matching_transactions(populated, "(payee:Grocer or payee:Landlord) and date<2024-02-01")
    assert len(txs) == 2


def test_currency_and_inequality(populated):
    assert len(list(run_query(populated, "currency:EUR"))) == 0
    assert len(list(run_query(populated, "currency!=EUR"))) == 16


def test_quoted_values(populated):
    assert len(matching_transactions(populated, 'payee:"Card payment"')) == 1


@pytest.mark.parametrize("text", ["bogus:1", "(account:Assets", "amount>abc", "account:Assets or"])
def test_syntax_errors(text):
    with pytest.raises(LedgerError):
        compile_query(text)
