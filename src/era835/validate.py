"""Control checks and the three balancing rules of the 835.

``difference_cents`` is always *computed minus reported*, so a positive number means the
reported figure is lower than the components add up to.

Rule ids: ``segment_count``, ``transaction_control``, ``group_count``, ``group_control``,
``interchange_count``, ``interchange_control`` (envelope) and ``line_balance``,
``claim_balance``, ``payment_balance`` (money).
"""

from __future__ import annotations

from dataclasses import dataclass

from era835.models import Group, Interchange, Remittance, Transaction


@dataclass(frozen=True)
class Violation:
    rule: str
    claim: str | None
    difference_cents: int | None
    detail: str


def check_interchange(interchange: Interchange) -> list[Violation]:
    violations: list[Violation] = []
    if interchange.iea01 != len(interchange.groups):
        violations.append(
            Violation(
                "interchange_count",
                None,
                None,
                f"IEA01 declares {interchange.iea01} groups, interchange has "
                f"{len(interchange.groups)}",
            )
        )
    if interchange.iea02 != interchange.isa13:
        violations.append(
            Violation(
                "interchange_control",
                None,
                None,
                f"IEA02 {interchange.iea02!r} does not match ISA13 {interchange.isa13!r}",
            )
        )
    return violations


def check_group(group: Group) -> list[Violation]:
    violations: list[Violation] = []
    if group.ge01 != len(group.transactions):
        violations.append(
            Violation(
                "group_count",
                None,
                None,
                f"GE01 declares {group.ge01} transactions, group has {len(group.transactions)}",
            )
        )
    if group.ge02 != group.gs06:
        violations.append(
            Violation(
                "group_control",
                None,
                None,
                f"GE02 {group.ge02!r} != GS06 {group.gs06!r}",
            )
        )
    return violations


def check_transaction(tx: Transaction) -> list[Violation]:
    violations: list[Violation] = []
    if tx.se01 != tx.segment_count:
        violations.append(
            Violation(
                "segment_count",
                None,
                None,
                f"SE01 declares {tx.se01} segments, transaction has {tx.segment_count}",
            )
        )
    if tx.se02 != tx.st02:
        violations.append(
            Violation(
                "transaction_control",
                None,
                None,
                f"SE02 {tx.se02!r} != ST02 {tx.st02!r}",
            )
        )
    if tx.remittance is not None:
        violations.extend(check_balance(tx.remittance))
    return violations


def check_balance(rem: Remittance) -> list[Violation]:
    """Line, claim and payment balancing rules, in file order."""
    violations: list[Violation] = []
    for claim in rem.claims:
        pcn = claim.patient_control_number
        claim_cas = sum(a.amount_cents for a in claim.adjustments)
        line_cas = 0
        for number, line in enumerate(claim.lines, start=1):
            adjusted = sum(a.amount_cents for a in line.adjustments)
            line_cas += adjusted
            diff = (line.charge_cents - adjusted) - line.paid_cents
            if diff:
                violations.append(
                    Violation(
                        "line_balance",
                        pcn,
                        diff,
                        f"line {number} ({line.procedure_code}): SVC02 - CAS != SVC03",
                    )
                )
        diff = (claim.charge_cents - claim_cas - line_cas) - claim.paid_cents
        if diff:
            violations.append(
                Violation("claim_balance", pcn, diff, "CLP03 - claim CAS - line CAS != CLP04")
            )
    claims_paid = sum(c.paid_cents for c in rem.claims)
    plb = sum(p.amount_cents for p in rem.provider_adjustments)
    diff = (claims_paid - plb) - rem.bpr02_cents
    if diff:
        violations.append(Violation("payment_balance", None, diff, "sum(CLP04) - PLB != BPR02"))
    return violations
