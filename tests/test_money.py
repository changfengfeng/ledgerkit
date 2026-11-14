from __future__ import annotations

from decimal import Decimal

import pytest

from ledgerkit.errors import CurrencyMismatchError, LedgerError
from ledgerkit.money import (
    Money,
    MoneyBag,
    get_currency,
    is_known_currency,
    known_currencies,
    register_currency,
    sum_money,
    to_decimal,
)


class TestDecimalConversion:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("1", Decimal("1")),
            ("1.50", Decimal("1.50")),
            (" 2.25 ", Decimal("2.25")),
            (3, Decimal("3")),
            (Decimal("4.1"), Decimal("4.1")),
            ("1,234.56", Decimal("1234.56")),
        ],
    )
    def test_to_decimal(self, value, expected):
        assert to_decimal(value) == expected

    @pytest.mark.parametrize("value", ["abc", "", "1.2.3"])
    def test_to_decimal_rejects_garbage(self, value):
        with pytest.raises(LedgerError):
            to_decimal(value)


class TestCurrencyRegistry:
    def test_builtin_currencies(self):
        assert get_currency("USD").decimals == 2
        assert get_currency("JPY").decimals == 0
        assert get_currency("KWD").decimals == 3

    def test_lookup_is_case_insensitive(self):
        assert get_currency("usd").code == "USD"

    def test_unknown_currency(self):
        assert not is_known_currency("ZZZ")

    def test_register_custom(self):
        cur = register_currency("PTS", decimals=0, name="Points")
        assert cur.code == "PTS"
        assert is_known_currency("PTS")
        assert get_currency("PTS").minor_unit == Decimal("1")

    def test_minor_unit(self):
        assert get_currency("USD").minor_unit == Decimal("0.01")

    def test_known_currencies_sorted(self):
        codes = [c.code for c in known_currencies()]
        assert "EUR" in codes and "USD" in codes


class TestMoneyBasics:
    def test_construction(self):
        m = Money("10.50", "usd")
        assert m.amount == Decimal("10.50")
        assert m.currency == "USD"

    def test_zero(self):
        z = Money.zero("EUR")
        assert z.is_zero()
        assert not z.is_positive()
        assert not z.is_negative()

    def test_sign_predicates(self):
        assert Money("1", "USD").is_positive()
        assert Money("-1", "USD").is_negative()

    @pytest.mark.parametrize(
        "text, amount, currency",
        [
            ("10 USD", "10", "USD"),
            ("-3.25 EUR", "-3.25", "EUR"),
            ("USD 7.10", "7.10", "USD"),
            ("1,000.00 GBP", "1000.00", "GBP"),
        ],
    )
    def test_parse(self, text, amount, currency):
        m = Money.parse(text)
        assert m.amount == Decimal(amount)
        assert m.currency == currency

    def test_parse_default_currency(self):
        assert Money.parse("12.5", default_currency="CHF") == Money("12.5", "CHF")

    def test_parse_without_currency_fails(self):
        with pytest.raises(LedgerError):
            Money.parse("12.5")

    def test_equality_and_hash(self):
        assert Money("1.0", "USD") == Money("1.00", "USD")
        assert len({Money("1.0", "USD"), Money("1.00", "USD")}) == 1
        assert Money("1", "USD") != Money("1", "EUR")

    def test_ordering(self):
        assert Money("1", "USD") < Money("2", "USD")
        assert sorted([Money("3", "USD"), Money("-1", "USD")])[0] == Money("-1", "USD")

    def test_ordering_requires_same_currency(self):
        with pytest.raises(CurrencyMismatchError):
            _ = Money("1", "USD") < Money("2", "EUR")


class TestMoneyArithmetic:
    def test_add_sub(self):
        assert Money("1.25", "USD") + Money("2.50", "USD") == Money("3.75", "USD")
        assert Money("5", "USD") - Money("7", "USD") == Money("-2", "USD")

    def test_neg_abs(self):
        assert -Money("4", "USD") == Money("-4", "USD")
        assert abs(Money("-4", "USD")) == Money("4", "USD")

    def test_mismatch(self):
        with pytest.raises(CurrencyMismatchError):
            Money("1", "USD") + Money("1", "EUR")

    def test_multiply(self):
        assert Money("2.50", "USD") * 3 == Money("7.50", "USD")
        assert 2 * Money("2.50", "USD") == Money("5.00", "USD")

    def test_divide_by_scalar(self):
        assert Money("9", "USD") / 3 == Money("3", "USD")

    def test_ratio(self):
        assert Money("5", "USD").ratio(Money("20", "USD")) == Decimal("0.25")

    def test_sum_builtin(self):
        assert sum([Money("1", "USD"), Money("2", "USD")], Money.zero("USD")) == Money("3", "USD")

    def test_sum_money_helper(self):
        assert sum_money([Money("1.10", "EUR"), Money("2.20", "EUR")], "EUR") == Money("3.30", "EUR")

    def test_sum_money_empty(self):
        assert sum_money([], "USD").is_zero()


class TestRounding:
    @pytest.mark.parametrize(
        "amount, expected",
        [("1.005", "1.00"), ("1.015", "1.02"), ("1.025", "1.02"), ("2.675", "2.68"), ("-1.015", "-1.02")],
    )
    def test_bankers_rounding(self, amount, expected):
        assert Money(amount, "USD").quantize().amount == Decimal(expected)

    def test_half_up(self):
        assert Money("1.005", "USD").quantize(rounding="half_up").amount == Decimal("1.01")

    def test_jpy_no_decimals(self):
        assert Money("1234.6", "JPY").quantize().amount == Decimal("1235")

    def test_explicit_decimals(self):
        assert Money("1.23456", "USD").quantize(4).amount == Decimal("1.2346")

    def test_is_quantized(self):
        assert Money("1.20", "USD").is_quantized()
        assert not Money("1.205", "USD").is_quantized()


class TestAllocation:
    def test_split_even(self):
        parts = Money("10.00", "USD").split(3)
        assert [p.amount for p in parts] == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]
        assert sum_money(parts, "USD") == Money("10.00", "USD")

    def test_allocate_ratios(self):
        parts = Money("100", "USD").allocate([1, 1, 2])
        assert sum_money(parts, "USD") == Money("100", "USD")
        assert parts[2].amount == Decimal("50.00")

    def test_allocate_negative(self):
        parts = Money("-0.05", "USD").allocate([1, 1])
        assert sum_money(parts, "USD") == Money("-0.05", "USD")

    def test_allocate_requires_positive_total(self):
        with pytest.raises(ValueError):
            Money("1", "USD").allocate([0, 0])

    def test_split_requires_parts(self):
        with pytest.raises(ValueError):
            Money("1", "USD").split(0)


class TestFormatting:
    def test_default(self):
        assert Money("1234.5", "USD").format() == "1,234.50 USD"

    def test_no_thousands(self):
        assert Money("1234.5", "USD").format(thousands=False) == "1234.50 USD"

    def test_symbol(self):
        assert "$" in Money("3", "USD").format(symbol=True)

    def test_str(self):
        assert str(Money("-2", "EUR")) == "-2.00 EUR"

    def test_dict_roundtrip(self):
        m = Money("12.34", "GBP")
        assert Money.from_dict(m.to_dict()) == m


class TestMoneyBag:
    def test_add_and_get(self):
        bag = MoneyBag.of(Money("1", "USD"), Money("2", "EUR"), Money("3", "USD"))
        assert bag.get("USD") == Money("4", "USD")
        assert bag.get("EUR") == Money("2", "EUR")
        assert bag.get("GBP").is_zero()

    def test_currencies_sorted(self):
        bag = MoneyBag.of(Money("1", "USD"), Money("2", "EUR"))
        assert bag.currencies() == ["EUR", "USD"]

    def test_zero_entries_dropped(self):
        bag = MoneyBag.of(Money("1", "USD"), Money("-1", "USD"))
        assert bag.is_zero()
        assert bag.currencies() == []

    def test_tolerance(self):
        bag = MoneyBag.of(Money("0.004", "USD"))
        assert not bag.is_zero()
        assert bag.is_zero({"USD": Decimal("0.005")})

    def test_merge_and_negate(self):
        a = MoneyBag.of(Money("1", "USD"))
        b = MoneyBag.of(Money("2", "USD"), Money("5", "EUR"))
        merged = a.merge(b)
        assert merged.get("USD") == Money("3", "USD")
        assert (-merged).get("EUR") == Money("-5", "EUR")

    def test_subtract(self):
        bag = MoneyBag.of(Money("10", "USD")).subtract(Money("4", "USD"))
        assert bag.get("USD") == Money("6", "USD")

    def test_copy_is_independent(self):
        bag = MoneyBag.of(Money("1", "USD"))
        other = bag.copy()
        other.add(Money("1", "USD"))
        assert bag.get("USD") == Money("1", "USD")

    def test_single(self):
        assert MoneyBag.of(Money("7", "USD")).single() == Money("7", "USD")
        with pytest.raises(LedgerError):
            MoneyBag.of(Money("1", "USD"), Money("1", "EUR")).single()

    def test_dict_roundtrip(self):
        bag = MoneyBag.of(Money("1.5", "USD"), Money("-2", "EUR"))
        assert MoneyBag.from_dict(bag.to_dict()) == bag

    def test_quantize(self):
        bag = MoneyBag.of(Money("1.005", "USD")).quantize()
        assert bag.get("USD").amount == Decimal("1.00")

    def test_str_empty(self):
        assert str(MoneyBag()) in ("0", "")
