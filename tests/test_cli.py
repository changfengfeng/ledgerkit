from __future__ import annotations

import json

import pytest

from ledgerkit.cli import build_parser, main
from ledgerkit.parser import load_file

CSV = "date,description,amount\n2024-04-02,COFFEE,-4.50\n2024-04-03,PAYROLL,1000.00\n"


@pytest.fixture
def ledger(tmp_path, sample_path):
    path = tmp_path / "books.ledger"
    path.write_text(sample_path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_parser_requires_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_check(capsys, ledger):
    code, out, _ = run(capsys, "-f", str(ledger), "check")
    assert code == 0
    assert "8 transactions" in out


def test_check_reports_errors(capsys, tmp_path):
    bad = tmp_path / "bad.ledger"
    bad.write_text('2024-01-01 open Assets:Cash\n2024-01-02 * "x"\n  Assets:Cash 1 USD\n  Expenses:Food\n', encoding="utf-8")
    code, out, _ = run(capsys, "-f", str(bad), "check")
    assert code == 1
    assert "error" in out


def test_balance(capsys, ledger):
    code, out, _ = run(capsys, "-f", str(ledger), "balance", "Assets", "--depth", "3")
    assert code == 0
    assert "Assets:Bank:Checking" in out
    assert "5,250.00 USD" in out


def test_balance_flat_natural(capsys, ledger):
    code, out, _ = run(capsys, "-f", str(ledger), "balance", "Income", "--flat", "--natural")
    assert code == 0
    assert "4,200.00 USD" in out
    assert "-4,200.00" not in out


def test_register(capsys, ledger):
    code, out, _ = run(capsys, "-f", str(ledger), "register", "Assets:Bank:Checking", "--format", "csv")
    assert code == 0
    assert "Landlord" in out


@pytest.mark.parametrize("name", ["trial-balance", "income-statement", "balance-sheet", "cash-flow"])
def test_reports(capsys, ledger, name):
    code, out, _ = run(capsys, "-f", str(ledger), "report", name)
    assert code == 0
    assert out.strip()


def test_report_json(capsys, ledger):
    code, out, _ = run(capsys, "-f", str(ledger), "report", "trial-balance", "--format", "json")
    assert code == 0
    assert json.loads(out)["title"]


def test_aging_requires_account(capsys, ledger):
    code, _, err = run(capsys, "-f", str(ledger), "report", "aging")
    assert code == 2
    assert "account" in err


def test_query(capsys, ledger):
    code, out, _ = run(capsys, "-f", str(ledger), "query", "account:Expenses:Rent")
    assert code == 0
    assert "1 postings" in out


def test_export_json(capsys, ledger, tmp_path):
    target = tmp_path / "out.json"
    code, _, _ = run(capsys, "-f", str(ledger), "export", "--format", "json", "-o", str(target))
    assert code == 0
    assert len(json.loads(target.read_text(encoding="utf-8"))["transactions"]) == 8


def test_import_dry_run(capsys, ledger, tmp_path):
    csv_path = tmp_path / "bank.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    code, out, _ = run(capsys, "-f", str(ledger), "--auto-open", "import", str(csv_path), "--account", "Assets:Bank:Checking", "--dry-run")
    assert code == 0
    assert json.loads(out)["created"] == 2
    assert len(load_file(ledger).journal) == 8


def test_import_writes_ledger(capsys, ledger, tmp_path):
    csv_path = tmp_path / "bank.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps([{"pattern": "payroll", "account": "Income:Salary"}]), encoding="utf-8")
    code, _, _ = run(capsys, "-f", str(ledger), "--auto-open", "import", str(csv_path), "--account", "Assets:Bank:Checking", "--rules", str(rules))
    assert code == 0
    assert len(load_file(ledger, auto_open=True).journal) == 10


def test_missing_file(capsys):
    code, _, err = run(capsys, "balance")
    assert code == 2
    assert "no ledger file" in err
