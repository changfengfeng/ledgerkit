"""Configuration loaded from an INI file (``ledgerkit.ini``) and the environment."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

ENV_PREFIX = "LEDGERKIT_"
DEFAULT_FILES = ("ledgerkit.ini", "~/.config/ledgerkit/ledgerkit.ini")


@dataclass
class Settings:
    ledger_file: str | None = None
    database: str | None = None
    currency: str = "USD"
    fiscal_year_start: int = 1
    date_tolerance: int = 3
    host: str = "127.0.0.1"
    port: int = 8765
    auto_open: bool = False
    import_rules: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None, environ: dict[str, str] | None = None) -> Settings:
        settings = cls()
        candidates = [Path(path)] if path else [Path(p).expanduser() for p in DEFAULT_FILES]
        for candidate in candidates:
            if candidate.exists():
                settings._apply_ini(candidate)
                break
        else:
            if path:
                raise ConfigError(f"config file not found: {path}")
        settings._apply_env(environ if environ is not None else dict(os.environ))
        return settings

    def _set(self, key: str, raw: str) -> None:
        key = key.lower().replace("-", "_")
        if key in ("fiscal_year_start", "date_tolerance", "port"):
            try:
                setattr(self, key, int(raw))
            except ValueError:
                raise ConfigError(f"{key} must be an integer, got {raw!r}") from None
        elif key == "auto_open":
            setattr(self, key, raw.strip().lower() in ("1", "true", "yes", "on"))
        elif key == "currency":
            self.currency = raw.strip().upper()
        elif key in ("ledger_file", "database", "host", "import_rules"):
            setattr(self, key, raw.strip())
        else:
            self.extra[key] = raw

    def _apply_ini(self, path: Path) -> None:
        parser = configparser.ConfigParser()
        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error as exc:
            raise ConfigError(f"invalid config {path}: {exc}") from None
        for section in ("ledgerkit", "server"):
            if parser.has_section(section):
                for key, value in parser.items(section):
                    self._set(key, value)

    def _apply_env(self, environ: dict[str, str]) -> None:
        for key, value in environ.items():
            if key.startswith(ENV_PREFIX):
                self._set(key[len(ENV_PREFIX):], value)

    def validate(self) -> None:
        if not 1 <= self.fiscal_year_start <= 12:
            raise ConfigError("fiscal_year_start must be between 1 and 12")
        if not 0 < self.port < 65536:
            raise ConfigError("port out of range")
