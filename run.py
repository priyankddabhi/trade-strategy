"""Phase 1 trading bot. Real market data, virtual money.

  python run.py backtest india --start 2015-01-01
  python run.py backtest us --start 2015-01-01 --end 2024-12-31
  python run.py daily                 # both markets, run once a day
  python run.py status
  python run.py check india           # simulation must match a backtest of the same days
  python run.py walkforward us        # year by year, sensitivity, practice vs exam years
  python run.py study us              # 10 years: compare ways of trading (takes about 10 minutes)
  python run.py passport us           # past-years test of the settings in use now, saved for the website
  python run.py reset-halt india      # after a 5% drawdown halt, once you have reviewed it
  python run.py web                   # website on http://127.0.0.1:8050
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from tbot import app
from tbot.config import MARKETS


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Phase 1 trading bot: real prices, virtual money.")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logging")
    parser.add_argument("--profile", choices=["p1", "p2", "p3"], default=os.environ.get("TBOT_PROFILE") or None,
                        help="profile to work on (default: the one picked last on the website)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="test the strategy on past prices")
    b.add_argument("market", choices=MARKETS)
    b.add_argument("--start", default="2015-01-01")
    b.add_argument("--end", default=None)
    b.add_argument("--offline", action="store_true", help="use cached prices only")
    b.add_argument("--no-blackouts", action="store_true", help="ignore event blackout days")

    d = sub.add_parser("daily", help="run the simulation up to the latest finished session")
    d.add_argument("--market", choices=MARKETS, action="append", help="default: both")
    d.add_argument("--offline", action="store_true")
    d.add_argument("--no-telegram", action="store_true")

    sub.add_parser("status", help="show the virtual accounts")

    c = sub.add_parser("check", help="compare the simulation with a backtest of the same days")
    c.add_argument("market", choices=MARKETS)
    c.add_argument("--offline", action="store_true")

    wf = sub.add_parser("walkforward", help="does the strategy hold up on years it was not tuned on?")
    wf.add_argument("market", choices=MARKETS)
    wf.add_argument("--start", default="2015-01-01")
    wf.add_argument("--split", default="2022-01-01", help="exam years start here; earlier years are practice")
    wf.add_argument("--end", default=None)
    wf.add_argument("--offline", action="store_true")

    sy = sub.add_parser("study", help="10 years: first 5 train, last 5 test, plus 1 and 2-year bundles")
    sy.add_argument("market", choices=MARKETS)
    sy.add_argument("--offline", action="store_true")

    pp = sub.add_parser("passport", help="past-years test of the settings in use now (saved for the website)")
    pp.add_argument("market", choices=MARKETS)
    pp.add_argument("--offline", action="store_true")

    r = sub.add_parser("reset-halt", help="clear a drawdown halt after review")
    r.add_argument("market", choices=MARKETS)

    w = sub.add_parser("web", help="open the website on this PC")
    w.add_argument("--port", type=int, default=8050)
    w.add_argument("--no-browser", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    logging.getLogger("peewee").setLevel(logging.WARNING)
    from tbot import profiles

    profiles.use(args.profile or profiles.active(), remember=False)   # the website keeps its own choice

    if args.cmd == "backtest":
        app.backtest(args.market, args.start, args.end, args.offline, not args.no_blackouts)
    elif args.cmd == "daily":
        app.daily(args.market or list(MARKETS), args.offline, not args.no_telegram, gate=app.test_gate)
    elif args.cmd == "status":
        app.status()
    elif args.cmd == "check":
        return 0 if app.check(args.market, args.offline) else 1
    elif args.cmd == "walkforward":
        from tbot import walkforward

        walkforward.run(args.market, args.start, args.split, args.end, args.offline)
    elif args.cmd == "study":
        from tbot import study

        study.run(args.market, args.offline)
    elif args.cmd == "passport":
        from tbot import study

        p = study.passport(args.market, args.offline)
        t = p["test"]
        print(f"{args.market.upper()} past-years test, {p['split']} to {p['end']}: profit factor {t['pf']:.2f}, "
              f"biggest drop {abs(t['dd']):.1%}, return {t['ret']:+.1%}, {t['trades']} trades. "
              + ("PASSED." if p["passed"] else "NOT passed."))
        for c in p["checks"]:
            print(("  pass: " if c["ok"] else "  fail: ") + c["text"])
    elif args.cmd == "reset-halt":
        app.reset_halt(args.market)
    elif args.cmd == "web":
        import threading
        import webbrowser

        import uvicorn

        from tbot.web import api

        url = f"http://127.0.0.1:{args.port}"
        print(f"Website: {url}  (press Ctrl+C to stop)")
        if not args.no_browser:
            threading.Timer(1.5, webbrowser.open, [url]).start()
        uvicorn.run(api, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
