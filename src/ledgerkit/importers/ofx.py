"""Minimal OFX (1.x SGML and 2.x XML) statement importer.

Only ``<STMTTRN>`` blocks are read; that is all most personal-finance exports
need.  Values are taken from leaf tags, which may or may not be closed.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..errors import ImporterError
from ..money import Money, to_decimal
from ..utils.dates import parse_date
from .base import ImportedLine

_BLOCK_RE = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.S | re.I)
_FIELD_RE = re.compile(r"<([A-Z0-9.]+)>([^<\r\n]*)", re.I)
_CURRENCY_RE = re.compile(r"<CURDEF>\s*([A-Z]{3})", re.I)
_BALANCE_RE = re.compile(r"<LEDGERBAL>.*?<BALAMT>\s*([-+0-9.,]+).*?<DTASOF>\s*(\d{8})", re.S | re.I)


class OfxImporter:
    def __init__(self, default_currency: str = "USD") -> None:
        self.default_currency = default_currency

    def statement_currency(self, text: str) -> str:
        match = _CURRENCY_RE.search(text)
        return match.group(1).upper() if match else self.default_currency

    def ledger_balance(self, text: str) -> tuple[Money, object] | None:
        match = _BALANCE_RE.search(text)
        if not match:
            return None
        currency = self.statement_currency(text)
        return Money(to_decimal(match.group(1)), currency), parse_date(match.group(2))

    def parse(self, text: str) -> list[ImportedLine]:
        currency = self.statement_currency(text)
        lines = []
        for number, block in enumerate(_BLOCK_RE.findall(text), start=1):
            fields = {name.upper(): value.strip() for name, value in _FIELD_RE.findall(block)}
            try:
                posted = fields["DTPOSTED"][:8]
                amount = fields["TRNAMT"]
            except KeyError as exc:
                raise ImporterError(f"transaction {number}: missing <{exc.args[0]}>") from None
            name = fields.get("NAME", "")
            memo = fields.get("MEMO", "")
            description = " - ".join(p for p in (name, memo) if p) or fields.get("TRNTYPE", "")
            lines.append(
                ImportedLine(
                    date=parse_date(posted),
                    amount=Money(to_decimal(amount), currency),
                    description=description,
                    reference=fields.get("FITID", ""),
                    payee=name,
                    raw=tuple(f"{k}={v}" for k, v in sorted(fields.items())),
                )
            )
        return lines

    def parse_file(self, path: str | Path) -> list[ImportedLine]:
        return self.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
