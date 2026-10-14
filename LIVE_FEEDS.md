# Live feeds — testing Arbex on real NSE/BSE currency futures quotes

The synthetic mode tests the detector on prices we generate. This mode runs the
**same** pipeline (`MultiSourceStreamer` → aligner → `ArbitrageEngine` → ranker →
execution filter) on real, two-sided order books from Indian exchanges.

## What it compares

USD/INR currency futures trade on both **NSE** and **BSE**. Same underlying,
same USD 1,000 lot, same monthly expiry. If one exchange's best bid is above the
other's best ask, you can buy on one and sell on the other at the same moment.
That is a cross-exchange arbitrage, and it is directly executable by a retail
account.

| leg | venue | provider | cost |
|---|---|---|---|
| `upstox_nse_usdinr` | NSE near-month USDINR future | Upstox | free |
| `upstox_bse_usdinr` | BSE near-month USDINR future, same expiry | Upstox | free |
| `kite_nse_usdinr` | the same NSE contract | Kite Connect | ₹500/month |
| `kite_bse_usdinr` | the same BSE contract | Kite Connect | ₹500/month |

Upstox alone gives the NSE-vs-BSE test for free. Adding Kite (the low-cost
showcase) also gives two *providers* quoting the *same* contract, which is the
original cross-provider question asked on real data.

USD/INR is the example used below. EUR/INR, GBP/INR, JPY/INR and the
EUR/USD, GBP/USD and USD/JPY futures work the same way (see
[Currency pairs](#currency-pairs)). The mode keeps its historical name,
`LIVE_USDINR_EXCHANGES`.

## Design rules (why the numbers can be trusted)

- **No invented quotes.** A leg emits a tick only for a genuine two-sided,
  uncrossed book. Empty or one-sided books and last-price-only quotes are
  suppressed, and the reason is shown.
- **One request per provider.** All of a provider's legs are fetched in a
  single API call, so NSE and BSE carry the *same* observation timestamp. Our
  own polling skew cannot create a fake gap.
- **Same contract only.** Legs are pinned to the nearest monthly expiry listed
  on every exchange. NSE-Oct is never compared with BSE-Nov.
- **Executable edge only.** In live mode an opportunity requires a crossed book
  (`bid_A > ask_B`). The synthetic-mode "latency arbitrage" heuristic (mid gap,
  no cross) is switched off. Profit is in paise (1 pip = 0.01 INR, tick = 0.25).
- **Stale data is dropped** (default 5 s).
- **Everything is recorded** to `research/recordings/<date>/<provider>.jsonl`,
  including suppressed one-sided books. Detected opportunities go to
  `opportunities.jsonl` in the same folder. Recordings replay through the exact
  same code.
- **Read-only.** No order endpoint is called anywhere. Keep broker accounts
  unfunded.

Expect NSE and BSE to be crossed **rarely**. The honest headline is the
distribution of the executable spread between venues, and how often it is
positive. That is what `/live/status` and the recordings measure.

## Setup (once)

```bash
pip install -r requirements.txt
cp .env.example .env        # then fill in tokens
```

- **Upstox:** developer console → create an app → generate an **Analytics
  Token** (read-only market data, about a year's validity) →
  `UPSTOX_ACCESS_TOKEN`.
- **Kite (optional):** Kite Connect app with the market-data plan →
  `KITE_API_KEY`. Generate `KITE_ACCESS_TOKEN` through the login flow **each
  day** (it expires around 06:00 IST).

## Before the review — pre-flight (market hours, Mon–Fri 09:00–17:00 IST)

```bash
python research/check_live_feeds.py                     # both providers
python research/check_live_feeds.py --providers upstox  # free only
```

It resolves the contracts, fetches one quote per leg and prints both books and
the cross-spread. If BSE resolves no contracts, it prints the segment codes
present in the Upstox BSE master (expected `BCD_FO`).

## Run

```bash
# Live (records automatically)
ARBEX_DATA_MODE=LIVE_USDINR_EXCHANGES python -m backend.server.run
# PowerShell: $env:ARBEX_DATA_MODE="LIVE_USDINR_EXCHANGES"; python -m backend.server.run

# Replay a recorded session — no credentials or market hours needed
ARBEX_DATA_MODE=LIVE_USDINR_EXCHANGES ARBEX_LIVE_REPLAY=research/recordings/2026-10-12 \
  python -m backend.server.run
```

Replay legs are named `replay_…` so they can never be mistaken for live data.
A looping replay (the default) re-detects the same recorded opportunities each
pass; `/live/status` shows `replay_loops`. Set `ARBEX_REPLAY_LOOP=0` to play once.

## Switching feeds from the dashboard (no restart)

The `arbex-web` dashboard has a **Synthetic | Live | Replay** toggle in its
header. Start the backend in any of these modes, then switch from the UI:

```bash
python -m backend.server.run              # backend (synthetic unless ARBEX_DATA_MODE says otherwise)
cd arbex-web && npm install && npm run dev   # dashboard on http://localhost:3001
```

- **Synthetic:** the six generated feeds, as before.
- **Live:** enabled once `UPSTOX_ACCESS_TOKEN` (or Kite keys) is in `.env`.
  The server re-reads `.env` on every check, so **attaching keys needs no
  restart**. (Removing a key does need one.) Hover a greyed-out button to see why
  it is unavailable.
- **Replay:** enabled once a session has been recorded; pick the day from the
  dropdown next to the toggle.

Switching is safe: live and replay legs are built *before* the running feed is
stopped, so a bad token or a network error leaves the current feed running and
shows the error next to the toggle. Every switch clears prices, candles and
opportunities from the previous feed, so nothing synthetic lingers on a live
screen.

In live and replay modes the left column becomes **Exchange Venues** (one line
per venue, prices to 4 decimals) and the depth panel shows the real L2 book.
The **Arbitrage Feed** column shows opportunities after the semantic engine
scores them, which needs Redis and `run_semantic.bat` running, in every mode.
Without them, use `/live` for detections.

API (what the toggle calls): `GET /feeds/mode` returns the current mode and
what is available; `POST /feeds/mode` with `{"mode": "live"}`,
`{"mode": "synthetic"}` or `{"mode": "replay", "session": "2026-10-13"}`
switches. Not available when the server was started in `LIVE_MT5` mode.

## What to open on review day

| endpoint | shows |
|---|---|
| `GET /live` | **the page to present**: self-updating view of both books, the best executable spread, price and spread charts, opportunities and feed health (works offline, light/dark, phone-friendly) |
| `GET /live/status` | each venue's live book, spread, quote age, and the NSE↔BSE executable spread in paise; recent opportunities |
| `GET /health` | per-leg health, suppression reasons, skipped feeds and why |
| `GET /sources` | per-leg stats incl. hub latency and poll counts |
| `GET /arbitrage/stats` | engine counters (only `cross_source` fires in live mode) |

A good demo sequence: open `/live`; point out both books share one
timestamp; show a BSE leg suppressed for having no two-sided book (evidence, not
a bug); then replay a full recorded session at speed to show detections and the
recorded `opportunities.jsonl`.

## Notes and limits

- **Redis is optional in this mode.** Opportunities are logged locally, so the
  dashboard path through the semantic engine is not required for the demo.
- **1 Hz polling.** Upstox allows more, Kite allows 1 quote request per second.
  The engine assumes ~1 s to act on both legs (`max_latency_risk_ms` 2000),
  which is retail reality. Faster opportunities are reported, not assumed
  capturable.
- **BSE liquidity.** BSE USD/INR futures can be much thinner than NSE. The leg
  reports this honestly through suppression reasons and recorded one-sided
  rows; measure it rather than assume it.
- **Verified offline only.** The parsers are tested against documented
  response shapes. The Upstox endpoints were verified live on `arbex-pivot`
  (2026-08-31), but the BSE segment code and Kite responses must be confirmed
  with `check_live_feeds.py` on a networked machine.

## Currency pairs

Every currency future listed on NSE and BSE is supported, in all three modes:

| pair | quoted as | pip | futures tick | lot |
|---|---|---|---|---|
| USD/INR | INR per USD | 0.01 (1 paisa) | 0.0025 (0.25 paise) | USD 1,000 |
| EUR/INR | INR per EUR | 0.01 | 0.0025 | EUR 1,000 |
| GBP/INR | INR per GBP | 0.01 | 0.0025 | GBP 1,000 |
| JPY/INR | INR per **100** JPY | 0.01 | 0.0025 | JPY 100,000 |
| EUR/USD | USD per EUR | 0.0001 | 0.0001 | EUR 1,000 |
| GBP/USD | USD per GBP | 0.0001 | 0.0001 | GBP 1,000 |
| USD/JPY | JPY per USD | 0.01 | 0.01 | USD 1,000 |

Choose them with `ARBEX_SYMBOLS` in `.env`. It takes a comma-separated list or
a preset: `all` (the seven above), `inr` (the four INR pairs) or `cross`
(EUR/USD, GBP/USD, USD/JPY). Unset means `USDINR`, the original behaviour.

```bash
ARBEX_SYMBOLS=all python -m backend.server.run
python research/check_live_feeds.py --symbols all     # pre-flight every pair
```

- **Live:** each pair gets its own legs (`upstox_nse_eurinr`, `kite_bse_usdjpy`,
  …) on its own common expiry. Each provider still makes **one** request
  covering every leg of every pair, so all legs share one observation
  timestamp. A pair that is not listed on two venues is skipped with the reason
  (`/live/status` → `skipped_feeds`, and the pre-flight output); the others run.
- **Synthetic:** six session × provider feeds per pair (`bloomberg_tokyo_eurinr`,
  …), each pair with its own reference price, seeded from the day's ECB rate
  when reachable (else a fixed level from the registry).
- **Replay:** recordings carry the pair, so a multi-pair session replays as
  multi-pair. Older USDINR-only recordings still replay.
- **Detection:** the live engine requires a crossed book of at least **one
  exchange tick of that pair** (0.25 paise for INR pairs, 1 pip for EUR/USD
  futures), not one global pip value. Pairs are never compared with each other.
- **Units:** INR pairs report spreads in paise and the others in pips,
  everywhere (`/live`, the dashboard, opportunity cards).
- **Dashboard:** a pair picker appears in the header when more than one pair is
  running. The ticker, venue/session charts and depth panel follow the chosen
  pair. The arbitrage feed and leaderboard show every pair, each card labelled.
  Pairs requested but missing from the current feed are listed in amber.
- **`/live`:** pair buttons at the top show each pair's live best spread
  (green when crossed); the page below follows the selected pair.

Expect thin books on the cross-currency futures and on BSE. Run the pre-flight
during market hours to see which pairs actually have two-sided books on both
venues; that availability is itself a result worth reporting.

### Non-INR pairs: free spot venues (no API key, no payment)

NSE/BSE's EUR/USD, GBP/USD and USD/JPY futures books are almost always empty,
so non-INR pairs use public spot quotes instead:

| venue | what it is | pairs |
|---|---|---|
| **Kraken** | crypto exchange with real fiat FX order books (executable) | EUR/USD GBP/USD USD/JPY USD/CAD AUD/USD USD/CHF EUR/GBP EUR/JPY EUR/CHF |
| **TrueFX** | Integral ECN interbank top of book (indicative) | the same majors |
| Bitstamp (opt-in) | crypto exchange with fiat FX order books | EUR/USD GBP/USD |

```bash
ARBEX_SYMBOLS=USDINR,EURUSD,GBPUSD,USDCAD,AUDUSD,EURGBP   # or: all / spot
ARBEX_SPOT_VENUES=kraken,truefx            # default; add bitstamp, or "off"
```

No credentials are needed: Live works with only spot pairs and no Upstox/Kite
token. A pair is never mixed across kinds: INR pairs use NSE/BSE futures,
non-INR pairs use spot (a future and a spot price differ by carry). All spot
venues are fetched concurrently in **one** poll, so their legs share one
timestamp. Leg ids are `spot_<venue>_<pair>`; recordings and replay work the
same as for futures.

**Venue basis (read this before presenting numbers).** Kraken's USD trades
persistently a little rich against the interbank TrueFX quote (measured
2026-10-14: EUR/USD ~1.1 pips, GBP/USD ~2 pips, USD/CAD ~5 pips), because moving
USD on or off a crypto venue costs fees and time. A raw cross between them
appears on *every* poll and is that basis, not arbitrage. So:

- `/live/status` reports, per venue pair, the raw `executable_pips`, the
  5-minute median `basis_pips`, and `beyond_basis_pips`;
- the engine reports a spot opportunity only when the cross exceeds the basis
  by at least one spot tick (0.1 pip), with the excess as its profit, and stays
  quiet for the first ~10 polls while the basis is measured.

In the first live run this turned "a cross on every poll" into 9 dislocations
of 0.1-0.2 pips in 61 polls. Those are before costs: Kraken's taker fee
(0.1-0.4%) is far larger, and TrueFX is indicative, so they measure how
quickly venues re-align, not money left on the table.

### Pip sizes: one source of truth

Pip size, exchange tick, decimals, contract size and futures specs for every
pair live in `backend/core/instruments.py`. All pip calculations (engine,
diagnostics, broker simulator, state store, `/live/status`, research endpoints)
read it. Adding a pair is one registry line. Unregistered six-letter pairs fall
back by quote currency (JPY/INR → 0.01, else 0.0001). A typo in `ARBEX_SYMBOLS`
(`USDINR,GOLD`) stops the server at startup. `GET /symbols` and
`GET /feeds/mode` list the running pairs, any requested pairs the feed cannot
serve, and each pair's display conventions.
