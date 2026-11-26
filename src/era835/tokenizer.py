"""Split an X12 interchange into segments, detecting the separators from the ISA segment.

The ISA segment is fixed width (106 characters including its terminator), which is what
makes detection possible without being told the separators: the element separator is the
4th character, the component separator is ISA16 (character 105) and the segment terminator
is the character right after it.
"""

from __future__ import annotations

from dataclasses import dataclass

from era835.errors import ParseError

ISA_LENGTH = 106
_ISA16_INDEX = 104
_TERMINATOR_INDEX = 105
_ISA_ELEMENTS = 16


@dataclass(frozen=True)
class Delimiters:
    element: str
    component: str
    terminator: str


@dataclass(frozen=True)
class Segment:
    """One segment. ``elements[0]`` is element 01, so ``el(1)`` is the first data element."""

    id: str
    elements: tuple[str, ...]

    def el(self, n: int) -> str:
        """Element ``n`` (1-based), or an empty string when the segment is shorter."""
        return self.elements[n - 1] if 0 < n <= len(self.elements) else ""


def detect_delimiters(text: str) -> Delimiters:
    if len(text) < ISA_LENGTH or not text.startswith("ISA"):
        raise ParseError("interchange must start with a fixed-width ISA segment")
    element = text[3]
    component = text[_ISA16_INDEX]
    terminator = text[_TERMINATOR_INDEX]
    if terminator == "\r" and text[ISA_LENGTH : ISA_LENGTH + 1] == "\n":
        terminator = "\n"
    for name, char in (("element", element), ("component", component)):
        if char.isalnum() or char.isspace():
            raise ParseError(f"{name} separator {char!r} is not a valid delimiter")
    if terminator.isalnum() or terminator in (element, component):
        raise ParseError(f"segment terminator {terminator!r} is not a valid delimiter")
    if element == component:
        raise ParseError("element and component separators must differ")
    if text[:_TERMINATOR_INDEX].count(element) != _ISA_ELEMENTS:
        raise ParseError("ISA segment must have 16 elements at their fixed widths")
    return Delimiters(element, component, terminator)


def tokenize(text: str) -> tuple[Delimiters, list[Segment]]:
    """Return the detected delimiters and every segment in the interchange."""
    text = text.lstrip("﻿ \t\r\n")
    delimiters = detect_delimiters(text)
    segments: list[Segment] = []
    for piece in text.split(delimiters.terminator):
        piece = piece.strip()
        if not piece:
            continue
        fields = piece.split(delimiters.element)
        segments.append(Segment(fields[0].strip(), tuple(f.strip() for f in fields[1:])))
    return delimiters, segments
