"""Configuration loader. Reads settings.yaml, nothing else."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = _ROOT / "config" / "settings.yaml"


class Config:
    """Thin wrapper so the rest of the code never touches YAML directly."""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path else _DEFAULT_CONFIG
        with open(self.path, "r", encoding="utf-8") as fh:
            self._raw: dict[str, Any] = yaml.safe_load(fh)
        self.root = _ROOT

    # --- generic access -------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        value = self.get(dotted)
        if value is None:
            raise KeyError(f"Missing required setting: {dotted}")
        return value

    # --- typed shortcuts ------------------------------------------------
    @property
    def risk(self) -> dict:
        return self._raw["risk"]

    @property
    def strategy(self) -> dict:
        return self._raw["strategy"]

    @property
    def costs(self) -> dict:
        return self._raw["costs"]

    @property
    def markets(self) -> list[str]:
        return ["india", "us"]

    def market_cfg(self, market: str) -> dict:
        return self._raw["markets"][market]

    def universe(self, market: str) -> list[str]:
        return list(self.market_cfg(market)["universe"])

    def etfs(self, market: str) -> list[str]:
        return list(self.market_cfg(market).get("etfs", []))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Config {self.path}>"
