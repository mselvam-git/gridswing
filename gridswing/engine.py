"""Day-by-day simulation loop: drives the grid state machine over daily closes.

No look-ahead: each day's decisions use only that day's close and prior state.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .grid import Grid
from .ledger import Ledger


@dataclass
class SimulationResult:
    symbol: str
    capital: float
    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    daily_ledger: pd.DataFrame
    exhausted_events: list[tuple[object, float]]
    buy_dates: list[object]
    ledger: Ledger
    grid: Grid


def run(
    symbol: str,
    ohlc: pd.DataFrame,
    capital: float,
    step: float,
    target: float,
    lot_size: float,
    mode: str = "classic",
    dynamic_weights: list[float] | None = None,
    max_deploy: float = 100.0,
    idle_yield: float = 0.0,
    anchor_mode: str = "first_close",
    brokerage: float = 0.0,
    stcg_rate: float = 30.0,
    ltcg_rate: float = 12.5,
    fills: str = "intraday",
    slippage: float = 0.1,
    fill_buffer: float = 0.0005,
) -> SimulationResult:
    """Simulate the grid strategy day by day.

    `fills="intraday"` (default): buys trigger on low<=level and fill at the level
    price; sells trigger on high>=target and fill at the target price — a lot bought
    today can also sell today. `fills="close"` is the legacy behavior: both trigger
    and fill happen at the day's close. Either way, only that day's own OHLC is used
    — no look-ahead.

    `slippage` (% per side, default 0.1) worsens every fill price. `fill_buffer`
    (fraction, default 0.0005 = 0.05%) requires a level/target to be cleared by more
    than a bare touch before it counts as triggered.
    """
    if ohlc.empty:
        raise ValueError("No price data to simulate over.")
    if fills == "intraday" and not {"High", "Low"}.issubset(ohlc.columns):
        raise ValueError("fills='intraday' requires High and Low columns in ohlc.")

    first_close = float(ohlc["Close"].iloc[0])
    grid = Grid(
        first_close=first_close, step_pct=step, target_pct=target, capital=capital,
        max_deploy_pct=max_deploy, lot_size=lot_size, mode=mode,
        dynamic_weights=dynamic_weights, anchor_mode=anchor_mode, fills=fills,
        slippage_pct=slippage, fill_buffer_pct=fill_buffer,
    )
    ledger = Ledger(symbol=symbol, stcg_rate=stcg_rate, ltcg_rate=ltcg_rate)

    cash = capital
    equity_rows = []
    daily_ledger_rows = []
    buy_dates: list[object] = []
    realized_pnl_cum = 0.0

    for row in ohlc.itertuples():
        date = row.Index
        close = float(row.Close)

        cash += cash * idle_yield / 100 / 365

        anchor_before = grid.anchor
        grid.update_anchor(close)
        anchor_after = grid.anchor

        if fills == "intraday":
            buy_trigger, sell_trigger = float(row.Low), float(row.High)
        else:
            buy_trigger = sell_trigger = close

        bought = grid.check_buy(buy_trigger, date)
        for lot in bought:
            cash -= lot.lot_value * (1 + brokerage / 100)
            buy_dates.append(date)

        sold = grid.check_sells(sell_trigger)
        sell_proceeds_day = 0.0
        realized_pnl_day = 0.0
        for sold_lot, fill_price in sold:
            proceeds = sold_lot.qty * fill_price * (1 - brokerage / 100)
            cash += proceeds
            sell_proceeds_day += proceeds
            realized_pnl_day += (fill_price - sold_lot.buy_price) * sold_lot.qty
            ledger.record_sell(
                buy_date=sold_lot.buy_date, sell_date=date,
                buy_price=sold_lot.buy_price, sell_price=fill_price, qty=sold_lot.qty,
            )
        realized_pnl_cum += realized_pnl_day

        open_value = sum(l.qty * close for l in grid.open_lots.values())
        equity_rows.append({"date": date, "cash": cash, "open_value": open_value, "equity": cash + open_value})

        if bought and sold:
            action = "BUY_SELL"
        elif bought:
            action = "BUY"
        elif sold:
            action = "SELL"
        else:
            action = "HOLD"

        # next grid level that would trigger a buy (smallest depth not already an open lot)
        next_buy_level = next((k for k in grid.depths if k not in grid.open_lots), None)
        next_leg_trigger_price = grid.depth_price(next_buy_level) if next_buy_level is not None else None
        next_leg_amount = grid.depth_weight(next_buy_level) * grid.lot_size if next_buy_level is not None else None

        open_principal = grid.deployed_capital
        portfolio_value = cash + open_value
        daily_ledger_rows.append({
            "date": date,
            "market_price": close,
            "anchor_before": anchor_before,
            "anchor_after": anchor_after,
            "new_ath": anchor_after > anchor_before,
            "drawdown_pct": (1 - close / anchor_after) * 100,
            "action": action,
            "buy_legs": len(bought),
            "sell_legs": len(sold),
            "buy_amount": sum(lot.lot_value for lot in bought),
            "sell_proceeds": sell_proceeds_day,
            "realized_pnl_day": realized_pnl_day,
            "realized_pnl_cum": realized_pnl_cum,
            "open_legs": len(grid.open_lots),
            "open_principal": open_principal,
            "holding_units": sum(l.qty for l in grid.open_lots.values()),
            "holding_value": open_value,
            "unrealized_pnl": open_value - open_principal,
            "cash": cash,
            "portfolio_value": portfolio_value,
            "overall_pnl": portfolio_value - capital,
            "next_buy_level": next_buy_level,
            "next_leg_trigger_price": next_leg_trigger_price,
            "next_leg_amount": next_leg_amount,
        })

    equity_curve = pd.DataFrame(equity_rows).set_index("date")
    daily_ledger = pd.DataFrame(daily_ledger_rows).set_index("date")

    return SimulationResult(
        symbol=symbol, capital=capital, trades=ledger.to_dataframe(),
        equity_curve=equity_curve, daily_ledger=daily_ledger, exhausted_events=grid.exhausted_events,
        buy_dates=buy_dates, ledger=ledger, grid=grid,
    )
