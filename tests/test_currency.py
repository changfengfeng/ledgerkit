from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ledgerkit.currency import Rate, RateTable
from ledgerkit.errors import LedgerError
from ledgerkit.money import Money, MoneyBag


@pytest.fixture
def rates():
    table = RateTable(pivot="USD")
    table.add("EUR", "USD", date(2024, 1, 1), "1.10")
    table.add("GBP", "USD", date(2024, 1, 1), "1.25")
    table.add("USD", "JPY", date(2024, 1, 1), "150")
    return table


def test_identity_rate(rates):
    assert rates.get_rate("USD", "USD") == Decimal("1")


def test_direct_rate(rates):
    assert rates.get_rate("EUR", "USD", date(2024, 3, 1)) == Decimal("1.10")


def test_inverse_rate(rates):
    rate = RateTable(pivot="USD")
    rate.add("EUR", "USD", date(2024, 1, 1), "1.25")
    assert rate.get_rate("USD", "EUR", date(2024, 2, 1)) == Decimal("0.8")


def test_triangulation_through_pivot(rates):
    value = rates.get_rate("EUR", "GBP", date(2024, 2, 1))
    assert value.quantize(Decimal("0.0001")) == Decimal("0.8800")


def test_triangulation_to_jpy(rates):
    assert rates.get_rate("EUR", "JPY", date(2024, 2, 1)) == Decimal("165.0")


def test_missing_rate(rates):
    with pytest.raises(LedgerError):
        rates.get_rate("CHF", "USD", date(2024, 1, 2))


def test_rate_before_first_entry(rates):
    with pytest.raises(LedgerError):
        rates.get_rate("EUR", "USD", date(2023, 12, 31))


def test_has_rate(rates):
    assert rates.has_rate("EUR", "USD", date(2024, 1, 2))
    assert not rates.has_rate("CHF", "USD", date(2024, 1, 2))


def test_convert_quantizes(rates):
    assert rates.convert(Money("10", "EUR"), "USD", date(2024, 1, 2)) == Money("11.00", "USD")


def test_convert_unquantized(rates):
    result = rates.convert(Money("1", "USD"), "EUR", date(2024, 1, 2), quantize=False)
    assert result.amount.quantize(Decimal("0.000001")) == Decimal("0.909091")


def test_convert_same_currency(rates):
    m = Money("3.33", "USD")
    assert rates.convert(m, "USD") == m


def test_convert_bag(rates):
    bag = MoneyBag.of(Money("10", "EUR"), Money("4", "GBP"), Money("1", "USD"))
    assert rates.convert_bag(bag, "USD", date(2024, 1, 2)) == Money("17.00", "USD")


def test_non_positive_rate_rejected():
    table = RateTable()
    with pytest.raises(ValueError):
        table.add("EUR", "USD", date(2024, 1, 1), "0")


def test_same_currency_rate_rejected():
    with pytest.raises(ValueError):
        RateTable().add("USD", "USD", date(2024, 1, 1), "1")


def test_pairs_and_history(rates):
    rates.add("EUR", "USD", date(2024, 2, 1), "1.08")
    assert ("EUR", "USD") in rates.pairs()
    history = rates.history("EUR", "USD")
    assert [r.value for r in history] == [Decimal("1.10"), Decimal("1.08")]
    assert rates.latest("EUR", "USD").value == Decimal("1.08")


def test_latest_missing(rates):
    assert rates.latest("CHF", "USD") is None


def test_remove_pair(rates):
    assert rates.remove_pair("GBP", "USD") == 1
    assert not rates.has_rate("GBP", "USD", date(2024, 1, 2))


def test_add_many_and_len(rates):
    rates.add_many([Rate("CHF", "USD", date(2024, 1, 1), Decimal("1.12"))])
    assert rates.has_rate("CHF", "USD", date(2024, 1, 1))
    assert len(rates) == 4


def test_rate_inverse():
    rate = Rate("EUR", "USD", date(2024, 1, 1), Decimal("1.25"))
    inv = rate.inverse()
    assert (inv.base, inv.quote, inv.value) == ("USD", "EUR", Decimal("0.8"))


def test_csv_roundtrip(rates, tmp_path):
    path = tmp_path / "rates.csv"
    rates.save(path)
    loaded = RateTable.load(path)
    assert loaded.get_rate("GBP", "USD", date(2024, 1, 1)) == Decimal("1.25")
    assert len(loaded) == len(rates)


def test_from_csv_rejects_bad_rows():
    with pytest.raises(LedgerError):
        RateTable.from_csv("base,quote,date,value\nEUR,USD,notadate,1.1\n")
