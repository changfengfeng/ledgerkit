"""Line-oriented tokenizer for the ledger text format."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ..errors import ParseError


class TokenType(str, Enum):
    DATE = "DATE"
    STRING = "STRING"
    ACCOUNT = "ACCOUNT"
    NUMBER = "NUMBER"
    CURRENCY = "CURRENCY"
    FLAG = "FLAG"
    TAG = "TAG"
    LINK = "LINK"
    KEY = "KEY"
    KEYWORD = "KEYWORD"
    AT = "AT"
    ATAT = "ATAT"
    COMMA = "COMMA"


KEYWORDS = {"open", "close", "rate", "option", "include", "commodity", "txn", "balance", "note"}

_TOKEN_SPECS = [
    (TokenType.DATE, r"\d{4}-\d{2}-\d{2}(?![\w-])"),
    (TokenType.STRING, r'"(?:[^"\\]|\\.)*"'),
    (TokenType.ACCOUNT, r"(?:Assets|Liabilities|Equity|Income|Expenses)(?::[A-Z0-9][A-Za-z0-9\-]*)+"),
    (TokenType.KEY, r"[a-z][a-zA-Z0-9_-]*:(?=\s|$)"),
    (TokenType.NUMBER, r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w.])"),
    (TokenType.ATAT, r"@@"),
    (TokenType.AT, r"@"),
    (TokenType.COMMA, r","),
    (TokenType.TAG, r"#[A-Za-z0-9_\-/.]+"),
    (TokenType.LINK, r"\^[A-Za-z0-9_\-/.]+"),
    (TokenType.FLAG, r"[*!](?=\s|$)"),
    (TokenType.CURRENCY, r"[A-Z][A-Z0-9_.'-]{0,22}[A-Z0-9](?![a-z])|[A-Z](?![A-Za-z0-9])"),
    (TokenType.KEYWORD, r"[a-z]+(?![\w:])"),
]

_MASTER_RE = re.compile("|".join(f"(?P<{t.value}>{p})" for t, p in _TOKEN_SPECS))


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    column: int


@dataclass(frozen=True)
class Line:
    number: int
    indent: int
    tokens: list[Token]
    comment: str
    raw: str

    @property
    def is_blank(self) -> bool:
        return not self.tokens


def strip_comment(text: str) -> tuple[str, str]:
    """Split ``text`` at the first ``;`` that is not inside a string."""
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if char == ";" and not in_string:
            return text[:index], text[index + 1:].strip()
    return text, ""


def tokenize_line(text: str, number: int = 0, source: str | None = None) -> Line:
    raw = text.rstrip("\n").rstrip("\r")
    expanded = raw.replace("\t", "    ")
    indent = len(expanded) - len(expanded.lstrip(" "))
    body, comment = strip_comment(expanded)
    tokens: list[Token] = []
    position = indent
    length = len(body)
    while position < length:
        if body[position].isspace():
            position += 1
            continue
        match = _MASTER_RE.match(body, position)
        if not match:
            snippet = body[position:position + 20]
            raise ParseError(f"unexpected input {snippet!r}", line=number, source=source)
        kind = TokenType(match.lastgroup)
        value = match.group()
        if kind is TokenType.KEYWORD and value not in KEYWORDS:
            raise ParseError(f"unknown keyword {value!r}", line=number, source=source)
        tokens.append(Token(kind, value, position))
        position = match.end()
    return Line(number=number, indent=indent, tokens=tokens, comment=comment, raw=raw)


def is_comment_or_blank(text: str) -> bool:
    stripped = text.strip()
    return not stripped or stripped.startswith((";", "%"))


def tokenize(text: str, source: str | None = None) -> list[Line]:
    lines = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if is_comment_or_blank(raw):
            comment = raw.strip().lstrip(";% ")
            lines.append(Line(number=number, indent=0, tokens=[], comment=comment, raw=raw))
            continue
        lines.append(tokenize_line(raw, number, source))
    return lines
