"""Text helpers: table rendering, slugging and fuzzy matching."""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Sequence

_WS_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def normalize_whitespace(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def slugify(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _NON_WORD_RE.sub("-", folded.lower()).strip("-")


def similarity(left: str, right: str) -> float:
    """Return a 0..1 similarity score that ignores case and punctuation."""
    a = slugify(left).replace("-", " ")
    b = slugify(right).replace("-", " ")
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def truncate(text: str, width: int, ellipsis: str = "…") -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= len(ellipsis):
        return text[:width]
    return text[: width - len(ellipsis)] + ellipsis


def display_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def pad(text: str, width: int, align: str = "left") -> str:
    gap = max(0, width - display_width(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    aligns: Sequence[str] | None = None,
    max_width: int | None = None,
) -> str:
    """Render a plain-text table with a header separator line."""
    columns = len(headers)
    aligns = list(aligns or ["left"] * columns)
    while len(aligns) < columns:
        aligns.append("left")
    cells = [[str(c) for c in row] + [""] * (columns - len(row)) for row in rows]
    widths = [display_width(h) for h in headers]
    for row in cells:
        for index, cell in enumerate(row[:columns]):
            widths[index] = max(widths[index], display_width(cell))
    if max_width:
        widths = [min(w, max_width) for w in widths]
        cells = [[truncate(c, widths[i]) for i, c in enumerate(row[:columns])] for row in cells]
    lines = ["  ".join(pad(h, widths[i], aligns[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for row in cells:
        if row and row[0] == "\x00sep":
            lines.append("  ".join("-" * w for w in widths))
            continue
        lines.append("  ".join(pad(c, widths[i], aligns[i]) for i, c in enumerate(row[:columns])).rstrip())
    return "\n".join(lines)


SEPARATOR_ROW = ("\x00sep",)


def indent(text: str, level: int, unit: str = "  ") -> str:
    return unit * level + text


def quote(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] == '"':
        body = text[1:-1]
        out = []
        i = 0
        while i < len(body):
            if body[i] == "\\" and i + 1 < len(body):
                out.append(body[i + 1])
                i += 2
                continue
            out.append(body[i])
            i += 1
        return "".join(out)
    return text
