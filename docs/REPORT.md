# 4H/15m Fair Value Gap Strategy: Backtest Report

**Data:** Dukascopy CFD index 1-minute bid data (exported with Tickstory), 2023-08-15 to 2026-08-14
**Instruments:** US30 (Dow Jones 30) and NAS100 (Dukascopy `USATECH`)
**Engine:** event-driven, bar by bar, closed candles only, conservative same-candle tie-break
**Reproduce:** every number below comes from `python analysis/full_study.py` (raw results are in `docs/results_*.json`)

## 1. Summary

- **No demonstrable edge.** Baseline (07:00-10:00 GMT, 1:2 RR): US30 averaged -0.30R per trade over 34 trades (-9.9%), NAS100 +0.05R over 39 trades (+1.5%). The instruments disagree in sign, and neither result is distinguishable from zero (bootstrap 95% intervals [-0.74, +0.15] and [-0.40, +0.51]).
- **The entry trigger carries no detectable directional information.** Across roughly 15,000 fifteen-minute gaps per instrument, forward 1h and 4h moves after bullish and bearish gaps do not differ from the market's unconditional drift (largest |t| = 1.32).
- **The one statistically notable pattern runs against the strategy's thesis.** After price first revisits a bearish 4H gap, the next 24 hours rose 39 to 47 points more than the market's average drift on both instruments (t = 2.1 on US30, 2.4 on NAS100). This is suggestive, not established: windows overlap, it is one of several comparisons, and on US30 it is concentrated in 2025-2026.
- **Small-sample results are fragile.** A one-minute candle misalignment, found and fixed during this work (section 8.2), moved baseline results by 0.14R to 0.22R per trade, enough to flip NAS100's sign, while the larger all-hours samples moved by 0.05R or less.
- **What this cannot show:** that the strategy loses. With 34 to 39 trades the 95% intervals are about 0.9R wide, so a small gain on US30 or a sizeable loss on NAS100 cannot be ruled out. It shows the absence of evidence that the rules work.

## 2. Strategy

An ICT-style multi-timeframe imbalance strategy: higher-timeframe context, lower-timeframe trigger.

**Rules (from the original handwritten notes)**
1. A 4H Fair Value Gap forms (bearish for shorts, bullish for longs). An FVG is a 3-candle pattern where candle A's low sits above candle C's high (bearish), or A's high below C's low (bullish). Wick-based.
2. Wait for price to retrace back into the 4H gap.
3. After the retrace, wait for a fresh 15m FVG in the same direction at that zone.
4. Enter with a limit order at the near edge of the 15m gap (bottom for shorts, top for longs).
5. Stop at the full high (shorts) or low (longs) of the first candle of the 15m pattern.
6. Take profit at 1:2 RR.
7. Entries only 07:00 to 10:00 GMT.
8. Position size set from the stop distance so every trade risks the same dollar amount.

**Assumptions added where the notes were silent** (all configurable)
- The session window applies to the close time of the 15m candle that completes the gap (07:00 to 09:45); the resulting limit order stays live until 10:00. It does not gate 4H gap formation or the retrace.
- A 4H zone is discarded if a 15m candle closes through it, or after 48 hours.
- Only the first qualifying 15m gap per 4H zone is traded.
- Unfilled orders are cancelled at 10:00. One position at a time.
- No minimum gap size. Gaps that span weekends or session breaks are not excluded.

## 3. Data and method

- **Data:** 1,021,914 (US30) and 1,016,028 (NAS100) one-minute bars, resampled to 68,245 fifteen-minute and 4,793 four-hour candles (US30) and 67,882 and 4,778 (NAS100). Timestamps are UTC, stamped by bar open time (checked against session edges: the last bar before the daily close is stamped 20:14, and the first bar after the reopen is stamped exactly 22:00).
- **Candles:** 15m and 4H candles are labelled by close time, so a candle's label is the moment its OHLC becomes known. The 4H candles are anchored to 00:00 UTC.
- **Fills:** an order placed when a candle closes can only fill on later candles. Limit orders fill at their price when touched (no queue modelling). If stop and target are both touched inside one candle, the stop is assumed to have hit first.
- **Risk and costs:** 1% of equity risked per trade from $10,000, $1 per point per lot, 2.0 points spread plus 0.5 points slippage per trade, no commission or financing.
- **Equity statistics** (drawdown, time under water) use closed-trade equity, not mark-to-market.

## 4. Baseline results (07:00-10:00 GMT, RR 1:2)

### 4.1 Full metrics

| Metric | US30 | NAS100 |
|---|---|---|
| Trades | 34 | 39 |
| Wins / losses | 9 / 25 | 15 / 24 |
| Win rate | 26.5% | 38.5% |
| Long / short trades | 13 / 21 | 20 / 19 |
| Avg R (expectancy) | -0.299 | +0.049 |
| Median R | -1.06 | -1.05 |
| Std dev of R | 1.35 | 1.47 |
| Average winning trade | +1.91R | +1.88R |
| Average losing trade | -1.09R | -1.10R |
| Payoff ratio (avg win / avg loss) | 1.75 | 1.72 |
| Profit factor | 0.62 | 1.06 |
| Net P&L | -$994 | +$151 |
| Net P&L in R | -10.2R | +1.9R |
| Net return on $10,000 | -9.94% | +1.51% |
| Max drawdown (closed-trade equity) | -10.62% | -7.14% |
| Longest time under water | 1047 days | 870 days |
| Max consecutive losses | 7 | 5 |
| Average hold time | 1.5 hours | 1.8 hours |
| Avg MAE, all trades | 1.39R | 1.06R |
| Avg MFE, all trades | 1.05R | 1.41R |
| Avg MAE on winners | 0.46R | 0.40R |
| Avg MFE on losers | 0.52R | 0.63R |
| Losers that reached +1R first | 7 of 25 | 4 of 24 |

At 1:2 RR with these costs, break-even needs a win rate of roughly 36.5% to 37%. US30 delivered 26.5% and NAS100 38.5%.

### 4.2 Setup funnel

| Stage | US30 | NAS100 |
|---|---|---|
| 4H bearish zones formed | 489 | 497 |
| 4H bullish zones formed | 579 | 697 |
| Zones invalidated: 48h timeout | 473 | 518 |
| Zones invalidated: price closed through | 595 | 674 |
| Zones retraced into (engaged) | 683 | 772 |
| Limit orders placed (15m FVG found in session) | 48 | 52 |
| Orders filled | 34 | 39 |
| Orders expired unfilled | 14 | 13 |

Zones are counted when they form; the stages are not disjoint (every zone is eventually invalidated, including those that were revisited). About 64% to 65% of 4H gaps were revisited. Only about 7% of revisited zones produced a qualifying 15m gap inside the session window, and 25% to 30% of the resulting orders never filled.

### 4.3 Cost decomposition (average R per trade)

| Configuration | US30 | NAS100 |
|---|---|---|
| 07-10 GMT, real costs (2.0 + 0.5 pts) | -0.299 | +0.049 |
| 07-10 GMT, zero costs | -0.206 | +0.154 |
| All hours, real costs | +0.056 | -0.136 |
| All hours, zero costs | +0.145 | -0.024 |

Costs of 2.5 points consume roughly 0.09R to 0.11R per trade. With zero costs the baseline is still negative on US30 and positive on NAS100, so the sign disagreement between instruments is not a cost effect.

### 4.4 Sensitivity checks (same data, so in-sample)

| Variant (trades / avg R / PF) | US30 | NAS100 |
|---|---|---|
| Baseline 07-10 GMT, RR 1:2 | 34 / -0.299 / 0.62 | 39 / +0.049 / 1.06 |
| All hours, RR 1:2 | 152 / +0.056 / 1.07 | 169 / -0.136 / 0.80 |
| 13-16 GMT, RR 1:2 | 30 / -0.242 / 0.69 | 40 / +0.069 / 1.09 |
| 07-10 GMT, RR 1:1 | 34 / -0.152 / 0.74 | 39 / -0.131 / 0.76 |
| 07-10 GMT, RR 1:1.5 | 34 / -0.358 / 0.54 | 39 / +0.049 / 1.07 |
| 07-10 GMT, RR 1:3 | 34 / -0.152 / 0.80 | 39 / +0.126 / 1.14 |

No variant is positive on both instruments with a meaningful sample. Note that average R can be identical across RR settings (for example US30 at 1:1 and 1:3): total R equals (RR + 1) x wins - trades - total cost, so different win counts can give the same sum.

## 5. Long vs short

### 5.1 Baseline

| Metric | US30 longs | US30 shorts | NAS100 longs | NAS100 shorts |
|---|---|---|---|---|
| Trades | 13 | 21 | 20 | 19 |
| Wins / losses | 3 / 10 | 6 / 15 | 8 / 12 | 7 / 12 |
| Win rate | 23.1% | 28.6% | 40.0% | 36.8% |
| Avg R | -0.41 | -0.23 | +0.10 | -0.00 |
| Median R | -1.08 | -1.04 | -1.04 | -1.05 |
| Avg winning trade | +1.95R | +1.89R | +1.87R | +1.89R |
| Avg losing trade | -1.11R | -1.08R | -1.09R | -1.10R |
| Profit factor | 0.51 | 0.70 | 1.13 | 0.98 |
| Net P&L | -$528 | -$466 | +$178 | -$28 |
| Net P&L in R | -5.3R | -4.9R | +1.9R | -0.0R |
| Max consecutive losses | 5 | 5 | 8 | 5 |
| Avg MAE | 1.80R | 1.14R | 0.99R | 1.13R |
| Avg MFE | 1.15R | 0.99R | 1.35R | 1.47R |
| Avg MFE on losers | 0.84R | 0.31R | 0.65R | 0.61R |

### 5.2 Baseline by year (n / win rate / avg R / net P&L)

| Year (n / WR / avg R / P&L) | US30 longs | US30 shorts | NAS100 longs | NAS100 shorts |
|---|---|---|---|---|
| 2023 | 2 / 0% / -1.09 / -$218 | 2 / 50% / +0.39 / +$75 | 1 / 100% / +1.80 / +$180 | 0 |
| 2024 | 3 / 0% / -1.15 / -$337 | 10 / 40% / +0.09 / +$77 | 7 / 43% / +0.14 / +$92 | 9 / 22% / -0.46 / -$423 |
| 2025 | 3 / 67% / +0.92 / +$261 | 4 / 25% / -0.33 / -$129 | 2 / 0% / -1.13 / -$226 | 7 / 57% / +0.61 / +$413 |
| 2026 | 5 / 20% / -0.48 / -$234 | 5 / 0% / -1.05 / -$489 | 10 / 40% / +0.14 / +$132 | 3 / 33% / -0.05 / -$18 |

### 5.3 All-hours variant (larger samples)

| Metric | US30 longs | US30 shorts | NAS100 longs | NAS100 shorts |
|---|---|---|---|---|
| Trades | 75 | 77 | 90 | 79 |
| Win rate | 34.7% | 41.6% | 25.6% | 40.5% |
| Avg R | -0.05 | +0.16 | -0.34 | +0.10 |
| Profit factor | 0.91 | 1.23 | 0.56 | 1.13 |
| Net P&L | -$468 | +$1,182 | -$2,782 | +$600 |
| Max consecutive losses | 6 | 9 | 14 | 9 |

### 5.4 All-hours by year (n / win rate / avg R / net P&L)

| Year (n / WR / avg R / P&L) | US30 longs | US30 shorts | NAS100 longs | NAS100 shorts |
|---|---|---|---|---|
| 2023 | 5 / 0% / -1.09 / -$562 | 16 / 44% / +0.18 / +$288 | 10 / 30% / -0.21 / -$224 | 8 / 25% / -0.51 / -$393 |
| 2024 | 21 / 43% / +0.16 / +$299 | 24 / 42% / +0.16 / +$382 | 25 / 24% / -0.44 / -$1,049 | 30 / 47% / +0.27 / +$744 |
| 2025 | 30 / 40% / +0.13 / +$398 | 18 / 39% / +0.07 / +$78 | 32 / 25% / -0.35 / -$980 | 29 / 38% / +0.05 / +$78 |
| 2026 | 19 / 26% / -0.27 / -$603 | 19 / 42% / +0.21 / +$434 | 23 / 26% / -0.28 / -$528 | 12 / 42% / +0.19 / +$170 |

In the baseline both directions lose on US30 (longs -0.41R on 13 trades, shorts -0.23R on 21), while on NAS100 longs are +0.10R (20) and shorts are -0.00R (19). Average win and loss sizes are near-identical across directions, so differences come from win rate, on cells of 13 to 21 trades.

In the all-hours variant shorts are positive on both instruments (US30 +0.16R, profit factor 1.23, 77 trades; NAS100 +0.10R, 1.13, 79 trades) and longs negative (US30 -0.05R; NAS100 -0.34R, profit factor 0.56, 90 trades, worst run of 14 consecutive losses). The sign is consistent across instruments here, but each shorts estimate is within one standard error of zero, and the baseline split does not show the same pattern. NAS100 all-hours longs is the only large-sample cut that looks clearly negative; it is one of eight direction cuts reported in this section.

## 6. Year by year

2023 covers 15 August to 31 December; 2026 covers 1 January to 14 August. Baseline per-year samples are 1 to 16 trades and are descriptive only.

### 6.1 Baseline (07:00-10:00 GMT)

**US30**

| Year | Trades | Win rate | Avg R | Profit factor | Net P&L | Long / short |
|---|---|---|---|---|---|---|
| 2023 | 4 | 25.0% | -0.35 | 0.57 | -$143 | 2 / 2 |
| 2024 | 13 | 30.8% | -0.20 | 0.74 | -$260 | 3 / 10 |
| 2025 | 7 | 42.9% | +0.21 | 1.32 | +$132 | 3 / 4 |
| 2026 | 10 | 10.0% | -0.77 | 0.20 | -$723 | 5 / 5 |
| **Total** | **34** | **26.5%** | **-0.30** | **0.62** | **-$994** | **13 / 21** |

**NAS100**

| Year | Trades | Win rate | Avg R | Profit factor | Net P&L | Long / short |
|---|---|---|---|---|---|---|
| 2023 | 1 | 100.0% | +1.80 | inf | +$180 | 1 / 0 |
| 2024 | 16 | 31.2% | -0.20 | 0.74 | -$331 | 7 / 9 |
| 2025 | 9 | 44.4% | +0.22 | 1.34 | +$188 | 2 / 7 |
| 2026 | 13 | 38.5% | +0.10 | 1.13 | +$115 | 10 / 3 |
| **Total** | **39** | **38.5%** | **+0.05** | **1.06** | **+$151** | **20 / 19** |

### 6.2 All-hours variant

**US30**

| Year | Trades | Win rate | Avg R | Profit factor | Net P&L | Long / short |
|---|---|---|---|---|---|---|
| 2023 | 21 | 33.3% | -0.12 | 0.83 | -$274 | 5 / 16 |
| 2024 | 45 | 42.2% | +0.16 | 1.24 | +$682 | 21 / 24 |
| 2025 | 48 | 39.6% | +0.10 | 1.15 | +$476 | 30 / 18 |
| 2026 | 38 | 34.2% | -0.03 | 0.94 | -$169 | 19 / 19 |
| **Total** | **152** | **38.2%** | **+0.06** | **1.07** | **+$714** | **75 / 77** |

**NAS100**

| Year | Trades | Win rate | Avg R | Profit factor | Net P&L | Long / short |
|---|---|---|---|---|---|---|
| 2023 | 18 | 27.8% | -0.34 | 0.60 | -$617 | 10 / 8 |
| 2024 | 55 | 36.4% | -0.05 | 0.92 | -$305 | 25 / 30 |
| 2025 | 61 | 31.1% | -0.16 | 0.76 | -$901 | 32 / 29 |
| 2026 | 35 | 31.4% | -0.12 | 0.82 | -$358 | 23 / 12 |
| **Total** | **169** | **32.5%** | **-0.14** | **0.80** | **-$2,182** | **90 / 79** |

Baseline US30 is negative in three of four years, with 2026 particularly poor (1 win in 10 trades). Baseline NAS100 is negative in 2024 and positive in 2025 and 2026. In the larger all-hours samples, NAS100 is negative in every year, while US30 is mixed (positive in 2024 and 2025, slightly negative otherwise).

## 7. Event studies

### 7.1 Does a 15m gap predict direction?

For every 15m FVG in the three years, the price change over the next 1 and 4 hours is compared with the unconditional average change over the same horizon. If gaps carried information, bullish gaps would show positive excess and bearish gaps negative excess.

| Instrument | Horizon | Gap type | n | Mean fwd move (pts) | Unconditional drift (pts) | Excess (pts) | t-stat |
|---|---|---|---|---|---|---|---|
| US30 | 1h | bullish | 7,927 | +1.7 | +1.1 | +0.6 | +0.71 |
| US30 | 1h | bearish | 7,019 | +1.7 | +1.1 | +0.6 | +0.61 |
| US30 | 4h | bullish | 7,926 | +4.5 | +4.3 | +0.2 | +0.13 |
| US30 | 4h | bearish | 7,019 | +6.9 | +4.3 | +2.6 | +1.32 |
| NAS100 | 1h | bullish | 8,211 | +0.8 | +0.9 | -0.1 | -0.15 |
| NAS100 | 1h | bearish | 6,695 | +1.1 | +0.9 | +0.2 | +0.24 |
| NAS100 | 4h | bullish | 8,209 | +5.1 | +3.5 | +1.6 | +1.28 |
| NAS100 | 4h | bearish | 6,695 | +4.6 | +3.5 | +1.1 | +0.70 |

All excess moves are small and statistically indistinguishable from zero (largest |t| = 1.32). Overlapping windows make these t-statistics, if anything, optimistic, which strengthens the null result. The gap that triggers the strategy's entry carries no detectable directional information.

### 7.2 Does a 4H gap revisit continue in the gap direction?

For each 4H gap, the first 15m candle that trades back into the zone (within 48 hours, before any close through it) is the event. The table shows the price change over the following 24 hours against unconditional 24-hour drift.

| Instrument | Zone type | n revisits | Mean 24h move (pts) | Unconditional drift (pts) | Excess (pts) | t-stat |
|---|---|---|---|---|---|---|
| US30 | bearish | 335 | +73.7 | +26.3 | +47.4 | +2.13 |
| US30 | bullish | 347 | +13.1 | +26.3 | -13.2 | -0.68 |
| NAS100 | bearish | 328 | +60.3 | +21.2 | +39.2 | +2.44 |
| NAS100 | bullish | 442 | +14.5 | +21.2 | -6.7 | -0.51 |

For bearish gaps, the market rose more than drift after the revisit on both instruments, the opposite of the thesis that a revisit of a bearish gap acts as resistance. Bullish revisits show no significant effect. Breaking the bearish case down by year:

| Year | US30 n | US30 mean 24h move | US30 drift | NAS100 n | NAS100 mean 24h move | NAS100 drift |
|---|---|---|---|---|---|---|
| 2023 | 47 | -6.0 | +29.0 | 45 | +12.0 | +17.7 |
| 2024 | 107 | -42.7 | +20.8 | 104 | +52.8 | +18.5 |
| 2025 | 108 | +133.7 | +23.6 | 105 | +78.3 | +18.0 |
| 2026 | 73 | +206.8 | +38.1 | 74 | +74.7 | +32.8 |

On US30 the effect is entirely a 2025-2026 phenomenon (2023 and 2024 moved less than drift, which is pro-thesis). On NAS100 it appears in 2024, 2025 and 2026. Revisits cluster in time and the 24-hour windows overlap, so the t-statistics overstate certainty, and this is one of several comparisons examined. It is a hypothesis, not a finding.

### 7.3 Limit entry vs market entry

A limit order at the gap edge only fills if price pulls back, which can select against the best setups. The same orders were replayed on one-minute data with a market entry at the confirming candle's close (same stop, target at 2R from the new entry).

| Instrument | Orders requested | Limit at edge: filled | Limit avg R | Market at confirmation: filled | Market avg R |
|---|---|---|---|---|---|
| US30 | 48 | 34 | -0.299 | 48 | +0.002 |
| NAS100 | 52 | 39 | +0.049 | 52 | -0.031 |

Limit minus market is -0.30R on US30 but +0.08R on NAS100: the sign is inconsistent, so there is no evidence of systematic adverse selection here. Market-entry results are close to zero on both instruments.

## 8. Statistical confidence and data-error sensitivity

### 8.1 Bootstrap on baseline trades

| Instrument | Observed avg R | 95% CI (bootstrap) | P(avg R > 0) |
|---|---|---|---|
| US30 | -0.299 | [-0.74, +0.15] | 9% |
| NAS100 | +0.049 | [-0.40, +0.51] | 56% |

10,000 resamples of each instrument's baseline R multiples. Both intervals comfortably include zero.

### 8.2 A one-minute candle misalignment

The first version of the candle builder grouped one-minute rows with right-closed bins, which is only correct for data stamped by bar close. This data is stamped by bar open, so every candle dropped its own first minute and absorbed the first minute of the next candle, a one-minute lookahead relative to the candle's close label. It was found during pre-publication review with a hand-built test, fixed, covered by a regression test, and every number in this report was regenerated. A second, smaller fix (a zone could be marked as revisited by the very candle that completed it) changed only the funnel counter and no trades.

| Configuration | Misaligned candles | Corrected candles |
|---|---|---|
| US30 baseline | 32 trades, -0.156R, PF 0.78, -5.2% | 34 trades, -0.299R, PF 0.62, -9.9% |
| NAS100 baseline | 35 trades, -0.173R, PF 0.76, -6.2% | 39 trades, +0.049R, PF 1.06, +1.5% |
| US30 all hours | 152 trades, +0.012R | 152 trades, +0.056R |
| NAS100 all hours | 162 trades, -0.135R | 169 trades, -0.136R |

A one-minute change moved the 35-trade baselines by 0.14R to 0.22R per trade, enough to flip NAS100's sign, while the roughly 160-trade samples moved by 0.05R or less. This is a concrete illustration of why results on 30 to 40 trades should not be trusted, and why the event studies (15,000 events) carry more weight than any single backtest configuration.

## 9. Limitations

- One data source (a single bank's CFD feed), bid prices only. Real execution venues differ slightly in candle highs and lows, which matters for gap detection.
- Three years, in a broadly rising market, with no out-of-sample holdout. The sensitivity checks were all run on the same data without correction for multiple comparisons.
- Baseline samples are 34 and 39 trades.
- Costs are assumed rather than measured. Limit orders are assumed to fill whenever price touches them, which flatters results. No financing, commission or news-spike slippage.
- Gaps are wick-based with no minimum size, and gaps spanning weekends or session breaks are not excluded.
- The 4H candles are anchored to 00:00 UTC. A broker using a different server time would see different 4H candles and therefore different gaps.
- Event-study t-statistics assume independent observations; overlapping windows make them overconfident.
- The results describe the rules as written. Any discretionary judgement the original author applies is not modelled.
