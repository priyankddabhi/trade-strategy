"""Paths and settings loading."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RUNS_DIR = ROOT / "runs"
STATE_DIR = Path(os.environ.get("TBOT_STATE_DIR", ROOT / "state"))
SETTINGS_DIR = STATE_DIR                 # the profile's settings; the time machine uses its own copy during a trip
STOP_FILE = Path(os.environ.get("TBOT_STOP_FILE", ROOT / "STOP"))   # stops both markets, in every profile
STOP_DIR: Path | None = None             # a profile's own folder for the per-market stop switches (None = next to STOP)

MARKETS = ("india", "us")


def stop_file_for(market: str) -> Path:
    """Per-market stop switch, next to the global STOP file."""
    return (STOP_DIR or STOP_FILE.parent) / f"STOP_{market}"


def stopped(market: str) -> bool:
    return STOP_FILE.exists() or stop_file_for(market).exists()


REQUIRED = ("currency", "timezone", "session", "capital", "regime", "universe",
            "strategy", "risk", "costs", "data")


def load_market(name: str, config_dir: Path = CONFIG_DIR) -> dict:
    if name not in MARKETS:
        raise ValueError(f"Unknown market '{name}'. Use one of: {', '.join(MARKETS)}")
    with open(config_dir / f"{name}.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        raise ValueError(f"{name}.yaml is missing: {', '.join(missing)}")
    cfg["name"] = name
    from .settings import apply                         # website settings on top of the yaml file
    return apply(cfg, name)


def load_events(config_dir: Path = CONFIG_DIR) -> dict[str, set[pd.Timestamp]]:
    """Blackout days per market. Empty sets when the switch is off."""
    path = config_dir / "events.yaml"
    raw = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    enabled = raw.get("enabled", True)
    return {m: ({pd.Timestamp(str(d)) for d in (raw.get(m) or [])} if enabled else set())
            for m in MARKETS}


def universe_tickers(cfg: dict) -> list[str]:
    """Stock and ETF tickers in yfinance format, e.g. RELIANCE -> RELIANCE.NS."""
    u = cfg["universe"]
    suffix = u.get("suffix", "")
    names = list(u.get("symbols") or []) + list(u.get("etfs") or [])
    seen, out = set(), []
    for s in names:
        t = str(s).strip()
        if not t.startswith("^") and suffix and not t.endswith(suffix):
            t += suffix
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def regime_tickers(cfg: dict) -> list[str]:
    r = cfg["regime"]
    return [t for t in (r.get("index"), r.get("vix")) if t]
