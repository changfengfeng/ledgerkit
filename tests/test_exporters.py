from __future__ import annotations

import csv
import io
import json

from ledgerkit.balances import balance
from ledgerkit.exporters import (
    export_json,
    export_postings_csv,
    export_transactions_csv,
    import_json,
)
from ledgerkit.exporters.json_export import journal_to_dict


def test_postings_csv(populated):
    rows = list(csv.DictReader(io.StringIO(export_postings_csv(populated))))
    assert len(rows) == 16
    assert {"date", "account", "amount", "currency"} <= set(rows[0])


def test_transactions_csv(populated):
    rows = list(csv.reader(io.StringIO(export_transactions_csv(populated))))
    assert len(rows) == 9


def test_json_structure(populated):
    data = json.loads(export_json(populated))
    assert data["version"] == 1
    assert len(data["transactions"]) == 8
    assert data["accounts"]


def test_json_roundtrip(sample):
    again = import_json(export_json(sample))
    assert len(again) == len(sample)
    assert again.options == sample.options
    for name in ("Assets", "Liabilities", "Income", "Expenses", "Equity"):
        assert balance(again, name) == balance(sample, name)
    assert len(again.rates) == len(sample.rates)


def test_dict_has_ids(populated):
    data = journal_to_dict(populated)
    assert all(tx["id"] for tx in data["transactions"])
