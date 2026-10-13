"""What the website shows for its five stages, built only from facts the bot really produced.

  1 Pick market          the practice account of this market is loaded from saved memory
  2 Search whole market  market check (index, fear gauge) and the strategy checks on every share
  3 Search my shares     every share already held: hold or sell
  4 Test past years      the saved past-years test of the settings in use now. A Run reuses it while it is up to date;
                         otherwise the Run starts it and waits (without blocking the live stop checks). New buys
                         only when it passed (validation_block)
  5 Buy or sell          risk gate, then buy and sell orders, then everything is saved

Nothing here changes a setting, an order or the strategy. A stage is green only when its result is real.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time

from . import config, report

NAMES = ["Pick market", "Search whole market", "Search my shares", "Test past years", "Buy or sell"]
IDLE, RUNNING, DONE, BLOCKED, ATTENTION = "idle", "running", "done", "blocked", "attention"
WORKING = {1: "Loading your practice account", 2: "Loading prices, then searching the whole market",
           3: "Loading prices, then checking the shares you hold (at the same time)",
           4: "Reading the saved past-years test", 5: "Placing orders and saving everything"}
FINISHED = 6                               # value of the "current stage" when no run is in progress


# ---------------------------------------------------------------- plain words
def money(cur: str, x: float | None, d: int = 0) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    whole, _, frac = f"{abs(x):.{d}f}".partition(".")
    if cur == "INR" and len(whole) > 3:                      # 10,00,000 style grouping
        head, tail, parts = whole[:-3], whole[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + parts + [tail])
    elif cur != "INR":
        whole = f"{int(whole):,}"
    return ("-" if x < 0 else "") + ("₹" if cur == "INR" else "$") + whole + (f".{frac}" if frac else "")


def signed(cur: str, x: float | None, d: int = 2) -> str:
    return "n/a" if x is None else ("+" if x >= 0 else "-") + money(cur, abs(x), d)


def _num(x, spec: str) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else format(x, spec)


def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


def _shares(q: float) -> str:
    return "1 share" if q == 1 else f"{q:g} shares"


def _name(c: dict, symbol: str) -> str:
    suffix = c.get("suffix") or ""
    return symbol[:-len(suffix)] if suffix and symbol.endswith(suffix) else symbol


def _clean(x):
    """JSON-safe: NaN and infinity become null, numpy numbers become plain numbers."""
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else x
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if hasattr(x, "item"):
        return _clean(x.item())
    return x


_io = threading.Lock()


def _write_json(path, obj) -> None:
    """Write whole or not at all, so the page never reads half a file.
    On Windows a reader can hold the file for a moment, so swapping it in is retried briefly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(_clean(obj), indent=1, default=str), encoding="utf-8")
    for attempt in range(40):
        try:
            with _io:
                os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)


def _read_json(path):
    for _ in range(20):
        try:
            with _io:
                return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        except OSError:                       # the writer is swapping the file in: look again in a moment
            time.sleep(0.02)
    return None


# ---------------------------------------------------------------- saved facts of the last run
def _facts_path(market: str, folder=None):
    return (folder or config.STATE_DIR) / f"last_run_{market}.json"


def load_facts(market: str, folder=None) -> dict:
    return _read_json(_facts_path(market, folder)) or {}


def save_facts(market: str, facts: dict, folder=None) -> None:
    _write_json(_facts_path(market, folder), facts)


# ---------------------------------------------------------------- saved past-years test ("passport")
def passport_path(market: str):
    return config.RUNS_DIR / "passport" / f"{market}.json"


def load_passport(market: str) -> dict | None:
    return _read_json(passport_path(market))


def save_passport(market: str, passport: dict, path=None) -> None:
    _write_json(path or passport_path(market), passport)


def fingerprint(cfg: dict) -> str:
    """Identifies the settings that decide trades. A saved test only counts while this is unchanged."""
    keep = {"strategy": cfg["strategy"], "regime": cfg["regime"], "costs": cfg["costs"],
            "risk": {k: v for k, v in cfg["risk"].items() if k != "max_positions"}}
    return hashlib.sha1(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:12]


PASSPORT_MAX_DAYS = 90                     # a saved test older than this (prices it used) is tested again


def passport_state(p: dict | None, fp: str, today=None, max_days: int = PASSPORT_MAX_DAYS) -> str:
    """missing, changed (settings differ), old (prices too old), failed or passed. p must be rejudged first."""
    import pandas as pd
    if not p:
        return "missing"
    if p.get("fingerprint") != fp:
        return "changed"
    today = pd.Timestamp(today if today is not None else _today()).normalize()
    if p.get("end") and (today - pd.Timestamp(p["end"])).days > max_days:
        return "old"
    return "passed" if p.get("passed") else "failed"


def _today():
    from . import clock
    return clock.now()                      # the time machine's day while it runs


def validation_block(cfg: dict, market: str, today=None) -> str | None:
    """Why new buys are not allowed by the past-years test, or None when it passed and is up to date.
    Shares already held are not affected: they keep their stops and are sold by their own rules."""
    marks = cfg.get("pass") or {}
    p = rejudge(load_passport(market), marks)
    state = passport_state(p, fingerprint(cfg), today, int(marks.get("max_age_days", PASSPORT_MAX_DAYS)))
    tail = " No new buys until a past-years test passes."
    if state == "missing":
        return "Strategy not validated: there is no past-years test for these settings yet." + tail
    if state == "changed":
        return "Strategy not validated: the settings changed since the last past-years test." + tail
    if state == "old":
        return f"Strategy not validated: the past-years test ends {p['end']}, more than {int(marks.get('max_age_days', PASSPORT_MAX_DAYS))} days ago." + tail
    if state == "failed":
        t = p.get("test") or {}
        bad = "; ".join(ch["text"] for ch in p.get("checks", []) if not ch["ok"]) or "too few trades to judge"
        return (f"Strategy did not pass the past-years test (profit factor {_num(t.get('pf'), '.2f')}, "
                f"biggest drop {_num(abs(t['dd']) if t.get('dd') is not None else None, '.1%')}): {bad}.") + tail
    return None


def judge(stats: dict | None, marks: dict | None = None) -> dict:
    """The pass marks (BOT_INSTRUCTIONS phase 1, or your settings) applied to one test result."""
    if not stats:
        return {"enough": False, "passed": False, "checks": []}
    m = marks or {}
    checks = [{"text": t, "ok": bool(ok)} for t, ok in report.pass_checks(
        stats, m.get("profit_factor", report.PASS_PF), m.get("max_drawdown", report.PASS_MAX_DD))]
    enough = int(stats.get("trades", 0)) >= int(m.get("min_trades", report.PASS_MIN_TRADES))
    return {"enough": enough, "checks": checks, "passed": enough and all(c["ok"] for c in checks)}


def rejudge(passport: dict | None, marks: dict | None) -> dict | None:
    """A saved test judged with the pass marks set now (changing a mark needs no new test)."""
    if not passport or not marks:
        return passport
    t = passport["test"]
    stats = {"profit_factor": t.get("pf"), "max_drawdown": t.get("dd"), "trades": t.get("trades") or 0}
    return {**passport, **judge(stats, marks)}


# ---------------------------------------------------------------- facts collected while the bot runs
def account_facts(cfg: dict, st) -> dict:
    if st is None:
        cap = float(cfg["capital"])
        return {"new": True, "budget": cap, "cash": cap, "holdings": 0, "orders": 0, "last_session": None}
    return {"new": False, "budget": float(st.capital_in()), "cash": float(st.cash), "holdings": len(st.positions),
            "orders": len(st.orders), "last_session": st.last_date}


def _short(cfg: dict, symbol: str) -> str:
    suffix = cfg["universe"].get("suffix", "")
    return symbol[:-len(suffix)] if suffix and symbol.endswith(suffix) else symbol


def look(cfg: dict, md, day) -> dict:
    """Stage 2 facts: how healthy the whole market is, and which shares pass the strategy checks.
    Read-only. The engine makes the same checks again when it decides."""
    rc, risk = cfg["regime"], float(cfg["risk"]["risk_per_trade"])
    bm = rc.get("bad_market") or {}
    ok, text = md.regime(day)
    if ok:
        level = md.level(day) if rc.get("levels") else "good"
        mult = 1.0 if level == "good" else float(rc.get("normal_risk_mult", 0.5))
        market = {"level": level, "extra_safe": False, "risk": risk * mult}
    elif rc.get("levels") and bm.get("allow") and md.only_below_trend(day):
        market = {"level": "bad", "extra_safe": True, "risk": risk * float(bm.get("risk_mult", 0.2))}
    else:
        market = {"level": "bad", "extra_safe": False, "risk": 0.0}
    rows = []
    matched = getattr(md.strategy, "matched", None)     # several rules: which ones want this share
    families = getattr(md.strategy, "families", None)   # the 4 pattern families
    for sym, bar in md.bars_on(day).items():
        flag = lambda name: bool(getattr(bar, name, False))
        roc = getattr(bar, "roc", float("nan"))
        rows.append({"symbol": _short(cfg, sym), "trend": flag("f_trend"), "breakout": flag("f_breakout"),
                     "volume": flag("f_volume"), "liquid": flag("f_liquid"), "signal": flag("signal"),
                     "quick": bool(getattr(bar, "f_quick", True)) and flag("f_liquid"),
                     "rules": matched(bar) if matched and flag("signal") else [],
                     "families": families(bar) if families and flag("signal") else [],
                     "score": None if roc is None or math.isnan(roc) else float(roc)})
    qualified = sorted((r for r in rows if r["signal"]), key=lambda r: -(r["score"] or 0.0))
    out = {"session": str(day.date()), **market, "text": text, "scanned": len(rows),
           "missing": max(len(md.rows) - len(rows), 0), "breakout_days": int(cfg["strategy"]["breakout_days"]),
           "trend": sum(r["trend"] for r in rows), "breakout": sum(r["breakout"] for r in rows),
           "volume": sum(r["volume"] for r in rows), "qualified": [r["symbol"] for r in qualified],
           "misses": {r["symbol"]: [k for k in ("trend", "breakout", "volume", "liquid") if not r[k]]
                      for r in rows if not r["signal"]}}
    if matched:
        out["rules"] = len(getattr(md.strategy, "keys", []))
        out["matched"] = {r["symbol"]: r["rules"] for r in qualified}
    if families:                                        # the funnel: all shares → quick check → a pattern → by family
        fam: dict[str, int] = {}
        for r in qualified:
            for f in r["families"][:1]:
                fam[f] = fam.get(f, 0) + 1
        out["funnel"] = {"all": len(rows), "quick": sum(r["quick"] for r in rows), "pattern": len(qualified), "families": fam,
                         "tested": sum(1 for b in md.bars_on(day).values() if getattr(b, "tested", False))}
        hist = getattr(md.strategy, "history", None)
        if hist:                                         # the chosen shares, each tested on its own past
            bars = {_short(cfg, s): b for s, b in md.bars_on(day).items()}
            out["history"] = {r["symbol"]: h for r in qualified[:40] if (h := hist(bars[r["symbol"]]))}
    if len(rows) <= 12:
        out["rows"] = rows
    return out


def review_held(cfg: dict, md, day, st) -> list[dict]:
    """The other side, at the same time as the market search: each held share, its strategy and pattern family,
    is its pattern still fine, and is it on track with its expected band (tbot/predict.py). Read-only."""
    from .rules import LABELS, family_label
    if st is None:
        return []
    bars, out = md.bars_on(day), []
    for sym, p in st.positions.items():
        bar, rule = bars.get(sym), p.strategy or "trend_breakout"
        out.append({"symbol": _short(cfg, sym), "rule": LABELS.get(rule, rule), "family": family_label(rule),
                    "pattern_ok": None if bar is None else bool(getattr(bar, "f_quick", True)),
                    "band": p.pred_state or "no band yet"})
    return out


def run_facts(results: list) -> dict:
    """Stage 3 and 5 facts from what the engine really did in this run."""
    last = results[-1]
    return {
        "exits": [{"symbol": t.symbol, "qty": float(t.qty), "price": float(t.exit_price), "pnl": float(t.pnl),
                   "why": t.exit_reason, "date": t.exit_date} for r in results for t in r.trades],
        "decision": {
            "session": str(last.date.date()), "sessions": len(results),
            "buys": [{"symbol": o.symbol, "qty": float(o.qty), "est_price": o.est_price, "stop": o.stop,
                      "risk": o.planned_risk} for o in last.orders if o.side == "BUY"],
            "sells": [{"symbol": o.symbol, "qty": float(o.qty), "why": o.reason} for o in last.orders if o.side == "SELL"],
            "rejects": [{"symbol": s, "why": m} for k, s, m in last.events if k == "reject"][:8],
            "blocked": next((m for k, _, m in last.events if k == "blocked"), None)}}


def queue_facts(st) -> dict:
    """No new session was processed, so the decision is simply what is queued now."""
    def side(name: str):
        return [o for o in st.orders if o.side == name]
    return {"session": st.last_date, "sessions": 0, "rejects": [], "blocked": None,
            "buys": [{"symbol": o.symbol, "qty": float(o.qty), "est_price": o.est_price, "stop": o.stop,
                      "risk": o.planned_risk} for o in side("BUY")],
            "sells": [{"symbol": o.symbol, "qty": float(o.qty), "why": o.reason} for o in side("SELL")]}


# ---------------------------------------------------------------- the five stage reports
def _carry(c: dict) -> list[str]:
    f = c["facts"]
    if not f.get("carried"):
        return []
    day = (f.get("decision") or {}).get("session")
    if f.get("processed_at"):
        return [f"No new market day since {day}, so nothing new was decided. This is the decision made on {f['processed_at']}."]
    return [f"No new market day since {day}, so nothing new was decided."]


def _s1(c: dict):
    a = c.get("account")
    if not a:
        return IDLE, "Waiting for Run", ["Press Run bot. It opens a practice account with the budget above."]
    cur = c["currency"]
    lines = [f"Market hours: {c['hours'].split(' (')[0]}. The market is {'open' if c['open'] else 'closed'} now."]
    if not c["started"]:
        lines.append(f"Opening a new practice account with {money(cur, a['budget'])}.")
    elif (c["facts"].get("account") or {}).get("new"):
        lines.append(f"Opened a new practice account with {money(cur, a['budget'])} on the first run.")
    else:
        lines.append(f"Practice account loaded from saved memory: budget {money(cur, a['budget'])}, money not used yet {money(cur, a['cash'])}.")
    lines.append(f"Holding {_n(a['holdings'], 'share')}. {_n(a['orders'], 'order')} waiting.")
    if a.get("last_session"):
        lines.append(f"Last market day counted: {a['last_session']}.")
    return DONE, f"{c['name']} market chosen", lines


def _s2(c: dict):
    lk = c["facts"].get("look")
    if not lk:
        return IDLE, "Not checked yet", ["Press Run bot to check the whole market."]
    days, n, q = lk["breakout_days"], lk["scanned"], lk["qualified"]
    if lk["level"] == "good":
        status, head = DONE, "Market is good"
        lines = [f"{lk['text']}. The bot may risk up to {lk['risk']:.2%} of the account on one trade."]
    elif lk["level"] == "normal":
        status, head = DONE, "Market is normal"
        lines = [f"{lk['text']}. The market is up but not steady enough for full risk, so risk is cut to {lk['risk']:.2%} per trade."]
    elif lk["extra_safe"]:
        status, head = ATTENTION, "Market is falling: extra-safe mode"
        lines = [f"{lk['text']}. The bot still looks, but buys at most one very strong share, with a tiny risk of {lk['risk']:.2%}."]
    else:
        return BLOCKED, "Market is falling: no new buys", [f"{lk['text']}. The bot keeps your money and skips the share search."]
    lines.insert(0, f"Market day checked: {lk['session']}.")
    fu = lk.get("funnel")
    if fu:
        fam = ", ".join(f"{k} {v}" for k, v in fu["families"].items())
        lines.append(f"Funnel: {_n(fu['all'], 'share')} → {fu['quick']} pass the quick check (above the 200-day average "
                     f"or rising over 3 months) → {fu['pattern']} show a pattern" + (f": {fam}." if fam else "."))
    if c.get("recheck"):
        rc = c["recheck"]
        lines.append(f"Live re-check {rc['at']}: {rc['market']}; {rc['quick']} of {rc['all']} shares pass the quick check now.")
    if lk.get("rules"):
        lines.append(f"Checked {_n(n, 'share')} with {lk['rules']} strategies. {len(q)} matched at least one.")
        count: dict[str, int] = {}
        for names in lk.get("matched", {}).values():
            for k in names:
                count[k] = count.get(k, 0) + 1
        if count:
            lines.append("Matches: " + ", ".join(f"{k} {v}" for k, v in count.items()) + ".")
    else:
        lines.append(f"Checked {_n(n, 'share')}: {lk['trend']} in an uptrend, {lk['breakout']} at a new {days}-day high, "
                     f"{lk['volume']} with strong volume. {len(q)} passed every check.")
    for r in lk.get("rows", []):
        lines.append(f"{r['symbol']}: uptrend {_yes(r['trend'])}, new {days}-day high {_yes(r['breakout'])}, "
                     f"strong volume {_yes(r['volume'])}. " + ("Passed." if r["signal"] else "Not strong enough."))
    lines.append("Passed: " + ", ".join(q) + "." if q else "No share is strong enough today, so there is nothing new to buy.")
    if lk.get("missing"):
        lines.append(f"{_n(lk['missing'], 'share')} had no price today.")
    return status, f"{head} · {len(q)} of {n} shares passed", lines


def _s3(c: dict):
    f = c["facts"]
    if "held" not in f and (not c.get("started") or "decision" not in f):     # this side has not looked yet
        return IDLE, "Not checked yet", ["Press Run bot. The bot checks every share you already hold, next to the market search."]
    cur, sells, lines, k = c["currency"], {o["symbol"]: o for o in c["orders"] if o["side"] == "SELL"}, [], 0
    for p in c["positions"]:
        if p["symbol"] in sells:
            word, why = "SELL", f"Sells at the next open: {sells[p['symbol']]['reason']}."
        elif p["price"] <= p["stop"]:
            word, why = "SELL", "The price is at the stop. The live watch sells it now."
        elif p["target"] and p["price"] >= p["target"]:
            word, why = "SELL", "The profit target is reached. The live watch sells it now."
        else:
            word, why = "HOLD", "Keep holding."
        if word == "SELL":
            k += 1
        stop = money(cur, p["stop"], 2) + (" (moved up, profit protected)" if p["stop"] >= p["buy_price"] else "")
        lines.append(f"{word} {_name(c, p['symbol'])}: {_shares(p['qty'])} bought at {money(cur, p['buy_price'], 2)}, "
                     f"now {money(cur, p['price'], 2)} ({signed(cur, p['pnl'])}). Stop {stop}"
                     + (f", target {money(cur, p['target'], 2)}" if p["target"] else "") + f". {why}")
    live = (c.get("recheck") or {}).get("held") or {}
    for h in f.get("held") or []:                      # pattern and near-future band of each held share
        ok = {True: "pattern still fine", False: "pattern broken", None: "no price today"}[h["pattern_ok"]]
        lines.append(f"{h['symbol']}: {h['rule']} ({h['family']}), {ok}, near future: {live.get(h['symbol'], h['band'])}.")
    exits = f.get("exits") or []
    for t in exits:
        lines.append(f"SOLD {_name(c, t['symbol'])}: {_shares(t['qty'])} at {money(cur, t['price'], 2)} "
                     f"({signed(cur, t['pnl'])}). {t['why']}.")
    if not c["positions"] and not exits:
        return DONE, "Nothing held, nothing to check", ["You hold no shares, so there is nothing to check."]
    title = _n(len(c["positions"]), "share") + " held" + (f" · {k} to sell" if k else " · all hold")
    return DONE, title + (f" · {len(exits)} sold" if exits else ""), lines + (_carry(c) if exits else [])


def _s4(c: dict):
    """Step 3 on the page: first the chosen shares, each on its own past; then the whole strategy mix on 10 years."""
    status, title, lines = _s4_mix(c)
    return status, title, _share_tests(c) + lines


def _share_tests(c: dict) -> list[str]:
    lk = c["facts"].get("look") or {}
    hist, fu = lk.get("history") or {}, lk.get("funnel") or {}
    if not hist:
        return []
    out = [f"Chosen shares tested on their own past: {fu.get('tested', 0)} of {fu.get('pattern', 0)} passed "
           "(every past buy signal of that strategy on that share in the last 3 years, checked 20 days later)."]
    for sym, h in list(hist.items())[:5]:
        out.append(f"{sym}: {h['rule']}, {h['n']} past signals, {h['up']} up, average {_num(h['avg'], '+.1%')}: "
                   + ("passed." if h["ok"] else "not enough proof, not bought."))
    out.append("Then the whole strategy mix is tested on 10 years:")
    return out


def _s4_mix(c: dict):
    t, p = c["test"], c["passport"]
    if t.get("running"):
        k, n = t.get("step") or 0, t.get("steps") or 7
        return RUNNING, f"Testing the past years: test {max(k, 1)} of {n}", [
            "Train 5 years, exam 5 years, then five 2-year parts, one after another. A minute or two.",
            "A Run waits here until the test is finished, then goes on to Buy or sell."]
    if p is None:
        lines = ["There is no saved past-years test for these settings yet. The next Run tests first (7 tests, a minute or two),",
                 "or press Test again. New buys only after it passes."]
        if t.get("error"):
            lines.insert(0, f"The last try failed: {t['error']}")
            return ATTENTION, "The last test failed", lines
        return IDLE, "Tested at the next Run", lines
    te, tr = p["test"], p["train"]
    lines = [("Pass: " if ch["ok"] else "Fail: ") + ch["text"] + "." for ch in p["checks"]]
    lines.append(f"Last {int(p['years_test'])} years, never used to choose a setting ({p['split']} to {p['end']}): "
                 f"return {_num(te['ret'], '+.1%')}, {te['trades']} trades, {_num(te['win'], '.0%')} won."
                 + ("" if p["enough"] else f" Only {te['trades']} trades: too few to judge."))
    lines.append(f"First 5 years ({p['start']} to {p['train_end']}): profit factor {_num(tr['pf'], '.2f')}, "
                 f"biggest drop {_num(abs(tr['dd']) if tr['dd'] is not None else None, '.1%')}, return {_num(tr['ret'], '+.1%')}.")
    parts = p.get("parts") or []
    if parts:
        plus = sum(1 for x in parts if x["ret"] is not None and x["ret"] > 0)
        lines.append("Two-year parts: " + ", ".join(_num(x["ret"], "+.1%") for x in parts) + f". Profitable in {plus} of {len(parts)}.")
    lines.append(f"Tested on {_n(p['shares'], 'share')} with the same rules and real costs, on {p['made']}.")
    if t.get("error"):
        lines.append(f"The last re-test failed: {t['error']}")
    state = passport_state(p, c["fingerprint"], c.get("today"))
    held = "Shares you hold are still watched and sold by their own rules."
    if not c.get("started"):                          # before the first Run nothing is wrong yet: just say what the Run will do
        word = {"passed": "Saved test passed", "failed": "Saved test did not pass"}.get(state, "Tested again at the next Run")
        return IDLE, word, ["Checked when you press Run bot."] + lines
    if state == "changed":
        return IDLE, "Settings changed: tested again at the next Run", [
            "No new buys until a new test passes. The next Run tests again first, or press Test again.", held] + lines
    if state == "old":
        return IDLE, "Test is out of date: tested again at the next Run", [
            f"The test used prices up to {p['end']}, more than {PASSPORT_MAX_DAYS} days ago. The next Run tests again first.", held] + lines
    if p["passed"]:
        return DONE, "Strategy passed the past-years test", lines + ["New buys are allowed (they still pass every risk check)."]
    return ATTENTION, "Strategy has NOT passed: no new buys", lines + [
        "No new buys until it passes. " + held, "Real money stays locked until this passes."]


def _s5(c: dict):
    d = c["facts"].get("decision")
    if not c.get("started") or not d:
        return IDLE, "Nothing decided yet", ["Press Run bot. The bot checks the risk rules, then buys, sells or does nothing."]
    cur, block = c["currency"], c.get("block")
    held = {p["symbol"]: p for p in c["positions"]}
    waiting = {o["symbol"] for o in c["orders"] if o["side"] == "BUY"}
    sells = {o["symbol"]: o for o in c["orders"] if o["side"] == "SELL"}
    lines, parts = [], []
    if block:
        lines += [block, "The bot can still sell what you hold, but it will not buy."]
    for b in d["buys"]:
        sym, name = b["symbol"], _name(c, b["symbol"])
        if sym in held:
            p = held[sym]
            lines.append(f"BUY {name}: bought {_shares(p['qty'])} at {money(cur, p['buy_price'], 2)}. "
                         f"Stop {money(cur, p['stop'], 2)}, risk {money(cur, b['risk'], 2)}.")
            parts.append(f"Bought {name}")
        elif sym in waiting:
            lines.append(f"BUY {name}: order for {_shares(b['qty'])} is waiting. "
                         f"It fills at the next open (about {money(cur, b['est_price'], 2)}).")
            parts.append(f"Buy order waiting: {name}")
        else:
            gone = next((t for t in c.get("trades", []) if t["symbol"] == sym and str(t["entry_date"]) >= d["session"]), None)
            if gone:
                lines.append(f"BUY {name}: bought, then sold again at {money(cur, gone['exit_price'], 2)} "
                             f"({signed(cur, gone['pnl'])}). {gone['exit_reason']}.")
                parts.append(f"Bought and sold {name}")
            else:
                lines.append(f"BUY {name}: the order is no longer waiting. {(c['facts'].get('live') or {}).get('msg', '')}".strip())
    for t in c["facts"].get("exits") or []:
        lines.append(f"SELL {_name(c, t['symbol'])}: sold {_shares(t['qty'])} at {money(cur, t['price'], 2)} "
                     f"({signed(cur, t['pnl'])}). {t['why']}.")
        parts.append(f"Sold {_name(c, t['symbol'])}")
    for sym, o in sells.items():
        lines.append(f"SELL {_name(c, sym)}: sells at the next open. {o['reason']}")
        parts.append(f"Selling {_name(c, sym)}")
    keep = [_name(c, s) for s in held if s not in sells and not any(b["symbol"] == s for b in d["buys"])]
    if keep:
        lines.append("HOLD: " + ", ".join(keep) + ". No sell signal.")
    if not parts:
        lines.append("No buy and no sell today." + ("" if held else " Your money is kept."))
    for r in d["rejects"][:3]:
        lines.append(f"Refused {_name(c, r['symbol'])}: {r['why']}.")
    named = {_name(c, b["symbol"]) for b in d["buys"]} | {_name(c, s) for s in held} | {_name(c, r["symbol"]) for r in d["rejects"]}
    left = [s for s in (c["facts"].get("look") or {}).get("qualified", []) if s not in named]
    if left and not block:
        lines.append(f"Also passed the checks but not bought: {', '.join(left)}. The bot buys the best-ranked share first "
                     f"and holds at most {_n(c.get('limit', 1), 'share')} at a time.")
    if d.get("blocked") and not d["buys"] and not block:
        lines.append(d["blocked"].replace("No new entries: ", "No new buys: ") + ".")
    live = (c["facts"].get("live") or {}).get("msg")
    if live:
        lines.append(f"Live fill: {live}")
    if passport_state(c["passport"], c["fingerprint"], c.get("today")) != "passed":
        lines.append("Real money stays locked until the past-years test passes.")
    if c.get("monitor"):
        lines.append("Next: " + c["monitor"])
    if block:
        return BLOCKED, "Safety stop: no new buys", lines
    return DONE, " · ".join(parts) or "No trade today", lines + _carry(c)


def build(c: dict) -> list[dict]:
    """The five stage reports. c holds only real facts (see web.simple_view).
    c['cur'] is 1 to 5 while that stage runs, 6 when no run is in progress."""
    cur, running, error = c["cur"], c["running"], c.get("error")
    out = []
    for n, (status, title, lines) in enumerate([_s1(c), _s2(c), _s3(c), _s4(c), _s5(c)], 1):
        testing = n == 4 and c["test"].get("running")
        if running and testing and cur < 4:            # a test already working on its own: the Run reaches it at step 3
            t = c["test"]
            status, title, lines = IDLE, (f"Next: a past-years test is already running (test {max(t.get('step') or 0, 1)} "
                                          f"of {t.get('steps') or 7}); the Run uses it here"), []
        elif running and not testing:
            if n > cur and not (cur == 2 and n == 3):
                status, title, lines = IDLE, "Waiting", []
            elif n == cur or (cur in (2, 3) and n in (2, 3)):   # market search and my shares: one step pair, side by side
                status, title, lines = RUNNING, WORKING[n], []
        elif error and cur != FINISHED and not testing:
            if n == cur:
                status, title, lines = BLOCKED, "The run stopped here", [str(error)]
            elif n > cur:
                status, title, lines = IDLE, "Waiting", []
        out.append({"n": n, "name": NAMES[n - 1], "status": status, "title": title, "lines": lines})
    return out
