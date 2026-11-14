"""Income statement (profit and loss)."""

from __future__ import annotations

from datetime import date

from ..accounts import AccountType, account_type_of
from ..balances import balances_by_account, rollup
from ..journal import Journal
from ..money import Money, MoneyBag
from .base import Report


def _converted_totals(
    journal: Journal, start: date, end: date, currency: str
) -> dict[str, Money]:
    """Convert every posting at the rate of its own transaction date."""
    totals: dict[str, Money] = {}
    for tx, posting in journal.postings(start=start, end=end):
        if tx.meta.get("closing") == "true":
            continue
        kind = account_type_of(posting.account)
        if kind not in (AccountType.INCOME, AccountType.EXPENSES):
            continue
        converted = journal.rates.convert(posting.amount, currency, tx.date, quantize=False)
        totals[posting.account] = totals.get(posting.account, Money.zero(currency)) + converted
    return {k: v.quantize() for k, v in sorted(totals.items())}


def income_statement(
    journal: Journal,
    start: date,
    end: date,
    currency: str | None = None,
    depth: int | None = None,
) -> Report:
    """Income and expenses for ``start..end`` (inclusive).

    Without ``currency`` each currency is reported separately.  With
    ``currency`` every posting is converted at its transaction-date rate
    (historical rate method), which is what most tax authorities require.
    """
    report = Report(
        title="Income Statement",
        subtitle=f"{start.isoformat()} .. {end.isoformat()}",
        columns=["Account", "Amount"],
    )
    if currency is not None:
        code = currency.upper()
        leaf = {k: MoneyBag([v]) for k, v in _converted_totals(journal, start, end, code).items()}
    else:
        leaf = {
            k: v
            for k, v in balances_by_account(journal, start=start, end=end).items()
            if account_type_of(k) in (AccountType.INCOME, AccountType.EXPENSES)
        }
        # Closing entries would zero the period out; exclude them.
        for tx in journal.transactions(start=start, end=end):
            if tx.meta.get("closing") == "true":
                for posting in tx.postings:
                    if posting.account in leaf:
                        leaf[posting.account] = leaf[posting.account] - posting.amount
    rolled = rollup(leaf)

    def visible(name: str) -> bool:
        return depth is None or name.count(":") < depth

    income_total = -rolled.get("Income", MoneyBag())
    expense_total = rolled.get("Expenses", MoneyBag())

    report.add("Income", kind="header")
    for name, bag in rolled.items():
        if name.startswith("Income:") and visible(name):
            report.add(name, -bag, level=name.count(":"))
    report.add("Total Income", income_total, kind="total")
    report.add_separator()
    report.add("Expenses", kind="header")
    for name, bag in rolled.items():
        if name.startswith("Expenses:") and visible(name):
            report.add(name, bag, level=name.count(":"))
    report.add("Total Expenses", expense_total, kind="total")
    report.add_separator()
    net = income_total - expense_total
    report.add("Net Income", net, kind="total")
    report.meta["net_income"] = net
    report.meta["income"] = income_total
    report.meta["expenses"] = expense_total
    if currency is not None:
        report.meta["currency"] = currency.upper()
    return report
