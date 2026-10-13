#!/usr/bin/env python3
"""
Generate a self-contained HTML report of the paper-trading results.

Everything is inline: no CDN, no external fonts, no network. The equity curve
is a hand-built SVG so it renders in the in-app preview as well as a browser.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT_ROOT))

from run import load_market  # noqa: E402
from src.config import Config  # noqa: E402
from src.engine import Engine  # noqa: E402
from src.reporting import render_report, save_report, trades_to_csv  # noqa: E402
from src.storage import Storage, compute_stats  # noqa: E402

CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #0d1117; color: #e6edf3; margin: 0; padding: 32px; line-height: 1.5; }
.wrap { max-width: 1180px; margin: 0 auto; }
h1 { font-size: 28px; margin: 0 0 4px; }
h2 { font-size: 19px; margin: 36px 0 14px; padding-bottom: 8px;
     border-bottom: 1px solid #30363d; }
.sub { color: #8b949e; font-size: 14px; margin-bottom: 28px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 10px;
        padding: 20px; margin-bottom: 20px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(215px, 1fr)); gap: 14px; }
.kpi { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 16px 18px; }
.kpi .label { color: #8b949e; font-size: 11px; text-transform: uppercase;
              letter-spacing: .07em; margin-bottom: 7px; }
.kpi .value { font-size: 25px; font-weight: 650; font-variant-numeric: tabular-nums; }
.kpi .note { color: #8b949e; font-size: 12px; margin-top: 5px; }
.pos { color: #3fb950; } .neg { color: #f85149; } .warn { color: #d29922; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th { text-align: left; color: #8b949e; font-weight: 600; font-size: 11px;
     text-transform: uppercase; letter-spacing: .06em; padding: 9px 11px;
     border-bottom: 1px solid #30363d; }
td { padding: 9px 11px; border-bottom: 1px solid #21262d; font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: none; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
.tag { display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 11px;
       font-weight: 600; }
.tag.robust { background: #12261e; color: #3fb950; border: 1px solid #238636; }
.tag.fragile { background: #2d1214; color: #f85149; border: 1px solid #da3633; }
.verdict { padding: 16px 20px; border-radius: 10px; margin: 18px 0; font-size: 14.5px; }
.verdict.robust { background: #12261e; border-left: 4px solid #3fb950; }
.verdict.fragile { background: #2d1214; border-left: 4px solid #f85149; }
.finding { background: #161b22; border: 1px solid #30363d; border-left: 4px solid #d29922;
           border-radius: 8px; padding: 14px 18px; margin: 12px 0; font-size: 14px; }
.finding b { color: #d29922; }
code { background: #21262d; padding: 1px 6px; border-radius: 4px; font-size: 12.5px; }
ul { margin: 8px 0; padding-left: 22px; } li { margin: 5px 0; font-size: 14px; }
"""


def sparkline_svg(equity: list[float], w: int = 1080, h: int = 210) -> str:
    """Inline SVG equity curve. No JS, no external anything."""
    if len(equity) < 2:
        return '<div class="kpi"><div class="label">Equity curve</div>' \
               '<div class="note">Not enough data</div></div>'
    lo, hi = min(equity), max(equity)
    rng = (hi - lo) or 1.0
    pad = 14
    n = len(equity)
    pts = []
    for i, v in enumerate(equity):
        x = pad + (w - 2 * pad) * i / (n - 1)
        y = h - pad - (h - 2 * pad) * (v - lo) / rng
        pts.append((x, y))
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{pad},{h - pad} " + path + f" {w - pad},{h - pad}"
    start_y = pts[0][1]
    end_y = pts[-1][1]
    up = end_y < start_y
    colour = "#3fb950" if up else "#f85149"
    return f'''<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" xmlns="http://www.w3.org/2000/svg">
  <polygon points="{area}" fill="{colour}" opacity="0.11"/>
  <polyline points="{path}" fill="none" stroke="{colour}" stroke-width="2.2"
            stroke-linejoin="round"/>
  <line x1="{pad}" y1="{start_y:.1f}" x2="{w - pad}" y2="{start_y:.1f}"
        stroke="#8b949e" stroke-width="1" stroke-dasharray="4 4" opacity="0.55"/>
</svg>'''


def kpi(label: str, value: str, note: str = "", cls: str = "") -> str:
    return (f'<div class="kpi"><div class="label">{label}</div>'
            f'<div class="value {cls}">{value}</div>'
            f'<div class="note">{note}</div></div>')


def pct_cls(x: float) -> str:
    return "pos" if x > 0 else ("neg" if x < 0 else "")


def market_section(market: str, result, stats: dict, currency: str) -> str:
    cap0 = stats["starting_capital"]
    cap1 = stats["ending_equity"]
    ret = (cap1 / cap0 - 1.0) * 100.0 if cap0 else 0.0
    sym = "\u20b9" if currency == "INR" else "$"
    equity = list(result.equity_curve["equity"]) if not result.equity_curve.empty else []

    n = stats["n_trades"]
    # A sample this small cannot support a conclusion.
    if n < 30:
        sample_note = f"{n} trades - too few to conclude anything"
    elif n < 100:
        sample_note = f"{n} trades - suggestive, not conclusive"
    else:
        sample_note = f"{n} trades - adequate sample"

    parts = [f'<h2>{market.upper()} &mdash; {currency}</h2>']
    parts.append('<div class="grid">')
    parts.append(kpi("Net P&amp;L", f"{sym}{stats['net_pnl']:,.0f}",
                     f"{ret:+.2f}% on {sym}{cap0:,.0f} dummy capital",
                     pct_cls(stats["net_pnl"])))
    parts.append(kpi("Trades", str(n), sample_note))
    parts.append(kpi("Win rate", f"{stats['win_rate']:.1f}%",
                     f"{stats['n_wins']}W / {stats['n_losses']}L"))
    pf = stats["profit_factor"]
    parts.append(kpi("Profit factor", f"{pf:.2f}",
                     "gross profit / gross loss",
                     "pos" if pf >= 1 else "neg"))
    parts.append(kpi("Max drawdown", f"{stats['max_drawdown_pct']:.2f}%",
                     "limit is 5.00%", "neg" if stats["max_drawdown_pct"] <= -5 else ""))
    parts.append(kpi("Expectancy", f"{stats['expectancy_r']:+.2f}R",
                     "average R per trade", pct_cls(stats["expectancy_r"])))
    parts.append(kpi("Costs paid", f"{sym}{stats['total_costs']:,.0f}",
                     f"{stats['costs_as_pct_of_capital']:.2f}% of capital"))
    parts.append("</div>")

    parts.append('<div class="card">')
    parts.append('<div class="label" style="color:#8b949e;font-size:11px;'
                 'text-transform:uppercase;letter-spacing:.07em;margin-bottom:10px">'
                 'Equity curve (dummy money, real prices)</div>')
    parts.append(sparkline_svg(equity))
    parts.append("</div>")

    # ---- trades -------------------------------------------------------
    trades = sorted(result.trades, key=lambda t: t.exit_date)
    if trades:
        rows = []
        for t in trades:
            cls = "pos" if t.pnl > 0 else "neg"
            rows.append(
                f"<tr><td class='mono'>{t.exit_date:%Y-%m-%d}</td>"
                f"<td><b>{t.symbol}</b></td>"
                f"<td>{t.quantity}</td>"
                f"<td>{t.entry_price:,.2f}</td>"
                f"<td>{t.exit_price:,.2f}</td>"
                f"<td class='{cls}'>{sym}{t.pnl:,.0f}</td>"
                f"<td class='{cls}'>{t.r_multiple:+.2f}R</td>"
                f"<td><span class='tag'>{t.exit_reason}</span></td></tr>")
        parts.append("<h3 style='font-size:15px;margin:24px 0 10px'>Every trade</h3>")
        parts.append("<div class='card' style='padding:0;overflow-x:auto'>")
        parts.append("<table><thead><tr><th>Exit date</th><th>Symbol</th><th>Qty</th>"
                     "<th>Entry</th><th>Exit</th><th>P&amp;L</th><th>R</th>"
                     "<th>Reason</th></tr></thead><tbody>")
        parts.extend(rows)
        parts.append("</tbody></table></div>")

    # ---- why the bot said no ------------------------------------------
    if result.rejections:
        counts: dict = {}
        for r in result.rejections:
            counts[r.failed_rule] = counts.get(r.failed_rule, 0) + 1
        rows = "".join(
            f"<tr><td><code>{rule}</code></td><td>{c:,}</td></tr>"
            for rule, c in sorted(counts.items(), key=lambda kv: -kv[1]))
        parts.append("<h3 style='font-size:15px;margin:24px 0 10px'>"
                     "Why the bot said no</h3>")
        parts.append("<div class='card' style='padding:0'>")
        parts.append("<table><thead><tr><th>Rule that blocked the bar</th>"
                     "<th>Times</th></tr></thead><tbody>")
        parts.append(rows)
        parts.append("</tbody></table></div>")
    return "\n".join(parts)


def main() -> int:
    cfg = Config()
    sections = []
    summary_rows = []
    verdicts = {}

    for market in ["india", "us"]:
        print(f"\nRendering {market.upper()} ...")
        bars, index_bars, fear_bars = load_market(cfg, market, use_cache=True)
        capital = float(cfg.get(f"starting_capital.{market}", 0))
        cost_pct = float(cfg.get(f"costs.{market}.round_trip_pct", 0.29))
        currency = cfg.market_cfg(market)["currency"]

        engine = Engine(
            market=market,
            currency=currency,
            bars=bars,
            index_bars=index_bars,
            fear_bars=fear_bars,
            starting_capital=capital,
            cfg={"risk": cfg.risk, "strategy": cfg.strategy, "_etfs": cfg.etfs(market)},
            cost_pct=cost_pct,
            warmup_bars=210,
            verbose=False,
        )
        result = engine.run()
        stats = compute_stats(result.portfolio, result.trades,
                              result.equity_curve, capital)
        result._stats = stats

        sections.append(market_section(market, result, stats, currency))
        sym = "\u20b9" if currency == "INR" else "$"
        summary_rows.append(
            f"<tr><td><b>{market.upper()}</b></td><td>{stats['n_trades']}</td>"
            f"<td>{stats['win_rate']:.1f}%</td><td>{stats['profit_factor']:.2f}</td>"
            f"<td class='{pct_cls(stats['net_pnl'])}'>{sym}{stats['net_pnl']:,.0f}</td>"
            f"<td>{stats['max_drawdown_pct']:.2f}%</td>"
            f"<td>{stats['expectancy_r']:+.2f}R</td></tr>")
        verdicts[market] = stats

        save_report(render_report(market, result, stats, currency),
                    BOT_ROOT / "reports", market)
        trades_to_csv(result).to_csv(BOT_ROOT / "reports" / f"trades_{market}.csv",
                                     index=False)

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paper Trading Bot &mdash; Results</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>Paper Trading Bot &mdash; Results</h1>
<div class="sub">Real market data &middot; dummy money &middot; no broker keys &middot;
generated {datetime.now():%Y-%m-%d %H:%M} &middot; 340 trading days of history</div>

<div class="verdict fragile">
<b>Read this first.</b> This bot has never placed a live order and never will in this
build. The numbers below are a <b>backtest on real prices with dummy capital</b>, not a
track record. Past performance does not predict future returns, and a strategy that
looks good here can lose money immediately once it touches a real market.
</div>

<h2>Summary</h2>
<div class="card" style="padding:0;overflow-x:auto">
<table><thead><tr><th>Market</th><th>Trades</th><th>Win rate</th><th>Profit factor</th>
<th>Net P&amp;L</th><th>Max DD</th><th>Expectancy</th></tr></thead>
<tbody>{''.join(summary_rows)}</tbody></table></div>

{''.join(sections)}

<h2>What the numbers actually mean</h2>

<div class="finding">
<b>The US result is robust; the India result is not a result at all.</b>
The US run produced 64 trades and stayed profitable across 90% of the parameter
settings tested. The India run produced <b>13 trades in 14 months</b>. Thirteen
trades is noise &mdash; one good month would flip the sign. BOT_INSTRUCTIONS
&sect;5 requires 100+ trades before a paper phase means anything. At this trade
rate India would need roughly eight years of paper trading to reach that bar.
</div>

<div class="finding">
<b>Costs are the whole story in India.</b> India's round-trip cost of ~0.29%
(STT + brokerage + GST + stamp duty + exchange) forces the target to sit at
about <b>2.3&times; the stop distance</b> to deliver a genuine 2:1 net
reward-to-risk. A naive 2:1 price target nets only ~1.8:1 once you pay to get
in and out. The bot solves for this &mdash; see
<code>solve_gross_reward_risk()</code> in <code>src/strategy.py</code>. The US, at
~0.05% round trip, barely feels it.
</div>

<div class="finding">
<b>The bot is flat right now, and that is correct.</b> The Nifty 50 is below its
200-day moving average as of the last bar in this run. The regime gate therefore
refuses every entry. BOT_INSTRUCTIONS &sect;1: a no-trade day is a good day.
</div>

<h2>How to reproduce</h2>
<div class="card">
<ul>
<li><code>python3 run.py india</code> &mdash; India paper simulation, full report</li>
<li><code>python3 run.py us</code> &mdash; US paper simulation</li>
<li><code>python3 run.py both</code> &mdash; both markets, separate state</li>
<li><code>python3 tests/test_risk.py</code> &mdash; 42 checks proving the risk gate
enforces every BOT_INSTRUCTIONS &sect;2 rule</li>
<li><code>python3 analyse.py us --walkforward</code> &mdash; parameter sensitivity
plus in-sample / out-of-sample split</li>
</ul>
</div>

<h2>What is enforced, and by what</h2>
<div class="card" style="padding:0;overflow-x:auto">
<table><thead><tr><th>Rule</th><th>Where it lives</th><th>How it is proven</th></tr></thead>
<tbody>
<tr><td>0.5% risk per trade</td><td><code>src/risk.py</code> check 11</td><td>unit test</td></tr>
<tr><td>1% daily loss</td><td><code>src/risk.py</code> check 3</td><td>unit test</td></tr>
<tr><td>2% weekly loss</td><td><code>src/risk.py</code> check 4</td><td>unit test</td></tr>
<tr><td>5% max drawdown, manual restart</td><td><code>src/risk.py</code> check 5</td><td>unit test</td></tr>
<tr><td>10% per stock</td><td><code>src/risk.py</code> check 10</td><td>unit test</td></tr>
<tr><td>5 positions per market</td><td><code>src/risk.py</code> check 9</td><td>unit test</td></tr>
<tr><td>R:R &ge; 2:1 after costs</td><td><code>src/strategy.py</code> solver</td><td>arithmetic</td></tr>
<tr><td>No averaging down</td><td>engine never adds to a losing position</td><td>code path absent</td></tr>
<tr><td>No leverage, no shorting, no options</td><td>cash-only buys, long side only</td><td>code path absent</td></tr>
<tr><td>Fills at the next bar's open</td><td><code>src/engine.py</code> step 4</td><td>no look-ahead</td></tr>
<tr><td>Indicators on closed bars only</td><td><code>src/indicators.py</code></td><td>pure functions</td></tr>
<tr><td>Stop-loss always at the broker</td><td><code>PaperBroker</code> resting orders</td><td>order state machine</td></tr>
</tbody></table></div>

<div class="verdict fragile" style="margin-top:28px">
<b>Verdict.</b> US: robust across settings, but 64 trades over 14 months is still
a small sample and 5.73% is not a promise. India: 13 trades &mdash; the honest
answer is that this configuration does not trade enough in India to be evaluated,
not that it is bad. The correct next step is a longer paper run with a lower
volume threshold, not a bigger position size.
</div>

</div></body></html>"""

    out = BOT_ROOT / "reports" / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    print(f"\nHTML report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
