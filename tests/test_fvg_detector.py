"""Fair value gap detection."""

import numpy as np
import pandas as pd

from fvg_detector import detect_fvgs, zones_overlap


def candles(rows, freq='15min'):
    """rows: list of (open, high, low, close)."""
    t = pd.date_range('2024-01-02 08:00', periods=len(rows), freq=freq, tz='UTC')
    o, h, l, c = zip(*rows)
    return pd.DataFrame({'time': t, 'open': o, 'high': h, 'low': l, 'close': c})


def test_bearish_gap_values():
    # A.low 110 > C.high 105 -> gap between 105 and 110
    df = candles([(114, 115, 110, 111), (111, 112, 100, 101), (101, 105, 95, 96)])
    (f,) = detect_fvgs(df)
    assert f['direction'] == 'bearish'
    assert f['top'] == 110 and f['bottom'] == 105
    assert f['stop_ref'] == 115          # full high of candle A, per the strategy notes
    assert f['formed_idx'] == 2
    assert f['formed_time'] == df['time'].iloc[2]


def test_bullish_gap_values():
    # A.high 100 < C.low 105 -> gap between 100 and 105
    df = candles([(96, 100, 95, 99), (99, 110, 98, 109), (109, 112, 105, 111)])
    (f,) = detect_fvgs(df)
    assert f['direction'] == 'bullish'
    assert f['bottom'] == 100 and f['top'] == 105
    assert f['stop_ref'] == 95           # full low of candle A


def test_overlapping_candles_are_not_a_gap():
    df = candles([(105, 110, 100, 104), (104, 108, 99, 100), (100, 101, 96, 97)])
    assert detect_fvgs(df) == []         # A.low 100 vs C.high 101: overlap


def test_touching_edges_are_not_a_gap():
    df = candles([(105, 110, 100, 104), (104, 108, 95, 96), (96, 100, 90, 91)])
    assert detect_fvgs(df) == []         # A.low == C.high: strict inequality required


def test_detection_is_causal():
    """Detecting on a truncated history must give exactly the gaps that the full
    history reports up to that point: no gap may depend on later candles."""
    rng = np.random.default_rng(3)
    close = 100 + np.cumsum(rng.normal(0, 2, 400))
    open_ = np.roll(close, 1)
    open_[0] = 100
    high = np.maximum(open_, close) + rng.random(400)
    low = np.minimum(open_, close) - rng.random(400)
    df = pd.DataFrame({'time': pd.date_range('2024-01-02', periods=400, freq='15min', tz='UTC'),
                       'open': open_, 'high': high, 'low': low, 'close': close})
    full = detect_fvgs(df)
    assert len(full) > 10, 'test data should contain gaps'
    key = lambda f: (f['direction'], f['formed_idx'], f['top'], f['bottom'], f['stop_ref'])
    for k in (60, 150, 399):
        part = detect_fvgs(df.iloc[:k].reset_index(drop=True))
        assert [key(f) for f in part] == [key(f) for f in full if f['formed_idx'] < k]


def test_zone_overlap():
    assert zones_overlap(100, 110, 105, 115)
    assert zones_overlap(100, 110, 110, 120)       # touching counts
    assert not zones_overlap(100, 110, 111, 120)
