import itertools
from datetime import date

from era835.chains import collapse


def ev(eid, pcn, ccn, status, paid, when, payer="P", resp=0):
    return {
        "event_id": eid,
        "patient_control_number": pcn,
        "payer_name": payer,
        "payer_claim_control_number": ccn,
        "status_code": status,
        "paid_cents": paid,
        "patient_resp_cents": resp,
        "payment_date": when,
        "payment_month": when.strftime("%Y-%m"),
        "remittance_id": "r-" + eid,
    }


ORIG = ev("a", "PC1", "CCN1", "1", 10000, date(2026, 1, 5), resp=500)
REV = ev("b", "PC1", "CCN1", "22", -10000, date(2026, 2, 9), resp=-500)
CORR = ev("c", "PC1", "CCN2", "1", 8000, date(2026, 2, 9), resp=300)


def test_chain_collapses_in_every_arrival_order():
    results = set()
    for perm in itertools.permutations([ORIG, REV, CORR]):
        final, pending = collapse(list(perm))
        assert pending == []
        (row,) = final
        results.add(tuple(sorted(row.items())))
        assert row["net_paid_cents"] == 8000
        assert row["net_patient_resp_cents"] == 300
        assert row["final_status"] == "primary"
        assert row["chain_length"] == 3
        assert row["final_claim_control_number"] == "CCN2"
    assert len(results) == 1


def test_reversal_without_correction_is_reversed_and_nets_zero():
    (row,), pending = collapse([ORIG, REV])
    assert pending == []
    assert (row["final_status"], row["net_paid_cents"], row["chain_length"]) == ("reversed", 0, 2)


def test_reversal_waits_until_its_original_arrives():
    final, pending = collapse([REV, CORR])
    assert [p["event_id"] for p in pending] == ["b"]
    assert final[0]["net_paid_cents"] == 8000 and final[0]["chain_length"] == 1
    final, pending = collapse([REV, CORR, ORIG])
    assert pending == []
    assert final[0]["chain_length"] == 3


def test_reversal_alone_produces_no_final_row():
    final, pending = collapse([REV])
    assert final == [] and len(pending) == 1


def test_reversal_for_a_different_patient_control_number_does_not_match():
    other = ev("x", "PC9", "CCN1", "22", -10000, date(2026, 2, 9))
    final, pending = collapse([ORIG, other])
    assert [p["event_id"] for p in pending] == ["x"]
    assert final[0]["net_paid_cents"] == 10000


def test_later_correction_supersedes_without_a_reversal():
    first = ev("a", "PC1", "CCN1", "1", 10000, date(2026, 1, 5))
    later = ev("c", "PC1", "CCN2", "1", 7000, date(2026, 3, 1))
    for order in ([first, later], [later, first]):
        (row,), _ = collapse(order)
        assert row["net_paid_cents"] == 7000 and row["last_payment_date"] == date(2026, 3, 1)


def test_two_payers_both_count():
    primary = ev("a", "PC1", "CCN1", "1", 10000, date(2026, 1, 5), payer="P")
    secondary = ev("b", "PC1", "S1", "2", 1500, date(2026, 1, 20), payer="S")
    (row,), _ = collapse([secondary, primary])
    assert row["net_paid_cents"] == 11500
    assert row["final_status"] == "secondary"


def test_denial_status():
    (row,), _ = collapse([ev("d", "PC2", "D1", "4", 0, date(2026, 1, 5))])
    assert row["final_status"] == "denied"
