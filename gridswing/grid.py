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
    """Linear grid spacing: level_k = anchor * (1 - k * step/100)."""
    return anchor * (1 - k * step_pct / 100)


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
    """Tracks anchor, grid depths, and open lots. Engine drives it day by day.

    `fills` selects execution semantics:
    - "close": trigger and fill both happen at the same day-close price (legacy).
    - "intraday": buy triggers on low<=level and fills AT the level price (limit-order
      semantics); sell triggers on high>=target and fills AT the target price.

    The anchor always ratchets up on any close above it — `anchor_mode` is accepted
    for backward-compatible call signatures but no longer gates that behavior.

    `slippage_pct` worsens every fill price (paid more on buys, received less on
    sells), applied regardless of fill mode. `fill_buffer_pct` is a fraction (e.g.
    0.0005 = 0.05%) that tightens the trigger condition so a level must be cleared by
    more than a bare touch: a buy needs low <= level*(1-buffer), a sell needs
    high >= target*(1+buffer). It only gates whether a fill happens — the fill price
    itself (level/target in intraday mode) is unaffected by the buffer.
    """

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
        fills: str = "close",
        slippage_pct: float = 0.0,
        fill_buffer_pct: float = 0.0,
    ):
        if fills not in ("close", "intraday"):
            raise ValueError(f"fills must be 'close' or 'intraday', got {fills!r}")

        self.step_pct = step_pct
        self.target_pct = target_pct
        self.capital = capital
        self.max_deploy_pct = max_deploy_pct
        self.lot_size = lot_size
        self.mode = mode
        self.dynamic_weights = dynamic_weights
        self.anchor_mode = anchor_mode
        self.fills = fills
        self.slippage_pct = slippage_pct
        self.fill_buffer_pct = fill_buffer_pct

        self.anchor = first_close
        self.depths = compute_levels(self.anchor, step_pct, capital, max_deploy_pct, lot_size, mode, dynamic_weights)
        self.open_lots: dict[int, Lot] = {}
        self.deployed_capital = 0.0
        self.exhausted_events: list[tuple[object, float]] = []

    def update_anchor(self, close: float) -> None:
        """Ratchet the anchor up to any new close high, recomputing grid depths."""
        if close > self.anchor:
            self.anchor = close
            self.depths = compute_levels(
                self.anchor, self.step_pct, self.capital, self.max_deploy_pct,
                self.lot_size, self.mode, self.dynamic_weights,
            )

    def depth_price(self, k: int) -> float:
        return level_price(self.anchor, self.step_pct, k)

    def depth_weight(self, k: int) -> float:
        return level_weight(k, self.mode, self.dynamic_weights)

    def check_buy(self, trigger_price: float, date: object) -> list[Lot]:
        """Buy every qualifying level (a gap-down day can cross more than one).

        trigger_price is the day's close in "close" mode or low in "intraday" mode.
        Fill price is the level price itself in "intraday" mode (limit-order fill),
        or the trigger price in "close" mode.
        """
        bought = []
        for k in self.depths:
            level_px = self.depth_price(k)
            if trigger_price <= level_px * (1 - self.fill_buffer_pct) and k not in self.open_lots:
                lot_value = self.depth_weight(k) * self.lot_size
                if self.deployed_capital + lot_value > self.capital * self.max_deploy_pct / 100:
                    continue
                fill_price = level_px if self.fills == "intraday" else trigger_price
                fill_price *= 1 + self.slippage_pct / 100  # pay slightly more on every buy
                qty = lot_value / fill_price
                lot = Lot(level=k, buy_date=date, buy_price=fill_price, qty=qty, lot_value=lot_value)
                self.open_lots[k] = lot
                self.deployed_capital += lot_value
                bought.append(lot)
        # capital-exhaustion: price has fallen below the deepest grid level, no room left to buy deeper
        if self.depths and trigger_price <= self.depth_price(self.depths[-1]) * (1 - self.fill_buffer_pct):
            depth_below_anchor_pct = (1 - trigger_price / self.anchor) * 100
            self.exhausted_events.append((date, depth_below_anchor_pct))
        return bought

    def check_sells(self, trigger_price: float) -> list[tuple[Lot, float]]:
        """Sell every lot whose target is met, returning (lot, fill_price) pairs.

        trigger_price is the day's close in "close" mode or high in "intraday" mode.
        Fill price is the lot's target price in "intraday" mode, or the trigger price
        in "close" mode. A lot opened earlier the same day is eligible immediately.
        """
        sold = []
        for k in list(self.open_lots.keys()):
            lot = self.open_lots[k]
            target_price = lot.buy_price * (1 + self.target_pct / 100)
            if trigger_price >= target_price * (1 + self.fill_buffer_pct):
                fill_price = target_price if self.fills == "intraday" else trigger_price
                fill_price *= 1 - self.slippage_pct / 100  # receive slightly less on every sell
                sold.append((lot, fill_price))
                del self.open_lots[k]
                self.deployed_capital -= lot.lot_value
        return sold
