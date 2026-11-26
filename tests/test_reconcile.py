import pytest

from conftest import write
from era835.reconcile import read_deposits, reconcile
from era835.warehouse import Warehouse
from x12 import header, interchange, paid_claim


@pytest.fixture
def wh(tmp_path, star):
    bad = interchange(header("BAD1", "55") + paid_claim("P2", "C2"), isa13="000000002")
    warehouse = Warehouse(tmp_path / "wh")
    warehouse.load([write(tmp_path, "a.835", star), write(tmp_path, "b.835", bad)])
    return warehouse


def by_trace(table):
    return {r["trace_number"]: r for r in table.to_dicts()}


def test_read_deposits_parses_decimal_dollars(tmp_path):
    path = write(tmp_path, "d.csv", "trace_number,deposit_date,amount\nT1,2026-01-06,1234.56\n")
    assert read_deposits(path) == {"T1": ("2026-01-06", 123456)}


def test_loaded_eft_ties_and_quarantined_eft_is_not_a_difference(wh):
    deposits = {"TRACE-0001": ("2026-01-06", 15800), "BAD1": ("2026-01-06", 10000)}
    rows = by_trace(reconcile(wh, deposits))
    assert rows["TRACE-0001"]["status"] == "tied"
    assert rows["TRACE-0001"]["warehouse_net_cents"] == 15800
    assert rows["BAD1"]["status"] == "quarantined"
    assert rows["BAD1"]["difference_cents"] is None


def test_altered_deposit_reports_exactly_that_difference(wh):
    deposits = {"TRACE-0001": ("2026-01-06", 15800 + 1234)}
    row = by_trace(reconcile(wh, deposits))["TRACE-0001"]
    assert row["status"] == "difference"
    assert row["difference_cents"] == 1234
    assert row["bpr02_cents"] == 15800


def test_missing_and_unmatched_deposits(wh):
    rows = by_trace(reconcile(wh, {"STRAY": ("2026-01-07", 500)}))
    assert rows["TRACE-0001"]["status"] == "no_deposit"
    assert rows["STRAY"]["status"] == "unmatched_deposit"


def test_month_filter(wh):
    deposits = {"TRACE-0001": ("2026-01-06", 15800)}
    assert reconcile(wh, deposits, month="2026-02").height == 0
    assert reconcile(wh, deposits, month="2026-01").height == 2


def test_two_payers_sharing_a_trace_number_stay_separate_rows(tmp_path):
    a = interchange(
        header("SAME", "100", payer="Payer A") + paid_claim("P1", "C1"), isa13="000000001"
    )
    b = interchange(
        header("SAME", "100", payer="Payer B") + paid_claim("P2", "C2"), isa13="000000002"
    )
    warehouse = Warehouse(tmp_path / "wh")
    warehouse.load([write(tmp_path, "a.835", a), write(tmp_path, "b.835", b)])
    assert warehouse.table("remittances").height == 2
    table = reconcile(warehouse, {"SAME": ("2026-01-06", 10000)})
    assert sorted(table["payer_name"].to_list()) == ["Payer A", "Payer B"]
    assert set(table["status"]) == {"ambiguous_trace"}
