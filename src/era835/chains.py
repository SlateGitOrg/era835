"""Collapse reversal chains into one final row per patient control number.

The result depends only on the *set* of claim events, never on the order they were loaded:

* a reversal (CLP02 = 22) cancels the earlier payment with the same payer, patient control
  number (CLP01) and payer claim control number (CLP07);
* a reversal whose original has not been loaded is *pending* and is left out of the final
  table until the original arrives;
* among the payments that are not reversed, the latest by payment date supersedes earlier
  ones from the same payer (a correction), ties broken by CLP07;
* when every payment was reversed the claim's final status is ``reversed``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

REVERSAL = "22"
STATUS_NAMES = {"1": "primary", "2": "secondary", "4": "denied", REVERSAL: "reversed"}

Event = dict[str, Any]


def status_name(code: str) -> str:
    return STATUS_NAMES.get(code, f"status_{code}")


def _order(event: Event) -> tuple:
    return (event["payment_date"], event["payer_claim_control_number"], event["event_id"])


def collapse(events: list[Event]) -> tuple[list[Event], list[Event]]:
    """Return ``(claim_final rows, pending_reversal rows)`` for a list of claim events."""
    originals = {
        (e["payer_name"], e["payer_claim_control_number"], e["patient_control_number"])
        for e in events
        if e["status_code"] != REVERSAL
    }
    pending: list[Event] = []
    by_claim: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        key = (
            event["payer_name"],
            event["payer_claim_control_number"],
            event["patient_control_number"],
        )
        if event["status_code"] == REVERSAL and key not in originals:
            pending.append(event)
        else:
            by_claim[event["patient_control_number"]].append(event)

    final: list[Event] = []
    for pcn in sorted(by_claim):
        chain = by_claim[pcn]
        reversed_keys = {
            (e["payer_name"], e["payer_claim_control_number"])
            for e in chain
            if e["status_code"] == REVERSAL
        }
        latest_per_payer: dict[str, Event] = {}
        for event in sorted(chain, key=_order):
            if event["status_code"] == REVERSAL:
                continue
            if (event["payer_name"], event["payer_claim_control_number"]) in reversed_keys:
                continue
            latest_per_payer[event["payer_name"]] = event
        counted = list(latest_per_payer.values())
        last = max(counted or chain, key=_order)
        final.append(
            {
                "patient_control_number": pcn,
                "payer_name": last["payer_name"],
                "final_status": status_name(last["status_code"]) if counted else "reversed",
                "net_paid_cents": sum(e["paid_cents"] for e in counted),
                "net_patient_resp_cents": sum(e["patient_resp_cents"] for e in counted),
                "chain_length": len(chain),
                "final_claim_control_number": last["payer_claim_control_number"],
                "last_payment_date": last["payment_date"],
                "payment_month": last["payment_month"],
            }
        )
    pending_rows = [
        {
            "event_id": e["event_id"],
            "remittance_id": e["remittance_id"],
            "payment_month": e["payment_month"],
            "payment_date": e["payment_date"],
            "patient_control_number": e["patient_control_number"],
            "payer_name": e["payer_name"],
            "payer_claim_control_number": e["payer_claim_control_number"],
            "paid_cents": e["paid_cents"],
        }
        for e in pending
    ]
    return final, pending_rows
