# Inspiration: useful things from Bot 1 and Bot 2 that trendstrategy does not use yet

Bot 1 and Bot 2 are archived in `../_archive/` (next to this project, not inside it).
Paths below are inside that archive. Nothing here is used by the live bot. Each item is an idea to test
first, through the risk rules in `BOT_INSTRUCTIONS.md`, before it goes into `tbot/`.

**In short**
- **Bot 1:** almost all of its code is already in trendstrategy, which has the same or a newer copy. What is
  left of value are its **saved test results**.
- **Bot 2:** all of its code is also in `research/books_lab/`. The live bot uses none of the ideas below yet.

## From Bot 1 (`_archive/bot/`)

| Value | Item | Where | Why it may matter |
|---|---|---|---|
| High | US walk-forward results | `runs/walkforward/us_20261008-113638/report.txt` | Trail 3 ATR and target 2.5R did better on the exam years (profit factor 1.24 vs 1.07). trendstrategy still uses 2.0 / 3.0. Worth re-testing. |
| High | India walk-forward results | `runs/walkforward/india_20261008-113626/report.txt` | Verdict "fragile" (profit factor 0.83 to 1.04). A clear warning for India settings. |
| Medium | 2015 to 2026 backtests at the old settings | `runs/backtest/` | A reference with $25,000 and 5 positions: US profit factor 1.18 with 8 drawdown halts. |

## From Bot 2 (`_archive/paper-trading-bot (1)/`)

| Value | Item | Where | Why it may matter |
|---|---|---|---|
| High | Order tracker with safe order IDs | `src/orders.py` | Each order has one record with a state (new, sent, filled, rejected). The ID comes from symbol + date, so a network retry can never buy twice. Needed for real brokers. |
| High | Broker contract | `src/broker/base.py` | One standard list of broker actions (connect, place order, cancel, positions, cash, market open) that paper and real brokers (OpenAlgo, IBKR, Alpaca) would follow. Starting point for phase 2. |
| High | Stops resting at the paper broker | `src/broker/paper.py` (`evaluate_resting_orders`) | Stop and target sit as orders at the broker. A gap down fills at the open. If one bar hits both, the stop wins. Matches the rule "stop placed at the broker". |
| Medium | Target that allows for costs | `src/strategy.py` (`solve_gross_reward_risk`) | Moves the target far enough that the trade still makes 2:1 after costs, and refuses it beyond 4:1. trendstrategy only checks 2:1 after costs. |
| Medium | Cap on total open risk | `src/risk.py` (rule 12, 2%) | Blocks a new trade when all open trades together already risk 2%. Useful if the bot goes back to 5 positions. |
| Medium | Stale and broken data checks | `src/data/validator.py`, `src/engine.py` (`_bar_age_hours`) | Refuses data with high below low, prices outside the day's range, gaps over 10 days, duplicate dates, or a last bar older than 72 hours. trendstrategy drops bad rows but has no age or gap check. |
| Medium | Event dates | `src/events.py` | FOMC, RBI and Budget dates. `config/events.yaml` has no RBI dates yet. |
| Medium | Time exit and trend-break exit | `config/settings.yaml` (`max_hold_bars: 20`, `exit_on_trend_break`) | Sell after 20 days, or when the close falls under the 50-day average. |
| Medium | Risk-limit test pattern | `tests/test_risk.py` | Tests every risk rule exactly at its limit and checks each refusal names its rule. |
| Medium | Written findings | `README.md` | India trades too rarely. Results are flattered by using today's index members. The 15-minute session blackout needs intraday data. Run OpenAlgo only as a separate service (AGPL licence). |
| Low | Original settings with comments | `config/settings.yaml` | Easier to read than the books_lab copy. Also lists the IWM and VTI funds. |
| Low | Bot 2 results | `reports/` | RSI + MACD strategy: US 64 trades, profit factor 1.52. India 13 trades. Something to compare against. |

## Not kept (already in trendstrategy, or not useful)
- All Bot 1 code, tests, settings and scripts. The same or newer copies are in trendstrategy, including Telegram alerts.
- The Bot 1 price cache. trendstrategy has the same data.
- Bot 2's live price feed, loss limits, sizing, "why the bot said no" list and sensitivity sweep. trendstrategy has its own versions.
- Bot 2's 60-minute revenge-trade lock. It does nothing on daily bars; the 5-day per-share cooldown covers the idea.
- Bot 2's DuckDB storage. The plan keeps SQLite.
- Neither bot has real broker code, Docker or server (VPS) setup, or `.env` files.

⚠️ **Note:** `plan.md` section 4 says Bot 2's RSI, MACD and ADX indicators, data validator and 72-hour
stale-data guard were copied. They are **not** in `tbot/` (it has only the averages, highs, ATR and
rate of change the trend strategy needs). The stale-data guard above is a real gap to close before live data.
