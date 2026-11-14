# ledgerkit

A dependency-free double-entry bookkeeping engine for Python 3.10+.

ledgerkit keeps a journal of balanced transactions, converts between
currencies, produces the usual financial statements, imports bank exports and
persists everything to SQLite or an append-only JSON log.  It can be used as a
library, from the command line, or through a small JSON HTTP API.

## Features

- **Exact money arithmetic** – `Money` wraps `Decimal` with per-currency
  precision, banker's rounding and remainder-preserving allocation.
- **Multi-currency** – `MoneyBag` holds mixed balances; `RateTable` resolves
  direct, inverse and pivot-triangulated rates by date.
- **Chart of accounts** – five root types (`Assets`, `Liabilities`, `Equity`,
  `Income`, `Expenses`), open/close dates and currency constraints.
- **Journal** – validation, period locking, void-with-reversal, change events
  and flexible filtering.
- **Text ledger format** – a Beancount-like syntax with amount inference,
  `@` / `@@` prices, tags, links, metadata, balance assertions and includes.
- **Reports** – trial balance, income statement, balance sheet, cash flow,
  account register and receivables aging; rendered as text, CSV or JSON.
- **Importers** – configurable CSV and OFX importers, regex categorisation
  rules and duplicate detection.
- **Budgets, taxes, recurring transactions, bank reconciliation** and a
  small **query language** (`account:Expenses and amount>100`).
- **Storage** – SQLite (`.db`) and JSON-lines (`.jsonl`) repositories.

## Installation

```bash
pip install .            # or: pip install -e ".[dev]" for development
```

## Quick start (library)

```python
from datetime import date
from ledgerkit import Journal, TransactionBuilder, default_chart, balance
from ledgerkit.reports import income_statement

journal = Journal(default_chart(date(2024, 1, 1)))
journal.add(
    TransactionBuilder(date(2024, 1, 5), "Employer", "January salary")
    .debit("Assets:Bank:Checking", "3000", "USD")
    .credit("Income:Salary", "3000", "USD")
    .build()
)
journal.add(
    TransactionBuilder(date(2024, 1, 7), "Landlord", "Rent")
    .debit("Expenses:Rent", "1200", "USD")
    .credit("Assets:Bank:Checking", "1200", "USD")
    .build()
)

print(balance(journal, "Assets"))          # 1800.00 USD
print(income_statement(journal, date(2024, 1, 1), date(2024, 1, 31)).to_text())
```

## Quick start (CLI)

```bash
ledgerkit -f examples/sample.ledger check
ledgerkit -f examples/sample.ledger balance --depth 2
ledgerkit -f examples/sample.ledger report income-statement --period 2024-Q1
ledgerkit -f examples/sample.ledger report balance-sheet --currency USD
ledgerkit -f examples/sample.ledger query 'account:Expenses and amount>50'
ledgerkit -f examples/sample.ledger --auto-open import examples/bank.csv \
    --account Assets:Bank:Checking --rules examples/rules.json --dry-run
ledgerkit -f examples/sample.ledger budget examples/budget.json --period 2024-Q1
ledgerkit -f examples/sample.ledger export --format json -o books.json
```

Use `--db books.db` instead of `-f` to work against a SQLite repository; the
first `export`/`import`/`void` writes the database.

## HTTP API

```bash
ledgerkit -f examples/sample.ledger serve --port 8765
curl localhost:8765/balances
curl 'localhost:8765/reports/income-statement?period=2024&currency=USD'
```

See `docs/api.md` for every endpoint.

## Project layout

```
src/ledgerkit/
  money.py, currency.py         amounts and exchange rates
  accounts.py, transactions.py  chart of accounts and transactions
  journal.py, balances.py       the journal and balance queries
  periods.py, budget.py, tax.py, recurring.py, reconcile.py, query.py
  parser/                       lexer, parser, loader and printer
  reports/                      financial statements
  importers/, exporters/        bank imports, CSV/JSON exports
  storage/                      SQLite and JSONL repositories
  cli.py, server.py             command line and HTTP front-ends
tests/                          pytest suite
docs/                           format and API documentation
examples/                       sample ledger, bank CSV, rules, budget
```

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

## License

MIT
