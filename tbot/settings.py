"""Settings you can change on the website. Saved per market in state/settings_<market>.json and applied
every time the bot loads its settings (config.load_market), so the live bot, the tests of past years and
the time machine all use them. Defaults are the numbers in docs/BOT_INSTRUCTIONS.md (and config/*.yaml
for strategy details)."""
from __future__ import annotations

import json
import math

from . import config, rules

PCT = "%"
# key, group, label, unit, default, choices, rule from BOT_INSTRUCTIONS (None = not a rule there)
FIELDS = [
    ("risk.risk_per_trade", "Risk", "Risk per trade", PCT, 0.005, [0.001, 0.0025, 0.005, 0.0075, 0.01, 0.02], "at most 0.5%"),
    ("risk.max_positions", "Risk", "Shares held at once", "", 5, [1, 2, 3, 4, 5, 6, 8, 10], "at most 5"),
    ("risk.max_new_per_day", "Risk", "New buys per day", "", 3, [1, 2, 3, 5], None),
    ("risk.per_strategy_max", "Risk", "Shares per strategy", "", 1, [1, 2, 3, 5], None),
    ("risk.max_position_pct", "Risk", "Money in one share", PCT, 0.10, [0.05, 0.10, 0.15, 0.20, 0.25], "at most 10%"),
    ("risk.min_reward_risk", "Risk", "Reward : risk after costs", ":1", 2.0, [1.5, 2.0, 2.5, 3.0], "at least 2:1"),
    ("risk.max_daily_loss", "Safety stops", "Daily loss stop", PCT, 0.01, [0.005, 0.01, 0.015, 0.02, 0.03], "1%"),
    ("risk.max_weekly_loss", "Safety stops", "Weekly loss stop", PCT, 0.02, [0.01, 0.02, 0.03, 0.04, 0.05], "2%"),
    ("risk.max_drawdown", "Safety stops", "Drawdown full stop", PCT, 0.05, [0.03, 0.05, 0.08, 0.10, 0.15], "5%"),
    ("regime.bad_market.allow", "Market", "Buy when the index is under its 200-day average", "", False, [False, True],
     "no: trade only above the 200-day average"),
    ("strategy.breakout_days", "Strategy", "New high over (days)", "", 20, [10, 20, 40, 55], None),
    ("strategy.stop_atr", "Strategy", "Stop distance (ATR)", "", 2.0, [1.5, 2.0, 2.5, 3.0], None),
    ("strategy.trail_atr", "Strategy", "Trailing stop (ATR)", "", 2.0, [1.5, 2.0, 2.5, 3.0], None),
    ("strategy.take_profit_r", "Strategy", "Profit target (R)", "", 3.0, [2.0, 2.5, 3.0, 4.0], None),
    ("pass.profit_factor", "Test past years: pass marks", "Money won for every 1 lost", "×", 1.3, [1.1, 1.2, 1.3, 1.5, 2.0],
     "more than 1.3× (profit factor)"),
    ("pass.max_drawdown", "Test past years: pass marks", "Worst fall from its highest point", PCT, 0.10, [0.05, 0.10, 0.15, 0.20],
     "under 10% (biggest drop)"),
]
# Strategies: one Yes/No per lab rule. The default differs per market (the lab's best rules for that market).
FIELDS += [(f"strategy.use.{k}", "Strategies", rules.LABELS[k], "", {"us": k in rules.US or k == "trend_breakout",
            "india": k in rules.INDIA or k == "trend_breakout"}, [False, True], None)
           for k in ["trend_breakout"] + [k for k in rules.ALL if k != "trend_breakout"]]
KEYS = {f[0]: f for f in FIELDS}


def default(key: str, market: str):
    d = KEYS[key][4]
    return d.get(market, False) if isinstance(d, dict) else d


def _path(market: str):
    return config.SETTINGS_DIR / f"settings_{market}.json"


def saved(market: str) -> dict:
    try:
        return json.loads(_path(market).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get(cfg: dict, key: str):
    node = cfg
    for part in key.split("."):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return None if node == {} else node


def put(cfg: dict, key: str, value) -> None:
    *head, last = key.split(".")
    node = cfg
    for part in head:
        node = node.setdefault(part, {})
    node[last] = value


def apply(cfg: dict, market: str) -> dict:
    """Pass-mark defaults, then the saved website settings on top of the yaml file."""
    cfg.setdefault("pass", {"profit_factor": 1.3, "max_drawdown": 0.10, "min_trades": 30})
    mine = saved(market)
    for key, value in mine.items():
        if key in KEYS:
            put(cfg, key, value)
    budget = mine.get("capital")
    if isinstance(budget, (int, float)) and not isinstance(budget, bool) and budget > 0:
        cfg["capital"] = budget                         # the last budget you set: a new account opens with it
    return cfg


def remember_budget(market: str, amount: float) -> None:
    if not (amount > 0 and math.isfinite(amount)):
        raise ValueError("Type an amount above 0.")
    path = _path(market)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**saved(market), "capital": float(amount)}, indent=1), encoding="utf-8")


def _problem(key: str, v) -> str | None:
    """Why a typed value cannot be used, or None. Any number of 0 or more is allowed, not only the listed choices."""
    if key not in KEYS:
        return f"{key}: unknown setting"
    _, _, label, unit, default, choices, _ = KEYS[key]
    if isinstance(default, (bool, dict)):
        return None if isinstance(v, bool) else f"{label}: choose Yes or No"
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return f"{label}: type a number, 0 or more"
    if isinstance(default, int) and v != int(v):
        return f"{label}: type a whole number"
    if unit == PCT and v > 1:
        return f"{label}: at most 100%"
    return None


def save(market: str, values: dict) -> None:
    bad = [p for p in (_problem(k, v) for k, v in values.items()) if p]
    if bad:
        raise ValueError("Not allowed. " + "; ".join(bad))
    clean = {k: int(v) if isinstance(KEYS[k][4], int) and not isinstance(KEYS[k][4], bool) and not isinstance(v, bool) else v
             for k, v in values.items()}
    path = _path(market)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**saved(market), **clean}, indent=1), encoding="utf-8")


def reset(market: str) -> None:
    """Back to the BOT_INSTRUCTIONS numbers."""
    save(market, {f[0]: default(f[0], market) for f in FIELDS})


def view(market: str) -> list[dict]:
    cfg = config.load_market(market)
    out = []
    for key, group, label, unit, _, choices, rule in FIELDS:
        value, base = get(cfg, key), default(key, market)
        if group == "Strategies":
            value = bool(value)
        out.append({"key": key, "group": group, "label": label, "unit": unit, "value": value, "default": base,
                    "choices": choices, "rule": rule, "outside": rule is not None and value != base and _looser(key, value, base)})
    return out


def _looser(key: str, value, default) -> bool:
    """True when a value is less safe than the BOT_INSTRUCTIONS number."""
    if isinstance(default, bool):
        return bool(value) and not default
    stricter_is_bigger = key in ("risk.min_reward_risk", "pass.profit_factor")
    return value < default if stricter_is_bigger else value > default
