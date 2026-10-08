#!/usr/bin/env python3
"""
Reproduces every number in docs/REPORT.md from the raw 1-minute CSVs.

    python analysis/full_study.py --dow data/dow_1m.csv --nas data/nas_1m.csv --out docs

Writes docs/results_<inst>.json (all raw numbers) and docs/tables.md (the
markdown tables used in the report). Expect several minutes of runtime.
Use --render-only to rebuild tables.md from existing JSON files.
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from data_utils import load_ohlc_csv, resample_ohlc  # noqa: E402
from engine import BacktestEngine, Config  # noqa: E402
from fvg_detector import detect_fvgs  # noqa: E402
from report import trades_to_df  # noqa: E402
from strategies.fvg_cascade import FVGCascadeStrategy  # noqa: E402

START_EQ = 10000.0
SPREAD, SLIP = 2.0, 0.5   # points, round trip; typical retail index CFD costs
NAMES = {'dow': 'US30', 'nas': 'NAS100'}


# ------------------------------------------------------------------ helpers

def _jsonable(o):
    if isinstance(o, (np.floating, np.integer)):
        return float(o)
    return str(o)


def run(data, params=None, spread=SPREAD, slip=SLIP, strategy_cls=FVGCascadeStrategy):
    eng = BacktestEngine(data, 'ltf', strategy_cls(**(params or {})),
                         Config(spread_points=spread, slippage_points=slip))
    eq, trades = eng.run()
    df = trades_to_df(trades)
    if len(df):
        df['year'] = df.entry_time.dt.year
    return eng, eq, df


def trade_metrics(d):
    n = len(d)
    if n == 0:
        return None
    w = d[d.outcome == 'win']
    l = d[d.outcome == 'loss']
    gw, gl = w.pnl.sum(), -l.pnl.sum()
    streak = best = 0
    for o in d.outcome.values:
        streak = streak + 1 if o == 'loss' else 0
        best = max(best, streak)
    avg_win = w.r_multiple.mean() if len(w) else np.nan
    avg_loss = l.r_multiple.mean() if len(l) else np.nan
    return dict(
        n=n, wins=len(w), losses=len(l), win_rate=100 * len(w) / n,
        avg_r=d.r_multiple.mean(), median_r=d.r_multiple.median(), std_r=d.r_multiple.std(),
        avg_win_r=avg_win, avg_loss_r=avg_loss,
        payoff=(avg_win / abs(avg_loss)) if len(w) and len(l) else np.nan,
        profit_factor=(gw / gl) if gl > 0 else np.inf,
        net_pnl=d.pnl.sum(), net_r=d.r_multiple.sum(), max_loss_streak=best,
        avg_hold_h=d.hold_minutes.mean() / 60,
        mae=d.mae_r.mean(), mfe=d.mfe_r.mean(),
        mae_wins=w.mae_r.mean() if len(w) else np.nan,
        mfe_losses=l.mfe_r.mean() if len(l) else np.nan,
        losers_1r=int((l.mfe_r >= 1).sum()),
        longs=int((d.direction == 'long').sum()), shorts=int((d.direction == 'short').sum()),
    )


def path_metrics(eq):
    e = eq['equity'].values
    peak = np.maximum.accumulate(e)
    dd = (e - peak) / peak
    longest = cur = start = 0
    best = (0, 0)
    for i, u in enumerate(e < peak):
        if u:
            if cur == 0:
                start = i
            cur += 1
            if cur > longest:
                longest, best = cur, (start, i)
        else:
            cur = 0
    days = (eq['time'].iloc[best[1]] - eq['time'].iloc[best[0]]).days if longest else 0
    return dict(max_dd=dd.min() * 100, underwater_days=days, final=e[-1],
                ret=(e[-1] / START_EQ - 1) * 100)


class Recorder(FVGCascadeStrategy):
    """Same strategy, but remembers every order it requested."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.log = []

    def on_bar(self, ctx):
        orders = super().on_bar(ctx)
        for o in orders:
            self.log.append((ctx.now, ctx.bar.close, o))
        return orders


def make_sim(base):
    """Replay a single order on the raw 1-minute bars (limit or market entry)."""
    t = base['time']
    hi, lo = base['high'].values, base['low'].values

    def sim(entry_t, entry_p, stop_p, target_p, direction, valid_until=None, limit=True):
        i0 = int(t.searchsorted(entry_t, side='left'))
        if limit:
            i1 = int(t.searchsorted(valid_until, side='right')) if valid_until is not None else len(t)
            cond = hi[i0:i1] >= entry_p if direction == 'short' else lo[i0:i1] <= entry_p
            if not cond.any():
                return None
            j = i0 + int(np.argmax(cond))
        else:
            j = i0
        h2, l2 = hi[j:], lo[j:]
        if direction == 'short':
            s_hit, t_hit = h2 >= stop_p, l2 <= target_p
        else:
            s_hit, t_hit = l2 <= stop_p, h2 >= target_p
        si = int(np.argmax(s_hit)) if s_hit.any() else None
        ti = int(np.argmax(t_hit)) if t_hit.any() else None
        if si is None and ti is None:
            return None
        risk = abs(entry_p - stop_p)
        cost_r = (SPREAD + SLIP) / risk
        rr = abs(target_p - entry_p) / risk
        if ti is None or (si is not None and si <= ti):
            return -1 - cost_r     # stop wins ties, same convention as the engine
        return rr - cost_r

    return sim


# ------------------------------------------------------------------ compute

def compute(inst, path):
    base = load_ohlc_csv(path)
    df15 = resample_ohlc(base, '15min').reset_index(drop=True)
    df4 = resample_ohlc(base, '4h').reset_index(drop=True)
    data = {'htf': df4, 'ltf': df15}
    R = dict(inst=inst, rows=len(base), start=str(base.time.iloc[0]), end=str(base.time.iloc[-1]),
             n15=len(df15), n4h=len(df4))

    variants = {
        'base': {},
        'all': dict(session_start_hour_gmt=0, session_end_hour_gmt=24),
        'ny': dict(session_start_hour_gmt=13, session_end_hour_gmt=16),
        'rr1': dict(rr_target=1.0), 'rr15': dict(rr_target=1.5), 'rr3': dict(rr_target=3.0),
    }
    R['variants'] = {}
    base_df = None
    for name, params in variants.items():
        print(f'  [{inst}] variant {name}', flush=True)
        eng, eq, df = run(data, params)
        entry = dict(full=trade_metrics(df), counters=dict(eng.counters))
        if name in ('base', 'all'):
            entry['path'] = path_metrics(eq)
            entry['by_year'] = {str(y): trade_metrics(g) for y, g in df.groupby('year')}
            entry['by_dir'] = {d: trade_metrics(df[df.direction == d]) for d in ('long', 'short')}
            entry['by_dir_year'] = {
                f'{d}|{y}': trade_metrics(df[(df.direction == d) & (df.year == y)])
                for d in ('long', 'short') for y in sorted(df.year.unique())}
            _, _, dz = run(data, params, spread=0.0, slip=0.0)
            entry['zero_cost_avg_r'] = float(dz.r_multiple.mean())
        if name == 'base':
            base_df = df
        R['variants'][name] = entry

    # event study: does a 15m FVG predict forward direction?
    print(f'  [{inst}] event study (15m FVGs)', flush=True)
    closes = df15['close'].values
    fv15 = detect_fvgs(df15)
    es15 = {}
    for hl, h in (('1h', 4), ('4h', 16)):
        drift = float(np.mean(closes[h:] - closes[:-h]))
        for d in ('bullish', 'bearish'):
            idx = np.array([f['formed_idx'] for f in fv15 if f['direction'] == d])
            idx = idx[idx + h < len(closes)]
            move = closes[idx + h] - closes[idx]
            se = move.std(ddof=1) / np.sqrt(len(move))
            es15[f'{hl}|{d}'] = dict(n=int(len(move)), mean_move=float(move.mean()), drift=drift,
                                     excess=float(move.mean() - drift),
                                     t=float((move.mean() - drift) / se))
    R['es15'] = es15
    R['n_fvg15'] = len(fv15)

    # event study: does the first revisit of a 4H FVG continue in the gap direction?
    print(f'  [{inst}] event study (4H revisits)', flush=True)
    H = 96
    t15 = df15['time']
    lows, highs = df15['low'].values, df15['high'].values
    fwd = pd.Series(closes).shift(-H) - closes
    drift24 = float(fwd.mean())
    drift_by_year = fwd.groupby(t15.dt.year.values).mean().to_dict()
    rows = []
    for z in detect_fvgs(df4):
        lo_i = int(t15.searchsorted(z['formed_time'], side='right'))
        hi_i = int(t15.searchsorted(z['formed_time'] + pd.Timedelta(hours=48), side='right'))
        if hi_i <= lo_i:
            continue
        c, l, h = closes[lo_i:hi_i], lows[lo_i:hi_i], highs[lo_i:hi_i]
        through = (c > z['top']) if z['direction'] == 'bearish' else (c < z['bottom'])
        touch = (l <= z['top']) & (h >= z['bottom'])
        if not touch.any():
            continue
        ti = int(np.argmax(touch))
        if through.any() and int(np.argmax(through)) <= ti:
            continue   # closed through the zone before touching it: invalidated
        gi = lo_i + ti
        if gi + H >= len(closes):
            continue
        rows.append(dict(direction=z['direction'], year=int(t15.iloc[gi].year),
                         move=float(closes[gi + H] - closes[gi])))
    ev = pd.DataFrame(rows)
    es4 = {}
    for d in ('bearish', 'bullish'):
        m = ev[ev.direction == d].move
        se = m.std(ddof=1) / np.sqrt(len(m))
        es4[d] = dict(n=int(len(m)), mean_move=float(m.mean()), drift=drift24,
                      excess=float(m.mean() - drift24), t=float((m.mean() - drift24) / se))
    R['es4h'] = es4
    R['es4h_bear_year'] = {
        str(y): dict(n=int(len(g)), mean_move=float(g.move.mean()), drift=float(drift_by_year.get(y, np.nan)))
        for y, g in ev[ev.direction == 'bearish'].groupby('year')}

    # limit-at-edge entry vs market-at-confirmation entry, same setups
    print(f'  [{inst}] limit vs market entry', flush=True)
    rec = Recorder()
    BacktestEngine(data, 'ltf', rec, Config(spread_points=SPREAD, slippage_points=SLIP)).run()
    sim = make_sim(base)
    lim, mkt = [], []
    for placed_t, bar_close, o in rec.log:
        r = sim(placed_t, o.price, o.stop_price, o.target_price, o.direction,
                valid_until=o.valid_until, limit=True)
        if r is not None:
            lim.append(r)
        risk = abs(bar_close - o.stop_price)
        if risk > 0:
            tgt = bar_close - 2 * risk if o.direction == 'short' else bar_close + 2 * risk
            r = sim(placed_t, bar_close, o.stop_price, tgt, o.direction, limit=False)
            if r is not None:
                mkt.append(r)
    R['lvm'] = dict(orders=len(rec.log), limit_n=len(lim), limit_avg_r=float(np.mean(lim)),
                    market_n=len(mkt), market_avg_r=float(np.mean(mkt)))

    # bootstrap on the baseline trade R multiples
    rng = np.random.default_rng(7)
    r = base_df['r_multiple'].values
    boots = np.array([rng.choice(r, len(r), replace=True).mean() for _ in range(10000)])
    R['boot'] = dict(observed=float(r.mean()), lo=float(np.percentile(boots, 2.5)),
                     hi=float(np.percentile(boots, 97.5)), p_pos=float((boots > 0).mean()))
    return R


# ------------------------------------------------------------------- render

def fnum(x, nd=2, sign=False):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return '-'
    if isinstance(x, float) and np.isinf(x):
        return 'inf'
    return f'{x:+.{nd}f}' if sign else f'{x:.{nd}f}'


def fmoney(x):
    return f'-${abs(x):,.0f}' if x < 0 else f'+${x:,.0f}'


def md(header, rows):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '|'.join(['---'] * len(header)) + '|']
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def render(res):
    insts = [i for i in ('dow', 'nas') if i in res]
    nm = [NAMES[i] for i in insts]
    T = {}

    def V(i, v):
        return res[i]['variants'][v]

    # full metrics, baseline
    def row(label, f):
        return [label] + [f(V(i, 'base')) for i in insts]
    T['metrics_full'] = md(['Metric'] + nm, [
        row('Trades', lambda b: b['full']['n']),
        row('Wins / losses', lambda b: f"{b['full']['wins']} / {b['full']['losses']}"),
        row('Win rate', lambda b: f"{b['full']['win_rate']:.1f}%"),
        row('Long / short trades', lambda b: f"{b['full']['longs']} / {b['full']['shorts']}"),
        row('Avg R (expectancy)', lambda b: fnum(b['full']['avg_r'], 3, True)),
        row('Median R', lambda b: fnum(b['full']['median_r'], 2, True)),
        row('Std dev of R', lambda b: fnum(b['full']['std_r'], 2)),
        row('Average winning trade', lambda b: fnum(b['full']['avg_win_r'], 2, True) + 'R'),
        row('Average losing trade', lambda b: fnum(b['full']['avg_loss_r'], 2, True) + 'R'),
        row('Payoff ratio (avg win / avg loss)', lambda b: fnum(b['full']['payoff'], 2)),
        row('Profit factor', lambda b: fnum(b['full']['profit_factor'], 2)),
        row('Net P&L', lambda b: fmoney(b['full']['net_pnl'])),
        row('Net P&L in R', lambda b: fnum(b['full']['net_r'], 1, True) + 'R'),
        row('Net return on $10,000', lambda b: fnum(b['path']['ret'], 2, True) + '%'),
        row('Max drawdown (closed-trade equity)', lambda b: fnum(b['path']['max_dd'], 2) + '%'),
        row('Longest time under water', lambda b: f"{int(b['path']['underwater_days'])} days"),
        row('Max consecutive losses', lambda b: b['full']['max_loss_streak']),
        row('Average hold time', lambda b: fnum(b['full']['avg_hold_h'], 1) + ' hours'),
        row('Avg MAE, all trades', lambda b: fnum(b['full']['mae'], 2) + 'R'),
        row('Avg MFE, all trades', lambda b: fnum(b['full']['mfe'], 2) + 'R'),
        row('Avg MAE on winners', lambda b: fnum(b['full']['mae_wins'], 2) + 'R'),
        row('Avg MFE on losers', lambda b: fnum(b['full']['mfe_losses'], 2) + 'R'),
        row('Losers that reached +1R first', lambda b: f"{b['full']['losers_1r']} of {b['full']['losses']}"),
    ])

    # funnel
    def crow(label, key):
        return [label] + [V(i, 'base')['counters'].get(key, 0) for i in insts]
    T['funnel'] = md(['Stage'] + nm, [
        crow('4H bearish zones formed', 'zones_formed_bearish'),
        crow('4H bullish zones formed', 'zones_formed_bullish'),
        crow('Zones invalidated: 48h timeout', 'zones_invalidated_timeout'),
        crow('Zones invalidated: price closed through', 'zones_invalidated_close_through'),
        crow('Zones retraced into (engaged)', 'zones_engaged'),
        crow('Limit orders placed (15m FVG found in session)', 'orders_placed'),
        crow('Orders filled', 'orders_filled'),
        crow('Orders expired unfilled', 'orders_expired'),
    ])

    # cost decomposition
    T['costs'] = md(['Configuration'] + nm, [
        ['07-10 GMT, real costs (2.0 + 0.5 pts)'] + [fnum(V(i, 'base')['full']['avg_r'], 3, True) for i in insts],
        ['07-10 GMT, zero costs'] + [fnum(V(i, 'base')['zero_cost_avg_r'], 3, True) for i in insts],
        ['All hours, real costs'] + [fnum(V(i, 'all')['full']['avg_r'], 3, True) for i in insts],
        ['All hours, zero costs'] + [fnum(V(i, 'all')['zero_cost_avg_r'], 3, True) for i in insts],
    ])

    # sensitivity
    labels = [('Baseline 07-10 GMT, RR 1:2', 'base'), ('All hours, RR 1:2', 'all'),
              ('13-16 GMT, RR 1:2', 'ny'), ('07-10 GMT, RR 1:1', 'rr1'),
              ('07-10 GMT, RR 1:1.5', 'rr15'), ('07-10 GMT, RR 1:3', 'rr3')]
    def sens(i, v):
        f = V(i, v)['full']
        return f"{f['n']} / {fnum(f['avg_r'], 3, True)} / {fnum(f['profit_factor'], 2)}" if f else '0 trades'
    T['sensitivity'] = md(['Variant (trades / avg R / PF)'] + nm,
                          [[lab] + [sens(i, v) for i in insts] for lab, v in labels])

    # by year
    def by_year(i, v):
        rows = []
        for y, m in V(i, v)['by_year'].items():
            if m:
                rows.append([y, m['n'], f"{m['win_rate']:.1f}%", fnum(m['avg_r'], 2, True),
                             fnum(m['profit_factor'], 2), fmoney(m['net_pnl']), f"{m['longs']} / {m['shorts']}"])
        f = V(i, v)['full']
        rows.append(['**Total**', f"**{f['n']}**", f"**{f['win_rate']:.1f}%**", f"**{fnum(f['avg_r'], 2, True)}**",
                     f"**{fnum(f['profit_factor'], 2)}**", f"**{fmoney(f['net_pnl'])}**",
                     f"**{f['longs']} / {f['shorts']}**"])
        return md(['Year', 'Trades', 'Win rate', 'Avg R', 'Profit factor', 'Net P&L', 'Long / short'], rows)
    for i in insts:
        T[f'year_base_{i}'] = by_year(i, 'base')
        T[f'year_all_{i}'] = by_year(i, 'all')

    # long vs short
    cols = [(i, d) for i in insts for d in ('long', 'short')]
    hdr = ['Metric'] + [f"{NAMES[i]} {d}s" for i, d in cols]
    def drow(label, f, v='base'):
        return [label] + [f(V(i, v)['by_dir'][d]) if V(i, v)['by_dir'][d] else '-' for i, d in cols]
    T['dir_base'] = md(hdr, [
        drow('Trades', lambda m: m['n']),
        drow('Wins / losses', lambda m: f"{m['wins']} / {m['losses']}"),
        drow('Win rate', lambda m: f"{m['win_rate']:.1f}%"),
        drow('Avg R', lambda m: fnum(m['avg_r'], 2, True)),
        drow('Median R', lambda m: fnum(m['median_r'], 2, True)),
        drow('Avg winning trade', lambda m: fnum(m['avg_win_r'], 2, True) + 'R'),
        drow('Avg losing trade', lambda m: fnum(m['avg_loss_r'], 2, True) + 'R'),
        drow('Profit factor', lambda m: fnum(m['profit_factor'], 2)),
        drow('Net P&L', lambda m: fmoney(m['net_pnl'])),
        drow('Net P&L in R', lambda m: fnum(m['net_r'], 1, True) + 'R'),
        drow('Max consecutive losses', lambda m: m['max_loss_streak']),
        drow('Avg MAE', lambda m: fnum(m['mae'], 2) + 'R'),
        drow('Avg MFE', lambda m: fnum(m['mfe'], 2) + 'R'),
        drow('Avg MFE on losers', lambda m: fnum(m['mfe_losses'], 2) + 'R'),
    ])
    T['dir_all'] = md(hdr, [
        drow('Trades', lambda m: m['n'], 'all'),
        drow('Win rate', lambda m: f"{m['win_rate']:.1f}%", 'all'),
        drow('Avg R', lambda m: fnum(m['avg_r'], 2, True), 'all'),
        drow('Profit factor', lambda m: fnum(m['profit_factor'], 2), 'all'),
        drow('Net P&L', lambda m: fmoney(m['net_pnl']), 'all'),
        drow('Max consecutive losses', lambda m: m['max_loss_streak'], 'all'),
    ])

    def dir_year(v):
        years = sorted({k.split('|')[1] for i in insts for k in V(i, v)['by_dir_year']})
        rows = []
        for y in years:
            r = [y]
            for i, d in cols:
                m = V(i, v)['by_dir_year'].get(f'{d}|{y}')
                r.append(f"{m['n']} / {m['win_rate']:.0f}% / {fnum(m['avg_r'], 2, True)} / {fmoney(m['net_pnl'])}" if m else '0')
            rows.append(r)
        return md(['Year (n / WR / avg R / P&L)'] + hdr[1:], rows)
    T['dir_year_base'] = dir_year('base')
    T['dir_year_all'] = dir_year('all')

    # event studies
    rows = []
    for i in insts:
        for hl in ('1h', '4h'):
            for d in ('bullish', 'bearish'):
                e = res[i]['es15'][f'{hl}|{d}']
                rows.append([NAMES[i], hl, d, f"{e['n']:,}", fnum(e['mean_move'], 1, True),
                             fnum(e['drift'], 1, True), fnum(e['excess'], 1, True), fnum(e['t'], 2, True)])
    T['es15'] = md(['Instrument', 'Horizon', 'Gap type', 'n', 'Mean fwd move (pts)',
                    'Unconditional drift (pts)', 'Excess (pts)', 't-stat'], rows)

    rows = []
    for i in insts:
        for d in ('bearish', 'bullish'):
            e = res[i]['es4h'][d]
            rows.append([NAMES[i], d, e['n'], fnum(e['mean_move'], 1, True), fnum(e['drift'], 1, True),
                         fnum(e['excess'], 1, True), fnum(e['t'], 2, True)])
    T['es4h'] = md(['Instrument', 'Zone type', 'n revisits', 'Mean 24h move (pts)',
                    'Unconditional drift (pts)', 'Excess (pts)', 't-stat'], rows)

    years = sorted({y for i in insts for y in res[i]['es4h_bear_year']})
    rows = []
    for y in years:
        r = [y]
        for i in insts:
            e = res[i]['es4h_bear_year'].get(y)
            r += [e['n'], fnum(e['mean_move'], 1, True), fnum(e['drift'], 1, True)] if e else ['0', '-', '-']
        rows.append(r)
    hdr4 = ['Year'] + [f'{NAMES[i]} {c}' for i in insts for c in ('n', 'mean 24h move', 'drift')]
    T['es4h_year'] = md(hdr4, rows)

    # limit vs market, bootstrap
    T['lvm'] = md(['Instrument', 'Orders requested', 'Limit at edge: filled', 'Limit avg R',
                   'Market at confirmation: filled', 'Market avg R'],
                  [[NAMES[i], res[i]['lvm']['orders'], res[i]['lvm']['limit_n'],
                    fnum(res[i]['lvm']['limit_avg_r'], 3, True), res[i]['lvm']['market_n'],
                    fnum(res[i]['lvm']['market_avg_r'], 3, True)] for i in insts])
    T['boot'] = md(['Instrument', 'Observed avg R', '95% CI (bootstrap)', 'P(avg R > 0)'],
                   [[NAMES[i], fnum(res[i]['boot']['observed'], 3, True),
                     f"[{fnum(res[i]['boot']['lo'], 2, True)}, {fnum(res[i]['boot']['hi'], 2, True)}]",
                     f"{res[i]['boot']['p_pos']:.0%}"] for i in insts])

    return '\n\n'.join(f'<!-- {k} -->\n{v}' for k, v in T.items())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dow')
    ap.add_argument('--nas')
    ap.add_argument('--out', default='docs')
    ap.add_argument('--render-only', action='store_true')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    if not args.render_only:
        for inst, path in (('dow', args.dow), ('nas', args.nas)):
            if path:
                print(f'computing {inst} ...', flush=True)
                R = compute(inst, path)
                with open(f'{args.out}/results_{inst}.json', 'w') as f:
                    json.dump(R, f, indent=1, default=_jsonable)
    res = {}
    for inst in ('dow', 'nas'):
        p = f'{args.out}/results_{inst}.json'
        if os.path.exists(p):
            with open(p) as f:
                res[inst] = json.load(f)
    with open(f'{args.out}/tables.md', 'w') as f:
        f.write(render(res))
    print('tables written to', f'{args.out}/tables.md')


if __name__ == '__main__':
    main()
