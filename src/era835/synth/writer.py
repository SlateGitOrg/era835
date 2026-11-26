"""Render generator output as X12 835 text."""

from __future__ import annotations

from datetime import date, timedelta

from era835.money import format_x12
from era835.synth.types import Adj, GClaim, GRemit, Style

PROVIDER_NAME = "Ridgeline Physician Group"
PROVIDER_NPI = "1999990001"
RECEIVER_ID = "RIDGELINEPG"


def _cas(groups: list[Adj], *, quantity: str | None) -> list[list[str]]:
    """One CAS segment per group code; triplets carry ``quantity`` or leave it empty."""
    order: list[str] = []
    for group, _, _ in groups:
        if group not in order:
            order.append(group)
    segments = []
    for group in order:
        items = [(r, a) for g, r, a in groups if g == group]
        fields = ["CAS", group]
        for n, (reason, amount) in enumerate(items):
            fields += [reason, format_x12(amount)]
            if quantity is not None:
                fields.append(quantity)
            elif n < len(items) - 1:
                fields.append("")
        segments.append(fields)
    return segments


def claim_segments(c: GClaim, style: Style, filing: str) -> list[list[str]]:
    segs = [
        [
            "CLP", c.pcn, c.status, format_x12(c.charge), format_x12(c.paid),
            format_x12(c.patient_resp), filing, c.ccn, "11", c.frequency,
        ]
    ]  # fmt: skip
    segs += _cas(c.adjustments, quantity="1")
    segs.append(["NM1", "QC", "1", c.last, c.first, "", "", "", "MI", c.patient_id])
    segs.append(["NM1", "82", "1", c.last, c.first, "", "", "", "XX", c.npi])
    segs.append(["DTM", "232", c.service_date.strftime("%Y%m%d")])
    for line in c.lines:
        segs.append(
            ["SVC", f"HC{style.component}{line.code}", format_x12(line.charge),
             format_x12(line.paid), "", str(line.units)]
        )  # fmt: skip
        segs.append(["DTM", "472", c.service_date.strftime("%Y%m%d")])
        segs += _cas(line.adjustments, quantity=None)
        if line.allowed:
            segs.append(["AMT", "B6", format_x12(line.allowed)])
    return segs


def _render(style: Style, segments: list[list[str]]) -> str:
    tail = style.terminator + ("\n" if style.newline_after else "")
    return "".join(style.element.join(s) + tail for s in segments)


def render_remittance(r: GRemit, gs06: int) -> str:
    """Render one interchange (ISA..IEA) holding one functional group and one transaction."""
    style = r.payer.style
    stamp = r.date.strftime("%Y%m%d")
    repetition = "^" if style.component != "^" else ":"
    isa = [
        "ISA", "00", " " * 10, "00", " " * 10, "ZZ", r.payer.payer_id.ljust(15), "ZZ",
        RECEIVER_ID.ljust(15), r.date.strftime("%y%m%d"), "1200", repetition, "00501",
        f"{r.isa13:09d}", "0", "P", style.component,
    ]  # fmt: skip
    isa_text = (
        style.element.join(isa)
        + style.terminator
        + ("\n" if style.newline_after and style.terminator != "\n" else "")
    )
    assert len(isa_text.rstrip("\n")) == 106 or style.terminator == "\n"

    body: list[list[str]] = [
        [
            "BPR", "I", format_x12(r.bpr02), "C", "ACH", "CCP", "01", "999999999", "DA",
            "123456789", "1" + r.payer.payer_id.ljust(9, "0"), "", "01", "888888888", "DA",
            "987654321", stamp,
        ],
        ["TRN", "1", r.trace, "1" + r.payer.payer_id.ljust(9, "0")],
        ["DTM", "405", stamp],
        ["N1", "PR", r.payer.name, "XV", r.payer.payer_id],
        ["N1", "PE", PROVIDER_NAME, "XX", PROVIDER_NPI],
    ]  # fmt: skip
    for claim in r.claims:
        body += claim_segments(claim, style, "12")
    for reason, ref, cents in r.plb:
        body.append(
            ["PLB", PROVIDER_NPI, f"{r.date.year}1231", f"{reason}{style.component}{ref}",
             format_x12(cents)]
        )  # fmt: skip
    count = len(body) + 2
    if r.malformed == "segment_count":
        count += 1
    segments = (
        [["GS", "HP", r.payer.payer_id, RECEIVER_ID, stamp, "1200", str(gs06), "X", "005010X221A1"]]
        + [["ST", "835", "0001"]]
        + body
        + [["SE", str(count), "0001"], ["GE", "1", str(gs06)], ["IEA", "1", f"{r.isa13:09d}"]]
    )
    return isa_text + _render(style, segments)


def deposit_date(d: date) -> str:
    return (d + timedelta(days=1)).isoformat()
