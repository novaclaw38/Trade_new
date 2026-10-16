import numpy as np
import pandas as pd
import pytest

from bot.strategy import Action, StrategyParams, compute_indicators, evaluate_at

PARAMS = StrategyParams()


def make_bars(closes, spread=0.005) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="B", tz="UTC")
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame(
        {"open": opens, "high": np.maximum(opens, closes) * (1 + spread),
         "low": np.minimum(opens, closes) * (1 - spread), "close": closes},
        index=idx,
    )


def uptrend_pullback_rally() -> np.ndarray:
    """Long uptrend, a pullback that pushes SMA20 under SMA50, then a rally
    that crosses back above while price is still above SMA200."""
    up = np.linspace(100, 160, 260)
    down = np.linspace(160, 146, 40)
    rally = np.linspace(146, 175, 60)
    return np.concatenate([up, down[1:], rally[1:]])


@pytest.fixture
def params():
    return PARAMS


@pytest.fixture
def buy_bars():
    """Bars ending exactly on the first BUY signal after the pullback."""
    bars = make_bars(uptrend_pullback_rally())
    ind = compute_indicators(bars, PARAMS)
    for i in range(280, len(ind)):
        if evaluate_at("TEST", ind, i, PARAMS, in_position=False).action is Action.BUY:
            return bars.iloc[: i + 1]
    raise AssertionError("synthetic data never produced a BUY; fixture is broken")
