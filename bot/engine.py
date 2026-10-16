"""One trading cycle: data -> signal -> (AI veto) -> risk check -> order -> journal.

Order of priorities inside a cycle:
  1. Protect existing positions (stop-loss check, trend-exit). Exits are
     always allowed, even when trading is halted.
  2. Only then consider new entries, which must pass every risk control.
Any error for a symbol is logged and that symbol is skipped: errors never
lead to a trade.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.broker import Account, Position
from bot.journal import Journal
from bot.risk import RiskManager
from bot.state import BotState
from bot.strategy import Action, Signal, StrategyParams, generate_signal

MARKET_TZ = ZoneInfo("America/New_York")


class Engine:
    def __init__(
        self,
        *,
        symbols: tuple[str, ...],
        params: StrategyParams,
        broker,
        data,
        risk: RiskManager,
        state: BotState,
        journal: Journal,
        ai_filter=None,
        kill_switch_file: Path | None = None,
    ):
        self.symbols = symbols
        self.params = params
        self.broker = broker
        self.data = data
        self.risk = risk
        self.state = state
        self.journal = journal
        self.ai_filter = ai_filter
        self.kill_switch_file = kill_switch_file
        self.last_signals: list[Signal] = []

    def kill_switch_on(self) -> bool:
        return bool(self.kill_switch_file and self.kill_switch_file.exists())

    def run_once(self, now: datetime | None = None) -> list[Signal]:
        now = now or datetime.now(timezone.utc)
        if not self.broker.market_is_open():
            self.journal.record("decision", message="Market is closed; nothing to do.")
            return []

        account = self.broker.get_account()
        today = now.astimezone(MARKET_TZ).date().isoformat()
        was_halted = self.state.halted and self.state.day == today
        self.risk.update_daily_status(account.equity, account.last_equity, today)
        if self.state.halted and not was_halted:
            self.journal.record("halt", reason=self.state.halt_reason, equity=account.equity)
        if not self.state.initial_equity:
            self.state.initial_equity = account.equity

        positions = self.broker.get_positions()
        signals = []
        for symbol in self.symbols:
            try:
                signals.append(self._process(symbol, account, positions))
            except Exception as e:  # noqa: BLE001 - one bad symbol must not stop the others
                self.journal.record("error", symbol=symbol, error=f"{type(e).__name__}: {e}",
                                    action="Skipped this symbol; no trade placed.")
        self.state.save()
        self.last_signals = signals
        return signals

    # ------------------------------------------------------------------
    def _process(self, symbol: str, account: Account, positions: dict[str, Position]) -> Signal:
        bars = self.data.get_bars(symbol)
        position = positions.get(symbol)
        signal = generate_signal(symbol, bars, self.params, in_position=position is not None)
        self.journal.record("signal", symbol=symbol, action=signal.action.value, reasons=signal.reasons,
                            close=signal.close, stop=signal.stop_price, bar_time=signal.bar_time,
                            indicators=signal.indicators)
        if position is not None:
            self._manage_position(position, signal)
        elif signal.action is Action.BUY:
            self._try_enter(signal, account, positions)
        return signal

    def _manage_position(self, pos: Position, signal: Signal) -> None:
        symbol = pos.symbol
        stop = self.state.stops.get(symbol)
        price = self.data.latest_price(symbol)
        if stop is None:
            self._exit(pos, price, "stop_loss", "Position has no recorded stop-loss; closing it so it is never unprotected.")
        elif price <= stop:
            self._exit(pos, price, "stop_loss", f"STOP-LOSS hit: price {price:.2f} <= stop {stop:.2f}.")
        elif signal.action is Action.SELL:
            self._exit(pos, price, "exit", " ".join(signal.reasons))
        else:
            self.journal.record("decision", symbol=symbol, message=f"Holding {pos.qty:g} shares; price {price:.2f}, stop {stop:.2f}.")

    def _exit(self, pos: Position, price: float, kind: str, reason: str) -> None:
        self.journal.record(kind, symbol=pos.symbol, price=price, reason=reason)
        order = self.broker.close_position(pos.symbol)
        fill = order.fill_price or price
        pnl = (fill - pos.avg_entry_price) * pos.qty
        self.state.stops.pop(pos.symbol, None)
        self.journal.record("trade", symbol=pos.symbol, side="SELL", qty=pos.qty, price=round(fill, 2),
                            pnl=round(pnl, 2), order_id=order.order_id, reason=reason)

    def _try_enter(self, signal: Signal, account: Account, positions: dict[str, Position]) -> None:
        symbol = signal.symbol
        if self.state.acted_bars.get(symbol) == signal.bar_time:
            self.journal.record("decision", symbol=symbol, message="Already acted on this signal bar; not trading it twice.")
            return
        # Consume the signal before anything else: if it's rejected now we won't keep retrying it all day.
        self.state.acted_bars[symbol] = signal.bar_time

        if self.broker.has_open_orders(symbol):
            self.journal.record("decision", symbol=symbol, message="An order is already pending; skipping.")
            return

        if self.ai_filter is not None:
            verdict = self.ai_filter.review(symbol, signal.summary())
            self.journal.record("ai_review", symbol=symbol, allow=verdict.allow, reason=verdict.reason)
            if not verdict.allow:
                return

        price = self.data.latest_price(symbol)
        decision = self.risk.approve_entry(
            equity=account.equity,
            buying_power=account.buying_power,
            entry=price,
            stop=signal.stop_price,
            open_positions=len(positions),
            kill_switch=self.kill_switch_on(),
        )
        self.journal.record("risk_check", symbol=symbol, approved=decision.approved, qty=decision.qty, reason=decision.reason)
        if not decision.approved:
            return

        order = self.broker.buy_with_stop(symbol, decision.qty, signal.stop_price, ref_price=price)
        self.state.stops[symbol] = signal.stop_price
        self.state.save()
        positions[symbol] = Position(symbol, decision.qty, price, price, 0.0)  # counts toward max positions this cycle
        self.journal.record("trade", symbol=symbol, side="BUY", qty=decision.qty, price=round(order.fill_price or price, 2),
                            stop=signal.stop_price, order_id=order.order_id, status=order.status,
                            reason=" ".join(signal.reasons))
