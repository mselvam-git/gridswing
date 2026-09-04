"""Grid state machine: levels, anchor, and open lots for the long-only grid strategy."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Lot:
    level: int
    buy_date: object
    buy_price: float
    qty: float
    lot_value: float


def level_price(anchor: float, step_pct: float, k: int) -> float:
    return anchor * (1 - step_pct / 100) ** k


def level_weight(k: int, mode: str, dynamic_weights: list[float] | None) -> float:
    if mode != "dynamic" or not dynamic_weights:
        return 1.0
    if k <= len(dynamic_weights):
        return dynamic_weights[k - 1]
    return dynamic_weights[-1]


def compute_levels(
    anchor: float,
    step_pct: float,
    capital: float,
    max_deploy_pct: float,
    lot_size: float,
    mode: str,
    dynamic_weights: list[float] | None,
) -> list[int]:
    """Depths 1..N such that cumulative deployment through depth N stays within max-deploy%."""
    budget = capital * max_deploy_pct / 100
    levels: list[int] = []
    cum = 0.0
    k = 1
    while True:
        deploy = level_weight(k, mode, dynamic_weights) * lot_size
        if cum + deploy > budget:
            break
        cum += deploy
        levels.append(k)
        k += 1
        if k > 1000:  # ponytail: sanity cap, real grids never get this deep
            break
    return levels


class Grid:
    """Tracks anchor, grid depths, and open lots. Engine drives it day by day."""

    def __init__(
        self,
        first_close: float,
        step_pct: float,
        target_pct: float,
        capital: float,
        max_deploy_pct: float,
        lot_size: float,
        mode: str,
        dynamic_weights: list[float] | None,
        anchor_mode: str,
    ):
        self.step_pct = step_pct
        self.target_pct = target_pct
        self.capital = capital
        self.max_deploy_pct = max_deploy_pct
        self.lot_size = lot_size
        self.mode = mode
        self.dynamic_weights = dynamic_weights
        self.anchor_mode = anchor_mode

        self.anchor = first_close
        self.depths = compute_levels(self.anchor, step_pct, capital, max_deploy_pct, lot_size, mode, dynamic_weights)
        self.open_lots: dict[int, Lot] = {}
        self.deployed_capital = 0.0
        self.exhausted_events: list[tuple[object, float]] = []

    def update_anchor(self, close: float) -> None:
        if self.anchor_mode == "trailing" and close > self.anchor:
            self.anchor = close
            self.depths = compute_levels(
                self.anchor, self.step_pct, self.capital, self.max_deploy_pct,
                self.lot_size, self.mode, self.dynamic_weights,
            )

    def depth_price(self, k: int) -> float:
        return level_price(self.anchor, self.step_pct, k)

    def depth_weight(self, k: int) -> float:
        return level_weight(k, self.mode, self.dynamic_weights)

    def check_buy(self, close: float, date: object) -> list[Lot]:
        """Buy every qualifying level (a gap-down day can cross more than one)."""
        bought = []
        for k in self.depths:
            if close <= self.depth_price(k) and k not in self.open_lots:
                lot_value = self.depth_weight(k) * self.lot_size
                if self.deployed_capital + lot_value > self.capital * self.max_deploy_pct / 100:
                    continue
                qty = lot_value / close
                lot = Lot(level=k, buy_date=date, buy_price=close, qty=qty, lot_value=lot_value)
                self.open_lots[k] = lot
                self.deployed_capital += lot_value
                bought.append(lot)
        # capital-exhaustion: price has fallen below the deepest grid level, no room left to buy deeper
        if self.depths and close <= self.depth_price(self.depths[-1]):
            depth_below_anchor_pct = (1 - close / self.anchor) * 100
            self.exhausted_events.append((date, depth_below_anchor_pct))
        return bought

    def check_sells(self, close: float) -> list[Lot]:
        sold = []
        for k in list(self.open_lots.keys()):
            lot = self.open_lots[k]
            if close >= lot.buy_price * (1 + self.target_pct / 100):
                sold.append(lot)
                del self.open_lots[k]
                self.deployed_capital -= lot.lot_value
        return sold
