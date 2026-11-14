"""Postings and transactions."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from .accounts import is_descendant, normalize_name, validate_name
from .errors import TransactionError, UnbalancedTransactionError
from .money import Money, MoneyBag, Number, get_currency

VALID_FLAGS = ("*", "!")


@dataclass
class Posting:
    """A single leg of a transaction.

    ``price`` is an optional per-unit price used when the posting moves a
    commodity (``10 AAPL @ 150 USD``); the posting's *weight* is then expressed
    in the price currency.
    """

    account: str
    amount: Money
    price: Money | None = None
    memo: str = ""
    flag: str = ""
    meta: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.account = normalize_name(self.account)
        validate_name(self.account)
        if self.price is not None and self.price.currency == self.amount.currency:
            raise TransactionError(
                f"price currency must differ from amount currency on {self.account}"
            )

    def weight(self) -> Money:
        if self.price is None:
            return self.amount
        return Money(self.amount.amount * self.price.amount, self.price.currency)

    @property
    def currency(self) -> str:
        return self.amount.currency

    def is_debit(self) -> bool:
        return self.amount.is_positive()

    def is_credit(self) -> bool:
        return self.amount.is_negative()

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"account": self.account, "amount": self.amount.to_dict()}
        if self.price is not None:
            data["price"] = self.price.to_dict()
        if self.memo:
            data["memo"] = self.memo
        if self.flag:
            data["flag"] = self.flag
        if self.meta:
            data["meta"] = dict(self.meta)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Posting:
        return cls(
            account=data["account"],
            amount=Money.from_dict(data["amount"]),
            price=Money.from_dict(data["price"]) if data.get("price") else None,
            memo=data.get("memo", ""),
            flag=data.get("flag", ""),
            meta=dict(data.get("meta") or {}),
        )


@dataclass
class Transaction:
    date: date
    payee: str = ""
    narration: str = ""
    postings: list[Posting] = field(default_factory=list)
    flag: str = "*"
    tags: set[str] = field(default_factory=set)
    links: set[str] = field(default_factory=set)
    meta: dict[str, str] = field(default_factory=dict)
    id: str | None = None
    voided: bool = False
    voided_by: str | None = None
    reversal_of: str | None = None

    def __post_init__(self) -> None:
        if self.flag not in VALID_FLAGS:
            raise TransactionError(f"invalid transaction flag: {self.flag!r}")
        self.tags = {t.lstrip("#") for t in self.tags}
        self.links = {link.lstrip("^") for link in self.links}

    # -- composition ------------------------------------------------------
    def add_posting(
        self,
        account: str,
        amount: Money | Number,
        currency: str | None = None,
        memo: str = "",
        price: Money | None = None,
    ) -> Posting:
        if not isinstance(amount, Money):
            if currency is None:
                raise TransactionError("currency is required when amount is not Money")
            amount = Money(amount, currency)
        posting = Posting(account=account, amount=amount, memo=memo, price=price)
        self.postings.append(posting)
        return posting

    # -- inspection -------------------------------------------------------
    def residual(self) -> MoneyBag:
        bag = MoneyBag()
        for posting in self.postings:
            bag.add(posting.weight())
        return bag

    def tolerance(self) -> dict[str, Decimal]:
        """Half a minor unit per currency, only for postings carrying a price.

        Plain postings must balance exactly; priced postings routinely leave
        sub-cent residue (``3 AAPL @ 101.333 USD``) that is acceptable.
        """
        tolerances: dict[str, Decimal] = {}
        for posting in self.postings:
            if posting.price is None:
                continue
            code = posting.price.currency
            unit = get_currency(code).minor_unit / 2
            tolerances[code] = max(tolerances.get(code, Decimal(0)), unit)
        return tolerances

    def is_balanced(self) -> bool:
        return self.residual().is_zero(self.tolerance())

    def validate(self) -> None:
        if len(self.postings) < 2:
            raise UnbalancedTransactionError(
                f"transaction on {self.date} needs at least two postings", self.residual()
            )
        if not self.is_balanced():
            residual = self.residual()
            raise UnbalancedTransactionError(
                f"transaction on {self.date.isoformat()} ({self.payee or self.narration!r}) "
                f"does not balance: residual {residual}",
                residual,
            )

    def accounts(self) -> list[str]:
        seen: list[str] = []
        for posting in self.postings:
            if posting.account not in seen:
                seen.append(posting.account)
        return seen

    def currencies(self) -> list[str]:
        return sorted({p.amount.currency for p in self.postings})

    def touches(self, account: str) -> bool:
        return any(is_descendant(p.account, account) for p in self.postings)

    def postings_for(self, account: str) -> Iterator[Posting]:
        for posting in self.postings:
            if is_descendant(posting.account, account):
                yield posting

    def amount_for(self, account: str) -> MoneyBag:
        return MoneyBag(p.amount for p in self.postings_for(account))

    def total_debits(self) -> MoneyBag:
        return MoneyBag(p.amount for p in self.postings if p.is_debit())

    @property
    def description(self) -> str:
        if self.payee and self.narration:
            return f"{self.payee} | {self.narration}"
        return self.payee or self.narration

    def copy(self) -> Transaction:
        return copy.deepcopy(self)

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "flag": self.flag,
            "payee": self.payee,
            "narration": self.narration,
            "tags": sorted(self.tags),
            "links": sorted(self.links),
            "meta": dict(self.meta),
            "voided": self.voided,
            "voided_by": self.voided_by,
            "reversal_of": self.reversal_of,
            "postings": [p.to_dict() for p in self.postings],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transaction:
        return cls(
            id=data.get("id"),
            date=date.fromisoformat(data["date"]),
            flag=data.get("flag", "*"),
            payee=data.get("payee", ""),
            narration=data.get("narration", ""),
            tags=set(data.get("tags") or []),
            links=set(data.get("links") or []),
            meta=dict(data.get("meta") or {}),
            voided=bool(data.get("voided", False)),
            voided_by=data.get("voided_by"),
            reversal_of=data.get("reversal_of"),
            postings=[Posting.from_dict(p) for p in data.get("postings", [])],
        )


class TransactionBuilder:
    """Fluent helper for constructing transactions in code and tests.

    >>> tx = (TransactionBuilder(date(2024, 1, 1), "Grocer")
    ...       .debit("Expenses:Food", "12.50", "USD")
    ...       .credit("Assets:Cash", "12.50", "USD")
    ...       .build())
    """

    def __init__(self, on_date: date, payee: str = "", narration: str = "", flag: str = "*") -> None:
        self._tx = Transaction(date=on_date, payee=payee, narration=narration, flag=flag)

    def debit(self, account: str, amount: Number, currency: str, memo: str = "") -> TransactionBuilder:
        self._tx.add_posting(account, abs(Money(amount, currency)), memo=memo)
        return self

    def credit(self, account: str, amount: Number, currency: str, memo: str = "") -> TransactionBuilder:
        self._tx.add_posting(account, -abs(Money(amount, currency)), memo=memo)
        return self

    def posting(
        self,
        account: str,
        amount: Number,
        currency: str,
        memo: str = "",
        price: Money | None = None,
    ) -> TransactionBuilder:
        self._tx.add_posting(account, Money(amount, currency), memo=memo, price=price)
        return self

    def tag(self, *tags: str) -> TransactionBuilder:
        self._tx.tags.update(t.lstrip("#") for t in tags)
        return self

    def link(self, *links: str) -> TransactionBuilder:
        self._tx.links.update(link.lstrip("^") for link in links)
        return self

    def meta(self, **values: str) -> TransactionBuilder:
        self._tx.meta.update(values)
        return self

    def build(self, validate: bool = True) -> Transaction:
        if validate:
            self._tx.validate()
        return self._tx


def transfer(
    on_date: date,
    source: str,
    destination: str,
    amount: Money,
    payee: str = "",
    narration: str = "",
) -> Transaction:
    """Create a simple two-legged transfer of ``amount`` from source to destination."""
    tx = Transaction(date=on_date, payee=payee, narration=narration)
    tx.postings.append(Posting(destination, amount))
    tx.postings.append(Posting(source, -amount))
    return tx


def split_transaction(
    on_date: date,
    source: str,
    splits: Iterable[tuple[str, Money]],
    payee: str = "",
    narration: str = "",
) -> Transaction:
    """One credit from ``source`` funding several debits."""
    tx = Transaction(date=on_date, payee=payee, narration=narration)
    total: Money | None = None
    for account, amount in splits:
        tx.postings.append(Posting(account, amount))
        total = amount if total is None else total + amount
    if total is None:
        raise TransactionError("split_transaction requires at least one split")
    tx.postings.append(Posting(source, -total))
    return tx
