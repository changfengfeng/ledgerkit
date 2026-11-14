"""Configurable CSV importer."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from ..errors import ImporterError, InvalidAmountError
from ..money import Money, to_decimal
from ..utils.dates import DateParseError, parse_date, parse_date_with_format
from .base import ImportedLine


@dataclass
class CsvMapping:
    """Column mapping for a bank's CSV export.

    Either ``amount`` or both ``debit`` and ``credit`` must be given.  Column
    references are header names (or 0-based indexes when ``has_header`` is
    false).  ``invert`` flips the sign for banks that report spending as
    positive numbers.
    """

    date: str = "date"
    description: str = "description"
    amount: str | None = "amount"
    debit: str | None = None
    credit: str | None = None
    currency: str | None = None
    reference: str | None = None
    payee: str | None = None
    balance: str | None = None
    default_currency: str = "USD"
    date_format: str | None = None
    dayfirst: bool = False
    decimal_comma: bool = False
    invert: bool = False
    delimiter: str = ","
    has_header: bool = True
    skip_rows: int = 0
    encoding: str = "utf-8-sig"

    def __post_init__(self) -> None:
        if self.amount is None and (self.debit is None or self.credit is None):
            raise ImporterError("CsvMapping needs 'amount' or both 'debit' and 'credit'")


class CsvImporter:
    def __init__(self, mapping: CsvMapping | None = None) -> None:
        self.mapping = mapping or CsvMapping()

    # -- helpers --------------------------------------------------------------
    def _number(self, text: str) -> str:
        value = text.strip()
        if self.mapping.decimal_comma:
            value = value.replace(".", "").replace(" ", "").replace(",", ".")
        return value

    def _get(self, row: dict[str, str] | list[str], column: str | None) -> str:
        if column is None:
            return ""
        if isinstance(row, list):
            try:
                return row[int(column)]
            except (ValueError, IndexError):
                raise ImporterError(f"column {column!r} not present in row") from None
        if column not in row:
            raise ImporterError(f"column {column!r} not found; available: {', '.join(row)}")
        return row[column] or ""

    def _date(self, text: str):
        if self.mapping.date_format:
            return parse_date_with_format(text, self.mapping.date_format)
        return parse_date(text, dayfirst=self.mapping.dayfirst)

    def _amount(self, row, currency: str) -> Money:
        m = self.mapping
        if m.amount is not None:
            raw = self._get(row, m.amount)
            value = to_decimal(self._number(raw))
        else:
            debit = self._number(self._get(row, m.debit))
            credit = self._number(self._get(row, m.credit))
            value = (to_decimal(credit) if credit else 0) - (to_decimal(debit) if debit else 0)
        if m.invert:
            value = -value
        return Money(value, currency)

    def _rows(self, text: str) -> Iterator[tuple[int, dict[str, str] | list[str]]]:
        lines = text.splitlines()[self.mapping.skip_rows:]
        buffer = io.StringIO("\n".join(lines))
        if self.mapping.has_header:
            reader = csv.DictReader(buffer, delimiter=self.mapping.delimiter)
            for index, row in enumerate(reader, start=2 + self.mapping.skip_rows):
                if not any((v or "").strip() for v in row.values()):
                    continue
                yield index, {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        else:
            for index, row in enumerate(csv.reader(buffer, delimiter=self.mapping.delimiter), start=1 + self.mapping.skip_rows):
                if not any(cell.strip() for cell in row):
                    continue
                yield index, [cell.strip() for cell in row]

    # -- public API -----------------------------------------------------------
    def parse(self, text: str) -> list[ImportedLine]:
        m = self.mapping
        lines: list[ImportedLine] = []
        for index, row in self._rows(text):
            try:
                currency = (self._get(row, m.currency) or m.default_currency).upper()
                amount = self._amount(row, currency)
                balance = None
                if m.balance:
                    raw_balance = self._get(row, m.balance)
                    if raw_balance:
                        balance = Money(to_decimal(self._number(raw_balance)), currency)
                raw = tuple(row.values()) if isinstance(row, dict) else tuple(row)
                lines.append(
                    ImportedLine(
                        date=self._date(self._get(row, m.date)),
                        amount=amount,
                        description=self._get(row, m.description),
                        reference=self._get(row, m.reference),
                        payee=self._get(row, m.payee),
                        balance=balance,
                        raw=raw,
                    )
                )
            except (InvalidAmountError, DateParseError, ImporterError) as exc:
                raise ImporterError(f"row {index}: {exc}") from None
        return lines

    def parse_file(self, path: str | Path) -> list[ImportedLine]:
        return self.parse(Path(path).read_text(encoding=self.mapping.encoding))
