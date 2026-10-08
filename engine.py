"""
Generic backtest engine.

This file has no knowledge of any specific strategy (no FVGs, no session
windows, nothing strategy-specific lives here). It handles the parts that
are the same regardless of what strategy you're testing:
  - stepping through bars of one "execution timeframe"
  - filling pending orders (limit / stop / market)
  - tracking one open position, checking its stop/target
  - position sizing and cost modeling
  - recording closed trades (with MAE/MFE, hold time, R-multiple)
  - a generic counters dict any strategy can log events into, for reporting

To test a new strategy: write a class in strategies/ that implements the
Strategy interface below, register it in run_backtest.py's STRATEGIES
dict, and run. You should not need to touch this file to add a strategy.

Current limitation: one open position at a time, single instrument. If you
ever want to test a strategy that holds multiple concurrent positions or
trades a portfolio, this engine would need extending, ask for that when
you get there rather than assuming it already works.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional
import pandas as pd


# ---------------------------------------------------------------- config --

def default_position_sizer(equity: float, entry_price: float, stop_price: Optional[float], cfg: 'Config') -> float:
    """Risk a fixed % of equity per trade, sized off the stop distance.
    This is the standard approach for stop-based strategies and matches
    "adjust size so every trade risks the same $ amount"."""
    if stop_price is None:
        return cfg.default_lots
    stop_distance = abs(entry_price - stop_price)
    if stop_distance == 0:
        return 0.0
    risk_amount = equity * cfg.risk_pct_per_trade
    return risk_amount / (stop_distance * cfg.point_value_per_lot)


@dataclass
class Config:
    starting_equity: float = 10000.0
    risk_pct_per_trade: float = 0.01      # used by the default position sizer
    point_value_per_lot: float = 1.0      # $ per price-point per 1 lot: CHECK YOUR BROKER
    spread_points: float = 1.5            # round-trip cost, in points: CHECK YOUR BROKER
    slippage_points: float = 0.5
    commission_per_trade: float = 0.0
    default_lots: float = 1.0             # fallback size for stop-less orders
    position_sizer: Callable = None       # (equity, entry, stop, cfg) -> lots

    def __post_init__(self):
        if self.position_sizer is None:
            self.position_sizer = default_position_sizer


# --------------------------------------------------------------- orders ---

@dataclass
class OrderRequest:
    """What a strategy returns from on_bar() to request a new order."""
    direction: str                      # 'long' | 'short'
    stop_price: Optional[float] = None  # used for the default exit check AND for sizing
    order_type: str = 'market'          # 'market' | 'limit' | 'stop'
    price: Optional[float] = None       # required for 'limit' / 'stop', ignored for 'market'
    target_price: Optional[float] = None  # omit to manage the exit yourself via manage_position
    valid_until: Optional[pd.Timestamp] = None  # None = good until cancelled/filled
    tag: Optional[str] = None           # free-form label, carried into the trade log for debugging


@dataclass
class Position:
    """A currently-open trade."""
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: Optional[float]
    target_price: Optional[float] = None
    tag: Optional[str] = None
    force_close: bool = False           # set True in manage_position() to exit at this bar's close
    _worst_points: float = field(default=0.0, repr=False)
    _best_points: float = field(default=0.0, repr=False)


@dataclass
class Trade:
    """A closed trade, as it appears in the trade log."""
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: Optional[float]
    target_price: Optional[float]
    tag: Optional[str]
    exit_time: pd.Timestamp
    exit_price: float
    outcome: str          # 'win' | 'loss'
    pnl: float
    r_multiple: float
    hold_minutes: float
    mae_r: Optional[float]
    mfe_r: Optional[float]


# ------------------------------------------------------------- strategy ---

class Strategy:
    """Base class. Implement on_bar (and optionally prepare/manage_position)."""

    def __init__(self, **params):
        self.params = params

    def prepare(self, data: dict) -> None:
        """Called once before the run. `data` is the full {timeframe_key: DataFrame}
        dict passed to the engine, use it to precompute indicators/patterns."""
        pass

    def on_bar(self, ctx: 'Context') -> list:
        """Called once per bar of the execution timeframe. Return a list of
        OrderRequest to place (usually 0 or 1). Check ctx.position is None
        before requesting an entry, since only one position is held at a time."""
        return []

    def manage_position(self, ctx: 'Context', position: Position) -> None:
        """Optional. Called each bar a position is open, before the default
        stop/target check. Mutate `position` in place: e.g. update
        position.stop_price for a trailing stop, or set
        position.force_close = True to exit at this bar's close."""
        pass


class Context:
    """Live view into engine state, passed to the strategy each bar."""

    def __init__(self, engine: 'BacktestEngine', now: pd.Timestamp, bar):
        self.engine = engine
        self.now = now
        self.bar = bar

    @property
    def position(self) -> Optional[Position]:
        return self.engine.position

    @property
    def equity(self) -> float:
        return self.engine.equity

    @property
    def pending_orders(self) -> list:
        return self.engine.pending_orders

    @property
    def data(self) -> dict:
        return self.engine.data

    def count(self, name: str, n: int = 1):
        """Log a named event for the report's 'Strategy events' section."""
        self.engine.counters[name] = self.engine.counters.get(name, 0) + n


# ---------------------------------------------------------------- engine --

class BacktestEngine:
    def __init__(self, data: dict, execution_tf: str, strategy: Strategy, config: Config):
        """
        data: {timeframe_key: DataFrame}, each with columns time,open,high,low,close
        execution_tf: which key in `data` the engine steps through bar-by-bar
                      for fills/exits (the strategy can still read other
                      timeframes from ctx.data for its own logic)
        """
        self.data = {k: v.reset_index(drop=True) for k, v in data.items()}
        self.execution_tf = execution_tf
        self.df_exec = self.data[execution_tf]
        self.strategy = strategy
        self.cfg = config

        self.pending_orders: list[OrderRequest] = []
        self.position: Optional[Position] = None
        self.trades: list[Trade] = []
        self.equity = config.starting_equity
        self.equity_curve = []
        self.counters: dict = {}

        self.strategy.prepare(self.data)

    def run(self):
        for bar in self.df_exec.itertuples():
            now = bar.time
            ctx = Context(self, now, bar)

            if self.position is not None:
                self.strategy.manage_position(ctx, self.position)
                self._update_excursion(bar)
                self._check_exit(now, bar)

            if self.position is None:
                self._check_fills(now, bar)

            for order in self.strategy.on_bar(ctx):
                if self.position is not None:
                    self.counters['orders_rejected_position_open'] = \
                        self.counters.get('orders_rejected_position_open', 0) + 1
                    continue
                self.pending_orders.append(order)
                self.counters['orders_placed'] = self.counters.get('orders_placed', 0) + 1

            self.equity_curve.append({'time': now, 'equity': self.equity})

        return pd.DataFrame(self.equity_curve), self.trades

    # ---- internals ----

    def _try_fill(self, o: OrderRequest, bar):
        if o.order_type == 'market':
            return True, bar.open
        if o.order_type == 'limit':
            if o.direction == 'short':
                return (bar.high >= o.price), o.price
            return (bar.low <= o.price), o.price
        if o.order_type == 'stop':
            if o.direction == 'short':
                return (bar.low <= o.price), o.price
            return (bar.high >= o.price), o.price
        raise ValueError(f"unknown order_type '{o.order_type}'")

    def _check_fills(self, now, bar):
        still_pending = []
        for o in self.pending_orders:
            if o.valid_until is not None and now > o.valid_until:
                self.counters['orders_expired'] = self.counters.get('orders_expired', 0) + 1
                continue
            filled, fill_price = self._try_fill(o, bar)
            if filled and self.position is None:
                self.counters['orders_filled'] = self.counters.get('orders_filled', 0) + 1
                self.position = Position(
                    direction=o.direction, entry_time=now, entry_price=fill_price,
                    stop_price=o.stop_price, target_price=o.target_price, tag=o.tag,
                )
            else:
                still_pending.append(o)
        self.pending_orders = still_pending
        if self.position is not None and self.pending_orders:
            # single-position engine: entering a trade cancels any other pending orders
            self.counters['orders_cancelled_on_entry'] = \
                self.counters.get('orders_cancelled_on_entry', 0) + len(self.pending_orders)
            self.pending_orders = []

    def _update_excursion(self, bar):
        p = self.position
        if p is None:
            return
        if p.direction == 'short':
            adverse = bar.high - p.entry_price
            favorable = p.entry_price - bar.low
        else:
            adverse = p.entry_price - bar.low
            favorable = bar.high - p.entry_price
        p._worst_points = max(p._worst_points, adverse)
        p._best_points = max(p._best_points, favorable)

    def _check_exit(self, now, bar):
        p = self.position
        if p is None:
            return
        if p.force_close:
            self._close_position(p, now, bar.close, None)
            return
        hit_stop = hit_target = False
        if p.direction == 'short':
            hit_stop = p.stop_price is not None and bar.high >= p.stop_price
            hit_target = p.target_price is not None and bar.low <= p.target_price
        else:
            hit_stop = p.stop_price is not None and bar.low <= p.stop_price
            hit_target = p.target_price is not None and bar.high >= p.target_price

        if hit_stop:  # conservative tie-break: stop wins if both true in the same bar
            self._close_position(p, now, p.stop_price, 'loss')
        elif hit_target:
            self._close_position(p, now, p.target_price, 'win')

    def _close_position(self, p: Position, now, exit_price: float, outcome: Optional[str]):
        lots = self.cfg.position_sizer(self.equity, p.entry_price, p.stop_price, self.cfg)

        raw_move = (exit_price - p.entry_price) if p.direction == 'long' else (p.entry_price - exit_price)
        gross_pnl = raw_move * self.cfg.point_value_per_lot * lots
        cost = (self.cfg.spread_points + self.cfg.slippage_points) * self.cfg.point_value_per_lot * lots \
            + self.cfg.commission_per_trade
        net_pnl = gross_pnl - cost

        if outcome is None:  # force_close: derive win/loss from realized pnl
            outcome = 'win' if net_pnl > 0 else 'loss'

        stop_distance = abs(p.entry_price - p.stop_price) if p.stop_price is not None else None
        risk_amount = stop_distance * self.cfg.point_value_per_lot * lots if stop_distance else None

        trade = Trade(
            direction=p.direction, entry_time=p.entry_time, entry_price=p.entry_price,
            stop_price=p.stop_price, target_price=p.target_price, tag=p.tag,
            exit_time=now, exit_price=exit_price, outcome=outcome, pnl=net_pnl,
            r_multiple=(net_pnl / risk_amount) if risk_amount else 0.0,
            hold_minutes=(now - p.entry_time).total_seconds() / 60.0,
            mae_r=(p._worst_points / stop_distance) if stop_distance else None,
            mfe_r=(p._best_points / stop_distance) if stop_distance else None,
        )
        self.equity += net_pnl
        self.trades.append(trade)
        self.position = None
