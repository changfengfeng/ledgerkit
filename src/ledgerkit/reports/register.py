"""Account register report."""

from __future__ import annotations

from datetime import date

from ..balances import running_balance
from ..journal import Journal
from ..utils.text import truncate
from .base import Report


def register(
    journal: Journal,
    account: str,
    currency: str,
    start: date | None = None,
    end: date | None = None,
    width: int = 40,
) -> Report:
    lines = running_balance(journal, account, currency, start=start, end=end)
    report = Report(
        title=f"Register: {account} ({currency.upper()})",
        columns=["Date", "Id", "Description", "Account", "Amount", "Balance"],
        aligns=["left", "left", "left", "left", "right", "right"],
    )
    for line in lines:
        report.add(
            line.date,
            line.tx_id,
            truncate(line.description, width),
            line.account,
            line.amount,
            line.running,
        )
    report.meta["count"] = len(lines)
    report.meta["final"] = lines[-1].running if lines else None
    return report
