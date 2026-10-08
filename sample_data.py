"""
Generates SYNTHETIC 1-minute OHLC data so the strategy code can be run
end-to-end before you have real data.

This is NOT a substitute for real backtesting. Random-walk-ish synthetic
data can produce FVGs and trades, but any win rate / profit factor from it
is meaningless: it doesn't reflect real market structure, real
volatility clustering, or the specific inefficiency (if any) this
strategy is trying to capture. Use it only to confirm the code runs and
the trade logic looks sane, then swap in real data.
"""

import numpy as np
import pandas as pd


def generate_sample_1m(start='2024-01-01', days=120, start_price=18000.0, seed=42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n_minutes = days * 24 * 60
    times = pd.date_range(start=start, periods=n_minutes, freq='1min', tz='UTC')

    hours = (times.hour + times.minute / 60.0).to_numpy(dtype=float)
    # rough intraday vol profile: quieter in Asia, livelier London/NY
    vol_profile = 0.6 + 0.9 * np.exp(-((hours - 8) ** 2) / 18) + 0.7 * np.exp(-((hours - 14.5) ** 2) / 10)

    # Work in raw index points (not log-returns) to avoid runaway compounding
    # over long synthetic runs. ~1.3 pts/min base noise is roughly NAS100-scale.
    base_minute_std = 1.3
    noise = rng.normal(0, 1, n_minutes) * base_minute_std * vol_profile

    # occasional displacement bursts so FVGs actually form
    burst_mask = rng.random(n_minutes) < 0.003
    bursts = np.zeros(n_minutes)
    bursts[burst_mask] = rng.normal(0, 18, burst_mask.sum())

    increments = noise + bursts
    close = start_price + np.cumsum(increments)

    open_ = np.roll(close, 1)
    open_[0] = start_price
    wick = np.abs(rng.normal(0, 1.0, n_minutes)) + 0.2
    high = np.maximum(open_, close) + wick * rng.random(n_minutes)
    low = np.minimum(open_, close) - wick * rng.random(n_minutes)

    df = pd.DataFrame({
        'time': times, 'open': open_, 'high': high, 'low': low, 'close': close,
    })
    # weekend gap: flatten Sat/Sun (indices/CFDs mostly pause then too)
    is_weekend = times.dayofweek >= 5
    df.loc[is_weekend, ['open', 'high', 'low', 'close']] = np.nan
    df = df.dropna().reset_index(drop=True)
    return df
