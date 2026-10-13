"""Commands: backtest, daily, status, check, reset-halt."""
from __future__ import annotations

import contextlib
import html
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime

import pandas as pd

from . import clock, config, data, report, stages
from .config import (STOP_FILE, load_events, load_market, regime_tickers,
                     stopped, universe_tickers)
from .engine import Engine, MarketData, apply_splits, prepare_market_data, results_to_frames
from .store import Store
from .strategy import make_strategy

log = logging.getLogger("tbot")


def load_data(cfg: dict, start, offline: bool = False) -> tuple[MarketData, dict]:
    strategy = make_strategy(cfg)
    lookback = pd.Timedelta(days=int(strategy.warmup_bars * 1.5) + 30)
    fetch_from = pd.Timestamp(start) - lookback
    tickers = universe_tickers(cfg) + regime_tickers(cfg)
    frames, splits = data.refresh(cfg["name"], tickers, fetch_from, offline=offline)
    frames = {t: data.drop_incomplete(df, cfg) for t, df in frames.items()}
    return prepare_market_data(cfg, frames, strategy), splits


def _benchmark(md: MarketData, start, end) -> float | None:
    c = md.index["Close"]
    c = c[(c.index >= start) & (c.index <= end)]
    return float(c.iloc[-1] / c.iloc[0] - 1) if len(c) > 1 else None


# ---------------------------------------------------------------- backtest

def backtest(market: str, start: str, end: str | None = None, offline: bool = False,
             blackouts: bool = True) -> dict:
    cfg = load_market(market)
    events = load_events()[market] if blackouts else set()
    md, _ = load_data(cfg, start, offline)
    dates = md.calendar(start, end)
    if not dates:
        raise SystemExit("No trading days in that date range.")
    eng = Engine(cfg, md, events, mode="backtest")
    st = eng.new_state(dates[0])
    results = eng.run(st, dates)
    equity, trades, events_df = results_to_frames(results)
    stats = report.summarize(equity["equity"], trades, st.initial_capital, st.halts)
    bench = _benchmark(md, dates[0], dates[-1])
    checks = report.pass_checks(stats)
    rejects = Counter(_reason_key(m) for m in events_df.loc[events_df["kind"] == "reject", "message"])

    out = config.RUNS_DIR / "backtest" / f"{market}_{dates[0].date()}_{dates[-1].date()}_{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    equity.to_csv(out / "equity.csv")
    trades.to_csv(out / "trades.csv", index=False)
    events_df.to_csv(out / "events.csv", index=False)
    (out / "stats.json").write_text(json.dumps(
        {"market": market, "stats": stats, "benchmark": bench,
         "checks": [{"text": t, "ok": bool(ok)} for t, ok in checks]},
        default=lambda x: None, indent=1).replace("NaN", "null").replace("Infinity", "null"), encoding="utf-8")

    lines = report.summary_lines(f"Backtest {market.upper()} (virtual money, real past prices)",
                                 cfg["currency"], stats, bench)
    lines.append(f"Open at end     {len(st.positions)} positions, valued at the last close")
    lines += ["", "Pass marks from BOT_INSTRUCTIONS.md:"]
    lines += [f"  [{'PASS' if ok else 'FAIL'}] {text}" for text, ok in checks]
    if md.skipped:
        lines += ["", f"Skipped {len(md.skipped)} symbols with no data: {', '.join(md.skipped)}"]
    (out / "report.html").write_text(_backtest_html(market, cfg, stats, bench, checks, equity, trades,
                                                    rejects, md.skipped), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nSaved: {out}\nOpen {out / 'report.html'} in a browser for the full report.")
    return stats


def _reason_key(msg: str) -> str:
    """Group rejection messages by their reason: numbers become #."""
    return re.sub(r"[-+]?\d[\d,.]*", "#", msg)


def _backtest_html(market, cfg, s, bench, checks, equity, trades, rejects, skipped) -> str:
    cur = cfg["currency"]
    good = lambda ok: "good" if ok else "bad"
    body = [f"<h1>Backtest: {market.upper()}</h1>",
            f"<p class='sub'>{s.get('start')} to {s.get('end')}. Virtual money on real past prices, "
            f"with costs and next-day fills.</p>",
            report.kpis([
                ("Total return", report._pct(s.get("total_return")), good(s.get("total_return", 0) > 0)),
                ("Yearly return", report._pct(s.get("cagr")), ""),
                ("Max drawdown", report._pct(s.get("max_drawdown")), good(checks[0][1])),
                ("Profit factor", report._num(s.get("profit_factor")), good(checks[1][1])),
                ("Trades", str(s.get("trades")), ""),
                ("Win rate", report._pct(s.get("win_rate")), ""),
                ("Average trade", f"{report._num(s.get('avg_r'))}R", ""),
                ("Index buy & hold", report._pct(bench), ""),
            ]),
            "<h2>Pass marks</h2><div class='card'>" + "".join(
                f"<div class='{good(ok)}'>{'PASS' if ok else 'FAIL'}: {html.escape(t)}</div>" for t, ok in checks)
            + f"<div class='sub'>Drawdown halts: {s.get('halts')}. In a backtest the bot resumes "
              f"{cfg['risk']['backtest_halt_resume_days']} sessions after a halt. Live, a halt needs a manual reset.</div></div>",
            "<h2>Equity</h2>", report.equity_svg(equity["equity"], f"Equity in {cur}")]
    if len(trades):
        t = trades.tail(300).iloc[::-1]
        body += ["<h2>Trades (latest 300)</h2>", report.table(
            t.to_dict("records"),
            [("symbol", "Symbol"), ("entry_date", "In"), ("exit_date", "Out"), ("entry_price", "Entry"),
             ("exit_price", "Exit"), ("pnl", "P&L"), ("r_multiple", "R"), ("exit_reason", "Exit reason"),
             ("entry_reason", "Why it entered")],
            numeric={"entry_price", "exit_price", "pnl", "r_multiple"})]
    if rejects:
        body += ["<h2>Signals the risk gate refused</h2>", report.table(
            [{"reason": k, "count": v} for k, v in rejects.most_common()],
            [("reason", "Reason"), ("count", "Times")], numeric={"count"})]
    if skipped:
        body.append(f"<p class='sub'>Skipped symbols with no data: {html.escape(', '.join(skipped))}</p>")
    return report.page(f"Backtest {market}", "".join(body))


# ---------------------------------------------------------------- daily simulation

RUN_LOCK_STALE = 3 * 3600                  # seconds: a run lock older than this was left by a crashed run


@contextlib.contextmanager
def run_lock(folder):
    """One run per profile at a time, also between the website and the command line (Task Scheduler)."""
    path = folder / ".run.lock"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - path.stat().st_mtime > RUN_LOCK_STALE:
            path.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("Another run is already working on this profile (maybe the scheduled one). Try again in a minute.")
    try:
        os.write(fd, f"{os.getpid()} {datetime.now():%Y-%m-%d %H:%M:%S}".encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def test_gate(market: str) -> str | None:
    """The command-line (and Task Scheduler) gate: like the website, run the past-years test first when it is
    missing, the settings changed or it is out of date; then say why new buys are blocked, or None."""
    from . import study
    cfg = load_market(market)
    marks = cfg.get("pass") or {}
    state = stages.passport_state(stages.rejudge(stages.load_passport(market), marks), stages.fingerprint(cfg),
                                  max_days=int(marks.get("max_age_days", stages.PASSPORT_MAX_DAYS)))
    if state in ("missing", "changed", "old"):
        print(f"{market.upper()}: past-years test {state}: testing first (7 tests, a minute or two)...")
        try:
            study.passport(market)
        except Exception as exc:                       # no test = no new buys; holdings are still managed
            log.exception("Past-years test failed")
            print(f"{market.upper()}: past-years test failed: {exc}")
    block = stages.validation_block(cfg, market)
    print(f"{market.upper()}: " + (block or "past-years test passed: new buys allowed (each still checked by the risk rules)"))
    return block


def daily(markets: list[str], offline: bool = False, telegram: bool = True, progress=None, gate=None) -> None:
    """Bring every market up to its latest finished session. One cycle, in this order:
    load the account → (download prices only when a new session is waiting) → market check and share search →
    past-years test gate → then the engine: sells and stops first, then the market level, then ranked buys
    through the risk gate → save. progress(market, stage) hears which website stage starts.
    gate(market) returns why new buys are not allowed (None = allowed). It may wait for a past-years test;
    the default only reads the saved test. What each stage found is saved in last_run_<market>.json."""
    folder, runs_dir = config.STATE_DIR, config.RUNS_DIR      # fixed for the whole run, even if the time machine is left meanwhile
    store = Store()
    stop_file = STOP_FILE.exists()
    sections, text = [], []
    with run_lock(folder):
        for m in markets:
            _daily_market(m, store, folder, offline, progress, gate, sections, text)
    _daily_report(runs_dir, stop_file, sections, text, telegram)


def _daily_market(m, store, folder, offline, progress, gate, sections, text) -> None:
    facts = stages.load_facts(m, folder)
    facts.pop("problem", None)

    def mark(stage: int, **found) -> None:
        facts.update(found)
        if found:
            stages.save_facts(m, facts, folder)
        if progress:
            progress(m, stage)

    cfg = load_market(m)
    mark(1)
    st = store.load_state(m)
    mark(2, account=stages.account_facts(cfg, st))
    expected, next_ready = data.session_clock(cfg)
    current = bool(st and st.last_date and pd.Timestamp(st.last_date) >= expected)
    anchor = pd.Timestamp(st.last_date) if st else clock.now().normalize()
    md, splits = load_data(cfg, anchor - pd.Timedelta(days=5), offline or current)   # nothing new: no download
    cal = md.calendar()
    if not cal:
        text.append(f"{m.upper()}: no index data, skipped.")
        mark(2, problem=f"No price data for the {m.upper()} market index, so nothing could be checked.")
        return
    mark(3)                                            # both sides start: whole market and my shares, side by side
    with ThreadPoolExecutor(max_workers=2) as pool:     # read-only work only: no account change here
        side_a = pool.submit(stages.look, cfg, md, cal[-1])
        side_b = pool.submit(stages.review_held, cfg, md, cal[-1], st)
        facts.update(look=side_a.result(), held=side_b.result())
    stages.save_facts(m, facts, folder)
    lk = facts["look"]
    if not current and lk.get("missing", 0) > lk.get("scanned", 0):   # most shares have no price for this day
        text.append(f"{m.upper()}: prices missing for {cal[-1].date()}, nothing traded.")
        mark(2, problem=f"Prices for {cal[-1].date()} are missing for {lk['missing']} of {lk['missing'] + lk['scanned']} "
                        "shares (the free price source left that day out). Nothing was traded and the day stays open: "
                        "press Run bot again later.")
        return
    no_buys = None
    if not current:                               # a new session will be traded: is the strategy validated?
        mark(4)
        no_buys = gate(m) if gate else stages.validation_block(cfg, m)
        facts["validation"] = {"ok": no_buys is None, "why": no_buys}
        stages.save_facts(m, facts, folder)
        st = store.load_state(m)                  # read again: the live watch may have sold while the gate waited
    if current:
        when = next_ready.tz_convert("Asia/Kolkata").strftime("%a %d %b, %H:%M IST")
        note = f"Up to date. The next session is counted after {when}."
        sec, lines = _market_section(m, cfg, st, store, [], [note])
        sections.append(sec)
        text += lines + [""]
        mark(5, carried=True, **({} if facts.get("decision") else {"decision": stages.queue_facts(st)}))
        return
    eng = Engine(cfg, md, load_events()[m], mode="sim", stop_file=stopped(m), no_buys=no_buys)
    notes = []
    if st is None:
        st = eng.new_state(cal[-1])
        dates = [cal[-1]]
        notes.append(f"First run. Virtual account opened with {st.initial_capital:,.0f} {cfg['currency']}.")
    else:
        notes += apply_splits(st, splits)
        dates = md.calendar(after=st.last_date)
    results = eng.run(st, dates)
    if results:
        for n in notes:
            results[0].add("note", "", n)
        mark(5, **stages.run_facts(results), carried=False, processed_at=clock.now().strftime("%Y-%m-%d %H:%M"))
        store.save_run(m, st, results)
    else:
        mark(5, carried=True)
        for n in notes:
            store.save_state(m, st, n)
        notes.append(f"No new completed session since {st.last_date}.")
    sec, lines = _market_section(m, cfg, st, store, results, notes)
    sections.append(sec)
    text += lines + [""]


def _daily_report(runs_dir, stop_file, sections, text, telegram) -> None:
    stamp = datetime.now()
    out_dir = runs_dir / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    head = (f"<h1>Daily simulation</h1><p class='sub'>Real prices, virtual money. Run at "
            f"{stamp:%Y-%m-%d %H:%M}.{' STOP file present: no new entries.' if stop_file else ''}</p>")
    page = report.page("Daily simulation", head + "".join(sections))
    (out_dir / f"report_{stamp:%Y-%m-%d}.html").write_text(page, encoding="utf-8")
    (out_dir / "latest.html").write_text(page, encoding="utf-8")
    print("\n".join(text))
    print(f"Report: {out_dir / 'latest.html'}")
    if telegram:
        print(report.send_telegram("\n".join(text)))


def _status(st) -> str:
    if st.halted:
        return "HALTED"
    if st.pause_sessions_left > 0:
        return "Paused"
    return "Trading"


def _market_section(m, cfg, st, store, results, notes) -> tuple[str, list[str]]:
    cur = cfg["currency"]
    eq = st.equity()
    ret = eq / st.capital_in() - 1
    last = results[-1] if results else None
    if last:
        regime = last.regime
    else:
        eq_hist = store.equity(m)
        regime = eq_hist["regime"].iloc[-1] if len(eq_hist) else ""
    lines = [f"{m.upper()} {st.last_date}: equity {eq:,.0f} {cur} ({ret:+.2%} since {st.start_date}), "
             f"drawdown {st.drawdown:.2%}, {len(st.positions)} open, {_status(st)}",
             f"  Market: {regime}" if regime else "  Market: n/a"]
    for r in results:
        for kind, sym, msg in r.events:
            if kind in ("fill", "exit", "order", "risk", "blocked", "cancel"):
                lines.append(f"  {r.date.date()} {sym + ': ' if sym and not msg.startswith(sym) else ''}{msg}"[:220])
    lines += [f"  {n}" for n in notes]

    pos_rows = [{"symbol": p.symbol, "qty": p.qty, "entry_date": p.entry_date, "entry": p.entry_price,
                 "last": p.last_close, "stop": p.stop, "target": p.target if p.target else float("nan"),
                 "pnl": (p.last_close - p.entry_price) * p.qty - p.entry_costs,
                 "r_now": (p.last_close - p.entry_price) / p.risk_per_share if p.risk_per_share else 0.0,
                 "reason": p.reason} for p in st.positions.values()]
    ord_rows = [{"side": o.side, "symbol": o.symbol, "qty": o.qty, "reason": o.reason} for o in st.orders]
    ev_rows = [{"date": str(r.date.date()), "kind": k, "symbol": s, "message": msg}
               for r in results for k, s, msg in r.events]
    trades = store.trades(m).tail(20).iloc[::-1]
    equity = store.equity(m)
    status_cls = "bad" if st.halted else ""
    body = [f"<h2>{m.upper()} <span class='pill'>{_status(st)}</span></h2>",
            f"<p class='sub'>Last session {st.last_date}. {html.escape(regime)}</p>",
            report.kpis([
                ("Equity", f"{eq:,.0f} {cur}", ""),
                ("Since start", f"{ret:+.2%}", "good" if ret >= 0 else "bad"),
                ("Today", f"{st.day_return:+.2%}", "good" if st.day_return >= 0 else "bad"),
                ("Drawdown", f"{st.drawdown:.2%}", status_cls),
                ("Cash", f"{st.cash:,.0f}", ""),
                ("Open positions", str(len(st.positions)), ""),
            ])]
    if st.halted:
        body.append(f"<div class='card bad'>HALTED: {html.escape(st.halt_reason)}. "
                    f"Review, then run: python run.py reset-halt {m}</div>")
    body += ["<h3>Open positions</h3>", report.table(pos_rows, [
        ("symbol", "Symbol"), ("qty", "Qty"), ("entry_date", "Since"), ("entry", "Entry"), ("last", "Last"),
        ("stop", "Stop"), ("target", "Target"), ("pnl", "P&L"), ("r_now", "R now"), ("reason", "Why")],
        numeric={"qty", "entry", "last", "stop", "target", "pnl", "r_now"})]
    body += ["<h3>Orders for the next open</h3>", report.table(ord_rows, [
        ("side", "Side"), ("symbol", "Symbol"), ("qty", "Qty"), ("reason", "Why")], numeric={"qty"})]
    body += ["<h3>What happened this run</h3>", report.table(ev_rows, [
        ("date", "Date"), ("kind", "Type"), ("symbol", "Symbol"), ("message", "Details")])]
    if notes:
        body.append("<div class='card'>" + "<br>".join(html.escape(n) for n in notes) + "</div>")
    body += ["<h3>Closed trades (latest 20)</h3>", report.table(trades.to_dict("records"), [
        ("symbol", "Symbol"), ("entry_date", "In"), ("exit_date", "Out"), ("pnl", "P&L"),
        ("r_multiple", "R"), ("exit_reason", "Exit reason")], numeric={"pnl", "r_multiple"})]
    if len(equity) > 1:
        body.append(report.equity_svg(equity["equity"], f"Equity in {cur}"))
    return "".join(body), lines


# ---------------------------------------------------------------- small commands

def status() -> None:
    store = Store()
    for m in ("india", "us"):
        st = store.load_state(m)
        if st is None:
            print(f"{m.upper()}: not started. Run: python run.py daily")
            continue
        cur = load_market(m)["currency"]
        eq = st.equity()
        print(f"{m.upper()}: {_status(st)}. Last session {st.last_date}. Equity {eq:,.0f} {cur} "
              f"({eq / st.capital_in() - 1:+.2%} since {st.start_date}). Drawdown {st.drawdown:.2%}.")
        for p in st.positions.values():
            print(f"  {p.symbol}: {p.qty:g} @ {p.entry_price:.2f}, last {p.last_close:.2f}, stop {p.stop:.2f}")
        for o in st.orders:
            print(f"  next open: {o.side} {o.qty:g} {o.symbol}")
        if st.halted:
            print(f"  HALTED: {st.halt_reason}")
    if STOP_FILE.exists():
        print("STOP file present: no new entries until it is deleted.")


def reset_halt(market: str) -> None:
    store = Store()
    st = store.load_state(market)
    if st is None or not st.halted:
        print(f"{market.upper()}: not halted, nothing to do.")
        return
    st.halted = False
    st.halt_reason = ""
    st.peak_equity = st.prev_equity
    store.save_state(market, st, "Halt reset by hand. Drawdown peak reset to current equity.")
    print(f"{market.upper()}: halt cleared. The drawdown limit now counts from {st.prev_equity:,.0f}.")


def check(market: str, offline: bool = False) -> bool:
    """Replay the simulation period as a backtest. Same code, same prices: entries must match."""
    store = Store()
    st = store.load_state(market)
    if st is None or not st.last_date:
        print(f"{market.upper()}: no simulation yet.")
        return True
    cfg = load_market(market)
    md, _ = load_data(cfg, st.start_date, offline)
    dates = md.calendar(st.start_date, st.last_date)
    eng = Engine(cfg, md, load_events()[market], mode="sim")
    replay = eng.new_state(dates[0])
    results = eng.run(replay, dates)
    _, bt_trades, _ = results_to_frames(results)
    sim_trades = store.trades(market)
    sim = {(r.symbol, r.entry_date) for r in sim_trades.itertuples()} | \
          {(p.symbol, p.entry_date) for p in st.positions.values()}
    bt = {(r.symbol, r.entry_date) for r in bt_trades.itertuples()} | \
         {(p.symbol, p.entry_date) for p in replay.positions.values()}
    both, only_sim, only_bt = sim & bt, sim - bt, bt - sim
    print(f"{market.upper()} {st.start_date} to {st.last_date}: {len(both)} entries match, "
          f"{len(only_sim)} only in the simulation, {len(only_bt)} only in the replay.")
    for s, d in sorted(only_sim):
        print(f"  only simulation: {s} {d}")
    for s, d in sorted(only_bt):
        print(f"  only replay:     {s} {d}")
    if only_sim or only_bt:
        print("Differences usually mean the data provider revised past prices, or settings changed.")
    return not (only_sim or only_bt)
