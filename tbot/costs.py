"""Trading charges per market. Slippage is applied to fill prices, not here."""
from __future__ import annotations


class CostModel:
    def __init__(self, c: dict):
        self.c = c
        self.slippage = float(c.get("slippage_pct", 0.0))

    def buy(self, qty: float, price: float) -> float:
        raise NotImplementedError

    def sell(self, qty: float, price: float) -> float:
        raise NotImplementedError


class IndiaDelivery(CostModel):
    """Cash delivery: STT both sides, stamp duty on buys, DP charge on sells, GST on fees."""

    def _common(self, value: float) -> float:
        c = self.c
        brokerage = min(value * c["brokerage_pct"], c["brokerage_max"])
        exchange = value * c["exchange_pct"]
        sebi = value * c["sebi_pct"]
        gst = (brokerage + exchange + sebi) * c["gst_pct"]
        stt = value * c["stt_pct"]
        return brokerage + exchange + sebi + gst + stt

    def buy(self, qty: float, price: float) -> float:
        value = qty * price
        return self._common(value) + value * self.c["stamp_pct"]

    def sell(self, qty: float, price: float) -> float:
        return self._common(qty * price) + self.c["dp_charge"]


class USStocks(CostModel):
    """IBKR fixed commission, plus SEC fee and FINRA TAF on sells."""

    def _commission(self, qty: float, price: float) -> float:
        c = self.c
        fee = max(qty * c["per_share"], c["min_per_order"])
        return min(fee, qty * price * c["max_pct"])

    def buy(self, qty: float, price: float) -> float:
        return self._commission(qty, price)

    def sell(self, qty: float, price: float) -> float:
        c = self.c
        sec = qty * price * c["sec_fee_rate"]
        taf = min(qty * c["finra_taf_per_share"], c["finra_taf_max"])
        return self._commission(qty, price) + sec + taf


MODELS = {"india_delivery": IndiaDelivery, "us_stocks": USStocks}


def make_cost_model(c: dict) -> CostModel:
    try:
        return MODELS[c["model"]](c)
    except KeyError as exc:
        raise ValueError(f"Unknown cost model '{c.get('model')}'. Use one of: {', '.join(MODELS)}") from exc
