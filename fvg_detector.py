"""
Fair Value Gap (FVG) detection.

A bearish FVG is a 3-candle pattern where candle A's low sits above candle
C's high, leaving an unfilled gap. It forms during a strong down move
(candle B is the displacement candle).

    A: [-------]
                   gap (the FVG zone)
    C:      [-------]

Zone:
    top    = A.low   (the ceiling of the gap)
    bottom = C.high  (the floor of the gap)
    stop_ref = A.high  (used for stop placement per the strategy notes:
               "stop loss = top of the 1st candle that forms the FVG",
               i.e. the full candle high, not just the gap edge)

A bullish FVG is the mirror image (candle A's high below candle C's low).

IMPORTANT: FVG formation only ever looks at candles up to and including
candle C, so precomputing this for a whole dataframe is NOT lookahead bias.
The strategy logic later decides when it's "allowed" to react to a given FVG.
"""

import pandas as pd


def detect_fvgs(df: pd.DataFrame) -> list[dict]:
    """
    df must have columns: time, open, high, low, close, sorted ascending,
    with a plain integer index (use df.reset_index(drop=True) beforehand).

    Returns a list of dicts, one per detected FVG:
        {
            'direction': 'bullish' | 'bearish',
            'formed_idx': int,          # index of candle C
            'formed_time': Timestamp,   # close time of candle C
            'top': float,
            'bottom': float,
            'stop_ref': float,
            'candle_a_idx': int,
        }
    """
    fvgs = []
    highs = df['high'].values
    lows = df['low'].values
    times = df['time'].tolist()  # keep tz-aware Timestamp objects (not numpy datetime64)

    for i in range(2, len(df)):
        a_low, a_high = lows[i - 2], highs[i - 2]
        c_low, c_high = lows[i], highs[i]

        # Bearish FVG: A's low is above C's high -> gap in between
        if a_low > c_high:
            fvgs.append({
                'direction': 'bearish',
                'formed_idx': i,
                'formed_time': times[i],
                'top': a_low,
                'bottom': c_high,
                'stop_ref': a_high,
                'candle_a_idx': i - 2,
            })

        # Bullish FVG: A's high is below C's low -> gap in between
        if a_high < c_low:
            fvgs.append({
                'direction': 'bullish',
                'formed_idx': i,
                'formed_time': times[i],
                'bottom': a_high,
                'top': c_low,
                'stop_ref': a_low,
                'candle_a_idx': i - 2,
            })

    return fvgs


def zones_overlap(a_bottom, a_top, b_bottom, b_top) -> bool:
    """Simple range-overlap test, used to check a 15m FVG actually sits
    inside/near the 4H FVG zone rather than being an unrelated gap elsewhere."""
    return a_bottom <= b_top and b_bottom <= a_top
