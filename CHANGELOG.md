# Changelog

## 0.4.2
- Reconciler widens the candidate window by `date_tolerance` when the
  statement range is inferred.
- Default chart gains `Expenses:Uncategorized` and `Liabilities:Tax`.

## 0.4.0
- SQLite and JSON-lines repositories with incremental (`attach`) writes.
- HTTP API (`ledgerkit serve`) on top of the standard library.
- Budget rollover and quarterly / yearly budget periods.

## 0.3.0
- OFX importer and rule-based categorisation (`rules.json`).
- Aging report, cash-flow report.
- Query language: `and` / `or` / `not`, parentheses, regex and comparisons.

## 0.2.0
- Text ledger format: parser, loader with balance assertions, printer.
- Multi-currency reports with conversion through a pivot currency.

## 0.1.0
- Core model: `Money`, `MoneyBag`, accounts, transactions, journal.
