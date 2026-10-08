"""
Convert a dukascopy-node CSV into the backtester's expected format.

Usage:
    python3 convert_dukascopy.py input.csv output.csv

Handles both dukascopy-node timestamp styles:
  - default: epoch milliseconds (e.g. 1704067200000)
  - formatted: datetime strings (if you used the -df flag)

Output: time,open,high,low,close in UTC/GMT, sorted, deduped --
exactly what the backtester's data loader expects.

Also prints sanity checks so you can eyeball that the data is healthy
BEFORE running a backtest on it: date range, row count, biggest gaps,
and a few sample rows.
"""

import sys
import pandas as pd


def convert(input_path: str, output_path: str):
    df = pd.read_csv(input_path)
    df.columns = [c.strip().lower() for c in df.columns]

    # Tickstory "generic bar format": separate Date (yyyymmdd) + Timestamp (HH:MM:SS)
    # columns -> combine them into one datetime before the normal path.
    if 'date' in df.columns and 'timestamp' in df.columns and \
            df['timestamp'].astype(str).str.contains(':').any():
        combined = df['date'].astype(str).str.zfill(8) + ' ' + df['timestamp'].astype(str)
        df['time'] = pd.to_datetime(combined, format='%Y%m%d %H:%M:%S', utc=True)
        ts_col = 'time'
    else:
        # find the single timestamp column
        ts_col = None
        for cand in ('timestamp', 'time', 'date', 'datetime'):
            if cand in df.columns:
                ts_col = cand
                break
        if ts_col is None:
            sys.exit(f"ERROR: no timestamp column found. Columns present: {list(df.columns)}")

    required = {'open', 'high', 'low', 'close'}
    missing = required - set(df.columns)
    if missing:
        sys.exit(f"ERROR: missing price columns: {missing}. Columns present: {list(df.columns)}")

    # epoch ms (numeric) vs datetime string (skip if already combined above)
    if ts_col != 'time':
        if pd.api.types.is_numeric_dtype(df[ts_col]):
            df['time'] = pd.to_datetime(df[ts_col], unit='ms', utc=True)
        else:
            df['time'] = pd.to_datetime(df[ts_col], utc=True)

    # sanity guard: if parsed dates land outside plausible market-data years,
    # the timestamp unit/format is wrong: refuse rather than write garbage
    yr_min, yr_max = df['time'].dt.year.min(), df['time'].dt.year.max()
    if yr_min < 2000 or yr_max > 2100:
        sys.exit(f"ERROR: parsed timestamps span years {yr_min}-{yr_max}, which can't be right.\n"
                 f"The timestamp column is probably not epoch-milliseconds or a standard datetime.\n"
                 f"First raw value seen: {df[ts_col].iloc[0]!r}")

    out = df[['time', 'open', 'high', 'low', 'close']].copy()

    before = len(out)
    out = out.dropna()
    out = out[(out[['open', 'high', 'low', 'close']] > 0).all(axis=1)]  # drop zero-price junk rows
    out = out.drop_duplicates(subset='time').sort_values('time').reset_index(drop=True)
    dropped = before - len(out)

    # basic OHLC integrity check
    bad = ((out['high'] < out['low']) |
           (out['high'] < out[['open', 'close']].max(axis=1)) |
           (out['low'] > out[['open', 'close']].min(axis=1))).sum()

    out.to_csv(output_path, index=False)

    # ---- sanity report ----
    print(f"rows written        : {len(out):,}  (dropped {dropped} bad/duplicate rows)")
    print(f"date range          : {out['time'].iloc[0]}  ->  {out['time'].iloc[-1]}")
    print(f"OHLC integrity fails: {bad}  (should be 0 or very near it)")

    gaps = out['time'].diff().dropna()
    big_gaps = gaps[gaps > pd.Timedelta(hours=3)]
    print(f"gaps > 3h           : {len(big_gaps)} (weekends + daily maintenance breaks are normal)")
    if len(big_gaps):
        print("largest 3 gaps:")
        largest = gaps.nlargest(3)
        for idx, g in largest.items():
            print(f"  {out['time'].iloc[idx - 1]} -> {out['time'].iloc[idx]}  ({g})")

    # does the session window actually have data?
    in_session = out[(out['time'].dt.hour >= 7) & (out['time'].dt.hour < 10)]
    pct = 100 * len(in_session) / len(out) if len(out) else 0
    print(f"rows in 7-10am GMT  : {len(in_session):,} ({pct:.1f}% of data)")
    if pct < 5:
        print("  WARNING: very little data in the strategy's session window.")
        print("  Check the timezone: timestamps may not actually be GMT.")

    print("\nfirst 3 rows:")
    print(out.head(3).to_string(index=False))
    print(f"\nsaved -> {output_path}")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit("usage: python3 convert_dukascopy.py input.csv output.csv")
    convert(sys.argv[1], sys.argv[2])
