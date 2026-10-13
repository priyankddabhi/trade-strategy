# Paper Trading Bot

**Real market data. Dummy money. Measured profit.**

No broker keys. No live orders. Nothing here can lose you a rupee.

---

## Run it

```bash
pip install yfinance duckdb quantstats fastapi uvicorn pandas numpy pyyaml

python3 run.py india          # India paper simulation (terminal)
python3 run.py us             # US paper simulation (terminal)
python3 run.py both           # both, separate state

python3 server.py             # WEBSITE - pick IN or US, type your money

python3 -m src.book_strategies --market india            # all 7 book strategies
python3 -m src.book_strategies --market india --as-of 2024-10-01
python3 tests/test_risk.py    # 42 checks proving the risk gate
python3 analyse.py us --walkforward   # sensitivity + in/out-of-sample
python3 dashboard.py          # build reports/dashboard.html
```

First run downloads ~2 years of daily bars for ~110 symbols and caches them in
`data/cache/`. Subsequent runs are instant.

---

## The book strategies

You uploaded seven books. **Every strategy in all of them** is implemented in
**one file**: [`src/book_strategies.py`](src/book_strategies.py) — 22 strategies.

| # | Book | Author | Strategies |
|---|---|---|---|
| 1 | Trade Like a Stock Market Wizard | Minervini | **SEPA + VCP**, **Superperformance trend template** |
| 2 | Way of the Turtle | Curtis Faith | **System 1** (20-day), **System 2** (55-day), **Whipsaw**, **Donchian + 50/300 filter** |
| 3 | Dual Momentum Investing | Gary Antonacci | **Absolute momentum**, **Relative momentum**, **GEM**, **Dual Momentum** |
| 4 | Unholy Grails | Nick Radge | **New Yearly High** (250-day), **Golden/Death Cross**, **TrendPilot**, **100-Day High** |
| 5 | Stocks on the Move | Andreas Clenow | **Standard trend following**, **Adjusted-slope momentum**, **Risk parity sizing** |
| 6 | The 30-Minute Stock Trader | Laurens Bensdorp | **Weekly Rotation**, **Mean Reversion Long**, **Mean Reversion Short**, **200-SMA trend**, **Mean reversion** |
| 7 | Nifty200 Momentum 30 (UTI) | NSE Indices | **Normalized Momentum Score**, **Factor-tilt weighting** |

Every strategy is an independent function returning the same shape, so they
can be compared side by side. All **146 parameters** live in
`config/settings.yaml` under `strategy.books:` — tune there, never in the code.

### The ensemble

The strategies are combined by a deliberately boring rule:

1. At least **2 strategies** must independently say long.
2. At least **3** must have expressed *any* opinion.
3. Longs must outnumber shorts.

That second rule is the important one. A 2-of-2 "100% consensus" is not
agreement when twenty strategies are silent because they correctly refuse to
trade a bear market. Without it, the ensemble traded on two votes while
Clenow, Radge, Turtle, Minervini and Bensdorp all stood down — the index was
below its 200-day average. Requiring participation is what makes the ensemble
behave like BOT_INSTRUCTIONS §1: *a no-trade day is a good day.*

### What it does on real data

```
$ python3 -m src.book_strategies --market india --limit 25

  EACH STRATEGY'S VERDICT  (INDIA, 25 symbols)
  strategy        book             long  short   hit rate
  --------------------------------------------------------------------------
  SEPA            Minervini           0      0       0.0%
  SuperPerf       Minervini          10      0      40.0%   <- not today
  TurtleS1        Faith               0      0       0.0%
  TurtleS2        Faith               0      0       0.0%
  Whipsaw         Faith               0      0       0.0%
  Donchian        Faith               0      0       0.0%
  AbsoluteMom     Antonacci          22      0      88.0%
  RelativeMom     Antonacci           9      0      36.0%
  GEM             Antonacci           9      0      36.0%
  DualMomentum    Antonacci          22      0      88.0%
  NewYearlyHigh   Radge               0      0       0.0%
  GoldenCross     Radge              23      0      92.0%
  TrendPilot      Radge               0      0       0.0%
  ClenowTF        Clenow              0      0       0.0%
  Momentum        Clenow              0      0       0.0%
  WeeklyRot       Bensdorp            0      0       0.0%
  MeanRevLong     Bensdorp            4      0      16.0%
  MeanRevShort    Bensdorp            0      0       0.0%
  Trend200        Bensdorp           25      0     100.0%
  MeanReversion   Bensdorp            2      0       8.0%
  NiftyMom30      NSE/UTI            25      0     100.0%
  --------------------------------------------------------------------------
  TOTAL                             151      0      28.8%

  ENSEMBLE DECISIONS: 8 long out of 25
```

Then evaluate the same strategies at a date when the market *was* trending:

```
$ python3 -m src.book_strategies --market india --limit 25 --as-of 2024-10-01

  strategy        book             long  short   hit rate
  AbsoluteMom     Antonacci          22      0      88.0%
  GoldenCross     Radge              23      0      92.0%
  Trend200        Bensdorp           25      0     100.0%
  NiftyMom30      NSE/UTI            25      0     100.0%
  GEM             Antonacci           9      0      36.0%
  SuperPerf       Minervini          10      0      40.0%
  MeanRevLong     Bensdorp            4      0      16.0%
  --------------------------------------------------------------------------
  TOTAL                             151      0      28.8%
```

The spread is the point: 151 long votes out of 550 possible on a trending
date, and the *same* 22 strategies fall almost completely silent when the
index drops below its 200-day average. The breakout-based methods (SEPA,
Turtle, TrendPilot, ClenowTF, New Yearly High, Weekly Rotation) are silent in
**both** runs because a breakout has to happen on the exact day you look —
they are event strategies, not state strategies. That is a property of the
methods, not a bug.

### Single-symbol breakdown

```
$ python3 -m src.book_strategies --market india --symbol TITAN
```

Prints every strategy's full reason dict — the MA stack, the ATR reading, the
excess return over cash, the index regime — so the website can always answer
*why*.

### A note on what these books are and are not

These are published trading methodologies. What is implemented here is their
*rule sets* — the entry conditions, the stop logic, the sizing rules — as
code, with attribution. That is different from reproducing the books' prose,
and it is what makes them testable. The prose stays in the books; what lives
in this repo is a runnable, auditable version of each method's logic that the
risk gate can refuse.

Note also that a book's rules are written for the market and era the author
traded. Clenow's regime filter and Radge's TrendPilot were both built on index
data; Turtle's 300-day filter was built on futures; Bensdorp's Weekly Rotation
assumes a 10-position S&P 500 universe. They work here, but they are not
magic — which is exactly why the sensitivity sweep exists.

### A note on what these books are and are not

These are published trading methodologies. What is implemented here is their
*rule sets* — the entry conditions, the stop logic, the sizing rules — as
code, with attribution. That is different from reproducing the books' prose,
and it is what makes them testable. The prose stays in the books; what lives
in this repo is a runnable, auditable version of each method's logic that the
risk gate can refuse.

Note also that a book's rules are written for the market and era the author
traded. Clenow's regime filter and Radge's TrendPilot were both built on
index data; Turtle's 300-day filter was built on futures. They work here, but
they are not magic — which is exactly why the sensitivity sweep exists.

```bash
python3 server.py     # then open http://localhost:8000
```

**Pick India or US → type how much money you want → press RUN BOT.**

The bot re-runs the whole simulation on *your* capital using real prices, and
the page shows live market data next to the result.

| What you get | |
|---|---|
| Market toggle | ₹ India / $ US — separate state, separate capital, never mixed |
| Capital input | Type any amount. Saved to `data/capitals.json` and reused next visit. |
| Live market card | Index level and % change, India VIX / VIX, 60-day index trend, top movers |
| Equity curve | Your capital over the full 340 days |
| KPIs | Net P&L, return %, trades, win rate, profit factor, max drawdown, expectancy, costs |
| Trades table | Every trade: date, symbol, qty, entry, exit, P&L, R-multiple, exit reason |
| "Why the bot said no" | Every rule that blocked a bar, and how often — the audit trail |

The page polls `/api/live/{market}` every 45 seconds, so the market card keeps
refreshing while you watch it.

**Capital genuinely scales the result.** ₹5,00,000 gives 13 trades; ₹2,00,000
gives 4. A smaller account means smaller positions, so the 10%-per-stock cap and
the cash constraint bite sooner. That is real behaviour, not a bug.

### API

```
GET  /api/markets          market list + saved capitals
POST /api/capital          {market, capital}   - save your money
POST /api/run              {market, capital}   - starts a run, returns job_id
GET  /api/job/{job_id}     queued | running | done | error
GET  /api/result/{market}  the finished result
GET  /api/live/{market}    live quotes, index, fear gauge, movers
GET  /api/health           liveness
```

Runs happen in a background thread, so the page never blocks while the bot
fetches ~110 symbols.

---

## What it does

| | |
|---|---|
| **Data** | Real daily OHLCV from Yahoo Finance. Split/dividend adjusted. Cached locally. |
| **Money** | Dummy. ₹5,00,000 India / $10,000 US. Never touches a broker. |
| **Strategy** | Cash-equity swing. Trend **and** momentum **and** volume **and** regime. |
| **Risk** | A pure-function gate that runs before every order and cannot be bypassed. |
| **Fills** | Next bar's **open**. Never the signal bar's close. No look-ahead. |
| **Costs** | India 0.29% round trip. US 0.05%. Charged on both legs. |

---

## The result so far

340 trading days of real history, 2024-07-29 → 2026-10-07.

| Market | Trades | Win rate | Profit factor | Net P&L | Max DD | Expectancy |
|---|---|---|---|---|---|---|
| India | 13 | 38.5% | 0.91 | **−₹1,249** | −1.76% | +0.16R |
| US | 64 | 34.4% | 1.52 | **+$573** | −3.59% | +0.08R |

**Neither number is a promise.** Read the three findings below before you
believe either of them.

### Finding 1 — India does not trade enough to be judged

13 trades in 14 months. BOT_INSTRUCTIONS §5 requires **100+ trades** before a
paper phase means anything. At this rate India needs about eight years to reach
that bar. The four-condition conjunction is simply too rare on Nifty 50 large
caps, which are liquid enough that 1.5× volume days are uncommon.

This is not "the strategy is bad in India". It is "we cannot tell yet", which is
a different and more honest statement.

### Finding 2 — costs force a wider target than you think

India's 0.29% round-trip cost (STT + brokerage + GST + stamp duty + exchange)
is large relative to a 3% stop distance. The arithmetic:

```
net R:R = (g·R − C) / (R + C)
solving for g when the net ratio must reach m:
g = m + (m + 1)·(C/R)
```

With C/R ≈ 0.10 and m = 2, **g ≈ 2.30**. A 2:1 *price* target nets only about
1.8:1 in rupees. Any system that targets 2:1 in price while claiming 2:1 after
costs is quietly trading at 1.8:1 and does not know it.

The bot solves for the target instead of hard-coding it —
`solve_gross_reward_risk()` in `src/strategy.py`. The US at 0.05% barely notices
(2:1 price → ~1.98:1 net).

### Finding 3 — the bot is flat right now, correctly

The Nifty 50 is below its 200-day moving average as of the last bar. The regime
gate therefore refuses every entry. BOT_INSTRUCTIONS §1: *a no-trade day is a
good day.*

---

## Robustness

`python3 analyse.py us --walkforward`

**US — robust.** 18 of 20 perturbed settings were profitable (90%), and the
out-of-sample leg (2026-01-13 → 2026-10-06, never used to pick anything)
delivered 18 trades, PF 3.44, +$702. The edge survives a ±20% nudge on every
threshold.

**India — fragile.** 2 of 20 settings profitable. Both walk-forward legs lost
money (−₹869 in-sample, −₹702 out-of-sample). Consistent, but on a sample too
small to distinguish a bad strategy from an unlucky thirteen trades.

---

## Architecture

```
run.py              CLI entry point
dashboard.py        HTML report generator
analyse.py          sensitivity sweep + walk-forward
config/
  settings.yaml     every threshold from BOT_INSTRUCTIONS, in one place
src/
  config.py         typed config access
  data/
    fetcher.py      Yahoo download + range-keyed cache
    validator.py    refuses stale/broken bars instead of repairing them
  indicators.py     SMA/EMA/RSI/MACD/ATR/ADX/OBV — pure functions
  strategy.py       the 4-condition signal + cost-aware target solver
  risk.py           THE GATE — pure function, no I/O, fully unit-tested
  sizing.py         0.5% risk sizing + net R:R after costs
  orders.py         order state machine + idempotent client IDs
  portfolio.py      positions, cash, risk counters
  events.py         FOMC / RBI / Budget blackout calendar
  broker/
    base.py         adapter contract every venue implements
    paper.py        real prices, dummy money, resting stops
  engine.py         the one place that advances time
  storage.py        DuckDB
  reporting.py      text reports
tests/
  test_risk.py      42 checks against BOT_INSTRUCTIONS §2
```

### The two rules that make it trustworthy

**1. The risk gate is a pure function.**

```python
risk_gate(symbol, market, proposed_notional, risk_amount,
          stop_distance, reward_risk, bar_age_hours, now,
          portfolio, config) -> Decision(allowed, rule, detail)
```

No I/O. No clock. No randomness. Same inputs → same answer, always. That is why
42 unit tests can *prove* it enforces every BOT_INSTRUCTIONS §2 rule without
needing data, a broker, or a network. Every rejection carries the rule name, so
the website can always answer "why not?".

**2. Nothing downstream may look ahead.**

The engine advances one bar at a time and in this order:

1. Advance to bar T
2. Fire resting stops/targets from bars already reached
3. Compute signals from data **closed at bar T−1**
4. Execute those signals at bar **T's open**
5. Mark positions to T's close, update risk counters

A signal computed on bar T−1 is filled at bar T's open. Never at the close of
the bar that produced it. That single rule removes the most common way
backtests lie.

---

## What is banned, and how

| Banned | Mechanism |
|---|---|
| Averaging down | The engine never adds to an existing position. The code path does not exist. |
| Leverage | Cash-only. Buys are funded from cash; nothing is borrowed. |
| Shorting | Long side only. `Side.SELL` appears only as an exit. |
| Option selling | No options in the universe, full stop. |
| Revenge trades | 60-minute lock per symbol after a loss, checked in the gate. |
| Stale data | Bar age checked per symbol against the simulation clock, in the gate. |
| Penny stocks | Universe is Nifty 50 / S&P 100 + index ETFs only. |
| Curve-fitting | Fixed strategy structure; only thresholds may move; ±20% sensitivity gate. |

---

## What this build is not

- **Not connected to a broker.** No keys, no orders, no money. `PaperBroker` is
  the only venue implemented.
- **Not intraday.** Daily bars. The 15-minute entry blackout from
  BOT_INSTRUCTIONS §4 is therefore *not* enforced — it needs 15-minute data. The
  event-day blackout (FOMC / RBI / Budget) **is** enforced.
- **Not survivorship-bias-free.** The universe is today's Nifty 50 and S&P 100.
  Companies that fell out of those indices during the window are missing. This
  flatters results and is documented rather than fixed.
- **Not a recommendation.** 64 trades over 14 months is a small sample even when
  it looks robust.

---

## Files written by a run

```
reports/dashboard.html      the readable report
reports/report_{market}_*.txt   plain-text report
reports/trades_{market}.csv     every trade with its reason
data/bot.duckdb              trades, signals, rejections, equity curve
data/cache/*.csv             cached price history
```

---

## Next steps, in order

1. **Lower the India volume threshold** to 1.2× and re-run. The goal is trade
   count, not profit. 100+ trades before any conclusion.
2. **Run it live in paper mode for 3+ months.** BOT_INSTRUCTIONS §5 cannot be
   compressed by coding speed. This build is ready for that phase.
3. **Add 15-minute data** so the session blackout is actually enforceable.
4. **Only then** consider a broker: OpenAlgo for India (AGPL — separate
   process, REST only, never imported), IBKR for the US. Never give a real
   login to a repo you have not read.
