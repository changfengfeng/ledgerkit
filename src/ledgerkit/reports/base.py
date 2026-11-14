"""Common report model and renderers."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..money import Money, MoneyBag
from ..utils.text import SEPARATOR_ROW, render_table


def format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Money):
        return value.format()
    if isinstance(value, MoneyBag):
        return str(value) if len(value) else "0"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def json_cell(value: Any) -> Any:
    if isinstance(value, Money):
        return value.to_dict()
    if isinstance(value, MoneyBag):
        return value.to_dict()
    if isinstance(value, date):
        return value.isoformat()
    return value


@dataclass
class ReportRow:
    cells: list[Any]
    level: int = 0
    kind: str = "data"  # data | header | total | separator

    @classmethod
    def separator(cls) -> ReportRow:
        return cls(cells=[], kind="separator")


@dataclass
class Report:
    title: str
    columns: list[str]
    rows: list[ReportRow] = field(default_factory=list)
    aligns: list[str] | None = None
    subtitle: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def add(self, *cells: Any, level: int = 0, kind: str = "data") -> ReportRow:
        row = ReportRow(list(cells), level=level, kind=kind)
        self.rows.append(row)
        return row

    def add_separator(self) -> None:
        self.rows.append(ReportRow.separator())

    def data_rows(self) -> list[ReportRow]:
        return [r for r in self.rows if r.kind == "data"]

    def find(self, label: str) -> ReportRow | None:
        for row in self.rows:
            if row.cells and str(row.cells[0]).strip() == label:
                return row
        return None

    # -- renderers ------------------------------------------------------------
    def to_text(self) -> str:
        body = []
        for row in self.rows:
            if row.kind == "separator":
                body.append(SEPARATOR_ROW)
                continue
            cells = [format_cell(c) for c in row.cells]
            if cells:
                cells[0] = "  " * row.level + cells[0]
            body.append(cells)
        aligns = self.aligns or ["left"] + ["right"] * (len(self.columns) - 1)
        header = self.title + (f"\n{self.subtitle}" if self.subtitle else "")
        return header + "\n\n" + render_table(self.columns, body, aligns) + "\n"

    def to_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(self.columns)
        for row in self.rows:
            if row.kind == "separator":
                continue
            writer.writerow([format_cell(c) for c in row.cells])
        return buffer.getvalue()

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "columns": self.columns,
            "meta": {k: json_cell(v) for k, v in self.meta.items()},
            "rows": [
                {"cells": [json_cell(c) for c in row.cells], "level": row.level, "kind": row.kind}
                for row in self.rows
                if row.kind != "separator"
            ],
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def render(self, fmt: str = "text") -> str:
        if fmt == "text":
            return self.to_text()
        if fmt == "csv":
            return self.to_csv()
        if fmt == "json":
            return self.to_json()
        raise ValueError(f"unknown report format: {fmt}")
