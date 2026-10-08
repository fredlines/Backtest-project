"""
Simple SMA crossover: example strategy showing the plugin interface for
a completely different shape from the FVG cascade (single timeframe,
market orders, no multi-stage setup tracking). Use this as your template
when writing a new strategy.

Enters long on a fast-SMA-crosses-above-slow-SMA, short on the reverse
cross. Fixed stop in points, fixed RR target, market order entry.
"""

from engine import Strategy, OrderRequest


class SmaCrossStrategy(Strategy):
    required_timeframes = {'exec': '4h'}
    execution_tf = 'exec'

    def __init__(self, fast=20, slow=50, stop_points=50.0, rr_target=2.0, exec_key='exec'):
        super().__init__()
        self.fast = fast
        self.slow = slow
        self.stop_points = stop_points
        self.rr_target = rr_target
        self.exec_key = exec_key
        self.signal_by_time = {}

    def prepare(self, data: dict) -> None:
        df = data[self.exec_key].copy()
        df['sma_fast'] = df['close'].rolling(self.fast).mean()
        df['sma_slow'] = df['close'].rolling(self.slow).mean()
        cross_up = (df['sma_fast'] > df['sma_slow']) & (df['sma_fast'].shift(1) <= df['sma_slow'].shift(1))
        cross_down = (df['sma_fast'] < df['sma_slow']) & (df['sma_fast'].shift(1) >= df['sma_slow'].shift(1))
        for row, up, down in zip(df.itertuples(), cross_up, cross_down):
            if up:
                self.signal_by_time[row.time] = 'long'
            elif down:
                self.signal_by_time[row.time] = 'short'

    def on_bar(self, ctx) -> list:
        if ctx.position is not None:
            return []
        signal = self.signal_by_time.get(ctx.now)
        if signal is None:
            return []
        ctx.count(f'signal_{signal}')

        entry = ctx.bar.close
        if signal == 'long':
            stop = entry - self.stop_points
            target = entry + self.rr_target * self.stop_points
        else:
            stop = entry + self.stop_points
            target = entry - self.rr_target * self.stop_points

        return [OrderRequest(direction=signal, order_type='market',
                              stop_price=stop, target_price=target)]
