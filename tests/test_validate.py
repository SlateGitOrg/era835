import pytest

from era835.parser import parse_interchange
from era835.validate import check_group, check_interchange, check_transaction
from x12 import header, interchange, paid_claim


def violations(text):
    inter = parse_interchange(text)
    out = check_interchange(inter)
    for group in inter.groups:
        out += check_group(group)
        for tx in group.transactions:
            out += check_transaction(tx)
    return out


def test_fixture_with_interest_and_recoupment_balances(star):
    # 165.50 claim payment + 0.00 denied claim - (-2.50 interest + 10.00 recoupment) = 158.00
    assert violations(star) == []


def test_plb_sign_convention_each_direction():
    # A recoupment (positive) lowers BPR02; interest (negative) raises it.
    claim = paid_claim("P1", "C1", amount="100", charge="100")
    recoup = header("T1", "90") + claim + [["PLB", "NPI", "20261231", "WO:R1", "10"]]
    interest = header("T2", "105") + claim + [["PLB", "NPI", "20261231", "L6:I1", "-5"]]
    assert violations(interchange(recoup)) == []
    assert violations(interchange(interest)) == []
    wrong = header("T3", "110") + claim + [["PLB", "NPI", "20261231", "WO:R1", "10"]]
    (v,) = violations(interchange(wrong))
    assert (v.rule, v.difference_cents) == ("payment_balance", -2000)


def test_line_balance_reports_the_difference():
    body = header("T1", "100") + [
        ["CLP", "P1", "1", "100", "100", "0", "12", "C1"],
        ["SVC", "HC:99213", "100", "100", "", "1"],
        ["CAS", "CO", "45", "30"],
    ]
    rules = {(v.rule, v.claim, v.difference_cents) for v in violations(interchange(body))}
    # Line: (100.00 - 30.00) - 100.00 = -30.00; the same CAS also unbalances the claim.
    assert ("line_balance", "P1", -3000) in rules
    assert ("claim_balance", "P1", -3000) in rules


def test_claim_level_cas_counts_toward_claim_balance_only():
    body = header("T1", "80") + [
        ["CLP", "P1", "1", "100", "80", "20", "12", "C1"],
        ["CAS", "PR", "3", "20"],
        ["SVC", "HC:99213", "100", "100", "", "1"],
    ]
    # Claim: 100 - 20 - 0 = 80 = CLP04. Line: 100 - 0 = 100 = SVC03.
    assert violations(interchange(body)) == []


def test_claim_balance_difference_is_computed_minus_reported():
    body = header("T1", "100") + [
        ["CLP", "P1", "1", "100", "100", "0", "12", "C1"],
        ["CAS", "OA", "23", "7"],
    ]
    (v,) = violations(interchange(body))
    assert (v.rule, v.claim, v.difference_cents) == ("claim_balance", "P1", -700)


def test_payment_balance_failure():
    body = header("T1", "99.99") + paid_claim("P1", "C1")
    (v,) = violations(interchange(body))
    assert (v.rule, v.claim, v.difference_cents) == ("payment_balance", None, 1)


def test_negated_reversal_balances():
    body = (
        header("T1", "0")
        + paid_claim("P1", "C1", amount="100")
        + [
            ["CLP", "P1", "22", "-100", "-100", "0", "12", "C1"],
            ["SVC", "HC:99213", "-100", "-100", "", "1"],
        ]
    )
    assert violations(interchange(body)) == []


@pytest.mark.parametrize(
    ("kwargs", "rule"),
    [
        ({"se01": 99}, "segment_count"),
        ({"se02": "0009"}, "transaction_control"),
        ({"ge01": 2}, "group_count"),
        ({"ge02": "77"}, "group_control"),
        ({"iea01": 3}, "interchange_count"),
        ({"iea02": "123456789"}, "interchange_control"),
    ],
)
def test_envelope_controls(kwargs, rule):
    body = header("T1", "100") + paid_claim("P1", "C1")
    assert [v.rule for v in violations(interchange(body, **kwargs))] == [rule]


def test_se01_counts_st_and_se_themselves():
    body = header("T1", "100") + paid_claim("P1", "C1")
    inter = parse_interchange(interchange(body))
    assert inter.transactions[0].segment_count == len(body) + 2
