"""Exception hierarchy used throughout ledgerkit.

Every error raised intentionally by the library derives from :class:`LedgerError`
so callers can catch a single base class at API boundaries (CLI, HTTP server).
"""

from __future__ import annotations


class LedgerError(Exception):
    """Base class for all ledgerkit errors."""

    code = "ledger_error"

    def to_dict(self) -> dict[str, str]:
        return {"error": self.code, "message": str(self)}


class InvalidAmountError(LedgerError):
    code = "invalid_amount"


class UnknownCurrencyError(LedgerError):
    code = "unknown_currency"


class CurrencyMismatchError(LedgerError):
    code = "currency_mismatch"

    def __init__(self, left: str, right: str, operation: str = "combine") -> None:
        super().__init__(f"cannot {operation} amounts in {left} and {right}")
        self.left = left
        self.right = right


class RateNotFoundError(LedgerError):
    code = "rate_not_found"


class AccountError(LedgerError):
    code = "account_error"


class InvalidAccountNameError(AccountError):
    code = "invalid_account_name"


class UnknownAccountError(AccountError):
    code = "unknown_account"


class DuplicateAccountError(AccountError):
    code = "duplicate_account"


class AccountClosedError(AccountError):
    code = "account_closed"


class CurrencyNotAllowedError(AccountError):
    code = "currency_not_allowed"


class TransactionError(LedgerError):
    code = "transaction_error"


class UnbalancedTransactionError(TransactionError):
    code = "unbalanced_transaction"

    def __init__(self, message: str, residual: object | None = None) -> None:
        super().__init__(message)
        self.residual = residual


class TransactionNotFoundError(TransactionError):
    code = "transaction_not_found"


class AlreadyVoidedError(TransactionError):
    code = "already_voided"


class PeriodClosedError(TransactionError):
    code = "period_closed"


class ParseError(LedgerError):
    code = "parse_error"

    def __init__(self, message: str, line: int | None = None, source: str | None = None) -> None:
        location = ""
        if source and line is not None:
            location = f"{source}:{line}: "
        elif line is not None:
            location = f"line {line}: "
        super().__init__(f"{location}{message}")
        self.line = line
        self.source = source
        self.raw_message = message


class ImporterError(LedgerError):
    code = "import_error"


class StorageError(LedgerError):
    code = "storage_error"


class QueryError(LedgerError):
    code = "query_error"


class BudgetError(LedgerError):
    code = "budget_error"


class ReconciliationError(LedgerError):
    code = "reconciliation_error"


class ConfigError(LedgerError):
    code = "config_error"
