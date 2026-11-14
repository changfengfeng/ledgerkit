# Architecture

```
            +-----------+      +-----------+
 text  ---> |  parser   | ---> |           | ---> reports (text/CSV/JSON)
 CSV/OFX -> | importers | ---> |  Journal  | ---> exporters (CSV/JSON/ledger)
 HTTP  ---> |  server   | ---> |           | <--> storage (SQLite/JSONL)
            +-----------+      +-----------+
                                 |  chart of accounts
                                 |  rate table
                                 |  transactions
```

## Core model

- `Money` is immutable: a `Decimal` amount plus an ISO-like currency code.
  Arithmetic between different currencies raises `CurrencyMismatchError`.
- `MoneyBag` is a mutable per-currency accumulator used for balances.
- `Posting` = account + `Money` (+ optional per-unit price).
- `Transaction` = date, payee, narration, flag, tags, links, metadata and
  postings.  `validate()` enforces at least two postings and a zero residual.
- `Journal` owns a `ChartOfAccounts`, a `RateTable` and the transactions,
  assigns ids, enforces the lock date and publishes `add`, `replace`, `void`
  and `remove` events to subscribers.

## Voiding

Booked transactions are never edited in place for corrections.  `void()`
marks the original as voided and books a reversal that mirrors every posting
with the opposite sign, dated on the void date.  The original keeps its
postings so the audit trail shows both entries and the net effect is zero.

## Exchange rates

`RateTable.get_rate(base, quote, on_date)` uses the most recent rate on or
before `on_date`.  If no direct pair exists it tries the inverse, then a
triangulation through the pivot currency.  Lookups are memoised.

Reports that convert to a single currency convert each posting at the rate
in effect on its transaction date (income statement) or at the report date
(balance sheet).

## Storage

`Repository.save()` writes a full snapshot; `attach()` subscribes to journal
events for incremental persistence.  The SQLite backend stores amounts in
their textual decimal representation so that values round-trip exactly; the
JSONL backend serialises transactions with `Transaction.to_dict()`.

## Error handling

All domain errors derive from `LedgerError` and carry a machine-readable
`code`; the CLI maps them to exit status 2 and the HTTP API to status 422
(404 for unknown transactions).
