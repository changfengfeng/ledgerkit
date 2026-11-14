# HTTP API

`ledgerkit serve` exposes the journal as JSON.  All responses are
`application/json`; errors have the shape `{"error": code, "message": text}`.

| Method | Path                          | Description                               |
|--------|-------------------------------|-------------------------------------------|
| GET    | `/health`                     | Status and transaction count              |
| GET    | `/accounts`                   | Chart of accounts                         |
| GET    | `/transactions`               | List; `start`, `end`, `account`, `q`, `limit` |
| GET    | `/transactions/<id>`          | One transaction                           |
| POST   | `/transactions`               | Create; body is a transaction without id  |
| POST   | `/transactions/<id>/void`     | Void; body `{"date", "reason"}`           |
| GET    | `/balances`                   | Rolled-up balances; `as_of`, `currency`   |
| GET    | `/reports/<name>`             | `trial-balance`, `balance-sheet`, `income-statement`, `cash-flow`; `period`, `as_of`, `currency` |
| POST   | `/rates`                      | Add a rate `{"base","quote","date","value"}` |

## Transaction JSON

```json
{
  "date": "2024-03-01",
  "payee": "Shop",
  "narration": "Lunch",
  "flag": "*",
  "tags": ["work"],
  "links": [],
  "meta": {"ref": "R-1"},
  "postings": [
    {"account": "Expenses:Food", "amount": {"amount": "12.50", "currency": "USD"}},
    {"account": "Assets:Cash",   "amount": {"amount": "-12.50", "currency": "USD"}}
  ]
}
```

Status codes: `200` OK, `201` created, `400` malformed request, `404` unknown
route or transaction, `413` body too large, `422` ledger rule violated (for
example an unbalanced transaction or a locked period).

When started with `--db`, every successful write is persisted to the
repository before the response is sent.
