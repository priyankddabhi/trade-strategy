"""10-year study of the market-level hierarchy.

  python run.py study us        (or india)

Real daily prices for the last 10 years:
  - Years 1-5 are TRAINING: the few hierarchy settings are chosen here only.
  - Years 6-10 are the TEST: run once, never used to choose anything.
  - Then the same 10 years in 1-year and 2-year bundles, each a fresh account.

Two ways of trading are compared with the same strategy, costs and risk rules:
  FLAT       same risk on every trade (the old way)
  HIERARCHY  good market = full risk, normal = reduced, bad = no buys; best-ranked buy of the day gets full size
Runs on the evidence list (about 100 shares) and on the live trade list (ABBV + AMZN for the US).
Nothing here changes the live settings.
"""
from __future__ import annotations

import copy
import itertools
import json
import logging
import math
from datetime import datetime

import pandas as pd
import yaml

from . import clock, config, report, stages
from . import walkforward as wf
from .config import CONFIG_DIR, load_events, load_market
from .engine import prepare_market_data
from .strategy import make_strategy

FLAT = {("regime", "levels"): False, ("risk", "second_choice_mult"): 1.0}   # plus bad_market off, set per config
GRID = {"normal_risk_mult": [0.25, 0.5, 0.75], "second_choice_mult": [0.5, 1.0]}
CALM = {"us": [16, 20, 25], "india": [13, 16, 20]}
MIN_TRADES = report.PASS_MIN_TRADES


def evidence_cfg(market: str) -> dict:
    cfg = load_market(market)
    ev = yaml.safe_load((CONFIG_DIR / f"evidence_{market}.yaml").read_text(encoding="utf-8"))
    cfg["universe"]["symbols"], cfg["universe"]["etfs"] = ev["symbols"], ev.get("etfs") or []
    cfg["capital"], cfg["risk"]["max_positions"] = ev["capital"], ev["max_positions"]
    return cfg


def _f(s, k):
    return wf._f(s, k)


def row(name: str, s: dict | None) -> dict:
    ret, dd = _f(s, "total_return"), _f(s, "max_drawdown")
    return {"name": name, "trades": (s or {}).get("trades", 0), "win": _f(s, "win_rate"), "pf": _f(s, "profit_factor"),
            "ret": ret, "dd": dd, "ret_dd": ret / abs(dd) if dd and not math.isnan(dd) and dd < 0 else float("nan"),
            "halts": (s or {}).get("halts", 0)}


def score(r: dict) -> float:
    """Return per unit of drawdown, only when the result is real enough to compare."""
    if r["trades"] < MIN_TRADES or math.isnan(r["pf"]) or r["pf"] < 1.0 or math.isnan(r["ret_dd"]):
        return float("-inf")
    return r["ret_dd"]


def windows(start: pd.Timestamp, end: pd.Timestamp, years: int) -> list[tuple[str, str]]:
    out, a = [], start
    while a < end:
        b = min(a + pd.DateOffset(years=years) - pd.Timedelta(days=1), end)
        out.append((str(a.date()), str(b.date())))
        a = b + pd.Timedelta(days=1)
    return out


def _fmt(r: dict) -> str:
    p = lambda x: "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.1f}%"
    n = lambda x: "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.2f}"
    return f"{r['name']:<34}{r['trades']:>7}{p(r['win']):>8}{n(r['pf']):>7}{p(r['ret']):>9}{p(r['dd']):>9}{n(r['ret_dd']):>8}"


HEAD = f"{'':<34}{'Trades':>7}{'Win':>8}{'PF':>7}{'Return':>9}{'MaxDD':>9}{'Ret/DD':>8}"


def study_list(label: str, cfg: dict, frames: dict, events: set, start, split, end, tune: bool) -> dict:
    train_end = str((split - pd.Timedelta(days=1)).date())
    s0, s1, e = str(start.date()), str(split.date()), str(end.date())
    res = {"label": label, "train": [], "test": [], "bundles1": [], "bundles2": [], "grid": [], "chosen": {}}
    flat = {**FLAT, ("regime", "bad_market"): dict(cfg["regime"].get("bad_market") or {}, allow=False)}
    nobad = {("regime", "bad_market"): dict(cfg["regime"].get("bad_market") or {}, allow=False)}
    flat_tr = row("Flat (old way)", wf.run_once(cfg, frames, events, s0, train_end, flat))
    nobad_tr = row("Levels, no buys in a bad market", wf.run_once(cfg, frames, events, s0, train_end, nobad))
    hier_tr = row("Hierarchy + extra-safe bad market", wf.run_once(cfg, frames, events, s0, train_end))
    res["train"] = [flat_tr, nobad_tr, hier_tr]
    chosen = {}
    if tune:                                     # choose settings on the TRAINING years only
        best, best_score = None, score(hier_tr)
        market = cfg["name"]
        for nr, sc, cv in itertools.product(GRID["normal_risk_mult"], GRID["second_choice_mult"], CALM[market]):
            ov = {("regime", "normal_risk_mult"): nr, ("risk", "second_choice_mult"): sc, ("regime", "calm_vix"): cv}
            r = row(f"normal {nr:g}x, 2nd {sc:g}x, calm {cv}", wf.run_once(cfg, frames, events, s0, train_end, ov))
            res["grid"].append(r)
            if score(r) > best_score + 0.05:     # must beat the default clearly, else keep the default
                best, best_score = ov, score(r)
        chosen = best or {}
        res["chosen"] = {k[1]: v for k, v in chosen.items()}
    test = [row("Flat (old way)", wf.run_once(cfg, frames, events, s1, e, flat)),
            row("Levels, no buys in a bad market", wf.run_once(cfg, frames, events, s1, e, nobad)),
            row("Hierarchy + extra-safe bad market", wf.run_once(cfg, frames, events, s1, e))]
    if chosen:
        res["train"].append(row("Hierarchy (tuned on training)", wf.run_once(cfg, frames, events, s0, train_end, chosen)))
        test.append(row("Hierarchy (tuned on training)", wf.run_once(cfg, frames, events, s1, e, chosen)))
    res["test"] = test
    final = chosen or None
    for key, yrs in (("bundles1", 1), ("bundles2", 2)):
        for a, b in windows(start, end, yrs):
            f, h = wf.run_once(cfg, frames, events, a, b, flat), wf.run_once(cfg, frames, events, a, b, final)
            res[key].append({"window": f"{a[:7]} to {b[:7]}", "flat": row("flat", f), "hier": row("hier", h)})
    return res


def run(market: str, offline: bool = False) -> dict:
    logging.getLogger("tbot.engine").setLevel(logging.ERROR)
    events = load_events()[market]
    ev_cfg = evidence_cfg(market)
    end_guess = pd.Timestamp.today().normalize()
    start_guess = end_guess - pd.DateOffset(years=10)
    frames = wf.load_frames(ev_cfg, start_guess, offline)
    end = max(df.index[-1] for df in frames.values())
    start = end - pd.DateOffset(years=10) + pd.Timedelta(days=1)
    split = start + pd.DateOffset(years=5)
    print(f"10-year study {market.upper()}: {start.date()} to {end.date()} | training {start.date()} to "
          f"{(split - pd.Timedelta(days=1)).date()} | test {split.date()} to {end.date()}")
    lists = [("Evidence list (%d shares)" % len(ev_cfg["universe"]["symbols"]), ev_cfg, frames, True)]
    live_cfg = load_market(market)
    if len(live_cfg["universe"]["symbols"]) <= 10:
        live_frames = wf.load_frames(live_cfg, start_guess, offline)
        lists.append((f"Trade list ({', '.join(live_cfg['universe']['symbols'])})", live_cfg, live_frames, False))
    results = [study_list(lbl, cfg, fr, events, start, split, end, tune) for lbl, cfg, fr, tune in lists]

    lines = [f"10-YEAR STUDY {market.upper()}  (real daily prices, practice money, real costs)",
             f"Training {start.date()} to {(split - pd.Timedelta(days=1)).date()}   Test {split.date()} to {end.date()}", ""]
    for r in results:
        lines += [f"=== {r['label']}", "TRAINING (first 5 years)", HEAD] + [_fmt(x) for x in r["train"]]
        if r["chosen"]:
            lines.append("Chosen on training: " + ", ".join(f"{k}={v}" for k, v in r["chosen"].items()))
        elif r["grid"]:
            lines.append("Chosen on training: default settings (no variant beat them clearly)")
        lines += ["TEST (last 5 years, run once)", HEAD] + [_fmt(x) for x in r["test"]]
        for key, title in (("bundles2", "2-YEAR BUNDLES"), ("bundles1", "1-YEAR BUNDLES")):
            lines += [f"{title} (fresh account each, hierarchy vs flat)",
                      f"{'Window':<22}{'Flat ret':>10}{'Flat DD':>9}{'Hier ret':>10}{'Hier DD':>9}{'Better':>9}"]
            wins = 0
            for b in r[key]:
                f, h = b["flat"], b["hier"]
                better = score_cmp(f, h)
                wins += better == "hier"
                lines.append(f"{b['window']:<22}{_p(f['ret']):>10}{_p(f['dd']):>9}{_p(h['ret']):>10}{_p(h['dd']):>9}{better:>9}")
            lines.append(f"Hierarchy better in {wins} of {len(r[key])} bundles")
        lines.append("")
    text = "\n".join(lines)
    print("\n" + text)
    out = config.RUNS_DIR / "study" / f"{market}_{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.txt").write_text(text, encoding="utf-8")
    (out / "results.json").write_text(json.dumps({"market": market, "start": str(start.date()), "split": str(split.date()),
                                                  "end": str(end.date()), "lists": results}, default=lambda x: None, indent=1)
                                      .replace("NaN", "null").replace("Infinity", "null"), encoding="utf-8")
    (out / "report.html").write_text(report.page(f"10-year study {market.upper()}", "<pre>" + text + "</pre>"), encoding="utf-8")
    print(f"\nSaved: {out}")
    return {"market": market, "lists": results}


def _p(x) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.1f}%"


def score_cmp(f: dict, h: dict) -> str:
    """Which did better in a bundle: return minus the worst drop (both count equally).
    Works for gains and for losses: a smaller loss with a smaller drop wins."""
    def val(r):
        dd = r["dd"] if r["dd"] is not None and not math.isnan(r["dd"]) else 0.0
        ret = r["ret"] if r["ret"] is not None and not math.isnan(r["ret"]) else 0.0
        return ret + dd
    a, b = val(f), val(h)
    if abs(a - b) < 0.005:
        return "same"
    return "hier" if b > a else "flat"


def _brief(s: dict | None) -> dict:
    r = row("", s)
    return {k: r[k] for k in ("trades", "win", "pf", "ret", "dd", "halts")}


def passport(market: str, offline: bool = False, progress=None) -> dict:
    """The past-years test of the settings the bot trades with right now: no tuning, no variants.
    First 5 years are practice, last 5 years are the exam, plus 2-year parts. Real prices, real costs.
    Saved to runs/passport/<market>.json. progress(k, n) hears each of the n tests as it starts."""
    target = stages.passport_path(market)       # fixed now: a test started in the time machine stays in its world
    live = load_market(market)
    cfg = evidence_cfg(market)
    events = load_events()[market]
    frames = wf.load_frames(cfg, clock.now().normalize() - pd.DateOffset(years=10), offline)
    end = max(df.index[-1] for df in frames.values())
    start = end - pd.DateOffset(years=10) + pd.Timedelta(days=1)
    split = start + pd.DateOffset(years=5)
    train_end = str((split - pd.Timedelta(days=1)).date())
    parts_at = windows(start, end, 2)
    tell = (lambda k: progress(k, 2 + len(parts_at))) if progress else (lambda k: None)
    tell(1)
    md = prepare_market_data(cfg, frames, make_strategy(cfg))     # prepared once, used by all 7 tests (read only)
    train = wf.run_once(cfg, frames, events, str(start.date()), train_end, md=md)
    tell(2)
    test = wf.run_once(cfg, frames, events, str(split.date()), str(end.date()), md=md)
    if train is None or test is None:
        raise ValueError("Not enough price history for a past-years test.")
    parts = []
    for i, (a, b) in enumerate(parts_at, 3):
        tell(i)
        parts.append({"window": f"{a[:7]} to {b[:7]}", **_brief(wf.run_once(cfg, frames, events, a, b, md=md))})
    out = {"market": market, "made": datetime.now().strftime("%Y-%m-%d %H:%M"), "fingerprint": stages.fingerprint(live),
           "shares": len(cfg["universe"]["symbols"]) + len(cfg["universe"]["etfs"]),
           "start": str(start.date()), "train_end": train_end, "split": str(split.date()), "end": str(end.date()),
           "years_test": 5, "train": _brief(train), "test": _brief(test), "parts": parts, **stages.judge(test, live.get("pass"))}
    stages.save_passport(market, out, target)
    return out
