from __future__ import annotations

import json
import threading
import urllib.request
from http import HTTPStatus

import pytest

from ledgerkit.server import LedgerApp, make_server


@pytest.fixture
def app(populated):
    return LedgerApp(populated)


NEW_TX = {
    "date": "2024-03-01",
    "payee": "Shop",
    "narration": "api",
    "postings": [
        {"account": "Expenses:Food", "amount": {"amount": "12.50", "currency": "USD"}},
        {"account": "Assets:Cash", "amount": {"amount": "-12.50", "currency": "USD"}},
    ],
}


def test_health(app):
    status, body = app.handle("GET", "/health", {}, None)
    assert status == HTTPStatus.OK
    assert body["transactions"] == 8


def test_accounts(app):
    status, body = app.handle("GET", "/accounts", {}, None)
    assert status == HTTPStatus.OK
    assert any(a["name"] == "Assets:Cash" for a in body)


def test_list_transactions_filters(app):
    _, body = app.handle("GET", "/transactions", {"account": "Expenses:Rent"}, None)
    assert len(body) == 2
    _, body = app.handle("GET", "/transactions", {"q": "payee:Grocer", "start": "2024-02-01"}, None)
    assert len(body) == 1
    _, body = app.handle("GET", "/transactions", {"limit": "3"}, None)
    assert len(body) == 3


def test_get_transaction(app):
    status, body = app.handle("GET", "/transactions/tx-000001", {}, None)
    assert status == HTTPStatus.OK
    assert body["payee"] == "Opening"


def test_get_missing_transaction(app):
    status, body = app.handle("GET", "/transactions/nope", {}, None)
    assert status == HTTPStatus.NOT_FOUND


def test_create_transaction(app):
    status, body = app.handle("POST", "/transactions", {}, NEW_TX)
    assert status == HTTPStatus.CREATED
    assert body["id"] == "tx-000009"


def test_create_unbalanced(app):
    bad = json.loads(json.dumps(NEW_TX))
    bad["postings"][1]["amount"]["amount"] = "-1"
    status, body = app.handle("POST", "/transactions", {}, bad)
    assert status == HTTPStatus.UNPROCESSABLE_ENTITY


def test_create_malformed(app):
    status, _ = app.handle("POST", "/transactions", {}, {"date": "2024-01-01"})
    assert status in (HTTPStatus.BAD_REQUEST, HTTPStatus.UNPROCESSABLE_ENTITY)


def test_void(app):
    status, body = app.handle("POST", "/transactions/tx-000004/void", {}, {"date": "2024-03-01", "reason": "dup"})
    assert status == HTTPStatus.CREATED
    assert body["reversal_of"] == "tx-000004"


def test_balances(app):
    status, body = app.handle("GET", "/balances", {}, None)
    assert status == HTTPStatus.OK
    assert body["Expenses"] == {"USD": "2650.00"}


@pytest.mark.parametrize("name", ["trial-balance", "balance-sheet", "income-statement", "cash-flow"])
def test_reports(app, name):
    status, body = app.handle("GET", f"/reports/{name}", {"period": "2024"}, None)
    assert status == HTTPStatus.OK
    assert body["rows"]


def test_unknown_report(app):
    status, _ = app.handle("GET", "/reports/nope", {"period": "2024"}, None)
    assert status == HTTPStatus.UNPROCESSABLE_ENTITY


def test_add_rate(app):
    status, body = app.handle("POST", "/rates", {}, {"base": "EUR", "quote": "USD", "date": "2024-01-01", "value": "1.1"})
    assert status == HTTPStatus.CREATED
    assert body["value"] == "1.1"


def test_not_found(app):
    status, _ = app.handle("DELETE", "/health", {}, None)
    assert status == HTTPStatus.NOT_FOUND


def test_http_roundtrip(populated):
    server = make_server(populated, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/health") as response:
            assert json.loads(response.read())["status"] == "ok"
        request = urllib.request.Request(f"{base}/transactions", data=json.dumps(NEW_TX).encode(), method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            assert response.status == 201
        assert len(populated) == 9
    finally:
        server.shutdown()
        server.server_close()
