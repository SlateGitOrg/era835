"""Tie each EFT's BPR02 to the bank deposit and to the net paid held in the warehouse."""

from __future__ import annotations

import csv
from pathlib import Path

import polars as pl

from era835.money import parse_cents
from era835.warehouse import Warehouse

COLUMNS = {
    "trace_number": pl.Utf8,
    "payer_name": pl.Utf8,
    "payment_month": pl.Utf8,
    "bpr02_cents": pl.Int64,
    "deposit_cents": pl.Int64,
    "warehouse_net_cents": pl.Int64,
    "difference_cents": pl.Int64,
    "status": pl.Utf8,
}


def read_deposits(path: str | Path) -> dict[str, tuple[str, int]]:
    """Read ``trace_number,deposit_date,amount`` into ``{trace: (deposit_date, cents)}``."""
    deposits: dict[str, tuple[str, int]] = {}
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            trace = row["trace_number"].strip()
            if trace in deposits:
                raise ValueError(f"deposit file lists trace {trace} twice")
            deposits[trace] = (row["deposit_date"].strip(), parse_cents(row["amount"]))
    return deposits


def reconcile(
    warehouse: Warehouse, deposits: dict[str, tuple[str, int]], month: str | None = None
) -> pl.DataFrame:
    """One row per EFT trace number.

    ``difference_cents`` is bank deposit minus warehouse net paid (net of PLB adjustments).
    Status is one of ``tied``, ``difference``, ``quarantined`` (the remittance was rejected,
    so there is no warehouse figure), ``no_deposit``, ``unmatched_deposit`` and
    ``ambiguous_trace`` (several payers share the trace number, which the trace-only deposit
    file cannot tell apart).
    """
    remittances = warehouse.table("remittances")
    claims_paid = (
        warehouse.table("claims")
        .group_by("remittance_id")
        .agg(pl.col("paid_cents").sum().alias("claims_paid"))
    )
    plb = (
        warehouse.table("provider_adjustments")
        .group_by("remittance_id")
        .agg(pl.col("amount_cents").sum().alias("plb"))
    )
    loaded = (
        remittances.join(claims_paid, on="remittance_id", how="left")
        .join(plb, on="remittance_id", how="left")
        .with_columns(
            (pl.col("claims_paid").fill_null(0) - pl.col("plb").fill_null(0)).alias("net")
        )
    )
    # Rows are keyed by (payer, trace) so two payers that happen to use the same trace
    # number stay separate; the deposit file carries only a trace number, so such a trace
    # cannot be matched to a deposit and is reported as ``ambiguous_trace``.
    rows: dict[tuple[str | None, str], dict] = {}
    for r in loaded.to_dicts():
        rows[(r["payer_name"], r["trace_number"])] = {
            "trace_number": r["trace_number"],
            "payer_name": r["payer_name"],
            "payment_month": r["payment_month"],
            "bpr02_cents": r["bpr02_cents"],
            "warehouse_net_cents": r["net"],
            "status": "loaded",
        }
    for r in warehouse.table("quarantine").to_dicts():
        key = (r["payer_name"], r["trace_number"])
        if r["trace_number"] and key not in rows and r["rule"] != "duplicate_trace":
            rows[key] = {
                "trace_number": r["trace_number"],
                "payer_name": r["payer_name"],
                "payment_month": r["payment_month"],
                "bpr02_cents": r["bpr02_cents"],
                "warehouse_net_cents": None,
                "status": "quarantined",
            }
    shared = {t for t in (k[1] for k in rows) if sum(1 for k in rows if k[1] == t) > 1}
    known_traces = {k[1] for k in rows}
    for trace, (deposit_date, _) in deposits.items():
        if trace not in known_traces:
            rows[(None, trace)] = {
                "trace_number": trace,
                "payer_name": None,
                "payment_month": deposit_date[:7],
                "bpr02_cents": None,
                "warehouse_net_cents": None,
                "status": "unmatched_deposit",
            }

    out = []
    for key in sorted(rows, key=lambda k: (k[1], k[0] or "")):
        row = rows[key]
        trace = row["trace_number"]
        if month and row["payment_month"] != month:
            continue
        if trace in shared:
            row["deposit_cents"] = None
            row["difference_cents"] = None
            row["status"] = "ambiguous_trace"
            out.append(row)
            continue
        deposit = deposits.get(trace, (None, None))[1]
        row["deposit_cents"] = deposit
        net = row["warehouse_net_cents"]
        row["difference_cents"] = None if deposit is None or net is None else deposit - net
        if row["status"] == "loaded":
            if deposit is None:
                row["status"] = "no_deposit"
            elif row["difference_cents"] == 0 and row["bpr02_cents"] == deposit:
                row["status"] = "tied"
            else:
                row["status"] = "difference"
        out.append(row)
    return pl.DataFrame(out, schema=COLUMNS)
