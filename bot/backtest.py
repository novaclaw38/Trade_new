"""Replay history through the same strategy and risk rules to see how they
would have behaved. A backtest is a sanity check, not a promise: real fills,
gaps and future markets will differ.

Mechanics (daily bars):
  * Signals are computed on a bar's close; orders fill at the NEXT bar's open
    (no peeking at the future), with a small slippage charge per side.
  * The stop-loss triggers if a bar's low touches it. If the bar opens below
    the stop (a gap), the fill is at the open: worse than the stop, as in
    real life.
  * Note: because entries fill at the next open, the daily loss limit
    rarely matters here; it matters live.
"""
from __future__ import annotations

import pandas as pd

from bot.broker import SimBroker
from bot.risk import RiskLimits, RiskManager
from bot.state import BotState
from bot.strategy import Action, StrategyParams, compute_indicators, evaluate_at


def run_backtest(
    bars: pd.DataFrame,
    symbol: str,
    params: StrategyParams,
    limits: RiskLimits,
    starting_cash: float = 10_000.0,
    slippage: float = 0.0005,
) -> dict:
    ind = compute_indicators(bars, params)
    broker = SimBroker(starting_cash)
    risk = RiskManager(limits, BotState())
    stop: float | None = None
    pending = None
    trades: list[dict] = []
    curve: list[float] = []
    entry_price = 0.0

    def close_out(fill: float, why: str, when) -> None:
        nonlocal stop
        qty = broker.get_positions()[symbol].qty
        broker.set_price(symbol, fill)
        broker.close_position(symbol)
        trades.append({"exit_time": when, "qty": qty, "entry": entry_price, "exit": fill,
                       "pnl": (fill - entry_price) * qty, "why": why})
        stop = None

    for i in range(len(ind)):
        row = ind.iloc[i]
        when = ind.index[i]
        day = str(pd.Timestamp(when).date())
        o, low, c = float(row["open"]), float(row["low"]), float(row["close"])
        in_pos = symbol in broker.get_positions()

        broker.start_day()  # yesterday's close equity becomes today's starting equity
        # 1) Execute yesterday's decision at today's open.
        if pending == "SELL" and in_pos:
            close_out(o * (1 - slippage), "trend exit", when)
        elif pending is not None and pending != "SELL" and not in_pos:
            entry = o * (1 + slippage)
            broker.set_price(symbol, entry)
            d = risk.approve_entry(equity=broker.equity(), buying_power=broker.cash, entry=entry,
                                   stop=pending.stop_price, open_positions=0)
            if d.approved:
                broker.buy_with_stop(symbol, d.qty, pending.stop_price)
                stop, entry_price = pending.stop_price, entry
        pending = None

        # 2) Intraday stop-loss.
        if symbol in broker.get_positions() and stop is not None and low <= stop:
            close_out(min(o, stop) * (1 - slippage), "stop-loss", when)

        # 3) At the close: mark to market, check the daily limit, decide for tomorrow.
        if symbol in broker.prices:
            broker.set_price(symbol, c)
        risk.update_daily_status(broker.equity(), broker.last_equity, day)
        in_pos = symbol in broker.get_positions()
        sig = evaluate_at(symbol, ind, i, params, in_position=in_pos)
        if in_pos and sig.action is Action.SELL:
            pending = "SELL"
        elif not in_pos and sig.action is Action.BUY:
            pending = sig
        curve.append(broker.equity())

    equity = pd.Series(curve, index=ind.index)
    drawdown = (equity / equity.cummax() - 1).min() if len(equity) else 0.0
    wins = [t for t in trades if t["pnl"] > 0]
    return {
        "symbol": symbol,
        "start": str(ind.index[0].date()) if len(ind) else "",
        "end": str(ind.index[-1].date()) if len(ind) else "",
        "starting_cash": starting_cash,
        "final_equity": float(equity.iloc[-1]) if len(equity) else starting_cash,
        "total_return": float(equity.iloc[-1] / starting_cash - 1) if len(equity) else 0.0,
        "buy_and_hold_return": float(bars["close"].iloc[-1] / bars["open"].iloc[0] - 1) if len(bars) else 0.0,
        "max_drawdown": float(drawdown),
        "trades": trades,
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "still_open": symbol in broker.get_positions(),
    }


def format_report(r: dict) -> str:
    lines = [
        f"Backtest {r['symbol']}  {r['start']} -> {r['end']}",
        f"  Starting cash:      ${r['starting_cash']:,.2f}",
        f"  Final equity:       ${r['final_equity']:,.2f}",
        f"  Strategy return:    {r['total_return']:+.2%}",
        f"  Buy & hold return:  {r['buy_and_hold_return']:+.2%}  (for comparison; with 100% of money invested)",
        f"  Max drawdown:       {r['max_drawdown']:.2%}",
        f"  Closed trades:      {len(r['trades'])}  (win rate {r['win_rate']:.0%})"
        + ("  + 1 still open" if r["still_open"] else ""),
    ]
    for t in r["trades"][-10:]:
        lines.append(f"    {str(t['exit_time'])[:10]}  {t['qty']:>5g} sh  {t['entry']:>9.2f} -> {t['exit']:>9.2f}"
                     f"  P&L {t['pnl']:>+10.2f}  ({t['why']})")
    lines.append("  Note: the strategy only risks ~1% per trade and holds a capped position, so it")
    lines.append("  usually returns less than buy & hold in strong bull markets, with smaller drawdowns.")
    return "\n".join(lines)
