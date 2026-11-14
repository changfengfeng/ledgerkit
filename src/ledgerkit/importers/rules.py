"""Rule-based categorisation of imported lines."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import ImporterError
from ..money import to_decimal

if TYPE_CHECKING:
    from .base import ImportedLine


@dataclass
class CategorizationRule:
    """Assign ``account`` to lines whose description matches ``pattern``.

    Optional constraints narrow the rule by amount sign/range and currency.
    """

    pattern: str
    account: str
    payee: str | None = None
    tags: tuple[str, ...] = ()
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    direction: str = "any"  # any | in | out
    currency: str | None = None
    priority: int = 0
    _compiled: re.Pattern[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            self._compiled = re.compile(self.pattern, re.IGNORECASE)
        except re.error as exc:
            raise ImporterError(f"invalid rule pattern {self.pattern!r}: {exc}") from None
        if self.direction not in ("any", "in", "out"):
            raise ImporterError(f"invalid rule direction {self.direction!r}")

    def matches(self, line: ImportedLine) -> bool:
        text = f"{line.payee} {line.description}".strip()
        if not self._compiled.search(text):
            return False
        amount = line.amount.amount
        if self.direction == "in" and amount <= 0:
            return False
        if self.direction == "out" and amount >= 0:
            return False
        if self.min_amount is not None and abs(amount) < self.min_amount:
            return False
        if self.max_amount is not None and abs(amount) > self.max_amount:
            return False
        if self.currency is not None and line.amount.currency != self.currency.upper():
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"pattern": self.pattern, "account": self.account}
        if self.payee:
            data["payee"] = self.payee
        if self.tags:
            data["tags"] = list(self.tags)
        if self.min_amount is not None:
            data["min_amount"] = str(self.min_amount)
        if self.max_amount is not None:
            data["max_amount"] = str(self.max_amount)
        if self.direction != "any":
            data["direction"] = self.direction
        if self.currency:
            data["currency"] = self.currency
        if self.priority:
            data["priority"] = self.priority
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CategorizationRule:
        try:
            return cls(
                pattern=data["pattern"],
                account=data["account"],
                payee=data.get("payee"),
                tags=tuple(data.get("tags") or ()),
                min_amount=to_decimal(data["min_amount"]) if data.get("min_amount") is not None else None,
                max_amount=to_decimal(data["max_amount"]) if data.get("max_amount") is not None else None,
                direction=data.get("direction", "any"),
                currency=data.get("currency"),
                priority=int(data.get("priority", 0)),
            )
        except KeyError as exc:
            raise ImporterError(f"rule is missing field {exc.args[0]!r}") from None


class RuleSet:
    def __init__(self, rules: Iterable[CategorizationRule] = ()) -> None:
        self._rules: list[CategorizationRule] = []
        for rule in rules:
            self.add(rule)

    def add(self, rule: CategorizationRule) -> None:
        self._rules.append(rule)
        # Stable sort: higher priority first, then insertion order.
        self._rules.sort(key=lambda r: -r.priority)

    def match(self, line: ImportedLine) -> CategorizationRule | None:
        for rule in self._rules:
            if rule.matches(line):
                return rule
        return None

    def __len__(self) -> int:
        return len(self._rules)

    def __iter__(self):
        return iter(self._rules)

    def to_json(self) -> str:
        return json.dumps([r.to_dict() for r in self._rules], indent=2)

    @classmethod
    def from_json(cls, text: str) -> RuleSet:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ImporterError(f"invalid rules JSON: {exc}") from None
        if not isinstance(data, list):
            raise ImporterError("rules JSON must be a list")
        return cls(CategorizationRule.from_dict(item) for item in data)

    @classmethod
    def load(cls, path: str | Path) -> RuleSet:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))
