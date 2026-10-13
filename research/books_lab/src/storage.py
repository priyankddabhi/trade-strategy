"""DuckDB storage. Trades, signals, rejections, equity curve.

Everything the website needs to render "why did the bot do that?" lives here.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

try:
    import duckdb
except ImportError:  # pragma: no cover
    duckdb = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    symbol VARCHAR, market VARCHAR, side VARCHAR,
    quantity BIGINT, entry_price DOUBLE, exit_price DOUBLE,
    entry_date TIMESTAMP, exit_date TIMESTAMP,
    pnl DOUBLE, r_multiple DOUBLE, exit_reason VARCHAR,
    costs DOUBLE, reason VARCHAR
);
CREATE TABLE IF NOT EXISTS signals (
    symbol VARCHAR, market VARCHAR, signal_date TIMESTAMP,
    entry_price DOUBLE, stop_price DOUBLE, target_price DOUBLE,
    reward_risk DOUBLE, reason VARCHAR
);
CREATE TABLE IF NOT EXISTS rejections (
    symbol VARCHAR, market VARCHAR, bar_date TIMESTAMP,
    failed_rule VARCHAR, detail VARCHAR
);
CREATE TABLE IF NOT EXISTS equity_curve (
    market VARCHAR, date TIMESTAMP, equity DOUBLE, cash DOUBLE,
    positions BIGINT, drawdown DOUBLE
);
CREATE TABLE IF NOT EXISTS runs (
    run_id VARCHAR, started_at TIMESTAMP, market VARCHAR,
    starting_capital DOUBLE, ending_equity DOUBLE,
    n_trades BIGINT, net_pnl DOUBLE, win_rate DOUBLE,
    profit_factor DOUBLE, max_drawdown DOUBLE
);
"""


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.enabled = duckdb is not None
        if self.enabled:
            self._con = duckdb.connect(str(self.path))
            self._con.execute(SCHEMA)
            self._con.execute("DROP TABLE IF EXISTS equity_curve")
            self._con.execute(SCHEMA)
        else:
            self._con = None

    # ------------------------------------------------------------------
    def write_run(self, market: str, result) -> str:
        import uuid
        from datetime import datetime

        run_id = uuid.uuid4().hex[:12]
        stats = result.stats
        if self.enabled:
            self._con.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    run_id,
                    datetime.utcnow(),
                    market,
                    result.portfolio.starting_capital,
                    result.portfolio.equity,
                    int(stats.get("n_trades", 0)),
                    float(stats.get("net_pnl", 0.0)),
                    float(stats.get("win_rate", 0.0)),
                    float(stats.get("profit_factor", 0.0)),
                    float(stats.get("max_drawdown", 0.0)),
                ],
            )
            if result.trades:
                self._con.executemany(
                    "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [[t.symbol, t.market, t.side, t.quantity, t.entry_price,
                      t.exit_price, t.entry_date, t.exit_date, t.pnl,
                      t.r_multiple, t.exit_reason, t.costs, str(t.reason)]
                     for t in result.trades],
                )
            if result.signals:
                self._con.executemany(
                    "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?)",
                    [[s.symbol, s.market, s.date, s.entry_price, s.stop_price,
                      s.target_price, s.reward_risk, s.reason_text()]
                     for s in result.signals],
                )
            if result.rejections:
                self._con.executemany(
                    "INSERT INTO rejections VALUES (?,?,?,?,?)",
                    [[r.symbol, market, r.date, r.failed_rule, r.detail]
                     for r in result.rejections],
                )
            if not result.equity_curve.empty:
                ec = result.equity_curve.reset_index()
                self._con.executemany(
                    "INSERT INTO equity_curve VALUES (?,?,?,?,?,?)",
                    [[market, row["date"], row["equity"], row["cash"],
                      int(row["positions"]), row["drawdown"]]
                     for _, row in ec.iterrows()],
                )
            self._con.commit()
        return run_id

    def close(self) -> None:
        if self._con is not None:
            self._con.close()


def compute_stats(portfolio, trades: list, equity_curve: pd.DataFrame,
                  starting_capital: float) -> dict:
    """Every number BOT_INSTRUCTIONS asks the report to show."""
    n = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    net = sum(t.pnl for t in trades)
    total_costs = sum(t.costs for t in trades)

    max_dd = 0.0
    if not equity_curve.empty and "drawdown" in equity_curve.columns:
        max_dd = float(equity_curve["drawdown"].min()) * 100.0

    r_multiples = [t.r_multiple for t in trades]
    exit_reasons: dict = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

    return {
        "n_trades": n,
        "n_wins": len(wins),
        "n_losses": len(losses),
        "win_rate": (len(wins) / n * 100.0) if n else 0.0,
        "gross_profit": gross_win,
        "gross_loss": gross_loss,
        "net_pnl": net,
        "net_pnl_pct": (net / starting_capital * 100.0) if starting_capital else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "expectancy_r": (sum(r_multiples) / n) if n else 0.0,
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
        "total_costs": total_costs,
        "costs_as_pct_of_capital": (total_costs / starting_capital * 100.0)
        if starting_capital else 0.0,
        "max_drawdown_pct": max_dd,
        "ending_equity": portfolio.equity,
        "starting_capital": starting_capital,
        "exit_reasons": exit_reasons,
        "max_consecutive_losses_seen": _max_streak([t.pnl for t in trades]),
    }


def _max_streak(pnls: list[float]) -> int:
    best = cur = 0
    for p in pnls:
        if p < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best
