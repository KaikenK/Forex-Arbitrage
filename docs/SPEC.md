# Arbex — Technical Specification: Onshore–Offshore USD/INR Basis Detection

**Status:** Active · Phase 3 · Path 2 · comparison basis = **Option A (futures↔futures)**
**Owners:** arbitrage / feeds / measurement team (semantic engine excluded — separate owner)
**Companion docs:** `CLAUDE.md`, "Arbex Build Plan" artifact, "Arbex Literature & Novelty" dossier

---

## 1. Goal

Extend Arbex to detect, classify, rank and assess the execution-feasibility of the
**real onshore–offshore USD/INR basis dislocation** using free, retail-accessible market
data, and produce a reproducible empirical results set for a conference paper.

Non-goal: live trading. Execution stays simulated (feasibility scoring only).

## 2. Definitions

| Term | Meaning |
|---|---|
| **Onshore leg** | NSE (or BSE) Currency Derivatives Segment USD/INR near-month future, incl. 5-level market depth. |
| **Offshore leg** | USD/INR futures on CME (contract "6R" / INR) or SGX; used as a proxy for the NDF market. |
| **OTC spot leg** | Aggregated interbank USD/INR spot from a free API (Dukascopy demo / OANDA practice / Twelve Data). |
| **Reference** | RBI reference rate / FBIL daily fix. Validation anchor only. |
| **Basis** | Normalised-price difference between two legs at an aligned instant. |
| **Comparison basis** | The single price representation all legs are converted to before comparison (see §5). |
| **Dislocation event** | An aligned instant where \|basis\| between a leg pair exceeds `min_basis_threshold` (pips). |
| **Persistence class** | `ephemeral` / `flickering` / `persistent` — re-tuned for the minute-scale regime. |

## 3. The comparison basis — **Option A: futures ↔ futures** *(decided)*

All legs are converted to a **normalised forward price at one common target expiry
`T*`**, then compared. `T*` is the **NSE near-month contract's expiry** (our anchor —
most liquid onshore instrument).

### 3.1 Per-leg conversion

Let `pip = 0.01`, `carry = CARRY_RATE_ANNUAL` (annualised USD/INR forward premium ≈
CIP-implied INR−USD rate differential; default `0.019`, a stated assumption — see §3.3),
`d(a,b)` = calendar days from `a` to `b`.

| Leg | Input | Normalised forward `F*` |
|---|---|---|
| **Onshore** (NSE USD/INR fut, expiry `T_on`) | `F_on` | `F_on · (1 + carry · d(T_on, T*)/365)` |
| **Offshore** (CME/SGX USD/INR fut, expiry `T_off`) | `F_off` | `F_off · (1 + carry · d(T_off, T*)/365)` |
| **OTC spot** (interbank spot `S`, time `t`) | `S` | `S · (1 + carry · d(t, T*)/365)` |

When a futures leg's own expiry equals `T*`, its adjustment is 0. NSE and CME monthly
expiries are typically 0–5 days apart; at a 1.9 % premium the adjustment is
≈ **0.45 pips per day** of expiry mismatch (≈ 2.3 pips at the 5-day extreme). Small, and
it largely cancels for the onshore↔offshore pair (both futures get the same treatment),
but it is applied and recorded per event as `carry_adjustment_pips` so the residual is
visible. Pairs involving the spot leg carry the full ~1 month of premium (~50 pips), so
the spot-leg basis is **carry-rate-sensitive** and only meaningful once `carry` is
calibrated (§3.3) — the onshore↔offshore pair is the robust headline number.

### 3.2 Basis and dislocation event

For an aligned instant and a leg pair `(X, Y)`:

```
basis_pips(X, Y) = (F*_X − F*_Y) / pip
```

A **dislocation event** fires when `|basis_pips| ≥ min_basis_threshold_pips` for an enabled
leg pair, with both quotes inside the same `basis_window_ms` window (§FR-3.1).

### 3.3 Rate source & validation

- **v1:** `carry` held constant at `CARRY_RATE_ANNUAL` (config), flagged in every output.
- **v2:** infer `carry` from the NSE forward curve (NSE lists 3 monthly expiries) — the
  implied onshore forward rate between two listed contracts.
- **Validation:** every `F*_leg` must stay within `reference_band_pips` of the RBI
  reference rate carried to `T*`; `InstrumentNormalizer` unit tests assert this and check
  the conversion against hand-computed values.
- **Peer review:** the §3.1 formulae to be checked by the team + guide before results runs.

### 3.4 Option B (implied spot) — secondary view only

A `to_implied_spot()` helper (`F / (1 + carry · τ)`) is also provided for the dashboard's
3-way onshore/offshore/OTC chart. It is **not** used for the headline result; label any
chart built from it "implied spot, carry assumption stated".

## 4. Functional requirements

### FR-1 · Feed ingestion
- FR-1.1 An onshore-broker `DataSourceInterface` implementation (`DhanDataSource` /
  `UpstoxDataSource` — free APIs; `KiteDataSource` skeleton exists but Kite costs ₹500/mo)
  streams the NSE-CDS USD/INR near-month future via WebSocket; `get_tick()` returns a
  `RawTick` (with `extra["expiry"]`); a `get_depth(symbol)` extension returns the top 5
  bid and 5 ask levels `(price, qty)`.
- FR-1.2 An onshore `StreamAdapter(BaseStreamAdapter)` publishes real 5-level DOM to
  `arbex.orderbooks` for `OrderbookService`, replacing the mock `MT5StreamAdapter` for USD/INR.
- FR-1.3 A `CMEDelayedSource` polls a delayed offshore USD/INR future; each `RawTick`
  carries `extra["staleness_ms"]`.
- FR-1.4 The existing `RESTDataSource` is configured for OTC spot USD/INR with an
  appropriate parser; synthesised bid/ask is flagged `extra["synthetic_spread"] = true`.
- FR-1.5 All feeds are selected by `DATA_MODE = LIVE_USDINR_BASIS` and registered on
  `MultiSourceStreamer` in `server/main.py`'s lifespan.

### FR-2 · Normalisation
- FR-2.1 `InstrumentNormalizer.to_common_forward(price, leg, quote_expiry, target_expiry)`
  converts each leg's quote to a normalised forward at `T*` per §3.1; it also returns
  `carry_adjustment_pips`. A `to_implied_spot()` helper (§3.4) exists for the dashboard only.
- FR-2.2 Unit tests validate conversions against hand-computed values and the RBI reference
  (normalised forwards must sit within `reference_band_pips` of the reference carried to `T*`).
- FR-2.3 All timestamps are mapped to one reference clock. Per-source delivery latency is
  measured (rolling estimate) and exposed in source stats; residual uncertainty is recorded.

### FR-3 · Detection
- FR-3.1 `TickAligner` supports a per-detection-type window: keep the tight (~20 ms)
  same-venue window; add a configurable `basis_window_ms` (default 2000, range 1000–5000).
- FR-3.2 `BasisArbitrageEngine` (extends `ArbitrageEngine`) detects dislocation events for
  each enabled leg pair: `onshore↔offshore`, `onshore↔otc`, `offshore↔otc`. Each event
  records leg pair, direction (which leg is cheap), basis in pips, and both source timestamps.
- FR-3.3 `OpportunityTracker` persistence-class thresholds are configurable and re-tuned:
  proposed `ephemeral < 5 s`, `flickering 5–60 s`, `persistent > 60 s` (validate against data).

### FR-4 · Feasibility
- FR-4.1 `SimulatedExecutionFilter` consumes **real** onshore 5-level depth for the onshore
  leg and produces a real slippage estimate and a `Strong / Risky / Unlikely` verdict.
- FR-4.2 For the offshore leg, the filter runs a sensitivity analysis over a configurable
  assumed-depth profile; output is explicitly labelled `assumed_depth = true`.

### FR-5 · Ranking
- FR-5.1 `OpportunityRanker` adds features: leg-liquidity asymmetry, offshore staleness
  penalty (scaled by `staleness_ms`), session-liquidity weight.

### FR-6 · Transport
- FR-6.1 `arbex.raw_opps` becomes a Redis **Stream** (`XADD`), consumed via a consumer
  group. The schema is frozen (see §7) and shared with the semantic-engine owner.
- FR-6.2 A replay test demonstrates that a slow consumer does not drop events.

### FR-7 · Measurement
- FR-7.1 `MetricsCollector` records, per run: detection latency
  (`detect_ts − quote_ts`), precision & recall vs. the baseline detector (§8), basis
  magnitude and duration distributions, opportunities per session, feasible fraction after
  the execution filter, and notional PnL-after-cost.
- FR-7.2 All raw ticks, detected events and derived metrics are written to `results/` in
  JSONL, partitioned by run id; every figure regenerates from `results/` with one command.

## 5. Data-source specs

| Leg | Provider (default) | Protocol | Instrument | Depth | Freshness | Auth | Cost |
|---|---|---|---|---|---|---|---|
| Onshore | **Dhan** (`DhanDataSource`) | WebSocket (`dhanhq` marketfeed) | NSE USD/INR `FUTCUR` near-month | 5×5, up to 200-level | sub-second | `DHAN_CLIENT_ID` + `DHAN_ACCESS_TOKEN` | **free**, keep account unfunded |
| Offshore | CME delayed (or SGX) | REST poll | USD/INR future (6R) | top-of-book | ~10 min delayed | key or none | free tier |
| OTC spot | Dukascopy JForex demo / Twelve Data | WS / REST | USD/INR spot | none (synthesised) | seconds | demo login / free key | free tier |
| Reference | RBI / FBIL | REST / CSV | USD/INR fix | n/a | daily | none | free |

**Onshore broker note.** Zerodha Kite Connect costs ₹500/month; **Dhan** and **Upstox**
APIs are free. The onshore leg is `DhanDataSource` (`dhan_data_source.py`) — read-only
(market feed + depth, **no order calls anywhere**), lazy `dhanhq` import, resolves the
near-month contract from Dhan's public scrip master (`dhan_instruments.py`), with a
`DHAN_USDINR_SECURITY_ID` env override if the master snapshot is stale. Validate the
setup with `python research/check_dhan.py`. A `KiteDataSource` skeleton also exists.
Credentials in `.env`, never committed; keep the account unfunded.

## 6. Config additions (`backend/config.py`)

```python
class DataMode(str, Enum):
    ...
    LIVE_USDINR_BASIS = "LIVE_USDINR_BASIS"

CARRY_RATE_ANNUAL: float = 0.045    # INR-USD rate differential; stated assumption (§3.3)

@dataclass
class BasisDetectionConfig:
    comparison_basis: str = "futures"           # Option A (decided). "implied_spot" = dashboard only
    carry_rate_annual: float = CARRY_RATE_ANNUAL
    basis_window_ms: int = 2000
    min_basis_threshold_pips: float = 2.0
    enabled_leg_pairs: tuple = ("onshore_offshore", "onshore_otc", "offshore_otc")
    reference_band_pips: float = 25.0           # normalised forward vs RBI reference @ T*
    persistence_ephemeral_max_s: float = 5.0
    persistence_flickering_max_s: float = 60.0

@dataclass
class OffshoreFeasibilityConfig:
    assumed_depth_levels: list = ...            # (price_offset_pips, qty) profile
```

## 7. Redis Streams schema — `arbex.raw_opps`  *(frozen)*

**Machine-checkable version: `backend/core/basis/basis_event.py` (`BasisEvent`,
`SCHEMA_VERSION = "1.0"`).** Published via `redis_client.xadd(RAW_STREAM, ev.to_stream_fields())`
as one JSON payload under a versioned key `{schema, payload}`. Consumers use
`BasisEvent.from_stream_fields()`. Any field/type change bumps `SCHEMA_VERSION` and
is coordinated with the semantic-engine owner.


```json
{
  "event_id": "uuid",
  "ts": 1730000000.123,                // detection time, epoch seconds
  "symbol": "USDINR",
  "leg_pair": "onshore_offshore",
  "buy_leg": "onshore", "sell_leg": "offshore",
  "buy_source_id": "kite_usdinr_fut",
  "sell_source_id": "cme_usdinr_fut",
  "basis_pips": 4.7,
  "comparison_basis": "futures",
  "target_expiry": "2026-09-29",
  "carry_adjustment_pips": 2.1,
  "buy_quote_ts": 1730000000.050,
  "sell_quote_ts": 1730000000.061,
  "offshore_staleness_ms": 540000,
  "persistence_class": "flickering",
  "execution_verdict": "Risky",
  "expected_slippage_pips": 1.9,
  "assumed_depth": true,
  "raw_score": 4.7
}
```

`arbex.scored_opps` (produced by the semantic engine, consumed by the WS bridge) is
unchanged except that it echoes `event_id` and adds the semantic fields — that contract
belongs to the semantic owner.

## 8. Ground truth & baseline

- **Synthetic runs:** injected opportunities from `config.py` are the ground truth.
- **Real runs:** a `ReferenceBasisDetector` — strict, no ranking, no filter: flags every
  aligned instant where the normalised-mid basis exceeds `min_basis_threshold_pips`. Arbex's
  full pipeline is scored (precision/recall, latency) against this baseline, and against an
  **alignment-off** ablation to demonstrate phantom suppression.

## 9. Metrics — exact definitions

| Metric | Definition |
|---|---|
| Detection latency | `detect_ts − max(buy_quote_ts, sell_quote_ts)` |
| Precision | `TP / (TP + FP)` vs. `ReferenceBasisDetector` events within a matching window |
| Recall | `TP / (TP + FN)` |
| Basis magnitude | distribution of `basis_pips` over all events, by leg pair and session |
| Basis duration | time from first detection to basis re-converging below threshold |
| Feasible fraction | share of events with `execution_verdict ∈ {Strong, Risky}` |
| PnL-after-cost | `basis_pips − expected_slippage_pips − assumed_fees_pips`, notional, per event |

## 10. Out of scope

- Live order execution / real capital.
- Real-time offshore data (delayed is acceptable — the regime is minute-scale).
- Direct NDF tick data (no free source; proxied via offshore futures, stated as a limitation).
- Triangular arbitrage (single pair).
- Any change to the semantic engine beyond consuming the frozen §7 schema.
- 24-hour continuous basis tracking (onshore hours ~09:00–17:00 IST).

## 11. Acceptance criteria

1. `DATA_MODE = LIVE_USDINR_BASIS` starts the server with three real legs streaming and
   recording for a full onshore session; each leg's normalised forward `F*` (Option A,
   §3.1) stays within `reference_band_pips` of the RBI reference carried to `T*`, and
   `carry_adjustment_pips` is recorded per event.
2. `BasisArbitrageEngine` emits classified dislocation events with feasibility verdicts on
   real data.
3. `arbex.raw_opps` Streams replay test: 0 dropped events with a deliberately slow consumer.
4. `results/` regenerates every figure and headline number from archived data with one command.
5. Alignment-on vs. alignment-off ablation shows a measurable drop in false positives.
6. Calibration report quantifies the synthetic-vs-real fidelity gap.
7. `python -m pytest tests/` passes, including new `test_instrument_normalizer.py` and
   `test_basis_engine.py`.
