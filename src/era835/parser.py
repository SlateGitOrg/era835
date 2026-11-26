"""Parse an X12 835 interchange into :mod:`era835.models` structures.

Supported segments: ISA/IEA, GS/GE, ST/SE, BPR, TRN, DTM, N1, CLP, CAS, NM1, SVC, AMT, PLB.
Anything else inside a transaction is ignored (but still counted by SE01).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from era835.errors import ParseError
from era835.models import (
    Adjustment,
    Claim,
    Group,
    Interchange,
    ProviderAdjustment,
    Remittance,
    ServiceLine,
    Transaction,
)
from era835.money import parse_cents
from era835.tokenizer import Delimiters, Segment, tokenize

GROUP_CODES = frozenset({"CO", "PR", "OA", "PI", "CR"})
MAX_CAS_TRIPLETS = 6
MAX_PLB_PAIRS = 6


def _parse_date(text: str, what: str) -> date:
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise ParseError(f"{what}: invalid date {text!r}") from exc


def _optional_date(text: str, what: str) -> date | None:
    return _parse_date(text, what) if text else None


def _to_int(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def _amount(text: str, what: str) -> int:
    if not text:
        raise ParseError(f"{what}: missing amount")
    try:
        return parse_cents(text)
    except ParseError as exc:
        raise ParseError(f"{what}: {exc}") from exc


def _parse_cas(seg: Segment) -> list[Adjustment]:
    group = seg.el(1)
    if group not in GROUP_CODES:
        raise ParseError(f"CAS: unknown group code {group!r}")
    adjustments: list[Adjustment] = []
    position = 2
    while position <= len(seg.elements):
        reason = seg.el(position)
        if not reason:
            break
        amount = _amount(seg.el(position + 1), f"CAS {group}-{reason}")
        adjustments.append(Adjustment(group, reason, amount, seg.el(position + 2) or None))
        position += 3
    if not adjustments:
        raise ParseError("CAS: no adjustment triplet")
    if len(adjustments) > MAX_CAS_TRIPLETS:
        raise ParseError(f"CAS: {len(adjustments)} triplets, at most {MAX_CAS_TRIPLETS}")
    return adjustments


def _parse_plb(seg: Segment, delimiters: Delimiters) -> list[ProviderAdjustment]:
    period_end = _optional_date(seg.el(2), "PLB02")
    result: list[ProviderAdjustment] = []
    position = 3
    while position <= len(seg.elements):
        composite = seg.el(position)
        if not composite:
            break
        parts = composite.split(delimiters.component)
        amount = _amount(seg.el(position + 1), f"PLB {parts[0]}")
        reference = parts[1] if len(parts) > 1 else ""
        result.append(ProviderAdjustment(seg.el(1), period_end, parts[0], reference, amount))
        position += 2
    if not result:
        raise ParseError("PLB: no adjustment pair")
    if len(result) > MAX_PLB_PAIRS:
        raise ParseError(f"PLB: {len(result)} pairs, at most {MAX_PLB_PAIRS}")
    return result


def parse_835(segments: Sequence[Segment], delimiters: Delimiters) -> Remittance:
    """Parse the segments between ST and SE (exclusive) into a :class:`Remittance`."""
    bpr: Segment | None = None
    trace = ""
    production_date: date | None = None
    payer = ("", "")
    payee = ("", "")
    claims: list[Claim] = []
    plb: list[ProviderAdjustment] = []
    claim: Claim | None = None
    line: ServiceLine | None = None

    for seg in segments:
        sid = seg.id
        if sid == "BPR":
            bpr = seg
        elif sid == "TRN":
            trace = seg.el(2)
        elif sid == "N1" and claim is None:
            if seg.el(1) == "PR":
                payer = (seg.el(2), seg.el(4))
            elif seg.el(1) == "PE":
                payee = (seg.el(2), seg.el(4))
        elif sid == "CLP":
            status = seg.el(2)
            ccn = seg.el(7)
            if not seg.el(1):
                raise ParseError("CLP01 patient control number is missing")
            if status == "22" and not ccn:
                raise ParseError(f"CLP {seg.el(1)}: reversal without CLP07")
            claim = Claim(
                patient_control_number=seg.el(1),
                status_code=status,
                charge_cents=_amount(seg.el(3), f"CLP {seg.el(1)} CLP03"),
                paid_cents=_amount(seg.el(4), f"CLP {seg.el(1)} CLP04"),
                patient_resp_cents=_amount(seg.el(5) or "0", f"CLP {seg.el(1)} CLP05"),
                payer_claim_control_number=ccn,
            )
            claims.append(claim)
            line = None
        elif sid == "CAS":
            if claim is None:
                raise ParseError("CAS outside a claim")
            (line.adjustments if line else claim.adjustments).extend(_parse_cas(seg))
        elif sid == "NM1" and claim is not None:
            if seg.el(1) == "QC":
                claim.patient_id = seg.el(9)
            elif seg.el(1) == "82":
                claim.rendering_npi = seg.el(9)
        elif sid == "SVC":
            if claim is None:
                raise ParseError("SVC outside a claim")
            parts = seg.el(1).split(delimiters.component)
            if len(parts) < 2:
                raise ParseError(f"SVC01 is not a composite procedure code: {seg.el(1)!r}")
            what = f"SVC {parts[1]}"
            line = ServiceLine(
                procedure_qualifier=parts[0],
                procedure_code=parts[1],
                modifiers=tuple(p for p in parts[2:] if p),
                charge_cents=_amount(seg.el(2), f"{what} SVC02"),
                paid_cents=_amount(seg.el(3), f"{what} SVC03"),
                units=seg.el(5) or "1",
            )
            claim.lines.append(line)
        elif sid == "AMT" and line is not None and seg.el(1) == "B6":
            line.allowed_cents = _amount(seg.el(2), "AMT B6")
        elif sid == "DTM":
            qualifier = seg.el(1)
            if claim is None:
                if qualifier == "405":
                    production_date = _parse_date(seg.el(2), "DTM 405")
            elif line is not None:
                if qualifier == "472":
                    line.service_date = _parse_date(seg.el(2), "DTM 472")
            elif qualifier == "232":
                claim.service_from = _parse_date(seg.el(2), "DTM 232")
        elif sid == "PLB":
            plb.extend(_parse_plb(seg, delimiters))

    if bpr is None:
        raise ParseError("transaction has no BPR segment")
    if not trace:
        raise ParseError("transaction has no TRN02 trace number")
    if not payer[0]:
        raise ParseError("transaction has no N1*PR payer")
    return Remittance(
        bpr02_cents=_amount(bpr.el(2), "BPR02"),
        payment_date=_parse_date(bpr.el(16), "BPR16"),
        payment_method=bpr.el(4),
        trace_number=trace,
        production_date=production_date,
        payer_name=payer[0],
        payer_id=payer[1],
        payee_name=payee[0],
        payee_id=payee[1],
        claims=claims,
        provider_adjustments=plb,
    )


def _transaction(segs: list[Segment], delimiters: Delimiters) -> Transaction:
    st, se = segs[0], segs[-1]
    remittance: Remittance | None = None
    error: str | None = None
    try:
        remittance = parse_835(segs[1:-1], delimiters)
    except ParseError as exc:
        error = str(exc)
    return Transaction(
        st02=st.el(2),
        se01=_to_int(se.el(1)),
        se02=se.el(2),
        segment_count=len(segs),
        remittance=remittance,
        error=error,
    )


def parse_interchange(text: str) -> Interchange:
    """Parse a whole interchange. Structural problems raise :class:`ParseError`.

    Problems confined to one transaction body do not raise: they are recorded on that
    transaction so the other transactions in the file can still be judged on their own.
    """
    delimiters, segments = tokenize(text)
    isa = segments[0]
    interchange = Interchange(isa13=isa.el(13), iea01=None, iea02="")
    group: Group | None = None
    body: list[Segment] | None = None
    closed = False
    for seg in segments[1:]:
        if closed:
            raise ParseError("segments after IEA")
        if seg.id == "GS":
            if group is not None:
                raise ParseError("GS inside an open functional group")
            group = Group(gs06=seg.el(6), ge01=None, ge02="")
        elif seg.id == "ST":
            if group is None or body is not None:
                raise ParseError("ST outside a functional group or inside another ST")
            body = [seg]
        elif seg.id == "SE":
            if body is None or group is None:
                raise ParseError("SE without ST")
            body.append(seg)
            group.transactions.append(_transaction(body, delimiters))
            body = None
        elif seg.id == "GE":
            if group is None or body is not None:
                raise ParseError("GE without a closed functional group")
            group.ge01, group.ge02 = _to_int(seg.el(1)), seg.el(2)
            interchange.groups.append(group)
            group = None
        elif seg.id == "IEA":
            if group is not None or body is not None:
                raise ParseError("IEA while a group or transaction is open")
            interchange.iea01, interchange.iea02 = _to_int(seg.el(1)), seg.el(2)
            closed = True
        elif body is not None:
            body.append(seg)
    if not closed:
        raise ParseError("interchange has no IEA trailer")
    return interchange
