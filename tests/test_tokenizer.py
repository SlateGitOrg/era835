import pytest

from era835.errors import ParseError
from era835.tokenizer import detect_delimiters, tokenize
from x12 import header, interchange, paid_claim


def test_detects_standard_separators(star):
    d = detect_delimiters(star)
    assert (d.element, d.component, d.terminator) == ("*", ":", "~")


def test_detects_pipe_caret_newline(pipe):
    d = detect_delimiters(pipe)
    assert (d.element, d.component, d.terminator) == ("|", "^", "\n")


def test_segments_ignore_newlines_after_terminator(star):
    _, segments = tokenize(star)
    assert segments[0].id == "ISA"
    assert segments[-1].id == "IEA"
    assert all("\n" not in s.id for s in segments)


def test_crlf_terminated_file():
    text = interchange(header("T1", "0"), element="|", component="^", terminator="\n")
    crlf = text.replace("\n", "\r\n")
    d, segments = tokenize(crlf)
    assert d.terminator == "\n"
    assert segments[-1].id == "IEA" and segments[-1].el(1) == "1"


def test_element_accessor_is_one_based_and_total():
    _, segments = tokenize(interchange(header("T1", "0")))
    trn = next(s for s in segments if s.id == "TRN")
    assert trn.el(2) == "T1"
    assert trn.el(40) == ""


@pytest.mark.parametrize(
    "text",
    ["", "GS*HP~", "ISA*00*short~", "XXX" + "A" * 120],
)
def test_rejects_non_isa_input(text):
    with pytest.raises(ParseError):
        tokenize(text)


def test_rejects_isa_with_wrong_widths():
    good = interchange(header("T1", "0") + paid_claim("P1", "C1"))
    broken = good.replace("*SENDER         *", "*SENDER*", 1)
    with pytest.raises(ParseError):
        tokenize(broken)
