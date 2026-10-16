import pytest

from bot.broker import SimBroker, require_protective_stop
from bot.risk import RiskLimits, RiskManager
from bot.state import BotState


def make_rm(**limits) -> RiskManager:
    return RiskManager(RiskLimits(**limits), BotState())


# --- Max loss per trade & position size -------------------------------------
def test_size_limited_by_risk_per_trade():
    rm = make_rm(risk_per_trade=0.01, max_position_pct=0.25)
    # $100k equity, risk 1% = $1,000. Entry 100, stop 95 -> $5/share -> 200 shares.
    d = rm.position_size(equity=100_000, entry=100, stop=95, buying_power=100_000)
    assert d.approved and d.qty == 200
    assert d.qty * (100 - 95) <= 100_000 * 0.01


def test_size_limited_by_max_position_pct():
    rm = make_rm(risk_per_trade=0.01, max_position_pct=0.10)
    # Tight stop would allow 1,000 shares ($100k), but the 10% cap allows only 100.
    d = rm.position_size(equity=100_000, entry=100, stop=99, buying_power=100_000)
    assert d.approved and d.qty == 100
    assert d.qty * 100 <= 100_000 * 0.10


def test_size_limited_by_buying_power():
    rm = make_rm()
    d = rm.position_size(equity=100_000, entry=100, stop=95, buying_power=1_000)
    assert d.qty == 10


def test_rejects_when_less_than_one_share():
    rm = make_rm()
    d = rm.position_size(equity=1_000, entry=500, stop=400, buying_power=1_000)
    assert not d.approved and d.qty == 0


# --- Stop-loss is mandatory ---------------------------------------------------
@pytest.mark.parametrize("stop", [None, 0, -1, 100, 105, float("nan")])
def test_rejects_missing_or_invalid_stop(stop):
    d = make_rm().position_size(equity=100_000, entry=100, stop=stop, buying_power=100_000)
    assert not d.approved


@pytest.mark.parametrize("stop", [None, 0, 100, 120])
def test_broker_refuses_entry_without_stop(stop):
    with pytest.raises(ValueError):
        require_protective_stop(100, stop)
    broker = SimBroker()
    broker.set_price("SPY", 100)
    with pytest.raises(ValueError):
        broker.buy_with_stop("SPY", 1, stop)
    assert broker.get_positions() == {}


# --- Hard ceilings can't be configured away ----------------------------------
@pytest.mark.parametrize("kwargs", [
    {"risk_per_trade": 0.05},
    {"risk_per_trade": 0},
    {"max_position_pct": 0.5},
    {"daily_loss_limit": 0.10},
    {"daily_loss_limit": -0.01},
    {"max_open_positions": 0},
    {"max_open_positions": 50},
])
def test_limits_above_hard_ceiling_are_rejected(kwargs):
    with pytest.raises(ValueError):
        RiskLimits(**kwargs)


# --- Daily loss limit -----------------------------------------------------------
def test_daily_limit_not_triggered_below_threshold():
    rm = make_rm(daily_loss_limit=0.02)
    rm.update_daily_status(equity=98_100, day_start_equity=100_000, today="2026-01-05")
    assert not rm.state.halted


def test_daily_limit_triggers_at_threshold_and_blocks_entries():
    rm = make_rm(daily_loss_limit=0.02)
    rm.update_daily_status(equity=98_000, day_start_equity=100_000, today="2026-01-05")
    assert rm.state.halted
    d = rm.approve_entry(equity=98_000, buying_power=98_000, entry=100, stop=95, open_positions=0)
    assert not d.approved and "halted" in d.reason.lower()


def test_daily_halt_stays_even_if_equity_recovers_same_day():
    rm = make_rm(daily_loss_limit=0.02)
    rm.update_daily_status(equity=97_000, day_start_equity=100_000, today="2026-01-05")
    rm.update_daily_status(equity=101_000, day_start_equity=100_000, today="2026-01-05")
    assert rm.state.halted


def test_daily_halt_survives_restart(tmp_path):
    path = tmp_path / "state.json"
    rm = RiskManager(RiskLimits(daily_loss_limit=0.02), BotState.load(path))
    rm.update_daily_status(equity=97_000, day_start_equity=100_000, today="2026-01-05")
    restarted = RiskManager(RiskLimits(daily_loss_limit=0.02), BotState.load(path))
    restarted.update_daily_status(equity=99_000, day_start_equity=100_000, today="2026-01-05")
    assert restarted.state.halted


def test_daily_halt_resets_next_day():
    rm = make_rm(daily_loss_limit=0.02)
    rm.update_daily_status(equity=97_000, day_start_equity=100_000, today="2026-01-05")
    rm.update_daily_status(equity=97_000, day_start_equity=97_000, today="2026-01-06")
    assert not rm.state.halted


def test_unknown_start_equity_halts():
    rm = make_rm()
    rm.update_daily_status(equity=100_000, day_start_equity=0, today="2026-01-05")
    assert rm.state.halted


# --- Other gates ----------------------------------------------------------------
def test_kill_switch_blocks_entries():
    d = make_rm().approve_entry(equity=1e5, buying_power=1e5, entry=100, stop=95, open_positions=0, kill_switch=True)
    assert not d.approved and "Kill switch" in d.reason


def test_max_open_positions_blocks_entries():
    d = make_rm(max_open_positions=2).approve_entry(equity=1e5, buying_power=1e5, entry=100, stop=95, open_positions=2)
    assert not d.approved


# --- No leverage: margin buying power is never used ----------------------------
def _alpaca_with_account(**fields):
    from types import SimpleNamespace

    from bot.broker import AlpacaBroker

    broker = AlpacaBroker.__new__(AlpacaBroker)  # skip real client construction
    broker.client = SimpleNamespace(get_account=lambda: SimpleNamespace(**fields))
    return broker


def test_alpaca_buying_power_excludes_margin():
    broker = _alpaca_with_account(equity="100000", last_equity="100000", buying_power="400000", cash="100000")
    assert broker.get_account().buying_power == 100_000


def test_alpaca_buying_power_never_negative_when_cash_borrowed():
    broker = _alpaca_with_account(equity="100000", last_equity="100000", buying_power="250000", cash="-5000")
    assert broker.get_account().buying_power == 0
