# Semi Final Bot: What It Is and What It Does

**Status:** built and working (practice money only). Branch `workflow-v2`.
**Rewritten from the code:** 13 Oct 2026. The old plan (08 Oct, "plan only, no code yet") is in git history
(branch `backup-2026-10-12`).
**Rules that always apply:** `docs/BOT_INSTRUCTIONS.md` and `CLAUDE.md` (including the step animation rules).

---

## 0. The idea in six lines

1. A swing trading bot for **US and Indian shares**, with **real prices** and **practice money**. No broker, no real orders.
2. Opening the page only loads memory. **Nothing runs until you press Run bot.**
3. A Run looks at the **whole market first**, sorts the shares, and checks the shares you hold **at the same time**.
4. Before any new buy, the strategy mix must pass a **past-years test** (10 years of old prices).
5. Every trade passes the **risk rules**: small risk per trade, stops, loss limits, at most 5 shares.
6. After a Run, the bot **keeps watching** while the program runs: held shares, waiting buys, and the market.

---

## 1. What you see

| Part | What it shows |
|---|---|
| Profile picker | 3 profiles ("Who's trading?"), each with its own account, settings, test, timers and time machine |
| Top bar | Market (US or India), Money box, **Run bot**, **Stop** (only while a Run works), clock, profile, side panel, theme |
| Step bar | 1 Search whole market · 2 Search my shares · 3 Test past years · 4 Buy or sell, with ring, reactor, tick and wires |
| Cards | What the bot does now · Live market (index, fear gauge) · Live feed · Where is my money · Trades |
| Side panel | Bot timers (live countdowns, Edit timers) · Time machine · Settings (Market, Risk, Safety stops, Strategy, Strategies, pass marks) |
| Phone | Same page, tighter; the 4 steps stay in one row |

---

## 2. One Run, step by step

```
Run bot → load the account → download new daily prices (only when a new day waits)
       → Step 1 Search whole market  ║  Step 2 Search my shares      (side by side)
       → each chosen share tested on its own past (3 years)
       → Step 3 past-years test of the whole mix (reused while up to date)
       → trading part: sells and stops first, market level, ranked buys through the risk check
       → Step 4 orders: buy at the next open (or at the live price if the market is open now) → save
```

- **One Run per profile at a time** (a small busy file), for the website and the command line together.
- **Nothing new** (no new finished day): no download, no test, no trading; the two searches run on saved prices.
- A Run **stops cleanly** when: you press Stop (before anything is saved), most shares have no price for the
  day (the day stays open), or the profile or time machine changes.

---

## 3. Strategies

| | US | India |
|---|---|---|
| Strategies in use | **20** | **16** |
| Order | best 10-year lab result first, Trend Breakout last | same |

**US:** Bollinger Breakout, 20% Flipper, TrendPilot, New Yearly High, Momentum 30, 100-Day High, Longboard,
Trend 200, SEPA, Golden Cross, Clenow Trend Following, Mean Reversion, Clenow Momentum, Dual Momentum,
Absolute Momentum, Relative Momentum, Turtle System 2, GEM, Weekly Rotation, Trend Breakout.
**India:** Momentum 30, Bollinger Breakout, Longboard, 20% Flipper, TrendPilot, 100-Day High, Fear Pullback,
New Yearly High, Clenow Momentum, Relative Momentum, GEM, Golden Cross, Trend 200, Absolute Momentum,
Dual Momentum, Trend Breakout.

**The funnel (step 1):** all shares → **quick check** (above the 200-day average or rising over 3 months, enough
trading, clean data) → a **pattern** in one of 4 families (Breakout / new high · Steady uptrend · Momentum leader ·
Dip in an uptrend) → that strategy's own rules → the **own-past check** (at least 3 past signals in 3 years,
judged 20 market days later, at least half higher, on average a profit).

**Held shares (step 2):** each held share's pattern is checked, plus an **expected range** for the next 5 market
days (the 20-day trend line, lower edge 1 normal daily move below). Below the range with a broken pattern: the stop
is raised (never lowered). The strategy that bought a share also decides its sale.

---

## 4. Risk rules and market levels

| Rule | Value |
|---|---|
| Risk per trade | 0.5% (normal market 0.25%, falling market 0.1%) |
| Shares at once | 5 (1 per strategy, max 3 new a day; falling market 1) |
| Money in one share | 10% |
| Reward : risk after costs | at least 2 |
| Day / week loss stop | 1% / 2% |
| Drawdown full stop | 5% (halt; restart only by you) |
| Losing streak | 4 losses in a row → 5 market days without new buys |
| Never | short selling, borrowed money, selling options, buying more of a loser |

**Market level** (index against its 200-day average, fear gauge): Good = full risk, 20 shares searched ·
Normal = 0.25%, 10 searched · Bad = no buys, or with the switch on: 0.1%, 1 share that beat the index by 10 points.
A fear gauge missing or older than 5 days blocks buys.

**Live buys** (waiting orders bought at the live price while the market is open) pass **the same safety checks as
the trading part**: STOP switch, halt, day and week loss stop, losing-streak pause, news days (cancelled), plus the
broker's checks (price above the stop, price jump, cash). There is no "first or last 15 minutes" rule (by choice).

---

## 5. The past-years test

- The whole strategy mix on **10 years** of old prices, in 7 parts.
- Pass marks (side panel): money won for every 1 lost ≥ **1.3**, worst fall ≤ **10%**, at least 30 trades.
- Reused while the trading settings are unchanged and the prices it used are less than 90 days old.
- **Not passed = no new buys.** Held shares are still watched and sold by their rules.

---

## 6. After a Run: the bot loop (Bot timers)

| Job | Default | Can trade? |
|---|---|---|
| Loop wakes (heartbeat; also buys waiting orders) | 60 s | buys |
| Held share: stop, target, stop to the buy price at +1R | every 2 to 10 min | sells |
| Whole market + my shares look again | 5 min (market open) | no, only looks |
| New market day finished? | 5 min | no, says "press Run bot" |

- Every time can be changed in **Bot timers → Edit timers** (per profile, safe limits).
- New buys are decided **once per finished market day** (Run bot). Auto run is off unless `TBOT_AUTO_RUN=1`.
- Free prices (Yahoo) can be up to 15 minutes late.

---

## 7. Time machine

- A separate world for one market from a chosen **day and hour** (market time; both markets' hours are shown).
- It **forgets the future**: prices are cut after its clock, no live prices, its own account, settings and test.
- Speeds from real time to 1 month per second. It runs a Run after each close and buys waiting orders at the
  **day's opening price** while its market is open.
- **The trip clock waits while the trip's own Run or past-years test works**, so no day is skipped at high speed.
- Start, Start again and Leave first stop all old work (like Reset).

---

## 8. Profiles, Stop and Reset

- 3 profiles, no log in. Prices are shared; everything else is per profile.
- **Stop** ends one market's Run at its next safe point, nothing saved; the time machine does not start it again
  until you press Run bot. Held shares stay watched.
- **Reset** first stops everything (Run, test, time machine), then wipes the profile.
- **Stop new buys** (kill switch) and **Restart after a 5% halt** are in Settings → Safety stops.

---

## 9. Step animations (rules in `CLAUDE.md`)

- One source of truth: `step_modes()` in `tbot/web.py`; the page only draws it.
- Step 1: ring on the first look of a Run, then the reactor while its work repeats by itself.
- Step 2: the reactor while held shares or waiting buys need watching.
- A loop job flares the reactor; a live buy or sell rings step 4. Steps 3 and 4 never get the reactor.

---

## 10. Data

- Daily prices: Yahoo Finance, saved in `data/` (shared by all profiles). A top-up never deletes a saved day.
- Live prices: Yahoo quotes, kept in memory only.
- Account: `sim.sqlite` per profile; findings of the last Run, settings, timers and the saved test per profile.

---

## 11. Where the code is

| File | Job |
|---|---|
| `tbot/app.py` | one Run (`daily`), the busy file, the command line gate |
| `tbot/engine.py`, `broker.py`, `risk.py`, `costs.py` | trading part, practice broker, risk check, costs |
| `tbot/strategy.py`, `rules.py`, `indicators.py`, `predict.py` | strategies, families, expected range |
| `tbot/stages.py`, `study.py`, `walkforward.py` | step facts, past-years test |
| `tbot/data.py`, `clock.py`, `timemachine.py` | prices, the clock, the time machine |
| `tbot/web.py`, `tbot/web/app.html` | website, bot loop, live checks, timers, step modes |
| `tbot/profiles.py`, `settings.py`, `store.py`, `report.py` | profiles, side panel settings, account file, reports |
| `tests/` | about 200 automated tests (pytest, Hypothesis) |
| `docs/flowcharts.html` | 12 flowcharts of the real flow |

---

## 12. Known gaps

| Gap | Note |
|---|---|
| News days list | `config/events.yaml` misses India RBI, CPI and results days |
| Market holidays | not known in advance (a holiday shows as "no new day") |
| Fractional US shares | off: small budgets cannot buy expensive shares |
| A second market's Run | refused while one works (use Stop, or wait) |
| Run output capture | process-wide; overlapping Runs can mix their details text |
| Default button | also sets "buy in a falling market" to No (BOT_INSTRUCTIONS default) |

---

## 13. Next steps (only when you decide)

1. Fill the news days and add a holiday calendar.
2. Track a "forward test" score (practice results apart from the past-years test).
3. Paper trade at least 30 trades per market before thinking about real money (BOT_INSTRUCTIONS phases).
