import random
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, r"C:\Users\priya\AppData\Local\Temp\claude\d--dabhim-imp-RJ-office-us-in-treding\e103e35f-c7d6-4e20-8069-109f2bb0a0ef\scratchpad")
import lab  # noqa: E402
from src import book_strategies as bs  # noqa: E402

CFGD = {"strategy": lab.CFG["strategy"]}
random.seed(7)

CALL = {
    "SEPA": lambda f, r, d: bs.minervini_sepa(f, CFGD, d),
    "SuperPerf": lambda f, r, d: bs.minervini_superperformance(f, r, CFGD, d),
    "TurtleS1": lambda f, r, d: bs.turtle_system1(f, r, CFGD, d),
    "TurtleS2": lambda f, r, d: bs.turtle_system2(f, r, CFGD, d),
    "Whipsaw": lambda f, r, d: bs.turtle_whipsaw(f, r, CFGD, d),
    "Donchian": lambda f, r, d: bs.turtle_donchian(f, r, CFGD, d),
    "AbsoluteMom": lambda f, r, d: bs.absolute_momentum(f, CFGD, d),
    "RelativeMom": lambda f, r, d: bs.relative_momentum(f, r, CFGD, d),
    "GEM": lambda f, r, d: bs.gem(f, r, CFGD, d),
    "DualMomentum": lambda f, r, d: bs.dual_momentum(f, r, CFGD, d),
    "NewYearlyHigh": lambda f, r, d: bs.radge_new_yearly_high(f, CFGD, d),
    "HundredDayHigh": lambda f, r, d: bs.radge_hundred_day_high(f, CFGD, d),
    "GoldenCross": lambda f, r, d: bs.radge_golden_cross(f, CFGD, d),
    "TrendPilot": lambda f, r, d: bs.radge_trendpilot(f, r.loc[:d], CFGD, d),
    "ClenowTF": lambda f, r, d: bs.clenow_trend_following(f, r, CFGD, d),
    "Momentum": lambda f, r, d: bs.clenow_momentum(f, r.loc[:d], CFGD, as_of=d),
    "WeeklyRot": lambda f, r, d: bs.bensdorp_weekly_rotation(f, r, CFGD, d),
    "MeanRevLong": lambda f, r, d: bs.bensdorp_mean_reversion_long(f, CFGD, d),
    "MeanRevShort": lambda f, r, d: bs.bensdorp_mean_reversion_short(f, CFGD, d),
    "Trend200": lambda f, r, d: bs.bensdorp_trend_following(f, CFGD, d),
    "MeanReversion": lambda f, r, d: bs.bensdorp_mean_reversion(f, CFGD, d),
}

tot = {k: [0, 0, 0, 0] for k in CALL}   # checked, sig mismatch, stop mismatch, true-signals checked
for mk in ("us", "india"):
    data, idx = lab.load_market(mk)
    I = lab.index_info(idx)
    regime = idx.copy()
    regime["ma200"] = lab.ind.sma(regime["close"], 200)
    regime["above_ma200"] = regime["close"] > regime["ma200"]
    syms = random.sample(list(data), 8)
    for s in syms:
        df = data[s]
        sg = lab.make_signals(df, I)
        dates = df.index[(df.index >= "2016-10-01")]
        for name, fn in CALL.items():
            r = sg[name]
            true_pos = np.where(r["sig"] & (df.index >= "2016-10-01"))[0]
            pick = list(random.sample(range(len(dates)), min(25, len(dates))))
            pos_all = [df.index.get_loc(dates[i]) for i in pick]
            pos_all += list(random.sample(list(true_pos), min(12, len(true_pos))))
            for p in pos_all:
                d = df.index[p]
                o = fn(df.loc[:d], regime, d)
                osig = o.direction != 0
                tot[name][0] += 1
                tot[name][3] += int(osig)
                if osig != bool(r["sig"][p]):
                    tot[name][1] += 1
                    if tot[name][1] <= 2:
                        print("SIG MISMATCH", mk, s, name, d.date(), "orig", osig, "vec", bool(r["sig"][p]))
                elif osig and o.stop_price is not None and abs(o.stop_price - r["stop"][p]) > 1e-6 * abs(o.stop_price):
                    tot[name][2] += 1
                    if tot[name][2] <= 2:
                        print("STOP MISMATCH", mk, s, name, d.date(), o.stop_price, r["stop"][p])
print(f"{'strategy':<16}{'checked':>8}{'orig long':>10}{'sig diff':>9}{'stop diff':>10}")
for k, v in tot.items():
    print(f"{k:<16}{v[0]:>8}{v[3]:>10}{v[1]:>9}{v[2]:>10}")
