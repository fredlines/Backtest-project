"""Execution engine: fills, exits, sizing, costs. Every scenario is a handful of
hand-built bars where the correct answer can be worked out on paper."""

import pandas as pd
import pytest

from engine import BacktestEngine, Config, OrderRequest, Strategy


class Scripted(Strategy):
    """Requests the given orders on given bar indexes: {bar_index: OrderRequest}."""

    def __init__(self, plan):
        super().__init__()
        self.plan = plan
        self.i = -1

    def on_bar(self, ctx):
        self.i += 1
        o = self.plan.get(self.i)
        return [o] if o else []


def bars(rows):
    t = pd.date_range('2024-01-02 08:00', periods=len(rows), freq='15min', tz='UTC')
    o, h, l, c = zip(*rows)
    return pd.DataFrame({'time': t, 'open': o, 'high': h, 'low': l, 'close': c})


def run(rows, plan, **cfg):
    cfg.setdefault('spread_points', 0.0)
    cfg.setdefault('slippage_points', 0.0)
    eng = BacktestEngine({'x': bars(rows)}, 'x', Scripted(plan), Config(**cfg))
    eq, trades = eng.run()
    return eng, eq, trades


SHORT = dict(direction='short', order_type='limit', price=100.0, stop_price=102.0, target_price=96.0)

WIN_BARS = [(99, 99.5, 98.5, 99),       # bar 0: order placed
            (99, 100.5, 98, 100),        # bar 1: price reaches 100 -> filled
            (100, 100.5, 95, 96)]        # bar 2: target 96 hit, stop 102 not


def test_limit_short_wins_at_two_r_with_no_costs():
    _, _, trades = run(WIN_BARS, {0: OrderRequest(**SHORT)})
    (t,) = trades
    assert (t.entry_price, t.exit_price, t.outcome) == (100.0, 96.0, 'win')
    assert t.r_multiple == pytest.approx(2.0)
    assert t.pnl == pytest.approx(200.0)       # 50 lots x 4 points


def test_costs_come_off_every_trade():
    # 2 points of spread+slippage on a 2 point stop = 1R of cost
    _, _, trades = run(WIN_BARS, {0: OrderRequest(**SHORT)}, spread_points=1.5, slippage_points=0.5)
    assert trades[0].r_multiple == pytest.approx(1.0)


def test_stop_loses_exactly_one_r():
    rows = WIN_BARS[:2] + [(100, 102.5, 97, 102)]
    _, _, trades = run(rows, {0: OrderRequest(**SHORT)})
    assert trades[0].outcome == 'loss'
    assert trades[0].r_multiple == pytest.approx(-1.0)


def test_stop_wins_when_stop_and_target_hit_in_the_same_bar():
    rows = WIN_BARS[:2] + [(100, 102.5, 95, 99)]    # touches both 102 and 96
    _, _, trades = run(rows, {0: OrderRequest(**SHORT)})
    assert trades[0].outcome == 'loss'


@pytest.mark.parametrize('stop_distance', [2.0, 5.0, 11.0])
def test_dollar_risk_is_constant_whatever_the_stop_distance(stop_distance):
    order = OrderRequest(direction='short', order_type='limit', price=100.0,
                         stop_price=100.0 + stop_distance, target_price=60.0)
    rows = [(99, 99.5, 98.5, 99), (99, 100.5, 98, 100), (100, 100.0 + stop_distance + 1, 99, 100)]
    _, _, trades = run(rows, {0: order})
    assert trades[0].pnl == pytest.approx(-100.0)    # 1% of $10,000


def test_order_cannot_fill_on_the_bar_it_was_placed():
    # bar 0 already trades through the limit price, but the order only exists after bar 0 closes
    rows = [(99, 100.5, 98.5, 99), (99, 99.5, 98, 99), (99, 99.5, 98, 99)]
    _, _, trades = run(rows, {0: OrderRequest(**SHORT)})
    assert trades == []


def test_market_order_fills_at_next_bar_open_not_signal_close():
    order = OrderRequest(direction='long', order_type='market', stop_price=98.0, target_price=104.0)
    rows = [(100, 101, 99, 100),            # signal bar closes at 100
            (102, 103, 101, 102.5),          # next bar opens at 102
            (102, 105, 101, 104)]
    _, _, trades = run(rows, {0: order})
    assert trades[0].entry_price == 102.0


def test_stop_order_fills_at_its_price():
    order = OrderRequest(direction='long', order_type='stop', price=101.0, stop_price=99.0, target_price=105.0)
    rows = [(100, 100.5, 99.5, 100), (100, 101.5, 99.8, 101), (101, 106, 100.5, 105)]
    _, _, trades = run(rows, {0: order})
    assert trades[0].entry_price == 101.0 and trades[0].outcome == 'win'


def test_expired_order_never_fills():
    order = OrderRequest(**SHORT, valid_until=pd.Timestamp('2024-01-02 08:15', tz='UTC'))
    rows = [(99, 99.5, 98.5, 99), (99, 99.5, 98, 99),       # bars 0, 1: limit not reached
            (99, 100.5, 98, 100), (100, 100.5, 95, 96)]     # bar 2 reaches it, but order expired at 08:15
    eng, _, trades = run(rows, {0: order})
    assert trades == [] and eng.counters['orders_expired'] == 1


def test_only_one_position_at_a_time():
    mkt = OrderRequest(direction='long', order_type='market', stop_price=90.0, target_price=130.0)
    rows = [(100, 101, 99, 100)] * 5
    eng, _, trades = run(rows, {0: mkt, 1: mkt})    # second request arrives while the first is open
    assert eng.counters['orders_rejected_position_open'] == 1


def test_equity_curve_has_one_point_per_bar():
    _, eq, _ = run(WIN_BARS, {0: OrderRequest(**SHORT)})
    assert len(eq) == len(WIN_BARS)
    assert eq['equity'].iloc[-1] == pytest.approx(10200.0)
