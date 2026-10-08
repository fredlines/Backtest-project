"""
Reporting layer. Everything here operates on the generic Trade log and
engine counters, no strategy-specific knowledge lives here.
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


def trades_to_df(trades) -> pd.DataFrame:
    df = pd.DataFrame([{
        'direction': t.direction, 'entry_time': t.entry_time, 'entry_price': t.entry_price,
        'stop_price': t.stop_price, 'target_price': t.target_price, 'tag': t.tag,
        'exit_time': t.exit_time, 'exit_price': t.exit_price,
        'outcome': t.outcome, 'pnl': t.pnl, 'r_multiple': t.r_multiple,
        'hold_minutes': t.hold_minutes, 'mae_r': t.mae_r, 'mfe_r': t.mfe_r,
    } for t in trades])
    if len(df):
        df['entry_hour'] = df['entry_time'].dt.hour
        df['weekday'] = df['entry_time'].dt.day_name()
        df['month'] = df['entry_time'].dt.strftime('%Y-%m')
    return df


def compute_stats(df: pd.DataFrame, equity_curve_df: pd.DataFrame, starting_equity: float) -> dict:
    if len(df) == 0:
        return {'total_trades': 0}

    wins = df[df.outcome == 'win']
    losses = df[df.outcome == 'loss']
    gross_win = wins.pnl.sum()
    gross_loss = -losses.pnl.sum()

    eq = equity_curve_df['equity']
    running_max = eq.cummax()
    drawdown = (eq - running_max) / running_max
    max_dd_pct = drawdown.min() * 100
    final_equity = eq.iloc[-1] if len(eq) else starting_equity

    r = df['r_multiple'].dropna()
    mae = df['mae_r'].dropna()
    mfe = df['mfe_r'].dropna()

    return {
        'total_trades': len(df),
        'wins': len(wins), 'losses': len(losses),
        'win_rate_pct': 100 * len(wins) / len(df),
        'avg_r': r.mean() if len(r) else float('nan'),
        'profit_factor': (gross_win / gross_loss) if gross_loss > 0 else float('inf'),
        'total_return_pct': 100 * (final_equity - starting_equity) / starting_equity,
        'max_drawdown_pct': max_dd_pct,
        'final_equity': final_equity,
        'r_min': r.min() if len(r) else float('nan'), 'r_max': r.max() if len(r) else float('nan'),
        'r_median': r.median() if len(r) else float('nan'), 'r_std': r.std() if len(r) else float('nan'),
        'r_p25': r.quantile(0.25) if len(r) else float('nan'), 'r_p75': r.quantile(0.75) if len(r) else float('nan'),
        'hold_min_avg': df.hold_minutes.mean(), 'hold_min_median': df.hold_minutes.median(),
        'mae_r_avg': mae.mean() if len(mae) else float('nan'),
        'mfe_r_avg': mfe.mean() if len(mfe) else float('nan'),
        'mfe_r_avg_losses': losses.mfe_r.mean() if len(losses) else float('nan'),
        'mae_r_avg_wins': wins.mae_r.mean() if len(wins) else float('nan'),
    }


def segment_report(df: pd.DataFrame, group_col: str, label: str):
    if len(df) == 0:
        return
    g = df.groupby(group_col).agg(
        trades=('outcome', 'count'),
        win_rate=('outcome', lambda x: 100 * (x == 'win').mean()),
        avg_r=('r_multiple', 'mean'),
        total_pnl=('pnl', 'sum'),
    ).round(2)
    print(f"\n-- By {label} --")
    print(g.to_string())


def print_report(stats: dict, cfg, counters: dict, df: pd.DataFrame, strategy_name: str):
    print('=' * 55)
    print(f'BACKTEST REPORT  [{strategy_name}]')
    print('=' * 55)
    print(f"Risk per trade    : {cfg.risk_pct_per_trade * 100:.2f}% of equity")
    print(f"Spread+slippage   : {cfg.spread_points + cfg.slippage_points} pts round trip")

    print('\n-- Strategy events --')
    if counters:
        for k in sorted(counters):
            print(f"{k:38s}: {counters[k]}")
    else:
        print("(strategy didn't log any custom counters via ctx.count)")

    if stats['total_trades'] == 0:
        print("\nNo trades were generated: see events above for where the funnel stopped.")
        return

    print('\n-- Overview --')
    print(f"Total trades      : {stats['total_trades']}")
    print(f"Win rate          : {stats['win_rate_pct']:.1f}%  ({stats['wins']}W / {stats['losses']}L)")
    print(f"Avg R per trade   : {stats['avg_r']:.3f}")
    print(f"Profit factor     : {stats['profit_factor']:.2f}")
    print(f"Total return      : {stats['total_return_pct']:.2f}%")
    print(f"Max drawdown      : {stats['max_drawdown_pct']:.2f}%")
    print(f"Final equity      : ${stats['final_equity']:,.2f}")

    print('\n-- R-multiple distribution --')
    print(f"min {stats['r_min']:.2f} | p25 {stats['r_p25']:.2f} | median {stats['r_median']:.2f} "
          f"| p75 {stats['r_p75']:.2f} | max {stats['r_max']:.2f} | std {stats['r_std']:.2f}")

    print('\n-- Hold time --')
    print(f"avg {stats['hold_min_avg']:.0f} min | median {stats['hold_min_median']:.0f} min")

    if not np.isnan(stats['mae_r_avg']):
        print('\n-- MAE / MFE (in R) --')
        print(f"Avg heat taken on ALL trades (MAE)      : {stats['mae_r_avg']:.2f}R")
        print(f"Avg best-available on ALL trades (MFE)  : {stats['mfe_r_avg']:.2f}R")
        print(f"Avg MAE on winners (heat before it worked): {stats['mae_r_avg_wins']:.2f}R")
        print(f"Avg MFE on losers (profit before it reversed): {stats['mfe_r_avg_losses']:.2f}R")
        if not np.isnan(stats['mfe_r_avg_losses']) and stats['mfe_r_avg_losses'] > 1.0:
            print("  -> losers were showing over 1R of open profit before reversing to a loss.")
            print("     worth testing a breakeven-stop or partial-profit rule.")

    segment_report(df, 'direction', 'direction')
    segment_report(df, 'entry_hour', 'entry hour (GMT)')
    segment_report(df, 'weekday', 'weekday')
    segment_report(df, 'month', 'month')
    print('=' * 55)


def save_charts(df: pd.DataFrame, equity_df: pd.DataFrame, outdir: str, using_sample: bool, strategy_name: str):
    suffix = ' (SYNTHETIC DATA - SMOKE TEST ONLY)' if using_sample else ''

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(equity_df['time'], equity_df['equity'])
    ax.set_title(f'Equity Curve [{strategy_name}]{suffix}')
    ax.set_xlabel('Time'); ax.set_ylabel('Equity ($)'); ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(f'{outdir}/equity_curve.png', dpi=130)
    plt.close(fig)

    if len(df) == 0:
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(df['r_multiple'].dropna(), bins=min(20, max(5, len(df) // 2)), edgecolor='black', alpha=0.75)
    ax.axvline(0, color='black', linewidth=1)
    ax.set_title(f'R-Multiple Distribution [{strategy_name}]{suffix}')
    ax.set_xlabel('R multiple'); ax.set_ylabel('Trade count'); ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(f'{outdir}/r_multiple_histogram.png', dpi=130)
    plt.close(fig)

    if df['mae_r'].notna().any():
        fig, ax = plt.subplots(figsize=(7, 7))
        colors = df['outcome'].map({'win': 'tab:green', 'loss': 'tab:red'})
        ax.scatter(df['mae_r'], df['mfe_r'], c=colors, alpha=0.7, edgecolor='black')
        lim = max(df['mae_r'].max(), df['mfe_r'].max(), 1) * 1.1
        ax.plot([0, lim], [0, lim], linestyle='--', color='gray', linewidth=1)
        ax.set_title(f'MAE vs MFE per trade [{strategy_name}]{suffix}')
        ax.set_xlabel('MAE (R, heat taken)'); ax.set_ylabel('MFE (R, best available)')
        ax.set_xlim(0, lim); ax.set_ylim(0, lim); ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(f'{outdir}/mae_mfe_scatter.png', dpi=130)
        plt.close(fig)
