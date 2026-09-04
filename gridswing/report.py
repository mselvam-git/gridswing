"""Console summary and run-artifact outputs (CSV/Markdown/PNG)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from . import metrics as metrics_mod
from .engine import SimulationResult

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def make_run_dir(symbol: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_DIR / f"{symbol.replace('.', '_')}_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def print_summary(result: SimulationResult, m: dict, bh: dict) -> None:
    print(f"\n=== GridSwing: {result.symbol} ===")
    print(f"Capital: {result.capital:,.0f}  Trades: {m['num_round_trip_trades']}  Win rate: {m['win_rate_pct']:.1f}%")
    print(f"\n{'Metric':<30}{'Grid':>15}{'Buy&Hold':>15}")
    for label, key in [
        ("Absolute return %", "absolute_return_pct"), ("CAGR %", "cagr_pct"),
        ("Post-tax abs. return %", "post_tax_absolute_return_pct"), ("Post-tax CAGR %", "post_tax_cagr_pct"),
    ]:
        print(f"{label:<30}{m[key]:>15.2f}{bh[key]:>15.2f}")
    print(f"\nMax drawdown: {m['max_drawdown_pct']:.2f}%  Max capital deployed: {m['max_capital_deployed_pct']:.2f}%")
    print(f"Dead days: {m['dead_days_pct']:.1f}%  Longest flat streak: {m['longest_flat_streak_days']} days")
    print(f"Capital-exhaustion events: {m['capital_exhaustion_events']}"
          + (f" (deepest {m['capital_exhaustion_depth_pct']:.1f}% below anchor)" if m["capital_exhaustion_depth_pct"] else ""))
    print(f"Total tax: {m['total_tax']:.0f} (STCG {m['stcg_tax']:.0f} / LTCG {m['ltcg_tax']:.0f})")


def plot_equity(result: SimulationResult, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    result.equity_curve["equity"].plot(ax=ax)
    ax.set_title(f"{result.symbol} — Equity Curve")
    ax.set_ylabel("Equity")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def plot_drawdown(result: SimulationResult, out_path: Path) -> None:
    equity = result.equity_curve["equity"]
    drawdown = (equity - equity.cummax()) / equity.cummax() * 100
    fig, ax = plt.subplots(figsize=(10, 5))
    drawdown.plot(ax=ax, color="crimson")
    ax.set_title(f"{result.symbol} — Drawdown %")
    ax.set_ylabel("Drawdown %")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def write_run_artifacts(result: SimulationResult, m: dict, bh: dict, run_dir: Path) -> None:
    result.trades.to_csv(run_dir / "trades.csv", index=False)
    result.equity_curve.to_csv(run_dir / "equity_curve.csv")

    metrics_json = {"grid": m, "buy_and_hold": bh}
    (run_dir / "metrics.json").write_text(json.dumps(metrics_json, indent=2, default=str))

    summary_md = [f"# GridSwing Run: {result.symbol}", "", f"Capital: {result.capital:,.0f}", "",
                  "## Metrics (Grid vs Buy & Hold)", "",
                  "| Metric | Grid | Buy&Hold |", "|---|---|---|"]
    for label, key in [
        ("Absolute return %", "absolute_return_pct"), ("CAGR %", "cagr_pct"),
        ("Post-tax abs. return %", "post_tax_absolute_return_pct"), ("Post-tax CAGR %", "post_tax_cagr_pct"),
    ]:
        summary_md.append(f"| {label} | {m[key]:.2f} | {bh[key]:.2f} |")
    summary_md += ["", f"Max drawdown: {m['max_drawdown_pct']:.2f}%",
                   f"Total tax: {m['total_tax']:.0f} (STCG {m['stcg_tax']:.0f} / LTCG {m['ltcg_tax']:.0f})"]
    (run_dir / "summary.md").write_text("\n".join(summary_md))

    plot_equity(result, run_dir / "equity.png")
    plot_drawdown(result, run_dir / "drawdown.png")
