# Trade_new

Paper-trading-first Python trading bot (Alpaca broker/data, optional Groq news veto).

**Start here:** read `docs/IMPLEMENTATION_PLAN.md`. It has the current status,
what is and isn't verified, and the next steps.

Rules for working in this repo:
- Paper mode only. Never enable live trading (`TRADING_MODE=live`).
- Never print or commit secrets (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `GROQ_API_KEY`).
- Don't loosen the hard risk ceilings in `bot/risk.py`.
- New failure paths must fail toward "no trade".
- Run `pytest` before committing; tests need no network or keys.
