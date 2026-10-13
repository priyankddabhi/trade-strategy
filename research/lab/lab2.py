"""Test of the NEW strategies from STRATEGY_RULES.md, one at a time, in five 2-year windows (same method as lab.py)."""
from __future__ import annotations
import json, math, sys, warnings
from pathlib import Path
import numpy as np, pandas as pd, yaml
warnings.filterwarnings("ignore")

TS = Path(r"D:\dabhim\imp\RJ\office\us.in treding\trendstrategy")
sys.path.insert(0, str(TS / "research" / "books_lab"))
from src import indicators as ind  # noqa

SP = Path(__file__).parent
MARKETS = {"us": dict(dir="us", index="_GSPC.csv", cap=5000.0, cost=0.05, yaml="evidence_us.yaml", etf="SPY.csv"),
           "india": dict(dir="india", index="_NSEI.csv", cap=400000.0, cost=0.29, yaml="evidence_india.yaml", etf="NIFTYBEES.NS.csv")}
WINDOWS = [("2016-10-10", "2018-10-09"), ("2018-10-10", "2020-10-09"), ("2020-10-10", "2022-10-09"),
           ("2022-10-10", "2024-10-09"), ("2024-10-10", "2026-10-07")]
LAST = "2026-10-07"
BAD = []
STOCK = ["Flipper", "Bollinger", "Longboard", "FearPullback"]


def load_csv(p):
    df = pd.read_csv(p, parse_dates=["Date"], index_col="Date")
    df.columns = [c.lower() for c in df.columns]
    df = df[~df.index.duplicated()].sort_index().dropna(subset=["close"])
    c = df["close"]
    bad = (c / c.rolling(9, center=True, min_periods=5).median() - 1).abs() > 0.5   # a few bars far away from their neighbours = bad print
    if bad.any():
        BAD.append((str(p.name), [str(x.date()) for x in df.index[bad]]))
        df = df[~bad]
    return df[df.index <= LAST]


def find_file(d, s):
    for n in (f"{s}.csv", f"{s}.NS.csv", f"{s.replace('&', '_')}.NS.csv"):
        if (d / n).exists():
            return d / n


def load_market(mk):
    m = MARKETS[mk]; d = TS / "data" / m["dir"]
    syms = yaml.safe_load(open(TS / "config" / m["yaml"], encoding="utf-8"))["symbols"]
    data = {}
    for s in syms:
        f = find_file(d, s)
        if f is not None:
            df = load_csv(f)
            if len(df) > 300:
                data[s] = df
    return data, load_csv(d / m["index"]), load_csv(d / m["etf"])


def new_signals(df):
    n = len(df); c, h = df["close"], df["high"]; cv, hv = c.to_numpy(), h.to_numpy()
    pos = np.arange(n); warm = lambda k: pd.Series(pos >= k - 1, index=df.index)
    atr10, atr40 = ind.atr(df, 10), ind.atr(df, 40)
    sma100 = ind.sma(c, 100); sd100 = c.rolling(100).std(ddof=0)
    sma150 = ind.sma(c, 150); adx7 = ind.adx(df, 7); rsi3 = ind.rsi(c, 3)
    out = {}
    # 1. 20% Flipper: own in/out state machine per share
    sig = np.zeros(n, bool); ex = np.zeros(n, bool); sc = np.zeros(n)
    state, runlow, hh = 0, cv[0], 0.0
    for i in range(n):
        if state == 0:
            runlow = min(runlow, cv[i])
            if cv[i] >= 1.2 * runlow:
                sig[i] = True; sc[i] = cv[i] / runlow; state = 1; hh = 0.0
        else:
            hh = max(hh, hv[i])
            if cv[i] <= 0.8 * hh:
                ex[i] = True; state = 0; runlow = cv[i]
    out["Flipper"] = dict(sig=sig & (pos >= 30), stop=(0.8 * c).to_numpy(), score=sc, exit=ex)
    # 2. Bollinger band breakout
    up = sma100 + 3 * sd100; lo = sma100 - sd100
    out["Bollinger"] = dict(sig=((c > up) & warm(100)).fillna(False).to_numpy(), stop=lo.to_numpy(),
                            score=((c - sma100) / sd100).to_numpy(), exit=(c < lo).fillna(False).to_numpy())
    # 3. Longboard: new all-time (since data start) closing high
    pm = c.shift(1).expanding().max()
    out["Longboard"] = dict(sig=((c > pm) & warm(252)).fillna(False).to_numpy(), stop=(c - 10 * atr40).to_numpy(),
                            score=(c / c.shift(126) - 1).to_numpy(), exit=np.zeros(n, bool), atr40=atr40.to_numpy())
    # 4. Fear pullback
    sg = ((c > sma150) & (adx7 > 45) & (atr10 / c > 0.04) & (rsi3 < 30) & warm(155)).fillna(False)
    out["FearPullback"] = dict(sig=sg.to_numpy(), stop=np.full(n, np.nan), score=(100 - rsi3).to_numpy(),
                               exit=np.zeros(n, bool), limit=(0.96 * c).to_numpy(), atr10=atr10.to_numpy())
    return out


def build(mk):
    data, idx, etf = load_market(mk)
    D = idx.index[idx.index >= "2016-01-01"]; syms = list(data); nD, nS = len(D), len(syms)
    P = {k: np.full((nD, nS), np.nan) for k in "OHLC"}
    S = {s: dict(sig=np.zeros((nD, nS), bool), stop=np.full((nD, nS), np.nan), score=np.zeros((nD, nS)),
                 exit=np.zeros((nD, nS), bool), limit=np.full((nD, nS), np.nan), atr40=np.full((nD, nS), np.nan),
                 atr10=np.full((nD, nS), np.nan)) for s in STOCK}
    for j, s in enumerate(syms):
        df = data[s]; m = D.get_indexer(df.index); ok = m >= 0; src = np.where(ok)[0]; dst = m[ok]
        for k, col in zip("OHLC", ("open", "high", "low", "close")):
            P[k][dst, j] = df[col].to_numpy()[src]
        sg = new_signals(df)
        for name in STOCK:
            for key, arr in sg[name].items():
                S[name][key][dst, j] = arr[src]
    return dict(D=D, syms=syms, P=P, S=S, idx=idx, etf=etf)


def sim2(name, PN, i0, i1, capital, cost_pct, max_pos=5, risk_pct=0.005, max_stock=0.10):
    O, H, L, C = (PN["P"][k] for k in "OHLC"); S = PN["S"][name]
    limit_mode = name == "FearPullback"; cost = cost_pct / 100
    cash = capital; pos = []; trades = []; curve = []

    def mark(t):
        tot = cash
        for p in pos:
            c_ = C[t, p["s"]]
            if not np.isnan(c_): p["last"] = c_
            tot += p["sh"] * p["last"]
        return tot

    def close(p, price):
        nonlocal cash
        out = p["sh"] * price * cost / 2
        cash += p["sh"] * price - out
        trades.append(p["sh"] * (price - p["entry"]) - p["c_in"] - out); pos.remove(p)

    for t in range(i0, i1 + 1):
        for p in list(pos):
            o = O[t, p["s"]]
            if not np.isnan(o) and p["flag"]: close(p, o)
        if t >= 1:
            held = {p["s"] for p in pos}
            cands = [j for j in np.where(S["sig"][t - 1])[0] if j not in held]
            cands.sort(key=lambda j: -S["score"][t - 1, j])
            eq = mark(t - 1)
            for j in cands:
                if len(pos) >= max_pos: break
                o = O[t, j]
                if np.isnan(o): continue
                if limit_mode:
                    lim = S["limit"][t - 1, j]
                    if np.isnan(lim) or L[t, j] > lim: continue
                    fill = min(o, lim); stop = fill - 2.5 * S["atr10"][t - 1, j]
                else:
                    fill = o; stop = S["stop"][t - 1, j]
                if np.isnan(stop) or np.isnan(C[t - 1, j]): continue
                risk = fill - stop
                if risk <= 0: continue
                if not limit_mode:
                    planned = C[t - 1, j] - stop
                    if planned <= 0 or risk > 1.5 * planned: continue
                sh = math.floor(min(risk_pct * eq / risk, max_stock * eq / fill, cash / (fill * (1 + cost / 2))))
                if sh < 1: continue
                c_in = sh * fill * cost / 2; cash -= sh * fill + c_in
                pos.append(dict(s=j, sh=sh, entry=fill, stop=stop, risk=risk, hh=H[t, j], last=fill, t0=t, flag=False, c_in=c_in, be=False))
        for p in list(pos):
            j = p["s"]; o, l_ = O[t, j], L[t, j]
            if np.isnan(o): continue
            if limit_mode and p["t0"] == t: continue          # stop not checked on the entry session
            if o <= p["stop"]: close(p, o)
            elif l_ <= p["stop"]: close(p, p["stop"])
        for p in list(pos):
            j = p["s"]; c_ = C[t, j]
            if np.isnan(c_): continue
            p["last"] = c_; p["hh"] = max(p["hh"], H[t, j])
            if not p["be"] and H[t, j] >= p["entry"] + p["risk"]:
                p["be"] = True; p["stop"] = max(p["stop"], p["entry"])
            if name == "Longboard":
                a = S["atr40"][t, j]
                if not np.isnan(a): p["stop"] = max(p["stop"], p["hh"] - 10 * a)
            if S["exit"][t, j]: p["flag"] = True
            if limit_mode:
                if c_ >= p["entry"] * 1.03: p["flag"] = True
                if t - p["t0"] >= 3: close(p, c_)             # time exit at the close of session 4
        curve.append(mark(t))
    for p in list(pos):
        close(p, p["last"] if np.isnan(C[i1, p["s"]]) else C[i1, p["s"]])
    return metrics(trades, curve, cash, capital)


def metrics(trades, curve, final, capital):
    eq = np.concatenate([[capital], np.array(curve + [final])]); pk = np.maximum.accumulate(eq)
    dd = ((eq - pk) / pk).min(); w = [x for x in trades if x > 0]; l_ = [-x for x in trades if x <= 0]
    pf = (sum(w) / sum(l_)) if l_ and sum(l_) > 0 else (float("inf") if w else float("nan"))
    return dict(n=len(trades), pf=pf, ret=final / capital - 1, dd=dd, pnl=sum(trades), gw=sum(w), gl=sum(l_))


def etf_sim(O, C, state, i0, i1, capital, cost_pct):
    """All-in ETF rule: state[t-1] decides the position held from the open of day t."""
    cost = cost_pct / 100; cash = capital; sh = 0; entry = 0.0; trades = []; curve = []
    for t in range(i0, i1 + 1):
        if t >= 1 and not np.isnan(O[t]):
            want = bool(state[t - 1])
            if want and sh == 0:
                k = math.floor(cash / (O[t] * (1 + cost / 2)))
                if k >= 1: sh = k; entry = O[t]; c_in = k * O[t] * cost / 2; cash -= k * O[t] + c_in
            elif not want and sh > 0:
                out = sh * O[t] * cost / 2; cash += sh * O[t] - out; trades.append(sh * (O[t] - entry) - c_in - out); sh = 0
        curve.append(cash + sh * (C[t] if not np.isnan(C[t]) else entry))
    if sh > 0:
        out = sh * C[i1] * cost / 2; cash += sh * C[i1] - out; trades.append(sh * (C[i1] - entry) - c_in - out); sh = 0
    return metrics(trades, curve, cash, capital)


def etf_multi(Os, Cs, target, i0, i1, capital, cost_pct):
    """Hold one of several ETFs (target[t-1] = index of the asset, -1 = cash)."""
    cost = cost_pct / 100; cash = capital; held = -1; sh = 0; entry = 0.0; trades = []; curve = []
    for t in range(i0, i1 + 1):
        if t >= 1:
            want = int(target[t - 1])
            if want != held:
                if held >= 0 and not np.isnan(Os[held][t]):
                    out = sh * Os[held][t] * cost / 2; cash += sh * Os[held][t] - out; trades.append(sh * (Os[held][t] - entry) - c_in - out); sh = 0; held = -1
                if want >= 0 and held == -1 and not np.isnan(Os[want][t]):
                    k = math.floor(cash / (Os[want][t] * (1 + cost / 2)))
                    if k >= 1: sh = k; held = want; entry = Os[want][t]; c_in = k * entry * cost / 2; cash -= k * entry + c_in
        curve.append(cash + (sh * Cs[held][t] if held >= 0 else 0))
    if held >= 0:
        out = sh * Cs[held][i1] * cost / 2; cash += sh * Cs[held][i1] - out; trades.append(sh * (Cs[held][i1] - entry) - c_in - out)
    return metrics(trades, curve, cash, capital)


def trendpilot_state(c):
    a = (c > ind.sma(c, 200)).to_numpy(); n = len(a); st = np.zeros(n, bool); cur = False; up = dn = 0
    for i in range(n):
        up = up + 1 if a[i] else 0; dn = 0 if a[i] else dn + 1
        if up >= 5: cur = True
        elif dn >= 5: cur = False
        st[i] = cur
    return st


def month_end(D):
    f = np.zeros(len(D), bool); mo = D.month.to_numpy()
    for i in range(len(D) - 1):
        if mo[i + 1] != mo[i]: f[i] = True
    return f


def get_extra():
    out = {}
    for tk in ("EFA", "AGG", "^IRX"):
        f = SP / f"x_{tk.strip('^')}.csv"
        if not f.exists():
            try:
                import yfinance as yf
                d = yf.download(tk, start="2014-06-01", end="2026-10-09", auto_adjust=True, progress=False)
                d.columns = [c[0] if isinstance(c, tuple) else c for c in d.columns]
                d.reset_index().to_csv(f, index=False)
            except Exception as e:
                print("download failed", tk, e); continue
        try:
            df = pd.read_csv(f, parse_dates=["Date"], index_col="Date"); df.columns = [c.lower() for c in df.columns]
            out[tk] = df.dropna(subset=["close"])
        except Exception as e:
            print("read failed", tk, e)
    return out


def run(mk):
    m = MARKETS[mk]; PN = build(mk); D = PN["D"]; res = {}
    wi = []
    for a, b in WINDOWS:
        wi.append((int(D.searchsorted(pd.Timestamp(a))), int(D.searchsorted(pd.Timestamp(b), side="right")) - 1))
    for name in STOCK:
        res[name] = [sim2(name, PN, i0, i1, m["cap"], m["cost"]) for i0, i1 in wi]
        print(mk, name, [f"{r['ret']*100:+.1f}%/{r['n']}" for r in res[name]], flush=True)
    # ETF level rules
    etf = PN["etf"]; idx = PN["idx"]
    al = lambda s: s.reindex(D, method="ffill")
    O = al(etf["open"]).to_numpy(); C = al(etf["close"]).to_numpy()
    ic = idx["close"]
    st75 = al((ic > ind.sma(ic, 75)).astype(float)).fillna(0).to_numpy() > 0
    stTP = al(pd.Series(trendpilot_state(ic).astype(float), index=ic.index)).fillna(0).to_numpy() > 0
    res["ETF75"] = [etf_sim(O, C, st75, i0, i1, m["cap"], m["cost"]) for i0, i1 in wi]
    res["ETF_TrendPilot"] = [etf_sim(O, C, stTP, i0, i1, m["cap"], m["cost"]) for i0, i1 in wi]
    if mk == "us":
        ex = get_extra()
        spy = etf; sc = al(spy["close"]); me = month_end(D)
        tb = None
        if "^IRX" in ex:
            tb = al(ex["^IRX"]["close"]).rolling(252, min_periods=60).mean().to_numpy() / 100.0
        else:
            tb = np.full(len(D), 0.025)
        r12 = (sc / sc.shift(252) - 1).to_numpy()
        state = np.zeros(len(D), bool); cur = False
        for i in range(len(D)):
            if me[i] and not np.isnan(r12[i]) and not np.isnan(tb[i]): cur = r12[i] > tb[i]
            state[i] = cur
        res["ETF_AbsMom"] = [etf_sim(O, C, state, i0, i1, m["cap"], m["cost"]) for i0, i1 in wi]
        if "EFA" in ex and "AGG" in ex:
            Os = [al(spy["open"]).to_numpy(), al(ex["EFA"]["open"]).to_numpy(), al(ex["AGG"]["open"]).to_numpy()]
            Cs = [al(spy["close"]).to_numpy(), al(ex["EFA"]["close"]).to_numpy(), al(ex["AGG"]["close"]).to_numpy()]
            rs = [(pd.Series(Cs[k]) / pd.Series(Cs[k]).shift(252) - 1).to_numpy() for k in range(3)]
            tgt = np.full(len(D), -1); cur = -1
            for i in range(len(D)):
                if me[i] and not np.isnan(rs[0][i]) and not np.isnan(rs[1][i]) and not np.isnan(tb[i]):
                    cur = (0 if rs[0][i] >= rs[1][i] else 1) if rs[0][i] > tb[i] else 2
                tgt[i] = cur
            res["ETF_GEM"] = [etf_multi(Os, Cs, tgt, i0, i1, m["cap"], m["cost"]) for i0, i1 in wi]
        res["_tbill"] = "^IRX 252-session average" if "^IRX" in ex else "fixed 2.5%"
    for k in [x for x in res if x.startswith("ETF")]:
        print(mk, k, [f"{r['ret']*100:+.1f}%/{r['n']}" for r in res[k]], flush=True)
    bh = []
    for a, b in WINDOWS:
        s = ic[(ic.index >= a) & (ic.index <= b)]; bh.append(s.iloc[-1] / s.iloc[0] - 1)
    return dict(res=res, bh=bh, nsym=len(PN["syms"]))


if __name__ == "__main__":
    out = {mk: run(mk) for mk in ("us", "india")}
    json.dump(out, open(SP / "results2.json", "w"), default=float)
    print("glitch bars removed:", BAD)
