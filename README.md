# Simple Trading Bot

A small, conservative trading bot for beginners. It trades a broad stock-market
ETF (SPY by default) using one well-known trend-following rule, with risk
controls that can't be switched off. It runs in **paper trading mode by
default**, which means simulated money in a broker sandbox.

> ## ⚠️ Disclaimer
> **No trading bot guarantees profit. You can lose money, including more than
> you expect if prices gap past a stop-loss.** This project is for learning.
> It is not financial advice. Past performance (including backtests) does not
> predict future results. Run it in paper mode for weeks before you even
> consider real money, and never trade money you can't afford to lose.

---

## What's inside

| File | Job |
|---|---|
| `bot/strategy.py` | **Signal engine.** Moving-average crossover rules. Says BUY / SELL / HOLD and *why*. |
| `bot/risk.py` | **Risk controls.** Position sizing, mandatory stop-loss, daily loss limit, size caps. |
| `bot/data.py` | **Market data** from Alpaca (daily bars, latest price, news headlines). |
| `bot/broker.py` | **Order execution and positions** via Alpaca, plus an in-memory broker for tests. |
| `bot/engine.py` | Runs one cycle: data → signal → (optional AI veto) → risk check → order → log. |
| `bot/ai_filter.py` | Optional Claude news check that can only **veto** a buy. Off by default. |
| `bot/dashboard.py` | The text dashboard: equity, P&L, positions, signals, recent trades. |
| `bot/journal.py` | Audit log of every signal, decision and order. |
| `bot/backtest.py` | Replays history through the same rules. |
| `bot/config.py` | Reads settings from `.env` / environment variables. |
| `tests/` | Unit tests for signals, risk controls, stop-loss and daily-limit behaviour. |

**Stack:** Python 3.10+, [`alpaca-py`](https://github.com/alpacahq/alpaca-py)
(the [Alpaca](https://alpaca.markets) broker and market-data API, which has a
free paper-trading sandbox), `pandas`, `python-dotenv`, `pytest`, and
optionally `anthropic` (Claude) for the AI layer.

---

## Setup (about 10 minutes)

1. **Install Python 3.10 or newer** from [python.org](https://www.python.org/downloads/).
2. **Create a free Alpaca account** at [alpaca.markets](https://alpaca.markets).
   In the dashboard, switch to **Paper Trading** and generate an API key and secret.
   Paper keys only work with fake money, which is what you want.
3. **Install the bot:**
   ```bash
   cd simple-trading-bot
   python -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. **Configure it:**
   ```bash
   cp .env.example .env               # Windows: copy .env.example .env
   ```
   Open `.env` and paste your paper `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`.
   Leave everything else at the defaults to start.
5. **Run the tests** to check everything works:
   ```bash
   pytest
   ```

## Running in paper mode

```bash
python -m bot status          # look at your (paper) account; places no orders
python -m bot backtest        # how would the rules have done over the last 5 years?
python -m bot run             # run ONE trading cycle, then print the dashboard
python -m bot run --loop      # keep running; checks every LOOP_MINUTES (Ctrl+C to stop)
python -m bot -v run          # same, but also print the detailed log to the screen
```

The bot only acts while the US stock market is open (9:30–16:00 New York time,
weekdays). Outside those hours, `run` just reports "Market is closed". With
daily bars, running once a day (for example around 10:00 New York time, using
cron or Windows Task Scheduler) is enough.

**Expect very few trades.** On daily bars this strategy may trade only a few
times a year. Long stretches of "HOLD: No fresh crossover" are normal.

Example dashboard:

```
========================================================================
 TRADING BOT  |  mode: PAPER  |  market OPEN
========================================================================
 Equity:          $100,212.40
 Today's P&L:     +$85.10 (+0.08%)
 P&L since start: +$212.40 (+0.21%)
 Buying power:    $90,224.73
 Trading status:  ACTIVE (new trades allowed)

 POSITIONS
   SYMBOL     QTY      ENTRY      PRICE       STOP     UNREALIZED
   SPY         17     581.20     587.51     566.90       +$107.27

 LATEST SIGNALS
   SPY    HOLD  Holding: trend still intact.

 RECENT TRADES
   2026-10-02T14:05  BUY      17 SPY    @ 581.2
========================================================================
```

### Stopping the bot

* **Ctrl+C** stops the program. Open positions keep their stop-loss orders
  **at the broker**, so they stay protected even when your computer is off.
* **Kill switch:** create a file named `STOP` in the project folder
  (`touch STOP`). The bot will place no new buys until you delete it.
  Exits and stop-losses still work.

## How the strategy works (plain language)

The bot watches two averages of SPY's closing price: the average of the last
**20 days** (fast) and of the last **50 days** (slow).

* **Buy** when the 20-day average crosses *above* the 50-day average. That
  usually means prices have started trending up. Two extra checks must also
  pass: the price must be above its **200-day** average (we only buy in a
  long-term uptrend), and the market must not be unusually jumpy (daily
  range, measured by ATR, under 4% of price).
* **Sell** when the 20-day average drops back *below* the 50-day average
  (the uptrend is over), **or** when the price hits the stop-loss.
* **Stop-loss:** set at entry, 2 × ATR (the average daily price range) below
  the price. If SPY usually moves $6 a day, the stop is about $12 below entry,
  far enough that normal wiggles don't trigger it.
* It only ever **buys** (no short selling), uses **no leverage**, and only
  looks at **completed** daily bars.

**Why this one?** It's simple enough to check on any free chart, it trades
rarely (low costs, easy to supervise), it doesn't try to predict tops and
bottoms, and trading a broad index ETF avoids the risk of any single company
collapsing. The trade-off: in choppy, sideways markets it will take a series
of small losses (the stop-losses), and in strong bull markets it will usually
earn less than simply buying and holding, because it is often in cash and only
uses part of the account.

### Risk controls (always on)

| Control | Default | Hard ceiling | What it does |
|---|---|---|---|
| Max loss per trade | 1% of equity | 2% | Position size = (1% of equity) ÷ (entry − stop). |
| Stop-loss on every position | 2 × ATR | required | No stop, no trade. Sent to the broker as a real stop order. The bot also checks it on every cycle. |
| Daily loss limit | 2% | 5% | If equity falls 2% below the day's start, no new buys until tomorrow. Survives restarts. |
| Max position size | 10% of equity | 25% | Caps any single position, whatever the stop distance. |
| Max open positions | 3 | 5 | |

The ceilings are in `bot/risk.py`. If `.env` asks for more than a ceiling,
the bot refuses to start. Every entry goes through `RiskManager.approve_entry`,
and the broker layer independently refuses any buy without a valid stop.

**When in doubt, it does nothing.** Missing data, an API error, not enough
price history, a signal it already acted on, or an AI check that fails all
result in no trade.

Note: the bot assumes it manages the positions in `SYMBOLS` on this account.
If it finds one of those positions with no stop-loss on record (for example,
you bought SPY by hand, or deleted `data/state.json`), it **closes** that
position rather than leave it unprotected. Use a dedicated paper account.

## Optional AI confirmation (Claude)

Off by default. When you set `AI_CONFIRMATION_ENABLED=true` (and add an
`ANTHROPIC_API_KEY`), every BUY signal is first shown to Claude together with
the last 48 hours of news headlines for that symbol (from Alpaca's news API).
Claude answers "allow" or "veto" with a one-sentence reason, which goes into
the log.

* It can **only veto** a trade. It never creates, enlarges or holds one.
* If the AI call fails or the model won't answer, the trade is **skipped**.
* No recent headlines → the rules-based signal stands on its own.

This adds a small cost per BUY signal (a few per year on daily bars).

## Logs and auditing

Everything goes into `data/logs/`:

* `journal.jsonl`: one JSON line per event (`signal`, `ai_review`,
  `risk_check`, `trade`, `stop_loss`, `exit`, `halt`, `error`, `decision`).
  This is the complete record of what the bot saw, decided and did.
* `bot.log`: the same events, human-readable.

`data/state.json` holds the stop-loss levels, the daily-halt flag, and which
signals have been acted on. Don't edit it while the bot is running.

## Configuration reference

All settings live in `.env` (see `.env.example` for every option with
comments) or can be set as environment variables. Secrets are never stored in
code. Key ones: `SYMBOLS` (comma-separated, e.g. `SPY,QQQ`), `TIMEFRAME`
(`1Day` recommended, or `1Hour`), and the `*_PCT` risk settings.

## Live trading (not recommended for beginners)

Live trading is locked behind **two** explicit settings:

```
TRADING_MODE=live
LIVE_TRADING_ACKNOWLEDGED=I_UNDERSTAND_I_CAN_LOSE_REAL_MONEY
```

You also need **live** Alpaca keys (different from paper keys). When started
in live mode, the bot prints a large warning and waits 10 seconds so you can
abort. Before you do this: run in paper mode for at least several weeks, read
every line of `journal.jsonl`, and make sure you understand every trade it made.

## Limitations

* Stop-losses can't protect against overnight gaps: if SPY opens far below
  your stop, you are filled at the (worse) open price.
* Paper fills are simulated and are usually kinder than real ones.
* The backtest is simple (next-open fills, a small slippage charge, no
  dividends). Use it to understand behaviour, not to predict profits.
