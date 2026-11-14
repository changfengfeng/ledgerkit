"""SQLite backend.

Schema overview::

    accounts(name PK, opened, closed, currencies, description, meta)
    rates(base, quote, date, value, source)       PK(base, quote, date)
    transactions(id PK, date, flag, payee, narration, tags, links, meta,
                 voided, voided_by, reversal_of)
    postings(tx_id FK, position, account, amount, currency,
             price_amount, price_currency, memo, flag, meta)
    settings(key PK, value)
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

from ..accounts import Account, ChartOfAccounts
from ..currency import RateTable
from ..errors import LedgerError, StorageError
from ..journal import Journal
from ..money import Money
from ..transactions import Posting, Transaction
from .base import Repository

SCHEMA_VERSION = 4


def _decimal(value: str | int | float) -> Decimal:
    # Databases written before schema 4 may hold binary floats; repr() gives
    # the shortest decimal that round-trips, i.e. the value originally stored.
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value)


POSTINGS_INDEX = "CREATE INDEX IF NOT EXISTS idx_postings_account ON postings(account);\n"

POSTINGS_COLUMNS = """ (
    tx_id          TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    account        TEXT NOT NULL,
    amount         TEXT NOT NULL,
    currency       TEXT NOT NULL,
    price_amount   TEXT,
    price_currency TEXT,
    memo           TEXT NOT NULL DEFAULT '',
    flag           TEXT NOT NULL DEFAULT '',
    meta           TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (tx_id, position)
);
"""

SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    name        TEXT PRIMARY KEY,
    opened      TEXT NOT NULL,
    closed      TEXT,
    currencies  TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    meta        TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS rates (
    base   TEXT NOT NULL,
    quote  TEXT NOT NULL,
    date   TEXT NOT NULL,
    value  TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    PRIMARY KEY (base, quote, date)
);
CREATE TABLE IF NOT EXISTS transactions (
    id          TEXT PRIMARY KEY,
    date        TEXT NOT NULL,
    flag        TEXT NOT NULL DEFAULT '*',
    payee       TEXT NOT NULL DEFAULT '',
    narration   TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '',
    links       TEXT NOT NULL DEFAULT '',
    meta        TEXT NOT NULL DEFAULT '{}',
    voided      INTEGER NOT NULL DEFAULT 0,
    voided_by   TEXT,
    reversal_of TEXT
);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE TABLE IF NOT EXISTS postings"""
    + POSTINGS_COLUMNS
    + POSTINGS_INDEX
)


class SqliteStore(Repository):
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # -- connection -----------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(self.path, check_same_thread=False)
            except sqlite3.Error as exc:
                raise StorageError(f"cannot open database {self.path}: {exc}") from None
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._migrate()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self.conn
            try:
                conn.execute("BEGIN")
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _migrate(self) -> None:
        assert self._conn is not None
        self._conn.isolation_level = None  # explicit transaction control
        self._conn.executescript(SCHEMA)
        row = self._conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
        elif int(row["value"]) > SCHEMA_VERSION:
            raise StorageError(
                f"database schema version {row['value']} is newer than supported ({SCHEMA_VERSION})"
            )
        elif int(row["value"]) < 4:
            self._migrate_postings_to_text()

    def _migrate_postings_to_text(self) -> None:
        """Schema 4: store posting amounts as TEXT instead of NUMERIC.

        NUMERIC affinity turned decimal strings into binary floats; existing
        values are converted back to their shortest decimal representation.
        """
        assert self._conn is not None
        conn = self._conn
        conn.execute("BEGIN")
        try:
            rows = conn.execute("SELECT * FROM postings").fetchall()
            conn.execute("CREATE TABLE postings_v4" + POSTINGS_COLUMNS)
            conn.executemany(
                "INSERT INTO postings_v4(tx_id, position, account, amount, currency, price_amount,"
                " price_currency, memo, flag, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r["tx_id"],
                        r["position"],
                        r["account"],
                        str(_decimal(r["amount"])),
                        r["currency"],
                        None if r["price_amount"] is None else str(_decimal(r["price_amount"])),
                        r["price_currency"],
                        r["memo"],
                        r["flag"],
                        r["meta"],
                    )
                    for r in rows
                ],
            )
            conn.execute("DROP TABLE postings")
            conn.execute("ALTER TABLE postings_v4 RENAME TO postings")
            conn.execute(POSTINGS_INDEX)
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),)
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def exists(self) -> bool:
        if self.path == ":memory:":
            return self._conn is not None
        if not Path(self.path).exists():
            return False
        row = self.conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()
        return bool(row["n"])

    # -- writing --------------------------------------------------------------
    def _write_account(self, conn: sqlite3.Connection, account: Account) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO accounts(name, opened, closed, currencies, description, meta)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                account.name,
                account.opened.isoformat(),
                account.closed.isoformat() if account.closed else None,
                ",".join(sorted(account.currencies)),
                account.description,
                json.dumps(account.meta, sort_keys=True),
            ),
        )

    def _write_transaction(self, conn: sqlite3.Connection, tx: Transaction) -> None:
        if tx.id is None:
            raise StorageError("cannot store a transaction without id")
        conn.execute("DELETE FROM postings WHERE tx_id = ?", (tx.id,))
        conn.execute(
            "INSERT OR REPLACE INTO transactions(id, date, flag, payee, narration, tags, links, meta,"
            " voided, voided_by, reversal_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tx.id,
                tx.date.isoformat(),
                tx.flag,
                tx.payee,
                tx.narration,
                " ".join(sorted(tx.tags)),
                " ".join(sorted(tx.links)),
                json.dumps(tx.meta, sort_keys=True),
                1 if tx.voided else 0,
                tx.voided_by,
                tx.reversal_of,
            ),
        )
        conn.executemany(
            "INSERT INTO postings(tx_id, position, account, amount, currency, price_amount,"
            " price_currency, memo, flag, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    tx.id,
                    position,
                    p.account,
                    str(p.amount.amount),
                    p.amount.currency,
                    str(p.price.amount) if p.price else None,
                    p.price.currency if p.price else None,
                    p.memo,
                    p.flag,
                    json.dumps(p.meta, sort_keys=True),
                )
                for position, p in enumerate(tx.postings)
            ],
        )

    def save(self, journal: Journal) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM postings")
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM rates")
            conn.execute("DELETE FROM accounts")
            for account in journal.chart:
                self._write_account(conn, account)
            conn.executemany(
                "INSERT INTO rates(base, quote, date, value, source) VALUES (?, ?, ?, ?, ?)",
                [(r.base, r.quote, r.date.isoformat(), str(r.value), r.source) for r in journal.rates],
            )
            for tx in journal.sorted():
                self._write_transaction(conn, tx)
            self._write_settings(conn, journal)

    def _write_settings(self, conn: sqlite3.Connection, journal: Journal) -> None:
        conn.execute("DELETE FROM settings WHERE key != 'schema_version'")
        values = {
            "options": json.dumps(journal.options, sort_keys=True),
            "pivot": journal.rates.pivot,
            "locked_through": journal.locked_through.isoformat() if journal.locked_through else "",
            "id_prefix": journal.id_prefix,
        }
        conn.executemany("INSERT INTO settings(key, value) VALUES (?, ?)", list(values.items()))

    def attach(self, journal: Journal) -> None:
        """Persist every subsequent change to ``journal`` immediately."""

        def on_event(event: str, tx: Transaction) -> None:
            with self._tx() as conn:
                for account in journal.chart:
                    self._write_account(conn, account)
                if event == "remove":
                    conn.execute("DELETE FROM transactions WHERE id = ?", (tx.id,))
                else:
                    self._write_transaction(conn, tx)

        journal.subscribe(on_event)

    # -- reading --------------------------------------------------------------
    def _settings(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def _read_chart(self) -> ChartOfAccounts:
        chart = ChartOfAccounts()
        for row in self.conn.execute("SELECT * FROM accounts ORDER BY name"):
            chart.add(
                Account(
                    name=row["name"],
                    opened=date.fromisoformat(row["opened"]),
                    closed=date.fromisoformat(row["closed"]) if row["closed"] else None,
                    currencies=frozenset(c for c in row["currencies"].split(",") if c),
                    description=row["description"],
                    meta=json.loads(row["meta"]),
                )
            )
        return chart

    def _read_rates(self, pivot: str) -> RateTable:
        table = RateTable(pivot=pivot)
        for row in self.conn.execute("SELECT * FROM rates ORDER BY base, quote, date"):
            table.add(row["base"], row["quote"], date.fromisoformat(row["date"]), Decimal(row["value"]), row["source"])
        return table

    def _read_postings(self) -> dict[str, list[Posting]]:
        postings: dict[str, list[Posting]] = {}
        for row in self.conn.execute("SELECT * FROM postings ORDER BY tx_id, position"):
            price = None
            if row["price_amount"] is not None:
                price = Money(_decimal(row["price_amount"]), row["price_currency"])
            postings.setdefault(row["tx_id"], []).append(
                Posting(
                    account=row["account"],
                    amount=Money(_decimal(row["amount"]), row["currency"]),
                    price=price,
                    memo=row["memo"],
                    flag=row["flag"],
                    meta=json.loads(row["meta"]),
                )
            )
        return postings

    def load(self) -> Journal:
        with self._lock:
            settings = self._settings()
            journal = Journal(
                chart=self._read_chart(),
                rates=self._read_rates(settings.get("pivot", "USD")),
                id_prefix=settings.get("id_prefix", "tx"),
            )
            journal.options.update(json.loads(settings.get("options", "{}")))
            postings = self._read_postings()
            for row in self.conn.execute("SELECT * FROM transactions ORDER BY date, id"):
                tx = Transaction(
                    id=row["id"],
                    date=date.fromisoformat(row["date"]),
                    flag=row["flag"],
                    payee=row["payee"],
                    narration=row["narration"],
                    tags=set(row["tags"].split()),
                    links=set(row["links"].split()),
                    meta=json.loads(row["meta"]),
                    voided=bool(row["voided"]),
                    voided_by=row["voided_by"],
                    reversal_of=row["reversal_of"],
                    postings=postings.get(row["id"], []),
                )
                try:
                    tx.validate()
                except LedgerError as exc:
                    raise StorageError(f"corrupt transaction {tx.id} in {self.path}: {exc}") from exc
                journal.add(tx, validate=False)
            if settings.get("locked_through"):
                journal.lock_through(date.fromisoformat(settings["locked_through"]))
            return journal

    # -- queries --------------------------------------------------------------
    def count(self, table: str) -> int:
        if table not in ("accounts", "rates", "transactions", "postings"):
            raise StorageError(f"unknown table {table!r}")
        return int(self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

    def account_totals(self) -> dict[tuple[str, str], Decimal]:
        """Per (account, currency) totals, summed exactly with ``Decimal``."""
        totals: dict[tuple[str, str], Decimal] = {}
        for r in self.conn.execute("SELECT account, currency, amount FROM postings"):
            key = (r["account"], r["currency"])
            totals[key] = totals.get(key, Decimal(0)) + _decimal(r["amount"])
        return totals
