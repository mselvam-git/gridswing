import pandas as pd

from gridswing import engine


def _ohlc(closes: list[float], start="2022-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=idx)


def test_simple_round_trip_no_lookahead():
    # anchor=100, buy at 99 (day2), sell at 100 (day3, >=99*1.01=99.99)
    df = _ohlc([100, 99, 100, 100])
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, mode="classic", max_deploy=100)
    assert len(res.trades) == 1
    t = res.trades.iloc[0]
    assert t["buy_price"] == 99.0 and t["sell_price"] == 100.0


def test_idle_yield_accrues_on_uninvested_cash():
    df = _ohlc([100] * 366)  # no trades at all, flat price
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, idle_yield=6.5, max_deploy=100)
    final_cash = res.equity_curve["equity"].iloc[-1]
    expected = 100000 * (1 + 6.5 / 100 / 365) ** 366
    assert abs(final_cash - expected) < 1.0


def test_tax_classification_at_365_days():
    # anchor=100 (day0), buy at 99 (day1), flat at 99 until day366 which hits target (100 >= 99*1.01)
    dates = pd.date_range("2022-01-01", periods=400, freq="D")
    closes = [100.0] + [99.0] * 365 + [100.0] + [100.0] * (400 - 367)
    df = pd.DataFrame({"Close": closes}, index=dates)
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, max_deploy=100)
    assert len(res.trades) == 1
    t = res.trades.iloc[0]
    holding_days = (t["sell_date"] - t["buy_date"]).days
    assert holding_days == 365
    assert t["tax_class"] == "LTCG"


def test_max_deploy_cap_limits_buys_across_days():
    df = _ohlc([100, 99, 98, 97, 96, 95])
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, max_deploy=20)
    # only 2 levels fit in a 20% budget
    assert res.grid.deployed_capital <= 100000 * 0.20 + 1e-6
