import hashlib
import os

import polars as pl
import pytest

from conftest import write
from era835.warehouse import Warehouse
from x12 import header, interchange, paid_claim


def tree_digest(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*.parquet"))
    }


def test_fixture_rows_land_in_every_table(tmp_path, star):
    wh = Warehouse(tmp_path / "wh")
    report = wh.load([write(tmp_path, "a.835", star)])
    assert report.count("loaded") == 1
    counts = {t: wh.table(t).height for t in (
        "remittances", "claims", "claim_adjustments", "service_lines", "line_adjustments",
        "provider_adjustments", "claim_events", "claim_final")}  # fmt: skip
    assert counts == {
        "remittances": 1, "claims": 2, "claim_adjustments": 1, "service_lines": 3,
        "line_adjustments": 5, "provider_adjustments": 2, "claim_events": 2, "claim_final": 2,
    }  # fmt: skip


def test_partitioned_layout_and_deterministic_file_names(tmp_path, star):
    wh = Warehouse(tmp_path / "wh")
    wh.load([write(tmp_path, "a.835", star)])
    assert (tmp_path / "wh/claims/payment_month=2026-01/part-0.parquet").is_file()
    assert (tmp_path / "wh/ledger/part-0.parquet").is_file()


def test_same_input_gives_byte_identical_parquet(tmp_path, star):
    path = write(tmp_path, "a.835", star)
    Warehouse(tmp_path / "w1").load([path])
    Warehouse(tmp_path / "w2").load([path])
    assert tree_digest(tmp_path / "w1") == tree_digest(tmp_path / "w2")


def test_redelivered_file_is_a_ledger_hit_and_changes_nothing(tmp_path, star):
    wh = Warehouse(tmp_path / "wh")
    wh.load([write(tmp_path, "a.835", star)])
    before = tree_digest(tmp_path / "wh")
    report = wh.load([write(tmp_path, "again.835", star)])
    assert [f.status for f in report.files] == ["duplicate"]
    assert tree_digest(tmp_path / "wh") == before


def test_money_columns_are_integers(tmp_path, star):
    wh = Warehouse(tmp_path / "wh")
    wh.load([write(tmp_path, "a.835", star)])
    claims = wh.table("claims")
    assert claims.schema["paid_cents"] == pl.Int64
    assert claims.sort("claim_id")["paid_cents"].to_list() == [16550, 0]


def test_garbage_file_is_quarantined_and_recorded_in_the_ledger(tmp_path):
    wh = Warehouse(tmp_path / "wh")
    junk = write(tmp_path, "junk.835", "this is not an 835")
    assert wh.load([junk]).files[0].status == "quarantined"
    assert wh.table("quarantine")["rule"].to_list() == ["parse_error"]
    assert wh.load([junk]).files[0].status == "duplicate"


def test_one_bad_transaction_does_not_block_its_neighbour(tmp_path):
    good = header("GOOD", "100") + paid_claim("P1", "C1")
    bad = header("BAD", "55") + paid_claim("P2", "C2")
    text = interchange([], transactions=[good, bad], ge01=2)
    wh = Warehouse(tmp_path / "wh")
    (outcome,) = wh.load([write(tmp_path, "two.835", text)]).files
    assert (outcome.status, outcome.remittances_loaded, outcome.remittances_quarantined) == (
        "partial", 1, 1,
    )  # fmt: skip
    assert wh.table("remittances")["trace_number"].to_list() == ["GOOD"]
    q = wh.table("quarantine")
    assert q["rule"].to_list() == ["payment_balance"] and q["trace_number"].to_list() == ["BAD"]


def test_envelope_failure_quarantines_every_transaction_in_the_file(tmp_path):
    bodies = [header("T1", "100") + paid_claim("P1", "C1"), header("T2", "100")
              + paid_claim("P2", "C2")]  # fmt: skip
    text = interchange([], transactions=bodies, ge01=5)
    wh = Warehouse(tmp_path / "wh")
    wh.load([write(tmp_path, "g.835", text)])
    assert wh.table("remittances").height == 0
    assert wh.table("quarantine")["rule"].to_list() == ["group_count", "group_count"]


def test_same_trace_from_a_different_file_is_quarantined(tmp_path):
    a = interchange(header("T1", "100") + paid_claim("P1", "C1"), isa13="000000001")
    b = interchange(header("T1", "100") + paid_claim("P9", "C9"), isa13="000000002")
    wh = Warehouse(tmp_path / "wh")
    wh.load([write(tmp_path, "a.835", a), write(tmp_path, "b.835", b)])
    assert wh.table("remittances").height == 1
    assert wh.table("quarantine")["rule"].to_list() == ["duplicate_trace"]


def _reversal_file(trace, isa13):
    body = header(trace, "0", payment="20260209") + [
        ["CLP", "PC1", "22", "-100", "-100", "0", "12", "CCN1", "11", "8"],
        ["SVC", "HC:99213", "-100", "-100", "", "1"],
    ]
    # BPR02 must equal -100 + 100 (the correction below) = 0
    body += [
        ["CLP", "PC1", "1", "100", "100", "0", "12", "CCN2", "11", "7"],
        ["SVC", "HC:99213", "100", "100", "", "1"],
    ]
    return interchange(body, isa13=isa13)


def test_pending_reversal_resolves_when_the_original_arrives(tmp_path):
    original = interchange(
        header("ORIG", "100", payment="20260105")
        + [["CLP", "PC1", "1", "100", "100", "0", "12", "CCN1", "11", "1"],
           ["SVC", "HC:99213", "100", "100", "", "1"]],
        isa13="000000001",
    )  # fmt: skip
    reversal = _reversal_file("REV", "000000002")
    wh = Warehouse(tmp_path / "wh")
    wh.load([write(tmp_path, "2.835", reversal)])
    assert wh.table("pending_reversals")["patient_control_number"].to_list() == ["PC1"]
    assert wh.table("claim_final")["net_paid_cents"].to_list() == [10000]
    wh.load([write(tmp_path, "1.835", original)])
    assert wh.table("pending_reversals").height == 0
    (row,) = wh.table("claim_final").to_dicts()
    assert (row["net_paid_cents"], row["chain_length"], row["payment_month"]) == (
        10000, 3, "2026-02",
    )  # fmt: skip


def test_claim_events_are_append_only_across_loads(tmp_path):
    a = interchange(header("T1", "100") + paid_claim("P1", "C1"), isa13="000000001")
    b = interchange(header("T2", "50") + paid_claim("P2", "C2", amount="50", charge="50"),
                    isa13="000000002")  # fmt: skip
    wh = Warehouse(tmp_path / "wh")
    wh.load([write(tmp_path, "a.835", a)])
    first = wh.table("claim_events")
    wh.load([write(tmp_path, "b.835", b)])
    after = wh.table("claim_events")
    assert after.height == 2
    assert after.filter(pl.col("event_id").is_in(first["event_id"])).equals(first)
    assert sorted(after["load_seq"].to_list()) == [1, 2]


@pytest.mark.parametrize("name", ["claims", "ledger", "quarantine"])
def test_empty_warehouse_tables_are_readable(tmp_path, name):
    assert Warehouse(tmp_path / "none").table(name).height == 0


def _two_files(tmp_path):
    a = interchange(header("T1", "100") + paid_claim("P1", "C1"), isa13="000000001")
    b = interchange(
        header("T2", "50") + paid_claim("P2", "C2", amount="50", charge="50"), isa13="000000002"
    )
    return [write(tmp_path, "a.835", a), write(tmp_path, "b.835", b)]


def _snapshot(wh):
    names = ("remittances", "claims", "claim_events", "claim_final", "ledger", "quarantine")
    return {n: wh.table(n).drop("load_seq", strict=False).to_dicts() for n in names}


@pytest.mark.parametrize("fail_at", range(1, 13))
def test_interrupted_commit_leaves_a_consistent_warehouse_after_retry(
    tmp_path, monkeypatch, fail_at
):
    """Crash at every os.replace of a load: the retry must end where a clean load ends.

    Call 1 is the commit rename; calls 2.. swap the tables in. A crash at 1 leaves the old
    state untouched, a crash later is rolled forward the next time the warehouse is opened.
    """
    files = _two_files(tmp_path)
    clean = Warehouse(tmp_path / "clean")
    clean.load(files)

    wh = Warehouse(tmp_path / "wh")
    real = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == fail_at:
            raise OSError("simulated crash")
        return real(src, dst)

    monkeypatch.setattr("era835.warehouse.os.replace", flaky)
    with pytest.raises(OSError, match="simulated crash"):
        wh.load(files)
    monkeypatch.setattr("era835.warehouse.os.replace", real)

    survivor = Warehouse(tmp_path / "wh")
    survivor.load(files)  # the retry
    assert _snapshot(survivor) == _snapshot(clean)
    assert survivor.table("quarantine").height == 0  # never mistaken for duplicate_trace
    assert survivor.table("ledger").height == 2
    assert not (tmp_path / "wh/.pending").exists()
    assert not (tmp_path / "wh/.staging").exists()


def test_crash_before_commit_keeps_the_previous_state(tmp_path, monkeypatch):
    files = _two_files(tmp_path)
    wh = Warehouse(tmp_path / "wh")
    wh.load(files[:1])
    before = _snapshot(wh)
    real = os.replace

    def crash(src, dst):
        raise OSError("simulated crash")

    monkeypatch.setattr("era835.warehouse.os.replace", crash)
    with pytest.raises(OSError):
        wh.load(files[1:])
    monkeypatch.setattr("era835.warehouse.os.replace", real)
    assert _snapshot(Warehouse(tmp_path / "wh")) == before


def test_group_with_no_transactions_is_quarantined_at_file_level(tmp_path):
    text = interchange([], transactions=[], ge01=3)
    wh = Warehouse(tmp_path / "wh")
    (outcome,) = wh.load([write(tmp_path, "empty.835", text)]).files
    assert outcome.status == "quarantined"
    q = wh.table("quarantine")
    assert q["rule"].to_list() == ["group_count"]
    assert q["remittance_id"].to_list() == [None]
    assert wh.table("remittances").height == 0


def test_empty_interchange_with_wrong_counts_reports_each_fault(tmp_path):
    text = interchange([], transactions=[], ge01=0, iea01=4, iea02="999999999")
    wh = Warehouse(tmp_path / "wh")
    wh.load([write(tmp_path, "empty.835", text)])
    assert sorted(wh.table("quarantine")["rule"].to_list()) == [
        "interchange_control",
        "interchange_count",
    ]


def test_interchange_with_no_transactions_at_all_is_not_silently_loaded(tmp_path):
    text = interchange([], transactions=[], ge01=0)
    wh = Warehouse(tmp_path / "wh")
    (outcome,) = wh.load([write(tmp_path, "empty.835", text)]).files
    assert outcome.status == "quarantined"
    assert wh.table("quarantine")["rule"].to_list() == ["no_transactions"]


def test_nonempty_group_faults_are_not_reported_twice(tmp_path):
    body = header("T1", "100") + paid_claim("P1", "C1")
    text = interchange(body, iea01=9)
    wh = Warehouse(tmp_path / "wh")
    wh.load([write(tmp_path, "x.835", text)])
    assert wh.table("quarantine")["rule"].to_list() == ["interchange_count"]
