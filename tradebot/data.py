"""Candle download from Coinbase's public market-data API (no key needed)."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd
import requests

API = "https://api.exchange.coinbase.com/products/{product}/candles"
MAX_CANDLES = 300  # per request
COLUMNS = ["time", "low", "high", "open", "close", "volume"]
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _get(product: str, start: int, end: int, granularity: int) -> list[list[float]]:
    params = {
        "granularity": granularity,
        "start": pd.Timestamp(start, unit="s", tz="UTC").isoformat(),
        "end": pd.Timestamp(end, unit="s", tz="UTC").isoformat(),
    }
    for attempt in range(6):
        try:
            r = requests.get(API.format(product=product), params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code not in (429, 500, 502, 503, 504):
                r.raise_for_status()
        except requests.ConnectionError:
            pass
        except requests.Timeout:
            pass
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Coinbase candles request failed for {product} {params}")


def fetch_candles(product: str, start: int, end: int, granularity: int = 900) -> pd.DataFrame:
    """Candles with open time in [start, end), ascending, indexed by UTC timestamp."""
    rows: list[list[float]] = []
    step = granularity * MAX_CANDLES
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + step, end)
        # The API treats both ends as inclusive; drop the end candle ourselves.
        rows.extend(_get(product, cursor, chunk_end - granularity, granularity))
        cursor = chunk_end
        time.sleep(0.12)  # stay under the public rate limit
    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df.set_index(pd.DatetimeIndex([], tz="UTC", name="time"))
    df = df.drop_duplicates("time").sort_values("time")
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.set_index("time")[["open", "high", "low", "close", "volume"]]


def cache_path(product: str, granularity: int) -> Path:
    return DATA_DIR / f"{product}_{granularity}.csv.gz"


def load_cached(product: str, granularity: int = 900) -> pd.DataFrame:
    df = pd.read_csv(cache_path(product, granularity), index_col="time", parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def download(product: str, days: int, granularity: int = 900) -> pd.DataFrame:
    end = int(time.time()) // granularity * granularity  # last fully closed candle
    df = fetch_candles(product, end - days * 86400, end, granularity)
    DATA_DIR.mkdir(exist_ok=True)
    df.to_csv(cache_path(product, granularity))
    return df


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Download candle history for backtesting")
    p.add_argument("--products", nargs="+", default=["BTC-USD", "ETH-USD"])
    p.add_argument("--days", type=int, default=1095)
    p.add_argument("--granularity", type=int, default=900)
    a = p.parse_args()
    for prod in a.products:
        d = download(prod, a.days, a.granularity)
        print(f"{prod}: {len(d)} candles {d.index[0]} -> {d.index[-1]}")
