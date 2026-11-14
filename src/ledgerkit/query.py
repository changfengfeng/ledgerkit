"""A tiny filter language for postings.

Grammar (informal)::

    expr    := or_expr
    or_expr := and_expr ("or" and_expr)*
    and_expr:= unary (["and"] unary)*
    unary   := "not" unary | "(" expr ")" | term
    term    := FIELD OP VALUE | WORD

Fields: ``account``, ``payee``, ``narration``, ``tag``, ``link``, ``date``,
``amount``, ``currency``, ``flag``, ``memo``, ``id``, ``voided``.

Operators: ``:`` (contains / prefix for accounts), ``=``, ``!=``, ``~`` (regex),
``>``, ``>=``, ``<``, ``<=``.  A bare word matches payee or narration.

Examples::

    account:Expenses:Food amount>20
    payee~"^(Star|Blue) ?bucks" and date>=2024-01-01
    tag:travel or tag:business
    not voided
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date

from .accounts import is_descendant
from .errors import QueryError
from .journal import Journal
from .money import to_decimal
from .transactions import Posting, Transaction
from .utils.dates import DateParseError, parse_date

Predicate = Callable[[Transaction, Posting], bool]

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<term>[A-Za-z_]+(?:!=|>=|<=|[:=~<>])(?:"(?:[^"\\]|\\.)*"|[^\s()]+))
      | (?P<string>"(?:[^"\\]|\\.)*")
      | (?P<word>[^\s()"]+)
    )
    """,
    re.VERBOSE,
)

_TERM_RE = re.compile(r"^([A-Za-z_]+)(!=|>=|<=|[:=~<>])(.*)$", re.S)

FIELDS = {
    "account",
    "payee",
    "narration",
    "desc",
    "tag",
    "link",
    "date",
    "amount",
    "currency",
    "flag",
    "memo",
    "id",
    "voided",
}


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    position: int


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    position = 0
    while position < len(text):
        if text[position:].strip() == "":
            break
        match = _TOKEN_RE.match(text, position)
        if not match or match.end() == position:
            raise QueryError(f"unexpected character at {position}: {text[position:position + 10]!r}")
        kind = match.lastgroup or "word"
        value = match.group(kind)
        tokens.append(Token(kind, value, match.start(kind)))
        position = match.end()
    return tokens


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def _compare(op: str, left, right) -> bool:
    if op in ("=", ":"):
        return left == right
    if op == "!=":
        return left != right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    raise QueryError(f"operator {op!r} not supported here")


def _text_predicate(getter: Callable[[Transaction, Posting], str], op: str, value: str) -> Predicate:
    if op == "~":
        try:
            pattern = re.compile(value, re.IGNORECASE)
        except re.error as exc:
            raise QueryError(f"invalid regular expression {value!r}: {exc}") from None
        return lambda tx, p: bool(pattern.search(getter(tx, p)))
    lowered = value.lower()
    if op == ":":
        return lambda tx, p: lowered in getter(tx, p).lower()
    if op == "=":
        return lambda tx, p: getter(tx, p).lower() == lowered
    if op == "!=":
        return lambda tx, p: getter(tx, p).lower() != lowered
    raise QueryError(f"operator {op!r} is not valid for text fields")


def build_term(field: str, op: str, raw_value: str) -> Predicate:
    field = field.lower()
    value = _unquote(raw_value)
    if field not in FIELDS:
        raise QueryError(f"unknown field {field!r}; expected one of {', '.join(sorted(FIELDS))}")
    if field == "account":
        if op == ":":
            return lambda tx, p: is_descendant(p.account, value)
        return _text_predicate(lambda tx, p: p.account, op, value)
    if field == "payee":
        return _text_predicate(lambda tx, p: tx.payee, op, value)
    if field == "narration":
        return _text_predicate(lambda tx, p: tx.narration, op, value)
    if field == "desc":
        return _text_predicate(lambda tx, p: tx.description, op, value)
    if field == "memo":
        return _text_predicate(lambda tx, p: p.memo, op, value)
    if field == "id":
        return _text_predicate(lambda tx, p: tx.id or "", op, value)
    if field == "flag":
        return _text_predicate(lambda tx, p: tx.flag, op, value)
    if field == "currency":
        return _text_predicate(lambda tx, p: p.currency, op, value.upper())
    if field == "tag":
        tag = value.lstrip("#")
        if op in (":", "="):
            return lambda tx, p: tag in tx.tags
        if op == "!=":
            return lambda tx, p: tag not in tx.tags
        raise QueryError("tag supports only ':', '=' and '!='")
    if field == "link":
        link = value.lstrip("^")
        if op in (":", "="):
            return lambda tx, p: link in tx.links
        if op == "!=":
            return lambda tx, p: link not in tx.links
        raise QueryError("link supports only ':', '=' and '!='")
    if field == "date":
        if op == "~":
            raise QueryError("date does not support '~'")
        try:
            when: date = parse_date(value)
        except DateParseError as exc:
            raise QueryError(str(exc)) from None
        return lambda tx, p: _compare(op, tx.date, when)
    if field == "amount":
        if op == "~":
            raise QueryError("amount does not support '~'")
        try:
            number = to_decimal(value)
        except Exception as exc:  # noqa: BLE001
            raise QueryError(f"invalid amount {value!r}: {exc}") from None
        return lambda tx, p: _compare(op, abs(p.amount.amount), abs(number))
    if field == "voided":
        expected = value.lower() in ("1", "true", "yes", "y")
        return lambda tx, p: (tx.voided or tx.reversal_of is not None) == expected
    raise QueryError(f"unhandled field {field!r}")  # pragma: no cover


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self) -> Token | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> Token:
        token = self.peek()
        if token is None:
            raise QueryError("unexpected end of query")
        self.index += 1
        return token

    def is_keyword(self, token: Token | None, keyword: str) -> bool:
        return token is not None and token.kind == "word" and token.value.lower() == keyword

    def parse(self) -> Predicate:
        if not self.tokens:
            return lambda tx, p: True
        predicate = self.parse_or()
        if self.peek() is not None:
            token = self.peek()
            assert token is not None
            raise QueryError(f"unexpected token {token.value!r} at {token.position}")
        return predicate

    def parse_or(self) -> Predicate:
        parts = [self.parse_and()]
        while self.is_keyword(self.peek(), "or"):
            self.take()
            parts.append(self.parse_and())
        if len(parts) == 1:
            return parts[0]
        return lambda tx, p: any(part(tx, p) for part in parts)

    def parse_and(self) -> Predicate:
        parts = [self.parse_unary()]
        while True:
            token = self.peek()
            if token is None or token.kind == "rparen" or self.is_keyword(token, "or"):
                break
            if self.is_keyword(token, "and"):
                self.take()
            parts.append(self.parse_unary())
        if len(parts) == 1:
            return parts[0]
        return lambda tx, p: all(part(tx, p) for part in parts)

    def parse_unary(self) -> Predicate:
        token = self.take()
        if self.is_keyword(token, "not"):
            inner = self.parse_unary()
            return lambda tx, p: not inner(tx, p)
        if token.kind == "lparen":
            inner = self.parse_or()
            closing = self.take()
            if closing.kind != "rparen":
                raise QueryError(f"expected ')' at {closing.position}")
            return inner
        if token.kind == "rparen":
            raise QueryError(f"unexpected ')' at {token.position}")
        if token.kind == "term":
            match = _TERM_RE.match(token.value)
            assert match is not None
            return build_term(*match.groups())
        if token.kind == "word" and token.value.lower() == "voided":
            return build_term("voided", "=", "true")
        text = _unquote(token.value).lower()
        return lambda tx, p: text in tx.payee.lower() or text in tx.narration.lower()


def compile_query(text: str) -> Predicate:
    return _Parser(tokenize(text)).parse()


def run_query(
    journal: Journal, text: str, transactions: Iterable[Transaction] | None = None
) -> Iterator[tuple[Transaction, Posting]]:
    predicate = compile_query(text)
    for tx in transactions if transactions is not None else journal.sorted():
        for posting in tx.postings:
            if predicate(tx, posting):
                yield tx, posting


def matching_transactions(journal: Journal, text: str) -> list[Transaction]:
    seen: dict[str, Transaction] = {}
    for tx, _posting in run_query(journal, text):
        seen.setdefault(tx.id or str(id(tx)), tx)
    return list(seen.values())
