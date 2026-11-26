import pytest

from era835.money import AmountError, format_dollars, format_x12, parse_cents


@pytest.mark.parametrize(
    ("text", "cents"),
    [("12.5", 1250), ("100", 10000), ("0.05", 5), ("-2.5", -250), (".5", 50), ("7.", 700),
     ("10.500", 1050), ("+3", 300)],
)  # fmt: skip
def test_parse_cents(text, cents):
    assert parse_cents(text) == cents


@pytest.mark.parametrize("text", ["", "abc", "1e3", "1,000.00", "10.005", "1.2.3", "--4"])
def test_parse_cents_rejects(text):
    with pytest.raises(AmountError):
        parse_cents(text)


@pytest.mark.parametrize("cents", [0, 5, 10, 1250, -250, 10000, 123456789])
def test_format_round_trip(cents):
    assert parse_cents(format_x12(cents)) == cents


def test_format_x12_drops_trailing_zeros():
    assert format_x12(1250) == "12.5"
    assert format_x12(12000) == "120"
    assert format_x12(10) == "0.1"


def test_format_dollars():
    assert format_dollars(-123456) == "-$1,234.56"
    assert format_dollars(None) == "-"


def test_thousand_dimes_are_exactly_ten_thousand_cents():
    assert sum(parse_cents("0.10") for _ in range(1000)) == 10_000
    # The float equivalent drifts, which is why no float is ever used.
    drifting = 0.0
    for _ in range(1000):
        drifting += 0.1
    assert drifting != 100.0
