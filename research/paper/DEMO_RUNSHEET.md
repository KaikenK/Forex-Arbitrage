# Arbex demo: run sheet (Tuesday 24 November 2026)

One page. Follow it top to bottom.

---

## 10 minutes before

1. **Close heavy apps** (games, video calls, many browser tabs). FinBERT needs about 1 GB of memory.
2. **Start the demo.** Press **Win+R**, paste the line below, and press Enter:

   ```
   C:\Users\xyreo\arbex\run\arbex_demo.bat
   ```

   A black window opens. Wait until it shows `dashboard: ready on :3001`, which takes about 2 minutes. Your browser then opens **http://localhost:3001** by itself. Leave the black window open; you can minimise it.
3. **Optional: real live prices from the cloud.** Tuesday is a trading day. Press **Win+R** and run:

   ```
   C:\Users\xyreo\arbex\cloud\cloud_tunnel.bat
   ```

   Leave that window open too, and open **http://localhost:18000/live** in a second browser tab.
4. **Check before you start:**
   - The dashboard top bar shows **REPLAY** and `2026-11-03_demo`.
   - The **Arbitrage Feed** fills with cards within about 30 seconds.
   - **Sentiment Intelligence** shows **SERVICE LIVE**.

---

## What to show (about 6 minutes)

| # | Where | What to say |
|---|---|---|
| 1 | Dashboard, top | "Arbex watches EUR/USD, GBP/USD, USD/JPY and USD/CHF on two venues, Kraken and TrueFX, once a second. These are two hours of real recorded prices from 3 November, replayed." |
| 2 | Chart + Arbitrage Feed | "When one venue's bid is above the other's ask, the prices have crossed. That's an arbitrage opportunity. Each card shows the pair, the gap in pips, where to buy and sell, and a confidence score." |
| 3 | A card's confidence | "The confidence is the detector's score **minus a news risk penalty**. When news says a pair is about to move more than usual, we trust the gap less." |
| 4 | Sentiment Intelligence panel | "This is the semantic wing reading **live news right now**. Headlines are scored with FinBERT, a financial language model, and rolled up per currency and per pair." |
| 5 | The research (say it, or show the guide) | "We tested whether news predicts **direction**. It doesn't, by the time you can read it: markets react to economic releases within a minute, and our news arrives about 20 minutes later. In every test, direction from news was a coin flip. But **news activity predicts how big the next move will be**, and that held strongly across five and a half years of data. So the product uses news as a **risk signal**, never to bet on direction." |
| 6 | Cloud live view (if open) | "This is the live recorder, running on a cloud server so the study keeps collecting data. These are real Kraken and TrueFX prices right now." |
| 7 | Honest limits | "TrueFX is an indicative feed, so these are price discrepancies, not guaranteed profit. Our live sample was short, so the live ranking evidence is still inconclusive. That's what the cloud recorder is for." |

**Numbers worth quoting:**
- Reactions to releases happen **within 1 minute** and persist for 2 hours (1,385 releases, 2018–2025).
- A simple economics rule calls release direction right **68%** of the time at 15 minutes, beating a learned model at 64%. **From the moment an article arrives: 49%**, a coin flip.
- News activity predicts the size of the next 30–60 minutes across **about 258,000** pair × half-hour observations, 2019–2025 (q < 1e-8).
- Live, our news arrived **about 23 minutes** after a release (median).

---

## If something goes wrong

| Problem | Fix |
|---|---|
| Dashboard shows **OFFLINE** | Wait 30 seconds and refresh. Still offline: run `arbex_demo.bat` again (it restarts everything). |
| No cards in the feed | Wait a minute: the replay starts at a quiet moment. Then refresh. |
| News panel is empty | The internet is down. Prices still work (they replay from disk); skip point 4 and say so. |
| Cloud view doesn't load | Skip point 6. The replay demo doesn't need it. |
| Everything fails | Open `research/paper/SEMANTIC_WING_EXPLAINED.md` and present from it. |

**Don't click SYNTHETIC or LIVE** in the dashboard top bar unless you mean to. LIVE switches the laptop to live venue prices: harmless, because it records only into the demo folder, but on a phone hotspot Kraken may look stale.

---

## After

1. Close the `cloud_tunnel` window, if you opened it.
2. Press **Win+R** and run:

   ```
   C:\Users\xyreo\arbex\run\arbex_stop.bat
   ```

The cloud recorder keeps running whatever happens on the laptop.
