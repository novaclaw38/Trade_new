"""End-to-end cycle tests with an in-memory broker and fake market data."""
from datetime import datetime, timezone

import numpy as np
import pytest

from bot.ai_filter import Verdict
from bot.broker import SimBroker
from bot.engine import Engine
from bot.journal import Journal
from bot.risk import RiskLimits, RiskManager
from bot.state import BotState
from bot.strategy import StrategyParams
from tests.conftest import make_bars

NOW = datetime(2026, 3, 2, 16, 0, tzinfo=timezone.utc)


class FakeData:
    def __init__(self, bars, price):
        self.bars = bars
        self.price = price

    def get_bars(self, symbol):
        return self.bars

    def latest_price(self, symbol):
        return self.price


def make_engine(bars, price, cash=100_000.0, ai_filter=None, kill_switch_file=None, **limits):
    broker = SimBroker(cash)
    broker.set_price("SPY", price)
    state = BotState()
    engine = Engine(
        symbols=("SPY",), params=StrategyParams(), broker=broker, data=FakeData(bars, price),
        risk=RiskManager(RiskLimits(**limits), state), state=state, journal=Journal(None),
        ai_filter=ai_filter, kill_switch_file=kill_switch_file,
    )
    return engine, broker


def events(engine, kind):
    return [e for e in engine.journal.memory if e["event"] == kind]


def test_buy_places_order_with_stop_and_respects_risk(buy_bars):
    price = float(buy_bars.close.iloc[-1])
    engine, broker = make_engine(buy_bars, price)
    engine.run_once(NOW)

    pos = broker.get_positions()["SPY"]
    stop = engine.state.stops["SPY"]
    assert 0 < stop < price
    assert broker.orders[0].stop_price == stop
    # Loss if the stop is hit must be <= 1% of equity; position <= 10% of equity.
    assert pos.qty * (price - stop) <= 100_000 * 0.01 + 1e-6
    assert pos.qty * price <= 100_000 * 0.10 + 1e-6
    assert events(engine, "trade")[0]["side"] == "BUY"


def test_same_signal_bar_is_never_traded_twice(buy_bars):
    price = float(buy_bars.close.iloc[-1])
    engine, broker = make_engine(buy_bars, price)
    engine.run_once(NOW)
    broker.close_position("SPY")      # e.g. stopped out intraday
    engine.state.stops.clear()
    engine.run_once(NOW)              # same bars, same BUY signal
    assert len([o for o in broker.orders if o.side == "buy"]) == 1


def test_stop_loss_triggers_sell(buy_bars):
    price = float(buy_bars.close.iloc[-1])
    engine, broker = make_engine(buy_bars, price)
    engine.run_once(NOW)
    stop = engine.state.stops["SPY"]

    # Price drops through the stop; the strategy itself would still say HOLD.
    crash = stop - 1
    broker.set_price("SPY", crash)
    engine.data.price = crash
    engine.run_once(NOW)

    assert "SPY" not in broker.get_positions()
    assert "SPY" not in engine.state.stops
    assert events(engine, "stop_loss")
    sell = [t for t in events(engine, "trade") if t["side"] == "SELL"][0]
    assert sell["pnl"] < 0


def test_price_above_stop_keeps_position(buy_bars):
    price = float(buy_bars.close.iloc[-1])
    engine, broker = make_engine(buy_bars, price)
    engine.run_once(NOW)
    engine.data.price = engine.state.stops["SPY"] + 0.5
    broker.set_price("SPY", engine.data.price)
    engine.run_once(NOW)
    assert "SPY" in broker.get_positions()


def test_daily_loss_limit_halts_new_trades(buy_bars):
    price = float(buy_bars.close.iloc[-1])
    engine, broker = make_engine(buy_bars, price)
    broker.last_equity = 103_000  # equity is 100k now: down ~2.9% today, limit is 2%
    engine.run_once(NOW)

    assert engine.state.halted
    assert broker.get_positions() == {}
    assert events(engine, "halt")
    assert events(engine, "risk_check")[0]["approved"] is False


def test_exits_still_allowed_while_halted(buy_bars):
    price = float(buy_bars.close.iloc[-1])
    engine, broker = make_engine(buy_bars, price)
    engine.run_once(NOW)
    engine.state.halt("test halt")
    engine.data.price = engine.state.stops["SPY"] - 1
    broker.set_price("SPY", engine.data.price)
    engine.run_once(NOW)
    assert "SPY" not in broker.get_positions()


def test_position_without_recorded_stop_is_closed(buy_bars):
    price = float(buy_bars.close.iloc[-1])
    engine, broker = make_engine(buy_bars, price)
    engine.run_once(NOW)
    engine.state.stops.clear()  # e.g. state file lost
    engine.run_once(NOW)
    assert "SPY" not in broker.get_positions()


def test_kill_switch_file_blocks_entries(buy_bars, tmp_path):
    switch = tmp_path / "STOP"
    switch.touch()
    engine, broker = make_engine(buy_bars, float(buy_bars.close.iloc[-1]), kill_switch_file=switch)
    engine.run_once(NOW)
    assert broker.get_positions() == {}


def test_ai_veto_blocks_trade(buy_bars):
    class Veto:
        def review(self, symbol, summary):
            return Verdict(False, "Bad news")

    engine, broker = make_engine(buy_bars, float(buy_bars.close.iloc[-1]), ai_filter=Veto())
    engine.run_once(NOW)
    assert broker.get_positions() == {}
    assert events(engine, "ai_review")[0]["allow"] is False


def test_market_closed_does_nothing(buy_bars):
    engine, broker = make_engine(buy_bars, float(buy_bars.close.iloc[-1]))
    broker.open = False
    assert engine.run_once(NOW) == []
    assert broker.orders == []


def test_data_error_means_no_trade(buy_bars):
    engine, broker = make_engine(buy_bars, float(buy_bars.close.iloc[-1]))

    def boom(symbol):
        raise ConnectionError("feed down")

    engine.data.get_bars = boom
    engine.run_once(NOW)
    assert broker.orders == []
    assert events(engine, "error")


def test_no_trade_without_signal():
    bars = make_bars(np.linspace(100, 160, 260))  # uptrend but no fresh crossover
    engine, broker = make_engine(bars, 160.0)
    engine.run_once(NOW)
    assert broker.orders == []


def test_dashboard_renders(buy_bars):
    from bot.dashboard import render

    engine, broker = make_engine(buy_bars, float(buy_bars.close.iloc[-1]))
    signals = engine.run_once(NOW)
    text = render(mode="paper", account=broker.get_account(), positions=broker.get_positions(),
                  state=engine.state, journal=engine.journal, signals=signals, market_open=True)
    assert "PAPER" in text and "SPY" in text and "BUY" in text


# --- Config: paper by default, live requires explicit acknowledgement -----------
@pytest.fixture
def clean_env(monkeypatch):
    for k in ("TRADING_MODE", "LIVE_TRADING_ACKNOWLEDGED", "RISK_PER_TRADE_PCT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    return monkeypatch


def test_paper_mode_is_default(clean_env):
    from bot.config import load_config

    assert load_config(env_file=None).is_live is False


def test_live_mode_requires_acknowledgement(clean_env):
    from bot.config import LIVE_ACK_PHRASE, ConfigError, load_config

    clean_env.setenv("TRADING_MODE", "live")
    with pytest.raises(ConfigError):
        load_config(env_file=None)
    clean_env.setenv("LIVE_TRADING_ACKNOWLEDGED", "yes")
    with pytest.raises(ConfigError):
        load_config(env_file=None)
    clean_env.setenv("LIVE_TRADING_ACKNOWLEDGED", LIVE_ACK_PHRASE)
    assert load_config(env_file=None).is_live


def test_config_rejects_reckless_risk(clean_env):
    from bot.config import ConfigError, load_config

    clean_env.setenv("RISK_PER_TRADE_PCT", "10")
    with pytest.raises(ConfigError):
        load_config(env_file=None)
