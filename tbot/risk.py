"""Risk gate. It can only block or shrink a trade, never create one."""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from .models import SimState, Trade


@dataclass
class Decision:
    ok: bool
    reason: str
    qty: float = 0.0
    planned_risk: float = 0.0
    target_r: float | None = None     # set when the gate lifted the target so it pays after costs


def start_of_session(st: SimState, mode: str) -> list[str]:
    notes = []
    if st.pause_sessions_left > 0:
        st.pause_sessions_left -= 1
    if st.halted and mode == "backtest" and st.halt_sessions_left > 0:
        st.halt_sessions_left -= 1
        if st.halt_sessions_left == 0:
            st.halted = False
            st.halt_reason = ""
            st.peak_equity = st.prev_equity
            notes.append("Backtest only: trading resumed after the halt review period, drawdown peak reset.")
    return notes


def update_counters(st: SimState, day: pd.Timestamp, equity: float, r: dict, mode: str) -> list[str]:
    """Daily, weekly and drawdown numbers, measured at today's close."""
    notes = []
    iso = day.isocalendar()
    key = f"{iso[0]}-{iso[1]:02d}"
    if key != st.week_key:
        st.week_key = key
        st.week_start_equity = st.prev_equity
    st.day_return = equity / st.prev_equity - 1 if st.prev_equity else 0.0
    st.week_return = equity / st.week_start_equity - 1 if st.week_start_equity else 0.0
    st.peak_equity = max(st.peak_equity, equity)
    st.drawdown = equity / st.peak_equity - 1 if st.peak_equity else 0.0

    if not st.halted and st.drawdown <= -r["max_drawdown"]:
        st.halted = True
        st.halts += 1
        st.halt_reason = (f"Drawdown {st.drawdown:.2%} hit the {r['max_drawdown']:.0%} limit "
                          f"on {day.date()}")
        if mode == "backtest":
            st.halt_sessions_left = int(r["backtest_halt_resume_days"])
        notes.append(f"HALTED. {st.halt_reason}")
    if st.day_return <= -r["max_daily_loss"]:
        notes.append(f"Daily loss {st.day_return:.2%} reached the {r['max_daily_loss']:.0%} limit.")
    if st.week_return <= -r["max_weekly_loss"]:
        notes.append(f"Weekly loss {st.week_return:.2%} reached the {r['max_weekly_loss']:.0%} limit.")
    st.prev_equity = equity
    return notes


def record_trade(st: SimState, trade: Trade, r: dict) -> list[str]:
    """Loss streak protection, the Freqtrade StoplossGuard idea."""
    if trade.pnl < 0:
        st.loss_streak += 1
    else:
        st.loss_streak = 0
    if st.loss_streak >= r["loss_streak"]:
        st.loss_streak = 0
        st.pause_sessions_left = int(r["loss_streak_pause_days"])
        return [f"{r['loss_streak']} losing trades in a row. New entries paused for "
                f"{st.pause_sessions_left} sessions."]
    return []


def account_block(st: SimState, r: dict, stop_file: bool = False) -> str | None:
    """Reasons that block ALL new entries today. None means the account may trade."""
    if st.halted:
        return f"Halted: {st.halt_reason}. Needs a manual reset."
    if stop_file:
        return "STOP file is present."
    if st.day_return <= -r["max_daily_loss"]:
        return f"Daily loss {st.day_return:.2%} reached the {r['max_daily_loss']:.0%} limit."
    if st.week_return <= -r["max_weekly_loss"]:
        return f"Weekly loss {st.week_return:.2%} reached the {r['max_weekly_loss']:.0%} limit."
    if st.pause_sessions_left > 0:
        return f"Loss-streak pause, {st.pause_sessions_left} sessions left."
    return None


def evaluate(st: SimState, cand, equity: float, r: dict, costs, target_r: float,
             new_today: int, pending_cash: float) -> Decision:
    """Checks for one trade idea, then the position size."""
    sym = cand.symbol
    if sym in st.positions or any(o.symbol == sym for o in st.orders):
        return Decision(False, "Already held or ordered")
    last_exit = st.cooldown.get(sym)
    if last_exit is not None and st.session_no - last_exit < r["symbol_cooldown_days"]:
        return Decision(False, f"Cooldown: exited {st.session_no - last_exit} sessions ago")
    open_count = len(st.positions) + sum(1 for o in st.orders if o.side == "BUY")
    if open_count >= r["max_positions"]:
        return Decision(False, f"Max {r['max_positions']} positions reached")
    if new_today >= r["max_new_per_day"]:
        return Decision(False, f"Max {r['max_new_per_day']} new entries per day reached")
    per, rule = r.get("per_strategy_max"), getattr(cand, "strategy", "")
    if per and rule:                                  # one slot per strategy: a slow strategy cannot fill every seat
        used = (sum(1 for p in st.positions.values() if getattr(p, "strategy", "") == rule)
                + sum(1 for o in st.orders if o.side == "BUY" and getattr(o, "strategy", "") == rule))
        if used >= per:
            return Decision(False, f"this strategy already holds {used} share(s) (at most {per} per strategy)")

    rps = cand.price - cand.stop
    if rps <= 0 or math.isnan(rps):
        return Decision(False, "Stop is not below the price")
    risk_budget = equity * r["risk_per_trade"]
    qty = risk_budget / rps
    qty = min(qty, equity * r["max_position_pct"] / cand.price)
    available = max(st.cash - pending_cash, 0.0)
    qty = min(qty, available * 0.98 / (cand.price * (1 + costs.slippage)))
    fractional = r.get("fractional_shares", False)
    qty = round(qty, 4) if fractional else math.floor(qty)
    if qty <= 0:
        return Decision(False, f"Position too small: risk budget {risk_budget:,.0f}, "
                               f"risk per share {rps:.2f}, money not used {available:,.0f}")

    gate_r = getattr(cand, "gate_r", None)
    tr = target_r if gate_r is None else gate_r
    buy_cost = costs.buy(qty, cand.price)
    loss = rps * qty + buy_cost + costs.sell(qty, cand.stop)

    def ratio(t: float) -> float:
        reward = t * rps * qty - buy_cost - costs.sell(qty, cand.price + t * rps)
        return reward / loss if loss > 0 else 0.0

    rr = ratio(tr)
    if rr >= r["min_reward_risk"]:
        return Decision(True, "OK", qty, rps * qty)
    if getattr(cand, "lift", False) and loss > 0:  # costs eat the 2R target: aim a little higher, at most +0.5R
        t = tr
        for _ in range(20):
            if ratio(t) >= r["min_reward_risk"]:
                break
            t += (r["min_reward_risk"] - ratio(t)) * loss / (rps * qty) + 1e-6
        if ratio(t) >= r["min_reward_risk"] and t - tr <= 0.5:
            return Decision(True, f"OK, target lifted to {t:.2f}R to cover the costs", qty, rps * qty, round(t, 4))
    return Decision(False, f"Reward:risk after costs {rr:.2f} is below {r['min_reward_risk']:g}")
