# Free Data-Source Registry (v1: news + calendar + central-bank)

Access notes and licensing caveats included so the dataset is reproducible and defensible.
Prefer sources with stable historical access and clear terms.

## FX price / returns

| Source | Access | Coverage | Notes |
|---|---|---|---|
| FRED `DEXINUS`, `DEXUSEU`, `DEXJPUS`, ... | Free API key | Daily USD/INR + majors | Clean, citable, but daily only. |
| Alpha Vantage `FX_INTRADAY` / `FX_DAILY` | Free key (have one) | Majors + USD/INR | ~25 req/day free; intraday limited history. |
| yfinance (`USDINR=X`, `EURUSD=X`) | Free, no key | Majors + USD/INR | Daily reliable; intraday ~60d only. |
| HistData.com | Free download | Majors (1-min) | Best free intraday for majors; no key. |
| Dukascopy historical | Free export | Majors + some EM (tick) | Highest resolution; heavier to fetch. |
| Twelve Data | Free key | Majors + USD/INR | Free tier rate-limited. |

Intraday INR is the weak spot: use HistData/Dukascopy for majors' minute bars, and
Alpha Vantage / FRED daily for USD/INR; align horizons to available resolution per pair.

## Events / news

| Source | Access | Notes |
|---|---|---|
| **GDELT 2.0** (Events + GKG) | Free, 15-min updates | Global, back to 2015 (events to 1979). Ships CAMEO event taxonomy + themes + tone = free event categorization. Primary event backbone. |
| Forex News Annotated (Kaggle) | Free download | 2,291 labeled headlines, majors (AUDUSD/EURCHF/EURUSD/GBPUSD/USDJPY). Sentiment eval set. |
| FNSPID | Free (HF) | Large-scale financial news + price; scale for pretraining/eval. |
| news_stream RSS + Finnhub | Already wired | Reuters, BBC, CNBC, ET, LiveMint, MoneyControl, FXStreet + Finnhub forex/general. |

## Economic calendar / surprise

| Source | Access | Notes |
|---|---|---|
| **FRED / ALFRED** | Free key | ALFRED vintages = real-time consensus vs revision -> leak-safe surprise. |
| Forex Factory calendar | Scrape (mind ToS) | actual / forecast / previous -> standardized surprise. |
| Investing.com calendar | Scrape (mind ToS) | Same fields; heavier anti-scrape. |
| Trading Economics | Limited free API | Calendar + indicators. |

## Central-bank text

| Source | Access | Notes |
|---|---|---|
| **BIS central bankers' speeches** | Free corpus | Large historical speech set across central banks; ideal for the speech category. |
| Fed / ECB / BoE RSS | Already wired | press + speeches. |
| RBI press releases / speeches | Add feed | India-specific policy text for USD/INR. |

## Regime controls (covariates for the market model)

VIX, DXY, US-IN 10y yield differential, Brent crude — via FRED / yfinance. Used to compute
abnormal (market-model-adjusted) returns and regime interaction terms.

## Deferred (free access restricted)

- **Twitter/X**: API v2 paid; no free bulk history; academic track discontinued.
- **Reddit**: API restricted since 2023; use Academic-Torrents dumps if social is added later.

## Legal / reproducibility

- Respect each source's ToS; prefer API + documented download over scraping.
- Store immutable raw snapshots + a fetch manifest (source, URL, retrieved_at) for reproducibility.
- Record event timestamps in UTC with an explicit publication-vs-event-time field.
