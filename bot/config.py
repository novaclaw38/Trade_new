"""Configuration, read from environment variables (optionally via a `.env` file).

Secrets are never hardcoded. Copy `.env.example` to `.env` and fill it in.
Percentages in the environment are written as percents (1.0 == 1%).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from bot.risk import RiskLimits
from bot.strategy import StrategyParams

# Live trading needs TRADING_MODE=live AND this exact phrase, so it can't happen by accident.
LIVE_ACK_PHRASE = "I_UNDERSTAND_I_CAN_LOSE_REAL_MONEY"
SUPPORTED_TIMEFRAMES = ("1Day", "1Hour")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    alpaca_api_key: str
    alpaca_secret_key: str
    trading_mode: str            # "paper" (default) or "live"
    symbols: tuple[str, ...]
    timeframe: str
    data_feed: str               # "iex" is free; "sip" needs a paid Alpaca data plan
    strategy: StrategyParams
    risk: RiskLimits
    ai_confirmation: bool
    ai_model: str
    loop_minutes: int
    data_dir: Path
    kill_switch_file: Path
    benchmark_symbol: str = "SPY"   # what the scorecard compares the bot against

    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"


def _get(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _num(name: str, default: str, cast=float):
    raw = _get(name, default)
    try:
        return cast(raw)
    except ValueError:
        raise ConfigError(f"{name}={raw!r} is not a valid number") from None


def _bool(name: str, default: str = "false") -> bool:
    return _get(name, default).lower() in ("1", "true", "yes", "on")


def load_config(env_file: str | os.PathLike | None = ".env", require_keys: bool = True) -> Config:
    if env_file:
        load_dotenv(env_file, override=False)

    mode = _get("TRADING_MODE", "paper").lower()
    if mode not in ("paper", "live"):
        raise ConfigError(f"TRADING_MODE must be 'paper' or 'live', got {mode!r}")
    if mode == "live" and _get("LIVE_TRADING_ACKNOWLEDGED", "") != LIVE_ACK_PHRASE:
        raise ConfigError(
            "TRADING_MODE=live requires LIVE_TRADING_ACKNOWLEDGED="
            f"{LIVE_ACK_PHRASE}. Stay in paper mode until you've watched the bot for weeks."
        )

    key, secret = _get("ALPACA_API_KEY", ""), _get("ALPACA_SECRET_KEY", "")
    if require_keys and not (key and secret):
        raise ConfigError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set (see .env.example).")

    symbols = tuple(s.strip().upper() for s in _get("SYMBOLS", "SPY").split(",") if s.strip())
    if not symbols:
        raise ConfigError("SYMBOLS must list at least one ticker.")

    timeframe = _get("TIMEFRAME", "1Day")
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ConfigError(f"TIMEFRAME must be one of {SUPPORTED_TIMEFRAMES}, got {timeframe!r}")

    try:
        strategy = StrategyParams(
            fast_sma=_num("FAST_SMA", "20", int),
            slow_sma=_num("SLOW_SMA", "50", int),
            trend_sma=_num("TREND_SMA", "200", int),
            atr_period=_num("ATR_PERIOD", "14", int),
            atr_stop_mult=_num("ATR_STOP_MULT", "2.0"),
            max_atr_pct=_num("MAX_ATR_PCT", "4.0") / 100,
        )
        risk = RiskLimits(
            risk_per_trade=_num("RISK_PER_TRADE_PCT", "1.0") / 100,
            max_position_pct=_num("MAX_POSITION_PCT", "10") / 100,
            daily_loss_limit=_num("DAILY_LOSS_LIMIT_PCT", "2.0") / 100,
            max_open_positions=_num("MAX_OPEN_POSITIONS", "3", int),
        )
    except ValueError as e:
        raise ConfigError(str(e)) from None

    data_dir = Path(_get("DATA_DIR", "data"))
    return Config(
        alpaca_api_key=key,
        alpaca_secret_key=secret,
        trading_mode=mode,
        symbols=symbols,
        timeframe=timeframe,
        data_feed=_get("DATA_FEED", "iex").lower(),
        strategy=strategy,
        risk=risk,
        ai_confirmation=_bool("AI_CONFIRMATION_ENABLED"),
        ai_model=_get("GROQ_MODEL", "openai/gpt-oss-20b"),
        loop_minutes=max(1, _num("LOOP_MINUTES", "15", int)),
        data_dir=data_dir,
        kill_switch_file=Path(_get("KILL_SWITCH_FILE", "STOP")),
        benchmark_symbol=_get("BENCHMARK_SYMBOL", "SPY").upper() or "SPY",
    )
