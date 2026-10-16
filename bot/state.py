"""Small JSON file that remembers things between runs.

Persisted so that restarting the bot never "forgets" a daily halt, a stop-loss
level, or that it already acted on a signal.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class BotState:
    day: str = ""                      # trading day (America/New_York) the halt flag belongs to
    halted: bool = False
    halt_reason: str = ""
    stops: dict[str, float] = field(default_factory=dict)        # symbol -> stop-loss price
    acted_bars: dict[str, str] = field(default_factory=dict)     # symbol -> bar time of last BUY signal handled
    initial_equity: float = 0.0        # equity the first time the bot ran, for "P&L since start"
    path: Path | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: Path) -> "BotState":
        if path.exists():
            data = json.loads(path.read_text())
            state = cls(**data)
        else:
            state = cls()
        state.path = path
        return state

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("path")
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)  # atomic: never leaves a half-written state file

    def roll_day(self, today: str) -> None:
        if self.day != today:
            self.day = today
            self.halted = False
            self.halt_reason = ""

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason
        self.save()
