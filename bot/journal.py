"""Audit trail: every signal, decision, order and trade is written to
`data/logs/journal.jsonl` (one JSON object per line) and to the human-readable
`data/logs/bot.log`."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

log = logging.getLogger("bot")


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_dir / "bot.log", maxBytes=2_000_000, backupCount=5)
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    root.handlers = [file_handler, console]


class Journal:
    def __init__(self, log_dir: Path | None):
        self.path = (log_dir / "journal.jsonl") if log_dir else None
        self.memory: list[dict] = []  # also kept in memory (used by tests and the dashboard)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields) -> dict:
        entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **fields}
        self.memory.append(entry)
        if self.path:
            with self.path.open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        level = logging.WARNING if event in ("halt", "error", "stop_loss") else logging.INFO
        log.log(level, "%s %s", event.upper(), json.dumps(fields, default=str))
        return entry

    def events(self, event: str | None = None, last: int = 10) -> list[dict]:
        if self.path and self.path.exists():
            lines = self.path.read_text().splitlines()
            entries = [json.loads(line) for line in lines if line.strip()]
        else:
            entries = self.memory
        if event:
            entries = [e for e in entries if e["event"] == event]
        return entries[-last:]
