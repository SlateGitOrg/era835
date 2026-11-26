"""Plain data structures produced by the parser. Every amount is an integer number of cents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Adjustment:
    """One CAS triplet: group code (CO, PR, OA, PI, CR), reason code (CARC) and amount."""

    group: str
    reason: str
    amount_cents: int
    quantity: str | None = None


@dataclass
class ServiceLine:
    procedure_qualifier: str
    procedure_code: str
    modifiers: tuple[str, ...]
    charge_cents: int
    paid_cents: int
    units: str
    allowed_cents: int | None = None
    service_date: date | None = None
    adjustments: list[Adjustment] = field(default_factory=list)


@dataclass
class Claim:
    patient_control_number: str
    status_code: str
    charge_cents: int
    paid_cents: int
    patient_resp_cents: int
    payer_claim_control_number: str
    patient_id: str = ""
    rendering_npi: str = ""
    service_from: date | None = None
    adjustments: list[Adjustment] = field(default_factory=list)
    lines: list[ServiceLine] = field(default_factory=list)


@dataclass
class ProviderAdjustment:
    """One PLB pair. A positive amount reduces the payment, a negative amount increases it."""

    provider_id: str
    period_end: date | None
    reason_code: str
    reference_id: str
    amount_cents: int


@dataclass
class Remittance:
    bpr02_cents: int
    payment_date: date
    payment_method: str
    trace_number: str
    production_date: date | None
    payer_name: str
    payer_id: str
    payee_name: str
    payee_id: str
    claims: list[Claim] = field(default_factory=list)
    provider_adjustments: list[ProviderAdjustment] = field(default_factory=list)


@dataclass
class Transaction:
    """One ST/SE pair. ``remittance`` is None when the body could not be parsed."""

    st02: str
    se01: int | None
    se02: str
    segment_count: int
    remittance: Remittance | None
    error: str | None = None


@dataclass
class Group:
    gs06: str
    ge01: int | None
    ge02: str
    transactions: list[Transaction] = field(default_factory=list)


@dataclass
class Interchange:
    isa13: str
    iea01: int | None
    iea02: str
    groups: list[Group] = field(default_factory=list)

    @property
    def transactions(self) -> list[Transaction]:
        return [t for g in self.groups for t in g.transactions]
