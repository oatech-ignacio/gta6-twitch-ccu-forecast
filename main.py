"""GTA VI launch-day Twitch CCU quantile forecaster (Python 3.13, standard library only).

Analog ("comparable launches") forecaster:

  1. Every past launch in the case's `comparables` is turned into a curve of log-ratios
     r_h = ln(viewers in hour h / viewers in hour 1) for the hours 2..24 of its UTC release day.
  2. Each comparable gets a weight = prior (unlock pattern, platforms, tier, read from its notes)
     x a Gaussian kernel on features known at the checkpoint: hour 1's size, hour 1 relative to the
     previous 24 hours' peak ("is the launch already live?") and the trend into hour 1.
  3. Per hour: weighted median of the log-ratios (smoothed over neighbouring hours) is the median
     forecast; the spread comes from the comparables' weighted quantiles around it (25/75 for the
     inner pair, a 25/75 + 10/90 blend for the 5/95 tails), separately below and above the median,
     with a random-walk floor.
  4. If minute-level reads are present (the final case), the level at the end of hour 1 relative
     to the hour's mean nudges the first forecast hours (momentum that the hourly print hides).

Run: mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
"""

import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]

# Model parameters (few, tuned coarsely on the leave-one-out backtest in backtest.py).
PARAMS = {
    "bw_size": 1.6,      # kernel bandwidth on ln(hour-1 viewers)
    "bw_live": 0.8,      # kernel bandwidth on ln(hour 1 / previous-24h peak)
    "bw_trend": 0.6,     # kernel bandwidth on ln(hour 1 / mean of the 3 hours before)
    "smooth": 1,         # half-width (hours) of the smoothing of the median curve
    "spread_mult": 0.9,  # scale of the inner (25/75) spread
    "tail_mult": 0.95,   # scale of the tail (5/95) spread
    "tail_w": 0.5,       # share of the 10/90 quantiles (vs the 25/75 ones) in the tail spread
    "floor": 0.12,       # random-walk floor of the spread, per sqrt(hour)
    "mom_w": 0.7,        # weight of the end-of-hour-1 momentum at hour 2
    "mom_decay": 3.0,    # e-folding (hours) of that momentum
    "mom_clip": 0.35,    # largest momentum adjustment, in log units
}


def ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def hourly_map(rows, key: str, day_start: float) -> dict:
    """{hour offset from day_start: mean viewers} from rows carrying an ISO time under `key`."""
    acc: dict = {}
    for r in rows or []:
        v = r.get("viewers")
        t = r.get(key)
        if v is None or t is None:
            continue
        k = math.floor((ts(t) - day_start) / 3600.0 + 1e-9)
        s = acc.setdefault(k, [0.0, 0])
        s[0] += v
        s[1] += 1
    return {k: s[0] / s[1] for k, s in acc.items()}


def features(H: dict, p1: float) -> tuple:
    """(ln size, ln(p1 / previous-24h peak), ln(p1 / mean of previous 3 hours)) at the checkpoint."""
    prev24 = [H[k] for k in range(-24, 0) if k in H and H[k] > 0]
    prev3 = [H[k] for k in range(-3, 0) if k in H and H[k] > 0]
    live = math.log(p1 / max(prev24)) if prev24 else 0.0
    trend = math.log(p1 / (sum(prev3) / len(prev3))) if prev3 else live
    clip = lambda x, a: max(-a, min(a, x))
    return (math.log(p1), clip(live, 3.0), clip(trend, 2.0))


def prior_weight(comp: dict) -> float:
    """How GTA-VI-like a comparable's launch is, from its unlock note and platforms."""
    unlock = (comp.get("unlock") or "").lower()
    plat = (comp.get("platforms") or "").lower()
    w = 1.0
    if "tier: weak" in unlock or "weak analogue" in unlock or "weak gta analogue" in unlock:
        w *= 0.4
    if "early-access-contaminated" in unlock:
        w *= 0.6
    if "rolling" in unlock or "midnight local" in unlock:
        w *= 1.8
    if "single" in unlock and "global" in unlock and "rolling" not in unlock:
        w *= 0.5
    if "pc" not in plat:
        w *= 1.4  # console-only, like GTA VI
    return w


def build_comps(case: dict) -> list:
    comps = []
    for c in case.get("comparables") or []:
        try:
            ds = ts(c["day_start"])
        except Exception:
            continue
        H = hourly_map(c.get("hourly"), "from", ds)
        p1 = H.get(0)
        if p1 is None or p1 < 1:
            continue
        ratios = {}
        for h in range(2, 25):
            v = H.get(h - 1)
            if v is not None:
                ratios[h] = math.log(max(v, 1.0) / p1)
        if len(ratios) < 12:
            continue
        comps.append({"name": c.get("name", ""), "f": features(H, p1), "r": ratios, "prior": prior_weight(c)})
    return comps


def wquantile(pairs: list, tau: float) -> float:
    """Weighted quantile (linear interpolation on the mid-point cumulative weights)."""
    pairs = sorted(pairs)
    tot = sum(w for _, w in pairs)
    if tot <= 0 or not pairs:
        return 0.0
    cum = 0.0
    pts = []
    for v, w in pairs:
        pts.append((cum + w / 2.0, v))
        cum += w
    target = tau * tot
    if target <= pts[0][0]:
        return pts[0][1]
    if target >= pts[-1][0]:
        return pts[-1][1]
    for i in range(1, len(pts)):
        if target <= pts[i][0]:
            c0, v0 = pts[i - 1]
            c1, v1 = pts[i]
            if c1 <= c0:
                return v1
            return v0 + (v1 - v0) * (target - c0) / (c1 - c0)
    return pts[-1][1]


def momentum(case: dict, day_start: float, p1: float) -> float:
    """ln(level at the end of hour 1 / hour-1 mean) from minute reads; 0 when history is hourly."""
    pts = []
    for r in case.get("history") or []:
        v, t = r.get("viewers"), r.get("at")
        if v is None or t is None:
            continue
        off = ts(t) - day_start
        if 0 <= off < 3600:
            pts.append((off, v))
    if len(pts) < 20:
        return 0.0
    last = [v for off, v in pts if off >= 2700]
    if len(last) < 5:
        return 0.0
    lvl = sum(last) / len(last)
    if lvl <= 0:
        return 0.0
    return math.log(lvl / p1)


def model(case: dict, P: dict = PARAMS) -> dict:
    """({hour: (median log-ratio, inner-low, inner-high, tail-low, tail-high sigmas)}, hour-1 print)."""
    day_start = ts(case["day_start"])
    first = next((k for k in case["known"] if k["hour"] == 1), None)
    p1 = max(1.0, float(first["viewers"])) if first else 1.0
    H = hourly_map(case.get("history_hourly"), "from", day_start)
    if not H:
        H = hourly_map(case.get("history"), "at", day_start)
    f = features(H, p1)
    comps = build_comps(case)
    bws = (P["bw_size"], P["bw_live"], P["bw_trend"])
    for c in comps:
        d2 = sum(((c["f"][i] - f[i]) / bws[i]) ** 2 for i in range(3))
        c["w"] = c["prior"] * math.exp(-0.5 * d2)
    tot = sum(c["w"] for c in comps)
    if comps and tot < 1e-6:  # nothing near: fall back to the priors alone
        for c in comps:
            c["w"] = c["prior"]
    # Per hour: weighted quantiles of the comparables' log-ratios. The inner pair (25/75) and the
    # tail pair (10/90) are both turned into Gaussian-equivalent sigmas, separately below and above.
    med, il, ih, tl, th = {}, {}, {}, {}, {}
    for h in range(2, 25):
        pairs = [(c["r"][h], c["w"]) for c in comps if h in c["r"] and c["w"] > 0]
        if len(pairs) < 3:
            fb = 0.15 * math.sqrt(h - 1)
            med[h], il[h], ih[h], tl[h], th[h] = 0.0, fb, fb, fb, fb
            continue
        m = wquantile(pairs, 0.5)
        med[h] = m
        il[h] = max(0.0, m - wquantile(pairs, 0.25)) / 0.674
        ih[h] = max(0.0, wquantile(pairs, 0.75) - m) / 0.674
        tl[h] = max(0.0, m - wquantile(pairs, 0.10)) / 1.2816
        th[h] = max(0.0, wquantile(pairs, 0.90) - m) / 1.2816
    k = int(P["smooth"])
    mom = max(-P["mom_clip"], min(P["mom_clip"], momentum(case, day_start, p1)))
    out = {}
    for h in range(2, 25):
        hs = list(range(max(2, h - k), min(24, h + k) + 1))
        avg = lambda d: sum(d[g] for g in hs) / len(hs)
        m = avg(med)
        if h == 2:
            m = (2 * med[2] + med[3]) / 3.0  # keep hour 2 close to its own median: it anchors on hour 1
        m += P["mom_w"] * mom * math.exp(-(h - 2) / P["mom_decay"])
        fl = P["floor"] * math.sqrt(h - 1)
        tw = P["tail_w"]
        s_il = max(fl, P["spread_mult"] * avg(il))
        s_ih = max(fl, P["spread_mult"] * avg(ih))
        s_tl = max(s_il, P["tail_mult"] * (tw * avg(tl) + (1 - tw) * avg(il)))
        s_th = max(s_ih, P["tail_mult"] * (tw * avg(th) + (1 - tw) * avg(ih)))
        out[h] = (m, s_il, s_ih, s_tl, s_th)
    return out, p1


def r2(x: float) -> float:
    return math.floor(x * 100 + 0.5) / 100


def forecast_case(case: dict, P: dict = PARAMS) -> dict:
    curve, p1 = model(case, P)
    rows = []
    for row in case["forecast"]:
        h = row["hour"]
        fb = 0.15 * math.sqrt(max(1, h - 1))
        m, s_il, s_ih, s_tl, s_th = curve.get(h, (0.0, fb, fb, fb, fb))
        q = []
        for z in Z:
            sig = 0.0 if z == 0 else (s_tl if z < -1 else s_il if z < 0 else s_ih if z < 1 else s_th)
            v = p1 * math.exp(m + z * sig)
            v = min(1e9, max(0.01, r2(v)))
            if q and v < q[-1]:
                v = q[-1]
            q.append(v)
        rows.append({"hour": h, "q": q})
    return {"hours": rows}


def baseline_case(case: dict) -> dict:
    """The house baseline (persistence), for the backtest and as a crash fallback."""
    first = next((k for k in case["known"] if k["hour"] == 1), None)
    p1 = max(1.0, float(first["viewers"])) if first else 1.0
    return {"hours": [{"hour": r["hour"], "q": [r2(p1 * math.exp(z * 0.15 * math.sqrt(r["hour"] - 1))) for z in Z]}
                      for r in case["forecast"]]}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        try:
            fc = forecast_case(case)
        except Exception:
            fc = baseline_case(case)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(json.dumps(fc, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
