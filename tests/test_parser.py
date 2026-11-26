from datetime import date

import pytest

from era835.errors import ParseError
from era835.parser import parse_interchange
from x12 import header, interchange, paid_claim


def test_parses_fixture(star):
    inter = parse_interchange(star)
    assert inter.isa13 == "000000001"
    rem = inter.transactions[0].remittance
    assert rem is not None
    assert rem.bpr02_cents == 15800
    assert rem.trace_number == "TRACE-0001"
    assert rem.payment_date == date(2026, 1, 5)
    assert rem.payer_name == "Test Payer"
    assert [c.patient_control_number for c in rem.claims] == ["PCN-A", "PCN-B"]


def test_claim_fields_and_levels(star):
    rem = parse_interchange(star).transactions[0].remittance
    a, b = rem.claims
    assert (a.charge_cents, a.paid_cents, a.patient_resp_cents) == (30000, 16550, 4500)
    assert a.patient_id == "MBR0000001"
    assert [(x.group, x.reason, x.amount_cents) for x in a.adjustments] == [("PR", "3", 1500)]
    assert a.adjustments[0].quantity == "1"
    assert b.status_code == "4"


def test_cas_triplets_with_blank_quantity(star):
    line = parse_interchange(star).transactions[0].remittance.claims[0].lines[0]
    assert [(x.group, x.reason, x.amount_cents) for x in line.adjustments] == [
        ("CO", "45", 5000),
        ("PR", "1", 2000),
        ("PR", "2", 1000),
    ]
    assert line.allowed_cents == 15000


def test_service_line_composite(star):
    lines = parse_interchange(star).transactions[0].remittance.claims[0].lines
    assert (lines[1].procedure_qualifier, lines[1].procedure_code) == ("HC", "93000")
    assert lines[1].modifiers == ("25",)
    assert lines[1].units == "2"
    assert lines[1].paid_cents == 6050


def test_plb_pairs_keep_sign(star):
    plb = parse_interchange(star).transactions[0].remittance.provider_adjustments
    assert [(p.reason_code, p.reference_id, p.amount_cents) for p in plb] == [
        ("L6", "INT01", -250),
        ("WO", "REC01", 1000),
    ]
    assert plb[0].period_end == date(2026, 12, 31)


def test_bad_amount_is_recorded_on_the_transaction_not_raised():
    body = header("T1", "10") + [["CLP", "P1", "1", "10.005", "10", "0", "12", "C1"]]
    tx = parse_interchange(interchange(body)).transactions[0]
    assert tx.remittance is None
    assert "more than two decimal places" in (tx.error or "")


@pytest.mark.parametrize(
    "bad_segment",
    [["CAS", "ZZ", "45", "5"], ["SVC", "99213", "10", "10"], ["CAS", "CO", "45", "5"]],
)
def test_structural_body_errors(bad_segment):
    body = header("T1", "10")
    if bad_segment[0] == "CAS" and bad_segment[1] == "CO":
        pass  # CAS before any claim
    else:
        body += [["CLP", "P1", "1", "10", "10", "0", "12", "C1"]]
    tx = parse_interchange(interchange(body + [bad_segment])).transactions[0]
    assert tx.remittance is None and tx.error


def test_reversal_requires_payer_claim_number():
    body = header("T1", "-10") + [["CLP", "P1", "22", "-10", "-10", "0", "12", ""]]
    assert parse_interchange(interchange(body)).transactions[0].error


def test_missing_trailer_is_a_file_level_error():
    text = interchange(header("T1", "0") + paid_claim("P1", "C1", amount="0", charge="0"))
    with pytest.raises(ParseError):
        parse_interchange(text.rsplit("IEA", 1)[0])


def test_thousand_service_lines_sum_to_exact_cents():
    lines = [["SVC", "HC:99213", "0.10", "0.10", "", "1"] for _ in range(1000)]
    body = header("T1", "100") + [["CLP", "P1", "1", "100", "100", "0", "12", "C1"]] + lines
    rem = parse_interchange(interchange(body)).transactions[0].remittance
    assert sum(line.paid_cents for line in rem.claims[0].lines) == 10_000
