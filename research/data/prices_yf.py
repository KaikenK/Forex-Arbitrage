"""Daily FX price loader via yfinance -> event-study `PriceSeries`.

Free yfinance only serves intraday for ~60 days, so the v1 atlas runs on *daily* bars with
daily horizons. Each daily bar is stamped at an approximate FX day-close hour (UTC) so an
event earlier that day maps leak-safely to that day's close; later events roll to the next.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from research.eventstudy.schema import PriceSeries

DEFAULT_CLOSE_HOUR_UTC = 21  # ~ New York FX close


def frame_to_series(
    frame: pd.DataFrame,
    pair: str,
    *,
    close_hour_utc: int = DEFAULT_CLOSE_HOUR_UTC,
) -> PriceSeries:
    """Convert a yfinance OHLC frame (DatetimeIndex + Close) into a PriceSeries."""
    close = frame["Close"]
    if isinstance(close, pd.DataFrame):  # yfinance MultiIndex columns for a single ticker
        close = close.iloc[:, 0]
    close = close.dropna()

    times: list[datetime] = []
    prices: list[float] = []
    for index_value, price in close.items():
        stamp = pd.Timestamp(index_value).to_pydatetime()
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        else:
            stamp = stamp.astimezone(UTC)
        stamp = stamp.replace(hour=close_hour_utc, minute=0, second=0, microsecond=0)
        times.append(stamp)
        prices.append(float(price))
    return PriceSeries(pair=pair, times=times, prices=prices)


def load_price_series(
    ticker: str,
    pair: str,
    start: datetime,
    end: datetime,
    *,
    close_hour_utc: int = DEFAULT_CLOSE_HOUR_UTC,
) -> PriceSeries:
    import yfinance as yf

    frame = yf.download(
        ticker,
        start=(start - timedelta(days=5)).date().isoformat(),
        end=(end + timedelta(days=10)).date().isoformat(),
        interval="1d",
        progress=False,
        auto_adjust=True,
    )
    if frame is None or len(frame) == 0:
        raise RuntimeError(f"yfinance returned no data for {ticker}")
    return frame_to_series(frame, pair, close_hour_utc=close_hour_utc)
