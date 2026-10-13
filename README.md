# Trend Strategy Bot

A swing-trading bot for **US (NYSE/Nasdaq)** and **India (NSE)** shares. It reads **real daily prices**,
trades **practice money only** (no broker, no real orders) and explains every step on a local website.
Phase 1 of the plan: prove the strategy before any real money (`docs/BOT_INSTRUCTIONS.md`, section 5).

## Folder map

```
trendstrategy/
├── README.md            this file
├── requirements.txt     Python packages
├── pytest.ini           test settings
├── .env.example         optional settings (copy to .env; .env is never uploaded)
├── run.py               all commands (see below)
├── config/              market settings: us.yaml, india.yaml, evidence lists, event days
├── tbot/                the bot
│   ├── app.py           one Run (daily), backtest, status, the one-run-per-profile lock
│   ├── engine.py        one market day: fills, stops, targets, market level, search, orders
│   ├── strategy.py      builds the strategy mix and the written reason for each trade
│   ├── rules.py         the 20+ lab strategies and their 4 pattern families
│   ├── indicators.py    averages, ATR and other price maths
│   ├── predict.py       expected range of a held share (next 5 market days)
│   ├── risk.py          risk check: size, limits, losing-streak pause, drawdown halt
│   ├── broker.py        practice fills with real costs
│   ├── costs.py         Indian charges and US fees
│   ├── data.py          yfinance prices, local cache, bad-bar checks, splits
│   ├── store.py         saved practice account (SQLite)
│   ├── models.py        account, position, order and trade records
│   ├── config.py        loads market settings and event days
│   ├── settings.py      side-panel settings (saved per profile)
│   ├── profiles.py      the 3 profiles and their folders
│   ├── clock.py         real clock or the time machine clock
│   ├── timemachine.py   trips into the past
│   ├── stages.py        what the 4 page steps show, from real facts
│   ├── study.py         10-year study and the past-years test
│   ├── walkforward.py   practice-years / exam-years tests
│   ├── report.py        numbers, pass marks, HTML reports
│   └── web.py, web/     the website: page (app.html), bot loop, timers, step modes
├── tests/               automatic tests (pytest + Hypothesis), about 200
├── scripts/             run_web.bat, run_daily.bat (double-click on Windows)
├── docs/
│   ├── BOT_INSTRUCTIONS.md   the rules that cannot be broken
│   ├── plan.md               what the bot is and does now
│   ├── flowcharts.html       12 flowcharts of the real flow (open in a browser)
│   ├── inspiration.md        useful ideas from the two older bots, not used yet
│   └── repos.md              open-source repos that may help later (brokers, data)
└── research/            studies, not used by the live bot
    ├── lab/             the 10-year lab that chose the strategies in rules.py
    ├── books_lab/       22 book strategies under study
    └── kronos_eval/     test of the Kronos price-prediction model (parked)
```

Made on your PC and never uploaded: `.venv/` (Python setup), `data/` (price cache), `state/`
(practice account), `runs/` (reports and test results), `_archive/` (unused files kept locally).

## Start

```
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python run.py web          # website on http://127.0.0.1:8050
```

Or double-click `scripts\run_web.bat`. The website only works on this PC.

## Commands

```
.venv\Scripts\python run.py daily            # process the latest finished market day, both markets
.venv\Scripts\python run.py status
.venv\Scripts\python run.py backtest us --start 2015-01-01
.venv\Scripts\python run.py check us         # simulation must equal a backtest of the same days
.venv\Scripts\python run.py walkforward us   # practice years vs exam years
.venv\Scripts\python run.py study us         # 10 years: first 5 train, last 5 test, 1 and 2-year bundles
.venv\Scripts\python run.py passport us      # past-years test of the settings in use now (website stage 4)
.venv\Scripts\python run.py reset-halt india # only after reviewing a 5% drawdown halt
.venv\Scripts\python -m pytest -q
```

## The website

- **Header:** market (US or India), money to trade with, **Run bot**.
- **5 stages of one run:** Pick market → Search whole market → Search my shares → Test past years → Buy or sell.
  Each stage shows its real result. Only the stage that is working moves. Badges show what the bot keeps
  doing, for example "Watching ABBV live".
- **First screen:** share search, live market, where the money is, and the activity box (numbers and a
  list of every buy, sell, stop move and block).
- **Control panel** (sliders button, top right): real settings per market (risk, shares at once, safety stops,
  strategy, pass marks), with **Default** = the `BOT_INSTRUCTIONS.md` numbers. Saved in `state/settings_<market>.json`.
  **Time machine:** pick a past date and a speed (1 day to 1 month per second); the bot trades forward from that
  day on real past prices with your money and settings. Your real practice account is not changed.
  To remove the time machine: delete `tbot/timemachine.py`, its block in `tbot/web.py` and its panel section.
- **Auto mode:** while the website is open it runs each market by itself after a session closes and watches
  held shares every 2 to 10 minutes. Set `TBOT_AUTO=0` to switch it off.

## How one market day works

1. Yesterday's sell orders fill at today's real open, with slippage and costs.
2. Stops and targets are checked against today's real high and low. The stop is checked first.
3. Yesterday's buy orders fill at today's open. A buy is cancelled on a gap below the stop, a big gap up,
   or an event blackout day.
4. After the close the stop moves to breakeven at +1R, then trails 2 ATR below the highest close.
5. The risk gate measures the day, the week and the drawdown.
6. Market check: good market = full risk, normal = half risk, falling = extra-safe mode (one very strong
   share, tiny size). Ideas that pass the risk gate become buy orders for the next open.

## Safety

- Risk rules from `docs/BOT_INSTRUCTIONS.md`: 0.5% risk per trade, 1% daily and 2% weekly loss stops,
  5% drawdown halt with manual restart, at most 10% of the money in one share.
- **Emergency stop:** create an empty file `STOP` in this folder (both markets), or `STOP_us` /
  `STOP_india` (one market; the website's Stop button makes these). Held shares keep their stops.
- Prices come from free data (yfinance) and can be up to 15 minutes late. They are for practice and
  research only; real trading needs broker data (phase 2).

## Daily schedule (Windows, optional)

Run once in a terminal inside this folder:

```
schtasks /Create /TN "TradingBotDaily" /TR "\"%CD%\scripts\run_daily.bat\" both" /SC DAILY /ST 07:00
```

## Limits of phase 1

- yfinance is free and unofficial. It can break or revise past prices.
- Backtests use today's index members, which makes results slightly optimistic.
- Fills at the open stand in for an entry shortly after the open.
