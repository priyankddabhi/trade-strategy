# Repo List – Possibly Useful for Our Bot (checked Oct 2026)

Legend: USE = build on it | READ = study only, do not build on it | LATER = after v1 works

## 1. Execution (placing orders)
| Repo | Market | Licence | Use | Notes |
|---|---|---|---|---|
| marketcalls/openalgo | India | AGPL-3.0 | USE | 35 brokers, sandbox, stop-loss/target, order approval mode. Run as a separate service, call its API. ~380 MB. |
| zerodha/pykiteconnect | India | MIT | OPTIONAL | Official Zerodha library. Use this OR OpenAlgo, not both. |
| alpacahq/alpaca-py | US | Apache-2.0 | CHECK | Active SDK. Confirm Alpaca accepts Indian residents before choosing it. |
| ib-api-reloaded/ib_async | US | BSD-2 | USE | Active IBKR library. IBKR accepts Indian residents. |

## 2. Market Data
| Repo | Market | Licence | Use | Notes |
|---|---|---|---|---|
| ranaroussi/yfinance | Both (.NS for India) | Apache-2.0 | USE (research) | Free, unofficial. Never for live orders. |
| JerBouma/FinanceDatabase | US mostly | MIT | USE | Symbol and sector lists. Install with pip, do not git clone (5 GB). |
| jugaad-py/jugaad-data | India | none | READ | NSE scraper, open bugs, no licence file. Research only. |
| openbq-org/OpenBB | Both | Apache-2.0 | LATER | Moved to non-profit OpenBQ, Oct 2026. Governance risk. |

## 3. India Analysis and Strategy Ideas
| Repo | Licence | Use | What to learn |
|---|---|---|---|
| krank-09/nse-algo-trading-system | MIT | READ | Indian cost model (~0.29% round trip), look-ahead-free backtest, walk-forward splits. 2 days old, 0 stars, 21% drawdown. |
| Prashant9800/stocks-algo-trading | GPL-3.0 | READ | Indian costs plus capital gains tax, backtest vs paper agreement check. Says no proven edge yet. |
| nitiprabhu/algo-trading | MIT | READ | Risk config blocks, positional stock swing module. Paper only, 645 MB. |
| pkjmesra/PKScreener | MIT | USE (pip only) | NSE screener for breakouts and patterns. Do not git clone (19 GB). |
| lshariprasad/Stock-Trading-Agent | none | READ | 11-step analysis ideas. No licence, do not copy code. |
| Rohanom/Indian-Market-Algo-Backtest | MIT | READ | India backtest ideas. Unchecked quality. |

## 4. Backtesting
| Repo | Licence | Use | Notes |
|---|---|---|---|
| polakowo/vectorbt | Commons Clause | USE (research) | Fast. Licence blocks selling a product built mainly on it. |
| QuantConnect/Lean | Apache-2.0 | LATER | Needs Docker. Conflicts with OpenAlgo order path. Its Algorithm.Python folder (453 files) is good for strategy ideas. |
| nautechsystems/nautilus_trader | LGPL-3.0 | LATER | Rust core, backtest-to-live parity. Heavy. |
| kernc/backtesting.py | AGPL-3.0 | OPTIONAL | Simple. AGPL licence, check before using in the website. |
| mementum/backtrader | GPL-3.0 | OPTIONAL | Mature but old. GPL licence. |

## 5. Risk and Reports
| Repo | Licence | Use | Notes |
|---|---|---|---|
| ranaroussi/quantstats | Apache-2.0 | USE | Drawdown, Sharpe, tearsheets. Measures only, never blocks. |
| freqtrade/freqtrade | GPL-3.0 | READ | Crypto only. Copy its design: strategy class shape, protections (drawdown halt, cooldown), dry-run = live, Telegram control. |
| yakub268/algo-trading-platform | MIT | READ | Crypto/forex, not our markets. Risk architecture ideas only. |
| Our own risk gate | - | MUST BUILD | No repo refuses bad orders. |

## 6. Brain (AI)
| Repo | Licence | Use | Notes |
|---|---|---|---|
| ollama/ollama | MIT | LATER | Run DeepSeek or Qwen locally. AI may only veto a trade, never add one. About 6 GB with a model. |
| TauricResearch/TradingAgents | Apache-2.0 | LATER | Research reports, supports local models. Results vary run to run. |
| HKUDS/Vibe-Trading | MIT | READ | Plain English to backtest idea. Unchecked quality. |
| AI4Finance-Foundation/FinRL | MIT | LATER | Reinforcement learning, research only. |
| microsoft/qlib | MIT | LATER | Machine learning research. Default data is Chinese stocks. |
| virattt/ai-hedge-fund | MIT | READ | README says not for real trading. |

## 7. Storage, Dashboard, Alerts
| Repo | Licence | Use | Notes |
|---|---|---|---|
| duckdb/duckdb | MIT | USE | One-file database for trades and risk counters. |
| streamlit/streamlit | Apache-2.0 | USE (internal) | Quick dashboard for ourselves, not the public website. |
| python-telegram-bot | LGPL / GPL-3.0 | USE | Alerts and STOP command. Check licence wording before bundling. |
| tiangolo/fastapi | MIT | USE | Backend API for the website. |
| grafana/grafana | AGPL-3.0 | OPTIONAL | Monitoring. Use unchanged. |

## 8. Reference Lists
| Repo | Use |
|---|---|
| wilsonfreitas/awesome-quant | READ. Trusted list, ~30k stars. |
| suguobin2021/awesome-open-source-trading | READ. Unvetted list. Verify each project. |
| paperswithbacktest/awesome-systematic-trading | READ. Strategy paper list. |

## 9. Checked and Rejected (do not use)
- Sell options or use leverage: brndnmtthws/thetagang, drwatson93/ThetaBot, buzzsubash/algo_trading_strategies_india, Aravin/Algo-Trade.
- Dead or archived: alpacahq/alpaca-trade-api-python, hemenkapadia/getbhavcopy.
- Weak or unfinished: 85599/kjscreener, fourstoats/openfade, man-group/ArcticDB, PyPortfolioOpt and Riskfolio-Lib (not needed in v1).
- Wrong market or style: hummingbot/hummingbot (crypto market making).
- Link broken: anoopkcn/pynse (404).

## 10. More Repos (added from the master list)
Link status was checked on GitHub. Stars and feature claims come from the shared list and were not all verified.

### India data (all READ, research only, none beats broker data via OpenAlgo)
| Repo | Notes |
|---|---|
| BennyThadikaran/NseIndiaApi | Unofficial NSE API. GPL-3.0. |
| NSEDownload/NSEDownload | Historical NSE and index data. |
| girishg4t/bhavCopy-downloader | Bhavcopy plus derivatives data. |
| Aniruddha1980/Bhavcopy | Bhavcopy, OI and option chain GUI. |
| pparesh25/NSE_BSE_Downloader | Desktop NSE and BSE downloader. |
| meticulousCraftman/TickerStore | NSE history through Upstox. |
| KalyanM45/MarketInsight | History plus news. |

### US data
| Repo | Licence | Use | Notes |
|---|---|---|---|
| polygon-io/client-python | MIT | LATER | Paid, professional US data. Only if free data stops being enough. |
| databento/databento-python | - | LATER | Paid institutional data. Link not verified. |

### India analysis and strategy ideas (all READ)
| Repo | What to look at |
|---|---|
| Bhala-Srinivash/nse-trading-skills | MCP trading skills for Claude. |
| NayakwadiS/NSE-Neuron | Deep-learning price prediction. Prediction models rarely survive costs. |
| chandewardnyanesh/kronos-nse-terminal | Kronos model plus backtesting. |
| thegeek13242/bharat-terminal | LangGraph, Claude, Redpanda. 1 star, new. |
| pmjangid90/StockMarket_Project | Option chain, PCR, max pain. Options focus. |
| KoushikEng/TradingSystem | Pluggable strategy framework, Fyers API. |
| parmar-m/NSE_TRADER | Download, strategy, compare flow. |
| Historify and OpenScript (OpenAlgo ecosystem) | Data store and scripting language that ship with OpenAlgo. |

### US ideas
| Repo | Licence | Use | Notes |
|---|---|---|---|
| blankly-finance/blankly | LGPL-3.0 | READ | Multi-asset framework with Alpaca. |
| bluedabadi/SchwabAutoTrading | - | READ | Schwab options automation. Options conflict with our rules. |
| sachinsaner86/sachin-labs-options-trade-analyzer | - | READ | E-Trade options analyser. Options conflict with our rules. |
| xiahialiu/ib-trader | - | SKIP | Link returned 404. |

### Brain and AI extras
| Repo | Licence | Use | Notes |
|---|---|---|---|
| shiyu-coder/Kronos | MIT | LATER | Foundation model trained on candlesticks. Test like any other signal. |
| AI4Finance-Foundation/FinRobot | - | READ | Equity research style reports. |
| AI4Finance-Foundation/FinGPT | - | READ | Finance language models, good for sentiment ideas. |
| hsliuping/TradingAgents-CN | tiered, check text | READ | TradingAgents for Chinese data. Licence has layered terms. |
| Open-Finance-Lab/AgenticTrading | OpenMDW-1.0 | READ | Agent orchestration building blocks. Unusual licence, read it first. |
| OpenBB-finance/experimental-openbb-platform-agent | - | READ | LLM agents over real data. Experimental. |
| FinMem, FinCon | - | READ | Research designs for LLM memory and manager-analyst-risk hierarchy. Owners not verified. |

### Risk and portfolio extras (all LATER, not needed in v1)
| Repo | Licence | Notes |
|---|---|---|
| stefan-jansen/alphalens-reloaded | Apache-2.0 | Tests whether a signal predicts returns. |
| stefan-jansen/pyfolio-reloaded | Apache-2.0 | Performance and risk attribution. |
| PyPortfolio/PyPortfolioOpt | MIT | Efficient frontier, CVaR. |
| dcajasn/Riskfolio-Lib | BSD-3 | Portfolio optimisation and risk. |
| cvxgrp/cvxportfolio | - | Optimisation plus backtesting. |
| jankrepl/deepdow | - | Deep learning portfolio optimisation. |
| tradytics/eiten | - | Statistical investing strategies. |
| ssantoshp/Empyrial | - | Portfolio backtesting. |
| SORADATA/Alphaedge-quant-analytics | - | ML portfolio optimisation. |
| backtester-mcp | - | Overfitting checks: PBO, deflated Sharpe. Owner not verified. Idea worth copying. |
| rulelint | - | Lints trading rules for look-ahead bias. Owner not verified. Idea worth copying. |

### Storage extras (all LATER)
| Repo | Licence | Notes |
|---|---|---|
| timescale/timescaledb | Apache-2.0 | Move here if DuckDB is outgrown. |
| questdb/questdb | Apache-2.0 | Fast time-series database. |
| manahl/arctic | LGPL-2.1 | Original MongoDB tick store. |
| zvtvz/zvt | - | Data and factor storage. |

### More reference lists (READ)
| Repo | Notes |
|---|---|
| arturoabreuhd/best-of-ai-markets | 680+ AI and quant tools list. |
| best-of-algorithmic-trading | Owner in the shared list was wrong, link not found. |

## 11. Wrong Market or Style (do not use)
- Crypto only: freqtrade/freqtrade-strategies, jesse-ai/jesse, Drakkar-Software/OctoBot, Superalgos/Superalgos, hummingbot/hummingbot, enarjord/passivbot, CryptoSignal/Crypto-Signal, chrisleekr/binance-trading-bot, thrasher-corp/gocryptotrader.
- Options or intraday shorting: aaryansinha16/AI-trader, Wixen-7/Morning-surge-short-Strategy.
- Reported as design spec with no code (not checked by us): pranay123-stack/NSE-derivatives-MFT-strategies-zerodha.
- Link not found: AdityaPawade Zerodha live trading bot (404).

## 12. Rules for Using Any Repo
- Never give a real broker login to a repo you have not read.
- Check licence first. No licence file means we cannot legally reuse the code.
- AGPL and GPL repos: run as separate services or just read them. Do not copy code into ours.
- Paper trade before any live money, always.
