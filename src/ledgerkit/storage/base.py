"""Repository interface shared by the storage backends."""

from __future__ import annotations

import abc
from pathlib import Path

from ..errors import StorageError
from ..journal import Journal


class Repository(abc.ABC):
    """Persist and restore complete journals.

    ``save`` writes the full state (accounts, rates, transactions, options,
    lock date); ``load`` rebuilds an equivalent :class:`Journal`.  Backends may
    also support incremental writes via :meth:`attach`, which subscribes to
    journal events and persists each change as it happens.
    """

    @abc.abstractmethod
    def save(self, journal: Journal) -> None: ...

    @abc.abstractmethod
    def load(self) -> Journal: ...

    @abc.abstractmethod
    def exists(self) -> bool: ...

    def attach(self, journal: Journal) -> None:
        raise StorageError(f"{type(self).__name__} does not support incremental writes")

    def close(self) -> None:  # pragma: no cover - default no-op
        return None

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_repository(path: str | Path) -> Repository:
    """Pick a backend from the file extension (``.db``/``.sqlite`` or ``.jsonl``)."""
    from .jsonl_store import JsonlStore
    from .sqlite_store import SqliteStore

    suffix = Path(path).suffix.lower()
    if suffix in (".db", ".sqlite", ".sqlite3"):
        return SqliteStore(path)
    if suffix in (".jsonl", ".ndjson"):
        return JsonlStore(path)
    raise StorageError(f"cannot infer storage backend from {path!r}")
