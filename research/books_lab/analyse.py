#!/usr/bin/env python3
"""
PARAMETER SENSITIVITY / WALK-FORWARD ANALYSIS

The point of this script is not to find the best parameters. It is to find
out whether the result depends on them.

From the architecture report:

  overfitting -> fixed structure + walk-forward + parameter-sensitivity gate
  (perturb every threshold +/-20%, no single parameter may drive results)

If the strategy only makes money at one exact setting of one knob, it is
curve-fit and it will not survive contact with the market. If it makes money
across a neighbourhood of settings, there is probably something real there.

Usage:
  python3 analyse.py india           # sensitivity sweep
  python3 analyse.py us --walkforward
"""
from __future__ import annotations

import argparse
import copy
import itertools
import sys
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT_ROOT))

import pandas as pd  # noqa: E402

from run import load_market  # noqa: E402
from src.config import Config  # noqa: E402
from src.engine import Engine  # noqa: E402
from src.storage import compute_stats  # noqa: E402

# The parameters that are allowed to move, and how much.
SWEEP = {
    "strategy.volume.multiplier": [1.2, 1.5, 1.8],
    "strategy.momentum.rsi_min": [45, 50, 55],
    "strategy.momentum.rsi_max": [70, 75, 80],
    "strategy.stop.atr_multiplier": [1.5, 2.0, 2.5],
    "strategy.trend.ma_fast": [20, 50],
    "strategy.max_hold_bars": [10, 20, 30],
    "risk.risk_per_trade_pct": [0.4, 0.5, 0.6],
}


def build_engine(cfg: Config, market: str, bars, index_bars, fear_bars,
                 trade_etfs: bool = True, start=None, end=None):
    capital = float(cfg.get(f"starting_capital.{market}", 0))
    cost_pct = float(cfg.get(f"costs.{market}.round_trip_pct", 0.29))
    return Engine(
        market=market,
        currency=cfg.market_cfg(market)["currency"],
        bars=bars,
        index_bars=index_bars,
        fear_bars=fear_bars,
        starting_capital=capital,
        cfg={
            "risk": cfg.risk,
            "strategy": cfg.strategy,
            "_etfs": cfg.etfs(market),
        },
        cost_pct=cost_pct,
        warmup_bars=210,
        trade_etfs=trade_etfs,
        verbose=False,
        start_date=start,
        end_date=end,
    )


def run_one(cfg: Config, market: str, data, start=None, end=None) -> dict:
    bars, index_bars, fear_bars = data
    engine = build_engine(cfg, market, bars, index_bars, fear_bars,
                          start=start, end=end)
    result = engine.run()
    stats = compute_stats(result.portfolio, result.trades,
                          result.equity_curve, result.portfolio.starting_capital)
    return stats


def set_dotted(cfg_dict: dict, dotted: str, value) -> dict:
    out = copy.deepcopy(cfg_dict)
    node = out
    parts = dotted.split(".")
    for p in parts[:-1]:
        node = node[p]
    node[parts[-1]] = value
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("market", nargs="?", default="india", choices=["india", "us"])
    ap.add_argument("--walkforward", action="store_true")
    args = ap.parse_args()

    cfg = Config()
    market = args.market
    print(f"\nLoading {market.upper()} data for sensitivity analysis ...")
    data = load_market(cfg, market, use_cache=True)

    base_cfg = {
        "risk": cfg.risk,
        "strategy": cfg.strategy,
        "_etfs": cfg.etfs(market),
    }

    print("\nRunning baseline ...")
    base = run_one(cfg, market, data)
    base_line = (f"  BASELINE  trades={base['n_trades']:>4}  "
                 f"win={base['win_rate']:>5.1f}%  PF={base['profit_factor']:>5.2f}  "
                 f"net={base['net_pnl']:>12,.0f}  maxDD={base['max_drawdown_pct']:>6.2f}%")
    print(base_line)

    # ------------------------------------------------------------------
    #  1. SENSITIVITY SWEEP
    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("  PARAMETER SENSITIVITY - does the answer survive a 20% nudge?")
    print("=" * 78)

    rows = []
    for dotted, values in SWEEP.items():
        print(f"\n  {dotted}")
        for v in values:
            cfg._raw = set_dotted(cfg._raw, dotted, v)
            st = run_one(cfg, market, data)
            rows.append({"param": dotted, "value": v, **{
                k: st[k] for k in ("n_trades", "win_rate", "profit_factor",
                                   "net_pnl", "max_drawdown_pct", "expectancy_r")
            }})
            flag = ""
            if st["profit_factor"] >= 1.2 and base["profit_factor"] < 1.2:
                flag = "  <- better than baseline"
            print(f"    {str(v):>6}   trades={st['n_trades']:>4}  "
                  f"win={st['win_rate']:>5.1f}%  PF={st['profit_factor']:>5.2f}  "
                  f"net={st['net_pnl']:>12,.0f}  maxDD={st['max_drawdown_pct']:>6.2f}%{flag}")
        cfg._raw["risk"] = base_cfg["risk"]
        cfg._raw["strategy"] = base_cfg["strategy"]

    df = pd.DataFrame(rows)
    print("\n" + "-" * 78)
    print("  SPREAD ACROSS ALL SETTINGS")
    print("-" * 78)
    print(f"    net P&L        min {df['net_pnl'].min():>14,.0f}   "
          f"max {df['net_pnl'].max():>14,.0f}")
    print(f"    profit factor  min {df['profit_factor'].min():>14.2f}   "
          f"max {df['profit_factor'].max():>14.2f}")
    print(f"    win rate       min {df['win_rate'].min():>14.1f}%  "
          f"max {df['win_rate'].max():>14.1f}%")
    print(f"    trade count    min {df['n_trades'].min():>14}   "
          f"max {df['n_trades'].max():>14}")

    positive = int((df["net_pnl"] > 0).sum())
    print(f"\n    settings with positive net P&L: {positive}/{len(df)} "
          f"({positive/len(df)*100:.0f}%)")
    pf_ok = int((df["profit_factor"] >= 1.0).sum())
    print(f"    settings with profit factor >= 1.0: {pf_ok}/{len(df)} "
          f"({pf_ok/len(df)*100:.0f}%)")

    # ------------------------------------------------------------------
    #  2. WALK-FORWARD
    # ------------------------------------------------------------------
    if args.walkforward:
        print("\n" + "=" * 78)
        print("  WALK-FORWARD - split the history, do not look at the future")
        print("=" * 78)
        bars = data[0]
        all_dates = sorted({d for df in bars.values() for d in df.index})
        n = len(all_dates)
        # two-thirds in-sample, one-third out-of-sample
        cut = int(n * 2 / 3)
        cut_date = all_dates[cut]
        print(f"\n  in-sample  : {all_dates[0].date()} -> {cut_date.date()} "
              f"({cut} bars)")
        print(f"  out-sample : {all_dates[cut].date()} -> {all_dates[-1].date()} "
              f"({n - cut} bars)")

        for label, lo, hi in [
            ("IN-SAMPLE ", all_dates[0], cut_date),
            ("OUT-SAMPLE", cut_date, all_dates[-1]),
        ]:
            # Keep the FULL history for indicator warmup, but only allow the
            # engine to trade inside the window. Slicing the bars instead would
            # leave 184 bars, which is fewer than the 210-bar warmup, and the
            # out-of-sample leg would silently report zero trades.
            st = run_one(cfg, market, data, start=lo, end=hi)
            print(f"\n  {label} trades={st['n_trades']:>4}  "
                  f"win={st['win_rate']:>5.1f}%  PF={st['profit_factor']:>5.2f}  "
                  f"net={st['net_pnl']:>12,.0f}  maxDD={st['max_drawdown_pct']:>6.2f}%")

    print("\n" + "=" * 78)
    print("  VERDICT")
    print("=" * 78)
    if positive / len(df) >= 0.7 and pf_ok / len(df) >= 0.7:
        print("  Robust: the strategy survives a wide range of settings.")
    elif positive / len(df) >= 0.4:
        print("  Mixed: profitable in a minority of settings. Treat any single")
        print("  run as noise, not as an edge.")
    else:
        print("  Fragile: the result is driven by the exact parameter values.")
        print("  This is curve-fitting. Do not trade it.")
    print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
