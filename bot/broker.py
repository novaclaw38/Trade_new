"""Order execution and position tracking.

`AlpacaBroker` talks to Alpaca (https://alpaca.markets). With paper=True (the
default) it uses Alpaca's paper-trading sandbox, which is a simulated account
with fake money that behaves like the real one.

`SimBroker` is a tiny in-memory broker used by the unit tests and the backtest.

Both brokers only expose `buy_with_stop` for entries: there is no way to open
a position without a stop-loss.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Account:
    equity: float
    last_equity: float   # equity at the previous market close (start of today)
    buying_power: float


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    unrealized_pl: float


@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: str
    qty: float
    status: str
    fill_price: float | None = None
    stop_price: float | None = None


def require_protective_stop(entry_price: float, stop_price: float | None) -> None:
    """Last line of defence: refuse any entry without a stop below the price."""
    if stop_price is None or not (0 < stop_price < entry_price):
        raise ValueError(f"Refusing to open a position without a valid stop-loss (entry {entry_price}, stop {stop_price}).")


class AlpacaBroker:
    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        from alpaca.trading.client import TradingClient

        self.paper = paper
        self.client = TradingClient(api_key, secret_key, paper=paper)

    def market_is_open(self) -> bool:
        return bool(self.client.get_clock().is_open)

    def get_account(self) -> Account:
        a = self.client.get_account()
        # Alpaca's buying_power includes margin (up to 4x equity). This bot never
        # borrows, so it may only spend the cash it actually has.
        spendable = max(0.0, min(float(a.buying_power), float(a.cash)))
        return Account(float(a.equity), float(a.last_equity), spendable)

    def get_positions(self) -> dict[str, Position]:
        return {
            p.symbol: Position(p.symbol, float(p.qty), float(p.avg_entry_price),
                               float(p.current_price), float(p.unrealized_pl))
            for p in self.client.get_all_positions()
        }

    def equity_history(self) -> list[tuple[str, float]]:
        """Daily (UTC date, equity) for the account's whole life, oldest first."""
        from datetime import datetime, timezone

        from alpaca.trading.requests import GetPortfolioHistoryRequest

        h = self.client.get_portfolio_history(GetPortfolioHistoryRequest(period="all", timeframe="1D"))
        return [
            (datetime.fromtimestamp(ts, timezone.utc).date().isoformat(), float(eq))
            for ts, eq in zip(h.timestamp, h.equity)
            if eq  # days before the account existed come back as 0
        ]

    def filled_orders_since(self, start: str) -> dict[str, int]:
        """How many buy and sell orders filled since `start` (YYYY-MM-DD)."""
        from datetime import datetime, timezone

        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        after = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        orders = self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=after, limit=500))
        if len(orders) == 500:
            log.warning("500 or more closed orders since %s; the scorecard's trade count may be low.", start)
        counts = {"buy": 0, "sell": 0}
        for o in orders:
            if o.filled_at is not None and o.side.value in counts:
                counts[o.side.value] += 1
        return counts

    def _open_orders(self, symbol: str):
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        return self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol]))

    def has_open_orders(self, symbol: str) -> bool:
        return len(self._open_orders(symbol)) > 0

    def buy_with_stop(self, symbol: str, qty: int, stop_price: float, ref_price: float) -> OrderResult:
        """Market buy plus an attached GTC stop-loss order (Alpaca "OTO" order).
        The stop lives at the broker, so it protects the position even if this
        bot crashes or your computer is off."""
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest

        require_protective_stop(ref_price, stop_price)
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.OTO,
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
        )
        o = self.client.submit_order(req)
        return OrderResult(str(o.id), symbol, "buy", qty, str(o.status), stop_price=stop_price)

    def close_position(self, symbol: str) -> OrderResult:
        # Cancel the resting stop order first, otherwise the shares are "held" by it.
        # Cancels are processed asynchronously, so wait (briefly) until they're gone.
        # If closing still fails, the error is logged, the stop stays recorded and
        # the next cycle tries again.
        for o in self._open_orders(symbol):
            self.client.cancel_order_by_id(o.id)
        for _ in range(10):
            if not self._open_orders(symbol):
                break
            time.sleep(0.5)
        o = self.client.close_position(symbol)
        return OrderResult(str(o.id), symbol, "sell", float(o.qty or 0), str(o.status))


class SimBroker:
    """Fills every order instantly at the price you set with `set_price`."""

    def __init__(self, cash: float = 100_000.0):
        self.cash = cash
        self.prices: dict[str, float] = {}
        self._positions: dict[str, tuple[float, float]] = {}  # symbol -> (qty, avg price)
        self.last_equity = cash
        self.open = True
        self.orders: list[OrderResult] = []

    def set_price(self, symbol: str, price: float) -> None:
        self.prices[symbol] = price

    def equity(self) -> float:
        return self.cash + sum(q * self.prices[s] for s, (q, _) in self._positions.items())

    def start_day(self) -> None:
        self.last_equity = self.equity()

    def market_is_open(self) -> bool:
        return self.open

    def get_account(self) -> Account:
        return Account(self.equity(), self.last_equity, self.cash)

    def get_positions(self) -> dict[str, Position]:
        return {
            s: Position(s, q, avg, self.prices[s], (self.prices[s] - avg) * q)
            for s, (q, avg) in self._positions.items()
        }

    def has_open_orders(self, symbol: str) -> bool:
        return False

    def buy_with_stop(self, symbol: str, qty: int, stop_price: float, ref_price: float | None = None) -> OrderResult:
        price = self.prices[symbol]
        require_protective_stop(price, stop_price)
        if qty * price > self.cash + 1e-9:
            raise ValueError("Insufficient cash")
        self.cash -= qty * price
        self._positions[symbol] = (qty, price)
        o = OrderResult(f"sim-{len(self.orders) + 1}", symbol, "buy", qty, "filled", price, stop_price)
        self.orders.append(o)
        return o

    def close_position(self, symbol: str) -> OrderResult:
        qty, _ = self._positions.pop(symbol)
        price = self.prices[symbol]
        self.cash += qty * price
        o = OrderResult(f"sim-{len(self.orders) + 1}", symbol, "sell", qty, "filled", price)
        self.orders.append(o)
        return o
