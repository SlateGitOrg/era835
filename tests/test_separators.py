from era835.normalize import normalize
from era835.parser import parse_interchange


def rows(text):
    inter = parse_interchange(text)
    tx = inter.transactions[0]
    return normalize(
        tx.remittance, source_sha256="0" * 64, isa13=inter.isa13, st02=tx.st02, load_seq=1
    )


def test_star_colon_tilde_and_pipe_caret_newline_give_identical_rows(star, pipe):
    assert "*" in star and "~" in star and "|" in pipe and "~" not in pipe
    star_rows, pipe_rows = rows(star), rows(pipe)
    assert star_rows == pipe_rows
    assert len(star_rows["service_lines"]) == 3
    # The composite code was split on the component separator in both files.
    assert [r["modifiers"] for r in star_rows["service_lines"]] == ["", "25", ""]
