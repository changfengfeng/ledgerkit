from __future__ import annotations

from datetime import date

import pytest

from ledgerkit.errors import LedgerError
from ledgerkit.recurring import Frequency, RecurrenceRule, RecurringScheduler, RecurringTemplate
from ledgerkit.transactions import TransactionBuilder


def rent_template(start=date(2024, 1, 31), **kw):
    tx = TransactionBuilder(start, "Landlord", "Rent").debit("Expenses:Rent", "1000", "USD").credit("Assets:Bank:Checking", "1000", "USD").build()
    return RecurringTemplate("rent", RecurrenceRule(Frequency.MONTHLY, start, **kw), tx)


class TestRule:
    def test_monthly_no_drift(self):
        rule = RecurrenceRule(Frequency.MONTHLY, date(2024, 1, 31))
        assert rule.between(date(2024, 1, 1), date(2024, 5, 31)) == [
            date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31), date(2024, 4, 30), date(2024, 5, 31)
        ]

    def test_weekly_interval(self):
        rule = RecurrenceRule(Frequency.WEEKLY, date(2024, 1, 1), interval=2)
        assert rule.between(date(2024, 1, 1), date(2024, 1, 31)) == [date(2024, 1, 1), date(2024, 1, 15), date(2024, 1, 29)]

    def test_daily_count(self):
        rule = RecurrenceRule(Frequency.DAILY, date(2024, 1, 1), count=3)
        assert list(rule.occurrences()) == [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]

    def test_quarterly_and_yearly(self):
        q = RecurrenceRule(Frequency.QUARTERLY, date(2024, 1, 15))
        assert q.between(date(2024, 1, 1), date(2024, 12, 31))[-1] == date(2024, 10, 15)
        y = RecurrenceRule(Frequency.YEARLY, date(2024, 2, 29))
        assert y.between(date(2024, 1, 1), date(2026, 12, 31)) == [date(2024, 2, 29), date(2025, 2, 28), date(2026, 2, 28)]

    def test_end_date(self):
        rule = RecurrenceRule(Frequency.MONTHLY, date(2024, 1, 1), end=date(2024, 3, 15))
        assert len(list(rule.occurrences())) == 3

    def test_weekdays_only(self):
        rule = RecurrenceRule(Frequency.MONTHLY, date(2024, 6, 1), weekdays_only=True, count=1)
        assert list(rule.occurrences()) == [date(2024, 6, 3)]

    @pytest.mark.parametrize("kwargs", [{"interval": 0}, {"count": 0}, {"end": date(2023, 1, 1)}])
    def test_invalid(self, kwargs):
        with pytest.raises(LedgerError):
            RecurrenceRule(Frequency.DAILY, date(2024, 1, 1), **kwargs)


class TestScheduler:
    def test_instantiate(self):
        tx = rent_template().instantiate(date(2024, 2, 29))
        assert tx.date == date(2024, 2, 29)
        assert tx.meta["recurring"] == "rent"
        assert tx.id is None

    def test_preview_does_not_mutate(self):
        scheduler = RecurringScheduler()
        scheduler.register(rent_template())
        assert len(scheduler.preview(date(2024, 3, 31))) == 3
        assert len(scheduler.preview(date(2024, 3, 31))) == 3

    def test_materialize_is_idempotent(self, journal):
        scheduler = RecurringScheduler()
        scheduler.register(rent_template())
        first = scheduler.materialize(journal, date(2024, 3, 31))
        assert [t.date for t in first] == [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31)]
        assert scheduler.materialize(journal, date(2024, 3, 31)) == []
        assert len(scheduler.materialize(journal, date(2024, 4, 30))) == 1
        assert len(journal) == 4

    def test_duplicate_name(self):
        scheduler = RecurringScheduler()
        scheduler.register(rent_template())
        with pytest.raises(LedgerError):
            scheduler.register(rent_template())

    def test_unregister(self):
        scheduler = RecurringScheduler()
        scheduler.register(rent_template())
        scheduler.unregister("rent")
        assert scheduler.templates() == []
        with pytest.raises(LedgerError):
            scheduler.get("rent")
