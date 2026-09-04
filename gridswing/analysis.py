"""Recommendations: ATR spacing, parameter sweep, walk-forward, 50/50 blend, verdict."""
from __future__ import annotations

from itertools import product

import numpy as np
import pandas as pd

from . import engine, metrics


def atr_pct(ohlc: pd.DataFrame, period: int = 14) -> float:
    high, low, close = ohlc["High"], ohlc["Low"], ohlc["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr / close.iloc[-1] * 100)


def suggest_spacing(ohlc: pd.DataFrame, current_step: float, trades_per_year_target=(40, 60)) -> dict:
    atr = atr_pct(ohlc)
    years = (ohlc.index[-1] - ohlc.index[0]).days / 365.0
    # a step roughly equal to daily ATR% tends to trigger ~1 level per ATR move; scale to target trigger frequency
    suggested_step = round(atr, 2)
    return {"atr_pct": atr, "current_step": current_step, "suggested_step": suggested_step, "period_years": years}


def sweep(
    symbol: str, ohlc: pd.DataFrame, capital: float, lot_size: float, max_deploy: float,
    steps=(0.5, 0.75, 1.0, 1.5, 2.0), targets=(1.0, 1.5, 2.0, 3.0),
    stcg_rate: float = 30.0, ltcg_rate: float = 12.5, fills: str = "intraday",
    slippage: float = 0.1, fill_buffer: float = 0.0005,
) -> pd.DataFrame:
    rows = []
    for step, target in product(steps, targets):
        res = engine.run(
            symbol, ohlc, capital=capital, step=step, target=target, lot_size=lot_size,
            max_deploy=max_deploy, stcg_rate=stcg_rate, ltcg_rate=ltcg_rate, fills=fills,
            slippage=slippage, fill_buffer=fill_buffer,
        )
        m = metrics.compute_metrics(res)
        rows.append({
            "step": step, "target": target,
            "post_tax_cagr_pct": m["post_tax_cagr_pct"], "max_drawdown_pct": m["max_drawdown_pct"],
        })
    return pd.DataFrame(rows)


def walk_forward(
    symbol: str, ohlc: pd.DataFrame, capital: float, lot_size: float, max_deploy: float,
    in_sample_end: str = "2024-12-31", out_sample_start: str = "2025-01-01",
    steps=(0.5, 0.75, 1.0, 1.5, 2.0), targets=(1.0, 1.5, 2.0, 3.0), fills: str = "intraday",
    slippage: float = 0.1, fill_buffer: float = 0.0005,
) -> dict:
    in_sample = ohlc.loc[ohlc.index <= in_sample_end]
    out_sample = ohlc.loc[ohlc.index >= out_sample_start]

    best = max(
        product(steps, targets),
        key=lambda st: metrics.compute_metrics(
            engine.run(symbol, in_sample, capital=capital, step=st[0], target=st[1], lot_size=lot_size,
                       max_deploy=max_deploy, fills=fills, slippage=slippage, fill_buffer=fill_buffer)
        )["post_tax_cagr_pct"],
    )
    step, target = best
    in_sample_cagr = metrics.compute_metrics(
        engine.run(symbol, in_sample, capital=capital, step=step, target=target, lot_size=lot_size,
                   max_deploy=max_deploy, fills=fills, slippage=slippage, fill_buffer=fill_buffer)
    )["post_tax_cagr_pct"]

    if out_sample.empty:
        return {"best_step": step, "best_target": target, "in_sample_cagr_pct": in_sample_cagr,
                "out_sample_cagr_pct": None, "overfitting_flag": None}

    out_sample_cagr = metrics.compute_metrics(
        engine.run(symbol, out_sample, capital=capital, step=step, target=target, lot_size=lot_size,
                   max_deploy=max_deploy, fills=fills, slippage=slippage, fill_buffer=fill_buffer)
    )["post_tax_cagr_pct"]

    drop_pct = (in_sample_cagr - out_sample_cagr) / abs(in_sample_cagr) * 100 if in_sample_cagr else 0.0
    return {
        "best_step": step, "best_target": target,
        "in_sample_cagr_pct": in_sample_cagr, "out_sample_cagr_pct": out_sample_cagr,
        "overfitting_flag": drop_pct > 30,
    }


def blend_5050(symbol: str, ohlc: pd.DataFrame, capital: float, **run_kwargs) -> dict:
    half = capital / 2
    grid_res = engine.run(symbol, ohlc, capital=half, **run_kwargs)
    grid_equity = grid_res.equity_curve["equity"]

    closes = ohlc["Close"]
    bh_equity = half * (closes / closes.iloc[0])

    combined = grid_equity.reindex(bh_equity.index).ffill() + bh_equity
    combined_final = combined.iloc[-1]
    combined_return_pct = (combined_final - capital) / capital * 100

    grid_metrics = metrics.compute_metrics(grid_res)
    bh_metrics = metrics.benchmark_buy_hold(ohlc, capital, run_kwargs.get("ltcg_rate", 12.5))

    return {
        "combined_post_tax_absolute_return_pct": combined_return_pct,
        "grid_only_post_tax_cagr_pct": grid_metrics["post_tax_cagr_pct"],
        "buy_hold_only_post_tax_cagr_pct": bh_metrics["post_tax_cagr_pct"],
        "combined_equity_curve": combined,
    }


def verdict(grid_metrics: dict, bh_metrics: dict, spacing: dict) -> str:
    lines = []
    if grid_metrics["post_tax_cagr_pct"] > bh_metrics["post_tax_cagr_pct"]:
        lines.append("Grid beat buy-and-hold here, post-tax — typical when the asset chopped sideways or range-bound.")
    else:
        lines.append("Buy-and-hold beat the grid, post-tax — typical in a strong, sustained bull run where the grid "
                      "sits mostly idle below a fixed anchor.")
    lines.append(f"Grid post-tax CAGR: {grid_metrics['post_tax_cagr_pct']:.1f}% vs "
                 f"buy-and-hold post-tax CAGR: {bh_metrics['post_tax_cagr_pct']:.1f}%.")
    lines.append(f"Max drawdown on the grid equity curve: {grid_metrics['max_drawdown_pct']:.1f}%.")
    lines.append(f"Realistic expectation: taxes ate {grid_metrics['total_tax']:.0f} "
                 f"({grid_metrics['stcg_tax']:.0f} STCG + {grid_metrics['ltcg_tax']:.0f} LTCG) off gross gains.")
    if grid_metrics["dead_days_pct"] > 50:
        lines.append(f"Biggest improvement available: idle-cash drag — {grid_metrics['dead_days_pct']:.0f}% of days "
                      "had no trade; park undeployed cash (e.g. LiquidBees) via --idle-yield to reduce it.")
    lines.append(f"ATR-based spacing check: current step is {spacing['current_step']}%, ATR(14) is "
                 f"{spacing['atr_pct']:.2f}% of price — consider a step near {spacing['suggested_step']}% "
                 "if trade frequency looks too low or too high.")
    return "\n".join(lines)
