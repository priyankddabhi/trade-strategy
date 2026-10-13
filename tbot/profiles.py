"""Three profiles, like a Netflix "Who's watching?" screen. No log in, no sign up.

Each profile has its own folders, so nothing is shared between them: the practice account and its history
(sim.sqlite), the website settings, the saved run facts, the past-years test, the daily logs, the per-market
stop switches, the time machine, the last budget and the last time-machine day and speed.
Profile 1 keeps the original folders (state/ and runs/), so everything made before profiles existed stays there.
Only one profile is active at a time; the bot (also auto mode) runs for that one. Prices are shared.
The global STOP file still stops every profile.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from . import config

IDS = ("p1", "p2", "p3")
LOOK = {"p1": ("Profile 1", "#E50914"), "p2": ("Profile 2", "#1F6C9F"), "p3": ("Profile 3", "#346538")}
_home: dict[str, Path] = {}          # profile 1's folders, taken once before the first switch


def _base() -> dict[str, Path]:
    if not _home:
        from . import clock, timemachine       # in the time machine the folders in use are its own: take the real ones
        w = timemachine._world if clock.active() else {}
        _home.update(state=w.get("state", config.STATE_DIR), runs=w.get("runs", config.RUNS_DIR),
                     stop=w.get("stop", config.STOP_DIR))       # (p1's settings live in its state folder)
    return _home


def folders(pid: str) -> dict[str, Path]:
    b = _base()
    if pid == "p1":
        return dict(b)
    own = b["state"] / "profiles" / pid
    return {"state": own, "runs": b["runs"] / "profiles" / pid, "stop": own}


def _index_path() -> Path:
    return _base()["state"] / "profiles.json"


def _index() -> dict:
    try:
        with _ix_lock:
            raw = json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw.setdefault("active", "p1")
    raw.setdefault("profiles", {})
    for pid in IDS:
        raw["profiles"].setdefault(pid, {})
        raw["profiles"][pid].setdefault("name", LOOK[pid][0])
    return raw


_ix_lock = threading.RLock()       # profiles.json: one reader or writer at a time


def _write(ix: dict) -> None:
    """Write a temporary file, then swap it in: a reader never sees half a file."""
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with _ix_lock:
        tmp.write_text(json.dumps(ix, indent=1), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, p)
                return
            except PermissionError:                  # Windows: a reader has the file open for a moment
                if attempt == 4:
                    raise
                time.sleep(0.05)


def active() -> str:
    a = _index()["active"]
    return a if a in IDS else "p1"


def listing() -> dict:
    ix = _index()
    return {"active": active(),
            "profiles": [{"id": pid, "name": ix["profiles"][pid]["name"], "color": LOOK[pid][1],
                          "picture": int(pic.stat().st_mtime) if (pic := picture(pid)) else None,
                          "started": [m for m in config.MARKETS if _has_account(pid, m)]} for pid in IDS]}


def _has_account(pid: str, market: str) -> bool:
    db = folders(pid)["state"] / "sim.sqlite"
    if not db.exists():
        return False
    import contextlib
    import sqlite3
    try:
        with contextlib.closing(sqlite3.connect(db)) as c:          # closed at once, so a reset can delete the file
            return c.execute("SELECT 1 FROM state WHERE market = ? LIMIT 1", (market,)).fetchone() is not None
    except sqlite3.Error:
        return False


def use(pid: str, remember: bool = True) -> None:
    """Point the whole bot at this profile's folders. The caller makes sure nothing is running.
    remember=False (command line): the website's chosen profile stays as it is."""
    if pid not in IDS:
        raise ValueError("Unknown profile.")
    f = folders(pid)
    f["state"].mkdir(parents=True, exist_ok=True)
    f["runs"].mkdir(parents=True, exist_ok=True)
    config.STATE_DIR = config.SETTINGS_DIR = f["state"]
    config.RUNS_DIR = f["runs"]
    config.STOP_DIR = f["stop"]
    if not remember:
        return
    with _ix_lock:
        ix = _index()
        ix["active"] = pid
        _write(ix)


def rename(pid: str, name: str) -> None:
    name = " ".join(str(name).split())[:24]
    if pid not in IDS or not name:
        raise ValueError("Type a name.")
    with _ix_lock:
        ix = _index()
        ix["profiles"][pid]["name"] = name
        _write(ix)


def remember(key: str, value) -> None:
    """Small things the active profile should remember, like the last time-machine day and speed."""
    with _ix_lock:
        ix = _index()
        ix["profiles"][ix["active"] if ix["active"] in IDS else "p1"].setdefault("memory", {})[key] = value
        _write(ix)


def recall(key: str, default=None):
    return _index()["profiles"][active()].get("memory", {}).get(key, default)


# Reset: what a fresh start removes. Shared things stay (prices, the profile list, pictures, the other profiles'
# folders); so do the STOP switches (a safety switch is never turned off by a reset) and the research reports
# made from the command line (runs/backtest, runs/study, runs/old_logs).
_KEEP_STATE = {"profiles.json", "avatars", "profiles"}
_WIPE_RUNS = ("daily", "passport", "timemachine")


def _remove(path: Path) -> None:
    import gc
    import shutil
    for attempt in range(6):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            return
        except PermissionError as exc:               # Windows: a closed sqlite file can stay locked for a moment
            if attempt == 5:
                raise ResetError(f"Could not delete {path.name}: another program has it open. Close it and press Reset again.") from exc
            gc.collect()
            time.sleep(0.3)


class ResetError(ValueError):
    """A reset could not finish. Pressing Reset again finishes it: nothing is half kept on purpose."""


def reset(pid: str) -> list[str]:
    """Wipe one profile back to a fresh start: both practice accounts (cash, bought shares, waiting orders), trades,
    history, settings, last budget, saved past-years test, daily reports, time machine and memory. The name and the
    picture stay. The caller makes sure nothing is running. Returns what was removed."""
    if pid not in IDS:
        raise ValueError("Unknown profile.")
    f = folders(pid)
    gone = []
    if f["state"].exists():
        for item in sorted(f["state"].iterdir()):
            if item.name in _KEEP_STATE or item.name.startswith("STOP"):
                continue
            _remove(item)
            gone.append(item.name)
    for name in _WIPE_RUNS:
        if (f["runs"] / name).exists():
            _remove(f["runs"] / name)
            gone.append(f"runs/{name}")
    with _ix_lock:
        ix = _index()
        ix["profiles"][pid].pop("memory", None)
        _write(ix)
    return gone


PICTURE_MAX = 3_000_000
_KINDS = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"RIFF": "webp"}


def picture(pid: str) -> Path | None:
    """The profile's own picture, if one was chosen."""
    if pid not in IDS:
        return None
    found = sorted((_base()["state"] / "avatars").glob(f"{pid}.*"))
    return found[0] if found else None


def save_picture(pid: str, raw: bytes) -> None:
    if pid not in IDS:
        raise ValueError("Unknown profile.")
    if not raw or len(raw) > PICTURE_MAX:
        raise ValueError("Pick a picture under 3 MB.")
    kind = next((k for magic, k in _KINDS.items() if raw.startswith(magic)), None)
    if kind is None or (kind == "webp" and raw[8:12] != b"WEBP"):
        raise ValueError("Use a PNG, JPG or WEBP picture.")
    folder = _base()["state"] / "avatars"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{pid}.*"):
        old.unlink()
    (folder / f"{pid}.{kind}").write_bytes(raw)
