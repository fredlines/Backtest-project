"""Whole-strategy property checks on deterministic synthetic data."""

import datetime as dt

import pytest

from data_utils import resample_ohlc
from engine import BacktestEngine, Config
from sample_data import generate_sample_1m
from strategies.fvg_cascade import FVGCascadeStrategy


@pytest.fixture(scope='module')
def result():
    base = generate_sample_1m(days=60, seed=42)
    data = {'htf': resample_ohlc(base, '4h'), 'ltf': resample_ohlc(base, '15min')}
    eng = BacktestEngine(data, 'ltf', FVGCascadeStrategy(), Config())
    _, trades = eng.run()
    return eng, trades


def test_it_actually_trades(result):
    assert len(result[1]) > 0


def test_stop_and_target_sit_on_the_correct_sides_of_entry(result):
    for t in result[1]:
        if t.direction == 'short':
            assert t.target_price < t.entry_price < t.stop_price
        else:
            assert t.stop_price < t.entry_price < t.target_price


def test_target_is_two_r(result):
    for t in result[1]:
        risk = abs(t.entry_price - t.stop_price)
        assert abs(t.target_price - t.entry_price) == pytest.approx(2 * risk)


def test_entries_only_happen_inside_the_session(result):
    for t in result[1]:
        assert dt.time(7, 0) < t.entry_time.time() <= dt.time(10, 0)


def test_trades_do_not_overlap(result):
    trades = sorted(result[1], key=lambda t: t.entry_time)
    for a, b in zip(trades, trades[1:]):
        assert a.exit_time <= b.entry_time


def test_funnel_counts_are_consistent(result):
    c = result[0].counters
    assert c['orders_filled'] == len(result[1])
    assert c['orders_filled'] + c.get('orders_expired', 0) + c.get('orders_cancelled_on_entry', 0) <= c['orders_placed']


def test_zone_is_not_engaged_by_the_candle_that_completes_it():
    """A bearish 4H zone sits above the completing candle's high, so price cannot have
    returned to it on that same candle. Regression test for an inclusive-comparison bug."""
    import pandas as pd

    def frame(times, rows):
        o, h, l, c = zip(*rows)
        return pd.DataFrame({'time': pd.to_datetime(times, utc=True),
                             'open': o, 'high': h, 'low': l, 'close': c})

    # 4H candles A, B, C: A.low 110 > C.high 105 -> bearish zone 105..110, complete at 12:00
    htf = frame(['2024-01-02 04:00', '2024-01-02 08:00', '2024-01-02 12:00'],
                [(114, 115, 110, 111), (111, 112, 100, 101), (101, 105, 95, 96)])

    def engaged(extra):
        ltf = frame(['2024-01-02 11:45', '2024-01-02 12:00', '2024-01-02 12:15'] + extra[0],
                    [(100, 103, 98, 99),
                     (99, 105, 96, 96),       # final 15m of candle C: prints C's exact high (105)
                     (96, 100, 95, 97)] + extra[1])   # never reaches the zone
        eng = BacktestEngine({'htf': htf, 'ltf': ltf}, 'ltf', FVGCascadeStrategy(), Config())
        eng.run()
        return eng.counters.get('zones_engaged', 0)

    assert engaged(([], [])) == 0
    # positive control: a later bar that really trades up into the zone does engage it
    assert engaged((['2024-01-02 12:30'], [(97, 106, 96, 100)])) == 1
