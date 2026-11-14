"""Balance computation over a journal."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .accounts import account_type_of, ancestors, is_descendant
from .currency import RateTable
from .journal import Journal
from .money import Money, MoneyBag
from .transactions import Transaction
from .utils.dates import iter_months


def _in_range(tx: Transaction, start: date | None, end: date | None) -> bool:
    if start is not None and tx.date < start:
        return False
    if end is not None and tx.date > end:
        return False
    return True


def balance(
    journal: Journal,
    account: str,
    as_of: date | None = None,
    start: date | None = None,
    include_children: bool = True,
) -> MoneyBag:
    """Sum of postings to ``account`` between ``start`` and ``as_of`` (inclusive)."""
    bag = MoneyBag()
    for tx in journal.sorted():
        if not _in_range(tx, start, as_of):
            continue
        for posting in tx.postings:
            matches = (
                is_descendant(posting.account, account)
                if include_children
                else posting.account == account
            )
            if matches:
                bag.add(posting.amount)
    return bag


def balances_by_account(
    journal: Journal,
    start: date | None = None,
    end: date | None = None,
    accounts: Iterable[str] | None = None,
) -> dict[str, MoneyBag]:
    """Leaf-level balances for every account with activity in the range."""
    wanted = list(accounts) if accounts is not None else None
    result: dict[str, MoneyBag] = defaultdict(MoneyBag)
    for tx in journal.sorted():
        if not _in_range(tx, start, end):
            continue
        for posting in tx.postings:
            if wanted is not None and not any(is_descendant(posting.account, w) for w in wanted):
                continue
            result[posting.account].add(posting.amount)
    return dict(sorted(result.items()))


def rollup(balances: dict[str, MoneyBag]) -> dict[str, MoneyBag]:
    """Add every leaf balance to all of its ancestors."""
    rolled: dict[str, MoneyBag] = defaultdict(MoneyBag)
    for name, bag in balances.items():
        rolled[name].merge(bag)
        for parent in ancestors(name):
            rolled[parent].merge(bag)
    return dict(sorted(rolled.items()))


def signed_for_display(account: str, bag: MoneyBag) -> MoneyBag:
    """Flip credit-normal accounts so that their natural balance shows positive."""
    if account_type_of(account).normal_sign < 0:
        return -bag
    return bag


@dataclass(frozen=True)
class RegisterLine:
    date: date
    tx_id: str
    description: str
    account: str
    amount: Money
    running: Money


def running_balance(
    journal: Journal,
    account: str,
    currency: str,
    start: date | None = None,
    end: date | None = None,
) -> list[RegisterLine]:
    """Per-posting register for one account and currency.

    The running total includes all activity before ``start`` as an opening
    balance so that the first line is correct.
    """
    running = Money.zero(currency)
    lines: list[RegisterLine] = []
    for tx in journal.sorted():
        if end is not None and tx.date > end:
            break
        for posting in tx.postings:
            if posting.currency != currency.upper() or not is_descendant(posting.account, account):
                continue
            running = running + posting.amount
            if start is not None and tx.date < start:
                continue
            lines.append(
                RegisterLine(
                    date=tx.date,
                    tx_id=tx.id or "",
                    description=tx.description,
                    account=posting.account,
                    amount=posting.amount,
                    running=running,
                )
            )
    return lines


def balance_in(
    journal: Journal,
    account: str,
    currency: str,
    rates: RateTable | None = None,
    as_of: date | None = None,
) -> Money:
    """Account balance converted into ``currency`` at the ``as_of`` rate."""
    table = rates or journal.rates
    bag = balance(journal, account, as_of=as_of)
    return table.convert_bag(bag, currency, as_of)


def net_worth(
    journal: Journal,
    currency: str,
    rates: RateTable | None = None,
    as_of: date | None = None,
) -> Money:
    assets = balance_in(journal, "Assets", currency, rates, as_of)
    liabilities = balance_in(journal, "Liabilities", currency, rates, as_of)
    return assets + liabilities


def monthly_activity(
    journal: Journal, account: str, start: date, end: date
) -> list[tuple[date, MoneyBag]]:
    """Net change for ``account`` per calendar month."""
    months = list(iter_months(start, end))
    buckets: dict[date, MoneyBag] = {first: MoneyBag() for first, _ in months}
    for tx in journal.transactions(start=start, end=end):
        first = tx.date.replace(day=1)
        if first not in buckets:
            continue
        for posting in tx.postings_for(account):
            buckets[first].add(posting.amount)
    return [(first, buckets[first]) for first, _ in months]


def average_monthly(journal: Journal, account: str, currency: str, start: date, end: date) -> Money:
    activity = monthly_activity(journal, account, start, end)
    if not activity:
        return Money.zero(currency)
    total = sum((bag.get(currency).amount for _, bag in activity), Decimal(0))
    return Money(total / len(activity), currency).quantize()


def turnover(journal: Journal, account: str, start: date | None = None, end: date | None = None) -> tuple[MoneyBag, MoneyBag]:
    """Return (debits, credits) posted to ``account`` in the range."""
    debits = MoneyBag()
    credits = MoneyBag()
    for _tx, posting in journal.postings(account, start, end):
        if posting.is_debit():
            debits.add(posting.amount)
        else:
            credits.add(posting.amount)
    return debits, credits
