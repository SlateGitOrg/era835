"""Exact money handling.

Amounts are decimal text on the wire. They are parsed with ``Decimal`` and become
integer cents immediately; no float is ever created, so sums are exact.
"""

from __future__ import annotations

import re
from decimal import Decimal

from era835.errors import ParseError

_AMOUNT = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)")


class AmountError(ParseError):
    """An amount that is not a decimal number with at most two decimal places."""


def parse_cents(text: str) -> int:
    """Parse an X12 decimal amount (``"12.5"``, ``"-0.05"``, ``"100"``) into integer cents."""
    token = text.strip()
    if not _AMOUNT.fullmatch(token):
        raise AmountError(f"not a decimal amount: {text!r}")
    scaled = Decimal(token) * 100
    cents = scaled.to_integral_value()
    if scaled != cents:
        raise AmountError(f"more than two decimal places: {text!r}")
    return int(cents)


def format_x12(cents: int) -> str:
    """Render cents the way payers write them: no trailing zeros (``1250`` -> ``"12.5"``)."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    if frac == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{frac:02d}".rstrip("0")


def format_dollars(cents: int | None) -> str:
    """Render cents for display (``-123456`` -> ``"-$1,234.56"``); ``None`` becomes ``"-"``."""
    if cents is None:
        return "-"
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"
