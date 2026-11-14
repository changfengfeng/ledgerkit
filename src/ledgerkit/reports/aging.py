"""Receivables / payables aging."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from ..accounts import is_descendant
from ..journal import Journal
from ..money import Money
from .base import Report

DEFAULT_BUCKETS = (30, 60, 90)


def _bucket_labels(buckets: tuple[int, ...]) -> list[str]:
    labels = []
    previous = 0
    for limit in buckets:
        labels.append(f"{previous}-{limit}")
        previous = limit + 1
    labels.append(f">{buckets[-1]}")
    return labels


def aging(
    journal: Journal,
    account: str,
    as_of: date,
    currency: str,
    buckets: tuple[int, ...] = DEFAULT_BUCKETS,
) -> Report:
    """Open items grouped by counterparty (payee) and age.

    Items are settled FIFO per payee: payments (postings with the opposite
    sign) consume the oldest open invoices first.
    """
    if not buckets or list(buckets) != sorted(buckets):
        raise ValueError("buckets must be a non-empty ascending sequence")
    code = currency.upper()
    open_items: dict[str, list[list]] = defaultdict(list)  # payee -> [[date, remaining]]
    for tx in journal.transactions(end=as_of):
        for posting in tx.postings:
            if posting.currency != code or not is_descendant(posting.account, account):
                continue
            payee = tx.payee or "(no payee)"
            amount = posting.amount.amount
            items = open_items[payee]
            # Settle against items with the opposite sign first.
            for item in items:
                if amount == 0:
                    break
                if item[1] == 0 or (item[1] > 0) == (amount > 0):
                    continue
                settled = min(abs(item[1]), abs(amount))
                item[1] += settled if item[1] < 0 else -settled
                amount += settled if amount < 0 else -settled
            if amount != 0:
                items.append([tx.date, amount])

    labels = _bucket_labels(buckets)
    report = Report(
        title=f"Aging: {account}",
        subtitle=f"as of {as_of.isoformat()} in {code}",
        columns=["Payee", *labels, "Total"],
    )
    grand = [Decimal(0)] * (len(labels) + 1)
    for payee in sorted(open_items):
        row = [Decimal(0)] * (len(labels) + 1)
        for when, remaining in open_items[payee]:
            if remaining == 0:
                continue
            age = (as_of - when).days
            index = next((i for i, limit in enumerate(buckets) if age <= limit), len(buckets))
            row[index] += remaining
            row[-1] += remaining
        if row[-1] == 0:
            continue
        for i, value in enumerate(row):
            grand[i] += value
        report.add(payee, *[Money(v, code) for v in row])
    report.add_separator()
    report.add("Total", *[Money(v, code) for v in grand], kind="total")
    report.meta["total"] = Money(grand[-1], code)
    return report
