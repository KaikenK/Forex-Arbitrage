# The Arbex Semantic Wing, Explained

*A plain-language guide to what we built, what we tested, what we found, and how to run it.*

Written 21 November 2026, after the last tests were run. Every number here comes from the project's research log (`research/RESEARCH_TRACKER.md`) and the result files in `research/artifacts/`.

The guide is written for someone who has never seen the project; a glossary of terms is at the end (§12).

---

## Contents

1. [The project in one page](#1-the-project-in-one-page)
2. [What the semantic wing is supposed to do](#2-what-the-semantic-wing-is-supposed-to-do)
3. [How it works, step by step](#3-how-it-works-step-by-step)
4. [How we tested it, and why we were strict about it](#4-how-we-tested-it-and-why-we-were-strict-about-it)
5. [The data we used](#5-the-data-we-used)
6. [Problems we found in free data](#6-problems-we-found-in-free-data)
7. [What we found: historical tests](#7-what-we-found-historical-tests)
8. [What we found: the live system](#8-what-we-found-the-live-system)
9. [The bottom line](#9-the-bottom-line)
10. [Limitations](#10-limitations-what-these-results-cannot-tell-you)
11. [The working product: how to run and show it](#11-the-working-product-how-to-run-and-show-it)
12. [Glossary](#12-glossary)
13. [Background reading](#13-background-reading)
14. [Where everything lives](#14-where-everything-lives)
15. [Dataset sizes, and how to make each one better](#15-dataset-sizes-and-how-to-make-each-one-better)

---

## 1. The project in one page

**Arbex** watches the prices of four major currency pairs on two different venues at the same time:
- the pairs are EUR/USD, GBP/USD, USD/JPY and USD/CHF;
- the venues are **Kraken** and **TrueFX**.

Sometimes one venue will sell a currency for less than the other will pay for it. For a moment, buying on one and selling on the other would lock in a small profit. That moment is called an **arbitrage opportunity** (or a "cross", because the two prices have crossed). Arbex's **detector** finds these moments and scores each one.

**The semantic wing** is the part of Arbex that reads the news. The idea behind it:
- News moves currencies.
- A price gap that appears just as important news lands might be riskier, because prices are about to jump.
- If the system understands the news, it should be able to rank opportunities better: trust some, be careful with others.

**The research question:** does reading the news actually help? In particular:
- Can news tell us which way a currency will move?
- Can it tell us how much it will move?
- Can it tell us when to be careful?

**The short answer** after about a month of building and testing:
- **News does not tell us which way prices will move** by the time we can read it. The market has already reacted, usually within a minute.
- **News does tell us when to expect bigger moves.** When there is a lot of news about a currency, its next 30 to 60 minutes tend to be more volatile.
- **On the live system**, there was too little data to show that using news this way improves the ranking. The tests are honest about that.

---

## 2. What the semantic wing is supposed to do

For every news article that arrives, the semantic wing tries to answer four questions for each currency pair:

| Question | Example answer |
|---|---|
| **Is this pair affected?** | "A US jobs report affects every pair with the dollar in it." |
| **Which direction?** | "Better-than-expected US jobs: the dollar should rise, so EUR/USD should fall." |
| **How much?** | "Typically about 8 pips in the first 15 minutes." |
| **How sure are we?** | "About 68% likely to be right." |

It also keeps a **rolling 30-minute news summary** for each pair: how much news there has been, and whether it leans positive or negative.

**Why a ranker cares:** if a price gap appears on EUR/USD just as a big US release lands, the gap may close or reverse before both trades fill. A ranker that knows about the release can lower its confidence in that opportunity.

---

## 3. How it works, step by step

### 3.1 The pieces

```
 News feeds (RSS, Finnhub)                   Price feeds (Kraken, TrueFX)
          |                                              |
          v                                              v
 +--------------------------+              +---------------------------+
 |  Semantic service :9000  |  news bias   |  Arbex backend :8000      |
 |  - reads headlines       | -----------> |  - detects price gaps     |
 |  - scores sentiment      |              |  - adds news risk penalty |
 |  - spots economic        |              |  - records everything     |
 |    releases              |              +---------------------------+
 |  - builds currency index |                            |
 +--------------------------+                            v
                                         Dashboard :3001 and /live page
```

### 3.2 The semantic service (the "reader")

- **Collects headlines** every 90 seconds from a set of RSS news feeds (central banks, government statistics offices, financial news sites) and from Finnhub.
- **Scores each headline's tone** with **FinBERT**, a language model trained on financial text. It outputs a number between −1 (very negative) and +1 (very positive).
- **Recognises economic releases.** When a headline says something like "US payrolls 165k vs 65k expected", the **release model**:
  - finds the matching event in the economic calendar;
  - works out how surprising the number was (the "surprise");
  - applies a simple rule from economics: a surprise that is good for a currency's economy pushes that currency up.

  This is the most reliable part of the system (see §7.2).
- **Keeps a currency index.** For each currency (dollar, euro, pound, yen, franc, rupee) it keeps a running score of recent news tone. A pair's score is the difference between its two currencies' scores.

### 3.3 The backend (the "detector")

- **Polls both venues** about once a second.
- **Detects a cross** whenever one venue's bid is above the other's ask.
- **Attaches a snapshot** of everything the semantic wing currently believes about that pair, so it can be studied later.
- **Applies a news risk penalty** (switched on 21 Nov 2026, after testing was complete):
  - each opportunity's confidence is lowered when the news suggests the pair is about to move more than usual;
  - it is also lowered when one venue's price is a few seconds old.
  - The system never uses news to bet on a direction; the tests showed there is nothing to bet on (§7).

### 3.4 What gets recorded

| File | Contents |
|---|---|
| Quotes | Every price from both venues, once a second. |
| Opportunities | Every cross detected, with the semantic snapshot. |
| Predictions | Every call the semantic wing made about every pair, so its accuracy can be checked later. |
| Shadow log | What the system *would* have recorded had the news penalty been on. Kept during testing, while the penalty was off. |

---

## 4. How we tested it, and why we were strict about it

### 4.1 The trap we wanted to avoid

When a model does not work, it is tempting to adjust it, try again, adjust again, and keep the version that looks best. Do this long enough and something will always "work" by luck. This is often called **p-hacking**.

### 4.2 What we did instead: pre-registration

Before running any test, we wrote down in a dated, append-only log:
- exactly what the test would be;
- what result would count as success;
- when we would stop.

The log is `research/RESEARCH_TRACKER.md`. Three rules applied throughout:
- **No changing a test after seeing its result.** Every change was logged *before* the data was looked at, with a reason.
- **A negative result is a finished result.** If an idea failed its test, we recorded the numbers and moved on.
- **Two attempts per component.** A component that failed twice was declared failed.

Over the project we logged:
- **6 numbered deviations:** changes to the original plan, such as a later data cutoff after the laptop was off for days;
- **42 numbered clarifications** (C1–C43; number C26 was never used): details the plan had left open, decided before the relevant results existed.

Each one says what changed, why, and confirms that no result had been seen yet.

### 4.3 Walk-forward testing

For tests that fit a model, we used a **walk-forward** design:
- the model is trained only on months before the month it is tested on;
- the test then steps forward one month and repeats.

The model never sees the future. This is how a real trading system would have to work.

### 4.4 A positive control

Before testing anything new, we checked that our pipeline could find an effect everyone agrees exists: **US economic surprises move the euro–dollar rate within minutes** (Andersen and colleagues, 2003). If our pipeline could not see that, it was broken.

It could not see it at first. Section 6 explains why, and that discovery turned into one of the project's contributions.

---

## 5. The data we used

| Data | What it is | Size |
|---|---|---|
| **Economic calendar** | Scheduled releases (jobs, inflation, central-bank decisions) with the expected and actual numbers, 2018–2025 | 34,333 events; 1,385 high-impact releases used for the main tests |
| **Minute prices** | One price per minute for the four major pairs, 2018–2025 (HistData) | 32 pair-years |
| **Historical news** | Headlines from GDELT, a free global news database, collected through Google BigQuery | 1.57 million currency-related headlines, Oct 2019 – Apr 2025; 955,570 after removing copies |
| **Live recordings** | Our own recordings of Kraken and TrueFX prices and detected crosses | 37,604 opportunities, 25 Oct – 7 Nov 2026 |
| **Live predictions** | Every call the live semantic wing made | about 32,000 rows |

**About USD/INR:** the rupee was originally a focus, but no free minute-by-minute rupee price data exists, and no free Indian economic calendar with forecasts exists either. The rupee is therefore studied only at daily resolution.

---

## 6. Problems we found in free data

Four of the most useful findings are not about news at all. They are silent errors in free data that would have produced wrong conclusions if we had not caught them, and each was caught by a check, not by a suspicious result.

1. **The calendar had the wrong times.**
   - About 30% of events had no time and were stored as midnight. Every US jobs report from 2018 to 2025 sat at the wrong time.
   - Summer events from 2023 on were an hour off, because of an outdated time-zone rule.
   - Effect: our positive control failed twice. After fixing the times from the agencies' published schedules, it passed.
2. **The minute prices were labelled with the wrong clock.**
   - The files say they use "Eastern Standard Time without daylight saving". They actually follow New York time *with* daylight saving.
   - The symptom: in summer, every release seemed to cause a mysterious second jump exactly one hour later.
   - After the fix, the summer reaction in the first minute went from +0.5 to +8.5 basis points.
3. **The news database returned only the last 12 minutes of each day.**
   - Asking GDELT's article service for a whole day's headlines (newest first, at most 250) returns only those published just before midnight.
   - 88% of our "daily" sample sat between 11 pm and 1 am. We discarded that sample and switched to GDELT's full archive on BigQuery.
4. **The full archive has no headlines before September 2019.** We started the sample in October 2019 instead of 2018.

We also found and fixed problems in our own live system:
- **Fake headlines in the live logs.** The service filled quiet periods with made-up demo headlines (92% of the log at first). These were removed and excluded from every test.
- **A feed "storm"** on 3 November, which re-sent the whole news backlog many times.
- **FinBERT accidentally switched off** in the live service until 5 November.

---

## 7. What we found: historical tests

### 7.1 Scheduled economic releases move prices immediately, and the move lasts

- **Speed:** across 1,385 high-impact releases, the four major pairs reacted within one minute.
- **Persistence:** the move did not fade over the next two hours.
- **Size by category:** inflation surprises moved prices most (+17.3 basis points in the first minute), then employment (+7.6), then growth (+5.4).
- **Speed by category: no difference.** All categories reacted within a minute.

| Test | Verdict |
|---|---|
| H2: different kinds of news have different effects | Supported |
| H3: different kinds of news move prices at different speeds | Not supported |

### 7.2 Direction: right at the moment of release, gone by the time we can read about it

| When the call is made | Simple economics rule | Learned model | Always guessing the common side |
|---|---|---|---|
| At the release, judged 1 minute later | **75%** | 68% | 52% |
| At the release, judged 15 minutes later (main test) | **68%** | 64% | 52% |
| 15 minutes after the release, judged over the next 45 minutes | 51% | **49%** | 50% |

1. **The simple rule beat the learned model.** The rule is "good surprise for an economy, its currency goes up". The fancier model was worse, so the live system uses the simple rule.
2. **By the time an article arrives, there is no direction left to predict.** The reaction has already happened. A headline about a release is old news to the market.

We later measured how late the live system actually receives news (§8.1): about 23 minutes after the release, on median. That makes the second point even stronger.

### 7.3 Size and confidence

- **Size:** the model ranks big and small moves sensibly. But its size estimates are no more accurate than simply using "how much this pair usually moves at this time of day".
- **Confidence:** when the model says "70% sure", it is right about 70% of the time, so it is well calibrated. But its confidence barely varies, so it rarely separates easy calls from hard ones.
- **Affected pairs:** "a release affects every pair with that currency" catches 90% of affected pairs. But only 41% of the pairs it flags actually move unusually, and pairs *without* that currency move unusually about 19% of the time.

### 7.4 Everyday news (not scheduled releases)

We tested four ideas on 1.57 million historical headlines:

| Idea | Verdict | What happened |
|---|---|---|
| H1: genuinely new information (novelty) predicts bigger moves | Not supported | Novelty mattered, but in the opposite direction: more novel news came with slightly smaller moves. |
| H5: news mood works at the level of a currency, not a pair | Partly supported | Building pair moods from currency moods helped a little with one scoring method. The "market-wide mood" part failed. |
| H6: a news-based currency score predicts the next day's moves | Not supported | No forecasting power; the hit rate was 49%. |
| Real release headlines, read automatically | Not supported | The reader recognised a release in only 6.6% of real headlines, and extracted the correct number in 4.7%. No method beat a coin flip on direction. |

### 7.5 The key positive result: news activity predicts how much prices will move

We tested the live system's own design, a 30-minute rolling news window, on five and a half years of history.

| Question | Result |
|---|---|
| Does the window's mood predict *which way* the next 30–60 minutes go? | **No.** Hit rate 50.0%. |
| Does the *amount* of news predict *how much* prices move next? | **Yes, strongly** (significant after correcting for multiple tests, at both 30 and 60 minutes). |
| Does a "caution" score (strong mood plus lots of news) predict bigger moves? | Weakly, at 60 minutes only. |

**This result is the heart of the project:**

> **News is a risk signal, not a direction signal.**
> It tells you when to be careful, not which way to bet.

### 7.6 The key result, repeated on news the study had never seen (C45, 21 Nov 2026)

A result found once could be luck, so we repeated the test on fresh data.
- **The data:** 18 months of headlines the study had never used (May 2025 – Oct 2026), about 400,000 currency headlines, or 250,000 after removing copies.
- **The test:** fixed in the research log before any of that data was downloaded.
- **One change, also decided in advance:** the calendar we had ends in April 2025, so the test kept only half-hours away from each currency's usual release times (for example 08:30 New York, 07:00 London).

| | Original (2019–2025) | Repeat on new data (2025–2026) |
|---|---|---|
| Does more news mean a bigger move in the next 60 minutes? | Yes (strength 0.048) | **Yes (strength 0.041, p = 0.003)** |
| Next 30 minutes? | Yes (0.043) | **Yes (0.044, p = 0.0002)** |
| Does the news mood predict direction? | No (hit rate 50%) | **No (hit rate 49.8%)** |

The finding held, with almost the same strength, on news the study had never seen and at times when no scheduled release was due. Direction stayed at chance.

---

## 8. What we found: the live system

The live tests ran on our own recordings, 25 October to 7 November 2026. Recording stopped on 7 November and was not resumed; the project owner concluded it on 21 November. That left fewer days than planned, so every live result below is a description, not proof.

### 8.1 How fast does news reach us?

From 9 economic releases we caught live:
- **Feed time:** the first headline about a release reached the system after a median of **23 minutes**.
- **Publisher time:** news publishers themselves often stamped their stories a few minutes after the release (median 4.6 minutes).
- **Coverage:** only 2 of the 10 big releases in that week had a matching headline within 90 minutes.

This confirms §7.2: by the time a typical article arrives, the market has long since moved.

### 8.2 Did the live models call direction correctly?

The check: does the price, over the hour after the system received the news, move the way the model said?

| Model | Calls | Days | Right | What it means |
|---|---|---|---|---|
| V1 (the original text model), 25 Oct – 1 Nov | 124 | 4 | 44% | No better than a coin. |
| Release model, first version, 2 – 4 Nov | 26 | 3 | 62% | Too few to say anything. |
| Release model, improved version, 5 – 6 Nov | 23 | 2 | 91% | All on 4 big surprises: Bank of England hike, US jobless claims, US payrolls, consumer sentiment. Matches the historical finding that big surprises give clear direction, but 4 events prove nothing. |

### 8.3 Did news help rank live opportunities?

We measured how well a score sorts opportunities that **survive** (the price gap is still there at the next check) from ones that **vanish**. The measure is AUC: 0.5 is a coin flip, 1.0 is perfect.

| Live test | Days | Result |
|---|---|---|
| H6b (25 Oct – 4 Nov), 36,668 opportunities | 6 | Detector alone: 0.918. Adding the news risk penalty made it slightly *worse* (by 0.003–0.012). The 30-minute window made no difference. |
| RQ5 extension (4 – 6 Nov), 916 opportunities | 3 | Detector alone: 0.815. No news add-on made a measurable difference either way. |
| C40-1: are opportunities worse around releases? | 8 | **Could not be computed.** Comparisons need the same time of day in other weeks, and the live calendar archive held only one week. |
| C40-2: do older venue prices predict vanishing gaps? | 8 | No (0.495). |
| C40-3: does the amount of news predict vanishing gaps? | 6 | No, overall (0.531, not significant). Verdict for "news as risk" on the live recorder: **not supported**. |
| C43 shadow test (5 – 6 Nov), 318 opportunities | 2 | With the news penalty applied, ranking was slightly *better* (0.863 vs 0.844). Too little data to rely on. The currency filter removed 83% of window items, which shows that most headlines in a pair's window are not about that pair's currencies. |

### 8.4 Why the live results are weak

- **Too few days.** 2–8 days, with frequent outages (Wi-Fi drops, a phone hotspot, the laptop being off).
- **Very few vanishing gaps to learn from.** About 98% of detected gaps were still there one second later.
- **Indicative prices.** TrueFX is an indicative feed, not a market you can actually trade on. Kraken often ran badly on the phone hotspot.
- **A different question.** "Is the gap still there one second later?" is not what the historical tests measured, which was "how much will prices move in the next 30 minutes?".

So the live results neither confirm nor refute the historical finding. They simply could not test it properly.

---

## 9. The bottom line

1. **Direction from news is not available in practice.** Prices react to economic news within a minute, and our news arrives about 20–40 minutes later. Every attempt to predict direction from news came out at coin-flip level once timing was realistic:
   - historical release headlines;
   - 1.5 million everyday headlines;
   - novelty scores;
   - currency scores;
   - the live models.
2. **A simple economic rule beats a learned model** for releases, at the moment of release.
3. **News activity predicts how much prices will move.** This was tested on five and a half years of data and is the main positive finding. It was then **repeated on 18 months of news the study had never seen, with almost the same strength** (§7.6). It supports using news as a **risk signal**: a reason to be careful, not a reason to bet.
4. **Live evidence that the risk signal improves ranking is inconclusive.** One live test found it slightly harmful, one slightly helpful, the others found no difference. All rested on too little data.
5. **Free financial data has silent errors that flip conclusions.** We found four and documented how to detect and fix them.
6. **The product follows the evidence.**
   - The live system applies a news-based **risk penalty**, switched on after testing finished.
   - It **never** uses news to bet on direction.
   - It filters out headlines that do not concern the pair's currencies.

---

## 10. Limitations (what these results cannot tell you)

- **Rupee:** no free minute data, so the rupee is studied daily only.
- **Japanese yen and Swiss franc** releases rarely come with forecasts in the calendar, so these currencies are thin in the release tests.
- **The news archive is English-only** and begins in October 2019.
- **Live recording covered about 8 trading days**, with many outages, on a consumer laptop and, for part of the time, a phone hotspot.
- **TrueFX is indicative**, so detected crosses are price differences, not proven executable profit.
- **Some plans changed during the project**, for example the data cutoff dates. Every change is logged with its reason and was made before results were seen, but readers should know they happened.
- **The positive control's official record is "failed".** The failures were later traced to the data errors in §6, and the control passes on corrected data. Because that pass came after the fact, the record is left as it was.

---

## 11. The working product: how to run and show it

### 11.1 Two modes

| Mode | What it does | When to use it |
|---|---|---|
| **Demo** | Replays two real recorded hours of Kraken and TrueFX prices (3 November 2026, 19:00–21:00 IST) in a loop, with **live news**. Records nothing. | Presentations, and any time the FX market is closed (Saturday 03:30 IST to Monday 03:30 IST). |
| **Live** | Connects to the real venues and records everything, as during the study. | To collect more data. A new recording is a new study and needs its own log entry before anyone looks at it. |

### 11.2 How to start it

Press **Win+R**, paste one of the following, and press Enter. Starting it this way keeps it running after any other app is closed.

- **Demo** (opens the dashboard automatically):

  ```
  C:\Users\xyreo\arbex\run\arbex_demo.bat
  ```

- **Live recording:**

  ```
  C:\Users\xyreo\arbex\run\arbex_live.bat
  ```

- **Stop everything:**

  ```
  C:\Users\xyreo\arbex\run\arbex_stop.bat
  ```

The semantic service loads FinBERT first, so allow about two minutes for everything to come up. Then open:
- the **dashboard** at http://localhost:3001;
- the **live monitor** at http://localhost:8000/live.

### 11.3 What to point out in a demo

1. **The feed bar** at the top reads **REPLAY** and names the session. Say that these are real recorded prices, replayed.
2. **The arbitrage feed** shows each detected gap: pair, size in pips, which venue to buy and sell on, and a confidence score.
3. **The confidence score is the detector's score minus the news risk penalty.** In the raw data, `raw_confidence_score` is the detector's score and `semantic_applied` shows the penalty.
4. **The Sentiment Intelligence panel** shows the live news reader:
   - FinBERT scores;
   - the currency index;
   - the 30-minute window.
   The news is real and current, even in demo mode.
5. **The live monitor** (`/live`) shows each venue's bid and ask, how old each price is, the best current gap, and recent opportunities.
6. **The message:** the system uses news to decide **when to be careful**, not **which way to trade**, because that is what the evidence supports.

### 11.4 Continuing to record later

**A cloud recorder has been running since 21 November 2026**, so the laptop no longer needs to stay on.
- **Where:** an Azure for Students server in Central India, free for 12 months; no card is attached, so it cannot charge anything.
- **What it records:** everything live mode records, with the same settings.
- **From the laptop**, in `C:\Users\xyreo\arbex\cloud\`:
  - `cloud_status.bat` checks the server's health;
  - `cloud_pull.bat` copies the data to `C:\Users\xyreo\arbex\cloud_data`;
  - `cloud_tunnel.bat` shows the cloud live monitor at http://localhost:18000/live.
- **The rule:** this is a new study (C44 in the research log). Its outcomes are not looked at until 30 weekdays are recorded and an analysis plan is logged.

Recording on the laptop is still possible too, and nothing has been deleted or locked:
1. Run `arbex_live.bat` on a weekday.
2. Before looking at any of the new data, add an entry to the research log describing the new sample and how it will be analysed.
3. Never mix the new recording with the 25 Oct – 7 Nov sample.

The news risk penalty is now on, so new recordings will include it: `raw_confidence_score` holds the detector's original score.

---

## 12. Glossary

| Term | Meaning |
|---|---|
| **Arbitrage / cross** | Two venues' prices for the same pair have crossed, so buying on one and selling on the other would profit. |
| **AUC** | A 0.5-to-1.0 score for how well a number sorts good cases above bad ones. 0.5 is a coin flip, 1.0 is perfect. |
| **Basis point (bp)** | One hundredth of one percent (0.01%). |
| **Calibrated** | When a model says "70% sure", it is right about 70% of the time. |
| **Confidence interval (CI)** | A range that would contain the true value most of the time (here, 95%); wide intervals mean little data. |
| **Day-cluster bootstrap** | A way to compute confidence intervals that treats each day as one unit, because events on the same day are related. |
| **FinBERT** | A language model trained on financial text that rates a sentence's tone from negative to positive. |
| **GDELT** | A free, public database of world news, updated every 15 minutes. |
| **Indicative feed** | Prices shown for information, not necessarily tradable at that price. |
| **Novelty** | How different a headline is from recent headlines about the same currency. |
| **Pip** | The smallest usual price step: 0.0001 for most pairs, 0.01 for yen pairs. |
| **Pre-registration** | Writing down a test and its success rule before seeing the data. |
| **Positive control** | A test of something known to be true, to check the tools work. |
| **Release / surprise** | A scheduled economic number (for example, jobs). The surprise is the actual number minus the forecast, scaled by how big surprises usually are. |
| **Shadow mode** | Computing what a feature *would* do and logging it, without letting it change anything. |
| **Walk-forward** | Training only on the past and testing on the next period, then stepping forward. |

---

## 13. Background reading

A short, non-technical tour of the ideas this project builds on. Verify the exact references before citing any of them.

- **Prices react to economic news within minutes.** Andersen, Bollerslev, Diebold and Vega (2003, *American Economic Review*) showed that US data surprises move exchange rates almost immediately. Ederington and Lee (1993, *Journal of Finance*) found prices adjust within seconds to tens of seconds, with extra volatility lasting about 15 minutes. Our results agree: one minute for the jump, then no further drift.
- **The direction of the reaction follows economics.** Several studies, including Faust and colleagues (2007) and Chaboud, Chernenko and Wright (2008), confirm that good news for an economy strengthens its currency. That is why our simple rule works at the moment of release.
- **Old news is not news.** Tetlock (2011, *Review of Financial Studies*) showed that markets react less to stories that repeat earlier ones. This is the idea behind our novelty test; we found that novelty did not predict bigger currency moves.
- **News can be measured at scale.** Baker, Bloom and Davis (2016, *Quarterly Journal of Economics*) built an influential uncertainty index by counting newspaper articles. Our currency-level news index follows a similar spirit.
- **Tools.** FinBERT (Araci, 2019) is the language model we used for tone. GDELT (Leetaru and Schrodt, 2013) is the news database.
- **Order flow and longer horizons.** Evans and Lyons (2005, 2008) show that news continues to matter over days through who is buying and selling. This channel is outside what our minute-level tests can see.

---

## 14. Where everything lives

| What | Where |
|---|---|
| Research log (every decision and result) | `research/RESEARCH_TRACKER.md` |
| Result files | `research/artifacts/` |
| How to reproduce each result | `research/REPRODUCE.md`; `python -m research.reproduce --list` |
| Technical status summary (17 Nov snapshot) | `research/paper/SEMANTIC_WING_STATUS.md` |
| Paper draft (LaTeX) | `research/paper/latex/` |
| Semantic service code | `services/semantic/src/` |
| Backend semantic code | `backend/core/` (`semantic_engine.py`, `sentiment_bridge.py`, `semantic_apply.py`, `semantic_shadow.py`, `window_bias_v2.py`) |
| Dashboard | `arbex-web/` |
| Launchers and logs | `C:\Users\xyreo\arbex\run\` and `C:\Users\xyreo\arbex\logs\` |
| Live recordings (research sample) | `C:\Users\xyreo\AppData\Local\arbex\recordings\` |
| Demo data and anything the demo writes | `C:\Users\xyreo\arbex\demo\` |
| Copies of the shadow log | `C:\Users\xyreo\arbex\data\` |
| Code branch | `semantic-microservice` on GitHub |

---

## 15. Dataset sizes, and how to make each one better

### 15.1 Rows are not the same as evidence

Many rows can come from a few independent events. For example, one US jobs report produces a row for each of four pairs; 30,000 live opportunities came from only 8 days. What limits how sure we can be is the number of **independent units**:
- release moments, for the release tests;
- trading days, for news and live tests;
- rare outcomes, such as the few price gaps that vanish.

The tables below give both counts.

### 15.2 Historical tests

| Test | Rows | Independent units | What limits it | How to improve it |
|---|---|---|---|---|
| Positive control, H2/H3 (US releases × 4 pairs, 2018–2025) | about 1,370 per pair; 1,482 US releases | about 770 release moments per pair | US releases only; the calendar ends April 2025 | (1) Validate release clocks for euro-area, UK and Swiss agencies (only 17 US families are validated now), which adds non-US releases. (2) Extend prices and calendar past April 2025. (3) Add earlier years (2010–2017) as a **pre-registered replication**, if the calendar dump covers them. |
| RQ1–RQ4 (1,385 releases with a surprise × 4 pairs) | 3,687 release × pair rows at 15 min; 4,514 for RQ1 | 900 release moments; 76 test months | Same calendar limits; only 3 JPY and 30 CHF releases | Same as above. Adding non-US releases matters most for yen and franc. Tick data (Dukascopy, about 0.75 GB) would show the first 10–60 seconds, where most of the reaction happens. |
| R1 (daily, US releases) | 1,608 releases | 932 release dates | Daily prices hide minute-level reactions | Nothing more to gain at daily resolution. Use intraday data instead, already done for the majors. |
| GDELT news archive | 3,690,637 headlines extracted; 1,569,940 currency-tagged (Oct 2019 – Apr 2025); 955,570 after removing copies | 67 months; about 1,450–1,720 days | English only; titles only; timestamps rounded to 15 minutes; no titles before Sept 2019 | (1) Extend to May 2025 – Nov 2026: the BigQuery free quota resets monthly. (2) Add GDELT's translated non-English stream. (3) Use a source with exact publication times (a wire service) so horizons shorter than 30 minutes can be tested. |
| H1 novelty | 243,360 rows; 234,273 headlines | 1,717 dates | Headlines naming only the US dollar get no novelty score and are dropped | Count how many are dropped (open Step E) and test them separately. Size is not the problem: the result is a precise "no". |
| H1 rupee arm | 101,176 headlines | 1,436 news-days | Daily rupee prices only | Find intraday USD/INR data (paid, or the NSE/BSE currency-futures APIs already wired: Upstox, Kite). |
| H5 currency mood | 3,640 test rows | 1,455 dates; 1,293 days with VIX | Daily data | More years (extend GDELT), not more rows per day. |
| H6 currency score | 5,006 rows (4,077 out-of-sample) | 1,455 dates; 55 test months | Daily, Monday–Thursday only | Same as H5. The result is a clear "no", so more data is unlikely to change it. |
| window30 (30-minute news window) | about 255,000–261,000 signal times | 1,445 dates | 15-minute GDELT timestamps | **The main positive result. Done (C45, 21 Nov 2026):** it replicated on May 2025 – Oct 2026 news (§7.6). Still open: the like-for-like check on the original sample, and precise timestamps for 5–15 minute tests. |
| RQ2-E2E (real release headlines) | 2,410 candidates; 245 rows the reader could parse | 535 moments; only 51 with a parsed number | The tagger keeps only headlines naming a currency, so "US CPI rises 0.5%" never enters | Collect release headlines by indicator name ("payrolls", "CPI", "jobless claims"), and widen the reader's patterns. Live feeds like investinglive print "X vs Y expected", which parses well. |

### 15.3 Model training data

| Data | Size | Problem | How to improve it |
|---|---|---|---|
| V1 text model, USD/INR training rows | 743 rows; 553 after removing 190 synthetic ones | Small, and partly synthetic; the majors part was trained on template headlines | Rebuild from real headlines labelled by the moves that followed. Given the direction results, the realistic aim is a size/risk model, not a direction model. |
| Release model | Fitted on the 1,385-release history | Works as designed | Refit after adding non-US releases and newer years. |

### 15.4 Live recordings and tests

| Test | Rows | Independent units | What limits it | How to improve it |
|---|---|---|---|---|
| Recording overall | 37,604 opportunities; quotes from 2 venues | About 8 UTC days with data, about 5.5 with both venues | Laptop off, app closes, Wi-Fi and hotspot drops; recording stopped 7 Nov | Run on an always-on machine with a wired or stable connection (a desktop or a small cloud server), so the laptop's state does not matter. Record **at least 30 weekdays**. |
| H6b ranking | 36,668 opportunities | 6 days; 790 vanished gaps | Few days; 98% of gaps survive one second | More days, as above. Add a pre-registered second outcome with more variation, such as the realised edge after 5–30 seconds. |
| RQ5 extension | 916 opportunities | 3 days; 42 vanished gaps | Kraken degraded on the hotspot; only 3 days after the engine update | Same as the line above. |
| C40 news as risk | 37,561 opportunities | 8 days; 832 vanished; **4 release windows, 0 usable controls** | The calendar archive held one week, so no comparison weeks existed | Archive the calendar every week, starting at least 3 weeks **before** the test window; record about 3 months to reach the 30 release windows the test needs. |
| C43 shadow | 318 opportunities | 2 days; 18 vanished | The shadow ran only 2 days | More days. The shadow is no longer needed now that the penalty is applied: new recordings hold both the raw and the adjusted score. |
| Prospective tests (did live calls get direction right?) | V1: 124 calls; V2.0: 26; V2.1: 23 | 4, 3 and 2 days; V2.1 is only **4 releases** | Few releases caught; only 2 of 10 big releases matched a headline | Aim for about **100 releases**, which gives about ±10 percentage points: roughly 3 months at 8–10 matched releases a week. Raise coverage with faster sources: cache-busting for fxstreet, one shared poller, and direct agency feeds (BLS, BEA, ECB). |
| News arrival delay | 19 headline rows | 9 releases (10 needed) | Same coverage problem | Two more weeks with better coverage reaches the pre-registered minimum. |
| Venues | Kraken + TrueFX | 8 legs | TrueFX is indicative (not tradable); Kraken has no exchange timestamp and an unusable USD/JPY book | Add venues with tradable quotes and exchange timestamps, such as broker demo APIs with streaming prices, or Bitstamp for EUR and GBP. More legs also mean more crosses. |

### 15.5 Demo data

| Data | Size | How to improve it |
|---|---|---|
| Replay session | 42,840 quote rows; 2 hours (3 Nov, 19:00–21:00 IST); about 120 opportunities per loop | Cut a longer slice (4–6 hours; a whole day is about 180 MB and is loaded into memory), or several sessions selectable from the dashboard's feed menu. |

### 15.6 The three changes that would help most

1. **Record for longer on a machine that stays on.** Days are the unit of evidence, and every live test was limited by having 2–8 of them. About 6–12 weeks of weekdays would turn the live tests into real tests.
2. **Archive the economic calendar every week and improve news coverage.** This makes C40 computable and the prospective tests meaningful (about 100 releases).
3. ~~Replicate the window30 result on news the study never touched.~~ **Done on 21 Nov 2026 (C45): it replicated** (§7.6).

Each of these is a new sample: it needs its own entry in the research log before any of its data is examined.
