"""Exchange rates and currency conversion.

A :class:`RateTable` stores dated exchange rates for currency pairs.  A rate
``base/quote = 1.10`` means *one unit of base buys 1.10 units of quote*.

Resolution order for ``get_rate(base, quote, on_date)``:

1. the most recent direct ``base/quote`` rate dated on or before ``on_date``;
2. the inverse of the most recent ``quote/base`` rate;
3. triangulation through the pivot currency (``base/pivot * pivot/quote``).
"""

from __future__ import annotations

import bisect
import csv
import io
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from .errors import RateNotFoundError
from .money import ONE, Money, MoneyBag, get_currency, to_decimal
from .utils.dates import parse_date


@dataclass(frozen=True)
class Rate:
    base: str
    quote: str
    date: date
    value: Decimal
    source: str = "manual"

    def inverse(self) -> Rate:
        return Rate(self.quote, self.base, self.date, ONE / self.value, self.source)

    def to_row(self) -> list[str]:
        return [self.date.isoformat(), self.base, self.quote, str(self.value), self.source]


class RateTable:
    """Dated exchange rates with inverse and pivot resolution."""

    def __init__(self, pivot: str = "USD", max_age_days: int | None = None) -> None:
        self.pivot = get_currency(pivot).code
        self.max_age_days = max_age_days
        self._dates: dict[tuple[str, str], list[date]] = {}
        self._rates: dict[tuple[str, str], list[Rate]] = {}
        self._cache: dict[tuple[str, str], Decimal] = {}

    # -- mutation -----------------------------------------------------------
    def add(
        self,
        base: str,
        quote: str,
        on_date: date | str,
        value: Decimal | str | int | float,
        source: str = "manual",
    ) -> Rate:
        base = get_currency(base).code
        quote = get_currency(quote).code
        if base == quote:
            raise ValueError("cannot add a rate between a currency and itself")
        rate_value = to_decimal(value)
        if rate_value <= 0:
            raise ValueError(f"exchange rate must be positive, got {rate_value}")
        when = parse_date(on_date) if isinstance(on_date, str) else on_date
        rate = Rate(base, quote, when, rate_value, source)
        key = (base, quote)
        dates = self._dates.setdefault(key, [])
        rates = self._rates.setdefault(key, [])
        index = bisect.bisect_left(dates, when)
        if index < len(dates) and dates[index] == when:
            rates[index] = rate
        else:
            dates.insert(index, when)
            rates.insert(index, rate)
        self._cache.clear()
        return rate

    def add_many(self, rates: Iterable[Rate]) -> None:
        for rate in rates:
            self.add(rate.base, rate.quote, rate.date, rate.value, rate.source)

    def remove_pair(self, base: str, quote: str) -> int:
        key = (base.upper(), quote.upper())
        removed = len(self._rates.pop(key, []))
        self._dates.pop(key, None)
        self._cache.clear()
        return removed

    # -- lookup -------------------------------------------------------------
    def _direct(self, base: str, quote: str, on_date: date) -> Decimal | None:
        key = (base, quote)
        dates = self._dates.get(key)
        if not dates:
            return None
        index = bisect.bisect_right(dates, on_date) - 1
        if index < 0:
            return None
        rate = self._rates[key][index]
        if self.max_age_days is not None and (on_date - rate.date).days > self.max_age_days:
            return None
        return rate.value

    def _direct_or_inverse(self, base: str, quote: str, on_date: date) -> Decimal | None:
        if base == quote:
            return ONE
        value = self._direct(base, quote, on_date)
        if value is not None:
            return value
        inverse = self._direct(quote, base, on_date)
        if inverse is not None:
            return ONE / inverse
        return None

    def _resolve(self, base: str, quote: str, on_date: date) -> Decimal:
        value = self._direct_or_inverse(base, quote, on_date)
        if value is not None:
            return value
        if self.pivot not in (base, quote):
            left = self._direct_or_inverse(base, self.pivot, on_date)
            right = self._direct_or_inverse(self.pivot, quote, on_date)
            if left is not None and right is not None:
                return left * right
        raise RateNotFoundError(f"no exchange rate for {base}/{quote} on {on_date.isoformat()}")

    def get_rate(self, base: str, quote: str, on_date: date | None = None) -> Decimal:
        base = base.upper()
        quote = quote.upper()
        if base == quote:
            return ONE
        when = on_date or date.today()
        # Pair-level memoisation: report generation converts thousands of
        # postings and the resolution path (direct -> inverse -> pivot) is hot.
        key = (base, quote)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        value = self._resolve(base, quote, when)
        self._cache[key] = value
        return value

    def has_rate(self, base: str, quote: str, on_date: date | None = None) -> bool:
        try:
            self.get_rate(base, quote, on_date)
        except RateNotFoundError:
            return False
        return True

    def convert(
        self,
        money: Money,
        to_currency: str,
        on_date: date | None = None,
        quantize: bool = True,
    ) -> Money:
        target = get_currency(to_currency).code
        if money.currency == target:
            return money
        rate = self.get_rate(money.currency, target, on_date)
        converted = Money(money.amount * rate, target)
        if quantize:
            exponent = Decimal(1).scaleb(-get_currency(target).decimals)
            converted = Money(converted.amount.quantize(exponent, rounding=ROUND_HALF_EVEN), target)
        return converted

    def convert_bag(
        self, bag: MoneyBag, to_currency: str, on_date: date | None = None
    ) -> Money:
        total = Money.zero(to_currency)
        for money in bag:
            total = total + self.convert(money, to_currency, on_date, quantize=False)
        return total.quantize()

    # -- inspection ---------------------------------------------------------
    def pairs(self) -> list[tuple[str, str]]:
        return sorted(self._rates)

    def history(
        self, base: str, quote: str, start: date | None = None, end: date | None = None
    ) -> list[Rate]:
        rates = self._rates.get((base.upper(), quote.upper()), [])
        return [
            r for r in rates if (start is None or r.date >= start) and (end is None or r.date <= end)
        ]

    def latest(self, base: str, quote: str) -> Rate | None:
        rates = self._rates.get((base.upper(), quote.upper()))
        return rates[-1] if rates else None

    def __iter__(self) -> Iterator[Rate]:
        for key in sorted(self._rates):
            yield from self._rates[key]

    def __len__(self) -> int:
        return sum(len(v) for v in self._rates.values())

    # -- persistence --------------------------------------------------------
    def to_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["date", "base", "quote", "rate", "source"])
        for rate in self:
            writer.writerow(rate.to_row())
        return buffer.getvalue()

    @classmethod
    def from_csv(cls, text: str, pivot: str = "USD") -> RateTable:
        table = cls(pivot=pivot)
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            table.add(
                row["base"],
                row["quote"],
                parse_date(row["date"]),
                row["rate"],
                row.get("source") or "csv",
            )
        return table

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_csv(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, pivot: str = "USD") -> RateTable:
        return cls.from_csv(Path(path).read_text(encoding="utf-8"), pivot=pivot)
