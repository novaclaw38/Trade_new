# Implementation plan and handoff

For the next Claude Code session (or a human) picking up this repo.
Goal of the next session: **prove that Groq and Alpaca both work end to end,
in paper mode**, then fix whatever that turns up.

Last updated: 2026-10-16.

---

## 1. Where things stand

**What exists** (two commits on `main`, plus this plan):

* A complete, paper-trading-first Python trading bot. Read `README.md` for the
  user-facing explanation. Module map:
  `bot/strategy.py` rules, `bot/risk.py` risk gate, `bot/engine.py` one cycle,
  `bot/broker.py` Alpaca + in-memory SimBroker, `bot/data.py` Alpaca market
  data/news, `bot/ai_filter.py` Groq news veto, `bot/dashboard.py`,
  `bot/journal.py`, `bot/backtest.py`, `bot/config.py`, `bot/__main__.py` CLI.
* 62 unit tests, all passing (`pytest`). No network needed for them.
* The strategy: long-only SMA 20/50 crossover on daily bars, only above SMA
  200, skip if ATR > 4% of price, stop = close − 2×ATR, exit on SMA20 < SMA50
  or stop. Default symbol SPY.

**What has been verified** (2026-10-16, all plan steps run with real keys):

| Item | Status | Evidence |
|---|---|---|
| Unit tests | ✅ 62/62 pass | `pytest` |
| Groq API | ✅ works | Real calls: allow on normal headlines, veto on a "trading halted" headline. |
| Alpaca paper account | ✅ works | `python -m bot status`: $100,000 equity, PAPER mode, no positions. |
| Alpaca market data + news | ✅ works | 293 daily SPY bars (needs 202), latest price, 10 headlines; signal computed (HOLD, no fresh crossover). |
| Real backtest, SPY 5y (2021-10-18 → 2026-10-15) | ✅ runs | $100k: **+2.46%** vs buy & hold +73.83%, max drawdown −1.05%, 7 closed trades (29% wins) + 1 open. |
| Paper cycle with AI on | ✅ partial | Ran after the close: "Market is closed; nothing to do." **Still to do: one run during market hours.** |

**Fixed during verification:** Alpaca reports `buying_power` including margin
($400k on $100k equity). `AlpacaBroker.get_account` now caps it at actual cash,
so the bot can never borrow (tests in `tests/test_risk.py`).

**Honest read of the backtest:** the risk caps (≤10% of equity per position,
≤1% risk per trade) keep the bot mostly in cash, so it gave up nearly all of
the bull market's gain in exchange for a tiny drawdown. Changing that is a
risk/return decision for the user, not a bug. Don't tune parameters to the
backtest.

**Remaining:** run Step 5 once during US market hours.

---

## 2. Ground rules (do not break these)

* **Paper mode only.** Never set `TRADING_MODE=live` or
  `LIVE_TRADING_ACKNOWLEDGED`. Live trading is out of scope for this work.
* **Never print, log or commit secrets.** Check variables with
  `[ -n "$VAR" ]` style tests, never `echo $VAR`. `.env` and `data/` are gitignored; keep it that way.
* **Do not loosen risk controls.** The hard ceilings in `bot/risk.py`
  (2% risk/trade, 25% position, 5% daily loss, 5 positions) stay as they are.
* **Uncertainty means no trade.** Any new code path that can fail must fail
  toward "do nothing", like the existing ones.
* Run `pytest` before every push. Commit to `main` only when tests are green.

---

## 3. Steps for the next session

### Step 0: Check the environment (2 min)

```bash
for v in ALPACA_API_KEY ALPACA_SECRET_KEY GROQ_API_KEY; do
  val="${!v}"; [ -n "$val" ] && echo "$v: set (length ${#val})" || echo "$v: NOT set"
done
```

* All three set → continue.
* Alpaca keys missing → stop and tell the user: add `ALPACA_API_KEY` and
  `ALPACA_SECRET_KEY` (paper keys, exact names) in the cloud environment's
  settings (environment menu in the session title bar → Edit), then start a
  **new** session. Do not ask the user to paste keys into the chat.

### Step 1: Install and run the tests (3 min)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pytest -q            # expect 62 passed
```

No `.env` file is needed: `bot/config.py` reads the process environment, and
`load_dotenv` does not override variables that are already set.

### Step 2: Verify Groq (1 min)

```bash
python - <<'EOF'
from bot.ai_filter import AIConfirmation
ai = AIConfirmation("openai/gpt-oss-20b", lambda s: ["SPY edges higher on steady inflows"])
print("normal :", ai.review("SPY", "SPY BUY: SMA20 crossed above SMA50."))
ai = AIConfirmation("openai/gpt-oss-20b", lambda s: ["Trading in SPY halted after exchange outage"])
print("halt   :", ai.review("SPY", "SPY BUY: SMA20 crossed above SMA50."))
EOF
```

Expected: `allow=True` for the first, `allow=False` for the second.
`APIConnectionError` → network policy blocks `api.groq.com`;
`AuthenticationError` → bad key. Either way the code vetoes (correct
fail-safe), but report it.

### Step 3: Verify Alpaca, read-only (5 min)

```bash
python -m bot status
```

Expected: the dashboard with the paper account's equity, "mode: PAPER",
market OPEN/CLOSED, and positions (probably none). Then check market data
and news directly:

```bash
python - <<'EOF'
import os
from bot.data import AlpacaData
d = AlpacaData(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
bars = d.get_bars("SPY")
print(len(bars), "daily bars, last:", bars.index[-1], float(bars.close.iloc[-1]))
print("latest price:", d.latest_price("SPY"))
print("headlines:", d.recent_headlines("SPY")[:3])
EOF
```

Expected: about 250+ bars (enough for the 200-bar trend SMA, which needs 202),
a latest price, and some headlines.

Possible issues to fix if they appear:
* `get_bars` returns fewer than 202 bars → raise the lookback multiplier in
  `AlpacaData.get_bars` (`bot/data.py`).
* 403 on `sip` data → keep `DATA_FEED=iex` (the default; free tier).
* Multi-index shape differs from `df.xs(symbol, level="symbol")` assumption
  in `get_bars` → adjust and add a unit test with a fixture DataFrame.

### Step 4: Real backtest on SPY (2 min)

```bash
python -m bot backtest --years 5
```

Report the numbers to the user honestly (return, max drawdown, trades,
win rate, buy & hold comparison). Don't tune parameters to make the backtest
look better; that's overfitting.

### Step 5: One paper trading cycle with the AI check on (5 min, market hours)

Only during US market hours (9:30–16:00 America/New_York, weekdays):

```bash
AI_CONFIRMATION_ENABLED=true python -m bot -v run
tail -n 20 data/logs/journal.jsonl
```

Expected: a `signal` event for SPY (almost always HOLD; crossovers are rare),
a `decision` event, and the dashboard. If it is a BUY, there will also be
`ai_review`, `risk_check` and `trade` events, and an OTO order with a stop leg
should appear in the Alpaca paper dashboard. Outside market hours expect
"Market is closed; nothing to do." That still counts as a pass for
connectivity.

To force-test the order path without waiting for a real crossover, do NOT
hack the strategy. The engine's order path is already covered by
`tests/test_engine.py` with `SimBroker`. If a real paper order is needed, ask
the user first.

### Step 6: Report back

Tell the user, per step, what passed and what didn't, with the actual output
(minus secrets). Commit any fixes with tests, then push to `main`.

---

## 4. Backlog (only if the user asks)

1. **SessionStart hook** so cloud sessions auto-install `requirements.txt`
   (see the `session-start-hook` skill).
2. **GitHub Actions CI** running `pytest` on every push.
3. ✅ **`python -m bot check`** (done 2026-10-16): tests the Alpaca account,
   market data, news and one Groq call; places no orders. All four passed.
4. ✅ **Scheduling** (working 2026-10-16): `scripts/scheduled_run.sh` runs one
   cycle on a fresh machine and keeps `data/` on the `bot-data` branch. It
   checks push access first and refuses to trade if state can't be saved.
   Cloud Routine "Trade bot daily paper run" fires weekdays 9:52 New York time,
   with the repo attached. A manual test run pushed to `bot-data` successfully.
   First real run: Monday 2026-10-19, which also covers Step 5 (a cycle during
   market hours). To halt new trades, add a `STOP` file to `bot-data`.
5. ✅ **Local web dashboard** (done 2026-10-16): `python -m bot web`
   (`bot/web.py`, `bot/web_page.html`). Read-only plus the kill switch;
   localhost only, Host-header check, custom header required for writes.

## 5. Known gotchas

* Cloud containers are ephemeral: `data/state.json` and logs vanish when the
  session ends. That's fine for verification, but it's not a place to run the bot
  unattended. The bot's broker-side stop orders still protect positions.
* If `data/state.json` is lost while a position is open, the bot **closes**
  that position on its next run (it never leaves a position without a recorded
  stop). Use a dedicated paper account.
* Changing `GROQ_MODEL`: the model must support Groq strict structured outputs
  (`json_schema`), or every review fails and every buy is skipped.
