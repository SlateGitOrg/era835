"""Seeded synthetic 835 data with injected, recorded patterns.

Everything here is fictional: payers, the provider group, patient names and identifiers.
The generator writes the ground truth next to the files so tests (and users) can check that
the loader recovers it.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from era835.synth.types import Adj, GClaim, GLine, GRemit, Payer, Style
from era835.synth.writer import deposit_date, render_remittance

STANDARD = Style("*", ":", "~", newline_after=False)
STANDARD_NL = Style("*", ":", "~", newline_after=True)
PIPE = Style("|", "^", "\n")

PAYERS = [
    Payer(0, "Alder Mutual Health", "ALD01", "alder-mutual", "ALD", STANDARD),
    Payer(1, "Brightwater Health Plan", "BRW02", "brightwater", "BRW", STANDARD_NL),
    Payer(2, "Cobalt Ridge Benefits", "CBR03", "cobalt-ridge", "CBR", STANDARD),
    Payer(3, "Dunmore Care Partners", "DNM04", "dunmore", "DNM", PIPE),
    Payer(4, "Elmstead Health Alliance", "ELM05", "elmstead", "ELM", STANDARD_NL),
    Payer(5, "Fennel Valley Insurance", "FNV06", "fennel-valley", "FNV", STANDARD),
]
FILES_PER_PAYER = [7, 7, 7, 7, 6, 6]
PROCEDURES = [
    "99213", "99214", "99203", "99395", "93000", "71046", "80053", "85025",
    "36415", "97110", "20610", "90834", "99283", "72148", "81002",
]  # fmt: skip
LAST = ["Arden", "Bishop", "Castell", "Dorrance", "Everly", "Fairfax", "Garrity", "Hollis"]
FIRST = ["Ada", "Ben", "Cora", "Dev", "Eli", "Faye", "Gus", "Hana", "Ira", "Jun"]

N_CHAINS = 120
CHAINS_SAME_FILE = 40
CHAINS_THIRD_FILE = 20  # correction arrives in a third, later file
N_OUT_OF_ORDER = 15
TOTAL_CLAIM_SEGMENTS = 3000
DENIAL_REASONS = [("CO", "50"), ("CO", "16"), ("CO", "97")]
DENIAL_WEIGHTS = [60, 32, 8]


def _adj_sum(adjs: list[Adj]) -> int:
    return sum(a[2] for a in adjs)


class _Ids:
    def __init__(self) -> None:
        self.pcn = 1_000_000
        self.ccn: dict[int, int] = defaultdict(int)
        self.member = 4_000_000

    def next_pcn(self) -> str:
        self.pcn += 1
        return f"PC{self.pcn:07d}"

    def next_ccn(self, payer: Payer) -> str:
        self.ccn[payer.idx] += 1
        return f"{payer.prefix}{self.ccn[payer.idx]:09d}"

    def next_member(self) -> str:
        self.member += 1
        return f"MBR{self.member:07d}"


def _people(rng: random.Random, ids: _Ids) -> dict[str, Any]:
    return {
        "patient_id": ids.next_member(),
        "last": rng.choice(LAST),
        "first": rng.choice(FIRST),
        "npi": f"1{rng.randrange(10**8, 10**9)}",
    }


def _paid_claim(
    rng: random.Random, pcn: str, ccn: str, svc_date: date, who: dict[str, Any],
    *, secondary: bool = False, frequency: str = "1", kind: str = "standalone",
) -> GClaim:  # fmt: skip
    lines: list[GLine] = []
    for i in range(rng.choices([1, 2, 3, 4], weights=[17, 30, 31, 22])[0]):
        charge = rng.randrange(4500, 62000)
        code = rng.choice(PROCEDURES)
        units = rng.choice([1, 1, 1, 2])
        if i > 0 and rng.random() < 0.06:
            lines.append(GLine(code, charge, 0, 0, units, [("CO", "97", charge)]))
            continue
        allowed = charge * rng.randrange(48, 86) // 100
        adjs: list[Adj] = [("CO", "45", charge - allowed)]
        remaining = allowed
        if rng.random() < 0.3:
            deductible = min(remaining, rng.choice([2500, 5000, 7500]))
            adjs.append(("PR", "1", deductible))
            remaining -= deductible
        if rng.random() < 0.35:
            coinsurance = remaining * 20 // 100
            if coinsurance:
                adjs.append(("PR", "2", coinsurance))
                remaining -= coinsurance
        lines.append(GLine(code, charge, remaining, allowed, units, adjs))
    line_paid = sum(ln.paid for ln in lines)
    claim_adjs: list[Adj] = []
    if secondary:
        other = line_paid * rng.randrange(5, 25) // 100
        if other:
            claim_adjs.append(("OA", "23", other))
    elif rng.random() < 0.4:
        copay = min(line_paid, rng.choice([2000, 2500, 3000]))
        if copay:
            claim_adjs.append(("PR", "3", copay))
    patient_resp = sum(a[2] for a in claim_adjs if a[0] == "PR") + sum(
        a[2] for ln in lines for a in ln.adjustments if a[0] == "PR"
    )
    return GClaim(
        pcn=pcn, status="2" if secondary else "1", ccn=ccn, frequency=frequency,
        charge=sum(ln.charge for ln in lines), paid=line_paid - _adj_sum(claim_adjs),
        patient_resp=patient_resp, adjustments=claim_adjs, lines=lines,
        service_date=svc_date, kind=kind, **who,
    )  # fmt: skip


def _denied_claim(
    rng: random.Random, pcn: str, ccn: str, svc_date: date, who: dict[str, Any]
) -> GClaim:
    group, reason = rng.choices(DENIAL_REASONS, weights=DENIAL_WEIGHTS)[0]
    lines = []
    for _ in range(rng.choices([1, 2, 3], weights=[50, 35, 15])[0]):
        charge = rng.randrange(4500, 62000)
        lines.append(GLine(rng.choice(PROCEDURES), charge, 0, 0, 1, [(group, reason, charge)]))
    return GClaim(
        pcn=pcn, status="4", ccn=ccn, frequency="1", charge=sum(ln.charge for ln in lines),
        paid=0, patient_resp=0, adjustments=[], lines=lines, service_date=svc_date, **who,
    )  # fmt: skip


def _negate(c: GClaim) -> GClaim:
    """A reversal repeats the original with every amount negated and status 22."""
    return GClaim(
        pcn=c.pcn, status="22", ccn=c.ccn, frequency="8", charge=-c.charge, paid=-c.paid,
        patient_resp=-c.patient_resp,
        adjustments=[(g, r, -a) for g, r, a in c.adjustments],
        lines=[
            GLine(ln.code, -ln.charge, -ln.paid, -ln.allowed, ln.units,
                  [(g, r, -a) for g, r, a in ln.adjustments])
            for ln in c.lines
        ],
        patient_id=c.patient_id, last=c.last, first=c.first, npi=c.npi,
        service_date=c.service_date, kind="rev",
    )  # fmt: skip


def _schedule(rng: random.Random) -> list[GRemit]:
    remits: list[GRemit] = []
    isa = 700_000
    for payer, count in zip(PAYERS, FILES_PER_PAYER, strict=True):
        start = date(2026, 1, 2) + timedelta(days=rng.randrange(0, 7))
        for i in range(count):
            when = start + timedelta(days=17 * i + rng.randrange(-2, 3))
            isa += rng.randrange(1, 40)
            trace = f"EFT{payer.idx}{when:%Y%m%d}{rng.randrange(100, 999)}"
            remits.append(GRemit(payer, when, trace, isa))
    return remits


def _plan_chains(
    rng: random.Random, eligible: list[GRemit]
) -> list[tuple[str, GRemit, GRemit, GRemit]]:
    by_payer: dict[int, list[GRemit]] = defaultdict(list)
    for r in eligible:
        by_payer[r.payer.idx].append(r)
    kinds = (
        ["same"] * CHAINS_SAME_FILE
        + ["third"] * CHAINS_THIRD_FILE
        + ["later"] * (N_CHAINS - CHAINS_SAME_FILE - CHAINS_THIRD_FILE)
    )
    rng.shuffle(kinds)
    plans = []
    for kind in kinds:
        if kind == "same":
            a = rng.choice(eligible)
            plans.append((kind, a, a, a))
            continue
        while True:
            a = rng.choice(eligible)
            bs = [r for r in by_payer[a.payer.idx] if 14 <= (r.date - a.date).days <= 42]
            if not bs:
                continue
            b = rng.choice(bs)
            if kind == "later":
                plans.append((kind, a, b, b))
                break
            cs = [r for r in by_payer[a.payer.idx] if (r.date - b.date).days >= 7]
            if cs:
                plans.append((kind, a, b, rng.choice(cs)))
                break
    return plans


def generate(out_dir: str | Path, seed: int = 835) -> dict[str, Any]:
    """Write ``files/*.835``, ``deposits.csv`` and ``truth.json``; return the truth dict."""
    rng = random.Random(seed)
    ids = _Ids()
    out = Path(out_dir)
    (out / "files").mkdir(parents=True, exist_ok=True)

    remits = _schedule(rng)
    malformed = rng.sample(remits, 4)
    for r, kind in zip(malformed, ["claim_balance"] * 3 + ["segment_count"], strict=True):
        r.malformed = kind
    eligible = [r for r in remits if r.malformed is None]

    # --- reversal chains (never touch the malformed files) -------------------------------
    chain_pcns: list[str] = []
    final_of: dict[str, GClaim] = {}
    reversal_files: dict[int, list[int]] = defaultdict(list)  # id(original file) -> reversers
    for _, a, b, c in _plan_chains(rng, eligible):
        pcn, who = ids.next_pcn(), _people(rng, ids)
        svc = a.date - timedelta(days=rng.randrange(20, 60))
        original = _paid_claim(rng, pcn, ids.next_ccn(a.payer), svc, who, kind="orig")
        while True:
            fixed = _paid_claim(
                rng, pcn, ids.next_ccn(a.payer), svc, who, frequency="7", kind="corr"
            )
            if fixed.paid != original.paid:
                break
        a.claims.append(original)
        b.claims.append(_negate(original))
        c.claims.append(fixed)
        chain_pcns.append(pcn)
        final_of[pcn] = fixed
        if a is not b:
            reversal_files[id(a)].append(id(b))

    # --- standalone claims ---------------------------------------------------------------
    remaining = TOTAL_CLAIM_SEGMENTS - sum(len(r.claims) for r in remits)
    weights = [rng.uniform(0.6, 1.4) for _ in remits]
    counts = [int(remaining * w / sum(weights)) for w in weights]
    counts[0] += remaining - sum(counts)
    standalone: dict[str, GClaim] = {}
    for r, n in zip(remits, counts, strict=True):
        for _ in range(n):
            pcn, who = ids.next_pcn(), _people(rng, ids)
            svc = r.date - timedelta(days=rng.randrange(15, 75))
            ccn = ids.next_ccn(r.payer)
            roll = rng.random()
            if roll < 0.07:
                claim = _denied_claim(rng, pcn, ccn, svc, who)
            else:
                claim = _paid_claim(rng, pcn, ccn, svc, who, secondary=roll > 0.93)
            r.claims.append(claim)
            standalone[pcn] = claim

    # --- provider-level adjustments --------------------------------------------------------
    plb_files = rng.sample(eligible, 4)
    for r, (reason, ref, cents) in zip(
        plb_files,
        [
            ("L6", "INT0126", -rng.randrange(800, 4500)),
            ("L6", "INT0326", -rng.randrange(800, 4500)),
            ("WO", "RCP0226", rng.randrange(5000, 40000)),
            ("WO", "RCP0426", rng.randrange(5000, 40000)),
        ],
        strict=True,
    ):
        r.plb.append((reason, ref, cents))

    # --- malformed files: alter one claim-level CAS so the claim is out of balance --------
    for r in malformed:
        if r.malformed != "claim_balance":
            continue
        candidates = [c for c in r.claims if c.adjustments and c.adjustments[0][2] >= 400]
        victim = rng.choice(candidates)
        group, reason, amount = victim.adjustments[0]
        delta = rng.randrange(100, amount // 2)
        r.altered = (victim.pcn, delta)
        # The file records a CAS amount that is `delta` lower than the true one; the payment
        # (CLP04), the charge and BPR02 stay as the payer really paid them.
        victim.adjustments[0] = (group, reason, amount - delta)

    for r in remits:
        rng.shuffle(r.claims)
        assert r.bpr02 >= 0, r.trace

    # --- delivery order ----------------------------------------------------------------------
    order = sorted(remits, key=lambda r: (r.date, r.payer.idx))
    by_id = {id(r): r for r in remits}
    candidates_moved = [by_id[i] for i in reversal_files]
    rng.shuffle(candidates_moved)
    moved = candidates_moved[:N_OUT_OF_ORDER]
    if len(moved) < N_OUT_OF_ORDER:
        raise RuntimeError("too few files hold chain originals; change the seed")
    # Latest first: a moved file that itself reverses another moved file's claims is already
    # in its final place when the earlier original is moved behind it.
    for a in sorted(moved, key=lambda r: (r.date, r.payer.idx), reverse=True):
        order.remove(a)
        last_reverser = max(order.index(by_id[i]) for i in reversal_files[id(a)])
        order.insert(last_reverser + 1, a)
    dup_source = rng.choice([r for r in order if r.malformed is None and r not in moved])
    slots: list[tuple[GRemit, bool]] = [(r, False) for r in order]
    at = [i for i, (r, _) in enumerate(slots) if r is dup_source][0]
    slots.insert(min(len(slots), at + rng.randrange(3, 9)), (dup_source, True))

    # --- write files -------------------------------------------------------------------------
    names: dict[tuple[int, bool], str] = {}
    delivery: list[dict[str, Any]] = []
    first_name: dict[int, str] = {}
    for rank, (r, is_dup) in enumerate(slots, start=1):
        name = f"{rank:03d}_{r.payer.payer_id.lower()}_{r.date:%Y%m%d}.835"
        text = render_remittance(r, gs06=r.isa13 % 1000 + 1)
        (out / "files" / name).write_bytes(text.encode("utf-8"))
        names[(id(r), is_dup)] = name
        if not is_dup:
            first_name[id(r)] = name
        delivery.append({"file": name, "trace": r.trace, "redelivery": is_dup})

    deposits = sorted((r.trace, deposit_date(r.date), r.bpr02) for r in remits)
    with open(out / "deposits.csv", "w", newline="") as handle:
        handle.write("trace_number,deposit_date,amount\n")
        for trace, when, cents in deposits:
            handle.write(f"{trace},{when},{cents // 100}.{cents % 100:02d}\n")

    # --- truth ---------------------------------------------------------------------------------
    loadable = [r for r in remits if r.malformed is None]
    claims_truth: dict[str, Any] = {}
    bad = {c.pcn for r in malformed for c in r.claims}
    payer_of = {c.pcn: r.payer.name for r in remits for c in r.claims}
    for pcn, c in {**standalone, **final_of}.items():
        if pcn in bad:
            continue
        claims_truth[pcn] = {
            "net_paid_cents": c.paid,
            "net_patient_resp_cents": c.patient_resp,
            "final_status": {"1": "primary", "2": "secondary", "4": "denied"}[c.status],
            "chain_length": 3 if pcn in final_of else 1,
            "payer": payer_of[pcn],
        }
    denials: dict[str, dict[str, Any]] = {}
    for r in loadable:
        for c in r.claims:
            if c.status != "4":
                continue
            for ln in c.lines:
                for g, reason, amount in ln.adjustments:
                    entry = denials.setdefault(f"{g}-{reason}", {"cents": 0, "claims": set()})
                    entry["cents"] += amount
                    entry["claims"].add(c.pcn)
    truth = {
        "seed": seed,
        "files": delivery,
        "claim_segments": sum(len(r.claims) for r in remits),
        "service_lines": sum(len(c.lines) for r in remits for c in r.claims),
        "deposits": {t: cents for t, _, cents in deposits},
        "claims": claims_truth,
        "chains": chain_pcns,
        "quarantined_claims": sorted(c.pcn for r in remits if r.malformed for c in r.claims),
        "malformed": [
            {
                "file": first_name[id(r)],
                "trace": r.trace,
                "rule": r.malformed,
                "claim": r.altered[0] if r.altered else None,
                "difference_cents": r.altered[1] if r.altered else None,
            }
            for r in malformed
        ],
        "duplicate": {
            "original": names[(id(dup_source), False)],
            "redelivery": names[(id(dup_source), True)],
        },
        "out_of_order_originals": sorted(first_name[id(a)] for a in moved),
        "plb": [
            {"file": first_name[id(r)], "trace": r.trace, "reason": p[0], "cents": p[2]}
            for r in remits
            for p in r.plb
        ],
        "denials": {
            k: {"cents": v["cents"], "claims": len(v["claims"])} for k, v in sorted(denials.items())
        },
        "payers": [p.name for p in PAYERS],
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=1, sort_keys=True) + "\n")
    return truth
