#!/usr/bin/env python3
"""
WEB DASHBOARD for the paper trading bot.

Pick a market (IN / US), type the money you want to trade with, press RUN.
The bot re-runs the whole simulation on YOUR capital using real prices, and
the page shows live market data alongside the result.

    python3 server.py            # then open the printed URL

No broker keys. No live orders. Dummy money only.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

BOT_ROOT = Path(__file__).resolve().parent
import sys  # noqa: E402

sys.path.insert(0, str(BOT_ROOT))

from run import load_market  # noqa: E402
from src.config import Config  # noqa: E402
from src.engine import Engine  # noqa: E402
from src.livefeed import live_quotes  # noqa: E402
from src.reporting import trades_to_csv  # noqa: E402
from src.storage import compute_stats  # noqa: E402

CAPITALS_FILE = BOT_ROOT / "data" / "capitals.json"
WEB_DIR = BOT_ROOT / "web"

app = FastAPI(title="Paper Trading Bot")

_cfg = Config()
_jobs: dict = {}
_results: dict = {}
_capitals: dict = {"india": 500000.0, "us": 10000.0}


def _load_capitals() -> None:
    global _capitals
    if CAPITALS_FILE.exists():
        try:
            _capitals.update(json.loads(CAPITALS_FILE.read_text()))
        except Exception:  # noqa: BLE001
            pass


def _save_capitals() -> None:
    CAPITALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CAPITALS_FILE.write_text(json.dumps(_capitals, indent=2))


_load_capitals()


# --------------------------------------------------------------------------
class CapitalIn(BaseModel):
    market: str
    capital: float


class RunIn(BaseModel):
    market: str
    capital: float | None = None


# --------------------------------------------------------------------------
def _run_simulation(market: str, capital: float) -> dict:
    bars, index_bars, fear_bars = load_market(_cfg, market, use_cache=True)
    if not bars:
        raise RuntimeError(f"no usable data for {market}")

    cost_pct = float(_cfg.get(f"costs.{market}.round_trip_pct", 0.29))
    engine = Engine(
        market=market,
        currency=_cfg.market_cfg(market)["currency"],
        bars=bars,
        index_bars=index_bars,
        fear_bars=fear_bars,
        starting_capital=capital,
        cfg={"risk": _cfg.risk, "strategy": _cfg.strategy, "_etfs": _cfg.etfs(market)},
        cost_pct=cost_pct,
        warmup_bars=210,
        verbose=False,
    )
    result = engine.run()
    stats = compute_stats(result.portfolio, result.trades,
                          result.equity_curve, capital)

    ec = result.equity_curve
    curve = [
        {"date": d.date().isoformat(), "equity": float(r["equity"]),
         "positions": int(r["positions"])}
        for d, r in ec.iterrows()
    ] if not ec.empty else []

    trades = []
    for t in sorted(result.trades, key=lambda x: x.exit_date):
        trades.append(
            {
                "symbol": t.symbol,
                "entry_date": t.entry_date.date().isoformat(),
                "exit_date": t.exit_date.date().isoformat(),
                "quantity": t.quantity,
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2),
                "pnl": round(t.pnl, 2),
                "r_multiple": round(t.r_multiple, 3),
                "exit_reason": t.exit_reason,
                "costs": round(t.costs, 2),
                "reason": t.reason.get("signal_reason", ""),
            }
        )

    rejections: dict = {}
    for r in result.rejections:
        rejections[r.failed_rule] = rejections.get(r.failed_rule, 0) + 1

    positions = []
    for sym, pos in result.portfolio.positions.items():
        positions.append(
            {
                "symbol": sym,
                "quantity": pos.quantity,
                "entry_price": round(pos.entry_price, 2),
                "stop_price": round(pos.stop_price, 2),
                "target_price": round(pos.target_price, 2),
                "breakeven_moved": pos.breakeven_moved,
                "bars_held": pos.bars_held,
            }
        )

    return {
        "market": market,
        "currency": _cfg.market_cfg(market)["currency"],
        "capital": capital,
        "cost_pct": cost_pct,
        "stats": {k: (round(v, 4) if isinstance(v, float) else v)
                  for k, v in stats.items() if k != "exit_reasons"},
        "exit_reasons": stats.get("exit_reasons", {}),
        "curve": curve,
        "trades": trades,
        "rejections": dict(sorted(rejections.items(), key=lambda kv: -kv[1])),
        "positions": positions,
        "n_signals": len(result.signals),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _worker(job_id: str, market: str, capital: float) -> None:
    try:
        _jobs[job_id]["status"] = "running"
        data = _run_simulation(market, capital)
        _results[market] = data
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = data
    except Exception as exc:  # noqa: BLE001
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    path = WEB_DIR / "index.html"
    if not path.exists():
        raise HTTPException(500, "web/index.html is missing")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/api/markets")
def markets() -> dict:
    out = []
    for m in ["india", "us"]:
        mkt = _cfg.market_cfg(m)
        out.append(
            {
                "id": m,
                "name": mkt["name"],
                "currency": mkt["currency"],
                "capital": _capitals.get(m),
                "n_symbols": len(mkt["universe"]) + len(mkt.get("etfs", [])),
                "index": mkt["index_symbol"],
                "cost_pct": float(_cfg.get(f"costs.{m}.round_trip_pct", 0.29)),
                "risk_per_trade_pct": float(_cfg.risk["risk_per_trade_pct"]),
            }
        )
    return {"markets": out}


@app.post("/api/capital")
def set_capital(body: CapitalIn) -> dict:
    if body.market not in ("india", "us"):
        raise HTTPException(400, "market must be india or us")
    if body.capital <= 0:
        raise HTTPException(400, "capital must be greater than zero")
    _capitals[body.market] = float(body.capital)
    _save_capitals()
    return {"ok": True, "market": body.market, "capital": float(body.capital)}


@app.post("/api/run")
def run(body: RunIn) -> dict:
    if body.market not in ("india", "us"):
        raise HTTPException(400, "market must be india or us")
    capital = body.capital if body.capital else _capitals.get(body.market)
    if not capital or capital <= 0:
        raise HTTPException(400, "capital must be greater than zero")
    _capitals[body.market] = float(capital)
    _save_capitals()

    job_id = uuid.uuid4().hex[:10]
    _jobs[job_id] = {"status": "queued", "market": body.market, "started": time.time()}
    threading.Thread(target=_worker, args=(job_id, body.market, float(capital)),
                     daemon=True).start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/job/{job_id}")
def job(job_id: str) -> dict:
    j = _jobs.get(job_id)
    if j is None:
        raise HTTPException(404, "unknown job")
    return j


@app.get("/api/result/{market}")
def result(market: str) -> dict:
    if market not in ("india", "us"):
        raise HTTPException(400, "market must be india or us")
    if market not in _results:
        return {"ready": False}
    return {"ready": True, "data": _results[market]}


@app.get("/api/live/{market}")
def live(market: str) -> dict:
    if market not in ("india", "us"):
        raise HTTPException(400, "market must be india or us")
    try:
        return live_quotes(market, _cfg)
    except Exception as exc:  # noqa: BLE001
        return {"market": market, "error": str(exc), "symbols": []}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "time": time.strftime("%Y-%m-%d %H:%M:%S")}


if __name__ == "__main__":
    print("=" * 60)
    print("  PAPER TRADING BOT - WEB DASHBOARD")
    print("  Real market data. Dummy money. No broker keys.")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
