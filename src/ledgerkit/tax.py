"""Sales tax / VAT helpers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .errors import LedgerError
from .journal import Journal
from .money import Money, to_decimal
from .transactions import Posting, Transaction


@dataclass(frozen=True)
class TaxRate:
    code: str
    rate: Decimal
    account: str
    description: str = ""

    def __init__(self, code: str, rate: Decimal | str | float, account: str, description: str = "") -> None:
        value = to_decimal(rate)
        if value < 0 or value >= 1:
            raise LedgerError(f"tax rate must be in [0, 1): {value}")
        object.__setattr__(self, "code", code.upper())
        object.__setattr__(self, "rate", value)
        object.__setattr__(self, "account", account)
        object.__setattr__(self, "description", description)


@dataclass(frozen=True)
class TaxBreakdown:
    net: Money
    tax: Money
    gross: Money


def from_net(net: Money, rate: TaxRate) -> TaxBreakdown:
    tax = (net * rate.rate).quantize(rounding="half_up")
    return TaxBreakdown(net=net.quantize(), tax=tax, gross=net.quantize() + tax)


def from_gross(gross: Money, rate: TaxRate) -> TaxBreakdown:
    gross_q = gross.quantize()
    net = (gross_q / (1 + rate.rate)).quantize(rounding="half_up")
    return TaxBreakdown(net=net, tax=gross_q - net, gross=gross_q)


def taxed_purchase(
    on_date: date,
    payee: str,
    expense_account: str,
    funding_account: str,
    gross: Money,
    rate: TaxRate,
    narration: str = "",
) -> Transaction:
    """Build a purchase where the tax portion is posted to the tax account."""
    breakdown = from_gross(gross, rate)
    tx = Transaction(date=on_date, payee=payee, narration=narration, meta={"tax_code": rate.code})
    tx.postings.append(Posting(expense_account, breakdown.net))
    if breakdown.tax:
        tx.postings.append(Posting(rate.account, breakdown.tax, meta={"tax_code": rate.code}))
    tx.postings.append(Posting(funding_account, -breakdown.gross))
    return tx


class TaxRegistry:
    def __init__(self) -> None:
        self._rates: dict[str, TaxRate] = {}

    def add(self, rate: TaxRate) -> TaxRate:
        self._rates[rate.code] = rate
        return rate

    def get(self, code: str) -> TaxRate:
        try:
            return self._rates[code.upper()]
        except KeyError:
            raise LedgerError(f"unknown tax code: {code}") from None

    def codes(self) -> list[str]:
        return sorted(self._rates)

    def summary(self, journal: Journal, start: date, end: date) -> dict[str, dict[str, Money]]:
        """Total tax collected/paid per tax code and currency in the range."""
        totals: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        accounts = {rate.account: rate.code for rate in self._rates.values()}
        for _tx, posting in journal.postings(start=start, end=end):
            code = posting.meta.get("tax_code") or accounts.get(posting.account)
            if code is None or posting.account not in accounts:
                continue
            totals[code][posting.currency] += posting.amount.amount
        return {
            code: {cur: Money(v, cur) for cur, v in sorted(by_cur.items())}
            for code, by_cur in sorted(totals.items())
        }
