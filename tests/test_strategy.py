import numpy as np

from bot.strategy import Action, StrategyParams, compute_indicators, generate_signal
from tests.conftest import make_bars


def test_buy_on_fresh_crossover_in_uptrend(buy_bars, params):
    sig = generate_signal("TEST", buy_bars, params, in_position=False)
    assert sig.action is Action.BUY
    ind = compute_indicators(buy_bars, params).iloc[-1]
    assert ind.sma_fast > ind.sma_slow and ind.close > ind.sma_trend
    # Stop is 2 x ATR below the close.
    assert sig.stop_price == round(ind.close - 2 * ind.atr, 2)
    assert sig.stop_price < sig.close


def test_no_buy_the_bar_after_the_crossover(buy_bars, params):
    """Only the crossover bar itself is an entry; we never chase later."""
    from tests.conftest import uptrend_pullback_rally

    longer = make_bars(uptrend_pullback_rally()).iloc[: len(buy_bars) + 1]
    sig = generate_signal("TEST", longer, params, in_position=False)
    assert sig.action is Action.HOLD
    assert "No fresh crossover" in sig.reasons[0]


def test_hold_when_not_enough_history(params):
    bars = make_bars(np.linspace(100, 120, 100))
    sig = generate_signal("TEST", bars, params, in_position=False)
    assert sig.action is Action.HOLD
    assert "Not enough history" in sig.reasons[0]


def test_hold_on_empty_data(params):
    assert generate_signal("TEST", None, params, in_position=False).action is Action.HOLD


def test_no_buy_below_long_term_trend(params):
    """Crossover inside a long-term downtrend is ignored."""
    down = np.linspace(200, 100, 260)
    dip = np.linspace(100, 90, 30)
    bounce = np.linspace(90, 110, 40)
    bars = make_bars(np.concatenate([down, dip[1:], bounce[1:]]))
    ind = compute_indicators(bars, params)
    actions = set()
    for i in range(params.min_bars, len(bars)):
        actions.add(generate_signal("TEST", bars.iloc[: i + 1], params, False).action)
        if ind.sma_fast.iloc[i] > ind.sma_slow.iloc[i] and ind.sma_fast.iloc[i - 1] <= ind.sma_slow.iloc[i - 1]:
            sig = generate_signal("TEST", bars.iloc[: i + 1], params, False)
            assert "downtrend" in sig.reasons[0]
    assert Action.BUY not in actions


def test_no_buy_when_too_volatile(buy_bars):
    tight = StrategyParams(max_atr_pct=0.0001)
    sig = generate_signal("TEST", buy_bars, tight, in_position=False)
    assert sig.action is Action.HOLD
    assert "volatility too high" in sig.reasons[0]


def test_sell_when_fast_drops_below_slow(params):
    up = np.linspace(100, 160, 260)
    down = np.linspace(160, 140, 40)
    bars = make_bars(np.concatenate([up, down[1:]]))
    assert generate_signal("TEST", bars, params, in_position=True).action is Action.SELL


def test_hold_position_while_trend_intact(params):
    bars = make_bars(np.linspace(100, 160, 260))
    assert generate_signal("TEST", bars, params, in_position=True).action is Action.HOLD


def test_indicators_do_not_look_ahead(params):
    bars = make_bars(np.linspace(100, 160, 260))
    full = compute_indicators(bars, params)
    partial = compute_indicators(bars.iloc[:220], params)
    assert full.iloc[:220][["sma_fast", "sma_slow", "sma_trend", "atr"]].equals(
        partial[["sma_fast", "sma_slow", "sma_trend", "atr"]]
    )
