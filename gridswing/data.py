"""Download and cache daily OHLC data for an NSE symbol via yfinance."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _cache_path(symbol: str) -> Path:
    return DATA_DIR / f"{symbol.replace('.', '_')}.parquet"


def get_ohlc(symbol: str, start: str, end: str | None = None, refresh: bool = False) -> pd.DataFrame:
    """Return daily OHLC for `symbol` between start/end (inclusive), cached as parquet.

    Cache stores the full downloaded history; each call slices to [start, end].
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(symbol)

    if path.exists() and not refresh:
        df = pd.read_parquet(path)
    else:
        df = yf.download(symbol, start="2005-01-01", progress=False, auto_adjust=True)
        if df is None or df.empty:
            raise ValueError(f"No data returned for symbol '{symbol}' — check the symbol or your connection.")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index.name = "date"
        df = df.dropna(subset=["Close"])
        df.to_parquet(path)

    df = df.loc[(df.index >= pd.Timestamp(start))]
    if end:
        df = df.loc[(df.index <= pd.Timestamp(end))]
    if df.empty:
        raise ValueError(f"No cached data for '{symbol}' in range {start}..{end}. Try refresh=True.")
    return df
