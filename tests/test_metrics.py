import pandas as pd

from gridswing import engine, metrics


def _ohlc(closes: list[float], start="2022-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=idx)


def test_metrics_absolute_return_and_drawdown():
    df = _ohlc([100, 99, 100, 100])
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, max_deploy=100)
    m = metrics.compute_metrics(res)
    assert m["absolute_return_pct"] > 0
    assert m["num_round_trip_trades"] == 1
    assert m["win_rate_pct"] == 100.0


def test_post_tax_lower_than_gross_when_stcg():
    df = _ohlc([100, 99, 100, 100])
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, max_deploy=100, stcg_rate=30)
    m = metrics.compute_metrics(res)
    assert m["post_tax_absolute_return_pct"] < m["absolute_return_pct"]
    assert m["total_tax"] > 0


def test_benchmark_buy_hold():
    df = _ohlc([100, 110, 120])
    b = metrics.benchmark_buy_hold(df, capital=100000, ltcg_rate=12.5)
    assert abs(b["absolute_return_pct"] - 20.0) < 1e-9
    assert b["post_tax_absolute_return_pct"] < b["absolute_return_pct"]
