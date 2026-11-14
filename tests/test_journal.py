from __future__ import annotations

from datetime import date

import pytest

from ledgerkit import Journal, TransactionBuilder
from ledgerkit.balances import balance
from ledgerkit.errors import LedgerError, TransactionNotFoundError
from ledgerkit.money import Money


def tx(day, amount="10", payee="Shop", expense="Expenses:Food", funding="Assets:Cash", tags=()):
    builder = TransactionBuilder(day, payee).debit(expense, amount, "USD").credit(funding, amount, "USD")
    if tags:
        builder.tag(*tags)
    return builder.build()


class TestAdd:
    def test_assigns_sequential_ids(self, journal):
        a = journal.add(tx(date(2024, 1, 2)))
        b = journal.add(tx(date(2024, 1, 3)))
        assert a.id == "tx-000001"
        assert b.id == "tx-000002"
        assert len(journal) == 2

    def test_keeps_explicit_id(self, journal):
        t = tx(date(2024, 1, 2))
        t.id = "custom"
        journal.add(t)
        assert journal.get("custom") is t

    def test_duplicate_id(self, journal):
        t = tx(date(2024, 1, 2))
        t.id = "dup"
        journal.add(t)
        again = tx(date(2024, 1, 3))
        again.id = "dup"
        with pytest.raises(LedgerError):
            journal.add(again)

    def test_unknown_account(self, journal):
        with pytest.raises(LedgerError):
            journal.add(tx(date(2024, 1, 2), expense="Expenses:Unknown"))

    def test_before_account_open(self, journal):
        with pytest.raises(LedgerError):
            journal.add(tx(date(2023, 6, 1)))

    def test_add_many(self, journal):
        added = journal.add_many([tx(date(2024, 1, d)) for d in range(2, 6)])
        assert len(added) == 4

    def test_contains_and_iter(self, journal):
        t = journal.add(tx(date(2024, 1, 2)))
        assert t.id in journal
        assert list(journal) == [t]


class TestLocking:
    def test_locked_period_rejects_add(self, journal):
        journal.lock_through(date(2024, 1, 31))
        with pytest.raises(LedgerError):
            journal.add(tx(date(2024, 1, 15)))
        journal.add(tx(date(2024, 2, 1)))

    def test_lock_cannot_move_backwards(self, journal):
        journal.lock_through(date(2024, 1, 31))
        with pytest.raises(LedgerError):
            journal.lock_through(date(2024, 1, 1))

    def test_unlock(self, journal):
        journal.lock_through(date(2024, 1, 31))
        journal.unlock()
        assert journal.locked_through is None
        journal.add(tx(date(2024, 1, 15)))

    def test_locked_remove(self, journal):
        t = journal.add(tx(date(2024, 1, 15)))
        journal.lock_through(date(2024, 1, 31))
        with pytest.raises(LedgerError):
            journal.remove(t.id)


class TestMutation:
    def test_replace(self, journal):
        t = journal.add(tx(date(2024, 1, 2), "10"))
        new = journal.replace(t.id, tx(date(2024, 1, 2), "25"))
        assert new.id == t.id
        assert balance(journal, "Expenses").get("USD") == Money("25", "USD")

    def test_replace_unknown(self, journal):
        with pytest.raises(TransactionNotFoundError):
            journal.replace("nope", tx(date(2024, 1, 2)))

    def test_remove(self, journal):
        t = journal.add(tx(date(2024, 1, 2)))
        journal.remove(t.id)
        assert len(journal) == 0

    def test_void_marks_original(self, journal):
        t = journal.add(tx(date(2024, 1, 2)))
        reversal = journal.void(t.id, date(2024, 1, 5), "duplicate")
        assert journal.get(t.id).voided
        assert journal.get(t.id).voided_by == reversal.id
        assert reversal.reversal_of == t.id
        assert reversal.date == date(2024, 1, 5)
        assert "duplicate" in reversal.narration

    def test_void_twice(self, journal):
        t = journal.add(tx(date(2024, 1, 2)))
        journal.void(t.id)
        with pytest.raises(LedgerError):
            journal.void(t.id)

    def test_get_unknown(self, journal):
        with pytest.raises(TransactionNotFoundError):
            journal.get("missing")


class TestEvents:
    def test_listeners_receive_events(self, journal):
        seen = []
        journal.subscribe(lambda event, t: seen.append((event, t.id)))
        t = journal.add(tx(date(2024, 1, 2)))
        journal.remove(t.id)
        assert seen == [("add", t.id), ("remove", t.id)]

    def test_replace_event(self, journal):
        seen = []
        t = journal.add(tx(date(2024, 1, 2)))
        journal.subscribe(lambda event, x: seen.append(event))
        journal.replace(t.id, tx(date(2024, 1, 2), "11"))
        assert seen == ["replace"]


class TestQueries:
    def test_sorted_by_date(self, populated):
        days = [t.date for t in populated.sorted()]
        assert days == sorted(days)

    def test_filter_by_range(self, populated):
        txs = populated.transactions(start=date(2024, 2, 1), end=date(2024, 2, 28))
        assert len(txs) == 4

    def test_filter_by_account(self, populated):
        assert len(populated.transactions(account="Expenses:Rent")) == 2
        assert len(populated.transactions(account="Expenses")) == 4

    def test_filter_by_payee(self, populated):
        assert len(populated.transactions(payee="employer")) == 2

    def test_filter_by_tag(self, journal):
        journal.add(tx(date(2024, 1, 2), tags=("trip",)))
        journal.add(tx(date(2024, 1, 3)))
        assert len(journal.transactions(tags=["#trip"])) == 1

    def test_postings(self, populated):
        postings = list(populated.postings(account="Income"))
        assert len(postings) == 2
        assert all(p.account == "Income:Salary" for _, p in postings)

    def test_payees_and_accounts(self, populated):
        assert "Landlord" in populated.payees()
        assert "Expenses:Rent" in populated.accounts_used()
        assert populated.currencies() == ["USD"]

    def test_date_range(self, populated, journal):
        assert populated.date_range() == (date(2024, 1, 1), date(2024, 2, 25))
        assert Journal().date_range() is None

    def test_find_by_link_and_meta(self, journal):
        t = TransactionBuilder(date(2024, 1, 2)).link("inv-9").meta(ref="A1").debit("Expenses:Food", "1", "USD").credit("Assets:Cash", "1", "USD").build()
        journal.add(t)
        assert journal.find_by_link("inv-9") == [t]
        assert journal.find_by_meta("ref", "A1") == [t]


class TestVoid:
    def test_void_keeps_original_postings(self, journal):
        t = journal.add(tx(date(2024, 1, 2), "10"))
        before = [(p.account, p.amount) for p in t.postings]
        journal.void(t.id)
        assert [(p.account, p.amount) for p in journal.get(t.id).postings] == before

    def test_void_reversal_mirrors_postings(self, journal):
        t = journal.add(tx(date(2024, 1, 2), "10"))
        reversal = journal.void(t.id)
        assert [(p.account, p.amount) for p in reversal.postings] == [
            (p.account, -p.amount) for p in t.postings
        ]
        assert all(r is not o for r, o in zip(reversal.postings, t.postings, strict=True))

    def test_void_nets_balances_to_zero(self, journal):
        t = journal.add(tx(date(2024, 1, 2), "10"))
        journal.void(t.id)
        assert balance(journal, "Assets").get("USD").is_zero()
        assert balance(journal, "Expenses").get("USD").is_zero()
