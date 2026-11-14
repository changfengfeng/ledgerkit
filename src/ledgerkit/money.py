"""Exact decimal money arithmetic.

:class:`Money` wraps a :class:`decimal.Decimal` amount together with an ISO-4217
currency code.  Amounts are *not* quantized on construction: prices, allocations
and conversions may carry more precision than the currency's minor unit, and
rounding is always an explicit operation (:meth:`Money.quantize`).

:class:`MoneyBag` is a small multi-currency inventory used for balances and
transaction residuals.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import CurrencyMismatchError, InvalidAmountError, UnknownCurrencyError

Number = int | str | Decimal | float

ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True)
class Currency:
    code: str
    decimals: int
    symbol: str = ""
    name: str = ""

    @property
    def minor_unit(self) -> Decimal:
        return Decimal(1).scaleb(-self.decimals)


_REGISTRY: dict[str, Currency] = {}


def register_currency(code: str, decimals: int = 2, symbol: str = "", name: str = "") -> Currency:
    """Register (or replace) a currency definition.

    Custom commodities such as stock tickers can be registered too; they usually
    use a higher number of decimals.
    """
    code = code.upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_.'-]{0,23}", code):
        raise UnknownCurrencyError(f"invalid currency code: {code!r}")
    if decimals < 0 or decimals > 18:
        raise UnknownCurrencyError(f"invalid number of decimals for {code}: {decimals}")
    currency = Currency(code=code, decimals=decimals, symbol=symbol, name=name)
    _REGISTRY[code] = currency
    return currency


def get_currency(code: str) -> Currency:
    try:
        return _REGISTRY[code.upper()]
    except KeyError:
        raise UnknownCurrencyError(f"unknown currency: {code!r}") from None


def is_known_currency(code: str) -> bool:
    return code.upper() in _REGISTRY


def known_currencies() -> list[Currency]:
    return sorted(_REGISTRY.values(), key=lambda c: c.code)


for _code, _dec, _sym, _name in (
    ("USD", 2, "$", "US Dollar"),
    ("EUR", 2, "€", "Euro"),
    ("GBP", 2, "£", "Pound Sterling"),
    ("JPY", 0, "¥", "Japanese Yen"),
    ("CNY", 2, "¥", "Chinese Yuan"),
    ("CHF", 2, "", "Swiss Franc"),
    ("CAD", 2, "", "Canadian Dollar"),
    ("AUD", 2, "", "Australian Dollar"),
    ("NZD", 2, "", "New Zealand Dollar"),
    ("HKD", 2, "", "Hong Kong Dollar"),
    ("SGD", 2, "", "Singapore Dollar"),
    ("SEK", 2, "", "Swedish Krona"),
    ("NOK", 2, "", "Norwegian Krone"),
    ("DKK", 2, "", "Danish Krone"),
    ("KRW", 0, "₩", "South Korean Won"),
    ("INR", 2, "₹", "Indian Rupee"),
    ("BRL", 2, "", "Brazilian Real"),
    ("MXN", 2, "", "Mexican Peso"),
    ("KWD", 3, "", "Kuwaiti Dinar"),
    ("BHD", 3, "", "Bahraini Dinar"),
    ("BTC", 8, "₿", "Bitcoin"),
):
    register_currency(_code, _dec, _sym, _name)

_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₩": "KRW", "₹": "INR", "₿": "BTC"}

_NUMBER_CLEAN_RE = re.compile(r"[\s_,']")


def to_decimal(value: Number) -> Decimal:
    """Convert user input into a Decimal without going through binary floats.

    Accepts ints, Decimals, floats (converted through ``repr`` so ``0.1`` stays
    ``0.1``) and strings.  Strings may contain thousands separators (``1,234.50``),
    a leading ``+``/``-`` or accounting-style parentheses (``(12.00)``).
    """
    if isinstance(value, bool):
        raise InvalidAmountError("booleans are not valid amounts")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        result = Decimal(repr(value))
    elif isinstance(value, str):
        text = value.strip()
        negative = False
        if text.startswith("(") and text.endswith(")"):
            negative = True
            text = text[1:-1].strip()
        text = _NUMBER_CLEAN_RE.sub("", text)
        if not text:
            raise InvalidAmountError(f"empty amount: {value!r}")
        try:
            result = Decimal(text)
        except InvalidOperation:
            raise InvalidAmountError(f"invalid amount: {value!r}") from None
        if negative:
            result = -result
    else:
        raise InvalidAmountError(f"unsupported amount type: {type(value).__name__}")
    if not result.is_finite():
        raise InvalidAmountError(f"amount must be finite: {value!r}")
    return result


ROUNDING_MODES = {
    "half_even": ROUND_HALF_EVEN,
    "half_up": ROUND_HALF_UP,
    "down": ROUND_DOWN,
}


@dataclass(frozen=True, order=False)
class Money:
    amount: Decimal
    currency: str

    def __init__(self, amount: Number, currency: str) -> None:
        code = currency.upper().strip()
        get_currency(code)
        object.__setattr__(self, "amount", to_decimal(amount))
        object.__setattr__(self, "currency", code)

    # -- constructors -----------------------------------------------------
    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(ZERO, currency)

    @classmethod
    def parse(cls, text: str, default_currency: str | None = None) -> Money:
        """Parse ``"12.50 USD"``, ``"USD 12.50"``, ``"$12.50"`` or ``"-$3"``."""
        raw = text.strip()
        if not raw:
            raise InvalidAmountError("empty money string")
        parts = raw.split()
        if len(parts) == 2:
            first, second = parts
            if is_known_currency(second) and not is_known_currency(first):
                return cls(first, second)
            if is_known_currency(first):
                return cls(second, first)
            raise InvalidAmountError(f"cannot parse money: {text!r}")
        if len(parts) != 1:
            raise InvalidAmountError(f"cannot parse money: {text!r}")
        token = parts[0]
        sign = ""
        if token[:1] in "+-":
            sign, token = token[0], token[1:]
        if token[:1] in _SYMBOLS:
            return cls(sign + token[1:], _SYMBOLS[token[0]])
        if default_currency is None:
            raise InvalidAmountError(f"money string without currency: {text!r}")
        return cls(sign + token, default_currency)

    # -- properties -------------------------------------------------------
    @property
    def currency_info(self) -> Currency:
        return get_currency(self.currency)

    def is_zero(self) -> bool:
        return self.amount == 0

    def is_positive(self) -> bool:
        return self.amount > 0

    def is_negative(self) -> bool:
        return self.amount < 0

    def __bool__(self) -> bool:
        return not self.is_zero()

    # -- arithmetic -------------------------------------------------------
    def _check(self, other: Money, operation: str) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"cannot {operation} Money and {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatchError(self.currency, other.currency, operation)

    def __add__(self, other: object) -> Money:
        if isinstance(other, int) and other == 0:
            return self
        if not isinstance(other, Money):
            return NotImplemented
        self._check(other, "add")
        return Money(self.amount + other.amount, self.currency)

    def __radd__(self, other: object) -> Money:
        # Allows sum([...]) which starts with int 0.
        return self.__add__(other)

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._check(other, "subtract")
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __pos__(self) -> Money:
        return self

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    def __mul__(self, factor: object) -> Money:
        if isinstance(factor, Money):
            raise TypeError("cannot multiply Money by Money")
        if not isinstance(factor, (int, Decimal, str, float)) or isinstance(factor, bool):
            return NotImplemented
        return Money(self.amount * to_decimal(factor), self.currency)

    __rmul__ = __mul__

    def __truediv__(self, divisor: object) -> Money:
        if isinstance(divisor, Money):
            raise TypeError("use Money.ratio() to divide Money by Money")
        if not isinstance(divisor, (int, Decimal, str, float)) or isinstance(divisor, bool):
            return NotImplemented
        value = to_decimal(divisor)
        if value == 0:
            raise ZeroDivisionError("division of Money by zero")
        return Money(self.amount / value, self.currency)

    def ratio(self, other: Money) -> Decimal:
        self._check(other, "divide")
        if other.amount == 0:
            raise ZeroDivisionError("ratio against zero amount")
        return self.amount / other.amount

    # -- comparison -------------------------------------------------------
    def _cmp_value(self, other: object, op: str) -> Decimal:
        if not isinstance(other, Money):
            raise TypeError(f"cannot compare Money with {type(other).__name__}")
        self._check(other, op)
        return other.amount

    def __lt__(self, other: object) -> bool:
        return self.amount < self._cmp_value(other, "compare")

    def __le__(self, other: object) -> bool:
        return self.amount <= self._cmp_value(other, "compare")

    def __gt__(self, other: object) -> bool:
        return self.amount > self._cmp_value(other, "compare")

    def __ge__(self, other: object) -> bool:
        return self.amount >= self._cmp_value(other, "compare")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.currency == other.currency and self.amount == other.amount

    def __hash__(self) -> int:
        return hash((self.currency, self.amount.normalize() if self.amount else ZERO))

    # -- rounding / allocation ---------------------------------------------
    def quantize(self, decimals: int | None = None, rounding: str = "half_even") -> Money:
        places = self.currency_info.decimals if decimals is None else decimals
        try:
            mode = ROUNDING_MODES[rounding]
        except KeyError:
            raise ValueError(f"unknown rounding mode: {rounding}") from None
        exponent = Decimal(1).scaleb(-places)
        return Money(self.amount.quantize(exponent, rounding=mode), self.currency)

    def is_quantized(self) -> bool:
        return self.quantize().amount == self.amount

    def allocate(self, ratios: Iterable[Number]) -> list[Money]:
        """Split the amount proportionally to ``ratios`` without losing minor units.

        The result always sums to the (quantized) original amount.  Leftover
        minor units are distributed to the parts with the largest fractional
        remainders, ties broken by position.
        """
        weights = [to_decimal(r) for r in ratios]
        if not weights:
            raise ValueError("allocate() requires at least one ratio")
        if any(w < 0 for w in weights):
            raise ValueError("ratios must be non-negative")
        total_weight = sum(weights, ZERO)
        if total_weight == 0:
            raise ValueError("ratios must not all be zero")
        unit = self.currency_info.minor_unit
        total_units = int((self.quantize().amount / unit).to_integral_value())
        sign = -1 if total_units < 0 else 1
        total_units = abs(total_units)
        exact = [Decimal(total_units) * w / total_weight for w in weights]
        floors = [int(e.to_integral_value(rounding=ROUND_DOWN)) for e in exact]
        remainder = total_units - sum(floors)
        order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - floors[i]), i))
        for i in order[:remainder]:
            floors[i] += 1
        return [Money(Decimal(sign * units) * unit, self.currency) for units in floors]

    def split(self, parts: int) -> list[Money]:
        if parts <= 0:
            raise ValueError("parts must be positive")
        return self.allocate([1] * parts)

    # -- formatting -------------------------------------------------------
    def format(self, symbol: bool = False, thousands: bool = True, decimals: int | None = None) -> str:
        info = self.currency_info
        places = info.decimals if decimals is None else decimals
        value = self.amount.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
        body = f"{abs(value):,.{places}f}" if thousands else f"{abs(value):.{places}f}"
        sign = "-" if value < 0 else ""
        if symbol and info.symbol:
            return f"{sign}{info.symbol}{body}"
        return f"{sign}{body} {self.currency}"

    def __str__(self) -> str:
        return self.format(thousands=False)

    def __repr__(self) -> str:
        return f"Money('{self.amount}', '{self.currency}')"

    def to_dict(self) -> dict[str, str]:
        return {"amount": str(self.amount), "currency": self.currency}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Money:
        try:
            return cls(str(data["amount"]), str(data["currency"]))
        except KeyError as exc:
            raise InvalidAmountError(f"missing money field: {exc.args[0]}") from None


class MoneyBag:
    """A mapping of currency -> Decimal that behaves like a multi-currency amount."""

    __slots__ = ("_amounts",)

    def __init__(self, items: Iterable[Money] | None = None) -> None:
        self._amounts: dict[str, Decimal] = {}
        for item in items or ():
            self.add(item)

    @classmethod
    def of(cls, *items: Money) -> MoneyBag:
        return cls(items)

    def copy(self) -> MoneyBag:
        bag = MoneyBag()
        bag._amounts = dict(self._amounts)
        return bag

    def add(self, money: Money) -> MoneyBag:
        current = self._amounts.get(money.currency, ZERO) + money.amount
        if current == 0:
            self._amounts.pop(money.currency, None)
        else:
            self._amounts[money.currency] = current
        return self

    def subtract(self, money: Money) -> MoneyBag:
        return self.add(-money)

    def merge(self, other: MoneyBag) -> MoneyBag:
        for money in other:
            self.add(money)
        return self

    def __add__(self, other: object) -> MoneyBag:
        result = self.copy()
        if isinstance(other, Money):
            return result.add(other)
        if isinstance(other, MoneyBag):
            return result.merge(other)
        return NotImplemented

    def __sub__(self, other: object) -> MoneyBag:
        if isinstance(other, Money):
            return self.copy().subtract(other)
        if isinstance(other, MoneyBag):
            return self.copy().merge(-other)
        return NotImplemented

    def __neg__(self) -> MoneyBag:
        bag = MoneyBag()
        bag._amounts = {k: -v for k, v in self._amounts.items()}
        return bag

    def get(self, currency: str) -> Money:
        code = currency.upper()
        return Money(self._amounts.get(code, ZERO), code)

    def currencies(self) -> list[str]:
        return sorted(self._amounts)

    def is_zero(self, tolerance: Mapping[str, Decimal] | None = None) -> bool:
        for code, value in self._amounts.items():
            limit = (tolerance or {}).get(code, ZERO)
            if abs(value) > limit:
                return False
        return True

    def __bool__(self) -> bool:
        return not self.is_zero()

    def __iter__(self) -> Iterator[Money]:
        for code in sorted(self._amounts):
            yield Money(self._amounts[code], code)

    def __len__(self) -> int:
        return len(self._amounts)

    def __contains__(self, currency: object) -> bool:
        return isinstance(currency, str) and currency.upper() in self._amounts

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Money):
            other = MoneyBag([other])
        if not isinstance(other, MoneyBag):
            return NotImplemented
        return self._amounts == other._amounts

    def __repr__(self) -> str:
        inner = ", ".join(str(m) for m in self)
        return f"MoneyBag([{inner}])"

    def __str__(self) -> str:
        return ", ".join(m.format() for m in self) or "0"

    def quantize(self) -> MoneyBag:
        return MoneyBag(m.quantize() for m in self)

    def single(self) -> Money:
        """Return the only Money in the bag (zero bag raises)."""
        if len(self._amounts) != 1:
            raise CurrencyMismatchError(
                ",".join(self.currencies()) or "<empty>", "<single>", "collapse"
            )
        return next(iter(self))

    def to_dict(self) -> dict[str, str]:
        return {code: str(value) for code, value in sorted(self._amounts.items())}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> MoneyBag:
        return cls(Money(str(v), k) for k, v in data.items())


def sum_money(items: Iterable[Money], currency: str) -> Money:
    total = Money.zero(currency)
    for item in items:
        total = total + item
    return total
