"""Candle construction. These tests pin down the timestamp convention:
input rows are stamped by bar OPEN time, output candles are labelled by CLOSE time."""

import numpy as np
import pandas as pd

from data_utils import load_ohlc_csv, resample_ohlc


def minute_frame(start, n, values=None):
    """1-minute rows stamped by open time. Row k has open=k, high=k+0.5, low=k-0.5, close=k+0.25."""
    t = pd.date_range(start, periods=n, freq='1min', tz='UTC')
    k = np.arange(n, dtype=float) if values is None else values
    return pd.DataFrame({'time': t, 'open': k, 'high': k + 0.5, 'low': k - 0.5, 'close': k + 0.25})


def test_15min_candle_holds_the_correct_minutes():
    out = resample_ohlc(minute_frame('2024-01-02 08:00', 30), '15min')
    first = out.iloc[0]
    # candle labelled 08:15 must hold the minutes stamped 08:00..08:14
    assert first['time'] == pd.Timestamp('2024-01-02 08:15', tz='UTC')
    assert first['open'] == 0.0
    assert first['close'] == 14.25
    assert first['high'] == 14.5
    assert first['low'] == -0.5
    second = out.iloc[1]
    assert second['time'] == pd.Timestamp('2024-01-02 08:30', tz='UTC')
    assert second['open'] == 15.0 and second['close'] == 29.25


def test_candle_never_contains_data_from_at_or_after_its_label():
    """Lookahead regression test: a spike in the minute stamped 08:15 must NOT
    appear in the candle labelled 08:15 (that candle closed at 08:15)."""
    m1 = minute_frame('2024-01-02 08:00', 30)
    spike = m1['time'] == pd.Timestamp('2024-01-02 08:15', tz='UTC')
    m1.loc[spike, 'high'] = 1000.0
    out = resample_ohlc(m1, '15min').set_index('time')
    assert out.loc[pd.Timestamp('2024-01-02 08:15', tz='UTC'), 'high'] < 1000.0
    assert out.loc[pd.Timestamp('2024-01-02 08:30', tz='UTC'), 'high'] == 1000.0


def test_4h_candles_are_anchored_to_midnight_utc():
    out = resample_ohlc(minute_frame('2024-01-02 00:00', 8 * 60), '4h')
    assert list(out['time']) == [pd.Timestamp('2024-01-02 04:00', tz='UTC'),
                                 pd.Timestamp('2024-01-02 08:00', tz='UTC')]
    assert out.iloc[0]['open'] == 0.0 and out.iloc[0]['close'] == 239.25


def test_gaps_in_the_data_do_not_create_empty_candles():
    a = minute_frame('2024-01-02 08:00', 15)
    b = minute_frame('2024-01-02 12:00', 15)
    out = resample_ohlc(pd.concat([a, b], ignore_index=True), '15min')
    assert len(out) == 2


def test_loader_returns_sorted_utc_timestamps(tmp_path):
    p = tmp_path / 'x.csv'
    p.write_text('time,open,high,low,close\n'
                 '2024-01-02 08:01:00,2,3,1,2\n'
                 '2024-01-02 08:00:00,1,2,0,1\n')
    df = load_ohlc_csv(str(p))
    assert str(df['time'].dt.tz) == 'UTC'
    assert df['time'].is_monotonic_increasing
    assert df.iloc[0]['open'] == 1
