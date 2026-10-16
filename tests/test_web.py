"""The web dashboard shows state, can only toggle the kill switch, and rejects cross-site requests."""
import json
import threading
from dataclasses import replace
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from bot.broker import SimBroker
from bot.config import load_config
from bot.journal import Journal
from bot.state import BotState
from bot.web import Dashboard, make_handler
from tests.conftest import make_bars, uptrend_pullback_rally


class FakeData:
    def __init__(self):
        self.bars = make_bars(uptrend_pullback_rally())

    def get_bars(self, symbol, lookback_bars=260, start=None):
        return self.bars

    def latest_price(self, symbol):
        return float(self.bars["close"].iloc[-1])


@pytest.fixture
def dash(tmp_path, monkeypatch):
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "SYMBOLS", "DATA_DIR", "KILL_SWITCH_FILE", "TRADING_MODE"):
        monkeypatch.delenv(k, raising=False)
    cfg = replace(load_config(None, require_keys=False), data_dir=tmp_path / "data", kill_switch_file=tmp_path / "STOP")
    broker = SimBroker(50_000)
    broker.set_price("SPY", 100.0)
    broker.buy_with_stop("SPY", 10, 90.0)
    state = BotState.load(cfg.state_file)
    state.stops["SPY"], state.initial_equity = 90.0, 40_000.0
    state.save()
    Journal(cfg.log_dir).record("trade", symbol="SPY", side="BUY", qty=10, price=100.0)
    return Dashboard(cfg, broker, FakeData())


def test_status_reads_saved_state(dash):
    s = dash.status()
    assert s["mode"] == "paper" and s["kill_switch"] is False
    assert s["initial_equity"] == 40_000.0
    assert s["positions"] == [{"symbol": "SPY", "qty": 10, "entry": 100.0, "price": 100.0,
                               "unrealized_pl": 0.0, "stop": 90.0}]
    assert "key" not in json.dumps(s).lower()


def test_signals_journal_and_backtest(dash):
    assert dash.signals()[0]["action"] in ("BUY", "SELL", "HOLD")
    assert dash.journal()[-1]["event"] == "trade"
    r = dash.backtest("spy", 5, 10_000)
    assert len(r["curve"]) == len(dash.data.bars)
    date, strat, bh = r["curve"][0]
    assert isinstance(date, str) and strat == 10_000
    with pytest.raises(ValueError):
        dash.backtest("TSLA", 5, 10_000)  # only configured symbols


def test_kill_switch_toggles_the_file(dash):
    assert dash.set_kill_switch(True) == {"kill_switch": True}
    assert dash.cfg.kill_switch_file.exists() and dash.status()["kill_switch"]
    assert dash.set_kill_switch(False) == {"kill_switch": False}


@pytest.fixture
def server(dash):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = srv.server_address[1]
    srv.RequestHandlerClass = make_handler(dash, port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port
    srv.shutdown()
    srv.server_close()


def request(port, method, path, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    r = conn.getresponse()
    return r.status, r.read()


def test_http_page_and_api(server, dash):
    status, page = request(server, "GET", "/")
    assert status == 200 and b"Trading Bot" in page
    status, body = request(server, "GET", "/api/status")
    assert status == 200 and json.loads(body)["equity"] == 50_000
    assert request(server, "GET", "/api/backtest?symbol=TSLA")[0] == 400


def test_http_rejects_foreign_host(server):
    assert request(server, "GET", "/api/status", headers={"Host": "evil.example:80"})[0] == 403


def test_http_kill_switch_needs_custom_header(server, dash):
    on = json.dumps({"on": True})
    json_ct = {"Content-Type": "application/json"}
    assert request(server, "POST", "/api/kill-switch", on, json_ct)[0] == 403
    assert not dash.cfg.kill_switch_file.exists()
    assert request(server, "POST", "/api/kill-switch", "nope", {**json_ct, "X-Bot-Dashboard": "1"})[0] == 400
    status, body = request(server, "POST", "/api/kill-switch", on, {**json_ct, "X-Bot-Dashboard": "1"})
    assert status == 200 and json.loads(body) == {"kill_switch": True}
    assert dash.cfg.kill_switch_file.exists()
