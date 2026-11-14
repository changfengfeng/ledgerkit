"""CSV exports."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from ..journal import Journal
from ..transactions import Transaction

POSTING_COLUMNS = [
    "id",
    "date",
    "flag",
    "payee",
    "narration",
    "account",
    "amount",
    "currency",
    "price",
    "price_currency",
    "memo",
    "tags",
    "voided",
]


def export_postings_csv(journal: Journal, transactions: Iterable[Transaction] | None = None) -> str:
    """One row per posting — convenient for spreadsheets and pivot tables."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(POSTING_COLUMNS)
    for tx in transactions if transactions is not None else journal.sorted():
        for posting in tx.postings:
            writer.writerow(
                [
                    tx.id or "",
                    tx.date.isoformat(),
                    tx.flag,
                    tx.payee,
                    tx.narration,
                    posting.account,
                    str(posting.amount.amount),
                    posting.amount.currency,
                    str(posting.price.amount) if posting.price else "",
                    posting.price.currency if posting.price else "",
                    posting.memo,
                    " ".join(sorted(tx.tags)),
                    "1" if tx.voided else "",
                ]
            )
    return buffer.getvalue()


def export_transactions_csv(journal: Journal) -> str:
    """One row per transaction with the postings condensed into a column."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["id", "date", "payee", "narration", "postings", "tags"])
    for tx in journal.sorted():
        legs = "; ".join(f"{p.account} {p.amount}" for p in tx.postings)
        writer.writerow([tx.id or "", tx.date.isoformat(), tx.payee, tx.narration, legs, " ".join(sorted(tx.tags))])
    return buffer.getvalue()
