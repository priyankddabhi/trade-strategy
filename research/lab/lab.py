"""Standalone test of every book strategy, one at a time, in five 2-year windows.

Vectorised re-implementation of books_lab/src/book_strategies.py (parity-checked
against the original functions), plus an event simulator with the project risk rules.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

BASE = Path(r"D:\dabhim\imp\RJ\office\us.in treding\semi final bot")
APP = BASE / "app"
sys.path.insert(0, str(BASE / "books_lab"))
from src import indicators as ind  # noqa: E402

CFG = yaml.safe_load(open(BASE / "books_lab/config/settings.yaml", encoding="utf-8-sig"))
B = CFG["strategy"]["books"]

STRATS = ["SEPA", "SuperPerf", "TurtleS1", "TurtleS2", "Whipsaw", "Donchian",
          "AbsoluteMom", "RelativeMom", "GEM", "DualMomentum",
          "NewYearlyHigh", "HundredDayHigh", "GoldenCross", "TrendPilot",
          "ClenowTF", "Momentum", "WeeklyRot", "MeanRevLong", "MeanRevShort",
          "Trend200", "MeanReversion", "NiftyMom30"]

MARKETS = {
    "us": dict(dir="us", index="_GSPC.csv", cap=5000.0, cost=0.05, cur="$",
               yaml="evidence_us.yaml"),
    "india": dict(dir="india", index="_NSEI.csv", cap=400000.0, cost=0.29, cur="Rs",
                  yaml="evidence_india.yaml"),
}
WINDOWS = [("2016-10-10", "2018-10-09"), ("2018-10-10", "2020-10-09"),
           ("2020-10-10", "2022-10-09"), ("2022-10-10", "2024-10-09"),
           ("2024-10-10", "2026-10-07")]
LAST_DAY = "2026-10-07"


# ---------------------------------------------------------------- data
def load_csv(p: Path) -> pd.DataFrame:
    df = pd.read_csv(p, parse_dates=["Date"], index_col="Date")
    df.columns = [c.lower() for c in df.columns]
    df = df[~df.index.duplicated()].sort_index()
    df = df.dropna(subset=["close"])
    return df[df.index <= LAST_DAY]


def find_file(d: Path, sym: str):
    for n in (f"{sym}.csv", f"{sym}.NS.csv", f"{sym.replace('&', '_')}.NS.csv"):
        if (d / n).exists():
            return d / n
    return None


def load_market(mk: str):
    m = MARKETS[mk]
    d = APP / "data" / m["dir"]
    syms = yaml.safe_load(open(APP / "config" / m["yaml"], encoding="utf-8"))["symbols"]
    data = {}
    for s in syms:
        f = find_file(d, s)
        if f is None:
            continue
        df = load_csv(f)
        if len(df) > 300:
            data[s] = df
    idx = load_csv(d / m["index"])
    return data, idx


def index_info(idx: pd.DataFrame) -> dict:
    c = idx["close"]
    ma = ind.sma(c, 200)
    above = (c > ma)
    return dict(
        close=c, ma200=ma, above=above,
        above5=(above.astype(float).rolling(5).min() == 1.0),
        ret126=c / c.shift(126) - 1.0, ret252=c / c.shift(252) - 1.0,
    )


# ---------------------------------------------------------------- signals
def pmax(s, n):
    return s.shift(1).rolling(n).max()


def pmin(s, n):
    return s.shift(1).rolling(n).min()


def rolling_adj_slope(c: pd.Series, w: int = 90) -> pd.Series:
    y = np.log(c.astype(float))
    x = np.arange(w, dtype=float)
    xs = x - x.mean()
    sxx = (xs ** 2).sum()
    yv = y.to_numpy()
    n = len(yv)
    sxy = np.full(n, np.nan)
    if n >= w:
        sxy[w - 1:] = np.correlate(yv, xs, mode="valid")
    sy = y.rolling(w).sum().to_numpy()
    syy = (y ** 2).rolling(w).sum().to_numpy()
    slope = sxy / sxx
    sse = (syy - sy ** 2 / w) - slope * sxy
    se = np.sqrt(np.maximum(sse, 0.0) / (w - 2))
    with np.errstate(divide="ignore", invalid="ignore"):
        adj = slope / se
    return pd.Series(adj, index=c.index)


def make_signals(df: pd.DataFrame, I: dict) -> dict:
    """Return {strategy: dict(sig, stop, tgt, score, exit, ...)} on the share's own dates."""
    n = len(df)
    pos = np.arange(n)
    ix = df.index
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    al = lambda s: s.reindex(ix, method="ffill")  # noqa: E731
    I_close, I_ma200 = al(I["close"]), al(I["ma200"])
    above = al(I["above"]).fillna(False).astype(bool)
    above5 = al(I["above5"]).fillna(False).astype(bool)
    ir126, ir252 = al(I["ret126"]), al(I["ret252"])

    atr10, atr14, atr20 = ind.atr(df, 10), ind.atr(df, 14), ind.atr(df, 20)
    s50, s100, s150, s200, s300 = (ind.sma(c, k) for k in (50, 100, 150, 200, 300))
    warm = lambda k: pd.Series(pos >= k - 1, index=ix)  # noqa: E731  (len >= k)
    own126 = c / c.shift(126) - 1.0
    own252 = c / c.shift(252) - 1.0
    rsi2, rsi3 = ind.rsi(c, 2), ind.rsi(c, 3)
    adx7 = ind.adx(df, 7)
    out: dict = {}

    def put(name, sig, stop, tgt, score, exit_=None, **kw):
        sig = (sig.fillna(False).astype(bool)) if hasattr(sig, "fillna") else sig
        out[name] = dict(sig=sig.to_numpy(), stop=np.asarray(stop, float), tgt=np.asarray(tgt, float),
                         score=np.asarray(score, float),
                         exit=None if exit_ is None else exit_.fillna(False).astype(bool).to_numpy(), **kw)

    # 1 SEPA
    f = s50
    stack = (c > f) & (f > s150) & (s150 > s200)
    rising = f > f.shift(19)
    rs = ((c / c.shift(126) - 1.0) * 100.0) > 0
    rec = atr14.rolling(10).mean()
    pri = atr14.shift(10).rolling(10).mean()
    contr = ((pri - rec) / pri).where(pri != 0, 0.0)
    brk = c >= pmax(h, 20) * (1 - 0.005)
    vx = (v / ind.sma(v, 50)) >= 1.5
    sig = stack & rising & rs & (contr >= 0.05) & brk & vx & warm(200)
    put("SEPA", sig, c * 0.93, c * 1.14, (0.35 + 0.15 + 0.2 + 0.15 + 0.15 * (contr / 0.05) * 0.5).clip(upper=1))

    # 2 SuperPerf
    stack2 = (c > s50) & (s50 > s150) & (s150 > s200)
    rel = own126 - ir126
    sig = stack2 & (rel > 0.0) & (c > s200) & warm(200)
    put("SuperPerf", sig, c - 3 * atr14, c + 6 * atr14, (0.6 + rel).clip(upper=1), exit_=~sig)

    # 3-6 Turtle family
    trend = s50 > s300
    gate = trend & above & warm(305)
    b20, b55 = c > pmax(h, 20), c > pmax(h, 55)
    put("TurtleS1", gate & b20, c - 2 * atr20, c + 4 * atr20, pd.Series(0.8, index=ix), exit_=c < pmin(l, 10))
    put("TurtleS2", gate & b55, c - 2 * atr20, c + 4 * atr20, pd.Series(0.9, index=ix), exit_=c < pmin(l, 20))
    put("Whipsaw", gate & b20, c - 0.5 * atr20, c + 1.0 * atr20, pd.Series(0.75, index=ix), exit_=c < pmin(l, 10))
    put("Donchian", gate & (b20 | b55), c - 2 * atr20, c + 4 * atr20,
        pd.Series(np.where(b20, 0.85, 0.95), index=ix), exit_=c < pmin(l, 10))

    # 7-10 Antonacci
    exc = own252 * 100.0 - 6.2
    sigA = (exc > 0) & warm(254)
    put("AbsoluteMom", sigA, c - 3 * atr14, c + 6 * atr14, (0.5 + exc / 40.0).clip(upper=1), exit_=~sigA)
    sigR = (own252 > ir252) & warm(254)
    put("RelativeMom", sigR, c - 3 * atr14, c + 6 * atr14, (0.5 + (own252 - ir252) * 2.0).clip(upper=1), exit_=~sigR)
    sigG = (ir252 > 0.062) & (own252 > ir252) & warm(254)
    put("GEM", sigG, c - 3 * atr14, c + 6 * atr14, (0.7 + (own252 - ir252)).clip(upper=1), exit_=~sigG)
    put("DualMomentum", sigA, c - 3 * atr14, c + 6 * atr14, (0.6 + 0.25 * (own252 > ir252) + 0.15 * (exc > 15)).clip(upper=1),
        exit_=~sigA)

    # 11-14 Radge
    lo250, lo100 = pmin(l, 250), pmin(l, 100)
    sig = (c > pmax(h, 250)) & warm(252)
    stop = np.minimum(lo250, c - 3 * atr14)
    put("NewYearlyHigh", sig, stop, c + (c - stop) * 2, pd.Series(0.9, index=ix), exit_=c < lo250)
    fast, slow = s50, s200
    conf = (fast > slow).astype(float).rolling(5).min() == 1.0
    sig = (fast > slow) & conf & warm(205)
    stop = np.minimum(slow, c - 3 * atr14)
    put("GoldenCross", sig, stop, c + (c - stop) * 2, pd.Series(0.8, index=ix), exit_=fast < slow)
    href = h.rolling(60).max()
    stop = np.minimum(np.minimum(lo100, href * 0.9), c - 3 * atr14)
    sig = above5 & (c > pmax(h, 100)) & warm(105)
    put("TrendPilot", sig, stop, c + (c - stop) * 2, pd.Series(0.9, index=ix), exit_=(c < lo100) | ~above)
    stop = np.minimum(lo100, c - 3 * atr14)
    sig = (c > pmax(h, 100)) & warm(102)
    put("HundredDayHigh", sig, stop, c + (c - stop) * 2, pd.Series(0.85, index=ix), exit_=c < lo100)

    # 15-16 Clenow
    sig = (s50 > s100) & above & (c > pmax(h, 50)) & warm(105)
    put("ClenowTF", sig, c - 3 * atr20, c + 6 * atr20, pd.Series(0.85, index=ix), trail_k=3.0, trail_atr=atr20.to_numpy())
    adj = rolling_adj_slope(c, 90)
    sig = above & (c > s100) & warm(100) & adj.notna()
    put("Momentum", sig, c - 3 * atr20, c + 6 * atr20, (0.5 + adj.abs() / 10.0).clip(upper=1), exit_=c < s100)

    # 17 Weekly rotation (eligibility; ranking by 200-day ROC done by the simulator)
    band_ok = I_close > I_ma200 * (1 - 0.02)
    roc200 = c / c.shift(200) - 1.0
    sig = band_ok & (rsi3 < 50) & (roc200 > 0) & warm(400)
    put("WeeklyRot", sig, c - 3 * atr14, c + 6 * atr14, (roc200 + 0.4).clip(upper=1), key=roc200.to_numpy())

    # 18, 21 mean reversion long
    up200 = (c > s200) & warm(205)
    sig = up200 & (rsi2 <= 10)
    sc = ((10 - rsi2) / 10 + 0.5).clip(upper=1).clip(lower=0.5)
    put("MeanRevLong", sig, c - 2.5 * atr10, c * 1.03, sc, max_hold=4)
    sig2 = sig & (adx7 >= 50)
    put("MeanReversion", sig2, c - 2.5 * atr10, c * 1.04, sc, max_hold=2)

    # 19 mean reversion SHORT (banned by the project rules - information only)
    up2 = (c > c.shift(1)) & (c.shift(1) > c.shift(2))
    sig = (adx7 > 50) & ((atr10 / c * 100.0) > 5.0) & up2 & (rsi3 > 85) & warm(25)
    put("MeanRevShort", sig, c + 2.5 * atr10, c * 0.96, pd.Series(0.8, index=ix), side=-1)

    # 20 Trend200
    sig = (c > s200) & warm(200)
    stop = np.minimum(s200, c - 3 * atr14)
    put("Trend200", sig, stop, c + (c - stop) * 2, pd.Series(0.6, index=ix), exit_=c <= s200)

    # NiftyMom30 inputs (ranking done at rebalance dates)
    mrs = []
    for lb in (126, 252):
        ret = (c / c.shift(lb) - 1.0) * 100.0
        sd = c.pct_change().rolling(lb).std() * math.sqrt(252) * 100.0
        mrs.append((ret / sd).where(sd > 0, 0.0).where(pos >= lb + 1))
    nms = pd.concat(mrs, axis=1).mean(axis=1, skipna=True)
    out["_nms"] = nms.to_numpy()
    out["_atr14"] = atr14.to_numpy()
    return out


# ---------------------------------------------------------------- panels
def build_panels(mk: str):
    data, idx = load_market(mk)
    I = index_info(idx)
    D = idx.index[idx.index >= "2016-01-01"]
    syms = list(data)
    nD, nS = len(D), len(syms)
    P = {k: np.full((nD, nS), np.nan) for k in ("O", "H", "L", "C")}
    perstrat = {s: dict(sig=np.zeros((nD, nS), bool), stop=np.full((nD, nS), np.nan),
                        tgt=np.full((nD, nS), np.nan), score=np.zeros((nD, nS)),
                        exit=np.zeros((nD, nS), bool)) for s in STRATS}
    keys = {s: np.full((nD, nS), np.nan) for s in ("WeeklyRot",)}
    nms = np.full((nD, nS), np.nan)
    atr14 = np.full((nD, nS), np.nan)
    trail_atr = np.full((nD, nS), np.nan)
    extras = {}
    for j, s in enumerate(syms):
        df = data[s]
        m = D.get_indexer(df.index)
        ok = m >= 0
        src = np.where(ok)[0]
        dst = m[ok]
        for k, col in (("O", "open"), ("H", "high"), ("L", "low"), ("C", "close")):
            P[k][dst, j] = df[col].to_numpy()[src]
        sg = make_signals(df, I)
        nms[dst, j] = sg["_nms"][src]
        atr14[dst, j] = sg["_atr14"][src]
        for name in STRATS:
            if name == "NiftyMom30":
                continue
            r = sg[name]
            ps = perstrat[name]
            ps["sig"][dst, j] = r["sig"][src]
            ps["stop"][dst, j] = r["stop"][src]
            ps["tgt"][dst, j] = r["tgt"][src]
            ps["score"][dst, j] = r["score"][src]
            if r.get("exit") is not None:
                ps["exit"][dst, j] = r["exit"][src]
            if "key" in r:
                keys[name][dst, j] = r["key"][src]
            if "trail_atr" in r:
                trail_atr[dst, j] = r["trail_atr"][src]
            for kk in ("max_hold", "trail_k", "side"):
                if kk in r:
                    extras[(name, kk)] = r[kk]
    return dict(D=D, syms=syms, P=P, S=perstrat, keys=keys, nms=nms, atr14=atr14,
                trail_atr=trail_atr, extras=extras, I=I, idx=idx)


def rebalance_flags(D, mode):
    nD = len(D)
    flag = np.zeros(nD, bool)
    if mode == "weekly":
        wk = D.isocalendar()
        key = (wk.year.astype(int) * 100 + wk.week.astype(int)).to_numpy()
        flag[:-1] = key[1:] != key[:-1]
    else:  # semi-annual: last trading day of March and September
        mo = D.month.to_numpy()
        for i in range(nD - 1):
            if mo[i] in (3, 9) and mo[i + 1] != mo[i]:
                flag[i] = True
    return flag


def nifty_targets(PN, nms_row, topn=30):
    ok = ~np.isnan(nms_row)
    if ok.sum() < 5:
        return []
    vals = nms_row[ok]
    sd = vals.std()
    if sd <= 0:
        return []
    z = (nms_row - vals.mean()) / sd
    z[~ok] = -np.inf
    order = np.argsort(-z)
    return [int(j) for j in order[:topn] if np.isfinite(z[j])]


# ---------------------------------------------------------------- simulator
def simulate(name, PN, i0, i1, capital, cost_pct, max_pos=5, risk_pct=0.005, max_stock=0.10):
    P, D = PN["P"], PN["D"]
    O, H, L, C = P["O"], P["H"], P["L"], P["C"]
    ps = PN["S"][name]
    ex = PN["extras"]
    side = ex.get((name, "side"), 1)
    max_hold = ex.get((name, "max_hold"))
    trail_k = ex.get((name, "trail_k"))
    rot = None
    if name == "WeeklyRot":
        rot = dict(reb=PN["reb_weekly"], topn=10)
    elif name == "NiftyMom30":
        rot = dict(reb=PN["reb_semi"], topn=30)
    cost = cost_pct / 100.0
    cash = capital
    pos = []  # dicts
    trades = []
    eq_curve = []
    nS = O.shape[1]

    def mark(t):
        tot = cash
        for p in pos:
            c_ = C[t, p["s"]]
            if not np.isnan(c_):
                p["last"] = c_
            tot += p["sh"] * (p["last"] if side == 1 else 2 * p["entry"] - p["last"])
        return tot

    def close_pos(p, price):
        nonlocal cash
        gross = p["sh"] * (price - p["entry"]) * side
        c_ = p["sh"] * price * cost / 2
        cash += p["sh"] * p["entry"] + gross - c_
        trades.append(gross - p["c_in"] - c_)
        pos.remove(p)

    for t in range(i0, i1 + 1):
        flagged_rot = None
        if rot is not None and t >= 1 and rot["reb"][t - 1]:
            if name == "WeeklyRot":
                el = ps["sig"][t - 1]
                key = np.where(el, PN["keys"]["WeeklyRot"][t - 1], -np.inf)
                order = np.argsort(-key)
                flagged_rot = [int(j) for j in order[:rot["topn"]] if np.isfinite(key[j])]
            else:
                flagged_rot = nifty_targets(PN, PN["nms"][t - 1], rot["topn"])
        # 1. exits at the open
        for p in list(pos):
            o = O[t, p["s"]]
            if np.isnan(o):
                continue
            if p["flag"] or (flagged_rot is not None and p["s"] not in flagged_rot):
                close_pos(p, o)
        # 2. entries at the open
        if t >= 1:
            held = {p["s"] for p in pos}
            if rot is not None:
                cands = [j for j in (flagged_rot or []) if j not in held]
            else:
                m = ps["sig"][t - 1]
                cands = [j for j in np.where(m)[0] if j not in held]
                cands.sort(key=lambda j: -ps["score"][t - 1, j])
            if cands and len(pos) < max_pos:
                eq = mark(t - 1)
                for j in cands:
                    if len(pos) >= max_pos:
                        break
                    o = O[t, j]
                    stop = ps["stop"][t - 1, j] if rot is None or name == "WeeklyRot" else 2 * 0  # placeholder
                    if rot is not None and name == "NiftyMom30":
                        a = PN["atr14"][t - 1, j]
                        stop = C[t - 1, j] - 2 * a * side if not np.isnan(a) else np.nan
                    if np.isnan(o) or np.isnan(stop) or np.isnan(C[t - 1, j]):
                        continue
                    risk = (o - stop) * side
                    if risk <= 0:
                        continue
                    planned = (C[t - 1, j] - stop) * side
                    if planned <= 0 or risk > 1.5 * planned:
                        continue
                    sh = math.floor(min(risk_pct * eq / risk, max_stock * eq / o,
                                        cash / (o * (1 + cost / 2))))
                    if sh < 1:
                        continue
                    c_in = sh * o * cost / 2
                    cash -= sh * o + c_in
                    tgt = ps["tgt"][t - 1, j]
                    if rot is not None:
                        tgt = np.nan
                    pos.append(dict(s=j, sh=sh, entry=o, stop=stop, tgt=tgt, risk=risk, hc=o, last=o,
                                    t0=t, flag=False, c_in=c_in, be=False))
        # 3. intraday stops / targets
        for p in list(pos):
            j = p["s"]
            o, h_, l_ = O[t, j], H[t, j], L[t, j]
            if np.isnan(o):
                continue
            if side == 1:
                if o <= p["stop"]:
                    close_pos(p, o)
                elif l_ <= p["stop"]:
                    close_pos(p, p["stop"])
                elif not np.isnan(p["tgt"]) and o >= p["tgt"]:
                    close_pos(p, o)
                elif not np.isnan(p["tgt"]) and h_ >= p["tgt"]:
                    close_pos(p, p["tgt"])
            else:
                if o >= p["stop"]:
                    close_pos(p, o)
                elif h_ >= p["stop"]:
                    close_pos(p, p["stop"])
                elif not np.isnan(p["tgt"]) and o <= p["tgt"]:
                    close_pos(p, o)
                elif not np.isnan(p["tgt"]) and l_ <= p["tgt"]:
                    close_pos(p, p["tgt"])
        # 4. end of day bookkeeping
        for p in pos:
            j = p["s"]
            c_ = C[t, j]
            if np.isnan(c_):
                continue
            p["last"] = c_
            if side == 1:
                p["hc"] = max(p["hc"], c_)
                if not p["be"] and H[t, j] >= p["entry"] + p["risk"]:
                    p["be"] = True
                    p["stop"] = max(p["stop"], p["entry"])
                if trail_k is not None and p["be"]:
                    a = PN["trail_atr"][t, j]
                    if not np.isnan(a):
                        p["stop"] = max(p["stop"], p["hc"] - trail_k * a)
            else:
                if not p["be"] and L[t, j] <= p["entry"] - p["risk"]:
                    p["be"] = True
                    p["stop"] = min(p["stop"], p["entry"])
            if ps["exit"][t, j]:
                p["flag"] = True
            if max_hold is not None and t - p["t0"] >= max_hold:
                p["flag"] = True
        eq_curve.append(mark(t))
    for p in list(pos):
        close_pos(p, p["last"] if np.isnan(C[i1, p["s"]]) else C[i1, p["s"]])
    final = cash
    eq = np.array(eq_curve + [final]) if eq_curve else np.array([capital])
    peak = np.maximum.accumulate(np.concatenate([[capital], eq]))
    dd = ((np.concatenate([[capital], eq]) - peak) / peak).min()
    wins = [x for x in trades if x > 0]
    loss = [-x for x in trades if x <= 0]
    pf = (sum(wins) / sum(loss)) if loss and sum(loss) > 0 else (float("inf") if wins else float("nan"))
    return dict(n=len(trades), win=(len(wins) / len(trades) if trades else float("nan")),
                pf=pf, ret=final / capital - 1.0, dd=dd, end=final, pnl=sum(trades),
                gw=sum(wins), gl=sum(loss))


def run_market(mk, PN=None):
    m = MARKETS[mk]
    PN = PN or build_panels(mk)
    D = PN["D"]
    PN["reb_weekly"] = rebalance_flags(D, "weekly")
    PN["reb_semi"] = rebalance_flags(D, "semi")
    wins_idx = []
    for a, b in WINDOWS:
        i0 = int(D.searchsorted(pd.Timestamp(a)))
        i1 = int(D.searchsorted(pd.Timestamp(b), side="right")) - 1
        wins_idx.append((i0, i1))
    res = {}
    for name in STRATS:
        res[name] = [simulate(name, PN, i0, i1, m["cap"], m["cost"]) for i0, i1 in wins_idx]
        print(mk, name, [f"{r['ret']*100:+.1f}%/{r['n']}" for r in res[name]], flush=True)
    bh = []
    idx = PN["idx"]["close"]
    for a, b in WINDOWS:
        s = idx[(idx.index >= a) & (idx.index <= b)]
        bh.append(s.iloc[-1] / s.iloc[0] - 1.0)
    return res, bh, len(PN["syms"])


if __name__ == "__main__":
    out = {}
    for mk in sys.argv[1:] or ["us", "india"]:
        res, bh, nsym = run_market(mk)
        out[mk] = dict(res=res, bh=bh, nsym=nsym)
    json.dump(out, open(Path(__file__).parent / "results.json", "w"), default=float)
