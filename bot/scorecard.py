"""Paper-trading scorecard: is the bot beating simply holding the index?

Compares the account's real daily equity with what the same starting money
would be worth had it all gone into the benchmark (default SPY) on day one.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import date

# Suggested paper-trading time before risking real money (see the user guide).
RECOMMENDED_PAPER_DAYS = 90


def _max_drawdown(values: list[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for v in values:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def compute(history: list[tuple[str, float]], closes: list[tuple[str, float]], symbol: str, today: str) -> dict:
    """`history` = daily (date, equity), `closes` = benchmark (date, close); both ISO dates, oldest first."""
    if not history:
        raise ValueError("The account has no history yet.")
    if not closes:
        raise ValueError(f"No {symbol} prices to compare against.")
    start, e0 = history[0]
    days = [d for d, _ in closes]

    def close_on(d: str) -> float:
        # The latest close on or before d; before the first one, the first one.
        i = bisect_right(days, d)
        return closes[max(i - 1, 0)][1]

    base = close_on(start)
    curve = [[d, round(e, 2), round(e0 * close_on(d) / base, 2)] for d, e in history]
    bot_end, bench_end = curve[-1][1], curve[-1][2]
    return {
        "start_date": start,
        "days": (date.fromisoformat(today) - date.fromisoformat(start)).days,
        "recommended_days": RECOMMENDED_PAPER_DAYS,
        "start_equity": e0,
        "equity": bot_end,
        "bot_return": bot_end / e0 - 1,
        "benchmark": symbol,
        "benchmark_equity": bench_end,
        "benchmark_return": bench_end / e0 - 1,
        "difference": bot_end - bench_end,
        "bot_max_drawdown": _max_drawdown([c[1] for c in curve]),
        "benchmark_max_drawdown": _max_drawdown([c[2] for c in curve]),
        "curve": curve,
    }
