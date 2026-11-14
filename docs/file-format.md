# Ledger file format

A ledger file is a sequence of dated entries.  Lines starting with `;` are
comments; a `;` after a posting is kept as the posting memo.

## Options and includes

```
option "title" "Household 2024"
option "operating_currency" "USD"
include "accounts.ledger"
```

Included paths are relative to the including file.  Include cycles are an
error.

## Directives

| Directive   | Example                                              |
|-------------|------------------------------------------------------|
| `open`      | `2024-01-01 open Assets:Bank:Checking USD,EUR`       |
| `close`     | `2024-12-31 close Assets:Bank:Old`                   |
| `commodity` | `2024-01-01 commodity AAPL 4`                        |
| `rate`      | `2024-01-01 rate EUR USD 1.1050`                     |
| `balance`   | `2024-02-28 balance Assets:Bank:Checking 5250.00 USD`|
| `note`      | `2024-03-01 note Assets:Cash "counted drawer"`       |

`open` may restrict the currencies an account accepts.  `commodity` registers
a non-ISO unit with the given number of decimals.  `rate` adds an exchange
rate effective from that date until the next rate for the same pair.

A `balance` assertion is checked against the running balance including every
transaction dated on or before the assertion date.

## Transactions

```
2024-02-12 * "Broker" "Buy shares" #invest ^order-17
  order: "17"
  Assets:Brokerage            5 AAPL @ 182.50 USD
    lot: "2024-02"
  Assets:Bank:Savings
```

- The flag is `*` (cleared) or `!` (pending).
- One quoted string is the narration; two are payee and narration.
- `#tag` and `^link` may follow the header.
- `key: "value"` lines directly under the header are transaction metadata;
  indented deeper under a posting they are posting metadata.
- `@` gives a per-unit price, `@@` a total price.
- At most one posting per currency may omit its amount; it is inferred so the
  transaction balances.  If the other postings use several currencies the
  posting is expanded into one leg per currency.

## Balancing rules

The *weight* of a posting is its amount, or amount × price when a price is
present.  Weights must sum to zero per currency.  Postings with a price are
allowed a residual of half a minor unit of the price currency; plain postings
must balance exactly.
