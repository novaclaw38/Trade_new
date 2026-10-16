"""Command line entry point.

    python -m bot run            # one cycle (good for cron / Task Scheduler)
    python -m bot run --loop     # keep running, one cycle every LOOP_MINUTES
    python -m bot status         # dashboard only, no trading
    python -m bot check          # test API keys / connections, no trading
    python -m bot web            # local web dashboard at http://127.0.0.1:8765
    python -m bot backtest       # replay history through the strategy
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

from bot.config import Config, ConfigError, load_config

LIVE_BANNER = r"""
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!                                                                    !!
!!   LIVE TRADING MODE - THIS BOT WILL PLACE ORDERS WITH REAL MONEY   !!
!!                                                                    !!
!!   No trading bot guarantees profit. You can lose money.            !!
!!   Press Ctrl+C within 10 seconds to abort.                         !!
!!                                                                    !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
"""


def live_warning(cfg: Config) -> None:
    if not cfg.is_live:
        print("Paper trading mode (Alpaca sandbox, simulated money).")
        return
    print(LIVE_BANNER, file=sys.stderr)
    for s in range(10, 0, -1):
        print(f"  starting live trading in {s}...", file=sys.stderr, end="\r", flush=True)
        time.sleep(1)
    print(file=sys.stderr)


def build_engine(cfg: Config, verbose: bool):
    from bot.ai_filter import AIConfirmation
    from bot.broker import AlpacaBroker
    from bot.data import AlpacaData
    from bot.engine import Engine
    from bot.journal import Journal, setup_logging
    from bot.risk import RiskManager
    from bot.state import BotState

    setup_logging(cfg.log_dir, verbose)
    state = BotState.load(cfg.state_file)
    data = AlpacaData(cfg.alpaca_api_key, cfg.alpaca_secret_key, cfg.timeframe, cfg.data_feed)
    ai = AIConfirmation(cfg.ai_model, data.recent_headlines) if cfg.ai_confirmation else None
    return Engine(
        symbols=cfg.symbols,
        params=cfg.strategy,
        broker=AlpacaBroker(cfg.alpaca_api_key, cfg.alpaca_secret_key, paper=not cfg.is_live),
        data=data,
        risk=RiskManager(cfg.risk, state),
        state=state,
        journal=Journal(cfg.log_dir),
        ai_filter=ai,
        kill_switch_file=cfg.kill_switch_file,
    )


def show(cfg: Config, engine) -> None:
    from bot.dashboard import render

    print(render(
        mode=cfg.trading_mode,
        account=engine.broker.get_account(),
        positions=engine.broker.get_positions(),
        state=engine.state,
        journal=engine.journal,
        signals=engine.last_signals,
        market_open=engine.broker.market_is_open(),
    ))


def cmd_run(cfg: Config, args) -> None:
    live_warning(cfg)
    engine = build_engine(cfg, args.verbose)
    while True:
        engine.run_once()
        show(cfg, engine)
        if not args.loop:
            return
        print(f"Next check in {cfg.loop_minutes} min (Ctrl+C to stop; positions keep their broker-side stops).")
        time.sleep(cfg.loop_minutes * 60)


def cmd_backtest(cfg: Config, args) -> None:
    from bot.backtest import format_report, run_backtest
    from bot.data import AlpacaData, load_csv

    if args.csv:
        bars = load_csv(args.csv)
    else:
        data = AlpacaData(cfg.alpaca_api_key, cfg.alpaca_secret_key, "1Day", cfg.data_feed)
        bars = data.get_bars(args.symbol, start=datetime.now(timezone.utc) - timedelta(days=365 * args.years))
    if len(bars) < cfg.strategy.min_bars:
        sys.exit(f"Only {len(bars)} bars of history; need at least {cfg.strategy.min_bars}.")
    print(format_report(run_backtest(bars, args.symbol, cfg.strategy, cfg.risk, args.cash)))


def cmd_check(cfg: Config) -> None:
    from bot.ai_filter import AIConfirmation
    from bot.broker import AlpacaBroker
    from bot.check import FAIL, format_results, run_checks
    from bot.data import AlpacaData

    broker = AlpacaBroker(cfg.alpaca_api_key, cfg.alpaca_secret_key, paper=not cfg.is_live)
    data = AlpacaData(cfg.alpaca_api_key, cfg.alpaca_secret_key, cfg.timeframe, cfg.data_feed)
    results = run_checks(cfg, broker, data, lambda: AIConfirmation(cfg.ai_model, data.recent_headlines))
    print(format_results(results))
    if any(r.status == FAIL for r in results):
        sys.exit(1)


def cmd_web(cfg: Config, args) -> None:
    from bot.broker import AlpacaBroker
    from bot.data import AlpacaData
    from bot.journal import setup_logging
    from bot.web import Dashboard, serve

    setup_logging(cfg.log_dir, args.verbose)
    broker = AlpacaBroker(cfg.alpaca_api_key, cfg.alpaca_secret_key, paper=not cfg.is_live)
    data = AlpacaData(cfg.alpaca_api_key, cfg.alpaca_secret_key, cfg.timeframe, cfg.data_feed)
    serve(Dashboard(cfg, broker, data), args.port)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m bot", description="Simple, conservative trading bot.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("-v", "--verbose", action="store_true", help="also print the log to the console")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one trading cycle (or --loop)")
    run.add_argument("--loop", action="store_true")
    sub.add_parser("status", help="show the dashboard without trading")
    sub.add_parser("check", help="test the Alpaca and Groq connections; places no orders")
    web = sub.add_parser("web", help="local web dashboard (read-only, plus a kill switch)")
    web.add_argument("--port", type=int, default=8765)
    bt = sub.add_parser("backtest", help="replay history through the strategy")
    bt.add_argument("--symbol", default=None, help="defaults to the first symbol in SYMBOLS")
    bt.add_argument("--years", type=int, default=5)
    bt.add_argument("--cash", type=float, default=10_000.0)
    bt.add_argument("--csv", help="use a local CSV (timestamp,open,high,low,close) instead of Alpaca")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.env_file, require_keys=not (args.command == "backtest" and args.csv))
    except ConfigError as e:
        sys.exit(f"Configuration error: {e}")

    if args.command == "run":
        cmd_run(cfg, args)
    elif args.command == "status":
        show(cfg, build_engine(cfg, args.verbose))
    elif args.command == "check":
        cmd_check(cfg)
    elif args.command == "web":
        cmd_web(cfg, args)
    else:
        args.symbol = (args.symbol or cfg.symbols[0]).upper()
        cmd_backtest(cfg, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
