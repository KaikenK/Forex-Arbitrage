# News sources for FX: assessment (2026-11-02)

Measured by `python -m research.feed_inventory` (one live fetch per source; raw table in
`news_sources.md`, data in `artifacts/feed_inventory.json`). The check ran on Sunday night UTC,
so freshness and items-per-hour understate weekday activity for anything that follows the
working week; re-run during a London/New York session for representative rates.

**What matters for this system.** For each article we need (1) a precise timestamp, ideally the
publisher's own; (2) which currency it concerns; (3) for scheduled releases, the actual figure, in
the title or the summary. Volume of general business news does not help: the research shows the
release move is complete within a minute, so speed and figures matter, not more headlines.

## Sources in use after the 2026-11-02 change

| Source | Why | FX share | Release-like | Timestamps |
|---|---|---|---|---|
| investingLive / ForexLive (`investinglive.com/feed/news`) | Fastest release headlines with figures ("+0.5% vs +0.3% expected"); old ForexLive URL redirects here | 76% | 96% | to the second |
| FXStreet news | FX-specialist wire, ~3 items/h on weekdays | 77% | 30% | to the second (87%) |
| CNBC economy / top news | US macro coverage | 30% / 0% | 23% / 0% | to the second |
| Bloomberg economics | Macro headlines (no full text) | 14% | 0% | 71% with seconds |
| BBC business | General; kept, filtered by keywords | 2% | 0% | to the second |
| Economic Times forex | The best INR source by relevance | 96% | 2% | to the second |
| Economic Times markets, Livemint, Business Standard | INR/India macro context | 4–9% | 3–17% | to the second |
| Federal Reserve (all press), ECB press, Bank of England, Bank of Japan, SNB, RBI | Policy decisions and statements, from the source | 25–85% | 0% | minute-level; RBI has none |
| BLS latest, BEA, Census indicators | US release figures in the summary (BLS 100% / BEA 92% / Census 78% of summaries carry numbers) | n/a (title has no currency; the source implies USD) | BLS 100%, BEA 67% | BLS to the second; BEA/Census minute |
| Finnhub `/news` general + forex (now enabled in the service) | Reuters and CNBC headlines (general), ForexLive (forex) | 2% (general), 100% (forex, 1 item on Sunday) | ForexLive items | to the second |

## Dropped or not added

| Source | Finding |
|---|---|
| Reuters legacy RSS (`feeds.reuters.com`) | DNS no longer resolves. Reuters retired public RSS; its headlines still arrive via Finnhub `general`. |
| NDTV business | HTTP 404 |
| Moneycontrol markets | HTTP 503 (blocks automated requests) |
| Yahoo Finance RSS | HTTP 404 |
| US Treasury press RSS | HTTP 404 at the tried URL |
| WSJ markets / world, MarketWatch market pulse, CNBC finance, FXEmpire | Feeds still answer but have not updated for weeks to months (abandoned) |
| Investing.com RSS | Works but updates slowly (0.2–0.7 items/h) and carries no summary figures |
| Nasdaq currencies | Fast (7 items/h) but 0% attributed to a study currency by the tagger |
| Eurostat catalogue RSS | 1,980 dataset-update items, almost none FX-relevant (0.5%); not a release wire |
| ONS release calendar | Announces upcoming releases, no figures |
| Action Forex, FXStreet analysis | Commentary rather than news; no release figures |
| Business Standard | Answers urllib but returns 403 to the service's HTTP client; kept in the list, currently failing |

## APIs

| API | Finding |
|---|---|
| Finnhub `/news` | Free tier works with the existing key. `general` = Reuters + CNBC, `forex` = ForexLive items, `merger`/`crypto` irrelevant here. Publisher time to the second. The key had not been configured in the live service, so it was RSS-only until 2026-11-02 03:19 IST. |
| Finnhub economic calendar | HTTP 403: premium only. The live calendar stays ForexFactory weekly JSON (forecasts, no actuals). |
| Alpha Vantage `NEWS_SENTIMENT` | Now premium-only ("This is a premium endpoint"); not usable on the free key. |

## Problems found while checking

- **TLS:** this machine's certificate store failed verification for the ECB, SNB and Eurostat
  feeds, so the service's fetches failed silently. It now fetches through httpx, which uses
  certifi's certificate bundle; verification stays on.
- **Timestamps:** the service stamped every item with its own fetch time and discarded the
  publisher's time and the source. Both are now kept (`publisher_time`, `feed_url`), alongside
  the fetch time.
- **Synthetic headlines:** the feed filled quiet periods with made-up demo headlines that the
  backend analysed as real: 92% of logged predictions. This is now off by default.
