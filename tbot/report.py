"""Numbers, terminal text, HTML report and an optional Telegram message."""
from __future__ import annotations

import html
import math
import os

import pandas as pd

# Pass marks from BOT_INSTRUCTIONS.md, phase 1 (backtest).
PASS_MAX_DD = 0.10
PASS_PF = 1.3
PASS_MIN_TRADES = 30        # fewer trades say nothing about a strategy


def summarize(equity: pd.Series, trades: pd.DataFrame, initial: float, halts: int = 0) -> dict:
    s: dict = {"trades": len(trades), "halts": halts, "initial": initial}
    if equity.empty:
        return s
    end = float(equity.iloc[-1])
    s.update(start=str(equity.index[0].date()), end=str(equity.index[-1].date()), final=end,
             total_return=end / initial - 1)
    years = max((equity.index[-1] - equity.index[0]).days, 1) / 365.25
    s["cagr"] = (end / initial) ** (1 / years) - 1 if years >= 0.5 and end > 0 else float("nan")
    s["max_drawdown"] = float((equity / equity.cummax() - 1).min())
    nan = float("nan")
    s.update(win_rate=nan, profit_factor=nan, avg_r=nan, avg_hold_days=nan, best_r=nan, worst_r=nan)
    if len(trades):
        wins = trades.loc[trades["pnl"] > 0, "pnl"].sum()
        losses = -trades.loc[trades["pnl"] <= 0, "pnl"].sum()
        hold = (pd.to_datetime(trades["exit_date"]) - pd.to_datetime(trades["entry_date"])).dt.days
        s.update(win_rate=float((trades["pnl"] > 0).mean()),
                 profit_factor=float(wins / losses) if losses > 0 else float("inf"),
                 avg_r=float(trades["r_multiple"].mean()), avg_hold_days=float(hold.mean()),
                 best_r=float(trades["r_multiple"].max()), worst_r=float(trades["r_multiple"].min()))
    return s


def pass_checks(s: dict, pf_mark: float = PASS_PF, dd_mark: float = PASS_MAX_DD) -> list[tuple[str, bool]]:
    dd, pf = s.get("max_drawdown", float("nan")), s.get("profit_factor", float("nan"))
    dd, pf = float("nan") if dd is None else dd, float("nan") if pf is None else pf
    return [
        (f"Max drawdown {dd:.1%} must stay under {dd_mark:.0%}", dd > -dd_mark),
        (f"Profit factor {pf:.2f} must be over {pf_mark:g}", pf > pf_mark),
    ]


def _pct(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.1%}"


def _num(x, d=2) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.{d}f}"


def summary_lines(title: str, currency: str, s: dict, benchmark: float | None = None) -> list[str]:
    lines = [title, "-" * len(title)]
    if "final" not in s:
        return lines + ["No data."]
    lines += [
        f"Period          {s['start']} to {s['end']}",
        f"Start / end     {s['initial']:,.0f} -> {s['final']:,.0f} {currency}",
        f"Total return    {_pct(s['total_return'])}   (yearly {_pct(s['cagr'])})",
        f"Max drawdown    {_pct(s['max_drawdown'])}",
        f"Trades          {s['trades']}   win rate {_pct(s['win_rate'])}   avg {_num(s['avg_r'])}R",
        f"Profit factor   {_num(s['profit_factor'])}   best {_num(s['best_r'])}R   worst {_num(s['worst_r'])}R",
        f"Avg holding     {_num(s['avg_hold_days'], 1)} days",
        f"Drawdown halts  {s['halts']}",
    ]
    if benchmark is not None:
        lines.append(f"Index buy&hold  {_pct(benchmark)} (for comparison only)")
    return lines


# ---------- HTML ----------

CSS = """
:root{--bg:#fbfaf7;--fg:#1f2328;--muted:#5f6670;--card:#ffffff;--line:#e3e1db;--accent:#2f6fb5;
--good:#1f7a4d;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#16181b;--fg:#e8e6e3;--muted:#a0a4aa;--card:#1e2125;
--line:#2f3338;--accent:#7fb0e8;--good:#5cc28d;--bad:#ef7a72}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}
h2{font-size:18px;margin:28px 0 10px}h3{font-size:15px;margin:18px 0 8px;color:var(--muted)}
.sub{color:var(--muted);margin:0 0 16px}.card{background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:14px 16px;margin:10px 0}.kpis{display:grid;
grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.kpi b{display:block;font-size:20px;
font-variant-numeric:tabular-nums}.kpi span{color:var(--muted);font-size:13px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.good{color:var(--good)}.bad{color:var(--bad)}svg.chart{width:100%;height:auto;display:block}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;border:1px solid var(--line);font-size:12px}
"""


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
            f"<body><main>{body}</main></body></html>")


def kpis(items: list[tuple[str, str, str]]) -> str:
    """items: (label, value, css class)"""
    cells = "".join(f"<div class='card kpi'><b class='{c}'>{html.escape(v)}</b><span>{html.escape(l)}</span></div>"
                    for l, v, c in items)
    return f"<div class='kpis'>{cells}</div>"


def table(rows: list[dict], cols: list[tuple[str, str]], numeric: set[str] = frozenset()) -> str:
    if not rows:
        return "<p class='sub'>None.</p>"
    head = "".join(f"<th>{html.escape(label)}</th>" for _, label in cols)
    body = []
    for r in rows:
        cells = []
        for key, _ in cols:
            v = r.get(key, "")
            if isinstance(v, float):
                v = "" if math.isnan(v) else f"{v:,.2f}"
            cls = " class='n'" if key in numeric else ""
            cells.append(f"<td{cls}>{html.escape(str(v))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def equity_svg(series: pd.Series, label: str = "Equity") -> str:
    if series is None or len(series) < 2:
        return "<p class='sub'>Not enough days for a chart yet.</p>"
    if len(series) > 800:
        series = series.iloc[:: math.ceil(len(series) / 800)]
    w, h, pad = 760, 200, 8
    lo, hi = float(series.min()), float(series.max())
    span = (hi - lo) or 1.0
    n = len(series) - 1
    pts = " ".join(f"{pad + i * (w - 2 * pad) / n:.1f},{pad + (hi - v) * (h - 2 * pad) / span:.1f}"
                   for i, v in enumerate(series.values))
    return (f"<div class='card'><svg class='chart' viewBox='0 0 {w} {h}' role='img' aria-label='{label}'>"
            f"<polyline fill='none' stroke='var(--accent)' stroke-width='1.6' points='{pts}'/></svg>"
            f"<p class='sub'>{html.escape(label)}: low {lo:,.0f}, high {hi:,.0f}, "
            f"{series.index[0].date()} to {series.index[-1].date()}</p></div>")


def send_telegram(text: str) -> str:
    token = os.environ.get("TBOT_TELEGRAM_TOKEN")
    chat = os.environ.get("TBOT_TELEGRAM_CHAT_ID")
    if not token or not chat:
        return "Telegram not set up (set TBOT_TELEGRAM_TOKEN and TBOT_TELEGRAM_CHAT_ID to enable)."
    import requests

    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text[:4000]}, timeout=20)
        return "Telegram message sent." if r.ok else f"Telegram failed: HTTP {r.status_code}"
    except requests.RequestException as exc:
        return f"Telegram failed: {exc}"
