# FVG Backtest Engine

An event-driven backtesting engine in Python, built to test an ICT-style fair value gap (FVG) strategy on index CFDs. It has a pluggable strategy interface and a reproducible analysis of what the strategy actually does on three years of 1-minute US30 and NAS100 data.

## Why this exists

This was a learning project. I wanted to build a backtester properly and test a real strategy through it, and I did not expect the strategy to be profitable. The strategy was the test case, not the point. What I wanted out of it was the engineering and the discipline: event-driven fills, honest costs, no lookahead, statistics that say how much a small sample can and cannot tell you, and checking the work for mistakes along the way.

The headline result is a negative one: **the strategy shows no demonstrable edge.** 

## What I found

Full write-up with every table: [docs/REPORT.md](docs/REPORT.md).

- **No demonstrable edge.** Baseline (07:00-10:00 GMT, 1:2 RR): US30 -0.30R per trade over 34 trades, NAS100 +0.05R over 39. The instruments disagree in sign and both bootstrap 95% intervals include zero.
- **The entry trigger is noise.** Across about 15,000 fifteen-minute gaps per instrument, forward 1h and 4h moves after bullish and bearish gaps do not differ from the market's unconditional drift (largest |t| = 1.32).
- **One pattern runs against the thesis.** After price first revisits a bearish 4H gap, the next 24 hours rose 39 to 47 points more than drift on both instruments (t of 2.1 and 2.4). Suggestive only: overlapping windows, several comparisons, and concentrated in 2025-2026 on US30.
- **Small samples are fragile.** A one-minute candle misalignment that was found and fixed during review moved the 35-trade baselines by 0.14R to 0.22R per trade (enough to flip NAS100's sign) but moved the 160-trade samples by 0.05R or less. The before/after table is in section 8.2 of the report, and a regression test now guards it.

## Layout

```
engine.py                generic engine: fills, sizing, costs, trade log, MAE/MFE
strategies/
    fvg_cascade.py       the strategy (4H zone -> retrace -> 15m FVG -> limit entry)
fvg_detector.py          3-candle fair value gap detection
data_utils.py            CSV loading and candle construction
convert_dukascopy.py     vendor CSV -> engine format, with a data health report
report.py                statistics and charts
run_backtest.py          command line entry point
analysis/full_study.py   reproduces every number in the report
tests/                   31 tests: candle alignment, FVG detection, fills, sizing, lookahead
docs/                    REPORT.md and the raw results (JSON)
```

## Quick start

```bash
pip install -r requirements.txt
python -m pytest                                   # needs: pip install -r requirements-dev.txt
python run_backtest.py --strategy fvg_cascade      # smoke test on SYNTHETIC data
```

The synthetic-data run only proves the code executes. It says nothing about the strategy.

## Getting real data

The data is not included in this repository. You need 1-minute **bid** candles for US30 and NAS100, in **UTC**, stamped by **bar open time** (the default for Dukascopy and Tickstory).

1. **Export.** Free option: Tickstory (Windows) with the Dukascopy feed. Download the instrument, then Export to file with *Generic bar format (comma delimited)*, 1 Minute, UTC, Bid. Alternative: `dukascopy-node` (Dukascopy rate-limits hard, so use small batches and pauses, e.g. `-bs 1 -bp 5000 -r 5 -rp 5000 -ch`). Dukascopy symbols are `USA30.IDX/USD` and `USATECH.IDX/USD`.
2. **Convert.** `python convert_dukascopy.py raw_export.csv data/dow_1m.csv`. This writes the engine's `time,open,high,low,close` format and prints a health report: date range, OHLC integrity, gaps, and the share of rows inside the 07:00-10:00 GMT window. If that share is far from about 13% your timestamps are probably not UTC, and the backtest would silently test the wrong hours.
3. **Run.**

```bash
python run_backtest.py --strategy fvg_cascade --data data/dow_1m.csv \
    --spread-points 2.0 --slippage-points 0.5
```

Any strategy parameter can be overridden with JSON, for example `--params '{"rr_target": 3.0, "directions": ["short"]}'`.

## Reproducing the report

```bash
python analysis/full_study.py --dow data/dow_1m.csv --nas data/nas_1m.csv --out docs
```

Takes several minutes and regenerates `docs/results_*.json` and `docs/tables.md`, the source of every table in the report.

## Writing a strategy

Use `strategies/fvg_cascade.py` as the worked example. A strategy is a class with three hooks:

```python
from engine import Strategy, OrderRequest

class MyStrategy(Strategy):
    required_timeframes = {'exec': '15min'}   # resampled from the 1-minute data
    execution_tf = 'exec'                      # the timeframe the engine steps through

    def prepare(self, data):                   # once, before the run
        ...

    def on_bar(self, ctx):                     # once per closed candle
        if ctx.position is not None:
            return []
        return [OrderRequest(direction='long', order_type='market',
                             stop_price=..., target_price=...)]

    def manage_position(self, ctx, position):  # optional: trailing stops, forced exits
        ...
```

Order types are `market` (fills at the next candle's open), `limit` and `stop`. Call `ctx.count("label")` anywhere to add an event counter to the report. Put the file in `strategies/`, then register it in the `STRATEGIES` dict in `run_backtest.py` (the timeframes it needs and which one it steps through) and run it with `--strategy my_strategy`.

## How lookahead is avoided

- The engine steps through closed candles only. A strategy sees nothing from a candle until it has closed.
- Input rows are stamped by bar open time. Output candles are labelled by close time, so a candle's label is the moment its OHLC becomes known. The 15m candle labelled 08:15 holds the minutes stamped 08:00 to 08:14.
- An order placed when a candle closes can only fill on later candles. Market orders fill at the next candle's open.
- A 4H zone can only be used after the 4H candle that completes it has closed, and cannot be marked as revisited by that same candle.
- If stop and target are both touched inside one candle, the stop is assumed to have hit first.

Most of these are covered by tests, and I confirmed the suite fails when the candle-alignment, zone-engagement and tie-break bugs are deliberately reintroduced.

## Limitations

One data source, bid prices only, three years, no out-of-sample holdout, and baseline samples of 34 and 39 trades. One position at a time, one instrument. Costs are assumed, not measured, and limit orders are assumed to fill whenever price touches them. The full list is in section 9 of the report.

## Disclaimer

Research and education only. Nothing here is financial advice, and past results on historical data do not predict future results.

## License

MIT, see [LICENSE](LICENSE).
