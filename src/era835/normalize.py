"""Turn a parsed remittance into rows for the normalised tables."""

from __future__ import annotations

from typing import Any

from era835.chains import REVERSAL
from era835.models import Remittance

Rows = dict[str, list[dict[str, Any]]]


def remittance_id(source_sha256: str, st02: str) -> str:
    """Content-derived id, so the same file yields the same ids in any load order."""
    return f"{source_sha256[:12]}-{st02}"


def _event_kind(status_code: str) -> str:
    if status_code == REVERSAL:
        return "reversal"
    return "denial" if status_code == "4" else "payment"


def normalize(rem: Remittance, *, source_sha256: str, isa13: str, st02: str, load_seq: int) -> Rows:
    rid = remittance_id(source_sha256, st02)
    month = rem.payment_date.strftime("%Y-%m")
    rows: Rows = {
        name: []
        for name in (
            "remittances",
            "claims",
            "claim_adjustments",
            "service_lines",
            "line_adjustments",
            "provider_adjustments",
            "claim_events",
        )
    }
    rows["remittances"].append(
        {
            "remittance_id": rid,
            "source_sha256": source_sha256,
            "isa13": isa13,
            "st02": st02,
            "payer_name": rem.payer_name,
            "payer_id": rem.payer_id,
            "payee_name": rem.payee_name,
            "payee_id": rem.payee_id,
            "trace_number": rem.trace_number,
            "payment_date": rem.payment_date,
            "payment_month": month,
            "production_date": rem.production_date,
            "payment_method": rem.payment_method,
            "bpr02_cents": rem.bpr02_cents,
            "claim_count": len(rem.claims),
            "plb_total_cents": sum(p.amount_cents for p in rem.provider_adjustments),
        }
    )
    for n, claim in enumerate(rem.claims, start=1):
        cid = f"{rid}-C{n:04d}"
        rows["claims"].append(
            {
                "claim_id": cid,
                "remittance_id": rid,
                "payment_month": month,
                "payment_date": rem.payment_date,
                "payer_name": rem.payer_name,
                "patient_control_number": claim.patient_control_number,
                "status_code": claim.status_code,
                "payer_claim_control_number": claim.payer_claim_control_number,
                "charge_cents": claim.charge_cents,
                "paid_cents": claim.paid_cents,
                "patient_resp_cents": claim.patient_resp_cents,
                "patient_id": claim.patient_id,
                "rendering_npi": claim.rendering_npi,
                "service_from": claim.service_from,
            }
        )
        rows["claim_events"].append(
            {
                "event_id": cid,
                "load_seq": load_seq,
                "remittance_id": rid,
                "payment_month": month,
                "payment_date": rem.payment_date,
                "patient_control_number": claim.patient_control_number,
                "payer_name": rem.payer_name,
                "payer_claim_control_number": claim.payer_claim_control_number,
                "event_kind": _event_kind(claim.status_code),
                "status_code": claim.status_code,
                "paid_cents": claim.paid_cents,
                "patient_resp_cents": claim.patient_resp_cents,
            }
        )
        for seq, adj in enumerate(claim.adjustments, start=1):
            rows["claim_adjustments"].append(
                {
                    "claim_id": cid,
                    "payment_month": month,
                    "seq": seq,
                    "group_code": adj.group,
                    "reason_code": adj.reason,
                    "amount_cents": adj.amount_cents,
                    "quantity": adj.quantity,
                }
            )
        for m, line in enumerate(claim.lines, start=1):
            lid = f"{cid}-L{m:02d}"
            rows["service_lines"].append(
                {
                    "line_id": lid,
                    "claim_id": cid,
                    "payment_month": month,
                    "seq": m,
                    "procedure_qualifier": line.procedure_qualifier,
                    "procedure_code": line.procedure_code,
                    "modifiers": ":".join(line.modifiers),
                    "charge_cents": line.charge_cents,
                    "paid_cents": line.paid_cents,
                    "allowed_cents": line.allowed_cents,
                    "units": line.units,
                    "service_date": line.service_date,
                }
            )
            for seq, adj in enumerate(line.adjustments, start=1):
                rows["line_adjustments"].append(
                    {
                        "line_id": lid,
                        "claim_id": cid,
                        "payment_month": month,
                        "seq": seq,
                        "group_code": adj.group,
                        "reason_code": adj.reason,
                        "amount_cents": adj.amount_cents,
                        "quantity": adj.quantity,
                    }
                )
    for seq, plb in enumerate(rem.provider_adjustments, start=1):
        rows["provider_adjustments"].append(
            {
                "remittance_id": rid,
                "payment_month": month,
                "seq": seq,
                "provider_id": plb.provider_id,
                "period_end": plb.period_end,
                "reason_code": plb.reason_code,
                "reference_id": plb.reference_id,
                "amount_cents": plb.amount_cents,
            }
        )
    return rows
