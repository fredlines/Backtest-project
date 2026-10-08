"""
HTF/LTF Fair Value Gap cascade strategy (your handwritten-notes strategy).

  1. A 4H FVG forms (bullish or bearish depending on trade direction).
  2. Wait for price to retrace back INTO that 4H FVG zone.
  3. After that retracement, watch for a fresh 15m FVG (same direction) to
     form at/inside that zone.
  4. Place a limit order at the edge of the 15m FVG (bottom for shorts, top
     for longs). Stop = the full high/low of the first candle of the 15m FVG.
     Target = configurable RR multiple.
  5. Only look for / place entries within the configurable session window (GMT).
     The window applies to the CLOSE time of the 15m candle that completes the
     FVG (07:00 up to 09:45 for the default window); the resulting limit order
     stays live until 10:00.
  6. Sizing/costs are handled generically by the engine's Config.

Assumptions made where the handwritten notes didn't fully specify (see
main README for the full list): the session window gates the 15m FVG
search and order placement, not 4H FVG formation; a 4H zone is discarded
if price closes fully back through it, or after zone_max_age_hours; only
the first qualifying 15m FVG per 4H zone is traded; unfilled orders expire
at the end of that day's session window.
"""

import pandas as pd
from engine import Strategy, OrderRequest
from fvg_detector import detect_fvgs, zones_overlap


class _Zone:
    __slots__ = ('direction', 'top', 'bottom', 'formed_time', 'state')

    def __init__(self, direction, top, bottom, formed_time):
        self.direction = direction
        self.top = top
        self.bottom = bottom
        self.formed_time = formed_time
        self.state = 'waiting_retracement'   # -> 'engaged' -> 'order_placed'


class FVGCascadeStrategy(Strategy):
    # which keys of the `data` dict this strategy needs, and which timeframe
    # the engine should step through bar-by-bar
    required_timeframes = {'htf': '4h', 'ltf': '15min'}
    execution_tf = 'ltf'

    def __init__(self, directions=('short', 'long'), session_start_hour_gmt=7,
                 session_end_hour_gmt=10, rr_target=2.0, zone_max_age_hours=48.0,
                 htf_key='htf', ltf_key='ltf'):
        super().__init__()
        self.directions = tuple(directions)
        self.session_start_hour_gmt = session_start_hour_gmt
        self.session_end_hour_gmt = session_end_hour_gmt
        self.rr_target = rr_target
        self.zone_max_age_hours = zone_max_age_hours
        self.htf_key = htf_key
        self.ltf_key = ltf_key

        self.active_zones: list[_Zone] = []
        self._next_htf_idx = 0

    def _wanted_directions(self):
        want = set()
        if 'short' in self.directions:
            want.add('bearish')
        if 'long' in self.directions:
            want.add('bullish')
        return want

    def prepare(self, data: dict) -> None:
        df_htf = data[self.htf_key]
        df_ltf = data[self.ltf_key]
        self.fvgs_htf = detect_fvgs(df_htf)
        fvgs_ltf = detect_fvgs(df_ltf)
        self.fvgs_ltf_by_time = {}
        for f in fvgs_ltf:
            self.fvgs_ltf_by_time.setdefault(f['formed_time'], []).append(f)

    def _in_session(self, ts: pd.Timestamp) -> bool:
        return self.session_start_hour_gmt <= ts.hour < self.session_end_hour_gmt

    def on_bar(self, ctx) -> list:
        now, bar = ctx.now, ctx.bar

        # 1. activate any new HTF zones formed as of now
        while (self._next_htf_idx < len(self.fvgs_htf) and
               self.fvgs_htf[self._next_htf_idx]['formed_time'] <= now):
            f = self.fvgs_htf[self._next_htf_idx]
            if f['direction'] in self._wanted_directions():
                self.active_zones.append(_Zone(f['direction'], f['top'], f['bottom'], f['formed_time']))
                ctx.count(f"zones_formed_{f['direction']}")
            self._next_htf_idx += 1

        # 2. expire stale/invalidated zones
        keep = []
        for z in self.active_zones:
            age_hours = (now - z.formed_time).total_seconds() / 3600.0
            if age_hours > self.zone_max_age_hours:
                ctx.count('zones_invalidated_timeout')
                continue
            if z.direction == 'bearish' and bar.close > z.top:
                ctx.count('zones_invalidated_close_through')
                continue
            if z.direction == 'bullish' and bar.close < z.bottom:
                ctx.count('zones_invalidated_close_through')
                continue
            keep.append(z)
        self.active_zones = keep

        # 3. update engagement (has price retraced into the zone?)
        for z in self.active_zones:
            if (z.state == 'waiting_retracement' and now > z.formed_time
                    and bar.low <= z.top and bar.high >= z.bottom):
                z.state = 'engaged'
                ctx.count('zones_engaged')

        if ctx.position is not None:
            return []  # only one position at a time

        if not self._in_session(now):
            return []

        matches = self.fvgs_ltf_by_time.get(now, [])
        if not matches:
            return []

        orders = []
        for f in matches:
            for z in self.active_zones:
                if z.state != 'engaged':
                    continue
                if z.direction != f['direction']:
                    continue
                if not zones_overlap(z.bottom, z.top, f['bottom'], f['top']):
                    continue

                if f['direction'] == 'bearish':
                    entry, stop = f['bottom'], f['stop_ref']
                    target = entry - self.rr_target * (stop - entry)
                    direction = 'short'
                else:
                    entry, stop = f['top'], f['stop_ref']
                    target = entry + self.rr_target * (entry - stop)
                    direction = 'long'

                valid_until = now.normalize() + pd.Timedelta(hours=self.session_end_hour_gmt)
                orders.append(OrderRequest(
                    direction=direction, order_type='limit', price=entry,
                    stop_price=stop, target_price=target, valid_until=valid_until,
                    tag=f"zone@{z.formed_time.isoformat()}",
                ))
                z.state = 'order_placed'
                ctx.count('orders_requested_by_strategy')
                break  # one order per matching zone
        return orders
