import pandas as pd
import pytest

from gridswing import engine


def _ohlc(closes: list[float], start="2022-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=idx)


def test_simple_round_trip_no_lookahead():
    # anchor=100, buy at 99 (day2), sell at 100 (day3, >=99*1.01=99.99). fills="close": legacy semantics.
    # slippage/fill_buffer zeroed to test the pure fill mechanics in isolation.
    df = _ohlc([100, 99, 100, 100])
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, mode="classic",
                      max_deploy=100, fills="close", slippage=0, fill_buffer=0)
    assert len(res.trades) == 1
    t = res.trades.iloc[0]
    assert t["buy_price"] == 99.0 and t["sell_price"] == 100.0


def test_idle_yield_accrues_on_uninvested_cash():
    df = _ohlc([100] * 366)  # no trades at all, flat price
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, idle_yield=6.5,
                      max_deploy=100, fills="close", slippage=0, fill_buffer=0)
    final_cash = res.equity_curve["equity"].iloc[-1]
    expected = 100000 * (1 + 6.5 / 100 / 365) ** 366
    assert abs(final_cash - expected) < 1.0


def test_tax_classification_at_365_days():
    # anchor=100 (day0), buy at 99 (day1), flat at 99 until day366 which hits target (100 >= 99*1.01)
    dates = pd.date_range("2022-01-01", periods=400, freq="D")
    closes = [100.0] + [99.0] * 365 + [100.0] + [100.0] * (400 - 367)
    df = pd.DataFrame({"Close": closes}, index=dates)
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, max_deploy=100,
                      fills="close", slippage=0, fill_buffer=0)
    assert len(res.trades) == 1
    t = res.trades.iloc[0]
    holding_days = (t["sell_date"] - t["buy_date"]).days
    assert holding_days == 365
    assert t["tax_class"] == "LTCG"


def test_max_deploy_cap_limits_buys_across_days():
    df = _ohlc([100, 99, 98, 97, 96, 95])
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, max_deploy=20,
                      fills="close", slippage=0, fill_buffer=0)
    # only 2 levels fit in a 20% budget
    assert res.grid.deployed_capital <= 100000 * 0.20 + 1e-6


def test_intraday_is_the_default():
    # a Close-only frame has no High/Low, so the default fills="intraday" must reject it.
    df = _ohlc([100, 99, 100, 100])
    with pytest.raises(ValueError, match="High and Low"):
        engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, max_deploy=100)


def test_intraday_buy_fills_at_level_and_same_day_sell_on_high():
    dates = pd.date_range("2022-01-01", periods=3, freq="D")
    # day0: anchor=100 (O=H=L=C=100). day1: low dips to 99 (buy fills at level 99), high
    # spikes to 101 same day -- target is 99*1.01=99.99, so the same lot can sell today.
    df = pd.DataFrame(
        {"Open": [100, 100, 100], "High": [100, 101, 100], "Low": [100, 99, 100], "Close": [100, 100, 100]},
        index=dates,
    )
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, max_deploy=100,
                      fills="intraday", slippage=0, fill_buffer=0)
    assert len(res.trades) == 1
    t = res.trades.iloc[0]
    assert t["buy_price"] == 99.0  # filled at the level price, not the low
    assert t["sell_price"] == 99.0 * 1.01  # filled at the target price, not the high
    assert t["buy_date"] == t["sell_date"]  # same-day round trip


def test_intraday_requires_high_low_columns():
    df = _ohlc([100, 99, 100])
    with pytest.raises(ValueError, match="High and Low"):
        engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, fills="intraday")


def test_slippage_worsens_fill_prices():
    dates = pd.date_range("2022-01-01", periods=3, freq="D")
    df = pd.DataFrame(
        {"Open": [100, 100, 100], "High": [100, 101, 100], "Low": [100, 99, 100], "Close": [100, 100, 100]},
        index=dates,
    )
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, max_deploy=100,
                      fills="intraday", slippage=1.0, fill_buffer=0)
    assert len(res.trades) == 1
    t = res.trades.iloc[0]
    buy_price = 99.0 * 1.01  # paid 1% more than the level price
    target_price = buy_price * 1.01  # target is relative to the actual (slipped) buy price
    assert abs(t["buy_price"] - buy_price) < 1e-9
    assert abs(t["sell_price"] - target_price * 0.99) < 1e-9  # received 1% less than the target price


def test_fill_buffer_blocks_a_bare_touch():
    dates = pd.date_range("2022-01-01", periods=2, freq="D")
    # low touches exactly the level (99.0) but doesn't clear the 0.05% buffer below it.
    df = pd.DataFrame({"Open": [100, 100], "High": [100, 100], "Low": [100, 99.0], "Close": [100, 100]}, index=dates)
    res = engine.run("TEST", df, capital=100000, step=1.0, target=1.0, lot_size=10000, max_deploy=100,
                      fills="intraday", slippage=0, fill_buffer=0.0005)
    assert len(res.grid.open_lots) == 0
    assert len(res.trades) == 0
