"""`python -m bot check` reports each service separately and never trades."""
from types import SimpleNamespace

import pandas as pd

from bot.ai_filter import AIConfirmation
from bot.broker import SimBroker
from bot.check import FAIL, OK, SKIP, format_results, run_checks
from tests.test_ai_filter import FakeGroq

CFG = SimpleNamespace(symbols=("SPY",), trading_mode="paper", timeframe="1Day",
                      data_feed="iex", ai_model="m", ai_confirmation=False)


class FakeData:
    def __init__(self, bars=None, error=None):
        idx = pd.date_range("2026-10-01", periods=5, freq="B", tz="UTC")
        self.bars = pd.DataFrame({"close": range(5)}, index=idx) if bars is None else bars
        self.error = error

    def get_bars(self, symbol, lookback_bars=260):
        if self.error:
            raise self.error
        return self.bars

    def recent_headlines(self, symbol):
        return ["a", "b"]


def statuses(results):
    return {r.name: r.status for r in results}


def ai(content='{"verdict": "allow", "reason": "ok"}', error=None):
    return lambda: AIConfirmation("m", lambda s: [], client=FakeGroq(content, error))


def test_all_ok_with_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "x")
    broker = SimBroker()
    results = run_checks(CFG, broker, FakeData(), ai())
    assert set(statuses(results).values()) == {OK}
    assert broker.orders == []
    assert "All checks passed" in format_results(results)


def test_groq_skipped_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert statuses(run_checks(CFG, SimBroker(), FakeData(), ai()))["Groq AI"] == SKIP


def test_groq_required_when_ai_enabled(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cfg = SimpleNamespace(**{**vars(CFG), "ai_confirmation": True})
    assert statuses(run_checks(cfg, SimBroker(), FakeData(), ai()))["Groq AI"] == FAIL


def test_groq_error_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "x")
    results = run_checks(CFG, SimBroker(), FakeData(), ai(error=PermissionError("invalid key")))
    groq = next(r for r in results if r.name == "Groq AI")
    assert groq.status == FAIL and "invalid key" in groq.detail


def test_data_failures_are_isolated(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    s = statuses(run_checks(CFG, SimBroker(), FakeData(error=ConnectionError("down")), ai()))
    assert s["Alpaca market data"] == FAIL and s["Alpaca account"] == OK
    s = statuses(run_checks(CFG, SimBroker(), FakeData(bars=pd.DataFrame()), ai()))
    assert s["Alpaca market data"] == FAIL
