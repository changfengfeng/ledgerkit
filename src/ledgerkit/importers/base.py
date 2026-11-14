"""Shared importer model: raw statement lines -> balanced transactions."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from ..errors import LedgerError
from ..journal import Journal
from ..money import Money
from ..transactions import Posting, Transaction
from .rules import RuleSet


@dataclass(frozen=True)
class ImportedLine:
    date: date
    amount: Money
    description: str
    reference: str = ""
    payee: str = ""
    balance: Money | None = None
    raw: tuple[str, ...] = ()


def fingerprint(account: str, line: ImportedLine) -> str:
    """Stable id used to skip lines that were imported before."""
    material = "|".join(
        [
            account,
            line.date.isoformat(),
            str(line.amount.amount.normalize()),
            line.amount.currency,
            line.reference or line.description.strip().lower(),
        ]
    )
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


@dataclass
class ImportResult:
    created: list[Transaction] = field(default_factory=list)
    duplicates: list[ImportedLine] = field(default_factory=list)
    errors: list[tuple[ImportedLine, str]] = field(default_factory=list)
    uncategorized: list[Transaction] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "created": len(self.created),
            "duplicates": len(self.duplicates),
            "errors": len(self.errors),
            "uncategorized": len(self.uncategorized),
        }


def line_to_transaction(
    line: ImportedLine,
    account: str,
    rules: RuleSet | None,
    fallback_account: str,
) -> tuple[Transaction, bool]:
    """Build a two-legged transaction; returns (tx, categorized?)."""
    match = rules.match(line) if rules else None
    counter = match.account if match else fallback_account
    payee = (match.payee if match and match.payee else None) or line.payee or line.description
    tx = Transaction(
        date=line.date,
        payee=payee,
        narration=line.description if payee != line.description else "",
        tags=set(match.tags) if match else set(),
        meta={"import_id": fingerprint(account, line)},
    )
    if line.reference:
        tx.meta["ref"] = line.reference
    tx.postings.append(Posting(account, line.amount))
    tx.postings.append(Posting(counter, -line.amount))
    return tx, match is not None


def import_into(
    journal: Journal,
    lines: Iterable[ImportedLine],
    account: str,
    rules: RuleSet | None = None,
    fallback_account: str = "Expenses:Uncategorized",
    dry_run: bool = False,
) -> ImportResult:
    known = {tx.meta.get("import_id") for tx in journal.sorted() if "import_id" in tx.meta}
    result = ImportResult()
    for line in lines:
        fid = fingerprint(account, line)
        if fid in known:
            result.duplicates.append(line)
            continue
        try:
            tx, categorized = line_to_transaction(line, account, rules, fallback_account)
            if not dry_run:
                journal.add(tx)
        except LedgerError as exc:
            result.errors.append((line, str(exc)))
            continue
        known.add(fid)
        result.created.append(tx)
        if not categorized:
            result.uncategorized.append(tx)
    return result
