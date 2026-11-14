"""Cash flow statement (direct method)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from ..accounts import AccountType, account_type_of, is_descendant
from ..journal import Journal
from ..money import MoneyBag
from .base import Report

DEFAULT_CASH_ACCOUNTS = ("Assets:Cash", "Assets:Bank")


def _category(account: str) -> str:
    kind = account_type_of(account)
    if kind in (AccountType.INCOME, AccountType.EXPENSES):
        return "Operating"
    if kind is AccountType.LIABILITIES or kind is AccountType.EQUITY:
        return "Financing"
    return "Investing"


def cash_flow(
    journal: Journal,
    start: date,
    end: date,
    cash_accounts: tuple[str, ...] = DEFAULT_CASH_ACCOUNTS,
) -> Report:
    """Classify every movement of cash by the counter-account it came from.

    For each transaction touching a cash account the cash delta is attributed
    to the non-cash postings proportionally; with the usual two-legged entries
    that is simply the counter account.
    """

    def is_cash(name: str) -> bool:
        return any(is_descendant(name, c) for c in cash_accounts)

    by_category: dict[str, dict[str, MoneyBag]] = defaultdict(lambda: defaultdict(MoneyBag))
    net = MoneyBag()
    for tx in journal.transactions(start=start, end=end):
        cash = [p for p in tx.postings if is_cash(p.account)]
        if not cash:
            continue
        others = [p for p in tx.postings if not is_cash(p.account)]
        if not others:
            continue  # transfer between cash accounts
        for posting in others:
            flow = -posting.weight()
            by_category[_category(posting.account)][posting.account].add(flow)
            net.add(flow)

    opening = MoneyBag()
    closing = MoneyBag()
    for tx in journal.sorted():
        for posting in tx.postings:
            if not is_cash(posting.account):
                continue
            if tx.date < start:
                opening.add(posting.amount)
            if tx.date <= end:
                closing.add(posting.amount)

    report = Report(
        title="Cash Flow Statement",
        subtitle=f"{start.isoformat()} .. {end.isoformat()}",
        columns=["Item", "Amount"],
    )
    report.add("Opening cash", opening, kind="total")
    report.add_separator()
    for category in ("Operating", "Investing", "Financing"):
        accounts = by_category.get(category, {})
        report.add(category, kind="header")
        subtotal = MoneyBag()
        for name in sorted(accounts):
            report.add(name, accounts[name], level=1)
            subtotal.merge(accounts[name])
        report.add(f"Net {category.lower()} cash flow", subtotal, kind="total")
        report.add_separator()
    report.add("Net change in cash", net, kind="total")
    report.add("Closing cash", closing, kind="total")
    report.meta["opening"] = opening
    report.meta["closing"] = closing
    report.meta["net_change"] = net
    report.meta["consistent"] = (opening + net) == closing
    return report
