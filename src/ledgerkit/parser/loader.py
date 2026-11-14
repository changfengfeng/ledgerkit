"""Turn a parsed ledger file into a :class:`~ledgerkit.journal.Journal`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..accounts import ChartOfAccounts
from ..balances import balance
from ..currency import RateTable
from ..errors import LedgerError, ParseError
from ..journal import Journal
from .parser import (
    BalanceAssertion,
    CloseDirective,
    CommodityDirective,
    NoteDirective,
    OpenDirective,
    ParsedFile,
    RateDirective,
    parse_file,
    parse_string,
)

# Same-day ordering: declare commodities, open accounts, record rates, then
# post transactions, check balances and finally close accounts.
_ORDER: dict[object, int] = {
    CommodityDirective: 0,
    OpenDirective: 1,
    RateDirective: 2,
    "transaction": 3,
    BalanceAssertion: 4,
    NoteDirective: 5,
    CloseDirective: 6,
}


@dataclass
class LoadResult:
    journal: Journal
    errors: list[LedgerError] = field(default_factory=list)
    notes: list[NoteDirective] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _entry_key(entry: object) -> tuple[date, int, int]:
    rank = _ORDER.get(type(entry))
    if rank is None:
        rank = _ORDER["transaction"]
        line = int(entry.meta.get("lineno", 0))  # type: ignore[attr-defined]
    else:
        line = getattr(entry, "line", 0)
    return (entry.date, rank, line)  # type: ignore[attr-defined]


def build_journal(parsed: ParsedFile, strict: bool = True, auto_open: bool = False) -> LoadResult:
    pivot = parsed.options.get("operating_currency", "USD")
    journal = Journal(chart=ChartOfAccounts(), rates=RateTable(pivot=pivot), auto_open=auto_open)
    journal.options.update(parsed.options)
    result = LoadResult(journal=journal, errors=list(parsed.errors))

    def fail(error: LedgerError, line: int | None = None) -> None:
        if not isinstance(error, ParseError) and line is not None:
            error = ParseError(str(error), line=line, source=parsed.source)
        if strict:
            raise error
        result.errors.append(error)

    entries: list[object] = [*parsed.directives, *parsed.transactions]
    entries.sort(key=_entry_key)
    for entry in entries:
        try:
            if isinstance(entry, OpenDirective):
                journal.chart.open(entry.account, entry.date, entry.currencies, entry.description)
            elif isinstance(entry, CloseDirective):
                journal.chart.close(entry.account, entry.date)
            elif isinstance(entry, RateDirective):
                journal.rates.add(entry.base, entry.quote, entry.date, entry.value, source="ledger")
            elif isinstance(entry, CommodityDirective):
                continue
            elif isinstance(entry, BalanceAssertion):
                actual = balance(journal, entry.account, as_of=entry.date).get(entry.amount.currency)
                if actual != entry.amount:
                    raise LedgerError(
                        f"balance assertion failed for {entry.account} on {entry.date}: "
                        f"expected {entry.amount}, got {actual}"
                    )
            elif isinstance(entry, NoteDirective):
                result.notes.append(entry)
            else:
                journal.add(entry)  # type: ignore[arg-type]
        except LedgerError as exc:
            line = getattr(entry, "line", None)
            if line is None and hasattr(entry, "meta"):
                line = int(entry.meta.get("lineno", 0)) or None  # type: ignore[attr-defined]
            fail(exc, line)
    return result


def load_string(text: str, strict: bool = True, auto_open: bool = False, source: str | None = None) -> LoadResult:
    return build_journal(parse_string(text, source=source, strict=strict), strict=strict, auto_open=auto_open)


def load_file(path: str | Path, strict: bool = True, auto_open: bool = False) -> LoadResult:
    return build_journal(parse_file(path, strict=strict), strict=strict, auto_open=auto_open)
