from gridswing.grid import Grid, compute_levels, level_price


def test_level_price_math():
    # linear spacing: level_k = anchor * (1 - k*step/100)
    assert level_price(100, 1.0, 1) == 99.0
    assert abs(level_price(100, 1.0, 2) - 98.0) < 1e-9
    assert abs(level_price(100, 1.0, 3) - 97.0) < 1e-9


def test_compute_levels_respects_max_deploy():
    # capital 100000, lot 10000, max-deploy 50% -> 5 levels
    levels = compute_levels(100, 1.0, 100000, 50, 10000, "classic", None)
    assert levels == [1, 2, 3, 4, 5]


def test_buy_triggers_at_level_and_no_double_buy_same_level():
    g = Grid(100, step_pct=1.0, target_pct=1.0, capital=100000, max_deploy_pct=100,
              lot_size=10000, mode="classic", dynamic_weights=None, anchor_mode="first_close")
    bought = g.check_buy(99.0, "d1")
    assert len(bought) == 1 and bought[0].level == 1
    # same level, still no lower price -> no new buy
    bought2 = g.check_buy(99.0, "d2")
    assert bought2 == []


def test_gap_down_buys_multiple_levels_same_day():
    g = Grid(100, step_pct=1.0, target_pct=1.0, capital=100000, max_deploy_pct=100,
              lot_size=10000, mode="classic", dynamic_weights=None, anchor_mode="first_close")
    bought = g.check_buy(97.0, "d1")  # crosses levels 1,2,3 (99, 98.01, 97.03)
    assert [l.level for l in bought] == [1, 2, 3]


def test_sell_triggers_at_target_and_lot_independence():
    g = Grid(100, step_pct=1.0, target_pct=1.0, capital=100000, max_deploy_pct=100,
              lot_size=10000, mode="classic", dynamic_weights=None, anchor_mode="first_close")
    g.check_buy(99.0, "d1")
    g.check_buy(98.0, "d2")
    sold = g.check_sells(98.9)  # below both targets (99*1.01=99.99, 98*1.01=98.98) -> not yet
    assert sold == []
    sold = g.check_sells(100.0)  # meets both targets
    levels_sold = sorted(lot.level for lot, _fill_price in sold)
    assert levels_sold == [1, 2]
    assert g.open_lots == {}


def test_max_deploy_cap_blocks_further_buys():
    g = Grid(100, step_pct=1.0, target_pct=1.0, capital=100000, max_deploy_pct=20,
              lot_size=10000, mode="classic", dynamic_weights=None, anchor_mode="first_close")
    assert g.depths == [1, 2]
    bought = g.check_buy(90.0, "d1")  # would cross many levels but only 2 exist
    assert [l.level for l in bought] == [1, 2]


def test_capital_exhaustion_event_recorded():
    g = Grid(100, step_pct=1.0, target_pct=1.0, capital=100000, max_deploy_pct=10,
              lot_size=10000, mode="classic", dynamic_weights=None, anchor_mode="first_close")
    g.check_buy(90.0, "d1")  # buys the single available level, price still below it
    assert len(g.exhausted_events) == 1
    assert g.exhausted_events[0][0] == "d1"


def test_trailing_anchor_moves_up_with_new_high():
    g = Grid(100, step_pct=1.0, target_pct=1.0, capital=100000, max_deploy_pct=100,
              lot_size=10000, mode="classic", dynamic_weights=None, anchor_mode="trailing")
    g.update_anchor(110)
    assert g.anchor == 110
    g.update_anchor(105)  # lower than current anchor -> no change
    assert g.anchor == 110


def test_ratchet_anchor_unconditional_regardless_of_anchor_mode():
    # anchor_mode="first_close" no longer pins the anchor -- it always ratchets up.
    g = Grid(100, step_pct=1.0, target_pct=1.0, capital=100000, max_deploy_pct=100,
              lot_size=10000, mode="classic", dynamic_weights=None, anchor_mode="first_close")
    g.update_anchor(120)
    assert g.anchor == 120


def test_intraday_buy_fills_at_level_price_not_trigger():
    g = Grid(100, step_pct=1.0, target_pct=1.0, capital=100000, max_deploy_pct=100,
              lot_size=10000, mode="classic", dynamic_weights=None, anchor_mode="first_close",
              fills="intraday")
    # low dips to 97 (crossing levels 99, 98, 97) but each fills AT its own level price
    bought = g.check_buy(97.0, "d1")
    assert [lot.level for lot in bought] == [1, 2, 3]
    assert [lot.buy_price for lot in bought] == [99.0, 98.0, 97.0]


def test_intraday_sell_fills_at_target_price_not_trigger():
    g = Grid(100, step_pct=1.0, target_pct=1.0, capital=100000, max_deploy_pct=100,
              lot_size=10000, mode="classic", dynamic_weights=None, anchor_mode="first_close",
              fills="intraday")
    g.check_buy(99.0, "d1")  # buy_price = level price = 99.0
    sold = g.check_sells(150.0)  # high spikes well past target, but fill is AT the target
    assert len(sold) == 1
    lot, fill_price = sold[0]
    assert fill_price == 99.0 * 1.01
