"""Regenerate tests/fixtures/*.835 (run once by hand; the files are committed)."""

from pathlib import Path

from x12 import header, interchange

BODY = header("TRACE-0001", "158") + [
    ["CLP", "PCN-A", "1", "300", "165.5", "45", "12", "CCN-A", "11", "1"],
    ["CAS", "PR", "3", "15", "1"],
    ["NM1", "QC", "1", "DOE", "JANE", "", "", "", "MI", "MBR0000001"],
    ["SVC", "HC:99213", "200", "120", "", "1"],
    ["CAS", "CO", "45", "50"],
    ["CAS", "PR", "1", "20", "", "2", "10"],
    ["AMT", "B6", "150"],
    ["SVC", "HC:93000:25", "100", "60.5", "", "2"],
    ["CAS", "CO", "45", "39.5"],
    ["CLP", "PCN-B", "4", "80", "0", "0", "12", "CCN-B", "11", "1"],
    ["SVC", "HC:99214", "80", "0", "", "1"],
    ["CAS", "CO", "50", "80"],
    ["PLB", "1999990001", "20261231", "L6:INT01", "-2.5", "WO:REC01", "10"],
]

here = Path(__file__).parent / "fixtures"
here.mkdir(exist_ok=True)
(here / "remit_star.835").write_text(interchange(BODY, newline=True))
(here / "remit_pipe.835").write_text(interchange(BODY, element="|", component="^", terminator="\n"))
