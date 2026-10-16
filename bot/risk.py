"""Hard risk controls.

Every new position the bot opens must be approved by `RiskManager.approve_entry`.
The rules here are the most important code in the project: the strategy decides
*whether* we'd like to trade; this module decides *whether we're allowed to* and
*how big* the trade can be.

Four controls, none of which can be switched off:
  1. Max loss per trade   - size every position so that hitting its stop-loss
                            loses at most `risk_per_trade` of account equity.
  2. Stop-loss required   - an entry without a valid stop below the entry price
                            is rejected (and the broker layer refuses it too).
  3. Daily loss limit     - once equity falls `daily_loss_limit` below the start
                            of the day, new entries are halted until tomorrow.
                            The halt is saved to disk, so restarting the bot
                            does not clear it.
  4. Max position size    - no single position may exceed `max_position_pct`
                            of equity, whatever the stop distance says.

On top of the configurable limits there are hard ceilings below. If someone
edits the config to something reckless, the bot refuses to start.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from bot.state import BotState

log = logging.getLogger(__name__)

# Absolute ceilings, deliberately not configurable.
HARD_MAX_RISK_PER_TRADE = 0.02    # never risk more than 2% of equity on one trade
HARD_MAX_POSITION_PCT = 0.25      # never put more than 25% of equity in one position
HARD_MAX_DAILY_LOSS = 0.05        # always halt by a 5% daily drawdown at the latest
HARD_MAX_OPEN_POSITIONS = 5


@dataclass(frozen=True)
class RiskLimits:
    """Risk settings, as fractions (0.01 == 1%). Validated on construction."""

    risk_per_trade: float = 0.01
    max_position_pct: float = 0.10
    daily_loss_limit: float = 0.02
    max_open_positions: int = 3

    def __post_init__(self) -> None:
        for name, value, ceiling in (
            ("risk_per_trade", self.risk_per_trade, HARD_MAX_RISK_PER_TRADE),
            ("max_position_pct", self.max_position_pct, HARD_MAX_POSITION_PCT),
            ("daily_loss_limit", self.daily_loss_limit, HARD_MAX_DAILY_LOSS),
        ):
            if not (0 < value <= ceiling):
                raise ValueError(
                    f"{name}={value:.4f} is not allowed: it must be above 0 and at most "
                    f"the hard ceiling of {ceiling:.2%}."
                )
        if not (1 <= self.max_open_positions <= HARD_MAX_OPEN_POSITIONS):
            raise ValueError(
                f"max_open_positions={self.max_open_positions} must be between 1 and "
                f"{HARD_MAX_OPEN_POSITIONS}."
            )


@dataclass
class Decision:
    approved: bool
    qty: int = 0
    reason: str = ""


class RiskManager:
    def __init__(self, limits: RiskLimits, state: BotState):
        self.limits = limits
        self.state = state

    # --- Control 3: daily loss limit -------------------------------------
    def update_daily_status(self, equity: float, day_start_equity: float, today: str) -> None:
        """Call once per cycle. Halts trading for the rest of `today` if the
        daily loss limit has been reached. A new day clears the halt."""
        self.state.roll_day(today)
        if self.state.halted:
            return
        if day_start_equity <= 0:
            self.state.halt("Could not determine start-of-day equity; halting to be safe.")
            return
        loss = (day_start_equity - equity) / day_start_equity
        if loss >= self.limits.daily_loss_limit:
            self.state.halt(
                f"Daily loss limit hit: down {loss:.2%} today "
                f"(limit {self.limits.daily_loss_limit:.2%}). No new trades until tomorrow."
            )
            log.warning(self.state.halt_reason)

    # --- Controls 1, 2 and 4: sizing -------------------------------------
    def position_size(self, equity: float, entry: float, stop: float, buying_power: float) -> Decision:
        """Whole-share quantity that respects the per-trade risk and the
        position-size cap. Returns qty 0 (rejected) if anything is off."""
        if entry is None or not math.isfinite(entry) or entry <= 0:
            return Decision(False, reason="Invalid entry price.")
        if stop is None or not math.isfinite(stop) or stop <= 0 or stop >= entry:
            return Decision(False, reason=f"Invalid stop-loss {stop!r} for entry {entry:.2f}; a stop below entry is required.")
        if equity <= 0:
            return Decision(False, reason="Account equity is not positive.")

        risk_per_share = entry - stop
        max_loss = equity * self.limits.risk_per_trade
        qty_by_risk = math.floor(max_loss / risk_per_share)
        qty_by_size = math.floor(equity * self.limits.max_position_pct / entry)
        qty_by_cash = math.floor(max(buying_power, 0) / entry)
        qty = min(qty_by_risk, qty_by_size, qty_by_cash)

        if qty < 1:
            return Decision(
                False,
                reason=(
                    f"Position would be under 1 share (risk allows {qty_by_risk}, size cap "
                    f"{qty_by_size}, cash {qty_by_cash}). Skipping."
                ),
            )
        return Decision(
            True,
            qty=qty,
            reason=(
                f"Buy {qty} @ ~{entry:.2f}, stop {stop:.2f}: max loss ${qty * risk_per_share:,.2f} "
                f"({qty * risk_per_share / equity:.2%} of equity), position "
                f"{qty * entry / equity:.2%} of equity."
            ),
        )

    def approve_entry(
        self,
        *,
        equity: float,
        buying_power: float,
        entry: float,
        stop: float,
        open_positions: int,
        kill_switch: bool = False,
    ) -> Decision:
        """The single gate every new position goes through."""
        if kill_switch:
            return Decision(False, reason="Kill switch file present: new trades disabled.")
        if self.state.halted:
            return Decision(False, reason=f"Trading halted: {self.state.halt_reason}")
        if open_positions >= self.limits.max_open_positions:
            return Decision(False, reason=f"Already at max open positions ({self.limits.max_open_positions}).")
        return self.position_size(equity, entry, stop, buying_power)
