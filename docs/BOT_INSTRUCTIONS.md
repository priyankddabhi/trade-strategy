# Trading Bot – Instructions (US + India)

## 1. Mindset
- Safety first, profit second.
- No trade = default. A no-trade day is a good day.
- Zero loss is impossible. Keep losses small, rare and capped.
- US and India: separate analysis, settings and code.
- Bot runs inside our website: visible, explainable, stoppable.

## 2. Risk Rules (cannot be bypassed)
- Max risk per trade: 0.5% of capital.
- Max daily loss: 1% → stop for the day.
- Max weekly loss: 2% → stop for the week.
- Max drawdown: 5% → full stop, manual restart only.
- Max 10% capital per stock, max 5 positions per market.
- Reward:risk ≥ 2:1 after costs.
- Stop-loss always placed at the broker. Move it to breakeven at +1R.
- Banned: averaging down, leverage, option selling, penny stocks, revenge trades, stale data.
- Kill switch: website button + auto trigger on errors or limit hit.

## 3. Strategy (v1)
- Swing trading, cash equity only.
- US: S&P 100 stocks + SPY/QQQ ETFs.
- India: Nifty 50 / Next 50 stocks + index ETFs.
- Trade only when the main index is above its 200-day average.
  - Exception, the owner's choice (08 Oct 2026, kept 12 Oct 2026): with the switch "Buy when the index is under its 200-day average" ON, the bot may buy at most one very strong share (beats the index by 10 points over 3 months) at 0.1% risk. Panic (fear gauge above its limit) or missing data still blocks every buy. Switch OFF = no buys below the 200-day average.
- New buys only while the past-years test of the settings in use has passed (phase 1 marks) and is up to date (made with prices from the last 90 days). Failed, missing or old test = no new buys; shares already held keep their stops and are sold by their own rules.
- Signal needs trend + momentum + volume + regime.
- Every trade stores the reason for showing on the website.

## 4. US vs India
| | US | India |
|---|---|---|
| Hours | 9:30–16:00 ET | 9:15–15:30 IST |
| Index / fear gauge | S&P 500, Nasdaq / VIX | Nifty, Bank Nifty / India VIX |
| Key drivers | Fed, CPI, jobs, earnings | RBI, Budget, FII/DII, crude, INR, GIFT Nifty |
| Costs | Low | Higher (STT, GST, stamp duty) |
| Rules | PDT rule | SEBI algo framework |
| Brokers | Alpaca, IBKR | Zerodha, Upstox, Angel One, Dhan |

- No entries in first/last 15 minutes or around FOMC, CPI, RBI, Budget, results days.

## 5. Phases
1. Backtest 5–10 years with costs → drawdown < 10%, profit factor > 1.3.
2. Paper trade 3+ months, 100+ trades.
3. Small real money for 3 months.
4. Scale slowly.

## 6. To Decide
- Capital per market.
- Brokers for US and India.
- Personal or public website.
- Website tech stack.
- AI path: customize open-source bot, fine-tune open-source model, or build own SLM.
