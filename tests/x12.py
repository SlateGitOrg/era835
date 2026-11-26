"""A tiny, independent 835 builder for test fixtures (does not use the synthetic generator)."""

from __future__ import annotations

Seg = list[str]


def interchange(
    body: list[Seg],
    *,
    element: str = "*",
    component: str = ":",
    terminator: str = "~",
    newline: bool = False,
    isa13: str = "000000001",
    gs06: str = "1",
    st02: str = "0001",
    se01: int | None = None,
    se02: str | None = None,
    ge01: int = 1,
    ge02: str | None = None,
    iea01: int = 1,
    iea02: str | None = None,
    transactions: list[list[Seg]] | None = None,
) -> str:
    """Wrap ``body`` (segments between ST and SE) in a complete ISA..IEA envelope.

    ``transactions`` lets a test put several ST/SE pairs in one group; each entry is a body.
    """
    isa = [
        "ISA", "00", " " * 10, "00", " " * 10, "ZZ", "SENDER".ljust(15), "ZZ",
        "RECEIVER".ljust(15), "260105", "1200", "^" if component != "^" else ":", "00501",
        isa13, "0", "P", component,
    ]  # fmt: skip
    tail = terminator + ("\n" if newline else "")
    out = element.join(isa) + tail
    bodies = transactions if transactions is not None else [body]
    segments: list[Seg] = [["GS", "HP", "SENDER", "RECEIVER", "20260105", "1200", gs06, "X", "x"]]
    for n, tx_body in enumerate(bodies):
        st = st02 if n == 0 else f"{n + 1:04d}"
        segments.append(["ST", "835", st])
        segments += tx_body
        count = len(tx_body) + 2 if (se01 is None or n > 0) else se01
        segments.append(["SE", str(count), se02 or st])
    segments.append(["GE", str(ge01), ge02 or gs06])
    segments.append(["IEA", str(iea01), iea02 or isa13])
    for seg in segments:
        out += element.join(s.replace(":", component) for s in seg) + tail
    return out


def header(
    trace: str, bpr02: str, payment: str = "20260105", payer: str = "Test Payer"
) -> list[Seg]:
    return [
        ["BPR", "I", bpr02, "C", "ACH", "CCP", "01", "999999999", "DA", "1234", "1999999999", "",
         "01", "888888888", "DA", "5678", payment],
        ["TRN", "1", trace, "1999999999"],
        ["DTM", "405", payment],
        ["N1", "PR", payer, "XV", "TP01"],
        ["N1", "PE", "Test Provider", "XX", "1999990001"],
    ]  # fmt: skip


def paid_claim(pcn: str, ccn: str, status: str = "1", amount: str = "100", charge: str = "100"):
    """One claim with a single unadjusted line, so it balances by construction."""
    return [
        ["CLP", pcn, status, charge, amount, "0", "12", ccn, "11", "1"],
        ["SVC", "HC:99213", charge, amount, "", "1"],
    ]
