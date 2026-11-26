"""Denied claims and dollars by CARC reason code and payer."""

from __future__ import annotations

import polars as pl

from era835.warehouse import Warehouse


def denials(warehouse: Warehouse, by_payer: bool = True) -> pl.DataFrame:
    """Claims whose final status is denied, grouped by ``GROUP-REASON`` (e.g. ``CO-50``).

    A denial that a later correction paid is not counted: only claims that
    ``claim_final`` still reports as denied. Dollars are the CAS amounts carried under the
    reason, at claim and service-line level.
    """
    cols = ["claim_id", "group_code", "reason_code", "amount_cents"]
    adjustments = pl.concat(
        [
            warehouse.table("claim_adjustments").select(cols),
            warehouse.table("line_adjustments").select(cols),
        ]
    )
    denied_pcns = (
        warehouse.table("claim_final")
        .filter(pl.col("final_status") == "denied")
        .select("patient_control_number")
    )
    denied_claims = (
        warehouse.table("claims")
        .filter(pl.col("status_code") == "4")
        .join(denied_pcns, on="patient_control_number", how="inner")
        .select("claim_id", "payer_name")
    )
    keys = ["payer_name", "carc"] if by_payer else ["carc"]
    return (
        adjustments.join(denied_claims, on="claim_id", how="inner")
        .with_columns((pl.col("group_code") + "-" + pl.col("reason_code")).alias("carc"))
        .group_by(keys)
        .agg(
            pl.col("claim_id").n_unique().alias("claims"),
            pl.col("amount_cents").sum().alias("denied_cents"),
        )
        .sort(["denied_cents", *keys], descending=[True] + [False] * len(keys))
    )
