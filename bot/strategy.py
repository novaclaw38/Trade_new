"""Signal engine: a long-only moving-average crossover with a trend filter.

WHY THIS STRATEGY (and why it's comparatively low-risk)
------------------------------------------------------
* Trend-following on a broad, liquid ETF (SPY by default) is one of the most
  studied, least exotic retail strategies. It doesn't try to predict tops or
  bottoms; it only participates once a trend is already visible.
* Long-only: no short selling, so a loss on a position is limited to the
  stop distance (plus any overnight gap). No leverage, no options.
* It trades rarely (a handful of times a year on daily bars), which keeps
  costs low and leaves plenty of time to watch what it's doing.
* Every rule is a plain inequality you can check on a chart. No black box.
* When in doubt it does nothing: missing data, a long-term downtrend, or
  unusually high volatility all produce HOLD.

Expect it to lose money in choppy, sideways markets (several small stopped-out
trades) and to make money only when a sustained trend develops. That trade-off
is the honest reality of trend-following.

RULES (evaluated on completed bars only)
----------------------------------------
Entry (BUY), all must be true:
  1. Fast SMA (20) crossed ABOVE slow SMA (50) on the latest bar.
  2. Close is above the long-term trend SMA (200): only buy in an uptrend.
  3. Volatility is normal: ATR(14) / close <= MAX_ATR_PCT.
Exit (SELL), any is enough:
  a. Fast SMA is back BELOW slow SMA (trend over), or
  b. Price hits the stop-loss (checked by the engine against the live price,
     and also held as a stop order at the broker).
Stop-loss: entry close - ATR_STOP_MULT (2) x ATR(14). Using ATR means the stop
  sits outside normal day-to-day noise for that particular instrument.
Position size: decided by bot.risk, never here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class StrategyParams:
    fast_sma: int = 20
    slow_sma: int = 50
    trend_sma: int = 200
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    max_atr_pct: float = 0.04

    def __post_init__(self) -> None:
        if not (1 < self.fast_sma < self.slow_sma <= self.trend_sma):
            raise ValueError("Need 1 < FAST_SMA < SLOW_SMA <= TREND_SMA.")
        if self.atr_period < 2 or self.atr_stop_mult <= 0 or self.max_atr_pct <= 0:
            raise ValueError("ATR_PERIOD must be >= 2; ATR_STOP_MULT and MAX_ATR_PCT must be > 0.")

    @property
    def min_bars(self) -> int:
        return max(self.trend_sma, self.atr_period) + 2


@dataclass
class Signal:
    symbol: str
    action: Action
    reasons: list[str]
    close: float | None = None
    stop_price: float | None = None
    bar_time: str = ""
    indicators: dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        return f"{self.symbol} {self.action.value}: " + "; ".join(self.reasons)


def compute_indicators(bars: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """Adds sma_fast, sma_slow, sma_trend and atr columns. Rolling windows use
    only past data, so value at row i never looks ahead."""
    df = bars.copy()
    close = df["close"]
    df["sma_fast"] = close.rolling(p.fast_sma).mean()
    df["sma_slow"] = close.rolling(p.slow_sma).mean()
    df["sma_trend"] = close.rolling(p.trend_sma).mean()
    prev_close = close.shift(1)
    true_range = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    df["atr"] = true_range.rolling(p.atr_period).mean()
    return df


def evaluate_at(symbol: str, ind: pd.DataFrame, i: int, p: StrategyParams, in_position: bool) -> Signal:
    """Apply the rules to row `i` of a frame from `compute_indicators`."""
    if i + 1 < p.min_bars:
        return Signal(symbol, Action.HOLD, [f"Not enough history ({i + 1}/{p.min_bars} bars)."])

    row, prev = ind.iloc[i], ind.iloc[i - 1]
    cols = ["close", "sma_fast", "sma_slow", "sma_trend", "atr"]
    if row[cols].isna().any() or prev[["sma_fast", "sma_slow"]].isna().any():
        return Signal(symbol, Action.HOLD, ["Indicator data missing; doing nothing."])

    close, fast, slow, trend, atr = (float(row[c]) for c in cols)
    sig = Signal(
        symbol,
        Action.HOLD,
        [],
        close=close,
        bar_time=str(ind.index[i]),
        indicators={"sma_fast": round(fast, 2), "sma_slow": round(slow, 2),
                    "sma_trend": round(trend, 2), "atr": round(atr, 2)},
    )

    if in_position:
        if fast < slow:
            sig.action = Action.SELL
            sig.reasons.append(f"Trend ended: SMA{p.fast_sma} {fast:.2f} fell below SMA{p.slow_sma} {slow:.2f}.")
        else:
            sig.reasons.append("Holding: trend still intact.")
        return sig

    crossed_up = prev["sma_fast"] <= prev["sma_slow"] and fast > slow
    if not crossed_up:
        state = "above" if fast > slow else "below"
        sig.reasons.append(f"No fresh crossover (SMA{p.fast_sma} is {state} SMA{p.slow_sma}).")
        return sig
    if close <= trend:
        sig.reasons.append(f"Crossover, but close {close:.2f} is below SMA{p.trend_sma} {trend:.2f} (long-term downtrend). Skipping.")
        return sig
    if atr / close > p.max_atr_pct:
        sig.reasons.append(f"Crossover, but volatility too high (ATR {atr / close:.2%} of price > {p.max_atr_pct:.2%}). Skipping.")
        return sig

    sig.action = Action.BUY
    sig.stop_price = round(close - p.atr_stop_mult * atr, 2)
    sig.reasons += [
        f"SMA{p.fast_sma} crossed above SMA{p.slow_sma}.",
        f"Close {close:.2f} above SMA{p.trend_sma} {trend:.2f} (uptrend).",
        f"Stop-loss at {sig.stop_price:.2f} ({p.atr_stop_mult}x ATR below close).",
    ]
    return sig


def generate_signal(symbol: str, bars: pd.DataFrame, p: StrategyParams, in_position: bool) -> Signal:
    if bars is None or len(bars) == 0:
        return Signal(symbol, Action.HOLD, ["No market data; doing nothing."])
    ind = compute_indicators(bars, p)
    return evaluate_at(symbol, ind, len(ind) - 1, p, in_position)
