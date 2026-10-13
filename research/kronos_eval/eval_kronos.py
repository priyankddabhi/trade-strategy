"""How accurate was Kronos over the last 2 years? Research only: the trading bot never imports this.

Kronos (github.com/shiyu-coder/Kronos, MIT) learned from prices up to June 2024 (paper, arXiv 2508.02739),
so the last 2 years (Oct 2024 - Oct 2026) are prices it never saw: an honest exam.

Every STEP market days in the window, for SHARES shares picked at random (fixed seed), Kronos-small reads the last
LOOKBACK daily candles (only the past) and forecasts the next HORIZON days. Each forecast is saved with what really
happened, plus two simple baselines: "up" (prices usually rise) and momentum (last 20 days' direction continues).

    python research/kronos_eval/eval_kronos.py us       (or india)   -> research/kronos_eval/rows_<market>.csv
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research" / "kronos"))
from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402

INDEX = {"us": "_GSPC.csv", "india": "_NSEI.csv"}
SHARES, STEP, LOOKBACK, HORIZON, YEARS, SEED = 30, 10, 200, 10, 2, 7
CHECK = (1, 5, 10)                       # days ahead that are scored


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["Date"]).dropna(subset=["Open", "High", "Low", "Close"])
    df = df[~df["Date"].duplicated()].sort_values("Date").set_index("Date")
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


def main(market: str) -> None:
    torch.manual_seed(SEED)
    torch.set_num_threads(8)                       # leave some CPU for the website
    folder = ROOT / "data" / market
    days = load(folder / INDEX[market]).index
    start = days[-1] - pd.DateOffset(years=YEARS)
    window = days[(days > start)]
    dates = list(window[:-HORIZON:STEP])            # every STEP market days, each with HORIZON days after it
    frames = {}
    for f in sorted(folder.glob("*.csv")):
        if f.name.startswith("_"):
            continue
        df = load(f)
        if len(df[df.index <= dates[0]]) >= LOOKBACK + 20 and df.index[-1] >= window[-1] - pd.Timedelta(days=10):
            frames[f.stem] = df
    picked = sorted(random.Random(SEED).sample(sorted(frames), min(SHARES, len(frames))))
    print(f"{market}: {len(picked)} shares, {len(dates)} forecast dates {dates[0].date()} .. {dates[-1].date()}", flush=True)

    tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    mdl = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    pred = KronosPredictor(mdl, tok, device="cpu", max_context=512)
    out = ROOT / "research" / "kronos_eval" / f"rows_{market}.csv"
    done = set(pd.read_csv(out, parse_dates=["date"])["date"]) if out.exists() else set()
    for k, day in enumerate(dates, 1):
        if day in done:
            continue
        xs, xts, yts, names, close0, mom = [], [], [], [], [], []
        for s in picked:
            df = frames[s]
            past, future = df[df.index <= day], df[df.index > day]
            if len(past) < LOOKBACK + 20 or len(future) < HORIZON or past.index[-1] != day:
                continue                           # no bar that day, or not enough after it
            hist = past.tail(LOOKBACK)
            xs.append(hist.reset_index(drop=True)); xts.append(pd.Series(hist.index))
            yts.append(pd.Series(future.index[:HORIZON])); names.append(s)
            close0.append(float(hist["close"].iloc[-1]))
            mom.append(float(hist["close"].iloc[-1] / past["close"].iloc[-21] - 1))
        if not xs:
            continue
        t0 = time.time()
        res = pred.predict_batch(xs, xts, yts, pred_len=HORIZON, T=1.0, top_p=0.9, sample_count=1, verbose=False)
        rows = []
        for s, c0, m, p in zip(names, close0, mom, res):
            real = frames[s][frames[s].index > day]["close"].iloc[:HORIZON].to_numpy()
            for h in CHECK:
                rows.append({"date": day, "symbol": s, "h": h, "pred_ret": float(p["close"].iloc[h - 1] / c0 - 1),
                             "real_ret": float(real[h - 1] / c0 - 1), "mom_20d": m})
        pd.DataFrame(rows).to_csv(out, mode="a", header=not out.exists(), index=False)
        print(f"{market} {k}/{len(dates)} {day.date()}: {len(names)} shares in {time.time() - t0:.0f}s", flush=True)
    print(f"{market}: done -> {out}", flush=True)


if __name__ == "__main__":
    for m in sys.argv[1:] or ["us", "india"]:
        main(m)
