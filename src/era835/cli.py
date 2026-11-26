"""Command line interface: generate, load, reconcile, denials, quarantine."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import polars as pl
import typer

from era835.denials import denials as denials_table
from era835.money import format_dollars
from era835.reconcile import read_deposits, reconcile
from era835.render import render_table
from era835.warehouse import Warehouse

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

WarehouseOpt = Annotated[Path, typer.Option("--warehouse", "-w", help="Warehouse directory.")]


@app.command()
def generate(
    out: Annotated[Path, typer.Option("--out", help="Output directory.")] = Path("data"),
    seed: Annotated[int, typer.Option("--seed", help="Random seed.")] = 835,
) -> None:
    """Write synthetic 835 files, deposits.csv and truth.json."""
    from era835.synth import generate as synth_generate

    truth = synth_generate(out, seed)
    typer.echo(
        f"wrote {len(truth['files'])} files, {truth['claim_segments']} claim segments, "
        f"{truth['service_lines']} service lines to {out}"
    )


@app.command()
def load(
    paths: Annotated[list[Path], typer.Argument(help="835 files, or directories of them.")],
    warehouse: WarehouseOpt = Path("warehouse"),
) -> None:
    """Load 835 files (a directory is loaded in file-name order) into the warehouse."""
    files: list[Path] = []
    for path in paths:
        files.extend(sorted(path.glob("*.835")) if path.is_dir() else [path])
    wh = Warehouse(warehouse)
    report = wh.load(files)
    rows = [
        [f.file_name, f.status, str(f.remittances_loaded), str(f.remittances_quarantined)]
        for f in report.files
        if f.status != "loaded"
    ]
    if rows:
        typer.echo(render_table(["file", "status", "loaded", "quarantined"], rows, right=[2, 3]))
    typer.echo(
        f"{len(report.files)} files: {report.count('loaded')} loaded, "
        f"{report.count('partial')} partial, {report.count('quarantined')} quarantined, "
        f"{report.count('duplicate')} duplicate"
    )
    typer.echo(
        f"warehouse: {wh.table('claims').height} claims, {wh.table('claim_final').height} "
        f"final claims, {wh.table('pending_reversals').height} pending reversals"
    )


@app.command("reconcile")
def reconcile_cmd(
    deposits: Annotated[Path, typer.Option("--deposits", help="Bank deposits CSV.")],
    warehouse: WarehouseOpt = Path("warehouse"),
    month: Annotated[
        str | None, typer.Option("--month", help="Only this payment month (YYYY-MM).")
    ] = None,
) -> None:
    """Tie BPR02, bank deposit and warehouse net paid for every EFT."""
    table = reconcile(Warehouse(warehouse), read_deposits(deposits), month)
    rows = [
        [
            r["trace_number"],
            r["payer_name"] or "-",
            format_dollars(r["bpr02_cents"]),
            format_dollars(r["deposit_cents"]),
            format_dollars(r["warehouse_net_cents"]),
            "-" if r["difference_cents"] is None else str(r["difference_cents"]),
            r["status"],
        ]
        for r in table.to_dicts()
    ]
    headers = ["trace", "payer", "BPR02", "deposit", "warehouse net", "diff (cents)", "status"]
    typer.echo(render_table(headers, rows, right=[2, 3, 4, 5]))
    bad = table.filter(
        pl.col("status").is_in(["difference", "no_deposit", "unmatched_deposit", "ambiguous_trace"])
    )
    if bad.height:
        raise typer.Exit(code=1)


@app.command("denials")
def denials_cmd(
    warehouse: WarehouseOpt = Path("warehouse"),
    by: Annotated[
        str, typer.Option("--by", help="'reason' or 'payer' (reason within payer).")
    ] = "reason",
    top: Annotated[int, typer.Option("--top", help="Show the top N rows.")] = 10,
) -> None:
    """Denied claims and dollars by CARC reason code (and payer)."""
    if by not in ("reason", "payer"):
        raise typer.BadParameter("--by must be 'reason' or 'payer'")
    table = denials_table(Warehouse(warehouse), by_payer=by == "payer").head(top)
    rows = []
    for r in table.to_dicts():
        rows.append(
            ([r["payer_name"]] if by == "payer" else [])
            + [r["carc"], str(r["claims"]), format_dollars(r["denied_cents"])]
        )
    headers = (["payer"] if by == "payer" else []) + ["reason", "claims", "denied"]
    typer.echo(render_table(headers, rows, right=[len(headers) - 2, len(headers) - 1]))


@app.command()
def quarantine(warehouse: WarehouseOpt = Path("warehouse")) -> None:
    """List remittances that failed a rule, with the rule id and the difference in cents."""
    table = Warehouse(warehouse).table("quarantine")
    rows = [
        [
            r["file_name"],
            r["rule"],
            r["claim"] or "-",
            "-" if r["difference_cents"] is None else str(r["difference_cents"]),
        ]
        for r in table.sort("file_name", "rule", "claim").to_dicts()
    ]
    typer.echo(render_table(["file", "rule", "claim", "cents"], rows, right=[3]))
