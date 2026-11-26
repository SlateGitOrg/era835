"""The two naive loads the collapse logic is compared against."""

from __future__ import annotations

from pathlib import Path

from era835.parser import parse_interchange


def naive_claim_rows(files: list[Path], skip: set[str]) -> list[tuple[str, int]]:
    """Every CLP segment of every file (skipping known-bad files and re-deliveries)."""
    seen: set[bytes] = set()
    rows: list[tuple[str, int]] = []
    for path in files:
        if path.name in skip:
            continue
        raw = path.read_bytes()
        if raw in seen:
            continue
        seen.add(raw)
        for tx in parse_interchange(raw.decode()).transactions:
            assert tx.remittance is not None
            rows += [(c.patient_control_number, c.paid_cents) for c in tx.remittance.claims]
    return rows


def last_row_per_claim_total(rows: list[tuple[str, int]]) -> int:
    """Keep the last row seen for each patient control number, then add them up."""
    last: dict[str, int] = {}
    for pcn, paid in rows:
        last[pcn] = paid
    return sum(last.values())
