"""JSON HTTP API built on :mod:`http.server` (no third-party dependencies).

Endpoints::

    GET  /health
    GET  /accounts
    GET  /transactions?start=&end=&account=&q=
    GET  /transactions/<id>
    POST /transactions                  body: Transaction JSON (without id)
    POST /transactions/<id>/void        body: {"date": "...", "reason": "..."}
    GET  /balances?as_of=&currency=
    GET  /reports/<name>?period=&as_of=&currency=
    POST /rates                         body: {"base","quote","date","value"}
"""

from __future__ import annotations

import json
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .balances import balances_by_account, rollup
from .errors import LedgerError, TransactionNotFoundError
from .journal import Journal
from .query import matching_transactions
from .reports import balance_sheet, cash_flow, income_statement, trial_balance
from .storage.base import Repository
from .transactions import Transaction
from .utils.dates import parse_date, parse_period

_TX_RE = re.compile(r"^/transactions/([^/]+)$")
_VOID_RE = re.compile(r"^/transactions/([^/]+)/void$")
_REPORT_RE = re.compile(r"^/reports/([a-z-]+)$")

MAX_BODY = 1_000_000


class LedgerApp:
    """Request routing and business logic, independent of the HTTP layer."""

    def __init__(self, journal: Journal, repository: Repository | None = None) -> None:
        self.journal = journal
        self.repository = repository
        self.lock = threading.RLock()

    def _persist(self) -> None:
        if self.repository is not None:
            self.repository.save(self.journal)

    def handle(self, method: str, path: str, query: dict[str, str], body: Any) -> tuple[int, Any]:
        try:
            with self.lock:
                return self._route(method, path, query, body)
        except TransactionNotFoundError as exc:
            return HTTPStatus.NOT_FOUND, exc.to_dict()
        except LedgerError as exc:
            return HTTPStatus.UNPROCESSABLE_ENTITY, exc.to_dict()
        except (KeyError, TypeError, ValueError) as exc:
            return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "message": str(exc)}

    def _route(self, method: str, path: str, query: dict[str, str], body: Any) -> tuple[int, Any]:
        if method == "GET" and path == "/health":
            return HTTPStatus.OK, {"status": "ok", "transactions": len(self.journal)}
        if method == "GET" and path == "/accounts":
            return HTTPStatus.OK, self.journal.chart.to_list()
        if path == "/transactions":
            if method == "GET":
                return HTTPStatus.OK, self._list_transactions(query)
            if method == "POST":
                tx = Transaction.from_dict({**body, "id": None})
                self.journal.add(tx)
                self._persist()
                return HTTPStatus.CREATED, tx.to_dict()
        match = _VOID_RE.match(path)
        if match and method == "POST":
            payload = body or {}
            when = parse_date(payload["date"]) if payload.get("date") else None
            reversal = self.journal.void(match.group(1), when, payload.get("reason", ""))
            self._persist()
            return HTTPStatus.CREATED, reversal.to_dict()
        match = _TX_RE.match(path)
        if match and method == "GET":
            return HTTPStatus.OK, self.journal.get(match.group(1)).to_dict()
        if method == "GET" and path == "/balances":
            as_of = parse_date(query["as_of"]) if query.get("as_of") else None
            rolled = rollup(balances_by_account(self.journal, end=as_of))
            if query.get("currency"):
                cur = query["currency"]
                return HTTPStatus.OK, {
                    k: self.journal.rates.convert_bag(v, cur, as_of).to_dict() for k, v in rolled.items()
                }
            return HTTPStatus.OK, {k: v.to_dict() for k, v in rolled.items()}
        match = _REPORT_RE.match(path)
        if match and method == "GET":
            return HTTPStatus.OK, self._report(match.group(1), query)
        if method == "POST" and path == "/rates":
            rate = self.journal.rates.add(body["base"], body["quote"], body["date"], str(body["value"]), "api")
            self._persist()
            return HTTPStatus.CREATED, {"base": rate.base, "quote": rate.quote, "date": rate.date.isoformat(), "value": str(rate.value)}
        return HTTPStatus.NOT_FOUND, {"error": "not_found", "message": f"{method} {path}"}

    def _list_transactions(self, query: dict[str, str]) -> list[dict[str, Any]]:
        start = parse_date(query["start"]) if query.get("start") else None
        end = parse_date(query["end"]) if query.get("end") else None
        if query.get("q"):
            txs = [t for t in matching_transactions(self.journal, query["q"]) if (start is None or t.date >= start) and (end is None or t.date <= end)]
        else:
            txs = self.journal.transactions(start=start, end=end, account=query.get("account"))
        limit = int(query.get("limit", 500))
        return [tx.to_dict() for tx in txs[:limit]]

    def _report(self, name: str, query: dict[str, str]) -> dict[str, Any]:
        currency = query.get("currency")
        if name == "trial-balance":
            as_of = parse_date(query["as_of"]) if query.get("as_of") else None
            return trial_balance(self.journal, as_of).to_dict()
        if name == "balance-sheet":
            as_of = parse_date(query["as_of"]) if query.get("as_of") else (self.journal.date_range() or (None, None))[1]
            if as_of is None:
                raise LedgerError("journal is empty")
            return balance_sheet(self.journal, as_of, currency=currency).to_dict()
        if query.get("period"):
            start, end = parse_period(query["period"])
        else:
            span = self.journal.date_range()
            if span is None:
                raise LedgerError("journal is empty")
            start, end = span
        if name == "income-statement":
            return income_statement(self.journal, start, end, currency=currency).to_dict()
        if name == "cash-flow":
            return cash_flow(self.journal, start, end).to_dict()
        raise LedgerError(f"unknown report {name!r}")


def _make_handler(app: LedgerApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ledgerkit"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return None

        def _send(self, status: int, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            body: Any = None
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "too_large"})
                    return
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else {}
                except json.JSONDecodeError as exc:
                    self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_json", "message": str(exc)})
                    return
            status, payload = app.handle(method, parsed.path.rstrip("/") or "/", query, body)
            self._send(status, payload)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

    return Handler


def make_server(journal: Journal, host: str = "127.0.0.1", port: int = 8765, repository: Repository | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _make_handler(LedgerApp(journal, repository)))


def serve(journal: Journal, host: str = "127.0.0.1", port: int = 8765, repository: Repository | None = None) -> None:
    server = make_server(journal, host, port, repository)
    print(f"ledgerkit API listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        server.server_close()
