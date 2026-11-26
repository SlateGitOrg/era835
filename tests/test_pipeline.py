"""End-to-end checks against the seeded synthetic data and its recorded ground truth."""

from __future__ import annotations

import polars as pl

from baselines import last_row_per_claim_total, naive_claim_rows
from era835.denials import denials
from era835.reconcile import read_deposits, reconcile
from era835.synth import generate
from era835.warehouse import Warehouse


def test_generator_is_deterministic(tmp_path, dataset):
    again = generate(tmp_path / "again", seed=835)
    assert again == dataset["truth"]
    for path in dataset["files"]:
        assert (tmp_path / "again/files" / path.name).read_bytes() == path.read_bytes()
    assert generate(tmp_path / "other", seed=7)["files"] != dataset["truth"]["files"]


def test_dataset_has_the_documented_shape(truth):
    assert len(truth["files"]) == 41  # 40 remittance files plus one re-delivery
    assert truth["claim_segments"] == 3000
    assert 7000 <= truth["service_lines"] <= 8000
    assert len(truth["chains"]) == 120
    assert len(truth["out_of_order_originals"]) == 15
    assert len(truth["payers"]) == 6
    assert sorted(p["reason"] for p in truth["plb"]) == ["L6", "L6", "WO", "WO"]
    assert [p["cents"] < 0 for p in truth["plb"] if p["reason"] == "L6"] == [True, True]


def test_one_payer_writes_pipe_caret_newline(dataset):
    texts = [p.read_text() for p in dataset["files"]]
    assert any(t[3] == "|" and t[104] == "^" and t[105] == "\n" for t in texts)
    assert any(t[3] == "*" and t[105] == "~" for t in texts)


def test_altered_files_are_quarantined_with_the_recorded_difference(loaded, truth):
    q = loaded.table("quarantine")
    for m in truth["malformed"]:
        rows = q.filter(pl.col("file_name") == m["file"]).to_dicts()
        assert [r["rule"] for r in rows] == [m["rule"]]
        if m["rule"] == "claim_balance":
            assert rows[0]["claim"] == m["claim"]
            assert rows[0]["difference_cents"] == m["difference_cents"]
    rules = sorted(m["rule"] for m in truth["malformed"])
    assert rules == ["claim_balance"] * 3 + ["segment_count"]
    assert q.height == 4


def test_nothing_from_quarantined_files_reaches_the_warehouse(loaded, truth):
    bad_traces = {m["trace"] for m in truth["malformed"]}
    assert not bad_traces & set(loaded.table("remittances")["trace_number"].to_list())
    bad_claims = set(truth["quarantined_claims"])
    assert bad_claims
    for table in ("claims", "claim_events", "claim_final"):
        got = set(loaded.table(table)["patient_control_number"].to_list())
        assert not got & bad_claims, table


def test_every_loaded_remittance_balances_to_the_cent(loaded):
    claims = loaded.table("claims").group_by("remittance_id").agg(pl.col("paid_cents").sum())
    plb = (
        loaded.table("provider_adjustments")
        .group_by("remittance_id")
        .agg(pl.col("amount_cents").sum().alias("plb"))
    )
    joined = (
        loaded.table("remittances")
        .join(claims, on="remittance_id")
        .join(plb, on="remittance_id", how="left")
        .with_columns((pl.col("paid_cents") - pl.col("plb").fill_null(0)).alias("net"))
    )
    assert joined.height == 36
    assert joined.filter(pl.col("net") != pl.col("bpr02_cents")).height == 0


def test_provider_adjustments_keep_their_signs(loaded, truth):
    plb = loaded.table("provider_adjustments")
    assert sorted(plb["amount_cents"].to_list()) == sorted(p["cents"] for p in truth["plb"])
    assert set(plb.filter(pl.col("amount_cents") < 0)["reason_code"]) == {"L6"}
    assert set(plb.filter(pl.col("amount_cents") > 0)["reason_code"]) == {"WO"}


def test_redelivered_file_is_a_no_op(dataset, loaded, tmp_path, truth):
    names = {f["file"] for f in truth["files"]}
    assert truth["duplicate"]["original"] in names and truth["duplicate"]["redelivery"] in names
    ledger = loaded.table("ledger")
    assert ledger.height == 40  # the 41st delivery was skipped
    assert truth["duplicate"]["redelivery"] not in ledger["file_name"].to_list()
    copy = Warehouse(tmp_path / "copy")
    copy.load(dataset["files"])
    before = {t: copy.table(t).height for t in ("remittances", "claims", "service_lines")}
    again = dict(zip((p.name for p in dataset["files"]), dataset["files"], strict=True))
    report = copy.load([again[truth["duplicate"]["redelivery"]]])
    assert [f.status for f in report.files] == ["duplicate"]
    assert {t: copy.table(t).height for t in before} == before


def test_claim_final_matches_truth_for_every_claim(loaded, truth):
    final = {r["patient_control_number"]: r for r in loaded.table("claim_final").to_dicts()}
    assert set(final) == set(truth["claims"])
    for pcn, expect in truth["claims"].items():
        row = final[pcn]
        assert row["net_paid_cents"] == expect["net_paid_cents"], pcn
        assert row["net_patient_resp_cents"] == expect["net_patient_resp_cents"], pcn
        assert row["final_status"] == expect["final_status"], pcn
        assert row["chain_length"] == expect["chain_length"], pcn
        assert row["payer_name"] == expect["payer"], pcn


def test_each_chain_nets_to_its_correction(loaded, truth):
    final = {r["patient_control_number"]: r for r in loaded.table("claim_final").to_dicts()}
    for pcn in truth["chains"]:
        assert final[pcn]["chain_length"] == 3
        assert final[pcn]["net_paid_cents"] == truth["claims"][pcn]["net_paid_cents"]
    claim_final = loaded.table("claim_final")
    assert claim_final["patient_control_number"].n_unique() == claim_final.height


def test_pending_reversals_exist_mid_load_and_clear_at_the_end(dataset, loaded, tmp_path, truth):
    assert loaded.table("pending_reversals").height == 0
    first_moved = min(
        i for i, p in enumerate(dataset["files"]) if p.name in truth["out_of_order_originals"]
    )
    partial = Warehouse(tmp_path / "partial")
    partial.load(dataset["files"][:first_moved])
    pending = partial.table("pending_reversals")
    assert pending.height > 0
    assert set(pending["patient_control_number"]) <= set(truth["chains"])
    partial.load(dataset["files"][first_moved:])
    assert partial.table("pending_reversals").height == 0
    assert partial.table("claim_final").equals(loaded.table("claim_final"))


def test_load_order_does_not_change_claim_final(loaded, loaded_reverse):
    assert loaded_reverse.table("pending_reversals").height == 0
    for name in ("claim_final", "claims", "remittances", "service_lines", "quarantine"):
        assert loaded.table(name).equals(loaded_reverse.table(name)), name


def test_reconcile_ties_every_loaded_eft_and_lists_quarantined(dataset, loaded, truth):
    deposits = read_deposits(dataset["deposits"])
    assert {t: c for t, (_, c) in deposits.items()} == truth["deposits"]
    table = reconcile(loaded, deposits)
    assert table.height == 40
    status = dict(zip(table["trace_number"], table["status"], strict=True))
    bad = {m["trace"] for m in truth["malformed"]}
    assert {t for t, s in status.items() if s == "quarantined"} == bad
    tied = table.filter(pl.col("status") == "tied")
    assert tied.height == 36
    assert tied.filter(pl.col("difference_cents") != 0).height == 0
    assert (tied["bpr02_cents"] == tied["deposit_cents"]).all()


def test_reconcile_reports_a_1234_cent_deposit_difference(dataset, loaded):
    deposits = read_deposits(dataset["deposits"])
    trace = next(iter(loaded.table("remittances").sort("trace_number")["trace_number"]))
    when, cents = deposits[trace]
    deposits[trace] = (when, cents + 1234)
    row = reconcile(loaded, deposits).filter(pl.col("trace_number") == trace).to_dicts()[0]
    assert (row["status"], row["difference_cents"]) == ("difference", 1234)
    assert reconcile(loaded, deposits).filter(pl.col("status") == "difference").height == 1


def test_naive_loads_miscount_and_depend_on_order(dataset, loaded, loaded_reverse, truth):
    skip = {m["file"] for m in truth["malformed"]}
    forward = naive_claim_rows(dataset["files"], skip)
    backward = naive_claim_rows(list(reversed(dataset["files"])), skip)
    assert len(forward) == loaded.table("claim_final").height + 240
    assert len(forward) == loaded.table("claims").height
    assert last_row_per_claim_total(forward) != last_row_per_claim_total(backward)
    final_total = loaded.table("claim_final")["net_paid_cents"].sum()
    assert final_total == loaded_reverse.table("claim_final")["net_paid_cents"].sum()
    assert final_total == sum(c["net_paid_cents"] for c in truth["claims"].values())
    assert last_row_per_claim_total(forward) != final_total


def test_denials_match_the_injected_totals(loaded, truth):
    by_reason = denials(loaded, by_payer=False)
    got = {r["carc"]: (r["claims"], r["denied_cents"]) for r in by_reason.to_dicts()}
    expect = {k: (v["claims"], v["cents"]) for k, v in truth["denials"].items()}
    assert got == expect
    assert got["CO-50"][0] > 50
    by_payer = denials(loaded)
    co50 = by_payer.filter(pl.col("carc") == "CO-50")
    assert co50["denied_cents"].sum() == truth["denials"]["CO-50"]["cents"]
    assert co50["claims"].sum() == truth["denials"]["CO-50"]["claims"]
    assert by_payer["denied_cents"].to_list() == sorted(by_payer["denied_cents"], reverse=True)
