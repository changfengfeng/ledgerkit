"""Trial balance: debit and credit totals per account."""

from __future__ import annotations

from datetime import date

from ..balances import balances_by_account
from ..journal import Journal
from ..money import Money
from .base import Report


def trial_balance(journal: Journal, as_of: date | None = None, currency: str | None = None) -> Report:
    """List every account with a non-zero balance in debit/credit columns.

    One block per currency; each block's debit and credit totals must match
    for a consistent journal.
    """
    balances = balances_by_account(journal, end=as_of)
    currencies = sorted({c for bag in balances.values() for c in bag.currencies()})
    if currency is not None:
        currencies = [c for c in currencies if c == currency.upper()]
    report = Report(
        title="Trial Balance",
        subtitle=f"as of {as_of.isoformat()}" if as_of else "all dates",
        columns=["Account", "Debit", "Credit"],
    )
    totals: dict[str, tuple[Money, Money]] = {}
    for code in currencies:
        debit_total = Money.zero(code)
        credit_total = Money.zero(code)
        rows = []
        for account, bag in balances.items():
            amount = bag.get(code)
            if amount.is_zero():
                continue
            if amount.is_positive():
                rows.append((account, amount, None))
                debit_total = debit_total + amount
            else:
                rows.append((account, None, -amount))
                credit_total = credit_total + (-amount)
        if not rows:
            continue
        report.add(code, kind="header")
        for account, debit, credit in rows:
            report.add(account, debit, credit, level=1)
        report.add("Total", debit_total, credit_total, kind="total")
        report.add_separator()
        totals[code] = (debit_total, credit_total)
    report.meta["balanced"] = all(d == c for d, c in totals.values())
    report.meta["totals"] = {c: {"debit": str(d.amount), "credit": str(k.amount)} for c, (d, k) in totals.items()}
    return report
