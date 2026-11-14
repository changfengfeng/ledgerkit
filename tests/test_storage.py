from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ledgerkit.balances import balance
from ledgerkit.errors import LedgerError
from ledgerkit.money import Money
from ledgerkit.storage import JsonlStore, SqliteStore, open_repository
from ledgerkit.transactions import TransactionBuilder


def same(a, b):
    assert len(a) == len(b)
    for name in ("Assets", "Liabilities", "Income", "Expenses", "Equity"):
        assert balance(a, name) == balance(b, name)


class TestOpenRepository:
    def test_by_extension(self, tmp_path):
        assert isinstance(open_repository(tmp_path / "x.db"), SqliteStore)
        assert isinstance(open_repository(tmp_path / "x.jsonl"), JsonlStore)

    def test_unknown_extension(self, tmp_path):
        with pytest.raises(LedgerError):
            open_repository(tmp_path / "x.txt")


class TestSqlite:
    def test_roundtrip(self, populated, tmp_path):
        path = tmp_path / "books.db"
        with SqliteStore(path) as store:
            store.save(populated)
        with SqliteStore(path) as store:
            assert store.exists()
            loaded = store.load()
        same(loaded, populated)
        assert loaded.chart.names() == populated.chart.names()

    def test_counts(self, populated):
        store = SqliteStore(":memory:")
        store.save(populated)
        assert store.count("transactions") == 8
        assert store.count("postings") == 16
        with pytest.raises(LedgerError):
            store.count("sqlite_master")

    def test_account_totals(self, populated):
        store = SqliteStore(":memory:")
        store.save(populated)
        totals = store.account_totals()
        assert totals[("Expenses:Rent", "USD")] == 2400

    def test_save_replaces(self, populated):
        store = SqliteStore(":memory:")
        store.save(populated)
        store.save(populated)
        assert store.count("transactions") == 8

    def test_preserves_metadata(self, journal):
        tx = TransactionBuilder(date(2024, 1, 2), "Shop", "n").tag("a").link("b").meta(ref="1").debit("Expenses:Food", "5", "USD").credit("Assets:Cash", "5", "USD").build()
        journal.add(tx)
        journal.lock_through(date(2024, 1, 1))
        journal.options["title"] = "Books"
        store = SqliteStore(":memory:")
        store.save(journal)
        loaded = store.load()
        again = loaded.get(tx.id)
        assert again.tags == {"a"}
        assert again.links == {"b"}
        assert again.meta == {"ref": "1"}
        assert loaded.locked_through == date(2024, 1, 1)
        assert loaded.options["title"] == "Books"

    def test_rates_roundtrip(self, journal):
        journal.rates.add("EUR", "USD", date(2024, 1, 1), "1.10")
        journal.rates.add("GBP", "USD", date(2024, 1, 1), "1.25")
        store = SqliteStore(":memory:")
        store.save(journal)
        loaded = store.load()
        assert len(loaded.rates) == 2
        assert str(loaded.rates.get_rate("EUR", "USD", date(2024, 1, 1))) == "1.10"

    def test_attach(self, journal, tmp_path):
        store = SqliteStore(tmp_path / "live.db")
        store.save(journal)
        store.attach(journal)
        tx = journal.add(TransactionBuilder(date(2024, 1, 2)).debit("Expenses:Food", "8", "USD").credit("Assets:Cash", "8", "USD").build())
        assert store.count("transactions") == 1
        journal.remove(tx.id)
        assert store.count("transactions") == 0

    def test_exists_false_for_new_file(self, tmp_path):
        assert not SqliteStore(tmp_path / "missing.db").exists()


class TestJsonl:
    def test_snapshot_roundtrip(self, populated, tmp_path):
        store = JsonlStore(tmp_path / "log.jsonl")
        store.save(populated)
        same(store.load(), populated)

    def test_replay_events(self, journal, tmp_path):
        store = JsonlStore(tmp_path / "log.jsonl")
        store.save(journal)
        store.attach(journal)
        a = journal.add(TransactionBuilder(date(2024, 1, 2)).debit("Expenses:Food", "8", "USD").credit("Assets:Cash", "8", "USD").build())
        b = journal.add(TransactionBuilder(date(2024, 1, 3)).debit("Expenses:Food", "2", "USD").credit("Assets:Cash", "2", "USD").build())
        journal.remove(a.id)
        loaded = store.load()
        assert [t.id for t in loaded] == [b.id]

    def test_compact(self, populated, tmp_path):
        path = tmp_path / "log.jsonl"
        store = JsonlStore(path)
        store.save(populated)
        store.attach(populated)
        populated.add(TransactionBuilder(date(2024, 3, 1)).debit("Expenses:Food", "1", "USD").credit("Assets:Cash", "1", "USD").build())
        store.compact()
        assert len(path.read_text(encoding="utf-8").splitlines()) == 1
        assert len(store.load()) == 9

    def test_missing_log(self, tmp_path):
        with pytest.raises(LedgerError):
            JsonlStore(tmp_path / "nope.jsonl").load()

    def test_corrupt_line(self, tmp_path):
        path = tmp_path / "bad.jsonl"
        path.write_text("{not json}\n", encoding="utf-8")
        with pytest.raises(LedgerError):
            JsonlStore(path).load()


class TestSqlitePrecision:
    @pytest.fixture
    def cents(self, journal):
        journal.add(
            TransactionBuilder(date(2024, 1, 4), "Shop")
            .debit("Expenses:Food", "0.10", "USD")
            .debit("Expenses:Fees", "0.20", "USD")
            .credit("Assets:Cash", "0.30", "USD")
            .build()
        )
        return journal

    @pytest.fixture
    def fractional(self, cents):
        journal = cents
        journal.add(
            TransactionBuilder(date(2024, 1, 5), "Broker")
            .posting("Assets:Bank:Checking", "10", "EUR", price=Money("1.0912345678901234567", "USD"))
            .credit("Assets:Cash", "10.912345678901234567", "USD")
            .build()
        )
        return journal

    def test_decimal_roundtrip(self, fractional, tmp_path):
        path = tmp_path / "books.db"
        with SqliteStore(path) as store:
            store.save(fractional)
        with SqliteStore(path) as store:
            loaded = store.load()
        same(loaded, fractional)
        for original, reloaded in zip(fractional.sorted(), loaded.sorted(), strict=True):
            for a, b in zip(original.postings, reloaded.postings, strict=True):
                assert str(a.amount.amount) == str(b.amount.amount)
                assert a.price == b.price

    def test_account_totals_exact(self, fractional):
        store = SqliteStore(":memory:")
        store.save(fractional)
        assert store.account_totals()[("Assets:Cash", "USD")] == Decimal("-11.212345678901234567")

    def test_migrates_numeric_schema(self, cents, tmp_path):
        import sqlite3

        path = tmp_path / "legacy.db"
        with SqliteStore(path) as store:
            store.save(cents)
        conn = sqlite3.connect(path)
        conn.executescript(
            "ALTER TABLE postings RENAME TO p_old;"
            "CREATE TABLE postings (tx_id TEXT NOT NULL, position INTEGER NOT NULL,"
            " account TEXT NOT NULL, amount NUMERIC NOT NULL, currency TEXT NOT NULL,"
            " price_amount NUMERIC, price_currency TEXT, memo TEXT NOT NULL DEFAULT '',"
            " flag TEXT NOT NULL DEFAULT '', meta TEXT NOT NULL DEFAULT '{}',"
            " PRIMARY KEY (tx_id, position));"
            "INSERT INTO postings SELECT * FROM p_old; DROP TABLE p_old;"
            "UPDATE settings SET value = '3' WHERE key = 'schema_version';"
        )
        conn.commit()
        assert conn.execute("SELECT typeof(amount) FROM postings LIMIT 1").fetchone()[0] == "real"
        conn.close()
        with SqliteStore(path) as store:
            loaded = store.load()
            types = {r[0] for r in store.conn.execute("SELECT typeof(amount) FROM postings")}
            version = store.conn.execute(
                "SELECT value FROM settings WHERE key = 'schema_version'"
            ).fetchone()[0]
        assert types == {"text"}
        assert version == "4"
        same(loaded, cents)
        assert balance(loaded, "Expenses:Food").get("USD") == Money("0.10", "USD")
