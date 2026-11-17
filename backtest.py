"""Leave-one-out backtest of main.py against the house baseline.

Every comparable launch in a kit case becomes a pseudo-case: its history up to the end of its hour 1
is the input, its hours 2..24 are the answer, and the forecast may use only the *other* comparables.
Scored with the contest's exact loss (mean pinball loss on the log scale).

The four practice cases' own answers are not in the kit (a practice case's comparables leave out every
practice launch), so they cannot be scored locally; the script only checks that their forecasts are valid.

Usage: python3 backtest.py [kit input dir]   (default: ../gta6-twitch-ccu-forecast-01-kit/input)
"""

import json
import math
import sys
from pathlib import Path

import main as M

LEVELS = M.LEVELS


def pinball(q: list, y: float) -> float:
    y = max(1.0, y)
    tot = 0.0
    for tau, qv in zip(LEVELS, q):
        d = math.log(y) - math.log(qv)
        tot += 2 * max(tau * d, (tau - 1) * d)
    return tot / len(LEVELS)


def score(fc: dict, truth: dict) -> tuple:
    """(mean loss, coverage hits, hours) of a forecast against {hour: viewers}."""
    losses, cover = [], 0
    for row in fc["hours"]:
        y = truth.get(row["hour"])
        if y is None:
            continue
        losses.append(pinball(row["q"], y))
        cover += row["q"][0] <= max(1.0, y) <= row["q"][4]
    return (sum(losses) / len(losses) if losses else float("nan"), cover, len(losses))


def pseudo_cases(case: dict) -> list:
    """One pseudo-case per comparable launch that has an hour 1 and enough of its release day."""
    out = []
    comps = case["comparables"]
    for i, c in enumerate(comps):
        ds = M.ts(c["day_start"])
        H = M.hourly_map(c["hourly"], "from", ds)
        if 0 not in H or sum(1 for h in range(1, 24) if h in H) < 12:
            continue
        hist = [{"at": r["from"], "viewers": r["viewers"], "channels": r.get("channels")}
                for r in c["hourly"] if M.ts(r["from"]) - ds < 3600]
        pc = {
            "id": c["name"], "day_start": c["day_start"], "levels": LEVELS,
            "known": [{"hour": 1, "viewers": H[0]}],
            "forecast": [{"hour": h} for h in range(2, 25)],
            "history": hist, "history_hourly": None, "streams": None, "categories_hourly": None,
            "comparables": comps[:i] + comps[i + 1:],
        }
        truth = {h: H[h - 1] for h in range(2, 25) if (h - 1) in H}
        out.append((pc, truth, c))
    return out


def gta_like(pc: dict, c: dict) -> bool:
    """The launches most like the final case: already live in hour 1 (>= 20k viewers), not weak-tier."""
    u = c["unlock"].lower()
    return pc["known"][0]["viewers"] >= 20000 and "tier: weak" not in u


def validate(fc: dict) -> bool:
    hrs = sorted(r["hour"] for r in fc["hours"])
    if hrs != list(range(2, 25)):
        return False
    for r in fc["hours"]:
        q = r["q"]
        if len(q) != 5 or any(not (0 < x <= 1e9) or not math.isfinite(x) for x in q):
            return False
        if any(q[i] > q[i + 1] for i in range(4)):
            return False
    return True


def run(kit: Path, P: dict = M.PARAMS, verbose: bool = True) -> dict:
    case = json.loads((kit / "p00.in").read_text(encoding="utf-8"))
    rows = []
    for pc, truth, c in pseudo_cases(case):
        lm, cov, n = score(M.forecast_case(pc, P), truth)
        lb, covb, _ = score(M.baseline_case(pc), truth)
        rows.append((pc["id"], lm, lb, cov, covb, n, gta_like(pc, c)))
    res = {}
    for label, sel in (("all", rows), ("gta-like", [r for r in rows if r[6]])):
        n = sum(r[5] for r in sel)
        res[label] = (sum(r[1] for r in sel) / len(sel), sum(r[2] for r in sel) / len(sel),
                      sum(r[3] for r in sel) / n, sum(r[4] for r in sel) / n, len(sel))
    if verbose:
        print(f"{'launch':44s} {'model':>8s} {'baseline':>8s} cover  like")
        for r in rows:
            print(f"{r[0][:44]:44s} {r[1]:8.4f} {r[2]:8.4f} {r[3]:2d}/{r[5]:2d}  {'*' if r[6] else ''}")
        for label, v in res.items():
            print(f"LOO {label:9s} n={v[4]:2d}  model {v[0]:.4f} (cover {v[2]:.2f})   baseline {v[1]:.4f} (cover {v[3]:.2f})")
        for p in sorted(kit.glob("*.in")):
            pc = json.loads(p.read_text(encoding="utf-8"))
            fc = M.forecast_case(pc, P)
            med = {r["hour"]: r["q"][2] for r in fc["hours"]}
            p1 = pc["known"][0]["viewers"]
            print(f"practice {p.stem}: valid={validate(fc)} hour1={p1} median ratio h2/h8/h14/h20/h24 = "
                  + "/".join(f"{med[h] / p1:.2f}" for h in (2, 8, 14, 20, 24)) + "  (answers not in kit: not scored)")
    return res


if __name__ == "__main__":
    kit = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "gta6-twitch-ccu-forecast-01-kit" / "input"
    run(kit)
