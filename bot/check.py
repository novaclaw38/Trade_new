"""Connection check: confirms the API keys work without placing any orders.

    python -m bot check

Each check talks to one service and reports OK / FAIL / SKIP with a short
detail line. Nothing here trades or writes bot state.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

OK, FAIL, SKIP = "OK", "FAIL", "SKIP"

# A harmless, made-up headline so the Groq call runs the full structured-output
# path even when there is no real news. The verdict itself is ignored.
TEST_HEADLINES = ["Index fund reports routine quarterly rebalancing"]


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str


def _attempt(name: str, fn: Callable[[], str]) -> CheckResult:
    try:
        return CheckResult(name, OK, fn())
    except Exception as e:  # noqa: BLE001 - report every failure, don't crash
        return CheckResult(name, FAIL, f"{type(e).__name__}: {e}")


def run_checks(cfg, broker, data, make_ai: Callable[[], object]) -> list[CheckResult]:
    symbol = cfg.symbols[0]

    def account() -> str:
        a = broker.get_account()
        return f"{cfg.trading_mode} account, equity ${a.equity:,.2f}"

    def bars() -> str:
        df = data.get_bars(symbol, lookback_bars=10)
        if df.empty:
            raise RuntimeError(f"no bars returned for {symbol} (feed={cfg.data_feed})")
        return f"{len(df)} recent {cfg.timeframe} bars for {symbol}, last {df.index[-1]:%Y-%m-%d}"

    def news() -> str:
        return f"{len(data.recent_headlines(symbol))} headlines for {symbol} in the last 48h"

    def groq() -> str:
        v = make_ai().ask(symbol, "connectivity check (no trade)", TEST_HEADLINES)
        return f"model {cfg.ai_model} answered ({'allow' if v.allow else 'veto'})"

    results = [
        _attempt("Alpaca account", account),
        _attempt("Alpaca market data", bars),
        _attempt("Alpaca news", news),
    ]
    if os.getenv("GROQ_API_KEY", "").strip():
        results.append(_attempt("Groq AI", groq))
    elif cfg.ai_confirmation:
        results.append(CheckResult("Groq AI", FAIL, "AI_CONFIRMATION_ENABLED=true but GROQ_API_KEY is not set"))
    else:
        results.append(CheckResult("Groq AI", SKIP, "GROQ_API_KEY not set (AI layer is off)"))
    return results


def format_results(results: list[CheckResult]) -> str:
    width = max(len(r.name) for r in results)
    lines = [f" [{r.status:<4}] {r.name:<{width}}  {r.detail}" for r in results]
    failed = sum(r.status == FAIL for r in results)
    lines.append(f" {'All checks passed.' if not failed else f'{failed} check(s) failed.'} No orders were placed.")
    return "\n".join(lines)
