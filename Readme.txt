TRADE_NEW - A SIMPLE TRADING BOT
================================

A small, conservative trading bot for beginners. It trades a broad stock-market ETF
(SPY by default) with one trend-following rule and risk controls that cannot be
switched off. It runs in PAPER TRADING mode (simulated money) by default.

DISCLAIMER: No trading bot guarantees profit. You can lose money. This project is for
learning and is not financial advice. Run it in paper mode for weeks before even
considering real money.

HOW IT WORKS
------------
- BUY when the 20-day average crosses above the 50-day average, the price is above its
  200-day average, and volatility (ATR) is under 4% of price.
- SELL when the 20-day average drops below the 50-day average, or the stop-loss is hit.
- Every position gets a mandatory stop-loss (2 x ATR). Long only, no leverage.
- Risk controls: 1% max loss per trade, 2% daily loss limit, 10% max position size,
  3 open positions. Hard ceilings in bot/risk.py cannot be exceeded from configuration.
- Optional Groq AI check that can only VETO a buy (off by default).
- Kill switch: create a file named STOP to block new buys.

PROJECT LAYOUT
--------------
  bot/strategy.py    Signal engine (BUY / SELL / HOLD and why)
  bot/risk.py        Position sizing, stop-loss, daily loss limit
  bot/data.py        Alpaca market data
  bot/broker.py      Orders and positions via Alpaca (plus in-memory broker for tests)
  bot/engine.py      One trading cycle: data -> signal -> risk check -> order -> log
  bot/ai_filter.py   Optional Groq news veto
  bot/dashboard.py   Text dashboard
  bot/journal.py     Audit log of every signal and decision
  bot/backtest.py    Replays history through the same rules
  bot/config.py     Settings from .env
  tests/             Unit tests for signals and risk controls
  scripts/           Scheduled-run helper
  docs/              Supporting notes

GETTING STARTED
---------------
  python -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  cp .env.example .env           (add your Alpaca PAPER API key and secret)
  pytest
  python -m bot check            (verify keys; places no orders)
  python -m bot status
  python -m bot backtest
  python -m bot run              (one cycle)   |   python -m bot run --loop
  python -m bot web              (local dashboard at http://127.0.0.1:8765)

Live trading is locked behind two explicit settings; see README.md before enabling it.
