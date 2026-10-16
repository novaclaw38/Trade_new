"""Plain-text dashboard printed after every cycle and by `python -m bot status`."""
from __future__ import annotations

from bot.broker import Account, Position
from bot.journal import Journal
from bot.state import BotState
from bot.strategy import Signal


def _money(x: float) -> str:
    return f"{'-' if x < 0 else '+'}${abs(x):,.2f}"


def render(
    *,
    mode: str,
    account: Account,
    positions: dict[str, Position],
    state: BotState,
    journal: Journal,
    signals: list[Signal] | None = None,
    market_open: bool | None = None,
) -> str:
    out = []
    bar = "=" * 72
    out.append(bar)
    out.append(f" TRADING BOT  |  mode: {mode.upper()}"
               + ("  |  market OPEN" if market_open else "  |  market CLOSED" if market_open is False else ""))
    out.append(bar)

    day_pl = account.equity - account.last_equity
    day_pct = day_pl / account.last_equity if account.last_equity else 0.0
    out.append(f" Equity:          ${account.equity:,.2f}")
    out.append(f" Today's P&L:     {_money(day_pl)} ({day_pct:+.2%})")
    if state.initial_equity:
        total = account.equity - state.initial_equity
        out.append(f" P&L since start: {_money(total)} ({total / state.initial_equity:+.2%})")
    out.append(f" Buying power:    ${account.buying_power:,.2f}")
    status = f"HALTED - {state.halt_reason}" if state.halted else "ACTIVE (new trades allowed)"
    out.append(f" Trading status:  {status}")

    out.append("\n POSITIONS")
    if positions:
        out.append(f"   {'SYMBOL':<7}{'QTY':>7}{'ENTRY':>11}{'PRICE':>11}{'STOP':>11}{'UNREALIZED':>15}")
        for p in positions.values():
            stop = state.stops.get(p.symbol)
            out.append(f"   {p.symbol:<7}{p.qty:>7g}{p.avg_entry_price:>11.2f}{p.current_price:>11.2f}"
                       f"{(f'{stop:.2f}' if stop else 'NONE!'):>11}{_money(p.unrealized_pl):>15}")
    else:
        out.append("   (none - the bot is in cash)")

    if signals:
        out.append("\n LATEST SIGNALS")
        for s in signals:
            out.append(f"   {s.symbol:<7}{s.action.value:<6}{' '.join(s.reasons)}")

    trades = journal.events("trade", last=8)
    out.append("\n RECENT TRADES")
    if trades:
        for t in trades:
            pnl = f"  P&L {_money(t['pnl'])}" if "pnl" in t else ""
            out.append(f"   {t['time'][:16]}  {t['side']:<4} {t['qty']:>6g} {t['symbol']:<6} @ {t['price']:<10}{pnl}")
    else:
        out.append("   (no trades yet - this strategy trades rarely; that's normal)")
    out.append(bar)
    return "\n".join(out)
