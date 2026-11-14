"""Serialise journals back into the ledger text format."""

from __future__ import annotations

from collections.abc import Iterable

from ..journal import Journal
from ..money import Money, get_currency
from ..transactions import Posting, Transaction
from ..utils.text import quote

INTERNAL_META = {"lineno", "filename"}


def format_amount(money: Money) -> str:
    places = max(get_currency(money.currency).decimals, -money.amount.as_tuple().exponent)  # type: ignore[operator]
    return f"{money.amount:.{places}f} {money.currency}"


def format_posting(posting: Posting, width: int = 50, indent: str = "  ") -> list[str]:
    head = indent + (f"{posting.flag} " if posting.flag else "") + posting.account
    amount = format_amount(posting.amount)
    if posting.price is not None:
        amount += f" @ {format_amount(posting.price)}"
    gap = max(2, width - len(head) - len(amount.split(" ")[0]))
    line = f"{head}{' ' * gap}{amount}"
    if posting.memo:
        line += f" ; {posting.memo}"
    lines = [line]
    for key, value in sorted(posting.meta.items()):
        lines.append(f"{indent}{indent}{key}: {quote(value)}")
    return lines


def format_transaction(tx: Transaction, width: int = 50, include_internal_meta: bool = False) -> str:
    parts = [tx.date.isoformat(), tx.flag]
    if tx.payee:
        parts.append(quote(tx.payee))
        parts.append(quote(tx.narration))
    elif tx.narration:
        parts.append(quote(tx.narration))
    parts.extend(f"#{tag}" for tag in sorted(tx.tags))
    parts.extend(f"^{link}" for link in sorted(tx.links))
    lines = [" ".join(parts)]
    for key, value in sorted(tx.meta.items()):
        if key in INTERNAL_META and not include_internal_meta:
            continue
        lines.append(f"  {key}: {quote(value)}")
    for posting in tx.postings:
        lines.extend(format_posting(posting, width))
    return "\n".join(lines)


def format_transactions(transactions: Iterable[Transaction], width: int = 50) -> str:
    return "\n\n".join(format_transaction(tx, width) for tx in transactions) + "\n"


def dump_journal(journal: Journal, width: int = 50) -> str:
    """Render options, account openings, rates and transactions."""
    sections: list[str] = []
    if journal.options:
        sections.append(
            "\n".join(f"option {quote(k)} {quote(v)}" for k, v in sorted(journal.options.items()))
        )
    opens = []
    closes = []
    for account in journal.chart:
        line = f"{account.opened.isoformat()} open {account.name}"
        if account.currencies:
            line += " " + ",".join(sorted(account.currencies))
        if account.description:
            line += " " + quote(account.description)
        opens.append(line)
        if account.closed:
            closes.append(f"{account.closed.isoformat()} close {account.name}")
    if opens:
        sections.append("\n".join(opens))
    rates = [
        f"{rate.date.isoformat()} rate {rate.base} {rate.quote} {rate.value}" for rate in journal.rates
    ]
    if rates:
        sections.append("\n".join(sorted(rates)))
    txs = journal.sorted()
    if txs:
        sections.append(format_transactions(txs, width).rstrip("\n"))
    if closes:
        sections.append("\n".join(closes))
    return "\n\n".join(sections) + "\n"
