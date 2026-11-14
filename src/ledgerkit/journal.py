"""The journal: an ordered, validated collection of transactions."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable, Iterator
from datetime import date

from .accounts import ChartOfAccounts, is_descendant
from .currency import RateTable
from .errors import (
    AlreadyVoidedError,
    PeriodClosedError,
    TransactionError,
    TransactionNotFoundError,
    UnknownAccountError,
)
from .transactions import Posting, Transaction

Listener = Callable[[str, Transaction], None]


class Journal:
    """Holds transactions and enforces accounting invariants on insert.

    * every transaction must balance;
    * when a chart of accounts is attached, every posting must target an open
      account that accepts the posting currency (``auto_open`` opens unknown
      accounts on first use instead of failing);
    * transactions dated on or before the lock date are immutable.
    """

    def __init__(
        self,
        chart: ChartOfAccounts | None = None,
        rates: RateTable | None = None,
        auto_open: bool = False,
        id_prefix: str = "tx",
    ) -> None:
        self.chart = chart if chart is not None else ChartOfAccounts()
        self.rates = rates if rates is not None else RateTable()
        self.auto_open = auto_open
        self.id_prefix = id_prefix
        self.options: dict[str, str] = {}
        self._transactions: dict[str, Transaction] = {}
        self._counter = itertools.count(1)
        self._locked_through: date | None = None
        self._listeners: list[Listener] = []

    # -- events -------------------------------------------------------------
    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def _emit(self, event: str, tx: Transaction) -> None:
        for listener in list(self._listeners):
            listener(event, tx)

    # -- id management --------------------------------------------------------
    def _next_id(self) -> str:
        while True:
            candidate = f"{self.id_prefix}-{next(self._counter):06d}"
            if candidate not in self._transactions:
                return candidate

    def _reserve_id(self, tx_id: str) -> None:
        prefix = f"{self.id_prefix}-"
        if tx_id.startswith(prefix) and tx_id[len(prefix):].isdigit():
            number = int(tx_id[len(prefix):])
            current = next(self._counter)
            self._counter = itertools.count(max(current, number + 1))

    # -- locking --------------------------------------------------------------
    @property
    def locked_through(self) -> date | None:
        return self._locked_through

    def lock_through(self, day: date) -> None:
        if self._locked_through is not None and day < self._locked_through:
            raise PeriodClosedError(
                f"journal already locked through {self._locked_through.isoformat()}"
            )
        self._locked_through = day

    def unlock(self) -> None:
        self._locked_through = None

    def _check_lock(self, day: date) -> None:
        if self._locked_through is not None and day <= self._locked_through:
            raise PeriodClosedError(
                f"period is closed through {self._locked_through.isoformat()}; "
                f"cannot modify entries dated {day.isoformat()}"
            )

    # -- validation -----------------------------------------------------------
    def _check_accounts(self, tx: Transaction) -> None:
        for posting in tx.postings:
            if posting.account not in self.chart:
                if not self.auto_open:
                    raise UnknownAccountError(
                        f"posting to unknown account {posting.account} on {tx.date.isoformat()}"
                    )
                self.chart.open(posting.account, tx.date)
            self.chart.check_posting(posting.account, posting.currency, tx.date)

    def validate(self, tx: Transaction) -> None:
        tx.validate()
        self._check_lock(tx.date)
        self._check_accounts(tx)

    # -- mutation -------------------------------------------------------------
    def add(self, tx: Transaction, validate: bool = True) -> Transaction:
        if validate:
            self.validate(tx)
        if tx.id is None:
            tx.id = self._next_id()
        elif tx.id in self._transactions:
            raise TransactionError(f"duplicate transaction id: {tx.id}")
        else:
            self._reserve_id(tx.id)
        self._transactions[tx.id] = tx
        self._emit("add", tx)
        return tx

    def add_many(self, transactions: Iterable[Transaction], validate: bool = True) -> list[Transaction]:
        return [self.add(tx, validate=validate) for tx in transactions]

    def replace(self, tx_id: str, new_tx: Transaction) -> Transaction:
        """Replace a transaction in place, keeping its id.

        Both the old and new dates must be outside the locked period.
        """
        old = self.get(tx_id)
        if old.voided:
            raise AlreadyVoidedError(f"cannot edit voided transaction {tx_id}")
        self._check_lock(old.date)
        new_tx.id = tx_id
        self.validate(new_tx)
        self._transactions[tx_id] = new_tx
        self._emit("replace", new_tx)
        return new_tx

    def void(self, tx_id: str, on_date: date | None = None, reason: str = "") -> Transaction:
        """Void a transaction by recording a reversing entry.

        The original transaction is kept (and flagged ``voided``) so the audit
        trail is preserved; the reversal carries the opposite amounts, so the
        two cancel out in every balance.
        """
        original = self.get(tx_id)
        if original.voided:
            raise AlreadyVoidedError(f"transaction {tx_id} is already voided")
        if original.reversal_of is not None:
            raise TransactionError(f"transaction {tx_id} is itself a reversal")
        when = on_date or original.date
        self._check_lock(when)
        reversal = Transaction(
            date=when,
            payee=original.payee,
            narration=f"Void of {tx_id}" + (f": {reason}" if reason else ""),
            postings=list(original.postings),
            flag=original.flag,
            tags=set(original.tags) | {"void"},
            links=set(original.links),
            meta={"void_reason": reason} if reason else {},
            reversal_of=tx_id,
        )
        for posting in reversal.postings:
            posting.amount = -posting.amount
        self.add(reversal)
        original.voided = True
        original.voided_by = reversal.id
        self._emit("void", original)
        return reversal

    def remove(self, tx_id: str) -> Transaction:
        tx = self.get(tx_id)
        self._check_lock(tx.date)
        if tx.voided or tx.reversal_of:
            raise TransactionError("voided transactions and reversals cannot be removed")
        del self._transactions[tx_id]
        self._emit("remove", tx)
        return tx

    # -- access ---------------------------------------------------------------
    def get(self, tx_id: str) -> Transaction:
        try:
            return self._transactions[tx_id]
        except KeyError:
            raise TransactionNotFoundError(f"no transaction with id {tx_id}") from None

    def __contains__(self, tx_id: object) -> bool:
        return isinstance(tx_id, str) and tx_id in self._transactions

    def __len__(self) -> int:
        return len(self._transactions)

    def __iter__(self) -> Iterator[Transaction]:
        return iter(self.sorted())

    def sorted(self) -> list[Transaction]:
        return sorted(self._transactions.values(), key=lambda t: (t.date, t.id or ""))

    def transactions(
        self,
        start: date | None = None,
        end: date | None = None,
        account: str | None = None,
        payee: str | None = None,
        tags: Iterable[str] | None = None,
        include_voided: bool = True,
    ) -> list[Transaction]:
        """Filter transactions; ``start`` and ``end`` are inclusive."""
        wanted_tags = {t.lstrip("#") for t in tags or ()}
        payee_lower = payee.lower() if payee else None
        result = []
        for tx in self.sorted():
            if start is not None and tx.date < start:
                continue
            if end is not None and tx.date > end:
                continue
            if not include_voided and (tx.voided or tx.reversal_of):
                continue
            if account is not None and not tx.touches(account):
                continue
            if payee_lower is not None and payee_lower not in tx.payee.lower():
                continue
            if wanted_tags and not wanted_tags <= tx.tags:
                continue
            result.append(tx)
        return result

    def postings(
        self,
        account: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> Iterator[tuple[Transaction, Posting]]:
        for tx in self.transactions(start=start, end=end):
            for posting in tx.postings:
                if account is None or is_descendant(posting.account, account):
                    yield tx, posting

    def payees(self) -> list[str]:
        return sorted({tx.payee for tx in self._transactions.values() if tx.payee})

    def tags(self) -> list[str]:
        found: set[str] = set()
        for tx in self._transactions.values():
            found |= tx.tags
        return sorted(found)

    def accounts_used(self) -> list[str]:
        found: set[str] = set()
        for tx in self._transactions.values():
            found.update(p.account for p in tx.postings)
        return sorted(found)

    def currencies(self) -> list[str]:
        found: set[str] = set()
        for tx in self._transactions.values():
            found.update(p.currency for p in tx.postings)
        return sorted(found)

    def date_range(self) -> tuple[date, date] | None:
        if not self._transactions:
            return None
        dates = [tx.date for tx in self._transactions.values()]
        return min(dates), max(dates)

    def find_by_link(self, link: str) -> list[Transaction]:
        link = link.lstrip("^")
        return [tx for tx in self.sorted() if link in tx.links]

    def find_by_meta(self, key: str, value: str) -> list[Transaction]:
        return [tx for tx in self.sorted() if tx.meta.get(key) == value]
