"""Plain-text tables for the CLI."""

from __future__ import annotations

from collections.abc import Sequence


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]], right: Sequence[int] = ()):
    """Column-aligned text table; columns whose index is in ``right`` are right-aligned."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells: Sequence[str]) -> str:
        parts = [
            cell.rjust(widths[i]) if i in right else cell.ljust(widths[i])
            for i, cell in enumerate(cells)
        ]
        return "  ".join(parts).rstrip()

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(r) for r in rows)
    return "\n".join(lines)
