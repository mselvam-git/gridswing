from __future__ import annotations

from datetime import date

import click

from . import analysis, engine, metrics, report
from .data import get_ohlc


def _common_options(f):
    f = click.option("--symbol", required=True, help="yfinance symbol, e.g. GOLDBEES.NS")(f)
    f = click.option("--start", required=True, help="YYYY-MM-DD")(f)
    f = click.option("--end", default=None, help="YYYY-MM-DD, defaults to today")(f)
    f = click.option("--capital", required=True, type=float)(f)
    f = click.option("--lot-size", default=10000.0, type=float)(f)
    f = click.option("--max-deploy", default=100.0, type=float, help="max %% of capital deployable")(f)
    f = click.option("--stcg-rate", default=30.0, type=float)(f)
    f = click.option("--ltcg-rate", default=12.5, type=float)(f)
    return f


def _load(symbol, start, end):
    return get_ohlc(symbol, start, end or date.today().isoformat())


@click.group()
def cli():
    pass


@cli.command()
@_common_options
@click.option("--step", default=1.0, type=float, help="grid step %%")
@click.option("--target", default=1.0, type=float, help="profit target %%")
@click.option("--mode", default="classic", type=click.Choice(["classic", "dynamic"]))
@click.option("--dynamic-weights", default=None, help="comma-separated multiples of lot-size, shallow to deep")
@click.option("--idle-yield", default=0.0, type=float, help="annual %% on idle cash")
@click.option("--anchor", "anchor_mode", default="first_close", type=click.Choice(["first_close", "trailing"]))
@click.option("--brokerage", default=0.0, type=float, help="%% per side")
def run(symbol, start, end, capital, step, target, lot_size, mode, dynamic_weights, max_deploy,
        idle_yield, anchor_mode, brokerage, stcg_rate, ltcg_rate):
    """Run a single grid backtest and write output/<symbol>_<timestamp>/."""
    ohlc = _load(symbol, start, end)
    weights = [float(w) for w in dynamic_weights.split(",")] if dynamic_weights else None

    result = engine.run(
        symbol, ohlc, capital=capital, step=step, target=target, lot_size=lot_size, mode=mode,
        dynamic_weights=weights, max_deploy=max_deploy, idle_yield=idle_yield, anchor_mode=anchor_mode,
        brokerage=brokerage, stcg_rate=stcg_rate, ltcg_rate=ltcg_rate,
    )
    m = metrics.compute_metrics(result)
    bh = metrics.benchmark_buy_hold(ohlc, capital, ltcg_rate)

    report.print_summary(result, m, bh)
    run_dir = report.make_run_dir(symbol)
    report.write_run_artifacts(result, m, bh, run_dir)
    print(f"\nArtifacts written to {run_dir}")


@cli.command()
@_common_options
def sweep(symbol, start, end, capital, lot_size, max_deploy, stcg_rate, ltcg_rate):
    """Parameter sweep over step x target -> CSV matrix."""
    ohlc = _load(symbol, start, end)
    df = analysis.sweep(symbol, ohlc, capital, lot_size, max_deploy, stcg_rate=stcg_rate, ltcg_rate=ltcg_rate)
    run_dir = report.make_run_dir(symbol)
    df.to_csv(run_dir / "sweep.csv", index=False)
    print(df.to_string(index=False))
    print(f"\nWritten to {run_dir / 'sweep.csv'}")


@cli.command()
@_common_options
def walkforward(symbol, start, end, capital, lot_size, max_deploy, stcg_rate, ltcg_rate):
    """Optimize on in-sample data, validate on out-of-sample; flag overfitting."""
    ohlc = _load(symbol, start, end)
    result = analysis.walk_forward(symbol, ohlc, capital, lot_size, max_deploy)
    print(f"Best params: step={result['best_step']} target={result['best_target']}")
    print(f"In-sample post-tax CAGR: {result['in_sample_cagr_pct']:.2f}%")
    if result["out_sample_cagr_pct"] is not None:
        print(f"Out-of-sample post-tax CAGR: {result['out_sample_cagr_pct']:.2f}%")
    if result["overfitting_flag"]:
        print("WARNING: out-of-sample CAGR dropped >30% vs in-sample — possible overfitting.")


@cli.command()
@_common_options
@click.option("--step", default=1.0, type=float)
@click.option("--target", default=1.0, type=float)
def blend(symbol, start, end, capital, lot_size, max_deploy, stcg_rate, ltcg_rate, step, target):
    """50/50 buy-and-hold + grid blend vs 100% each."""
    ohlc = _load(symbol, start, end)
    result = analysis.blend_5050(
        symbol, ohlc, capital, step=step, target=target, lot_size=lot_size,
        max_deploy=max_deploy, stcg_rate=stcg_rate, ltcg_rate=ltcg_rate,
    )
    print(f"Combined 50/50 post-tax absolute return: {result['combined_post_tax_absolute_return_pct']:.2f}%")
    print(f"Grid-only post-tax CAGR: {result['grid_only_post_tax_cagr_pct']:.2f}%")
    print(f"Buy&hold-only post-tax CAGR: {result['buy_hold_only_post_tax_cagr_pct']:.2f}%")


if __name__ == "__main__":
    cli()
