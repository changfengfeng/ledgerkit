"""Lossless JSON export/import of a whole journal."""

from __future__ import annotations

import json
from typing import Any

from ..accounts import ChartOfAccounts
from ..currency import RateTable
from ..errors import StorageError
from ..journal import Journal
from ..transactions import Transaction

FORMAT_VERSION = 1


def journal_to_dict(journal: Journal) -> dict[str, Any]:
    return {
        "format": "ledgerkit",
        "version": FORMAT_VERSION,
        "options": dict(journal.options),
        "locked_through": journal.locked_through.isoformat() if journal.locked_through else None,
        "pivot": journal.rates.pivot,
        "accounts": journal.chart.to_list(),
        "rates": [
            {"date": r.date.isoformat(), "base": r.base, "quote": r.quote, "value": str(r.value), "source": r.source}
            for r in journal.rates
        ],
        "transactions": [tx.to_dict() for tx in journal.sorted()],
    }


def export_json(journal: Journal, indent: int | None = 2) -> str:
    return json.dumps(journal_to_dict(journal), indent=indent, ensure_ascii=False)


def journal_from_dict(data: dict[str, Any]) -> Journal:
    if data.get("format") != "ledgerkit":
        raise StorageError("not a ledgerkit JSON document")
    if int(data.get("version", 0)) > FORMAT_VERSION:
        raise StorageError(f"unsupported format version {data.get('version')}")
    rates = RateTable(pivot=data.get("pivot") or "USD")
    for item in data.get("rates", []):
        rates.add(item["base"], item["quote"], item["date"], item["value"], item.get("source", "json"))
    journal = Journal(chart=ChartOfAccounts.from_list(data.get("accounts", [])), rates=rates)
    journal.options.update(data.get("options") or {})
    for item in data.get("transactions", []):
        journal.add(Transaction.from_dict(item), validate=False)
    for tx in journal.sorted():
        tx.validate()
    if data.get("locked_through"):
        from datetime import date

        journal.lock_through(date.fromisoformat(data["locked_through"]))
    return journal


def import_json(text: str) -> Journal:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StorageError(f"invalid JSON: {exc}") from None
    return journal_from_dict(data)
