"""The Parquet warehouse and the loader that feeds it.

A load reads the existing tables, adds the rows of every remittance that passes validation,
recomputes the derived tables (``claim_final``, ``pending_reversals``) from the full event
history, and rewrites each table deterministically: rows sorted by key, one
``part-0.parquet`` per payment-month partition. Loading the same set of files in any order
therefore yields identical tables (apart from ``ledger`` and ``claim_events.load_seq``,
which record the order on purpose).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow.parquet as pq

from era835.chains import collapse
from era835.errors import ParseError
from era835.normalize import normalize, remittance_id
from era835.parser import parse_interchange
from era835.schemas import PARTITIONED, SCHEMAS, SORT_KEYS, WRITE_ORDER
from era835.validate import Violation, check_group, check_interchange, check_transaction

PART_NAME = "part-0.parquet"


@dataclass
class FileOutcome:
    file_name: str
    status: str  # loaded | partial | quarantined | duplicate
    remittances_loaded: int = 0
    remittances_quarantined: int = 0


@dataclass
class LoadReport:
    files: list[FileOutcome] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for f in self.files if f.status == status)


def _quarantine_row(
    ctx: tuple[str, str, str], rule: str, detail: str, **extra: Any
) -> dict[str, Any]:
    file_name, sha, isa13 = ctx
    row: dict[str, Any] = {
        "file_name": file_name,
        "source_sha256": sha,
        "isa13": isa13,
        "remittance_id": None,
        "trace_number": None,
        "payer_name": None,
        "payment_month": None,
        "bpr02_cents": None,
        "rule": rule,
        "claim": None,
        "difference_cents": None,
        "detail": detail,
    }
    row.update(extra)
    return row


class Warehouse:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------ reading
    def table(self, name: str) -> pl.DataFrame:
        schema = SCHEMAS[name]
        self._recover()
        files = sorted((self.root / name).rglob("*.parquet"))
        if not files:
            return pl.DataFrame(schema=schema)
        frames = [pl.from_arrow(pq.ParquetFile(f).read()) for f in files]
        return pl.concat(frames).select(list(schema)).sort(SORT_KEYS[name])

    # ------------------------------------------------------------------ writing
    @property
    def _pending(self) -> Path:
        return self.root / ".pending"

    def _recover(self) -> None:
        """Finish a commit that was interrupted part-way through swapping tables in.

        A load stages every table under ``.staging`` and commits by renaming it to
        ``.pending`` in one step. Once ``.pending`` exists the load is committed: swapping its
        tables into place is idempotent, so any later open of the warehouse completes it. An
        incomplete ``.staging`` was never committed and is discarded.
        """
        staging = self.root / ".staging"
        if staging.exists():
            shutil.rmtree(staging)
        pending = self._pending
        if not pending.exists():
            return
        for name in WRITE_ORDER:
            source = pending / name
            if source.exists():
                final = self.root / name
                if final.exists():
                    shutil.rmtree(final)
                os.replace(source, final)
        shutil.rmtree(pending)

    def _write(self, tables: dict[str, pl.DataFrame]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._recover()
        staging = self.root / ".staging"
        staging.mkdir()
        for name in WRITE_ORDER:
            frame = tables[name].select(list(SCHEMAS[name])).sort(SORT_KEYS[name])
            table_dir = staging / name
            table_dir.mkdir()
            if name in PARTITIONED:
                for month in frame["payment_month"].unique().sort().to_list():
                    part = frame.filter(pl.col("payment_month") == month)
                    target = table_dir / f"payment_month={month}"
                    target.mkdir()
                    pq.write_table(part.to_arrow(), target / PART_NAME)
            elif frame.height:
                pq.write_table(frame.to_arrow(), table_dir / PART_NAME)
        os.replace(staging, self._pending)  # the commit point
        self._recover()

    # ------------------------------------------------------------------ loading
    def load(self, paths: list[Path]) -> LoadReport:
        """Load files in the given order. Re-delivered files are skipped via the ledger."""
        state = {name: self.table(name) for name in SCHEMAS}
        ledger_keys = {(r["isa13"], r["sha256"]) for r in state["ledger"].to_dicts()}
        known_traces = {
            (r["payer_name"], r["trace_number"])
            for r in state["remittances"].select("payer_name", "trace_number").to_dicts()
        }
        load_seq = int(state["ledger"]["load_seq"].max() or 0)
        new_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in SCHEMAS}
        report = LoadReport()

        for path in paths:
            raw = Path(path).read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            name = Path(path).name
            quarantine: list[dict[str, Any]] = []
            loaded = 0
            quarantined = 0
            try:
                interchange = parse_interchange(raw.decode("utf-8"))
                isa13, file_error = interchange.isa13, None
            except (ParseError, UnicodeDecodeError) as exc:
                interchange, isa13, file_error = None, "", str(exc)

            if (isa13, sha) in ledger_keys:
                report.files.append(FileOutcome(name, "duplicate"))
                continue
            load_seq += 1
            ctx = (name, sha, isa13)

            if interchange is None:
                quarantine.append(_quarantine_row(ctx, "parse_error", file_error or ""))
                quarantined = 1
            else:
                envelope = check_interchange(interchange)
                # Envelope faults with no transaction to attach them to are judged at file level.
                file_level: list[Violation] = []
                if not interchange.transactions:
                    file_level += envelope
                for group in interchange.groups:
                    if not group.transactions:
                        file_level += check_group(group)
                if not interchange.transactions and not file_level:
                    file_level.append(
                        Violation("no_transactions", None, None, "interchange has no ST/SE")
                    )
                for v in file_level:
                    quarantine.append(_quarantine_row(ctx, v.rule, v.detail))
                quarantined += 1 if file_level else 0
                for group in interchange.groups:
                    group_issues = envelope + check_group(group)
                    for tx in group.transactions:
                        rem = tx.remittance
                        rid = remittance_id(sha, tx.st02)
                        issues: list[Violation] = list(group_issues) + check_transaction(tx)
                        if rem is not None and (rem.payer_name, rem.trace_number) in known_traces:
                            issues.append(
                                Violation(
                                    "duplicate_trace",
                                    None,
                                    None,
                                    f"trace {rem.trace_number} is already loaded from another file",
                                )
                            )
                        if tx.error:
                            issues.append(Violation("parse_error", None, None, tx.error))
                        if issues:
                            quarantined += 1
                            for v in issues:
                                quarantine.append(
                                    _quarantine_row(
                                        ctx,
                                        v.rule,
                                        v.detail,
                                        remittance_id=rid,
                                        trace_number=rem.trace_number if rem else None,
                                        payer_name=rem.payer_name if rem else None,
                                        payment_month=(
                                            rem.payment_date.strftime("%Y-%m") if rem else None
                                        ),
                                        bpr02_cents=rem.bpr02_cents if rem else None,
                                        claim=v.claim,
                                        difference_cents=v.difference_cents,
                                    )
                                )
                            continue
                        assert rem is not None
                        known_traces.add((rem.payer_name, rem.trace_number))
                        rows = normalize(
                            rem, source_sha256=sha, isa13=isa13, st02=tx.st02, load_seq=load_seq
                        )
                        for table, table_rows in rows.items():
                            new_rows[table].extend(table_rows)
                        loaded += 1

            status = "loaded" if not quarantined else ("partial" if loaded else "quarantined")
            new_rows["ledger"].append(
                {
                    "isa13": isa13,
                    "sha256": sha,
                    "file_name": name,
                    "load_seq": load_seq,
                    "status": status,
                    "remittances_loaded": loaded,
                    "remittances_quarantined": quarantined,
                }
            )
            new_rows["quarantine"].extend(quarantine)
            ledger_keys.add((isa13, sha))
            report.files.append(FileOutcome(name, status, loaded, quarantined))

        tables: dict[str, pl.DataFrame] = {}
        for name in SCHEMAS:
            if name in ("claim_final", "pending_reversals"):
                continue
            added = pl.DataFrame(new_rows[name], schema=SCHEMAS[name])
            tables[name] = pl.concat([state[name], added]) if added.height else state[name]
        final, pending = collapse(tables["claim_events"].to_dicts())
        tables["claim_final"] = pl.DataFrame(final, schema=SCHEMAS["claim_final"])
        tables["pending_reversals"] = pl.DataFrame(pending, schema=SCHEMAS["pending_reversals"])
        if report.files and any(f.status != "duplicate" for f in report.files):
            self._write(tables)
        return report
