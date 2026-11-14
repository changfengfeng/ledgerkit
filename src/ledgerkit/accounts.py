"""Chart of accounts.

Account names are colon separated paths whose first segment is one of the five
root types: ``Assets``, ``Liabilities``, ``Equity``, ``Income`` and
``Expenses``.  Parent accounts exist implicitly; only leaves need to be opened.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from .errors import (
    AccountClosedError,
    CurrencyNotAllowedError,
    DuplicateAccountError,
    InvalidAccountNameError,
    UnknownAccountError,
)

SEPARATOR = ":"


class AccountType(str, Enum):
    ASSETS = "Assets"
    LIABILITIES = "Liabilities"
    EQUITY = "Equity"
    INCOME = "Income"
    EXPENSES = "Expenses"

    @property
    def normal_sign(self) -> int:
        """+1 for debit-normal accounts, -1 for credit-normal accounts."""
        return 1 if self in (AccountType.ASSETS, AccountType.EXPENSES) else -1

    @property
    def is_balance_sheet(self) -> bool:
        return self in (AccountType.ASSETS, AccountType.LIABILITIES, AccountType.EQUITY)

    @property
    def is_income_statement(self) -> bool:
        return not self.is_balance_sheet


_ROOT_ALIASES = {
    "assets": AccountType.ASSETS,
    "asset": AccountType.ASSETS,
    "liabilities": AccountType.LIABILITIES,
    "liability": AccountType.LIABILITIES,
    "equity": AccountType.EQUITY,
    "income": AccountType.INCOME,
    "revenue": AccountType.INCOME,
    "expenses": AccountType.EXPENSES,
    "expense": AccountType.EXPENSES,
}

_SEGMENT_RE = re.compile(r"^[A-Z0-9][A-Za-z0-9\-]*$")


def split_name(name: str) -> list[str]:
    return name.split(SEPARATOR)


def join_name(*segments: str) -> str:
    return SEPARATOR.join(s for s in segments if s)


def parent_of(name: str) -> str | None:
    if SEPARATOR not in name:
        return None
    return name.rsplit(SEPARATOR, 1)[0]


def ancestors(name: str) -> list[str]:
    """Return all ancestors from the root down, excluding ``name`` itself."""
    parts = split_name(name)
    return [SEPARATOR.join(parts[:i]) for i in range(1, len(parts))]


def leaf_of(name: str) -> str:
    return split_name(name)[-1]


def depth_of(name: str) -> int:
    return len(split_name(name))


def is_descendant(name: str, parent: str) -> bool:
    """True if ``name`` equals ``parent`` or lives beneath it."""
    return name == parent or name.startswith(parent + SEPARATOR)


def account_type_of(name: str) -> AccountType:
    root = split_name(name)[0]
    try:
        return _ROOT_ALIASES[root.lower()]
    except KeyError:
        raise InvalidAccountNameError(
            f"account {name!r} must start with one of: "
            + ", ".join(t.value for t in AccountType)
        ) from None


def validate_name(name: str) -> str:
    if not name or name != name.strip():
        raise InvalidAccountNameError(f"invalid account name: {name!r}")
    segments = split_name(name)
    if len(segments) < 2:
        raise InvalidAccountNameError(f"account {name!r} needs at least two segments")
    account_type_of(name)
    for segment in segments:
        if not _SEGMENT_RE.match(segment):
            raise InvalidAccountNameError(f"invalid segment {segment!r} in account {name!r}")
    return name


def normalize_name(name: str) -> str:
    """Canonicalise the root segment (``revenue:Sales`` -> ``Income:Sales``)."""
    segments = split_name(name.strip())
    root = _ROOT_ALIASES.get(segments[0].lower())
    if root is not None:
        segments[0] = root.value
    return SEPARATOR.join(segments)


@dataclass
class Account:
    name: str
    opened: date
    closed: date | None = None
    currencies: frozenset[str] = frozenset()
    description: str = ""
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def type(self) -> AccountType:
        return account_type_of(self.name)

    @property
    def parent(self) -> str | None:
        return parent_of(self.name)

    @property
    def leaf(self) -> str:
        return leaf_of(self.name)

    @property
    def depth(self) -> int:
        return depth_of(self.name)

    def is_open_on(self, day: date) -> bool:
        if day < self.opened:
            return False
        return self.closed is None or day <= self.closed

    def allows(self, currency: str) -> bool:
        return not self.currencies or currency.upper() in self.currencies

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.type.value,
            "opened": self.opened.isoformat(),
            "closed": self.closed.isoformat() if self.closed else None,
            "currencies": sorted(self.currencies),
            "description": self.description,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Account:
        closed = data.get("closed")
        return cls(
            name=str(data["name"]),
            opened=date.fromisoformat(str(data["opened"])),
            closed=date.fromisoformat(str(closed)) if closed else None,
            currencies=frozenset(str(c) for c in data.get("currencies") or []),  # type: ignore[union-attr]
            description=str(data.get("description") or ""),
            meta={str(k): str(v) for k, v in (data.get("meta") or {}).items()},  # type: ignore[union-attr]
        )


class ChartOfAccounts:
    """Registry of opened accounts with validation helpers."""

    def __init__(self, accounts: Iterable[Account] | None = None) -> None:
        self._accounts: dict[str, Account] = {}
        for account in accounts or ():
            self.add(account)

    def add(self, account: Account) -> Account:
        validate_name(account.name)
        if account.name in self._accounts:
            raise DuplicateAccountError(f"account already exists: {account.name}")
        if account.closed and account.closed < account.opened:
            raise AccountClosedError(f"account {account.name} closed before it was opened")
        self._accounts[account.name] = account
        return account

    def open(
        self,
        name: str,
        on_date: date,
        currencies: Iterable[str] | None = None,
        description: str = "",
        **meta: str,
    ) -> Account:
        name = normalize_name(name)
        account = Account(
            name=name,
            opened=on_date,
            currencies=frozenset(c.upper() for c in currencies or ()),
            description=description,
            meta=dict(meta),
        )
        return self.add(account)

    def close(self, name: str, on_date: date) -> Account:
        account = self.get(name)
        if account.closed is not None:
            raise AccountClosedError(f"account {name} already closed on {account.closed}")
        if on_date < account.opened:
            raise AccountClosedError(f"cannot close {name} before it was opened")
        account.closed = on_date
        return account

    def reopen(self, name: str) -> Account:
        account = self.get(name)
        account.closed = None
        return account

    def get(self, name: str) -> Account:
        try:
            return self._accounts[name]
        except KeyError:
            raise UnknownAccountError(f"unknown account: {name}") from None

    def find(self, name: str) -> Account | None:
        return self._accounts.get(name)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._accounts

    def __iter__(self) -> Iterator[Account]:
        for name in sorted(self._accounts):
            yield self._accounts[name]

    def __len__(self) -> int:
        return len(self._accounts)

    def names(self) -> list[str]:
        return sorted(self._accounts)

    def is_known(self, name: str) -> bool:
        """True for opened accounts and for implicit parents of opened accounts."""
        if name in self._accounts:
            return True
        prefix = name + SEPARATOR
        return any(n.startswith(prefix) for n in self._accounts)

    def children(self, name: str) -> list[str]:
        """Direct children (opened or implicit) of ``name``."""
        prefix = name + SEPARATOR
        found: set[str] = set()
        for account in self._accounts:
            if account.startswith(prefix):
                rest = account[len(prefix):]
                found.add(prefix + rest.split(SEPARATOR, 1)[0])
        return sorted(found)

    def descendants(self, name: str) -> list[Account]:
        return [a for a in self if is_descendant(a.name, name)]

    def by_type(self, account_type: AccountType) -> list[Account]:
        return [a for a in self if a.type is account_type]

    def match(self, pattern: str) -> list[Account]:
        """Glob-style match (``Expenses:*``, ``*:Bank:*``)."""
        return [a for a in self if fnmatch.fnmatchcase(a.name, pattern)]

    def open_on(self, day: date) -> list[Account]:
        return [a for a in self if a.is_open_on(day)]

    def all_names_with_parents(self) -> list[str]:
        names: set[str] = set()
        for name in self._accounts:
            names.add(name)
            names.update(ancestors(name))
        return sorted(names)

    def tree(self) -> dict[str, dict]:
        root: dict[str, dict] = {}
        for name in self.names():
            node = root
            for segment in split_name(name):
                node = node.setdefault(segment, {})
        return root

    def check_posting(self, name: str, currency: str, on_date: date) -> Account:
        """Validate that a posting to ``name`` in ``currency`` on ``on_date`` is allowed."""
        account = self.get(name)
        if on_date < account.opened:
            raise AccountClosedError(
                f"account {name} is not open yet on {on_date.isoformat()} "
                f"(opened {account.opened.isoformat()})"
            )
        if account.closed is not None and on_date > account.closed:
            raise AccountClosedError(
                f"account {name} was closed on {account.closed.isoformat()}"
            )
        if not account.allows(currency):
            raise CurrencyNotAllowedError(
                f"account {name} does not accept {currency} "
                f"(allowed: {', '.join(sorted(account.currencies))})"
            )
        return account

    def to_list(self) -> list[dict[str, object]]:
        return [a.to_dict() for a in self]

    @classmethod
    def from_list(cls, items: Iterable[dict[str, object]]) -> ChartOfAccounts:
        return cls(Account.from_dict(item) for item in items)


DEFAULT_ACCOUNTS = (
    "Assets:Cash",
    "Assets:Bank:Checking",
    "Assets:Bank:Savings",
    "Assets:Receivables",
    "Liabilities:CreditCard",
    "Liabilities:Payables",
    "Liabilities:Tax",
    "Equity:Opening-Balances",
    "Equity:RetainedEarnings",
    "Income:Salary",
    "Income:Sales",
    "Income:Interest",
    "Expenses:Food",
    "Expenses:Rent",
    "Expenses:Utilities",
    "Expenses:Travel",
    "Expenses:Office",
    "Expenses:Taxes",
    "Expenses:Fees",
    "Expenses:Uncategorized",
)


def default_chart(on_date: date) -> ChartOfAccounts:
    chart = ChartOfAccounts()
    for name in DEFAULT_ACCOUNTS:
        chart.open(name, on_date)
    return chart
