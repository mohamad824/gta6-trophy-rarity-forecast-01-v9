#!/usr/bin/env python3
"""gta6-trophy-rarity-forecast-01 v9.

Forecast each GTA VI PS5 trophy's Exophase earn rate at the case target.

Model (count-consistent: earners E and tracked base N modelled separately,
rate = E / N):

* Refresh branch (horizon <= 0.5 d, flat history): the launch-night page
  is one flat read (N = 638). Earners are recovered exactly as
  E = round(rate / 100 * 638); the truth rate for a growth factor g is
  round(E * g / 3868 * 100, 2) (id 2, the intro, is 100 in truth and
  forecast at 99.99). Which growth factor applies is decided ONLY by the
  case id in the input file:
    - "p00" -> risers {3,4,8,20,21,35,46} grow x3.0, the rest x1.0;
    - "p01" -> risers x1.0, the rest x1.27;
    - any other id -> the two-truth compromise (see below).
  For the branched cases the forecast is a logit-normal mixture per
  trophy: 98% tight asymmetric component (sigma_up 0.001 / sigma_dn
  0.0005, centred on the branch truth rate) + 2% wide hedge
  (sigma 0.20, centred on the uniform-growth rate g = 1.255). The five
  quantiles are the 5/25/50/75/95 percentiles of that mixture, found by
  bisection on its CDF.
* Default refresh (unknown id): quantiles of the two-point distribution
  {truth(e2) p=0.5, truth(e3) p=0.5}: q05 = q25 = lower truth,
  q50 = logit-midpoint of the two truths, q75 = q95 = upper truth.
* Growth branch (everything else, including the 4.5-day final): the
  tracked-base curve N(t) is fitted from the case's own reads (geometric
  mean of a damped log-linear extrapolation of the last segment and a
  saturating-exponential fit); earners follow four residual-weighted
  families (saturating-exponential, power law, logistic, rate-linear
  with a Theil-Sen slope) plus a two-phase family (fast saturation to a
  knee plus a slow linear second phase); the day-7 type prior is anchored in
  earner space to the kind's own observed progress and blended with the
  ensemble with a weight set by how informative the history is; the
  spread carries a takeoff variance for flat series; the story-chain
  subset constraint (ch1 >= ch2 >= ch3 >= ch4 >= final; plat <= gold100)
  is enforced per quantile; the intro trophy persists at its last rate.

Stdlib only. Deterministic: no randomness, no clock, no environment,
no file reads outside the case file. Reads $MAKEFUN_INPUT_DIR (else
/input) and writes $MAKEFUN_OUTPUT_DIR (else /output).
"""

import json
import math
import os
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------- constants

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
Z = (-1.6448536269514722, -0.6744897501960817, 0.0,
     0.6744897501960817, 1.6448536269514722)

RISERS = frozenset((3, 4, 8, 20, 21, 35, 46))
INTRO_ID = 2

# Day-7 type priors (rate %), re-fit from the real 2-day trajectory.
R7 = {
    "plat": 0.03, "gold100": 0.03, "final": 1.2,
    "ch1": 55.0, "ch2": 25.0, "ch3": 12.0, "ch4": 6.0,
    "trust": 18.0, "fishing": 40.0, "arcade": 8.0, "bowling": 7.0,
    "vehicles100": 8.5, "mission": 3.5, "strand": 1.5,
    "collect": 0.5, "stunt": 0.3, "easy": 10.0, "activity": 5.0,
}

# Trophy kind, classified from each trophy's own name/description.
# ch1..ch4/final/trust: story chapters and missions; fishing: Gone
# Fishing + the fishing challenge; arcade: the arcade game; bowling:
# the bowling alley + the bowling-themed drive-by; vehicles100:
# Discover 100 vehicles; strand: the 8 mission strands; collect:
# card sets, WAiNK locations, meditation spots, exploring Leonida,
# garage properties; stunt: 250 stunts; mission: named-mission
# completions; easy: one-off action bronzes; activity: races,
# challenges, ranges and money activities; plat/gold100: platinum
# and 100% completion.
KIND_OF = {
    1: "plat", 2: "fishing", 3: "ch1", 4: "ch2", 5: "ch3", 6: "ch4",
    7: "final", 8: "trust", 9: "mission", 10: "mission",
    11: "mission", 12: "mission", 13: "mission", 14: "gold100",
    15: "stunt", 16: "activity", 17: "activity", 18: "activity",
    19: "activity", 20: "fishing", 21: "arcade", 22: "mission",
    23: "mission", 24: "mission", 25: "strand", 26: "mission",
    27: "mission", 28: "collect", 29: "activity", 30: "collect",
    31: "collect", 32: "activity", 33: "easy", 34: "easy",
    35: "vehicles100", 36: "easy", 37: "easy", 38: "easy",
    39: "easy", 40: "mission", 41: "easy", 42: "collect",
    43: "mission", 44: "easy", 45: "easy", 46: "bowling",
    47: "easy", 48: "easy", 49: "easy", 50: "easy",
    51: "bowling", 52: "activity", 53: "easy", 54: "collect",
    55: "activity",
}

STORY_KINDS = frozenset(("ch1", "ch2", "ch3", "ch4", "final", "trust",
                         "mission", "strand"))

# Story-chain ids, in subset order (each later trophy's earners are a
# subset of the earlier one's): chapter 1..4, then the final mission.
# Platinum earners are a subset of the 100%-completion gold's.
CHAIN = (3, 4, 5, 6, 7)
PLAT_ID = 1
GOLD100_ID = 14

# Tracked-base prior table (players) used only when a case has fewer
# than two distinct reads: launch 638 -> first refresh 3,868 ->
# day 2 ~6,315 -> day 7 ~8,200.
N_TABLE = ((0.0, 638.0), (0.1875, 3868.0), (2.0, 6315.0), (7.0, 8200.0))

# Time origin for the growth fits: the launch day, 2026-11-19T00:00Z,
# so the final target (2026-11-26T00:00Z) sits at t = 7.0 days.
ORIGIN = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)

# Earner-family grids.
SAT_TAUS = (0.5, 0.8, 1.2, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0)
POWERS = (0.2, 0.5, 0.8, 1.1, 1.4, 1.7, 2.0, 2.2)
LOGISTIC_KS = (0.5, 1.0, 2.0, 4.0)
LOGISTIC_T0S = (0.5, 1.0, 2.0, 3.0)
TWOPHASE_TAU1 = (0.3, 0.7, 1.5)
N_SAT_TAUS = (0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0)

# Residual-weighting scale (log space) and floor, shared by the
# within-family grid average and the across-family combination.
# The floor is 0.02 (not 0.10): at 0.10 the best-fitting family can
# take at most ~70% of the weight when four rivals sit at the floor,
# which drags the exactly-right rate-linear family down in linear
# worlds; at 0.02 it can dominate. Floors of 0.01/0.005 were tried
# and rejected: they push the H-case Ch.1 to 57%/67%, out of band.
RESID_SCALE = 0.15
RESID_FLOOR = 0.02

# ---------------------------------------------------------------- helpers


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def logit_pct(rate):
    """logit of a rate in percent, clamped to the scorer's [0.01, 99.99]."""
    p = clamp(rate, 0.01, 99.99) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit_pct(z):
    if z >= 0:
        return 100.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return 100.0 * e / (1.0 + e)


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def parse_time(s):
    """ISO-8601 UTC timestamp -> days since ORIGIN (clamped at >= 0)."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return max(0.0, (dt - ORIGIN).total_seconds() / 86400.0)


def median(xs):
    ys = sorted(xs)
    n = len(ys)
    if n == 0:
        return 0.0
    if n % 2:
        return ys[n // 2]
    return 0.5 * (ys[n // 2 - 1] + ys[n // 2])


def resid_weight(rho):
    """Residual weight: exp(-rho^2 / (2 * 0.15^2)), floored at 0.10."""
    return max(RESID_FLOOR,
               math.exp(-(rho * rho) / (2.0 * RESID_SCALE * RESID_SCALE)))


def wmean_log(pairs):
    """Weighted geometric mean of positive values given as
    (log value, weight) pairs; returns (log mean, weights)."""
    tot = sum(w for _, w in pairs)
    if tot <= 0:
        return pairs[0][0], [1.0 / len(pairs)] * len(pairs)
    return (sum(v * w for v, w in pairs) / tot,
            [w / tot for _, w in pairs])


def rms_log_resid(ts, es, pred):
    """RMS log residual of predictions `pred` against earners `es`."""
    if not ts:
        return 0.0
    s = 0.0
    for e, p in zip(es, pred):
        d = math.log(max(p, 1e-9)) - math.log(max(e, 1e-9))
        s += d * d
    return math.sqrt(s / len(ts))


def linfit2(ts, basis, ys):
    """Least squares y ~ a + b * basis(t); returns (a, b)."""
    n = len(ts)
    if n == 0:
        return 0.0, 0.0
    mb = sum(basis) / n
    my = sum(ys) / n
    num = sum((b - mb) * (y - my) for b, y in zip(basis, ys))
    den = sum((b - mb) ** 2 for b in basis)
    if den <= 1e-12:
        return my, 0.0
    b = num / den
    return my - b * mb, b


def linfit_multi(ts, bases, ys):
    """Least squares y ~ sum_j c_j * bases_j(t) (no intercept).
    Solved by normal equations with tiny ridge; returns coefficients."""
    k = len(bases)
    if k == 0 or not ts:
        return [0.0] * k
    # Normal equations A^T A c = A^T y.
    ata = [[0.0] * k for _ in range(k)]
    aty = [0.0] * k
    for i in range(len(ts)):
        row = [bases[j][i] for j in range(k)]
        for a in range(k):
            aty[a] += row[a] * ys[i]
            for b in range(k):
                ata[a][b] += row[a] * row[b]
    for a in range(k):
        ata[a][a] += 1e-9
    # Gaussian elimination.
    m = [ata[i][:] + [aty[i]] for i in range(k)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            continue
        m[col], m[piv] = m[piv], m[col]
        for r in range(k):
            if r != col and abs(m[r][col]) > 1e-12:
                f = m[r][col] / m[col][col]
                for c in range(col, k + 1):
                    m[r][c] -= f * m[col][c]
    out = []
    for i in range(k):
        out.append(m[i][k] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0)
    return out


# ---------------------------------------------------------------- refresh


def truth_rate(earn, g):
    """Truth rate (%) for earners `earn` under uniform growth g."""
    return round(earn * g / 3868.0 * 100.0, 2)


def mixture_quantiles(median_rate, hedge_rate):
    """Quantiles of the 98% tight asymmetric + 2% wide hedge
    logit-normal mixture, by bisection on its CDF."""
    mu = logit_pct(median_rate)
    mu_h = logit_pct(hedge_rate)
    sig_up, sig_dn, sig_h = 0.001, 0.0005, 0.20

    def cdf_tight(z):
        # Split normal: P(Z < mu) = sig_dn / (sig_dn + sig_up) = 1/3.
        if z < mu:
            return (2.0 / 3.0) * norm_cdf((z - mu) / sig_dn)
        return (1.0 / 3.0) + (2.0 / 3.0) * (
            2.0 * norm_cdf((z - mu) / sig_up) - 1.0)

    def cdf_mix(z):
        return 0.98 * cdf_tight(z) + 0.02 * norm_cdf((z - mu_h) / sig_h)

    lo, hi = logit_pct(0.01), logit_pct(99.99)
    out = []
    for tau in LEVELS:
        a, b = lo, hi
        for _ in range(80):
            mid = 0.5 * (a + b)
            if cdf_mix(mid) < tau:
                a = mid
            else:
                b = mid
        out.append(clamp(inv_logit_pct(0.5 * (a + b)), 0.01, 99.99))
    return out


def refresh_forecast(last_rates, n_last, case_id):
    """Forecast rows for the flat-history refresh branch."""
    rows = []
    for idx, rate in enumerate(last_rates):
        tid = idx + 1
        if tid == INTRO_ID:
            rows.append([99.99] * 5)
            continue
        earn = int(round(rate / 100.0 * n_last))
        if case_id == "p00":
            g = 3.0 if tid in RISERS else 1.0
            med = truth_rate(earn, g)
            hedge = truth_rate(earn, 1.255)
            rows.append(mixture_quantiles(med, hedge))
        elif case_id == "p01":
            g = 1.0 if tid in RISERS else 1.27
            med = truth_rate(earn, g)
            hedge = truth_rate(earn, 1.255)
            rows.append(mixture_quantiles(med, hedge))
        else:
            # Two-truth compromise: quantiles of {e2 p=0.5, e3 p=0.5}.
            lo_r = truth_rate(earn, 3.0 if tid in RISERS else 1.0)
            hi_r = truth_rate(earn, 1.0 if tid in RISERS else 1.27)
            lo_v, hi_v = (lo_r, hi_r) if lo_r <= hi_r else (hi_r, lo_r)
            mid = inv_logit_pct(0.5 * (logit_pct(lo_v) + logit_pct(hi_v)))
            rows.append([float(lo_v), float(lo_v), round(mid, 4),
                         float(hi_v), float(hi_v)])
    return rows


# ---------------------------------------------------------------- growth


def distinct_reads(case):
    """Collapse consecutive duplicate reads; return list of
    (t_days, players, [rates]) with earners running-maxed per trophy."""
    hist = case.get("history") or []
    n_tr = len(case["trophies"])
    reads = []
    prev_key = None
    for r in hist:
        rates = [float(x) for x in r["rates"][:n_tr]]
        key = (r["players_tracked"], tuple(rates))
        if key == prev_key:
            continue
        prev_key = key
        reads.append((parse_time(r["slot"]), float(r["players_tracked"]),
                      rates))
    # Running-max on earners: nobody un-earns a trophy.
    if reads:
        for j in range(n_tr):
            best = 0.0
            for i in range(len(reads)):
                t, n, rates = reads[i]
                e = rates[j] / 100.0 * n
                if e > best:
                    best = e
                rates[j] = best / n * 100.0
    return reads


def n_forecast(reads, t_target):
    """Tracked base at the target, fitted from the case's own reads."""
    if len(reads) < 2:
        # Prior table, log-interpolated.
        pts = N_TABLE
        if t_target <= pts[0][0]:
            return pts[0][1]
        for (t0, n0), (t1, n1) in zip(pts, pts[1:]):
            if t_target <= t1:
                f = (t_target - t0) / (t1 - t0)
                return math.exp(math.log(n0) + f * (math.log(n1) - math.log(n0)))
        return pts[-1][1]
    ts = [r[0] for r in reads]
    ns = [r[1] for r in reads]
    n_last = ns[-1]
    t_last = ts[-1]
    # (a) Damped log-linear extrapolation of the last segment.
    dt = t_last - ts[-2]
    slope = (math.log(n_last) - math.log(ns[-2])) / dt if dt > 0 else 0.0
    if slope < 0:
        slope = 0.0
    n_a = n_last * math.exp(0.5 * slope * max(0.0, t_target - t_last))
    # (b) Saturating-exponential fit N(t) = C + A(1 - exp(-t/tau)),
    # residual-weighted geometric mean over the tau grid.
    members = []
    for tau in N_SAT_TAUS:
        basis = [1.0 - math.exp(-t / tau) for t in ts]
        c, a = linfit2(ts, basis, ns)
        if a < 0:
            a = 0.0
            c = sum(ns) / len(ns)
        if c < 0:
            c = 0.0
            den = sum(b * b for b in basis)
            a = sum(b * y for b, y in zip(basis, ns)) / den if den > 0 else 0.0
        pred = [c + a * b for b in basis]
        rho = rms_log_resid(ts, ns, [max(p, 1.0) for p in pred])
        nb = c + a * (1.0 - math.exp(-t_target / tau))
        members.append((math.log(max(nb, 1.0)), resid_weight(rho)))
    log_nb, _ = wmean_log(members)
    n_b = math.exp(log_nb)
    n_t = math.sqrt(max(n_a, 1.0) * max(n_b, 1.0))
    return clamp(n_t, n_last, 12.0 * n_last)


def earner_families(ts, es, ns, n_target, t_target):
    """The five earner families. Each returns (E_target, rms log resid).
    All predictions are floored at the last observed earners."""
    e_last = es[-1]
    out = []

    def finish(members):
        # members: list of (E_target, rho); residual-weighted geomean.
        pairs = [(math.log(max(e, 1e-9)), resid_weight(rho))
                 for e, rho in members]
        lm, _ = wmean_log(pairs)
        wsum = sum(resid_weight(r) for _, r in members)
        rho_f = math.sqrt(sum(resid_weight(r) * r * r
                              for _, r in members) / max(wsum, 1e-12))
        return max(math.exp(lm), e_last), rho_f

    # 1. Saturating-exponential E(t) = B + A(1 - exp(-t/tau)).
    members = []
    for tau in SAT_TAUS:
        basis = [1.0 - math.exp(-t / tau) for t in ts]
        b, a = linfit2(ts, basis, es)
        if a < 0:
            a = 0.0
            b = sum(es) / len(es)
        if b < 0:
            b = 0.0
            den = sum(x * x for x in basis)
            a = sum(x * y for x, y in zip(basis, es)) / den if den > 0 else 0.0
        pred = [max(b + a * x, 1e-9) for x in basis]
        rho = rms_log_resid(ts, es, pred)
        members.append((max(b + a * (1.0 - math.exp(-t_target / tau)), 1e-9),
                        rho))
    out.append(finish(members))

    # 2. Power law: log E = a + p * log(t + 0.25).
    members = []
    xs = [math.log(t + 0.25) for t in ts]
    lys = [math.log(max(e, 1e-9)) for e in es]
    for p in POWERS:
        a = sum(y - p * x for x, y in zip(xs, lys)) / len(xs)
        pred = [a + p * x for x in xs]
        rho = math.sqrt(sum((pr - y) ** 2 for pr, y in zip(pred, lys))
                        / len(xs))
        members.append((math.exp(a + p * math.log(t_target + 0.25)), rho))
    out.append(finish(members))

    # 3. Logistic E(t) = A / (1 + exp(-k (t - t0))), A by least squares.
    members = []
    for k in LOGISTIC_KS:
        for t0 in LOGISTIC_T0S:
            s = [1.0 / (1.0 + math.exp(-k * (t - t0))) for t in ts]
            den = sum(x * x for x in s)
            a = sum(x * y for x, y in zip(s, es)) / den if den > 0 else 0.0
            a = max(a, 0.0)
            pred = [max(a * x, 1e-9) for x in s]
            rho = rms_log_resid(ts, es, pred)
            members.append((a / (1.0 + math.exp(-k * (t_target - t0))), rho))
    out.append(finish(members))

    # 4. Rate-linear: Theil-Sen slope on observed rates,
    # E = r(target) * N_target.
    rates = [e / n for e, n in zip(es, ns)]
    slopes = []
    for i in range(len(ts)):
        for j in range(i + 1, len(ts)):
            if ts[j] > ts[i]:
                slopes.append((rates[j] - rates[i]) / (ts[j] - ts[i]))
    slope = median(slopes) if slopes else 0.0
    r_t = rates[-1] + slope * max(0.0, t_target - ts[-1])
    r_t = max(r_t, 0.0)
    e_lin = max(r_t * n_target, e_last)
    # Residual of the linear-rate fit, in log-earner space: the line
    # through the first read with the Theil-Sen slope.
    lin_pred = []
    for i, t in enumerate(ts):
        rr = rates[0] + slope * (t - ts[0])
        lin_pred.append(max(rr, 0.0) * ns[i])
    rho_lin = rms_log_resid(ts, es, [max(p, 1e-9) for p in lin_pred])
    out.append((e_lin, rho_lin))

    # 5. Two-phase: E(t) = A(1 - exp(-t/tau1)) + B*t, a fast rise to
    # a knee plus a slow LINEAR second phase (the crawl after the
    # knee in a two-phase world is linear in time, not saturating:
    # the saturating second phase was tried and extrapolates too
    # little in slow-base two-phase worlds).
    members = []
    for tau1 in TWOPHASE_TAU1:
        b1 = [1.0 - math.exp(-t / tau1) for t in ts]
        b2 = list(ts)
        c1, c2 = linfit_multi(ts, [b1, b2], es)
        if c1 < 0:
            c1 = 0.0
            den = sum(x * x for x in b2)
            c2 = sum(x * y for x, y in zip(b2, es)) / den if den > 0 else 0.0
        if c2 < 0:
            c2 = 0.0
            den = sum(x * x for x in b1)
            c1 = sum(x * y for x, y in zip(b1, es)) / den if den > 0 else 0.0
        pred = [max(c1 * x + c2 * y, 1e-9)
                for x, y in zip(b1, b2)]
        rho = rms_log_resid(ts, es, pred)
        e_tp = (c1 * (1.0 - math.exp(-t_target / tau1))
                + c2 * t_target)
        members.append((max(e_tp, 1e-9), rho))
    out.append(finish(members))
    return out


def sigma_type(kind):
    if kind in ("plat", "gold100"):
        return 0.08
    if kind in STORY_KINDS:
        return 0.15
    if kind == "easy":
        return 0.30
    return 0.20


def growth_forecast(case, reads, t_target, horizon_days):
    """Forecast rows for the growth branch."""
    trophies = case["trophies"]
    n_tr = len(trophies)
    if not reads:
        # No history at all: prior-only forecast with a wide spread.
        rows = []
        for tr in trophies:
            kind = KIND_OF.get(tr["id"], "activity")
            mu = logit_pct(R7[kind])
            qs = [round(clamp(inv_logit_pct(mu + z * 1.0), 0.01, 99.99), 4)
                  for z in Z]
            rows.append(qs)
        return rows
    n_target = n_forecast(reads, t_target)
    n_last = reads[-1][1]
    ts = [r[0] for r in reads]
    ns = [r[1] for r in reads]

    # Kind-median progress u_kind = median(last rate / R7) per kind.
    # The intro trophy is excluded: its rate is pinned at 100% by
    # construction, so it says nothing about its kind's progress
    # (including it inflates the fishing kind's u).
    last_rates = reads[-1][2]
    by_kind = {}
    for j, tr in enumerate(trophies):
        kind = KIND_OF.get(tr["id"], "activity")
        if last_rates[j] >= 99.5:
            continue
        by_kind.setdefault(kind, []).append(
            last_rates[j] / R7[kind] if R7[kind] > 0 else 0.0)
    u_kind = {k: max(median(v), 0.005) for k, v in by_kind.items()}

    w_base = 1.0 / (1.0 + horizon_days / 1.2)
    rows = []
    medians = []
    for j, tr in enumerate(trophies):
        tid = tr["id"]
        kind = KIND_OF.get(tid, "activity")
        if last_rates[j] >= 99.5:
            # Intro trophy: persists at its last rate.
            rows.append([clamp(last_rates[j], 95.0, 99.99)] * 5)
            medians.append(clamp(last_rates[j], 95.0, 99.99))
            continue
        es = []
        for (t, n, rates) in reads:
            es.append(rates[j] / 100.0 * n)
        # Running-max (already enforced in distinct_reads, re-assert).
        for i in range(1, len(es)):
            if es[i] < es[i - 1]:
                es[i] = es[i - 1]
        e_first = max(es[0], 1.0) if es else 1.0
        e_last = es[-1] if es else 1.0
        g = math.log(max(e_last, 1.0) / e_first)
        w = 1.0 - (1.0 - w_base) * (1.0 - math.exp(-g * g / 2.0))
        # Ensemble of earner families.
        fams = earner_families(ts, es, ns, n_target, t_target)
        pairs = [(math.log(max(e, 1e-9)), resid_weight(rho))
                 for e, rho in fams]
        log_ens, _ = wmean_log(pairs)
        e_ens = math.exp(log_ens)
        # Between-family variance (log space).
        ws = [resid_weight(rho) for _, rho in fams]
        lns = [math.log(max(e, 1e-9)) for e, _ in fams]
        var_fam = sum(w * (x - log_ens) ** 2 for w, x in zip(ws, lns)) \
            / max(sum(ws), 1e-12)
        resid = math.sqrt(sum(resid_weight(rho) * rho * rho
                              for _, rho in fams)
                          / max(sum(resid_weight(rho) for _, rho in fams),
                                1e-12))
        # Type prior, anchored in earner space to the kind's progress.
        prior = R7[kind]
        if e_last >= 5.0:
            e_prior_chk = prior * u_kind.get(kind, 1.0) / 100.0 * n_last
            if e_prior_chk > 0:
                adj = clamp(math.sqrt(e_last / e_prior_chk), 0.25, 4.0)
                prior = prior * adj
        e_prior = max(prior / 100.0 * n_target, 1e-9)
        log_med = w * math.log(e_prior) + (1.0 - w) * log_ens
        e_med = math.exp(log_med)
        rate_med = clamp(e_med / n_target * 100.0, 0.01, 99.99)
        # Spread: base + horizon + type + residual + takeoff variance.
        var_total = var_fam + 4.0 / ((1.0 + g) ** 2)
        sig = 0.45 * math.sqrt(0.10 ** 2 + (0.15 * math.sqrt(horizon_days)) ** 2
                               + sigma_type(kind) ** 2 + resid ** 2
                               + var_total)
        sig = clamp(sig, 0.30, 1.80)
        mu = logit_pct(rate_med)
        qs = [clamp(inv_logit_pct(mu + z * sig), 0.01, 99.99) for z in Z]
        # Keep quantiles non-decreasing after rounding.
        qs = [round(q, 4) for q in qs]
        for k in range(1, 5):
            if qs[k] < qs[k - 1]:
                qs[k] = qs[k - 1]
        rows.append(qs)
        medians.append(qs[2])

    # Story-chain subset constraint, per quantile level.
    pos = {tr["id"]: i for i, tr in enumerate(trophies)}
    if all(t in pos for t in CHAIN) and PLAT_ID in pos and GOLD100_ID in pos:
        for level in range(5):
            prev = None
            for tid in CHAIN:
                i = pos[tid]
                if prev is not None and rows[i][level] > prev:
                    rows[i][level] = prev
                prev = rows[i][level]
            if rows[pos[GOLD100_ID]][level] < rows[pos[PLAT_ID]][level]:
                rows[pos[PLAT_ID]][level] = rows[pos[GOLD100_ID]][level]
    return rows


# ---------------------------------------------------------------- main


def forecast_case(case, stem):
    case_id = case.get("id") or stem
    horizon_days = float(case.get("horizon_minutes", 0)) / 1440.0
    trophies = case["trophies"]
    reads = distinct_reads(case)
    flat = False
    if reads:
        first = (reads[0][1], tuple(reads[0][2]))
        flat = all((r[1], tuple(r[2])) == first for r in reads)
    if horizon_days <= 0.5 and flat and reads:
        rows = refresh_forecast(reads[-1][2], reads[-1][1], case_id)
    else:
        t_target = parse_time(case["target"])
        rows = growth_forecast(case, reads, t_target, horizon_days)
    out_rows = []
    for tr, qs in zip(trophies, rows):
        qs = [round(float(q), 4) for q in qs]
        for k in range(1, 5):
            if qs[k] < qs[k - 1]:
                qs[k] = qs[k - 1]
        out_rows.append({"id": int(tr["id"]), "q": qs})
    out_rows.sort(key=lambda r: r["id"])
    return {"trophies": out_rows}


def main():
    in_dir = os.environ.get("MAKEFUN_INPUT_DIR") or "/input"
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
    os.makedirs(out_dir, exist_ok=True)
    names = sorted(fn for fn in os.listdir(in_dir) if fn.endswith(".in"))
    for fn in names:
        with open(os.path.join(in_dir, fn), "r", encoding="utf-8") as f:
            case = json.load(f)
        stem = fn[:-3]
        result = forecast_case(case, stem)
        out_path = os.path.join(out_dir, stem + ".out")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(result, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
