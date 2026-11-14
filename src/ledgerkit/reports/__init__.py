"""Financial reports built on top of the journal."""

from .aging import aging
from .balance_sheet import balance_sheet
from .base import Report, ReportRow
from .cashflow import cash_flow
from .income_statement import income_statement
from .register import register
from .trial_balance import trial_balance

REPORTS = {
    "trial-balance": trial_balance,
    "income-statement": income_statement,
    "balance-sheet": balance_sheet,
    "cash-flow": cash_flow,
    "register": register,
    "aging": aging,
}

__all__ = [
    "REPORTS",
    "Report",
    "ReportRow",
    "aging",
    "balance_sheet",
    "cash_flow",
    "income_statement",
    "register",
    "trial_balance",
]
