"""Parser for the ledger text format.

Example::

    option "operating_currency" "USD"

    2024-01-01 open Assets:Bank:Checking USD
    2024-01-01 open Expenses:Food
    2024-01-05 rate EUR USD 1.0950

    2024-01-10 * "Grocer" "Weekly shopping" #food
      receipt: "A-1234"
      Expenses:Food            42.10 USD
      Assets:Bank:Checking              ; amount inferred
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from ..errors import LedgerError, ParseError
from ..money import Money, is_known_currency, register_currency, to_decimal
from ..transactions import Posting, Transaction
from ..utils.text import unquote
from .lexer import Line, Token, TokenType, is_comment_or_blank, tokenize, tokenize_line


@dataclass
class OpenDirective:
    date: date
    account: str
    currencies: list[str]
    description: str = ""
    line: int = 0


@dataclass
class CloseDirective:
    date: date
    account: str
    line: int = 0


@dataclass
class RateDirective:
    date: date
    base: str
    quote: str
    value: Decimal
    line: int = 0


@dataclass
class CommodityDirective:
    date: date
    code: str
    decimals: int
    line: int = 0


@dataclass
class BalanceAssertion:
    date: date
    account: str
    amount: Money
    line: int = 0


@dataclass
class NoteDirective:
    date: date
    account: str
    text: str
    line: int = 0


Directive = OpenDirective | CloseDirective | RateDirective | CommodityDirective | BalanceAssertion | NoteDirective


@dataclass
class ParsedFile:
    source: str | None = None
    options: dict[str, str] = field(default_factory=dict)
    directives: list[Directive] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)

    def merge(self, other: ParsedFile) -> None:
        for key, value in other.options.items():
            self.options.setdefault(key, value)
        self.directives.extend(other.directives)
        self.transactions.extend(other.transactions)
        self.errors.extend(other.errors)


@dataclass
class _PendingPosting:
    account: str
    amount: Money | None
    price: Money | None
    total_price: bool
    flag: str
    memo: str
    meta: dict[str, str]
    line: int


class _TokenStream:
    def __init__(self, line: Line, source: str | None) -> None:
        self.line = line
        self.tokens = line.tokens
        self.index = 0
        self.source = source

    def error(self, message: str) -> ParseError:
        return ParseError(message, line=self.line.number, source=self.source)

    def peek(self, offset: int = 0) -> Token | None:
        position = self.index + offset
        return self.tokens[position] if position < len(self.tokens) else None

    def at(self, *types: TokenType) -> bool:
        token = self.peek()
        return token is not None and token.type in types

    def take(self, *types: TokenType) -> Token:
        token = self.peek()
        if token is None:
            expected = "/".join(t.value for t in types) or "token"
            raise self.error(f"expected {expected}, found end of line")
        if types and token.type not in types:
            expected = "/".join(t.value for t in types)
            raise self.error(f"expected {expected}, found {token.value!r}")
        self.index += 1
        return token

    def optional(self, *types: TokenType) -> Token | None:
        return self.take(*types) if self.at(*types) else None

    def done(self) -> bool:
        return self.index >= len(self.tokens)

    def expect_end(self) -> None:
        if not self.done():
            token = self.peek()
            assert token is not None
            raise self.error(f"unexpected {token.value!r}")


def _parse_amount(stream: _TokenStream) -> Money:
    number = stream.take(TokenType.NUMBER)
    currency = stream.take(TokenType.CURRENCY)
    if not is_known_currency(currency.value):
        raise stream.error(f"unknown currency {currency.value!r} (declare it with 'commodity')")
    return Money(to_decimal(number.value), currency.value)


class Parser:
    def __init__(self, text: str, source: str | None = None, strict: bool = True) -> None:
        self.source = source
        self.strict = strict
        self.result = ParsedFile(source=source)
        self.lines = self._tokenize(text)
        self.index = 0

    def _tokenize(self, text: str) -> list[Line]:
        if self.strict:
            return tokenize(text, self.source)
        lines = []
        for number, raw in enumerate(text.splitlines(), start=1):
            if is_comment_or_blank(raw):
                lines.append(Line(number, 0, [], raw.strip().lstrip(";% "), raw))
                continue
            try:
                lines.append(tokenize_line(raw, number, self.source))
            except ParseError as exc:
                self.result.errors.append(exc)
                lines.append(Line(number, 0, [], "", ""))
        return lines

    def fail(self, error: ParseError) -> None:
        if self.strict:
            raise error
        self.result.errors.append(error)

    # -- driver ---------------------------------------------------------------
    def parse(self) -> ParsedFile:
        while self.index < len(self.lines):
            line = self.lines[self.index]
            self.index += 1
            if line.is_blank:
                continue
            if line.indent > 0:
                self.fail(ParseError("unexpected indented line", line=line.number, source=self.source))
                continue
            try:
                self._parse_entry(line)
            except ParseError as exc:
                self.fail(exc)
                self._skip_block()
            except LedgerError as exc:
                self.fail(ParseError(str(exc), line=line.number, source=self.source))
                self._skip_block()
        return self.result

    def _skip_block(self) -> None:
        while self.index < len(self.lines) and (
            self.lines[self.index].indent > 0 or self.lines[self.index].is_blank
        ):
            if self.lines[self.index].is_blank:
                break
            self.index += 1

    def _indented_block(self) -> Iterator[Line]:
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.is_blank and not line.comment:
                return
            if line.is_blank:
                self.index += 1
                continue
            if line.indent == 0:
                return
            self.index += 1
            yield line

    def _parse_entry(self, line: Line) -> None:
        stream = _TokenStream(line, self.source)
        first = stream.peek()
        assert first is not None
        if first.type is TokenType.KEYWORD and first.value == "option":
            stream.take()
            key = unquote(stream.take(TokenType.STRING).value)
            value = unquote(stream.take(TokenType.STRING).value)
            stream.expect_end()
            self.result.options[key] = value
            return
        if first.type is TokenType.KEYWORD and first.value == "include":
            stream.take()
            self.result.includes.append(unquote(stream.take(TokenType.STRING).value))
            stream.expect_end()
            return
        on_date = date.fromisoformat(stream.take(TokenType.DATE).value)
        token = stream.peek()
        if token is None:
            raise stream.error("expected directive after date")
        if token.type is TokenType.KEYWORD:
            keyword = stream.take().value
            handler = getattr(self, f"_directive_{keyword}", None)
            if handler is None:
                raise stream.error(f"keyword {keyword!r} is not valid here")
            handler(stream, on_date)
            return
        if token.type is TokenType.FLAG:
            self._parse_transaction(stream, on_date, stream.take().value)
            return
        raise stream.error(f"unexpected {token.value!r} after date")

    # -- directives -----------------------------------------------------------
    def _directive_open(self, stream: _TokenStream, on_date: date) -> None:
        account = stream.take(TokenType.ACCOUNT).value
        currencies: list[str] = []
        if stream.at(TokenType.CURRENCY):
            currencies.append(stream.take().value)
            while stream.optional(TokenType.COMMA):
                currencies.append(stream.take(TokenType.CURRENCY).value)
        description = ""
        if stream.at(TokenType.STRING):
            description = unquote(stream.take().value)
        stream.expect_end()
        self.result.directives.append(
            OpenDirective(on_date, account, currencies, description, stream.line.number)
        )

    def _directive_close(self, stream: _TokenStream, on_date: date) -> None:
        account = stream.take(TokenType.ACCOUNT).value
        stream.expect_end()
        self.result.directives.append(CloseDirective(on_date, account, stream.line.number))

    def _directive_rate(self, stream: _TokenStream, on_date: date) -> None:
        base = stream.take(TokenType.CURRENCY).value
        quote = stream.take(TokenType.CURRENCY).value
        value = to_decimal(stream.take(TokenType.NUMBER).value)
        stream.expect_end()
        if value <= 0:
            raise stream.error("exchange rate must be positive")
        self.result.directives.append(RateDirective(on_date, base, quote, value, stream.line.number))

    def _directive_commodity(self, stream: _TokenStream, on_date: date) -> None:
        code = stream.take(TokenType.CURRENCY).value
        decimals = 2
        if stream.at(TokenType.NUMBER):
            decimals = int(stream.take().value)
        stream.expect_end()
        if not is_known_currency(code):
            register_currency(code, decimals)
        self.result.directives.append(CommodityDirective(on_date, code, decimals, stream.line.number))

    def _directive_balance(self, stream: _TokenStream, on_date: date) -> None:
        account = stream.take(TokenType.ACCOUNT).value
        amount = _parse_amount(stream)
        stream.expect_end()
        self.result.directives.append(BalanceAssertion(on_date, account, amount, stream.line.number))

    def _directive_note(self, stream: _TokenStream, on_date: date) -> None:
        account = stream.take(TokenType.ACCOUNT).value
        text = unquote(stream.take(TokenType.STRING).value)
        stream.expect_end()
        self.result.directives.append(NoteDirective(on_date, account, text, stream.line.number))

    def _directive_txn(self, stream: _TokenStream, on_date: date) -> None:
        self._parse_transaction(stream, on_date, "*")

    # -- transactions ---------------------------------------------------------
    def _parse_transaction(self, stream: _TokenStream, on_date: date, flag: str) -> None:
        strings: list[str] = []
        while stream.at(TokenType.STRING):
            strings.append(unquote(stream.take().value))
        if len(strings) > 2:
            raise stream.error("a transaction takes at most a payee and a narration")
        if len(strings) == 2:
            payee, narration = strings
        elif len(strings) == 1:
            payee, narration = "", strings[0]
        else:
            payee, narration = "", ""
        tags: set[str] = set()
        links: set[str] = set()
        while not stream.done():
            token = stream.take(TokenType.TAG, TokenType.LINK)
            (tags if token.type is TokenType.TAG else links).add(token.value[1:])
        header_line = stream.line.number
        tx_meta: dict[str, str] = {}
        pending: list[_PendingPosting] = []
        posting_indent: int | None = None
        for line in self._indented_block():
            inner = _TokenStream(line, self.source)
            if inner.at(TokenType.KEY):
                key = inner.take().value[:-1]
                value_token = inner.take()
                value = unquote(value_token.value)
                inner.expect_end()
                if pending and posting_indent is not None and line.indent > posting_indent:
                    pending[-1].meta[key] = value
                else:
                    tx_meta[key] = value
                continue
            if inner.at(TokenType.TAG, TokenType.LINK):
                while not inner.done():
                    token = inner.take(TokenType.TAG, TokenType.LINK)
                    (tags if token.type is TokenType.TAG else links).add(token.value[1:])
                continue
            posting_indent = line.indent if posting_indent is None else posting_indent
            pending.append(self._parse_posting(inner))
        if not pending:
            raise ParseError("transaction has no postings", line=header_line, source=self.source)
        postings = self._complete_postings(pending, header_line)
        tx_meta.setdefault("lineno", str(header_line))
        if self.source:
            tx_meta.setdefault("filename", self.source)
        tx = Transaction(
            date=on_date,
            payee=payee,
            narration=narration,
            postings=postings,
            flag=flag,
            tags=tags,
            links=links,
            meta=tx_meta,
        )
        self.result.transactions.append(tx)

    def _parse_posting(self, stream: _TokenStream) -> _PendingPosting:
        flag = ""
        if stream.at(TokenType.FLAG):
            flag = stream.take().value
        account = stream.take(TokenType.ACCOUNT).value
        amount: Money | None = None
        price: Money | None = None
        total = False
        if stream.at(TokenType.NUMBER):
            amount = _parse_amount(stream)
            if stream.at(TokenType.AT, TokenType.ATAT):
                total = stream.take().type is TokenType.ATAT
                price = _parse_amount(stream)
        stream.expect_end()
        return _PendingPosting(
            account=account,
            amount=amount,
            price=price,
            total_price=total,
            flag=flag,
            memo=stream.line.comment,
            meta={},
            line=stream.line.number,
        )

    def _complete_postings(self, pending: list[_PendingPosting], header_line: int) -> list[Posting]:
        elided = [p for p in pending if p.amount is None]
        if len(elided) > 1:
            raise ParseError(
                "only one posting per transaction may omit its amount",
                line=elided[1].line,
                source=self.source,
            )
        postings: list[Posting] = []
        residual: dict[str, Decimal] = {}
        for item in pending:
            if item.amount is None:
                continue
            price = item.price
            if price is not None and item.total_price:
                if item.amount.is_zero():
                    raise ParseError("total price on zero amount", line=item.line, source=self.source)
                price = Money(price.amount / abs(item.amount.amount), price.currency)
            posting = Posting(
                account=item.account,
                amount=item.amount,
                price=price,
                memo=item.memo,
                flag=item.flag,
                meta=item.meta,
            )
            weight = posting.weight()
            residual[weight.currency] = residual.get(weight.currency, Decimal(0)) + weight.amount
            postings.append(posting)
        if elided:
            item = elided[0]
            open_currencies = [(c, v) for c, v in sorted(residual.items()) if v != 0]
            if not open_currencies:
                raise ParseError(
                    f"cannot infer amount for {item.account}: other postings already balance",
                    line=item.line,
                    source=self.source,
                )
            insert_at = pending.index(item)
            inferred = [
                Posting(item.account, Money(-value, code), memo=item.memo, flag=item.flag, meta=dict(item.meta))
                for code, value in open_currencies
            ]
            postings[insert_at:insert_at] = inferred
        return postings


def parse_string(text: str, source: str | None = None, strict: bool = True) -> ParsedFile:
    return Parser(text, source=source, strict=strict).parse()


def parse_file(path: str | Path, strict: bool = True, _seen: set[Path] | None = None) -> ParsedFile:
    """Parse ``path`` and every file it includes (relative to the including file)."""
    resolved = Path(path).resolve()
    seen = _seen if _seen is not None else set()
    if resolved in seen:
        raise ParseError(f"include cycle detected at {resolved}")
    seen.add(resolved)
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParseError(f"cannot read {resolved}: {exc.strerror}") from None
    result = parse_string(text, source=str(path), strict=strict)
    for include in result.includes:
        child = (resolved.parent / include) if not Path(include).is_absolute() else Path(include)
        result.merge(parse_file(child, strict=strict, _seen=seen))
    return result
