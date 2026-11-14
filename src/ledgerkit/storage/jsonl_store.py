"""Append-only JSON-lines event log backend.

Each line is one event: ``{"event": "snapshot", ...}`` with the full journal,
or ``{"event": "add" | "replace" | "void" | "remove", "tx": {...}}``.  Loading
replays the log from the most recent snapshot.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ..errors import StorageError
from ..exporters.json_export import journal_from_dict, journal_to_dict
from ..journal import Journal
from ..transactions import Transaction
from .base import Repository


class JsonlStore(Repository):
    def __init__(self, path: str | Path, fsync: bool = False) -> None:
        self.path = Path(path)
        self.fsync = fsync
        self._lock = threading.Lock()

    def exists(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    def _append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                if self.fsync:
                    os.fsync(handle.fileno())

    def save(self, journal: Journal) -> None:
        """Write a compacted log consisting of a single snapshot."""
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        record = {"event": "snapshot", "journal": journal_to_dict(journal)}
        with self._lock:
            tmp.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)

    def attach(self, journal: Journal) -> None:
        def on_event(event: str, tx: Transaction) -> None:
            self._append({"event": event, "tx": tx.to_dict()})

        journal.subscribe(on_event)

    def _records(self) -> list[dict]:
        records = []
        with self.path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise StorageError(f"{self.path}:{number}: invalid JSON: {exc}") from None
        return records

    def load(self) -> Journal:
        if not self.path.exists():
            raise StorageError(f"no such log: {self.path}")
        records = self._records()
        start = 0
        for index, record in enumerate(records):
            if record.get("event") == "snapshot":
                start = index
        if not records or records[start].get("event") != "snapshot":
            raise StorageError(f"{self.path} has no snapshot record")
        journal = journal_from_dict(records[start]["journal"])
        for record in records[start + 1:]:
            event = record.get("event")
            tx = Transaction.from_dict(record["tx"])
            if event == "add":
                if tx.id in journal:
                    continue
                journal.add(tx, validate=False)
            elif event in ("replace", "void"):
                journal._transactions[tx.id] = tx  # noqa: SLF001 - replaying history
            elif event == "remove":
                journal._transactions.pop(tx.id, None)  # noqa: SLF001
            else:
                raise StorageError(f"unknown event {event!r} in {self.path}")
        return journal

    def compact(self, journal: Journal | None = None) -> None:
        self.save(journal or self.load())
