"""
Data loading + resampling.

Expects a base CSV with at least: time, open, high, low, close (volume optional).
'time' should be timezone-aware or naive-but-GMT/UTC timestamps: the whole
strategy is defined in GMT (the 7am-10am session filter), so if your data
comes in a different timezone, convert it to UTC/GMT before using this.
"""

import pandas as pd


def load_ohlc_csv(path: str, time_col: str = 'time') -> pd.DataFrame:
    df = pd.read_csv(path)
    df[time_col] = pd.to_datetime(df[time_col], utc=True)
    df = df.rename(columns={time_col: 'time'})
    df = df.sort_values('time').reset_index(drop=True)
    cols = ['time', 'open', 'high', 'low', 'close'] + (
        ['volume'] if 'volume' in df.columns else []
    )
    return df[cols]


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """
    rule examples: '15min', '4h'

    Input rows must be stamped by bar OPEN time (the convention used by
    Dukascopy, Tickstory and most vendors): the row stamped 08:00 covers
    08:00 to 08:01.

    Output candles are labelled by CLOSE time: the 15min candle labelled
    08:15 holds the rows stamped 08:00 to 08:14, so its label is the moment
    its OHLC becomes fully known. Strategies rely on this to avoid lookahead.

    4H candles are anchored to 00:00 UTC (00:00, 04:00, 08:00, ...).
    """
    d = df.set_index('time')
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    if 'volume' in d.columns:
        agg['volume'] = 'sum'
    out = d.resample(rule, label='right', closed='left', origin='start_day').agg(agg)
    out = out.dropna(subset=['open', 'high', 'low', 'close']).reset_index()
    return out
