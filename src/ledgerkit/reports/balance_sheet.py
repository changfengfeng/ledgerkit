"""Balance sheet."""

from __future__ import annotations

from datetime import date

from ..accounts import AccountType, account_type_of
from ..balances import balances_by_account, rollup
from ..journal import Journal
from ..money import MoneyBag
from .base import Report


def balance_sheet(
    journal: Journal,
    as_of: date,
    currency: str | None = None,
    depth: int | None = None,
) -> Report:
    """Assets, liabilities and equity as of ``as_of`` (inclusive).

    Income and expenses that have not been closed yet are shown as
    ``Equity:CurrentEarnings`` so the sheet always balances.  With ``currency``
    every balance is converted at the ``as_of`` rate (closing rate method).
    """
    leaf = balances_by_account(journal, end=as_of)
    earnings = MoneyBag()
    sheet: dict[str, MoneyBag] = {}
    for name, bag in leaf.items():
        if account_type_of(name).is_income_statement:
            earnings.merge(bag)
        else:
            sheet[name] = bag
    if earnings:
        sheet["Equity:CurrentEarnings"] = sheet.get("Equity:CurrentEarnings", MoneyBag()) + earnings
    if currency is not None:
        code = currency.upper()
        sheet = {
            name: MoneyBag([journal.rates.convert_bag(bag, code, as_of)]) for name, bag in sheet.items()
        }
    rolled = rollup(sheet)

    def visible(name: str) -> bool:
        return depth is None or name.count(":") < depth

    report = Report(
        title="Balance Sheet",
        subtitle=f"as of {as_of.isoformat()}",
        columns=["Account", "Amount"],
    )
    sections = (
        (AccountType.ASSETS, "Assets", 1),
        (AccountType.LIABILITIES, "Liabilities", -1),
        (AccountType.EQUITY, "Equity", -1),
    )
    totals: dict[str, MoneyBag] = {}
    for kind, label, sign in sections:
        report.add(label, kind="header")
        for name, bag in rolled.items():
            if name.startswith(kind.value + ":") and visible(name):
                report.add(name, bag if sign > 0 else -bag, level=name.count(":"))
        total = rolled.get(kind.value, MoneyBag())
        total = total if sign > 0 else -total
        totals[label] = total
        report.add(f"Total {label}", total, kind="total")
        report.add_separator()
    liabilities_and_equity = totals["Liabilities"] + totals["Equity"]
    report.add("Total Liabilities + Equity", liabilities_and_equity, kind="total")
    difference = totals["Assets"] - liabilities_and_equity
    report.meta["balanced"] = difference.is_zero()
    report.meta["assets"] = totals["Assets"]
    report.meta["liabilities"] = totals["Liabilities"]
    report.meta["equity"] = totals["Equity"]
    report.meta["difference"] = difference
    return report
