"""Plain structures for the synthetic generator (money is integer cents)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

Adj = tuple[str, str, int]  # (group, reason, amount_cents)


@dataclass(frozen=True)
class Style:
    """How a payer writes its files."""

    element: str = "*"
    component: str = ":"
    terminator: str = "~"
    newline_after: bool = False


@dataclass(frozen=True)
class Payer:
    idx: int
    name: str
    payer_id: str
    slug: str
    prefix: str
    style: Style


@dataclass
class GLine:
    code: str
    charge: int
    paid: int
    allowed: int
    units: int
    adjustments: list[Adj] = field(default_factory=list)


@dataclass(eq=False)
class GClaim:
    pcn: str
    status: str  # CLP02: 1, 2, 4 or 22
    ccn: str
    frequency: str  # CLP09: 1 original, 7 replacement, 8 reversal
    charge: int
    paid: int
    patient_resp: int
    adjustments: list[Adj]
    lines: list[GLine]
    patient_id: str
    last: str
    first: str
    npi: str
    service_date: date
    kind: str = "standalone"  # standalone | orig | rev | corr


@dataclass(eq=False)
class GRemit:
    """One remittance. The generator writes exactly one per file."""

    payer: Payer
    date: date
    trace: str
    isa13: int
    claims: list[GClaim] = field(default_factory=list)
    plb: list[tuple[str, str, int]] = field(default_factory=list)  # (reason, ref, cents)
    malformed: str | None = None  # claim_balance | segment_count
    altered: tuple[str, int] | None = None  # (pcn, difference_cents) for claim_balance

    @property
    def bpr02(self) -> int:
        return sum(c.paid for c in self.claims) - sum(p[2] for p in self.plb)
