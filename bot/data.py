"""Market data ingestion via Alpaca's market data API (free IEX feed by default)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

BAR_LENGTH = {"1Day": timedelta(days=1), "1Hour": timedelta(hours=1)}


def drop_incomplete_bar(bars: pd.DataFrame, timeframe: str, now: datetime) -> pd.DataFrame:
    """Signals must only use finished bars; today's half-formed bar can flip
    a crossover back and forth during the day."""
    if len(bars) and bars.index[-1] + BAR_LENGTH[timeframe] > now:
        return bars.iloc[:-1]
    return bars


def load_csv(path: str) -> pd.DataFrame:
    """CSV with columns: timestamp, open, high, low, close[, volume]."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    ts_col = "timestamp" if "timestamp" in df.columns else "date"
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df = df.set_index(ts_col).sort_index()
    return df[["open", "high", "low", "close"]].astype(float)


class AlpacaData:
    def __init__(self, api_key: str, secret_key: str, timeframe: str = "1Day", feed: str = "iex"):
        from alpaca.data.historical import StockHistoricalDataClient

        self.client = StockHistoricalDataClient(api_key, secret_key)
        self.api_key, self.secret_key = api_key, secret_key
        self.timeframe = timeframe
        self.feed = feed

    def _feed(self):
        from alpaca.data.enums import DataFeed

        return DataFeed(self.feed)

    def get_bars(self, symbol: str, lookback_bars: int = 260, start: datetime | None = None) -> pd.DataFrame:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        now = datetime.now(timezone.utc)
        if start is None:
            # Calendar days needed to cover `lookback_bars` trading bars, with margin.
            days = lookback_bars * 1.6 + 10 if self.timeframe == "1Day" else lookback_bars / 6.5 * 1.6 + 10
            start = now - timedelta(days=days)
        tf = TimeFrame.Day if self.timeframe == "1Day" else TimeFrame.Hour
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start, feed=self._feed())
        df = self.client.get_stock_bars(req).df
        if df.empty:
            return df
        df = df.xs(symbol, level="symbol") if "symbol" in df.index.names else df
        df = df[["open", "high", "low", "close"]].astype(float).sort_index()
        return drop_incomplete_bar(df, self.timeframe, now)

    def latest_price(self, symbol: str) -> float:
        from alpaca.data.requests import StockLatestTradeRequest

        trade = self.client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self._feed())
        )[symbol]
        return float(trade.price)

    def recent_headlines(self, symbol: str, hours: int = 48, limit: int = 10) -> list[str]:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest

        client = NewsClient(self.api_key, self.secret_key)
        start = datetime.now(timezone.utc) - timedelta(hours=hours)
        news = client.get_news(NewsRequest(symbols=symbol, start=start, limit=limit))
        return [n.headline for n in news.data.get("news", [])]
