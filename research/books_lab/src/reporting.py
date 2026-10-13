"""Reporting. Tables, because the user asked for tables."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd


def fmt_money(x: float, currency: str = "INR") -> str:
    sym = "₹" if currency == "INR" else "$"
    return f"{sym}{x:,.0f}"


def render_report(market: str, result, stats: dict, currency: str) -> str:
    lines: list[str] = []
    add = lines.append

    cap0 = stats["starting_capital"]
    cap1 = stats["ending_equity"]
    ret = (cap1 / cap0 - 1.0) * 100.0 if cap0 else 0.0

    add("=" * 74)
    add(f"  PAPER TRADING RESULT  -  {market.upper()}")
    add(f"  {datetime.now():%Y-%m-%d %H:%M}")
    add("=" * 74)
    add("")
    add("  MONEY")
    add(f"    Starting capital (dummy)      {fmt_money(cap0, currency):>16}")
    add(f"    Ending equity                 {fmt_money(cap1, currency):>16}")
    add(f"    Net P&L                       {fmt_money(stats['net_pnl'], currency):>16}")
    add(f"    Return                        {ret:>15.2f}%")
    add("")

    add("  PERFORMANCE")
    add(f"    Trades                        {stats['n_trades']:>16}")
    add(f"    Win rate                      {stats['win_rate']:>15.1f}%")
    add(f"    Profit factor                 {stats['profit_factor']:>16.2f}")
    add(f"    Expectancy                    {stats['expectancy_r']:>15.2f}R")
    add(f"    Max drawdown                  {stats['max_drawdown_pct']:>15.2f}%")
    add(f"    Avg win                       {fmt_money(stats['avg_win'], currency):>16}")
    add(f"    Avg loss                      {fmt_money(stats['avg_loss'], currency):>16}")
    add(f"    Worst loss streak             {stats['max_consecutive_losses_seen']:>16}")
    add("")

    add("  COSTS (this is where paper trading lies to you)")
    add(f"    Total costs paid              {fmt_money(stats['total_costs'], currency):>16}")
    add(f"    Costs as % of capital         {stats['costs_as_pct_of_capital']:>15.2f}%")
    add(f"    Costs as % of gross profit    "
        f"{(stats['total_costs'] / stats['gross_profit'] * 100.0 if stats['gross_profit'] else 0):>15.1f}%")
    add("")

    add("  EXIT REASONS")
    for reason, count in sorted(stats["exit_reasons"].items(), key=lambda kv: -kv[1]):
        add(f"    {reason:<30} {count:>8}")
    add("")

    # --- equity curve stats ------------------------------------------
    ec = result.equity_curve
    if not ec.empty:
        days = len(ec)
        add("  CURVE")
        add(f"    Trading days simulated        {days:>16}")
        peak = ec["equity"].max()
        trough = ec["equity"].min()
        add(f"    Peak equity                   {fmt_money(peak, currency):>16}")
        add(f"    Trough equity                 {fmt_money(trough, currency):>16}")
        add("")

    # --- top winners / losers ----------------------------------------
    trades = sorted(result.trades, key=lambda t: t.pnl, reverse=True)
    if trades:
        add("  BEST 5 TRADES")
        for t in trades[:5]:
            add(f"    {t.symbol:<12} {t.exit_date:%Y-%m-%d} "
                f"{fmt_money(t.pnl, currency):>12}  {t.r_multiple:+.2f}R  {t.exit_reason}")
        add("")
        add("  WORST 5 TRADES")
        for t in trades[-5:]:
            add(f"    {t.symbol:<12} {t.exit_date:%Y-%m-%d} "
                f"{fmt_money(t.pnl, currency):>12}  {t.r_multiple:+.2f}R  {t.exit_reason}")
        add("")

    # --- risk gate activity ------------------------------------------
    rej = result.rejections
    if rej:
        add("  RISK GATE REJECTIONS (why the bot said no)")
        counts: dict = {}
        for r in rej:
            counts[r.failed_rule] = counts.get(r.failed_rule, 0) + 1
        for rule, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            add(f"    {rule:<34} {count:>8}")
        add(f"    {'TOTAL blocks':<34} {len(rej):>8}")
        add("")

    add("  SANITY CHECKS")
    add(f"    Signals generated             {len(result.signals):>16}")
    add(f"    Positions still open at end   {len(result.portfolio.positions):>16}")
    add(f"    Bars blocked by rules         {len(rej):>16}")
    add("")
    add("=" * 74)
    return "\n".join(lines)


def trades_to_csv(result) -> pd.DataFrame:
    rows = []
    for t in result.trades:
        rows.append(
            {
                "symbol": t.symbol,
                "market": t.market,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "quantity": t.quantity,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": t.pnl,
                "r_multiple": t.r_multiple,
                "exit_reason": t.exit_reason,
                "costs": t.costs,
                "reason": t.reason.get("signal_reason", ""),
            }
        )
    return pd.DataFrame(rows)


def save_report(text: str, out_dir: Path, market: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"report_{market}_{datetime.now():%Y%m%d_%H%M%S}.txt"
    path.write_text(text, encoding="utf-8")
    return path
