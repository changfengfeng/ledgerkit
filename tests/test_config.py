from __future__ import annotations

import pytest

from ledgerkit.config import Settings
from ledgerkit.errors import LedgerError


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings.load(environ={})
    assert settings.currency == "USD"
    assert settings.port == 8765
    assert settings.ledger_file is None


def test_ini_file(tmp_path):
    path = tmp_path / "ledgerkit.ini"
    path.write_text(
        "[ledgerkit]\nledger_file = books.ledger\ncurrency = eur\nfiscal_year_start = 4\ncustom = x\n"
        "[server]\nport = 9000\nauto_open = yes\n",
        encoding="utf-8",
    )
    settings = Settings.load(path, environ={})
    assert settings.ledger_file == "books.ledger"
    assert settings.currency == "EUR"
    assert settings.fiscal_year_start == 4
    assert settings.port == 9000
    assert settings.auto_open is True
    assert settings.extra == {"custom": "x"}


def test_environment_overrides_file(tmp_path):
    path = tmp_path / "ledgerkit.ini"
    path.write_text("[ledgerkit]\ncurrency = EUR\n", encoding="utf-8")
    settings = Settings.load(path, environ={"LEDGERKIT_CURRENCY": "gbp", "LEDGERKIT_PORT": "7000", "OTHER": "1"})
    assert settings.currency == "GBP"
    assert settings.port == 7000


def test_missing_explicit_file(tmp_path):
    with pytest.raises(LedgerError):
        Settings.load(tmp_path / "nope.ini", environ={})


def test_bad_integer(tmp_path):
    with pytest.raises(LedgerError):
        Settings.load(environ={"LEDGERKIT_PORT": "eighty"})


@pytest.mark.parametrize("field, value", [("fiscal_year_start", 0), ("fiscal_year_start", 13), ("port", 70000)])
def test_validate(field, value):
    settings = Settings()
    setattr(settings, field, value)
    with pytest.raises(LedgerError):
        settings.validate()
