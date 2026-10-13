#!/usr/bin/env python3
"""
PAPER TRADING BOT - real market data, dummy money, measured profit.

Usage
-----
  python3 run.py india              # backtest + paper sim, India
  python3 run.py us                 # same, United States
  python3 run.py both               # both markets, separately
  python3 run.py india --no-cache   # force fresh download

No broker keys. No live orders. Dummy money only.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

BOT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT_ROOT))

from src.config import Config                      # noqa: E402
from src.data.fetcher import fetch                 # noqa: E402
from src.data.validator import validate            # noqa: E402
from src.engine import Engine                      # noqa: E402
from src.reporting import render_report, save_report, trades_to_csv  # noqa: E402
from src.storage import Storage, compute_stats     # noqa: E402


def load_market(cfg: Config, market: str, use_cache: bool = True) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    mkt = cfg.market_cfg(market)
    suffix = mkt["suffix"]
    lookback = int(cfg.get("data.lookback_days", 800))

    print(f"\n  Fetching {market.upper()} data "
          f"({len(mkt['universe'])} stocks + {len(mkt.get('etfs', []))} ETFs) ...")

    bars: dict = {}
    failures: list[str] = []

    all_symbols = list(mkt["universe"]) + list(mkt.get("etfs", []))
    for i, sym in enumerate(all_symbols, 1):
        yf_sym = f"{sym}{suffix}" if suffix else sym
        df = fetch(yf_sym, lookback_days=lookback, use_cache=use_cache)
        if df.empty:
            failures.append(yf_sym)
            print(f"    [{i:>2}/{len(all_symbols)}] {yf_sym:<18} FAILED")
            continue
        res = validate(df, max_bar_age_hours=int(cfg.get("data.max_bar_age_hours", 72)))
        if not res.ok:
            failures.append(yf_sym)
            print(f"    [{i:>2}/{len(all_symbols)}] {yf_sym:<18} INVALID: {res.problems[0]}")
            continue
        bars[sym] = df
        print(f"    [{i:>2}/{len(all_symbols)}] {yf_sym:<18} "
              f"{len(df)} bars  {df.index[0].date()} -> {df.index[-1].date()}")
        time.sleep(0.05)

    if failures:
        print(f"  ! {len(failures)} symbols unusable: {', '.join(failures[:8])}"
              f"{' ...' if len(failures) > 8 else ''}")

    print(f"  Fetching {mkt['index_symbol']} and {mkt['fear_symbol']} ...")
    index_bars = fetch(mkt["index_symbol"], lookback_days=lookback, use_cache=use_cache)
    fear_bars = fetch(mkt["fear_symbol"], lookback_days=lookback, use_cache=use_cache)

    if index_bars.empty:
        raise SystemExit(f"FATAL: could not load index {mkt['index_symbol']}. "
                         "The regime filter cannot work without it.")
    return bars, index_bars, fear_bars


def run_market(cfg: Config, market: str, use_cache: bool = True,
               trade_etfs: bool = True, verbose: bool = True) -> dict:
    bars, index_bars, fear_bars = load_market(cfg, market, use_cache)
    if not bars:
        raise SystemExit(f"FATAL: no usable data for {market}.")

    currency = cfg.market_cfg(market)["currency"]
    capital = float(cfg.get(f"starting_capital.{market}", 0))
    cost_pct = float(cfg.get(f"costs.{market}.round_trip_pct", 0.29))

    cfg_dict = {
        "risk": cfg.risk,
        "strategy": cfg.strategy,
        "_etfs": cfg.etfs(market),
    }

    print(f"\n  Running paper simulation [{market.upper()}] ...")
    engine = Engine(
        market=market,
        currency=currency,
        bars=bars,
        index_bars=index_bars,
        fear_bars=fear_bars,
        starting_capital=capital,
        cfg=cfg_dict,
        cost_pct=cost_pct,
        warmup_bars=210,
        trade_etfs=trade_etfs,
        verbose=verbose,
    )
    result = engine.run()

    stats = compute_stats(
        result.portfolio, result.trades, result.equity_curve, capital
    )
    result._stats = stats

    text = render_report(market, result, stats, currency)
    print("\n" + text)

    out_dir = BOT_ROOT / "reports"
    save_report(text, out_dir, market)
    csv_path = out_dir / f"trades_{market}.csv"
    trades_to_csv(result).to_csv(csv_path, index=False)

    store = Storage(BOT_ROOT / cfg.get("storage.path", "data/bot.duckdb"))
    run_id = store.write_run(market, result)
    store.close()

    print(f"\n  Report  -> {out_dir}/report_{market}_*.txt")
    print(f"  Trades  -> {csv_path}")
    print(f"  Run id  -> {run_id}")
    return {"market": market, "stats": stats, "result": result}


def main() -> int:
    ap = argparse.ArgumentParser(description="Paper trading bot")
    ap.add_argument("market", nargs="?", default="india",
                    choices=["india", "us", "both"])
    ap.add_argument("--no-cache", action="store_true",
                    help="force a fresh download instead of using the local cache")
    ap.add_argument("--no-etfs", action="store_true",
                    help="trade only stocks, not index ETFs")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress per-trade output")
    args = ap.parse_args()

    cfg = Config()

    print("=" * 74)
    print("  PAPER TRADING BOT  -  real data, dummy money, measured profit")
    print("  Mode: paper. No broker keys. No live orders.")
    print("=" * 74)

    markets = ["india", "us"] if args.market == "both" else [args.market]
    summary = []

    for mkt in markets:
        try:
            summary.append(run_market(
                cfg, mkt,
                use_cache=not args.no_cache,
                trade_etfs=not args.no_etfs,
                verbose=not args.quiet,
            ))
        except SystemExit as exc:
            print(f"\n  {mkt.upper()} FAILED: {exc}")

    if len(summary) > 1:
        print("\n" + "=" * 74)
        print("  BOTH MARKETS")
        print("=" * 74)
        print(f"  {'market':<10}{'trades':>8}{'win%':>8}{'PF':>7}"
              f"{'net P&L':>16}{'max DD%':>10}")
        for s in summary:
            st = s["stats"]
            cur = "INR" if s["market"] == "india" else "USD"
            sym = "\u20b9" if cur == "INR" else "$"
            print(f"  {s['market']:<10}{st['n_trades']:>8}{st['win_rate']:>7.1f}%"
                  f"{st['profit_factor']:>7.2f}{sym}{st['net_pnl']:>15,.0f}"
                  f"{st['max_drawdown_pct']:>9.2f}%")
        print("")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
