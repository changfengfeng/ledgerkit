"""Recurring transactions (rent, salaries, subscriptions...)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

from .errors import LedgerError
from .journal import Journal
from .transactions import Transaction
from .utils.dates import add_months


class Frequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


@dataclass(frozen=True)
class RecurrenceRule:
    frequency: Frequency
    start: date
    interval: int = 1
    end: date | None = None
    count: int | None = None
    weekdays_only: bool = False

    def __post_init__(self) -> None:
        if self.interval < 1:
            raise LedgerError("recurrence interval must be >= 1")
        if self.count is not None and self.count < 1:
            raise LedgerError("recurrence count must be >= 1")
        if self.end is not None and self.end < self.start:
            raise LedgerError("recurrence end precedes start")

    def _nth(self, n: int) -> date:
        """The n-th raw occurrence (0-based), always computed from ``start``.

        Computing from the anchor rather than from the previous occurrence
        avoids day-of-month drift (31 Jan -> 28 Feb -> 28 Mar ...).
        """
        step = n * self.interval
        if self.frequency is Frequency.DAILY:
            return self.start + timedelta(days=step)
        if self.frequency is Frequency.WEEKLY:
            return self.start + timedelta(weeks=step)
        if self.frequency is Frequency.MONTHLY:
            return add_months(self.start, step, anchor_day=self.start.day)
        if self.frequency is Frequency.QUARTERLY:
            return add_months(self.start, 3 * step, anchor_day=self.start.day)
        return add_months(self.start, 12 * step, anchor_day=self.start.day)

    @staticmethod
    def _roll_to_weekday(day: date) -> date:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day

    def occurrences(self, until: date | None = None) -> Iterator[date]:
        limit = until
        if self.end is not None:
            limit = self.end if limit is None else min(limit, self.end)
        if limit is None and self.count is None:
            raise LedgerError("unbounded recurrence: provide until, end or count")
        n = 0
        while True:
            if self.count is not None and n >= self.count:
                return
            day = self._nth(n)
            if self.weekdays_only:
                day = self._roll_to_weekday(day)
            if limit is not None and day > limit:
                return
            yield day
            n += 1

    def between(self, start: date, end: date) -> list[date]:
        return [d for d in self.occurrences(until=end) if d >= start]


@dataclass
class RecurringTemplate:
    name: str
    rule: RecurrenceRule
    template: Transaction
    last_generated: date | None = None
    tags: set[str] = field(default_factory=set)

    def instantiate(self, day: date) -> Transaction:
        tx = self.template.copy()
        tx.id = None
        tx.date = day
        tx.tags |= self.tags | {"recurring"}
        tx.meta["recurring"] = self.name
        tx.meta["occurrence"] = day.isoformat()
        return tx

    def due(self, through: date) -> list[date]:
        days = list(self.rule.occurrences(until=through))
        if self.last_generated is not None:
            days = [d for d in days if d > self.last_generated]
        return days


class RecurringScheduler:
    def __init__(self) -> None:
        self._templates: dict[str, RecurringTemplate] = {}

    def register(self, template: RecurringTemplate) -> RecurringTemplate:
        template.template.validate()
        if template.name in self._templates:
            raise LedgerError(f"recurring template {template.name!r} already exists")
        self._templates[template.name] = template
        return template

    def get(self, name: str) -> RecurringTemplate:
        try:
            return self._templates[name]
        except KeyError:
            raise LedgerError(f"no recurring template named {name!r}") from None

    def unregister(self, name: str) -> None:
        self._templates.pop(name, None)

    def templates(self) -> list[RecurringTemplate]:
        return [self._templates[k] for k in sorted(self._templates)]

    def preview(self, through: date) -> list[Transaction]:
        result = []
        for template in self.templates():
            for day in template.due(through):
                result.append(template.instantiate(day))
        result.sort(key=lambda t: (t.date, t.meta.get("recurring", "")))
        return result

    def materialize(self, journal: Journal, through: date) -> list[Transaction]:
        """Post every due occurrence up to ``through`` into ``journal``.

        Already generated occurrences (tracked per template, and detected via
        ``recurring``/``occurrence`` metadata in the journal) are skipped.
        """
        existing = {
            (tx.meta.get("recurring"), tx.meta.get("occurrence"))
            for tx in journal.sorted()
            if "recurring" in tx.meta
        }
        created = []
        for template in self.templates():
            for day in template.due(through):
                if (template.name, day.isoformat()) in existing:
                    template.last_generated = day
                    continue
                created.append(journal.add(template.instantiate(day)))
                template.last_generated = day
        return created
