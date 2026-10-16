"""The scorecard compares account equity with the same money held in the benchmark."""
import pytest

from bot.scorecard import compute

CLOSES = [("2026-01-02", 100.0), ("2026-01-05", 110.0), ("2026-01-06", 99.0), ("2026-01-08", 121.0)]


def test_benchmark_tracks_the_same_starting_money():
    history = [("2026-01-02", 1000.0), ("2026-01-05", 1010.0), ("2026-01-06", 1005.0), ("2026-01-08", 1020.0)]
    r = compute(history, CLOSES, "SPY", "2026-01-08")
    assert r["curve"] == [["2026-01-02", 1000.0, 1000.0], ["2026-01-05", 1010.0, 1100.0],
                          ["2026-01-06", 1005.0, 990.0], ["2026-01-08", 1020.0, 1210.0]]
    assert r["bot_return"] == pytest.approx(0.02)
    assert r["benchmark_return"] == pytest.approx(0.21)
    assert r["difference"] == pytest.approx(-190.0)
    assert r["days"] == 6
    assert r["benchmark_max_drawdown"] == pytest.approx(990 / 1100 - 1)
    assert r["bot_max_drawdown"] == pytest.approx(1005 / 1010 - 1)


def test_days_without_a_close_use_the_previous_one():
    # Jan 7 had no benchmark bar (a holiday, say): it reuses Jan 6's close.
    history = [("2026-01-06", 500.0), ("2026-01-07", 501.0)]
    r = compute(history, CLOSES, "SPY", "2026-01-07")
    assert r["curve"][1] == ["2026-01-07", 501.0, 500.0]


def test_start_before_first_close_and_empty_inputs():
    r = compute([("2026-01-01", 100.0)], CLOSES, "SPY", "2026-01-01")
    assert r["benchmark_return"] == 0 and r["days"] == 0
    with pytest.raises(ValueError):
        compute([], CLOSES, "SPY", "2026-01-01")
    with pytest.raises(ValueError):
        compute([("2026-01-01", 100.0)], [], "SPY", "2026-01-01")
