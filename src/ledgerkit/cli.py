"""Command line interface: ``ledgerkit <command> [options]``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from . import __version__
from .balances import balances_by_account, rollup, signed_for_display
from .budget import Budget, compare
from .errors import LedgerError
from .exporters import export_json, export_postings_csv
from .importers import CsvImporter, CsvMapping, OfxImporter, RuleSet, import_into
from .journal import Journal
from .parser import dump_journal, load_file
from .periods import FiscalCalendar, close_period
from .query import run_query
from .reports import aging, balance_sheet, cash_flow, income_statement, register, trial_balance
from .storage import open_repository
from .utils.dates import parse_date, parse_period
from .utils.text import render_table


def _load(args: argparse.Namespace) -> Journal:
    if getattr(args, "db", None):
        repo = open_repository(args.db)
        if repo.exists():
            return repo.load()
    if not args.file:
        raise LedgerError("no ledger file given (use -f FILE or --db PATH)")
    result = load_file(args.file, strict=not args.lenient, auto_open=args.auto_open)
    for error in result.errors:
        print(f"warning: {error}", file=sys.stderr)
    return result.journal


def _save(args: argparse.Namespace, journal: Journal) -> None:
    if getattr(args, "db", None):
        open_repository(args.db).save(journal)
    elif args.file:
        Path(args.file).write_text(dump_journal(journal), encoding="utf-8")


def _period(args: argparse.Namespace) -> tuple[date, date]:
    if args.period:
        return parse_period(args.period)
    span = args.journal.date_range()
    if span is None:
        today = date.today()
        return today, today
    return span


# -- commands ----------------------------------------------------------------------
def cmd_check(args: argparse.Namespace) -> int:
    result = load_file(args.file, strict=False, auto_open=args.auto_open)
    for error in result.errors:
        print(f"error: {error}")
    print(f"{len(result.journal)} transactions, {len(result.journal.chart)} accounts, {len(result.errors)} errors")
    return 1 if result.errors else 0


def cmd_balance(args: argparse.Namespace) -> int:
    journal = args.journal
    as_of = parse_date(args.as_of) if args.as_of else None
    leaf = balances_by_account(journal, end=as_of)
    rolled = rollup(leaf) if not args.flat else leaf
    rows = []
    for name, bag in rolled.items():
        if args.account and not name.startswith(args.account):
            continue
        if args.depth and name.count(":") >= args.depth:
            continue
        if bag.is_zero() and not args.empty:
            continue
        shown = signed_for_display(name, bag) if args.natural else bag
        if args.currency:
            shown_text = journal.rates.convert_bag(shown, args.currency, as_of).format()
        else:
            shown_text = str(shown)
        rows.append(["  " * name.count(":") + name if not args.flat else name, shown_text])
    print(render_table(["Account", "Balance"], rows, ["left", "right"]))
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    start, end = parse_period(args.period) if args.period else (None, None)
    print(register(args.journal, args.account, args.currency, start, end).render(args.format), end="")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    journal = args.journal
    name = args.name
    if name == "trial-balance":
        report = trial_balance(journal, parse_date(args.as_of) if args.as_of else None)
    elif name == "income-statement":
        start, end = _period(args)
        report = income_statement(journal, start, end, currency=args.currency, depth=args.depth)
    elif name == "balance-sheet":
        as_of = parse_date(args.as_of) if args.as_of else (_period(args)[1])
        report = balance_sheet(journal, as_of, currency=args.currency, depth=args.depth)
    elif name == "cash-flow":
        start, end = _period(args)
        report = cash_flow(journal, start, end)
    elif name == "aging":
        if not args.account:
            raise LedgerError("aging requires --account")
        as_of = parse_date(args.as_of) if args.as_of else date.today()
        report = aging(journal, args.account, as_of, args.currency or "USD")
    else:  # pragma: no cover - argparse restricts choices
        raise LedgerError(f"unknown report {name}")
    print(report.render(args.format), end="")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    rows = []
    for tx, posting in run_query(args.journal, " ".join(args.query)):
        rows.append([tx.date.isoformat(), tx.id or "", tx.description[:40], posting.account, posting.amount.format()])
    print(render_table(["Date", "Id", "Description", "Account", "Amount"], rows, ["left"] * 4 + ["right"]))
    print(f"\n{len(rows)} postings")
    return 0


def cmd_void(args: argparse.Namespace) -> int:
    journal = args.journal
    reversal = journal.void(args.id, parse_date(args.date) if args.date else None, args.reason or "")
    _save(args, journal)
    print(f"voided {args.id}; reversal {reversal.id}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    journal = args.journal
    rules = RuleSet.load(args.rules) if args.rules else None
    if args.source.lower().endswith((".ofx", ".qfx")):
        lines = OfxImporter(default_currency=args.import_currency).parse_file(args.source)
    else:
        mapping = CsvMapping(
            date=args.date_column,
            description=args.description_column,
            amount=args.amount_column,
            default_currency=args.import_currency,
            date_format=args.date_format,
            delimiter=args.delimiter,
            invert=args.invert,
        )
        lines = CsvImporter(mapping).parse_file(args.source)
    result = import_into(journal, lines, args.account, rules, args.fallback, dry_run=args.dry_run)
    print(json.dumps(result.summary()))
    if not args.dry_run:
        _save(args, journal)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    journal = args.journal
    if args.export_format == "json":
        output = export_json(journal)
    elif args.export_format == "csv":
        output = export_postings_csv(journal)
    else:
        output = dump_journal(journal)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    journal = args.journal
    calendar = FiscalCalendar(args.fiscal_start)
    period = calendar.year_range(args.year)
    result = close_period(journal, period, args.retained_earnings)
    _save(args, journal)
    net = ", ".join(m.format() for m in result.net_income.values()) or "0"
    print(f"closed {period.label} ({period.start}..{period.end}); net income {net}")
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.budget).read_text(encoding="utf-8"))
    budget = Budget(name=data.get("name", "budget"), currency=data.get("currency", "USD"), rollover=bool(data.get("rollover")))
    for line in data.get("lines", []):
        start, end = parse_period(line["period"])
        budget.set_range(line["account"], start, end, line["amount"])
    start, end = _period(args)
    rows = []
    for item in compare(budget, args.journal, start, end):
        pct = f"{item.percent_used}%" if item.percent_used is not None else "-"
        rows.append([item.account, item.period_start.strftime("%Y-%m"), item.available.format(), item.actual.format(), item.remaining.format(), pct])
    print(render_table(["Account", "Period", "Budget", "Actual", "Remaining", "Used"], rows, ["left", "left", "right", "right", "right", "right"]))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    repo = open_repository(args.db) if args.db else None
    serve(args.journal, host=args.host, port=args.port, repository=repo)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledgerkit", description="Double-entry bookkeeping toolkit")
    parser.add_argument("--version", action="version", version=f"ledgerkit {__version__}")
    parser.add_argument("-f", "--file", help="ledger file")
    parser.add_argument("--db", help="SQLite (.db) or JSONL (.jsonl) repository")
    parser.add_argument("--lenient", action="store_true", help="collect errors instead of failing")
    parser.add_argument("--auto-open", action="store_true", help="open unknown accounts on first use")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="validate a ledger file")
    p.set_defaults(func=cmd_check, needs_journal=False)

    p = sub.add_parser("balance", help="account balances")
    p.add_argument("account", nargs="?")
    p.add_argument("--as-of")
    p.add_argument("--depth", type=int)
    p.add_argument("--flat", action="store_true")
    p.add_argument("--empty", action="store_true")
    p.add_argument("--natural", action="store_true", help="show credit-normal accounts as positive")
    p.add_argument("--currency", help="convert to this currency")
    p.set_defaults(func=cmd_balance)

    p = sub.add_parser("register", help="account register")
    p.add_argument("account")
    p.add_argument("--currency", default="USD")
    p.add_argument("--period")
    p.add_argument("--format", choices=["text", "csv", "json"], default="text")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("report", help="financial reports")
    p.add_argument("name", choices=["trial-balance", "income-statement", "balance-sheet", "cash-flow", "aging"])
    p.add_argument("--period")
    p.add_argument("--as-of")
    p.add_argument("--currency")
    p.add_argument("--account")
    p.add_argument("--depth", type=int)
    p.add_argument("--format", choices=["text", "csv", "json"], default="text")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("query", help="filter postings")
    p.add_argument("query", nargs="*")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("void", help="void a transaction")
    p.add_argument("id")
    p.add_argument("--date")
    p.add_argument("--reason")
    p.set_defaults(func=cmd_void)

    p = sub.add_parser("import", help="import a bank CSV/OFX export")
    p.add_argument("source")
    p.add_argument("--account", required=True)
    p.add_argument("--rules")
    p.add_argument("--fallback", default="Expenses:Uncategorized")
    p.add_argument("--currency", dest="import_currency", default="USD")
    p.add_argument("--date-column", default="date")
    p.add_argument("--description-column", default="description")
    p.add_argument("--amount-column", default="amount")
    p.add_argument("--date-format")
    p.add_argument("--delimiter", default=",")
    p.add_argument("--invert", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("export", help="export the journal")
    p.add_argument("--format", dest="export_format", choices=["ledger", "json", "csv"], default="ledger")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("close", help="close a fiscal year")
    p.add_argument("year", type=int)
    p.add_argument("--fiscal-start", type=int, default=1)
    p.add_argument("--retained-earnings", default="Equity:RetainedEarnings")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("budget", help="budget vs actual")
    p.add_argument("budget", help="budget JSON file")
    p.add_argument("--period")
    p.set_defaults(func=cmd_budget)

    p = sub.add_parser("serve", help="run the HTTP API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if getattr(args, "needs_journal", True):
            args.journal = _load(args)
        elif not args.file:
            parser.error("check requires -f FILE")
        return int(args.func(args))
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
