# Arbex — README_3 (Phase 3: the live USD/INR basis system)

**Team PW26_CHB_02 · "Efficient Detection of Arbitrage Opportunities in FX" · Guide: Dr. Chaitra B**

This document describes the system **as it stands now** (Sept 2026), after the Phase‑3
pivot.

- `README.md` / `README_2.md` describe the **original synthetic** cross‑provider system — still valid, still runnable, unchanged.
- **This file (`README_3.md`)** describes the **Phase‑3 live basis system** layered on top.
- `docs/SPEC.md` is the formal spec; `md/PHASE3_PLAN.md` is the team plan / roadmap.

---

## 1. What changed in Phase 3, in one paragraph

The original thesis — "detect microsecond price differences of USD/INR between two data
providers" — turned out to be mostly **feed‑latency phantom arbitrage**: the gaps are
artifacts of one feed updating before another, not money you can capture without
co‑location. So the project pivoted to a **real, documented dislocation**: the
**onshore–offshore USD/INR basis**. The onshore price (NSE currency futures) and the
offshore price (CME rupee futures / NDF) and OTC spot disagree by a **minute‑to‑hour
scale** margin because of capital controls, RBI intervention, and balance‑sheet
frictions. That basis is *structural*, detectable with a **1‑second feed**, and
measurable with **free, retail‑accessible data**. The deliverable is an **empirical
research paper**; a public dashboard is secondary; the patent idea is dropped.

The engine is now **one pipeline, two modes** (see §3).

---

## 2. Current status

| Area | Status |
|---|---|
| Synthetic cross‑provider system (`README_2`) | ✅ untouched, still runs |
| Option‑A instrument normaliser (carry every leg to a common expiry) | ✅ built + tested |
| Basis detection engine + persistence classifier | ✅ built + tested |
| Execution‑feasibility filter (does the edge survive the spread?) | ✅ built + tested |
| **EOD basis** (daily settlements → basis + events, zero‑KYC) | ✅ done, real NSE data |
| **Live research‑basis mode** (onshore + offshore + OTC, 1 s feed) | ✅ runs end‑to‑end |
| **Live retail‑arb mode** (NSE future vs options‑implied vs calendar) | ✅ runs end‑to‑end |
| Onshore live feed — **Upstox** (free) | ✅ verified live 2026‑08‑31 |
| Offshore live feed — Yahoo `SIR=F` (free, delayed) | ⚠️ works but thin/stale (see §11) |
| OTC spot — Yahoo `USDINR=X` + Frankfurter fallback (free) | ✅ works |
| Data‑driven basis dashboard (`/basis`) | ✅ works, live + replay |
| Semantic engine adapter (keeps both modes working) | ✅ built + tested |
| Always‑on intraday collector | ✅ built, **not yet started collecting** |
| Multi‑week collection + RBI‑event study (the paper's core result) | ⏳ not started — calendar‑bound, needs to start ~late Sept |
| `CARRY_RATE_ANNUAL` calibration from the real forward curve | ❌ still a stated assumption (1.9%) |

Tests: `python -m pytest tests/` → **156 pass, 1 fail**. The one failure
(`test_semantic_engine.py::test_process_raw_opp_publishes_neutral_fallback...`) is the
semantic owner's pre‑existing failure, unrelated to Phase‑3 work.

---

## 3. One engine, two modes

The pipeline is **leg‑agnostic**. A "leg" is any priced instrument with an expiry. The
normaliser carries every leg to a common target expiry `T*`, the engine compares the
resulting forward prices pairwise, the persistence tracker classifies how long each gap
lasts, and the execution filter checks whether it survives trading costs. **Swapping the
set of legs is the only difference between the two modes.**

```
                       ┌─────────────────────────────────────────────────┐
  leg sources  ──────▶ │ InstrumentNormalizer  (carry all legs to  T*)    │
  (DataSourceInterface)│ BasisArbitrageEngine  (pairwise basis, threshold)│
                       │ BasisPersistenceTracker (ephemeral/flickering/…) │
                       │ BasisExecutionFilter  (net-of-cost verdict)      │
                       └───────────────┬─────────────────────────────────┘
                                       │ BasisEvent (frozen schema, SCHEMA_VERSION 1.2)
                        ┌──────────────┴───────────────┐
                        ▼                              ▼
              Redis stream arbex.raw_opps      WebSocket /ws/basis  ──▶  /basis dashboard
                        │
                        ▼  (semantic teammate's engine, separate process)
              arbex.scored_opps  ──▶  news-conditioned score
```

### Mode A — research‑basis  (`build_basis_pipeline()`, the default in basis mode)

| leg | instrument | source | free? |
|---|---|---|---|
| `onshore` | NSE USD/INR near‑month future + 5‑level depth | **Upstox** (`UpstoxDataSource`) | ✅ |
| `offshore` | CME Indian Rupee future (`SIR=F`), quoted `10000 / USDINR` | Yahoo (`_yahoo_offshore_fetcher`) | ✅ but delayed |
| `otc` | aggregated USD/INR spot | Yahoo `USDINR=X`, Frankfurter fallback (`OtcSpotSource`) | ✅ |

Pairs detected: `onshore_offshore`, `onshore_otc`, `offshore_otc`.
This is the **paper's subject**. Headline number: the onshore−OTC basis (continuous),
with onshore−offshore for EOD where staleness does not matter.

### Mode B — retail‑arb  (`build_retail_arb_pipeline()`, `ARBEX_RETAIL_ARB=1`)

| leg | instrument | source |
|---|---|---|
| `future` | NSE USD/INR near‑month future | `UpstoxDataSource(month_offset=0)` |
| `options` | forward implied from NSE USD/INR options via put‑call parity | `UpstoxOptionsSource` |
| `far` | NSE USD/INR **next**‑month future, carried back to `T*` | `UpstoxDataSource(month_offset=1)` |

Pairs detected: `future_options` (box / conversion‑reversal arb), `future_far` (calendar
spread). All legs are on **one exchange**, so this is genuinely retail‑executable — but
the edge is small (a few paise) and the current output is dominated by two modelling
artifacts (see §11). This mode recovers the original "retail trader detects + executes
himself" vision as a **secondary** contribution.

Both modes still coexist with the untouched **synthetic** mode
(`DataMode.SYNTHETIC_USDINR_ONLY`, the default when no env var is set).

---

## 4. The maths

### 4.1 Option A — carry every leg to a common forward (`InstrumentNormalizer`)

Every leg is a forward/future for *some* delivery date. To compare them you must put
them on the same date. We pick `T*` = the NSE near‑month contract's expiry and carry
each leg's price to `T*` using a constant annualised carry rate:

```
F(T*) = F(t_leg) · [ 1 + CARRY_RATE_ANNUAL · (T* − t_leg) / 365 ]
```

- A leg already at `T*` (the onshore near future) is unchanged.
- The far (Oct) future is discounted back ~1 month.
- Spot is carried forward to `T*`.

`CARRY_RATE_ANNUAL = 0.019` is a **stated assumption**, printed in every output. It is
*not yet calibrated* — see §11. `basis_pips = (F_leg_A(T*) − F_leg_B(T*)) / 0.01`.
`implied_spot` (Option B — back out spot from each future) is kept **dashboard‑only**.

### 4.2 Options‑implied forward — put‑call parity (`normalization/options_forward.py`)

For retail‑arb mode we need a "price" from the options market. Put‑call parity gives a
synthetic forward from a call and put at the same strike `K`:

```
F(T) = K + (C − P) / DF(T)       DF(T) = 1 / (1 + r · days/365)
```

`r = OPTIONS_DISCOUNT_RATE_ANNUAL = 0.065` (stated assumption, ~1‑month INR rate).
`implied_forward_from_chain()` averages the `n` strikes nearest the ATM future to reduce
single‑strike noise. Bid/ask are kept separate so the dashboard can show how wide the
synthetic forward is (currently very wide — the options barely trade).

### 4.3 Persistence classes (`BasisPersistenceTracker`)

A gap is only interesting if it lasts. Each detected basis is tracked and labelled:

- `ephemeral` — gone within `persistence_ephemeral_max_s` (5 s research / 8 s retail). Noise.
- `flickering` — on/off within `persistence_flickering_max_s` (60 s / 90 s).
- `persistent` — sustained beyond that. **This is the research signal.**

Two modes: `"duration"` (how long has this specific gap been open) and `"count"` (how
often does this leg‑pair dislocate over a window).

### 4.4 Execution feasibility (`BasisExecutionFilter`, `config.BasisExecutionConfig`)

A basis you cannot trade after costs is not an opportunity. For a target notional
(`target_notional_usd = 100_000`) the filter walks each leg's order book (real L2 for
onshore; an assumed depth profile, `OFFSHORE_FEASIBILITY_CONFIG`, for legs without L2),
computes notional‑weighted slippage, applies a `min_leg_cost_pips = 0.5` floor per leg
(the spread you always cross), and an offshore **staleness haircut**
(`0.5 pip / minute` of age). Then:

```
net_pips = basis_pips − Σ slippage_pips − staleness_haircut_pips
net ≥ min_viable_net_pips (2.0)  →  "viable"
0 … 2                            →  "risky"
≤ 0                              →  "unlikely"
```

The verdict, expected slippage, and assumed‑depth flag are stamped onto the `BasisEvent`
before it is published.

---

## 5. Data sources — what is free and what is not

This was the single biggest research question of Phase 3. **Verified 2026‑08‑31 against
live accounts / official docs:**

| Provider | Onshore data | Cost | Verdict |
|---|---|---|---|
| **Upstox** | NSE currency futures + options, quotes + 5×5 depth, WebSocket, historical | **₹0** — "all trading + data APIs free" (official trading‑api page) | ✅ **default onshore leg** |
| Dhan | same | trading APIs free; **market‑data API is a paid add‑on** (~₹500/mo). `/v2/marketfeed/quote` → `401 {"806":"Data APIs not Subscribed"}` | ❌ blocked behind paywall; kept as `ARBEX_ONSHORE_BROKER=dhan` fallback |
| Zerodha Kite | same | ₹500/month | ❌ not used |
| Yahoo Finance (unofficial) | `USDINR=X` spot (~1 min), `SIR=F` CME rupee future | free, no key | ✅ used for OTC + offshore; needs a browser User‑Agent |
| Frankfurter (`api.frankfurter.dev`) | ECB daily USD/INR | free, no key | ✅ OTC‑spot fallback only |
| NSE / BSE / yfinance EOD CSV | daily settlement prices | free | ✅ EOD basis track |

**Upstox gotchas that cost us iterations (all handled in code now):**

1. `api.upstox.com` sits behind Cloudflare and **1010‑blocks Python's default
   User‑Agent** — every request sends a browser UA (`_UA` constant).
2. The full‑quote endpoint is `/v2/market-quote/quotes` — **plural**. `/quote` returns
   `UDAPI100012 "Invalid Endpoint"`.
3. Use an **Analytics Token** (generated once on the Upstox developer console, ~1‑year
   life) — the standard OAuth access token expires ~03:30 IST daily.
4. NSE currency derivatives are segment **`NCD_FO`** in the instrument master
   (`https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz`, kept
   current, unlike Dhan's frozen scrip master).
5. `/v2/user/profile` needs a static IP; market‑data GETs do not. So the **quote call
   itself is the health check** (`research/check_upstox.py`).

**Yahoo `SIR=F` conversion:** CME quotes the Indian Rupee future as USD per INR ×
sensible scaling; we normalise with `10000 / raw` to get an INR‑per‑USD price
(`normalise_convention` → `INRUSD_x10000` in `eod_loader.py`;
`_yahoo_offshore_fetcher` for live).

---

## 6. New / changed files 

Everything below is Phase‑3. **The semantic engine is off‑limits** (teammate‑owned) —
the *only* change there is a sanctioned 6‑line branch in `semantic_engine.py::start()`
that the user explicitly authorised.

### `backend/core/normalization/`
| file | role |
|---|---|
| `instrument_normalizer.py` | Option‑A carry maths. `to_common_forward()` handles `instrument_kind` `future` and `options_forward`. |
| `options_forward.py` | put‑call‑parity synthetic forward; `implied_forward_from_chain()`. |

### `backend/core/basis/`
| file | role |
|---|---|
| `basis_event.py` | **frozen** `BasisEvent` stream schema. `SCHEMA_VERSION = "1.2"`, `LEG_PAIRS` now includes `future_options`, `future_far` (additive, back‑compatible). |
| `basis_engine.py` | `BasisArbitrageEngine` (pairwise dislocation detection) + `BasisPersistenceTracker`. `_PAIR_LEGS` maps each pair name to its two legs. |
| `basis_execution.py` | `BasisExecutionFilter` + `BasisExecutionAssessment` (see §4.4). |
| `eod_basis.py` | `EODBasisRunner` — daily settlement CSV → same Option‑A maths → basis + events. Zero KYC. Runner: `research/run_eod_basis.py`. |
| `basis_replay.py` | `BasisReplayer` — streams a completed EOD run over `/ws/basis` so the dashboard demos with no account. |
| `stream_consumer.py` | `BasisStreamConsumer` — Redis consumer‑group reader for `arbex.raw_opps` (so a slow semantic consumer cannot drop events). |
| `semantic_adapter.py` | `basis_event_to_raw_msg()` maps a `BasisEvent` onto the `ArbitrageOpportunity`‑shaped message the semantic engine already expects; `consume_basis_stream()` is the async loop. **This is what keeps the semantic engine working in both modes** — see §8. |

### `backend/core/data_sources/`
| file | role |
|---|---|
| `upstox_data_source.py` | `UpstoxDataSource(leg=…, month_offset=…)` — NSE future quotes + depth, urllib + poller thread. |
| `upstox_instruments.py` | scrip‑master resolver: `resolve_near_month(nth=…)` for futures, `usdinr_options(expiry)` for the option chain. `NCD_FO`, 12 h cache. |
| `upstox_options_source.py` | `UpstoxOptionsSource` — resolves the ATM strike band, batch‑quotes calls+puts, returns an options‑implied forward tick (`leg="options"`, `instrument_kind="options_forward"`). |
| `otc_spot_source.py` | `OtcSpotSource` — Yahoo `USDINR=X` primary, Frankfurter fallback; synthesises a bid/ask from mid ± `OTC_SPOT_SPREAD_PIPS/2`. |
| `dhan_data_source.py`, `dhan_instruments.py` | onshore leg via Dhan — **built but blocked** on Dhan's paid data plan; kept behind `ARBEX_ONSHORE_BROKER=dhan`. |
| `cme_delayed_source.py` | polled offshore adapter skeleton. |
| `eod_loader.py` | column‑tolerant CSV / yfinance loader, NSE front‑month selection, HTML‑response guard, `INRUSD_x10000` convention. |
| `basis_pipeline.py` | **assembles it all.** `build_basis_pipeline()` (research), `build_retail_arb_pipeline()` (retail), `_tick_once()` (one poll → normalise → detect → stamp → broadcast + Redis), `_yahoo_offshore_fetcher()`, `_build_onshore_source()`, `_build_otc_source()`. Writes `basis_<day>.jsonl` + `basis_events_<day>.jsonl` (research) / `retail_*.jsonl` (retail). |

### `backend/`
| file | Phase‑3 change |
|---|---|
| `config.py` | `DataMode.LIVE_USDINR_BASIS`, env override `ARBEX_DATA_MODE`; `CARRY_RATE_ANNUAL`, `OPTIONS_DISCOUNT_RATE_ANNUAL`; `BASIS_LEGS`, `BasisDetectionConfig` / `BASIS_DETECTION_CONFIG` / `RETAIL_ARB_CONFIG`; `OffshoreFeasibilityConfig`, `BasisExecutionConfig`; helpers `is_basis_mode()`. |
| `core/redis_client.py` | `xadd()` for the stream; **circuit breaker** — `ARBEX_REDIS=off` skips Redis entirely; after 3 failed connects it stops trying and stops logging (the dashboard does not need Redis). |
| `server/main.py` | basis‑mode lifespan: chooses replay vs live, retail vs research; serves `basis_dashboard.html` at `/` and `/basis`; `/ws/basis` endpoint. |
| `server/run.py` | `load_dotenv()` **before** importing `main` (config reads `os.environ` at import time). |
| `core/semantic_engine.py` | **sanctioned 6‑line branch only** (see §8). |

### `frontend/basis_dashboard.html`
Rewritten to be **data‑driven**: no hardcoded leg names. It reads the leg set and pair
set from the WebSocket snapshot, creates one line series per leg and one card per
pair on first sight, and sets its own title from the `track` field (`retail` →
"Retail‑Arbitrage", else "Onshore–Offshore Basis").

### `research/`
| file | role |
|---|---|
| `check_upstox.py` | Upstox token + data‑access health check. `--probe <instrument_key>`. |
| `check_dhan.py` | Dhan token health check + `--probe <security_id>` (this is how we found Dhan's data API is paid). |
| `collect_basis.py` | **the always‑on collector** — see §9. |
| `run_eod_basis.py` | EOD basis runner; `--offshore-convention INRUSD_x10000`. |

### `tests/` (new)
`test_basis_e2e.py`, `test_basis_engine.py`, `test_basis_event.py`,
`test_basis_execution.py`, `test_instrument_normalizer.py`, `test_options_forward.py`,
`test_otc_spot_source.py`, `test_retail_arb.py`, `test_semantic_adapter.py`,
`test_stream_consumer.py`, `test_upstox_instruments.py`, `test_eod_basis.py`,
`test_eod_loader.py`, `test_dhan_instruments.py`. Plus `conftest.py` (imports
`fakeredis` first so it does not clash with the semantic test's `redis` stub).

---

## 7. Configuration reference

### Environment variables

| var | values | effect |
|---|---|---|
| `ARBEX_DATA_MODE` | `SYNTHETIC_USDINR_ONLY` (default), `LIVE_USDINR_BASIS`, `LIVE_MT5` | picks the whole pipeline |
| `ARBEX_ONSHORE_BROKER` | `upstox` (default), `dhan` | which onshore feed to build |
| `ARBEX_RETAIL_ARB` | `1` / unset | in basis mode, use retail‑arb legs instead of research legs |
| `BASIS_REPLAY` | `1` / unset | force dashboard replay mode (also auto‑on when no onshore creds) |
| `ARBEX_OFFSHORE` | `off` / unset | disable the offshore leg (runs 2‑leg: onshore + OTC) |
| `CME_USDINR_URL` | URL | override the offshore fetch (else Yahoo `SIR=F`) |
| `ARBEX_REDIS` | `off` / unset | skip Redis entirely (dashboard still works) |
| `UPSTOX_ACCESS_TOKEN` | analytics token | onshore auth — **in `.env`, never committed** |
| `UPSTOX_USDINR_INSTRUMENT_KEY` | e.g. `NCD_FO|1769` | pin the contract if auto‑resolution is stale |
| `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN` | — | only if `ARBEX_ONSHORE_BROKER=dhan` |
| `DHAN_USDINR_SECURITY_ID` | int | pin the Dhan contract (their scrip master is stale) |
| `PYTHONHASHSEED` | `0` | reproducible research runs |

`.env` is git‑ignored and has explicit `Read`/`Edit` deny rules in
`.claude/settings.json`. **Never commit it. Keep broker accounts unfunded** — a
zero‑balance account physically cannot place a trade, which is our safety guarantee
(the system is read‑only; only `PaperBroker` is wired).

### Key config objects (`backend/config.py`)

- `CARRY_RATE_ANNUAL = 0.019` — carry for Option‑A normalisation. **Stated assumption, uncalibrated.**
- `OPTIONS_DISCOUNT_RATE_ANNUAL = 0.065` — discount rate in the put‑call‑parity forward.
- `BASIS_DETECTION_CONFIG` — research mode: `min_basis_threshold_pips=2.0`, `basis_window_ms=2000`, pairs `onshore_offshore / onshore_otc / offshore_otc`.
- `RETAIL_ARB_CONFIG` — retail mode: `min_basis_threshold_pips=1.0`, `basis_window_ms=3000`, pairs `future_options / future_far`.
- `BASIS_EXECUTION_CONFIG` — `target_notional_usd=100_000`, `contract_size_usd=1_000`, `min_leg_cost_pips=0.5`, `min_viable_net_pips=2.0`.

---

## 8. How the semantic engine keeps working (the adapter)

The semantic / sentiment engine is owned by another teammate and **must not be
modified**. It subscribes to `arbex.raw_opps` and expects an
`ArbitrageOpportunity`‑shaped message. Our `BasisEvent` is a different shape.

Solution: `backend/core/basis/semantic_adapter.py`.
`basis_event_to_raw_msg()` maps a `BasisEvent` onto that expected shape
(`type = CROSS_SOURCE`, `symbols = ["USDINR"]`, `estimated_profit_pips = basis_pips`,
`confidence_score` derived from the execution verdict + persistence class, all basis
fields tucked into `details`). `consume_basis_stream()` reads the Redis stream, adapts
each event, and calls the engine's existing `process_raw_opp`.

The only change inside `semantic_engine.py` is a **6‑line, user‑authorised** branch in
`start()`:

```python
from backend.config import is_basis_mode
if is_basis_mode():
    from backend.core.basis.semantic_adapter import consume_basis_stream
    await consume_basis_stream(self)      # basis mode → adapted stream
else:
    await redis_client.subscribe("arbex.raw_opps", self.process_raw_opp)  # synthetic → unchanged
```

So: **synthetic mode is byte‑for‑byte unchanged**; basis mode routes through the
adapter. In the paper, the semantic engine's role is reframed as **news‑conditioning
the event study** (does the basis widen more around RBI headlines?).

---

## 9. The always‑on collector (`research/collect_basis.py`)

The paper's central result needs **weeks of intraday basis data** around calendar
events (the RBI 12:30 IST fixing, MPC dates — next MPC **7 Oct 2026** — month‑end). This
is calendar‑time‑bound, so the collector must start running soon.

What it does:
- Runs `pipeline._tick_once()` every `--poll` seconds (default 2 s).
- Only during NSE currency hours (Mon–Fri 09:00–17:00 IST) unless `--force`.
- Day‑rolled output: `basis_<day>.jsonl` (every snapshot) + `basis_events_<day>.jsonl` (dislocations).
- **Event dedup** — writes an event only when the key is new, the persistence class
  changes, or the basis moves ≥ 2 pips (otherwise a static basis writes one event per tick).
- Reconnects sources after 10/30 empty ticks; `status.json` heartbeat; top‑level
  supervisor restarts on crash after 30 s.
- Output goes to `research/results/collect/` (git‑ignored).

```bash
# research legs, real hours:
python research/collect_basis.py --outdir research/results/collect
# retail-arb legs:
python research/collect_basis.py --retail --outdir research/results/collect
# quick smoke test (90 s, ignores market hours):
python research/collect_basis.py --dry-run
```

**Status: built, not yet started.** The team has open doubts to resolve first
(sampling rate, storage, whether to run research + retail collectors in parallel).

---

## 10. Running everything

### Prerequisites
```bash
python -m venv venv && venv\Scripts\activate      # Windows
pip install -r requirements.txt
# for research figures: pip install matplotlib pandas python-pptx
```

### Synthetic mode (original system — no accounts, no env)
```bash
python -m backend.server.run            # → http://localhost:8000
```

### Basis dashboard — replay (no account, real NSE EOD data)
```bash
# PowerShell:
$env:ARBEX_DATA_MODE="LIVE_USDINR_BASIS"; $env:BASIS_REPLAY="1"; python -m backend.server.run
# → http://localhost:8000/basis
```
This is **backtest‑style validation**: it replays a completed EOD run and shows how the
detection + classification + feasibility pipeline behaves on real historical settlements.

### Basis dashboard — LIVE research mode (needs `UPSTOX_ACCESS_TOKEN` in `.env`, market hours)
```bash
$env:ARBEX_DATA_MODE="LIVE_USDINR_BASIS"; $env:ARBEX_REDIS="off"; python -m backend.server.run
# → http://localhost:8000/basis   (onshore + offshore + OTC, 1 s feed)
```

### Basis dashboard — LIVE retail‑arb mode
```bash
$env:ARBEX_DATA_MODE="LIVE_USDINR_BASIS"; $env:ARBEX_RETAIL_ARB="1"; $env:ARBEX_REDIS="off"; python -m backend.server.run
```

### EOD basis (no server, no account)
```bash
python research/run_eod_basis.py --onshore data/eod/nse_usdinr.csv \
    --offshore data/eod/cme_sir.csv --offshore-convention INRUSD_x10000
```

### Verify a broker token
```bash
python research/check_upstox.py                 # health check
python research/check_upstox.py --probe "NCD_FO|1769"
```

### Frontend
```bash
cd arbex-web && npm install && npm run dev       # → http://localhost:3001
```
(The basis dashboard is a standalone `frontend/basis_dashboard.html` served by FastAPI —
it does **not** need the Next.js app.)

---

## 11. Known limitations / caveats (read before trusting a number)

1. **`CARRY_RATE_ANNUAL` is not calibrated.** It is fixed at 1.9%. The real USD/INR
   near/far forward spread currently annualises to ~3.5–4%. So in **retail‑arb mode**
   the normaliser only strips ~half the real Sep→Oct spread and reports the leftover
   (~15 pips) as a "persistent dislocation" that fires every tick. **That is a modelling
   artifact, not arbitrage.** Fix: infer the carry from the live near/far futures
   instead of assuming it. Same caveat applies (smaller) to research mode.

2. **The offshore leg (`SIR=F`) is thin and delayed.** CME's Indian Rupee future trades
   ~12 times/day and, during Indian market hours, is in CME's overnight session — quotes
   lag 15–30 min. The offshore line looks stagnant/step‑wise. For the paper: use
   **onshore − OTC spot** (continuous) as the live headline, and **onshore − offshore
   future** only for EOD where staleness is irrelevant. The execution filter already
   applies a staleness haircut, but a stale mid is still a stale mid.

3. **NSE USD/INR options barely trade.** The options‑implied forward has a ~20–30 paise
   bid/ask. Any `future_options` basis against it is mostly noise. Needs a max‑spread
   gate before those events are published.

4. **Yahoo is an unofficial API** — no SLA, can change without notice. Frankfurter is
   the sanctioned fallback for spot; there is no free fallback for the offshore future.

5. **The retail‑arb edge is genuinely small.** Even with calibrated carry, an NSE
   box/calendar trade is a few paise, capital‑heavy, and needs near‑simultaneous
   two‑leg execution with an options‑enabled NSE account. It is a **monitor / pre‑trade
   filter**, not a money‑maker. The paper's value is the research basis.

6. **Repo carries ~9 MB of committed runtime logs** (`sentiment_assets/runtime/*.jsonl`)
   — the semantic owner's, not ours; do not touch.

---

## 12. What's left

From `md/PHASE3_PLAN.md`:

1. **Start the collector** (calendar‑bound — needs weeks of data before the 7 Oct MPC).
2. **Calibrate `CARRY_RATE_ANNUAL`** from the live forward curve (kills the retail‑arb artifact).
3. **Max‑spread gate** on the options leg.
4. **The event study** — basis behaviour around the RBI 12:30 fixing / MPC / month‑end,
   news‑conditioned by the semantic engine. This is the paper's core result.
5. Backup finding if the dynamics are unremarkable: the **feasibility‑adjusted
   opportunity** — raw basis vs what survives execution cost (`BasisExecutionFilter`).
6. Team decisions still open: target venue for the paper, guide sign‑off, final effort split.

### Team split (current)
- **Davis** — data feeds (Upstox / offshore / OTC) + the collector.
- **Dhruv** — retail‑arb mode + dashboard.
- **Nikhil & Navika** — semantic engine, reframed as news‑conditioning of the event study.

---

## 13. Boundary — do NOT touch

Owned by the semantic teammate:
`backend/core/semantic_engine.py` (except the one sanctioned branch),
`backend/core/semantic/`, `backend/core/sentiment_bridge.py`, `sentiment_assets/`,
`run_semantic.bat`, `/sentiment/*` routes, the `SemanticContextEngine` wiring.
The only interface is the Redis schema `arbex.raw_opps` → `arbex.scored_opps`.
Coordinate on the schema; never edit engine code.
