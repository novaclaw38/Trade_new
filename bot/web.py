"""Local web dashboard: `python -m bot web`, then open http://127.0.0.1:8765

Read-only view of the account, positions, signals, journal and a backtest,
plus one control: the kill switch, which can only STOP new trades. The
dashboard never places or closes orders; trading stays with `python -m bot run`.

Safety:
* Listens on 127.0.0.1 only, so other machines can't reach it.
* Rejects requests whose Host header isn't localhost (blocks DNS rebinding).
* Writes need a custom header, which other websites' pages can't send
  cross-origin without a CORS preflight this server never approves.
* API keys stay in this process; no endpoint returns them.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bot.journal import Journal
from bot.state import BotState
from bot.strategy import generate_signal

log = logging.getLogger(__name__)

WRITE_HEADER = "X-Bot-Dashboard"
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
PAGE = Path(__file__).with_name("web_page.html")


class Dashboard:
    """The data behind each endpoint. Plain methods so tests need no HTTP."""

    def __init__(self, cfg, broker, data):
        self.cfg = cfg
        self.broker = broker
        self.data = data

    def _state(self) -> BotState:
        # Re-read every time: `python -m bot run` updates it from another process.
        return BotState.load(self.cfg.state_file)

    def status(self) -> dict:
        account = self.broker.get_account()
        positions = self.broker.get_positions()
        state = self._state()
        return {
            "mode": self.cfg.trading_mode,
            "symbols": list(self.cfg.symbols),
            "market_open": self.broker.market_is_open(),
            "equity": account.equity,
            "last_equity": account.last_equity,
            "buying_power": account.buying_power,
            "initial_equity": state.initial_equity,
            "halted": state.halted,
            "halt_reason": state.halt_reason,
            "kill_switch": self.cfg.kill_switch_file.exists(),
            "ai_confirmation": self.cfg.ai_confirmation,
            "positions": [
                {"symbol": p.symbol, "qty": p.qty, "entry": p.avg_entry_price, "price": p.current_price,
                 "unrealized_pl": p.unrealized_pl, "stop": state.stops.get(p.symbol)}
                for p in positions.values()
            ],
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def signals(self) -> list[dict]:
        positions = self.broker.get_positions()
        out = []
        for symbol in self.cfg.symbols:
            try:
                s = generate_signal(symbol, self.data.get_bars(symbol), self.cfg.strategy,
                                    in_position=symbol in positions)
                out.append({"symbol": symbol, "action": s.action.value, "reasons": s.reasons,
                            "close": s.close, "stop": s.stop_price, "bar_time": s.bar_time,
                            "indicators": s.indicators})
            except Exception as e:  # noqa: BLE001 - show the error, keep the other symbols
                out.append({"symbol": symbol, "action": "ERROR", "reasons": [f"{type(e).__name__}: {e}"]})
        return out

    def journal(self, last=100) -> list[dict]:
        last = max(1, min(int(last), 1000))  # a bad value raises ValueError -> HTTP 400
        return Journal(self.cfg.log_dir).events(last=last)

    def backtest(self, symbol: str, years: int, cash: float) -> dict:
        from bot.backtest import run_backtest

        symbol = symbol.upper()
        if symbol not in self.cfg.symbols:
            raise ValueError(f"{symbol} is not in SYMBOLS")
        years = max(1, min(int(years), 10))
        bars = self.data.get_bars(symbol, start=datetime.now(timezone.utc) - timedelta(days=365 * years))
        if len(bars) < self.cfg.strategy.min_bars:
            raise ValueError(f"only {len(bars)} bars of history; need {self.cfg.strategy.min_bars}")
        r = run_backtest(bars, symbol, self.cfg.strategy, self.cfg.risk, float(cash))
        r["trades"] = [{**t, "exit_time": str(t["exit_time"])[:10]} for t in r["trades"]]
        return r

    def scorecard(self) -> dict:
        from zoneinfo import ZoneInfo

        from bot.scorecard import compute

        symbol = self.cfg.benchmark_symbol
        today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        history = self.broker.equity_history()
        if not history:
            raise ValueError("The account has no history yet.")
        # Alpaca's history ends at the last close; add today's live equity.
        equity = self.broker.get_account().equity
        history = [h for h in history if h[0] < today] + [(today, equity)]

        start = datetime.fromisoformat(history[0][0]).replace(tzinfo=timezone.utc)
        bars = self.data.get_bars(symbol, start=start - timedelta(days=10))
        closes = [(str(t.date()), float(c)) for t, c in zip(bars.index, bars["close"])]
        try:
            closes = [c for c in closes if c[0] < today] + [(today, self.data.latest_price(symbol))]
        except Exception as e:  # noqa: BLE001 - fall back to the last close
            log.info("scorecard: no live %s price (%s); using the last close", symbol, e)

        result = compute(history, closes, symbol, today)
        result["trades"] = self.broker.filled_orders_since(history[0][0])
        result["mode"] = self.cfg.trading_mode
        return result

    def set_kill_switch(self, on: bool) -> dict:
        path = self.cfg.kill_switch_file
        if on:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Created from the web dashboard. Delete this file to allow new trades again.\n")
        else:
            path.unlink(missing_ok=True)
        return {"kill_switch": path.exists()}


def make_handler(dash: Dashboard, port: int):
    allowed = {f"{h}:{port}" for h in ALLOWED_HOSTS}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.info("web: " + fmt, *args)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def _host_ok(self) -> bool:
            if self.headers.get("Host", "") in allowed:
                return True
            self._json(403, {"error": "Forbidden host"})
            return False

        def _call(self, fn, *args) -> None:
            try:
                self._json(200, fn(*args))
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001 - report, don't crash the server
                log.warning("web endpoint failed: %s", e)
                self._json(502, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self):
            if not self._host_ok():
                return
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            if url.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/status":
                self._call(dash.status)
            elif url.path == "/api/scorecard":
                self._call(dash.scorecard)
            elif url.path == "/api/signals":
                self._call(dash.signals)
            elif url.path == "/api/journal":
                self._call(dash.journal, q.get("last", 100))
            elif url.path == "/api/backtest":
                self._call(dash.backtest, q.get("symbol", dash.cfg.symbols[0]),
                           q.get("years", 5), q.get("cash", 10_000))
            else:
                self._json(404, {"error": "Not found"})

        def do_POST(self):
            if not self._host_ok():
                return
            if self.headers.get(WRITE_HEADER) != "1":
                self._json(403, {"error": f"Missing {WRITE_HEADER} header"})
                return
            if urlparse(self.path).path != "/api/kill-switch":
                self._json(404, {"error": "Not found"})
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            except ValueError:
                self._json(400, {"error": "Body must be JSON"})
                return
            if not isinstance(body.get("on"), bool):
                self._json(400, {"error": "Body must be {\"on\": true|false}"})
                return
            self._call(dash.set_kill_switch, body["on"])

    return Handler


def serve(dash: Dashboard, port: int = 8765) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(dash, port))
    print(f"Dashboard running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    finally:
        server.server_close()
