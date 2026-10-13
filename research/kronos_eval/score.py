"""Score the Kronos forecasts saved by eval_kronos.py against what really happened and two simple baselines.

    python research/kronos_eval/score.py      -> prints the table and writes research/kronos_eval/results.md
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def score(df: pd.DataFrame) -> dict:
    d = df[df["real_ret"] != 0]
    up = (d["real_ret"] > 0)
    ics, mics, top, mtop, n_dates = [], [], [], [], 0
    for _, g in df.groupby("date"):
        if len(g) < 8:
            continue
        n_dates += 1
        ics.append(g["pred_ret"].rank().corr(g["real_ret"].rank()))
        mics.append(g["mom_20d"].rank().corr(g["real_ret"].rank()))
        avg = g["real_ret"].mean()
        top.append(g.nlargest(5, "pred_ret")["real_ret"].mean() - avg)
        mtop.append(g.nlargest(5, "mom_20d")["real_ret"].mean() - avg)
    ic, mic = np.nanmean(ics), np.nanmean(mics)
    t = ic / (np.nanstd(ics) / np.sqrt(len(ics))) if len(ics) > 1 and np.nanstd(ics) > 0 else float("nan")
    strong = df[df["pred_ret"] > 0.02]
    return {
        "forecasts": len(df), "dates": n_dates,
        "kronos_direction": float(((d["pred_ret"] > 0) == up).mean()),
        "always_up": float(up.mean()),
        "momentum_direction": float(((d["mom_20d"] > 0) == up).mean()),
        "kronos_error": float((df["pred_ret"] - df["real_ret"]).abs().mean()),
        "no_change_error": float(df["real_ret"].abs().mean()),
        "kronos_rank_ic": float(ic), "ic_t": float(t), "momentum_rank_ic": float(mic),
        "kronos_top5_extra": float(np.mean(top)), "momentum_top5_extra": float(np.mean(mtop)),
        "strong_up_calls": len(strong), "strong_up_right": float((strong["real_ret"] > 0).mean()) if len(strong) else float("nan"),
        "strong_up_avg_real": float(strong["real_ret"].mean()) if len(strong) else float("nan"),
    }


def main() -> None:
    lines = ["| Market | Days ahead | Forecasts | Kronos direction right | Always 'up' | Momentum | Kronos error | No-change error | "
             "Rank IC (t) | Momentum IC | Kronos top-5 extra | Momentum top-5 extra | Kronos '+2%' calls: right / avg real |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for mk in ("us", "india"):
        f = HERE / f"rows_{mk}.csv"
        if not f.exists():
            continue
        rows = pd.read_csv(f, parse_dates=["date"])
        for h, g in rows.groupby("h"):
            s = score(g)
            lines.append(f"| {mk.upper()} | {h} | {s['forecasts']} | {s['kronos_direction']:.1%} | {s['always_up']:.1%} | "
                         f"{s['momentum_direction']:.1%} | {s['kronos_error']:.2%} | {s['no_change_error']:.2%} | "
                         f"{s['kronos_rank_ic']:+.3f} ({s['ic_t']:+.1f}) | {s['momentum_rank_ic']:+.3f} | "
                         f"{s['kronos_top5_extra']:+.2%} | {s['momentum_top5_extra']:+.2%} | "
                         f"{s['strong_up_calls']}: {s['strong_up_right']:.0%} / {s['strong_up_avg_real']:+.2%} |")
    text = "\n".join(lines)
    print(text)
    (HERE / "results.md").write_text("# Kronos-small, last 2 years (prices it never saw)\n\n" + text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
