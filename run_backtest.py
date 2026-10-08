"""
CLI entry point.

Usage:
    # smoke test on synthetic data
    python3 run_backtest.py --strategy fvg_cascade

    # real data, default params
    python3 run_backtest.py --strategy fvg_cascade --data path/to/1min.csv

    # real data, custom params (anything the strategy's __init__ accepts)
    python3 run_backtest.py --strategy fvg_cascade --data path/to/1min.csv \\
        --params '{"rr_target": 3.0, "directions": ["short"]}'

    python3 run_backtest.py --strategy sma_cross --data path/to/1min.csv \\
        --params '{"fast": 10, "slow": 30, "stop_points": 40}'

To add your own strategy:
    1. Write strategies/my_strategy.py, subclassing engine.Strategy
       (see strategies/sma_cross.py for the minimal template).
    2. Register it in the STRATEGIES dict below: which timeframes it needs
       (resampled from the base 1-min data) and which one it executes on.
    3. Run with --strategy my_strategy.
"""

import argparse
import json
import sys
import os

from data_utils import load_ohlc_csv, resample_ohlc
from engine import BacktestEngine, Config
from sample_data import generate_sample_1m
from report import trades_to_df, compute_stats, print_report, save_charts

from strategies.fvg_cascade import FVGCascadeStrategy
from strategies.sma_cross import SmaCrossStrategy

# ----------------------------------------------------------------------
# Strategy registry. Add a new strategy here to make it available via
# --strategy. `timeframes` maps the strategy's internal keys to the
# pandas resample rule used to build them from the base 1-min data.
STRATEGIES = {
    'fvg_cascade': {
        'cls': FVGCascadeStrategy,
        'timeframes': {'htf': '4h', 'ltf': '15min'},
        'execution_tf': 'ltf',
    },
    'sma_cross': {
        'cls': SmaCrossStrategy,
        'timeframes': {'exec': '4h'},
        'execution_tf': 'exec',
    },
}
# ----------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--strategy', type=str, required=True, choices=list(STRATEGIES.keys()))
    parser.add_argument('--data', type=str, default=None,
                         help='Path to a 1-minute (or finer) OHLC CSV: time,open,high,low,close. '
                              'If omitted, SYNTHETIC sample data is used for a code smoke-test only.')
    parser.add_argument('--params', type=str, default='{}',
                         help='JSON dict of kwargs passed straight to the strategy constructor.')
    parser.add_argument('--starting-equity', type=float, default=10000.0)
    parser.add_argument('--risk-pct', type=float, default=0.01)
    parser.add_argument('--point-value', type=float, default=1.0)
    parser.add_argument('--spread-points', type=float, default=1.5)
    parser.add_argument('--slippage-points', type=float, default=0.5)
    parser.add_argument('--outdir', type=str, default='output')
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    spec = STRATEGIES[args.strategy]

    if args.data:
        base = load_ohlc_csv(args.data)
        using_sample = False
    else:
        print("!! No --data provided. Using SYNTHETIC sample data.")
        print("!! Results below are a CODE SMOKE TEST ONLY, not a real backtest. !!\n")
        base = generate_sample_1m()
        using_sample = True

    data = {key: resample_ohlc(base, rule) for key, rule in spec['timeframes'].items()}

    strategy_kwargs = json.loads(args.params)
    strategy = spec['cls'](**strategy_kwargs)

    cfg = Config(
        starting_equity=args.starting_equity, risk_pct_per_trade=args.risk_pct,
        point_value_per_lot=args.point_value, spread_points=args.spread_points,
        slippage_points=args.slippage_points,
    )

    engine = BacktestEngine(data=data, execution_tf=spec['execution_tf'], strategy=strategy, config=cfg)
    equity_df, trades = engine.run()
    df = trades_to_df(trades)

    stats = compute_stats(df, equity_df, cfg.starting_equity)
    print_report(stats, cfg, engine.counters, df, args.strategy)

    if len(df):
        df.to_csv(f'{args.outdir}/trade_log.csv', index=False)
        print(f"\nTrade log saved to {args.outdir}/trade_log.csv")

    save_charts(df, equity_df, args.outdir, using_sample, args.strategy)
    print(f"Charts saved to {args.outdir}/")


if __name__ == '__main__':
    sys.exit(main())
