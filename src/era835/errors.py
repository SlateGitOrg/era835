"""Exception types shared by the tokeniser, parser and loader."""

from __future__ import annotations


class ParseError(ValueError):
    """The input is not a well-formed X12 835 (structure, amount or date)."""
