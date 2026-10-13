"""Walk-forward and sensitivity tests: does the strategy hold up on years it was not tuned on?

  python run.py walkforward us --start 2015-01-01 --split 2022-01-01

Three tests, all using the real engine, real costs and next-day fills:
  1. Year by year      The same fixed rules, one calendar year at a time. Shows good and bad years.
  2. Sensitivity       Change one setting at a time (stop, target, breakout days, ...) on the practice years.
                       If a small change flips the result, the strategy is fragile.
  3. Practice / exam   Pick the best value of each setting on the practice years only, then run the
                       exam years once. Compare with the untouched settings. Tuning that does not help
                       on the exam years was just fitting noise.

Nothing here changes the live settings and nothing is written to runs/backtest.
"""
from __future__ import annotations

import copy
import json
import logging
import math
from datetime import datetime

import pandas as pd

from . import config, data, report
from .config import load_events, load_market, regime_tickers, universe_tickers
from .engine import Engine, prepare_market_data, results_to_frames
from .strategy import make_strategy

# One setting at a time: (section, key) -> values to try. The live value is always added.
GRID = {
    ("strategy", "breakout_days"): [10, 20, 40],
    ("strategy", "volume_mult"): [1.0, 1.3, 1.6],
    ("strategy", "stop_atr"): [1.5, 2.0, 3.0],
    ("strategy", "trail_atr"): [1.5, 2.0, 3.0],
    ("strategy", "take_profit_r"): [2.5, 3.0, 4.0],      # 2.0 would fail the min reward:risk rule after costs
}
MIN_TRADES = 30          # a setting is only compared when it made at least this many trades


# ---------------------------------------------------------------- one run

def load_frames(cfg: dict, start, offline: bool = False) -> dict:
    strategy = make_strategy(cfg)
    lookback = pd.Timedelta(days=int(strategy.warmup_bars * 1.5) + 30)
    frames, _ = data.refresh(cfg["name"], universe_tickers(cfg) + regime_tickers(cfg),
                             pd.Timestamp(start) - lookback, offline=offline)
    return {t: data.drop_incomplete(df, cfg) for t, df in frames.items()}


def run_once(cfg: dict, frames: dict, events: set, start, end=None, overrides: dict | None = None,
             md=None) -> dict | None:
    """Backtest one date range with some settings changed. None when the range is too short.
    md: market data already prepared with exactly these settings (saves preparing it again)."""
    c = copy.deepcopy(cfg)
    for (section, key), value in (overrides or {}).items():
        c[section][key] = value
    if md is None or overrides:
        md = prepare_market_data(c, frames, make_strategy(c))
    dates = md.calendar(start, end)
    if len(dates) < 20:
        return None
    eng = Engine(c, md, events, mode="backtest")
    st = eng.new_state(dates[0])
    equity, trades, _ = results_to_frames(eng.run(st, dates))
    return report.summarize(equity["equity"], trades, st.initial_capital, st.halts)


def _f(s: dict | None, key: str) -> float:
    v = (s or {}).get(key)
    return float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else float("nan")


# ---------------------------------------------------------------- pure helpers (unit tested)

def year_windows(start: str, end: str) -> list[tuple[str, str]]:
    """Calendar-year windows from start to end. The last one may be a part year."""
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    out = []
    for y in range(s.year, e.year + 1):
        a, b = max(s, pd.Timestamp(f"{y}-01-01")), min(e, pd.Timestamp(f"{y}-12-31"))
        out.append((str(a.date()), str(b.date())))
    return out


def choose_best(rows: list[dict], default) -> object:
    """Best value of one setting by profit factor, ignoring variants with too few trades.
    Keeps the live value unless another one is clearly better (higher profit factor by over 0.05)."""
    ok = [r for r in rows if r["trades"] >= MIN_TRADES and not math.isnan(r["pf"])]
    if not ok:
        return default
    best = max(ok, key=lambda r: r["pf"])
    base = next((r for r in ok if r["value"] == default), None)
    if base is not None and best["pf"] <= base["pf"] + 0.05:
        return default
    return best["value"]


def sensitivity_verdict(default_pf: float, pfs: list[float]) -> tuple[bool, str]:
    """Fragile when profit factor swings a lot or is above 1.0 in under half of the variants."""
    pfs = [p for p in pfs if not math.isnan(p)]
    if not pfs or math.isnan(default_pf):
        return False, "Not enough trades to judge."
    lo, hi = min(pfs), max(pfs)
    above = sum(p > 1.0 for p in pfs)
    fragile = (hi - lo) > 0.5 or above < len(pfs) / 2
    text = (f"Profit factor ranges from {lo:.2f} to {hi:.2f} across {len(pfs)} variants, "
            f"and is above 1.0 in {above} of them. ")
    text += ("FRAGILE: small changes to the settings change the result a lot."
             if fragile else "STABLE: small changes to the settings do not flip the result.")
    return fragile, text


# ---------------------------------------------------------------- the three tests

def yearly(cfg, frames, events, start, end) -> list[dict]:
    rows = []
    for a, b in year_windows(start, end):
        s = run_once(cfg, frames, events, a, b)
        rows.append({"window": a[:4] if b[:4] == a[:4] and a.endswith("01-01") and b.endswith("12-31") else f"{a} to {b}",
                     "trades": (s or {}).get("trades", 0), "ret": _f(s, "total_return"), "pf": _f(s, "profit_factor"),
                     "dd": _f(s, "max_drawdown"), "win": _f(s, "win_rate")})
    return rows


def sensitivity(cfg, frames, events, start, end) -> tuple[list[dict], dict]:
    base = run_once(cfg, frames, events, start, end)
    rows = []
    for (section, key), values in GRID.items():
        live = cfg[section][key]
        for v in sorted(set(values) | {live}):
            s = base if v == live else run_once(cfg, frames, events, start, end, {(section, key): v})
            rows.append({"setting": key, "value": v, "live": v == live, "trades": (s or {}).get("trades", 0),
                         "ret": _f(s, "total_return"), "pf": _f(s, "profit_factor"), "dd": _f(s, "max_drawdown")})
    return rows, base


def practice_exam(cfg, frames, events, start, split, end, sens_rows) -> dict:
    best = {}
    for (section, key) in GRID:
        mine = [r for r in sens_rows if r["setting"] == key]
        best[(section, key)] = choose_best(mine, cfg[section][key])
    changed = {k: v for k, v in best.items() if v != cfg[k[0]][k[1]]}
    exam_live = run_once(cfg, frames, events, split, end)
    exam_tuned = run_once(cfg, frames, events, split, end, best) if changed else exam_live
    return {"best": best, "changed": changed, "exam_live": exam_live, "exam_tuned": exam_tuned}


# ---------------------------------------------------------------- report

def _p(x) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else report._pct(x)


def _n(x, d=2) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{d}f}"


def run(market: str, start: str = "2015-01-01", split: str = "2022-01-01", end: str | None = None,
        offline: bool = False) -> dict:
    cfg = load_market(market)
    events = load_events()[market]
    frames = load_frames(cfg, start, offline)
    last = max(df.index[-1] for df in frames.values())
    end = end or str(last.date())
    train_end = str((pd.Timestamp(split) - pd.Timedelta(days=1)).date())
    print(f"Walk-forward test for {market.upper()}: practice {start} to {train_end}, exam {split} to {end}")
    missing = [t for t in universe_tickers(cfg) if t not in frames or len(frames[t]) < 30]
    if missing:
        print(f"Skipped {len(missing)} symbols with no data: {', '.join(missing)}")
    logging.getLogger("tbot.engine").setLevel(logging.ERROR)       # the same warning would repeat on every run

    print("1/3 year by year ...")
    years = yearly(cfg, frames, events, start, end)
    print("2/3 sensitivity on the practice years ...")
    sens, base = sensitivity(cfg, frames, events, start, train_end)
    print("3/3 practice and exam ...")
    pe = practice_exam(cfg, frames, events, start, split, end, sens)

    good_years = [y for y in years if y["trades"] and y["ret"] > 0]
    scored = [y for y in years if y["trades"]]
    fragile, sens_text = sensitivity_verdict(_f(base, "profit_factor"), [r["pf"] for r in sens])
    live, tuned = pe["exam_live"], pe["exam_tuned"]
    exam_pf, practice_pf = _f(live, "profit_factor"), _f(base, "profit_factor")
    if math.isnan(exam_pf):
        exam_word = "not enough exam trades."
    elif exam_pf >= report.PASS_PF:
        exam_word = "it holds up and passes the 1.3 mark."
    elif exam_pf >= 1.0:
        exam_word = f"it stays profitable but is below the {report.PASS_PF} pass mark."
    else:
        exam_word = "it did NOT hold up on years it had not seen."
    verdicts = [
        f"Profitable years: {len(good_years)} of {len(scored)} (years with at least one trade).",
        sens_text,
        f"Practice years profit factor {_n(practice_pf)}, exam years {_n(exam_pf)} with the live settings: {exam_word}",
    ]
    verdicts += [f"Exam years {'PASS' if ok else 'FAIL'}: {text}" for text, ok in report.pass_checks(live or {})] if live else []
    if pe["changed"]:
        helped = _f(tuned, "profit_factor") > exam_pf + 0.05
        verdicts.append("Tuning on the practice years " + ("helped" if helped else "did NOT help")
                        + f" on the exam years (profit factor {_n(exam_pf)} live, {_n(_f(tuned, 'profit_factor'))} tuned). "
                        + ("One split only, so repeat with another --split before trusting it." if helped
                           else "Keep the live settings; the tuned ones were fitted to noise."))
    else:
        verdicts.append("No setting beat the live value by a clear margin on the practice years, so nothing was tuned.")

    cur = cfg["currency"]
    lines = [f"Walk-forward test, {market.upper()} ({cur}, virtual money, real past prices)", "-" * 60,
             f"Practice years {start} to {train_end}   Exam years {split} to {end}", "", "1. YEAR BY YEAR (live settings)",
             f"{'Year':<24}{'Trades':>7}{'Return':>9}{'PF':>7}{'Max DD':>9}{'Win':>7}"]
    lines += [f"{y['window']:<24}{y['trades']:>7}{_p(y['ret']):>9}{_n(y['pf']):>7}{_p(y['dd']):>9}{_p(y['win']):>7}" for y in years]
    lines += ["", "2. SENSITIVITY (practice years, one setting changed at a time)",
              f"{'Setting':<16}{'Value':>7}{'Trades':>8}{'Return':>9}{'PF':>7}{'Max DD':>9}"]
    for r in sens:
        lines.append(f"{r['setting']:<16}{r['value']:>7g}{r['trades']:>8}{_p(r['ret']):>9}{_n(r['pf']):>7}{_p(r['dd']):>9}"
                     + ("  <- live" if r["live"] else ""))
    lines += ["", "3. PRACTICE / EXAM",
              f"Best values from practice years: " + (", ".join(f"{k[1]}={v:g}" for k, v in pe["changed"].items()) or "none beat the live ones"),
              f"Exam, live settings : return {_p(_f(live, 'total_return'))}, PF {_n(exam_pf)}, max DD {_p(_f(live, 'max_drawdown'))}, trades {(live or {}).get('trades', 0)}",
              f"Exam, tuned settings: return {_p(_f(tuned, 'total_return'))}, PF {_n(_f(tuned, 'profit_factor'))}, max DD {_p(_f(tuned, 'max_drawdown'))}, trades {(tuned or {}).get('trades', 0)}",
              "", "VERDICT"] + [f"  - {v}" for v in verdicts]
    text = "\n".join(lines)
    print("\n" + text)

    out = config.RUNS_DIR / "walkforward" / f"{market}_{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.txt").write_text(text, encoding="utf-8")
    result = {"market": market, "start": start, "split": split, "end": end, "years": years, "sensitivity": sens,
              "practice": base, "exam_live": live, "exam_tuned": tuned,
              "changed": {k[1]: v for k, v in pe["changed"].items()}, "fragile": fragile, "verdicts": verdicts}
    (out / "results.json").write_text(json.dumps(result, default=lambda x: None, indent=1)
                                      .replace("NaN", "null").replace("Infinity", "null"), encoding="utf-8")
    cls = lambda ok: "good" if ok else "bad"
    body = [f"<h1>Walk-forward test: {market.upper()}</h1>",
            f"<p class='sub'>Practice years {start} to {train_end}. Exam years {split} to {end}. "
            f"Real past prices, real costs, next-day fills. Live settings are never changed by this test.</p>",
            report.kpis([("Profitable years", f"{len(good_years)} of {len(scored)}", cls(len(good_years) >= len(scored) / 2)),
                         ("Practice PF", _n(practice_pf), cls(practice_pf >= 1.0)),
                         ("Exam PF", _n(exam_pf), cls(exam_pf >= 1.0)),
                         ("Sensitivity", "fragile" if fragile else "stable", cls(not fragile))]),
            "<h2>Verdict</h2><div class='card'>" + "".join(f"<div>{v}</div>" for v in verdicts) + "</div>",
            "<h2>Year by year</h2>",
            report.table([{"w": y["window"], "t": y["trades"], "r": _p(y["ret"]), "pf": _n(y["pf"]), "dd": _p(y["dd"]),
                           "win": _p(y["win"])} for y in years],
                         [("w", "Year"), ("t", "Trades"), ("r", "Return"), ("pf", "Profit factor"), ("dd", "Max drawdown"),
                          ("win", "Win rate")], {"t", "r", "pf", "dd", "win"}),
            "<h2>Sensitivity (practice years)</h2>",
            report.table([{"s": r["setting"], "v": f"{r['value']:g}" + (" (live)" if r["live"] else ""), "t": r["trades"],
                           "r": _p(r["ret"]), "pf": _n(r["pf"]), "dd": _p(r["dd"])} for r in sens],
                         [("s", "Setting"), ("v", "Value"), ("t", "Trades"), ("r", "Return"), ("pf", "Profit factor"),
                          ("dd", "Max drawdown")], {"t", "r", "pf", "dd"})]
    (out / "report.html").write_text(report.page(f"Walk-forward {market.upper()}", "\n".join(body)), encoding="utf-8")
    print(f"\nSaved: {out}\nOpen {out / 'report.html'} in a browser.")
    return result
