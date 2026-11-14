"""Bank reconciliation: match statement lines against ledger postings."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from .accounts import is_descendant
from .errors import ReconciliationError
from .journal import Journal
from .money import Money
from .transactions import Posting, Transaction
from .utils.text import similarity


@dataclass(frozen=True)
class StatementLine:
    date: date
    amount: Money
    description: str = ""
    reference: str = ""


@dataclass(frozen=True)
class Match:
    line: StatementLine
    transaction: Transaction
    posting: Posting
    score: float


@dataclass
class ReconciliationResult:
    account: str
    matches: list[Match] = field(default_factory=list)
    unmatched_statement: list[StatementLine] = field(default_factory=list)
    unmatched_ledger: list[tuple[Transaction, Posting]] = field(default_factory=list)
    statement_balance: Money | None = None
    ledger_balance: Money | None = None

    @property
    def difference(self) -> Money | None:
        if self.statement_balance is None or self.ledger_balance is None:
            return None
        return self.statement_balance - self.ledger_balance

    @property
    def is_reconciled(self) -> bool:
        diff = self.difference
        return (
            not self.unmatched_statement
            and not self.unmatched_ledger
            and (diff is None or diff.is_zero())
        )

    def summary(self) -> dict[str, object]:
        return {
            "account": self.account,
            "matched": len(self.matches),
            "unmatched_statement": len(self.unmatched_statement),
            "unmatched_ledger": len(self.unmatched_ledger),
            "difference": str(self.difference) if self.difference is not None else None,
            "reconciled": self.is_reconciled,
        }


class Reconciler:
    """Greedy best-score matcher.

    A candidate pair must have exactly the same amount and dates no more than
    ``date_tolerance`` days apart.  Among candidates the score favours close
    dates, then similar descriptions, then matching references.
    """

    def __init__(self, journal: Journal, account: str, date_tolerance: int = 3) -> None:
        if date_tolerance < 0:
            raise ReconciliationError("date_tolerance must be >= 0")
        self.journal = journal
        self.account = account
        self.date_tolerance = date_tolerance

    def _score(self, line: StatementLine, tx: Transaction, posting: Posting) -> float | None:
        if posting.amount != line.amount:
            return None
        gap = abs((tx.date - line.date).days)
        if gap > self.date_tolerance:
            return None
        score = 1.0 - gap / (self.date_tolerance + 1)
        if line.description:
            score += 0.5 * similarity(line.description, tx.description)
        if line.reference and line.reference in (tx.meta.get("ref"), posting.meta.get("ref")):
            score += 1.0
        return score

    def reconcile(
        self,
        lines: Iterable[StatementLine],
        start: date | None = None,
        end: date | None = None,
        statement_balance: Money | None = None,
    ) -> ReconciliationResult:
        statement = sorted(lines, key=lambda line: line.date)
        window = timedelta(days=self.date_tolerance)
        if start is None and statement:
            start = statement[0].date - window
        if end is None and statement:
            end = statement[-1].date + window
        candidates: list[tuple[Transaction, Posting]] = []
        for tx in self.journal.transactions(start=start, end=end):
            for posting in tx.postings:
                if is_descendant(posting.account, self.account):
                    candidates.append((tx, posting))

        scored: list[tuple[float, int, int]] = []
        for li, line in enumerate(statement):
            for ci, (tx, posting) in enumerate(candidates):
                score = self._score(line, tx, posting)
                if score is not None:
                    scored.append((score, li, ci))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))

        used_lines: set[int] = set()
        used_candidates: set[int] = set()
        result = ReconciliationResult(account=self.account)
        for score, li, ci in scored:
            if li in used_lines or ci in used_candidates:
                continue
            used_lines.add(li)
            used_candidates.add(ci)
            tx, posting = candidates[ci]
            result.matches.append(Match(statement[li], tx, posting, round(score, 4)))
        result.matches.sort(key=lambda m: (m.line.date, m.transaction.id or ""))
        result.unmatched_statement = [line for i, line in enumerate(statement) if i not in used_lines]
        result.unmatched_ledger = [c for i, c in enumerate(candidates) if i not in used_candidates]

        if statement_balance is not None:
            result.statement_balance = statement_balance
            total = Money.zero(statement_balance.currency)
            for tx in self.journal.transactions(end=end):
                for posting in tx.postings:
                    if is_descendant(posting.account, self.account) and posting.currency == statement_balance.currency:
                        total = total + posting.amount
            result.ledger_balance = total
        return result
