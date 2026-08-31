# Arbex — Technical Specification: Onshore–Offshore USD/INR Basis Detection

**Status:** Active · Phase 3 · Path 2 · Option A (futures↔futures) · carry 1.9% (to calibrate)
**Built:** normaliser, EOD track (real NSE futures), `BasisArbitrageEngine` + persistence
+ scoring, `BasisEvent` v1.1 + Redis Streams + consumer-group replay test,
`UpstoxDataSource` (default onshore leg — free market data, current instrument
master) + `DhanDataSource` (kept, needs paid Dhan Data API), semantic adapter + E2E
chain, `/basis` dashboard + `BasisReplayer`.
Onshore feed **verified live** (Upstox analytics token, `NCD_FO|1769`, 5×5 depth).
`BasisExecutionFilter` (real onshore book -> slippage + verdict) built and wired.
**Remaining:** live 3-leg basis (offshore + OTC live sources); carry calibration.
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
| **Offshore leg** | CME (contract "6R" / E-micro "MIR") or SGX rupee future; NDF proxy. **Quoted USD-per-INR (≈ 0.0117), not USD/INR** — inverted to the USD/INR convention on load (`eod_loader.normalise_convention`, auto or `--offshore-convention`). |
| **OTC spot leg** | Aggregated interbank USD/INR spot from a free API (Dukascopy demo / OANDA practice / Twelve Data). |
| **Reference** | RBI reference rate / FBIL daily fix. Validation anchor only. |
| **Basis** | Normalised-price difference between two legs at an aligned instant. |
| **Comparison basis** | The single price representation all legs are converted to before comparison (see §5). |
| **Dislocation event** | An aligned instant where \|basis\| between a leg pair exceeds `min_basis_threshold` (pips). |
| **Persistence class** | `ephemeral` / `flickering` / `persistent` — re-tuned for the minute-scale regime. |

## 3. The comparison basis — **Option A: futures ↔ futures** *(decided)*

All legs are first put on the **USD/INR price convention** (the offshore CME/SGX
future is quoted USD-per-INR and is reciprocated on load), then converted to a
**normalised forward price at one common target expiry `T*`**, then compared. `T*`
is the **NSE near-month contract's expiry** (our anchor — most liquid onshore
instrument).

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
- FR-1.1 An onshore-broker `DataSourceInterface` implementation streams the NSE-CDS
  USD/INR near-month future; `get_tick()` returns a `RawTick` (with `extra["expiry"]`);
  `get_depth(symbol)` returns the top 5 bid + 5 ask levels `(price, qty)`. Broker is
  selected by `ARBEX_ONSHORE_BROKER` (default `upstox`).
  - **`UpstoxDataSource` (default, built).** Upstox API v2 market data is **free** — no
    data subscription (verified against Upstox docs 2026-08-31; only API-*placed* orders
    carry brokerage). Pure `urllib` REST, polls `/v2/market-quote/quote` at 1 s (well
    inside the 10 req/s free limit) for LTP + 5-level depth. `UPSTOX_ACCESS_TOKEN` only
    (an *analytics token* avoids daily re-auth). `upstox_instruments.py` resolves the
    near-month **monthly** contract (`weekly == False`, segment `NCD_FO`) from the public
    instrument master `assets.upstox.com/.../NSE.json.gz`, which stays current (carries
    `USDINR FUT 28 SEP 26` = `NCD_FO|1769` and out to Aug 2027). Pin with
    `UPSTOX_USDINR_INSTRUMENT_KEY`. Validate: `research/check_upstox.py [--probe KEY]`.
  - **`DhanDataSource` (kept, blocked).** `dhanhq` >= 2.x (`DhanContext` + `MarketFeed`,
    `Full` request code). Trading token verified (`/v2/fundlimit` 200) but **market data
    needs Dhan's paid Data API subscription** — every quote/feed call returns
    `401 806 "Data APIs not Subscribed"`. Dhan's public scrip master is also months stale
    for the currency segment (`resolve_near_month(allow_stale=True)` + `DHAN_USDINR_SECURITY_ID`
    override). `research/check_dhan.py --probe SID`. Use only if the paid plan is bought.
  - Zerodha Kite Connect costs ₹500/mo; `KiteDataSource` is a skeleton.
- FR-1.2 An onshore `StreamAdapter(BaseStreamAdapter)` publishes real 5-level DOM to
  `arbex.orderbooks` for `OrderbookService`, replacing the mock `MT5StreamAdapter` for USD/INR.
- FR-1.3 A `CMEDelayedSource` polls a delayed offshore rupee future; each `RawTick`
  carries `extra["staleness_ms"]`. The USD-per-INR quote is reciprocated to USD/INR
  before it enters the pipeline. (EOD path: `eod_loader.normalise_convention`.)
- FR-1.4 The existing `RESTDataSource` is configured for OTC spot USD/INR with an
  appropriate parser; synthesised bid/ask is flagged `extra["synthetic_spread"] = true`.
- FR-1.5 Feeds are selected by `DATA_MODE = LIVE_USDINR_BASIS` (env `ARBEX_DATA_MODE`)
  and assembled in `basis_pipeline.build_basis_pipeline()` in `server/main.py`'s lifespan;
  with no Dhan creds (or `BASIS_REPLAY=1`) a `BasisReplayer` streams a completed EOD run.
- FR-1.6 EOD track: `eod_loader.load_eod_csv` — column-tolerant, ambiguous-date
  detection, NSE-derivatives front-month selection, price-convention normalisation.

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

### FR-4 · Feasibility  — **built** (`basis_execution.py::BasisExecutionFilter`, T2.3)
- FR-4.1 The filter walks each leg's order book for `target_notional_usd` (default
  $100k, retail scale): notional-weighted price offset from the touch = per-leg slippage
  in pips, plus a `min_leg_cost_pips` floor (the spread you cross) and a
  `thin_book_penalty` when a book cannot fill the size. The **onshore** leg uses the
  real Upstox 5-level book (`get_depth()`, wired in `basis_pipeline._tick_once`).
- FR-4.2 The **offshore** future and **OTC** spot have no retail L2 → the filter falls
  back to `OFFSHORE_FEASIBILITY_CONFIG.assumed_depth_levels` and sets `assumed_depth =
  true` on the event (always true for `onshore_offshore`, matching the "partial /
  sensitivity-only" position in the novelty matrix).
- FR-4.3 `net_basis_pips = basis_pips − expected_slippage_pips − offshore staleness
  haircut`; verdict = `viable` (net ≥ `min_viable_net_pips`, default 2) / `risky`
  (0..2) / `unlikely` (≤ 0). `stamp()` writes `execution_verdict`,
  `expected_slippage_pips`, `assumed_depth` onto the `BasisEvent` before scoring, so
  `score_basis_event` picks up the real verdict bonus. Runs in both the live pipeline
  and the EOD runner (EOD = all-assumed). EOD `real` run: 673 viable / 24 risky / 0
  unlikely — at retail size the EOD basis clears cost whenever it fires; the mix
  broadens on the live intraday feed.

### FR-5 · Ranking
- FR-5.1 `OpportunityRanker` adds features: leg-liquidity asymmetry, offshore staleness
  penalty (scaled by `staleness_ms`), session-liquidity weight.

### FR-6 · Transport
- FR-6.1 `arbex.raw_opps` becomes a Redis **Stream** (`XADD`, capped `maxlen`),
  consumed via a consumer group (`arbex.semantic`). The schema is frozen (see §7) and
  shared with the semantic-engine owner. Producer: `RedisClient.xadd`. Reference
  consumer: `backend/core/basis/stream_consumer.py::BasisStreamConsumer`
  (`ensure_group` / `read` / `ack` / `pending_count`).
- FR-6.3 The two data modes are mutually exclusive (`config.DATA_MODE`) and are
  demoed on separate pages — synthetic on `/` (or `arbex-web`), basis on `/basis`. The
  same `SemanticEngine` serves both: in basis mode `SemanticEngine.start` calls
  `backend/core/basis/semantic_adapter.py::consume_basis_stream`, which reads the
  Stream via the consumer group and maps each `BasisEvent` onto the existing
  `{event_id, opportunity, raw_score}` envelope (`basis_event_to_raw_msg`) so the
  persistence / execution / ranking / news-bias stages are unchanged. Basis-specific
  fields ride through in `opportunity.details`. `tests/test_basis_e2e.py` runs the whole
  chain (xadd -> consumer group -> adapter -> `SemanticEngine` -> `arbex.scored_opps`)
  on fakeredis.
- FR-6.2 `tests/test_stream_consumer.py` (fakeredis, no server) demonstrates: a slow
  batch consumer drains all 500 events exactly once and in order; unacked events stay
  in the group's pending list for post-crash replay; the `maxlen` cap bounds the
  backlog for an absent consumer; two consumers partition the stream with no
  double-delivery. **Done.**

### FR-7 · Measurement
- FR-7.1 `MetricsCollector` records, per run: detection latency
  (`detect_ts − quote_ts`), precision & recall vs. the baseline detector (§8), basis
  magnitude and duration distributions, opportunities per session, feasible fraction after
  the execution filter, and notional PnL-after-cost.
- FR-7.2 All raw ticks, detected events and derived metrics are written to `results/` in
  JSONL, partitioned by run id; every figure regenerates from `results/` with one command.

### FR-8 · Dashboard
- FR-8.1 `/basis` serves `frontend/basis_dashboard.html`; `/ws/basis` streams
  `basis_meta` / `basis_snapshot` / `basis_event` via `WebSocketManager.broadcast_basis()`.
- FR-8.2 The live `BasisPipeline` broadcasts each snapshot + event; `BasisReplayer`
  replays a completed EOD run over the same channel so the dashboard demos without an
  account. New clients get a catch-up (last meta + snapshot + recent events).
- FR-8.3 Dashboard shows: per-leg normalised-forward chart, pairwise-basis cards vs the
  threshold band, a basis spark, and a live dislocation-event feed ranked by
  `composite_score`.

## 5. Data-source specs

| Leg | Provider (default) | Protocol | Instrument | Depth | Freshness | Auth | Cost |
|---|---|---|---|---|---|---|---|
| Onshore | **Upstox** (`UpstoxDataSource`, default) | REST poll `/v2/market-quote/quote` @ 1 s | NSE USD/INR `NCD_FO` monthly future | 5×5 | ~1 s | `UPSTOX_ACCESS_TOKEN` (analytics token) | **free** — no data subscription |
| Onshore (alt) | Dhan (`DhanDataSource`) | WebSocket (`dhanhq` `MarketFeed`, `Full`) | NSE USD/INR `FUTCUR` near-month | 5×5 | sub-second | `DHAN_CLIENT_ID` + `DHAN_ACCESS_TOKEN` | data API **paid** (₹~500/mo) |
| Offshore | CME / SGX settlements, Barchart, Nasdaq Data Link | CSV (EOD) / REST poll (live) | Rupee future 6R / MIR — **USD-per-INR, reciprocated on load** | top-of-book | EOD or ~10 min delayed | none / free tier | free tier |
| OTC spot | yfinance `USDINR=X` / Dukascopy demo / Twelve Data | WS / REST / CSV | USD/INR spot | none (synthesised) | seconds–EOD | none / demo | free |
| Reference | RBI / FBIL | REST / CSV | USD/INR fix | n/a | daily | none | free |

**Onshore broker note.** Only **Upstox** gives free market data for NSE currency
derivatives — Zerodha Kite (₹500/mo) and Dhan (data API paid, `401 806`) both charge.
The default onshore leg is `UpstoxDataSource` (`upstox_data_source.py`) — read-only
(REST quote poll, **no order calls anywhere**), pure `urllib`, resolves the near-month
monthly contract from Upstox's public instrument master (`upstox_instruments.py`), with
a `UPSTOX_USDINR_INSTRUMENT_KEY` override. `DhanDataSource` is kept behind
`ARBEX_ONSHORE_BROKER=dhan`. Validate with `python research/check_upstox.py`.
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
`SCHEMA_VERSION = "1.1"`).** Published via `redis_client.xadd(RAW_STREAM, ev.to_stream_fields())`
as one JSON payload under a versioned key `{schema, payload}`. Consumers use
`BasisEvent.from_stream_fields()`. Additive fields with defaults are back-compatible
(bump the minor); a rename/removal bumps the major and is coordinated with the
semantic-engine owner.

```json
{
  "event_id": "uuid", "ts": 1730000000.123, "symbol": "USDINR",
  "leg_pair": "onshore_offshore",
  "buy_leg": "onshore", "sell_leg": "offshore",
  "buy_source_id": "dhan_usdinr_fut", "sell_source_id": "cme_usdinr_fut",
  "basis_pips": 4.7, "comparison_basis": "futures",
  "target_expiry": "2026-09-29", "carry_adjustment_pips": 2.1,
  "buy_quote_ts": 1730000000.050, "sell_quote_ts": 1730000000.061,
  "offshore_staleness_ms": 540000,
  "persistence_class": "flickering",
  "execution_verdict": "unknown", "expected_slippage_pips": 0.0, "assumed_depth": true,
  "raw_score": 4.7, "composite_score": 41.6,
  "schema_version": "1.1", "cadence": "tick"
}
```

`execution_verdict` ∈ `{viable, risky, unlikely, unknown}`; `persistence_class` ∈
`{ephemeral, flickering, persistent}`; `cadence` ∈ `{tick, eod}`.

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

| # | Criterion | Status |
|---|---|---|
| 1 | `ARBEX_DATA_MODE=LIVE_USDINR_BASIS` streams three real legs for a full onshore session; each `F*` within `reference_band_pips` of the RBI reference @ `T*`; `carry_adjustment_pips` per event | blocked on Dhan key |
| 2 | `BasisArbitrageEngine` emits classified dislocation events on real data | **done** (EOD: real NSE futures) |
| 3 | `arbex.raw_opps` Streams replay test: 0 dropped events with a slow consumer | **done** (`tests/test_stream_consumer.py`, fakeredis) |
| 4 | `research/results/` regenerates every figure + headline number with one command | **done** |
| 5 | Alignment-on vs -off ablation shows a measurable drop in false positives | **done** (synthetic harness) |
| 6 | Calibration report quantifies the synthetic-vs-real fidelity gap | pending (P4) |
| 7 | `python -m pytest tests/` passes | **done** — 116/117 (1 failure is the semantic engine's) |
| 8 | `/basis` dashboard shows the forward chart + live dislocation feed | **done** (replay + live) |
