# The Arbex Sentiment Engine — Explained From Scratch

**A complete guide to what this system does, how it works, and what its numbers mean.**

This document assumes you have never worked with machine learning or financial markets.
Every technical term is defined the first time it appears. If you read it top to bottom,
you should be able to explain the entire system to someone else.

---

## Table of contents

1. [The problem in one page](#1-the-problem-in-one-page)
2. [Financial vocabulary you need](#2-financial-vocabulary-you-need)
3. [Machine learning vocabulary you need](#3-machine-learning-vocabulary-you-need)
4. [The central insight: tone is not direction](#4-the-central-insight-tone-is-not-direction)
5. [What the system predicts](#5-what-the-system-predicts)
6. [The data: what we have and where it came from](#6-the-data-what-we-have-and-where-it-came-from)
7. [How the model was trained](#7-how-the-model-was-trained)
8. [How a prediction is made, step by step](#8-how-a-prediction-is-made-step-by-step)
9. [Retrieval: finding comparable past events](#9-retrieval-finding-comparable-past-events)
10. [Every score in the system](#10-every-score-in-the-system)
11. [The life of one news article](#11-the-life-of-one-news-article)
12. [System architecture](#12-system-architecture)
13. [The numbers, and what they honestly mean](#13-the-numbers-and-what-they-honestly-mean)
14. [Known limitations](#14-known-limitations)
15. [What was recently fixed](#15-what-was-recently-fixed)
16. [Next steps](#16-next-steps)
17. [Papers to cite](#17-papers-to-cite)
18. [Running the system](#18-running-the-system)
19. [Anticipated questions](#19-anticipated-questions)
20. [Glossary](#20-glossary)

---

## 1. The problem in one page

### The wider project

**Arbex** is a research terminal for **arbitrage** in foreign exchange markets.

> **Arbitrage** means buying something in one place and selling it in another at
> the same moment, for a profit, because the two places briefly disagree on the
> price. If one data feed says the US dollar costs 83.10 rupees and another says
> 83.15, that gap is an arbitrage opportunity. These gaps are usually tiny and
> vanish within a second.

The main engine watches several price feeds at once and reports these gaps. It also
scores each one with a **confidence** number: how sure it is that the gap is real and
actionable, rather than a stale quote or a glitch.

### Where sentiment comes in

Price data alone cannot answer one important question: **is right now a sensible moment
to act?**

A price gap during a calm hour means something different from the same gap thirty
seconds after a central bank announcement, when everything is moving violently and
quotes are unreliable.

The sentiment engine reads the news as it is published and answers two things:

1. **Which way is the news pushing this currency pair right now?**
2. **How chaotic is the news environment — should we be more cautious than usual?**

It then adjusts the arbitrage engine's confidence accordingly. Crucially, it can only
ever make the system **more cautious**, never more aggressive, and by a strictly limited
amount. We will come back to why that design choice matters.

---

## 2. Financial vocabulary you need

### Currency pair

Currencies are always priced against each other. **USD/INR** means "how many Indian
rupees does one US dollar cost." If USD/INR = 83.00, one dollar costs 83 rupees.

The first currency (USD) is the **base**. The second (INR) is the **quote**.

### The direction trap

This is the single most important idea in the whole project.

When the **rupee gets weaker**, it takes *more* rupees to buy one dollar. So USD/INR
goes **up**.

That means:

> **Bad news for India makes the USD/INR number go UP.**

A headline like *"Rupee plunges 1.2% on tariff threat"* sounds negative — and it is
negative *for India* — but the chart you are watching goes **up**. Any system that
treats "negative news" as "price goes down" will be wrong, consistently, for this pair.

Remember this. It explains most of the design decisions that follow.

### Basis point (bp)

A **basis point** is one hundredth of one percent.

- 1 bp = 0.01% = 0.0001 as a decimal fraction
- 100 bp = 1%

It is used because it is unambiguous. "The rate rose 0.5%" could be misread; "the rate
rose 50 basis points" cannot.

**What it is worth here.** At USD/INR ≈ 83.00:

| Basis points | As a fraction | Rupee move | Roughly |
| --- | --- | --- | --- |
| 1 bp | 0.0001 | ₹0.0083 | under 1 paisa |
| **5 bp** | 0.0005 | ₹0.0415 | about 4 paise |
| 20 bp | 0.0020 | ₹0.166 | about 17 paise |
| 100 bp | 0.0100 | ₹0.83 | 83 paise |

So a 20 bp move takes USD/INR from 83.00 to roughly 83.17.

Basis points are **proportional**, which is why they are used rather than raw rupees:
5 bp means the same thing whether the pair trades at 83 or at 95.

### Economic release

Governments publish statistics on a fixed, published schedule. Each publication is
called a **release**.

An example: **Non-Farm Payrolls** is the United States jobs report — how many jobs the
economy added last month. It is published by the Bureau of Labor Statistics on the
first Friday of each month at 13:30 UTC.

Everyone knows the date and time weeks ahead.

### Forecast, consensus and surprise

Because the timing is known in advance, banks publish **forecasts** — their economists'
best guesses at the number. These get averaged into a **consensus forecast**.

Markets trade on that consensus *before* the number arrives. The price already reflects
what everyone expects.

Then the real number lands. The gap between them is the **surprise**:

```
Forecast (consensus expectation) : 160,000 new jobs
Actual   (what really happened)  : 256,000 new jobs
Surprise                         : +96,000, i.e. +60%
```

**Markets move on the surprise, not on the number.** If payrolls had come in at exactly
160,000, the price would barely twitch — not because jobs do not matter, but because
that information was already in the price. Only the 96,000 nobody expected is new
information.

Note that "surprise" here is a precise measurement, not a dramatic event. It simply
means "how far the number missed what was expected."

### Sentiment

**Sentiment** just means the emotional tone of a piece of text — does it read as
positive, negative, or neutral? It is a property of the *writing*, not of the market.

Keep sentiment and direction separate in your head. They are different things, and for
USD/INR they frequently point opposite ways.

---

## 3. Machine learning vocabulary you need

### Model

A **model** is a mathematical function: numbers go in, a prediction comes out. It is not
a program someone wrote rule by rule. Its behaviour is determined by internal numbers
called **weights**, and those weights were discovered automatically from examples.

### Feature

A **feature** is one measurable property of a thing, expressed as a number.

To describe a news event to a model, we cannot hand it the sentence. We must convert it
into a list of numbers. Each position in that list is a feature.

### Label

A **label** is the correct answer for one training example.

If you are teaching a model to recognise cats, the label is "cat" or "not cat." In our
case the label is **what the price actually did after the event** — which we can look up
in history, because it already happened.

### Training vs inference — the most important distinction

These are two entirely separate phases, and confusing them is the most common source of
misunderstanding.

**Training** happens once, in advance, on historical data. We know both the input (the
event text) *and* the answer (what the price did), because it is the past. The model
studies these pairs and adjusts its weights until it predicts well.

**Inference** happens live, on new articles. We know the input. **We do not know the
answer — the future has not happened yet.** The model produces a guess.

| | Text | Price move |
| --- | --- | --- |
| **Training** | input | **known answer** we learn from |
| **Inference (live)** | input | **the output we are predicting** |

> **This is why a new headline does not "get assigned" basis points.** During training,
> basis points go *in* as the answer. During inference, basis points come *out* as the
> prediction. Same quantity, opposite direction of flow.
>
> It is also why the ~1,600 articles the system has already analysed cannot be used for
> training: they have text but nobody recorded what the price did afterwards. They have
> inputs but no answers.

### Classification and regression

- **Classification** picks one option from a fixed set. "Up, down, or flat?" is
  classification.
- **Regression** predicts a number on a continuous scale. "How big will the move be?"
  is regression.

Our system does both.

### Weight

A **weight** is one number the model holds for each feature. It expresses how strongly
that feature pushes toward a particular answer.

A large positive weight on the word "tariff" for the outcome "up" means: *historically,
when "tariff" appeared, USD/INR tended to rise.*

Nobody writes these numbers by hand. They emerge from training.

### Linear model

A **linear model** makes predictions by multiplying each feature by its weight and
adding everything up. That is all.

A worked example with three features:

```
features    : [ "tariff" present = 1,  "inflation" present = 0,  surprise = 0.6 ]
weights(up) : [        +2.1,                   -0.4,              +1.8         ]

score(up) = (1 × 2.1) + (0 × -0.4) + (0.6 × 1.8)
          = 2.1 + 0 + 1.08
          = 3.18
```

Compute the same for "down" and "flat," and whichever scores highest is the prediction.
Converting the three scores into percentages gives the model's confidence.

"Linear" means there are no layers and no complicated transformations. It is the
simplest thing that can learn — which, as we will see, is exactly right for a small
dataset.

### Training set, holdout, and why the split matters

You cannot judge a model on the examples it studied, any more than you can test a
student on the exact questions they were given the answers to. A model can score
perfectly on its training data by memorising it, while being useless on anything new.

So the data is split:

- **Training set** — the model learns from these.
- **Holdout (or test set)** — hidden during training, used only to score the model.

All reported accuracy figures come from the holdout.

### Overfitting and regularisation

**Overfitting** is memorisation rather than learning. If a model has many weights and
few examples, it can latch onto coincidences — for example, giving a huge weight to a
word that appeared once, in one event, that happened to go up.

**Regularisation** is a penalty applied during training that discourages large weights.
It forces the model to find patterns that hold broadly rather than quirks that fit one
example. It is the main defence against overfitting when data is scarce.

### Baseline

A **baseline** is the score you would get without learning anything. It is the bar a
model must clear to have demonstrated any value.

With three possible answers and blind guessing, you would be right about **one time in
three: 33%.** That is our baseline.

### Accuracy, F1, and why both are needed

**Accuracy** is simply the percentage of correct predictions.

Accuracy alone can lie. If 47% of your examples are "flat," a lazy model that always
answers "flat" scores 47% while being worthless.

**F1** is a score that rewards being genuinely good at *every* class, not just the
common one. **Macro F1** averages the F1 across all three classes equally. A healthy
macro F1 means the model really is distinguishing up from down from flat.

Always quote both. Accuracy tells you how often it is right; F1 tells you whether it is
right for good reasons.

### Confusion matrix

A table showing what the model predicted against what actually happened. It reveals
*which* mistakes it makes — for instance, whether it confuses "up" with "flat" but never
with "down."

### Calibration

**Calibration** asks: when the model says it is 70% confident, is it actually right
about 70% of the time?

An uncalibrated model can be right often but wildly overconfident, which is dangerous —
you would trust it when you should not. Calibration adjusts the confidence numbers so
they mean what they claim.

### Embedding and cosine similarity

An **embedding** turns a piece of text into a list of numbers that captures its meaning,
such that similar meanings produce similar lists. "Rupee weakens" and "INR declines"
land close together even though they share no words.

**Cosine similarity** measures how similar two such lists are, by measuring the angle
between them. The result runs from 0 (unrelated) to 1 (identical direction).

### Hashing (the "hashing trick")

A cheap alternative to embeddings. Take each word, run it through a function that always
maps the same word to the same numbered slot, and count how many times each slot is hit.

"inflation" always lands in slot 87. The result is a fixed-length list of counts.

It captures which words appeared, not what they meant. Its advantages are that it needs
no dictionary, never fails on a word it has not seen, and has a fixed size regardless of
vocabulary — which matters when you have very little training data.

---

## 4. The central insight: tone is not direction

Here is the problem in concrete form.

A headline arrives: **"Trump threatens 25% tariffs on all Indian imports, rupee plunges 1.2%"**

A language model reading tone scores this **−0.87** — strongly negative. That is correct
as a description of the writing.

But the tradeable reality is that USD/INR **rises**. Bad news for the rupee means it
takes more rupees to buy a dollar.

So a system built on tone alone would predict "down" and be exactly wrong.

**This is why there are two separate models.**

| Model | What it does | How it knows |
| --- | --- | --- |
| **FinBERT** | reads tone | trained on financial text by its original authors |
| **Reaction model** | predicts direction | trained on *actual USD/INR price history* |

The reaction model was never told what the news means. It was shown past events
alongside what the price actually did, and it worked out the relationship itself. It
learned the inversion from evidence rather than being told about it.

**FinBERT** deserves a note. It is a language model (110 million internal parameters)
originally built by taking Google's BERT and further training it on financial news, then
teaching it three-way sentiment. It reads financial language well. But it was trained
mostly on company and stock news, where bad news means the number goes down. It has no
concept of quote conventions. That limitation is precisely what the second model exists
to cover.

---

## 5. What the system predicts

For a given article and currency pair, the reaction model outputs three things at once:

| Output | Type | Meaning |
| --- | --- | --- |
| **Direction** | classification | up (+1), down (−1), or flat (0) |
| **Magnitude** | regression | how large the move will be |
| **Horizon** | classification | which window it applies to: 1 hour, 4 hours, or 1 day |

These combine into a single signed number called the **market score** — a predicted
return. Multiply by 10,000 to read it in basis points.

**The flat band.** Any predicted or observed move smaller than ±5 bp (about 4 paise) is
called "flat." Below that threshold, a move is indistinguishable from ordinary
tick-by-tick noise, and claiming a direction would be pretending to see a signal that is
not there.

**A worked reading.** If the model outputs `+0.0008`, that is `+8 bp`, meaning:

> "I expect USD/INR to be roughly 0.08% higher at the end of this window — about
> 83.00 → 83.07."

Treat the **sign** as the main content and the size as a strength indicator. See
[section 13](#13-the-numbers-and-what-they-honestly-mean) for the error bars.

---

## 6. The data: what we have and where it came from

### The four kinds of data

| # | What it is | Size | What it is used for |
| --- | --- | --- | --- |
| 1 | **Labelled event table** | 190 rows / 78 events | Training the model **and** serving as the evidence library |
| 2 | **Trained model file** | 133 training rows inside it | The finished model plus its scorecard |
| 3 | **Analysed article archive** | ~1,600 articles | Display only — not used by any model |
| 4 | **Live mirror** | ~1,700 headlines, ~1,300 analyses | Powers the dashboard and the 30-minute window |

### What one row of the labelled table contains

Each row is a **before-and-after pair**: an event, and what the price did next.

| Field | Example | What it is |
| --- | --- | --- |
| Event text | "United States Non Farm Payrolls released at 256K versus 160K forecast…" | what was published |
| Timestamp | 2025-01-10T13:30:00Z | exactly when |
| Pair | USD/INR | which currency pair |
| Horizon | 60 | which window this row measures, in minutes |
| **Realised move** | **+0.0002556** | **the answer: what the price actually did** |
| Event type | nfp | payrolls, CPI, rate decision, etc. |
| Scheduled | true | was it on the published calendar |
| Actual / Forecast / Previous | 256000 / 160000 / 212000 | the numbers |
| Surprise | +0.6 | how far it missed expectations |

### How the realised move is calculated

This is mechanical, not a judgement call:

1. Find the USD/INR price **at the moment of the event** → start price
2. Find the price **one horizon later** → end price
3. Compute `(end − start) ÷ start`

Worked through with real numbers:

```
Event at 13:30, horizon 60 minutes
  price at 13:30 : 85.7726
  price at 14:30 : 85.7945

  (85.7945 − 85.7726) ÷ 85.7726 = 0.0002556

  → the label is +0.0002556, i.e. +2.6 basis points
```

The prices come from downloaded historical market data. The label is observed fact, not
anyone's opinion. Nobody sat down and marked headlines "bullish" or "bearish" — a real
strength, because it means the model cannot inherit a human's bias.

### How surprise is calculated

```
surprise = (actual − forecast) ÷ |forecast|

Payrolls: (256,000 − 160,000) ÷ 160,000 = +0.60
```

Fallbacks are recorded in the data itself: if the forecast is zero, divide by the
previous value; if that is also zero, divide by 1.

The sign matters — positive means the number beat expectations. See
[section 14](#14-known-limitations) for a known weakness in this formula.

### Why 190 rows but only 78 events

Each event is measured at up to **three** time windows — one hour, four hours, one day.
The same text, three different answers. So one event can produce three rows.

If every event had all three, 78 × 3 = 234. We have 190. The gap is **missing price
data**:

| Horizons available | Events | Rows |
| --- | --- | --- |
| All three | 39 | 117 |
| Only two | 34 | 68 |
| Only one | 5 | 5 |
| **Total** | **78** | **190** |

Why would a window be missing? An event late on a Friday has no price one day later —
the market is closed. An event near the edge of the downloaded price history has nothing
after it. When the price cannot be found, **the row is skipped rather than invented.**

This is the system being honest. Interpolating a fake price would have produced a tidy
234, and every one of those extra rows would have been fiction.

**The number that matters for honesty: 190 rows describe 78 real events.** When we say
"57 held-out rows," that is **24 independent events**.

### Was there any cheating in the split?

A fair concern: if the one-hour row of an event were used for training while its one-day
row was used for testing, the model would have partially seen the answer.

**This was checked directly. Zero of 78 events straddle the split boundary.** All rows
from one event share a timestamp, and the split is by date, so they always land on the
same side together.

---

## 7. How the model was trained

### Step 1 — turn each event into 264 numbers

**Numbers 1–256: the text.** Each word is hashed into one of 256 slots and counted, then
the list is scaled to a consistent length. Most slots are zero — a headline has perhaps
fifteen words.

**Numbers 257–264: the facts.** Eight slots holding the source, event category, event
type, whether it was scheduled, and the surprise value.

The result looks something like:

```
[ 0, 0, 0.117, 0, 0, 0.234, ... , 0, 1, 0, 0.6 ]
  └──────── text (256) ────────┘   └─ facts (8) ─┘
```

### Step 2 — turn the price move into a label

```
realised move > +5 bp  →  label = up   (+1)
realised move < −5 bp  →  label = down (−1)
otherwise              →  label = flat ( 0)
```

### Step 3 — split by date, never randomly

Rows are sorted by time. The earliest **70%** trains the model; the remaining **30%** is
held back for scoring.

**Why by date rather than at random?** A random split would let the model learn from
2026 events to predict 2025 ones. In the real world you never have tomorrow's data
today, so a random split produces scores that cannot be achieved in practice.

```
190 rows sorted by date
├── 133 rows (54 events)  → training
└──  57 rows (24 events)  → holdout, never seen during training
```

### Step 4 — learn the weights

**All 264 weights start at zero** — meaning "I know nothing; all three outcomes are
equally likely."

Then the training loop runs:

1. Take a training event and compute scores using the current weights
2. Compare the prediction with the true label
3. Nudge every weight that contributed — up if it pushed toward the right answer, down
   if toward the wrong one, by an amount proportional to how wrong it was
4. Repeat across all 133 rows, then repeat the whole pass, up to 2,000 times

The process stops when further nudging no longer reduces error.

Three settings matter:

**Balanced class weighting.** The data has more "flat" than "up" or "down." Without
correction the model would learn "always say flat" and score respectably while being
useless. Balancing makes rare outcomes count for more, forcing genuine discrimination.
This is why macro F1 must be quoted alongside accuracy.

**Regularisation.** With 264 features and 133 rows, the model could memorise the
training set by assigning a huge weight to a word that appeared once. A penalty on large
weights prevents this. It is the main reason the model generalises at all.

**A separate regressor for magnitude**, with the same anti-memorisation pressure.

### Step 5 — save and score

The finished weights are written to a file. **At runtime the model does no learning** —
it only multiplies and adds. It is then scored on the 57 held-out rows it never saw.

### Why not a neural network?

A larger model has more weights, and more weights need more examples. With 133 training
rows, a deep network would memorise them perfectly and fail on anything new.

This was tested rather than assumed. Three model families were trained on identical
splits:

| Model | Accuracy | Macro F1 |
| --- | --- | --- |
| **Linear** | **61.4%** | **0.594** |
| TF-IDF + SVM | 54.4% | 0.384 |
| k-nearest-neighbours | 40.4% | 0.377 |

The simplest model won clearly. That is a real experimental result, not a shortcut.

---

## 8. How a prediction is made, step by step

At runtime, for a brand-new headline:

1. **Convert the headline into the same 264 numbers.** Identical recipe to training —
   this must match exactly, or the weights would be meaningless.

2. **Multiply and add.** For each of the three outcomes, multiply each feature by its
   weight and sum:

   ```
   score(up)   = Σ (feature × weight_up)
   score(down) = Σ (feature × weight_down)
   score(flat) = Σ (feature × weight_flat)
   ```

3. **Pick the winner.** The highest score is the predicted direction. Converting the
   three scores into percentages gives the confidence.

4. **Predict the size** using the separate magnitude model.

5. **Predict the horizon** — which time window this belongs to.

6. **Combine** into the signed market score.

That is the whole inference path. No learning, no lookups, no database — just
arithmetic against saved weights. It runs in well under a millisecond.

---

## 9. Retrieval: finding comparable past events

Alongside every prediction, the system shows **five past events** as supporting
evidence, so a human can see *why* rather than being handed a bare number.

### A common misconception

It is tempting to say "it finds the five most similar articles." That is not what
happens, and the difference matters.

**It filters by matching labels first, then ranks by text similarity within whatever
survives.**

The filter is tried as a ladder, strictest first:

```
1. same pair + same horizon + same event family + same scheduled status
2. same pair + same horizon + same event family
3. same pair + same horizon + same scheduled status
4. same pair + same horizon
5. ... progressively looser ...
```

At the first level that returns anything, candidates are ranked by cosine similarity of
their text, duplicates removed, and the top five kept.

So it is **"find past events of the same kind, then pick the most textually similar
among them"** — not "search for similar text."

This is why an oil story can surface an inflation release: they matched on pair and
horizon, and text similarity was only the tie-breaker within a very small pool.

### How similarity is measured

Both texts become lists of numbers, and the angle between them is measured. With the
semantic embedder enabled, texts pass through a sentence-transformer producing 384
numbers capturing meaning. Cosine similarity then scores 0 to 1.

In practice scores average about **0.24** and top out near **0.6**, so 0.6 is treated as
the realistic ceiling rather than assuming 1.0 is reachable.

### What "agree" and "disagree" mean

Every retrieved past event carries the move that actually followed it.

Suppose the model predicts **up**. For each of the five retrieved events:

| That event was followed by | Stance |
| --- | --- |
| a rise (same direction as our prediction) | **supportive** |
| a fall (opposite) | **contradictory** |
| no meaningful move | **neutral** |

The question being asked is: *"when things like this happened before, did the market do
what I am now predicting?"*

**Retrieval only ever adjusts confidence — never the direction or the size.** Evidence
can make the system less sure of itself. It can never overrule the model.

---

## 10. Every score in the system

These names are easy to confuse. This table is the reference.

| Term | Range | What it means | Where it is used |
| --- | --- | --- | --- |
| **Sentiment score** | −1 to +1 | How the headline *sounds*. Negative = bad news. | Display and explanation only. **Does not set direction.** |
| **Market score** | about ±0.005 | The predicted price move, as a fraction. **This is the prediction.** | Sign gives direction, size gives magnitude |
| **Confidence** | 0 to 1 | How much to trust this single analysis | Below ~0.34 the verdict becomes WAIT |
| **Semantic score** | −1 to +1 | The 30-minute weighted **average of market scores** | Becomes the news bias for arbitrage |
| **Risk penalty** | 0 to 0.08 | How much that bias reduces arbitrage confidence | Subtracted from the engine's confidence |
| **Relevance** | 0 to about 0.6 | How closely a retrieved past event matches | Weights the evidence term |
| **Surprise** | any | How far a release missed its forecast | A model feature, and the high-risk trigger |

> **Note the easy mix-up:** the *sentiment* score is FinBERT's tone reading of one
> headline. The *semantic* score is the thirty-minute average of *market* predictions.
> Different things entirely.

### How confidence is built

```
confidence = 0.30 × sentiment strength
           + 0.50 × market model certainty
           + 0.20 × evidence agreement
```

**Sentiment strength (30%)** — not whether the news is good or bad, but **how sure**
FinBERT is. A clearly-toned headline gives high certainty; an ambiguous one gives low.
Direction is irrelevant here; only conviction counts.

**Market model certainty (50%)** — how lopsided the model's three scores are. A clear
winner means high certainty; a near three-way tie means low. It carries the largest
weight because it is the only component trained on actual price outcomes. It is also
*calibrated*, so "70% confident" genuinely corresponds to being right about 70% of the
time.

**Evidence agreement (20%)** — the product of two things:

- **Relevance** — how close the retrieved events are, measured against the realistic
  ceiling of 0.6.
- **Agreement** — supportive weight minus contradictory weight, normalised, with each
  event weighted by its own relevance. A *strongly matching* contradiction therefore
  outweighs a *weakly matching* agreement.

If all five retrieved events are neutral, agreement returns 0.5 — neither confirming nor
refuting, and deliberately scoring lower than evidence that actively agrees.

### The thirty-minute window

Each analysis in the last thirty minutes gets a weight:

```
weight = recency × confidence
```

**Recency** = `1 − (age in minutes ÷ 30)`, with a floor of 0.1:

| Age | Recency weight |
| --- | --- |
| 1 minute | 0.97 |
| 15 minutes | 0.50 |
| 29 minutes | 0.10 (floored) |

**Confidence** is that analysis's own confidence, so shaky predictions count for less.

Then a weighted average: multiply each market score by its weight, sum, and **divide by
the total weight**. Dividing is what makes it an average rather than a sum — ten
articles do not automatically produce a bigger number than two.

### How the risk penalty is built

```
penalty = |semantic score| × 0.08        ← bias strength
        + conflict ratio    × 0.04        ← disagreement
        + staleness                       ← data age

then capped at 0.08 total
```

**Bias strength** — stronger directional news warrants more caution.

**Conflict ratio** — of the articles in the window, take the smaller of the bullish and
bearish counts, divided by the total. Four bullish and four bearish gives 0.5, the
maximum. Eight bullish and zero bearish gives 0. The reasoning: news pulling in both
directions is *more* dangerous than news pulling firmly one way, because it means the
market has not settled.

**Staleness** — a step function on the age of the newest data: nothing under 5 minutes,
0.01 under 15, 0.02 under 30, 0.03 beyond.

### Why the cap exists

The total penalty can never exceed **0.08**, and it can only ever *reduce* confidence.

> A price gap is a **fact** the engine measured. A news prediction is an **opinion**.
> An opinion may make us more cautious about acting on a fact. It must never overrule
> it, and it must never invent an opportunity that is not there.

This is a deliberate design decision, and it is worth stating explicitly when presenting
the system.

---

## 11. The life of one news article

1. **Published.** A news wire puts out a headline.

2. **Ingested.** Within seconds the system picks it up, works out which currency pair it
   concerns and what kind of event it is, and records whether it came from a real wire or
   was generated for testing — so nothing is ever quietly faked.

3. **Tone read.** FinBERT scores the sentiment: −0.87, "sounds bad." Display only.

4. **Converted.** The headline becomes 264 numbers.

5. **Direction predicted.** Those numbers are multiplied by the saved weights. Result:
   +0.0008, "the pair rises about 8 bp." **Note this is the opposite sign to the tone —
   correctly so.**

6. **Evidence gathered.** Five comparable past events are retrieved.

7. **Evidence weighed.** Do those five agree with the prediction?

8. **Confidence computed.** The 30/50/20 blend.

9. **Verdict issued.** BUY, SELL, HOLD or WAIT, with a written explanation that names
   the pair convention rather than appearing to contradict itself.

10. **Saved.** Written to disk immediately — which is why the dashboard still shows real
    analysis even when the news service is switched off.

11. **Aggregated.** The last thirty minutes of market scores are averaged, weighted by
    recency and confidence, producing the semantic score.

12. **Applied.** That becomes a risk penalty of at most 0.08, subtracted from the
    arbitrage engine's confidence.

---

## 12. System architecture

Two separate programs cooperate.

**The news service** hosts the models. It polls news feeds, runs FinBERT and the
reaction model, performs retrieval, and exposes the results over HTTP. It runs on port
**9000**.

**The Arbex backend** is the trading research platform. It consumes the news service,
mirrors everything to disk, serves the dashboard, and applies the news bias to arbitrage
scoring. It runs on port **8000**.

```
   news wires (RSS, Finnhub)
              │
              ▼
   ┌──────────────────────────┐
   │   News service  :9000    │   FinBERT + reaction model + retrieval
   └──────────┬───────────────┘
              │  HTTP / event stream
              ▼
   ┌──────────────────────────┐
   │   Arbex backend  :8000   │   mirrors to disk, computes window bias
   └──────────┬───────────────┘
              │
      ┌───────┴────────┐
      ▼                ▼
  Dashboard      Arbitrage engine
  (what you      (confidence adjusted
   see on screen) by at most 0.08)
```

**Why two programs?** Separation of concerns. The models are heavy and slow to start;
the trading platform must restart quickly. Keeping them apart means either can be
restarted without the other, and the trading side degrades gracefully if the news side
is unavailable.

**Graceful degradation.** If the news service stops, everything keeps working: the
dashboard serves the last saved analyses from disk, the status indicator reads "service
degraded," and arbitrage scoring continues with a neutral news bias. Nothing blanks and
nothing crashes.

---

## 13. The numbers, and what they honestly mean

### The model's scorecard

Measured on 57 held-out rows (24 events) it never saw during training:

| Metric | Value | How to read it |
| --- | --- | --- |
| **Direction accuracy** | **61.4%** | vs 33% for random guessing |
| **Macro F1** | **0.594** | it distinguishes all three classes, not just the common one |
| **Magnitude error** | **9.3 bp** | average error on the size of the move |
| **Calibration** | 0.574 | how well confidence matches reality |

### What 61.4% versus 33% means

Three possible answers. Guess blindly and you are right one time in three — **33%**.
That is the floor meaning "learned nothing."

The model reaches **61.4%** on events it never saw. Nearly double chance. Not
spectacular in absolute terms; clearly evidence of something real being learned.

### What 9.3 bp means

That is the average error on the *size* of the predicted move. If it predicts 20 bp,
expect roughly 11–29 bp in reality.

Be candid: the error is the same order of magnitude as the signal. Predictions should be
read as "up, and more strongly than usual" rather than as precise targets.

### Accuracy by time window

| Window | Events tested | Accuracy |
| --- | --- | --- |
| 1 hour | 22 | 59.1% |
| 4 hours | 19 | 52.6% |
| 1 day | 16 | 75.0% |

**Do not build a story on this.** With 16–22 rows per cell, the differences are within
noise. An earlier, smaller version of this dataset showed the *opposite* pattern. Quote
the overall figure and treat the breakdown as indicative only.

### What the model actually outputs in practice

Measured across roughly 1,300 live analyses:

| | Range | Typical |
| --- | --- | --- |
| **Predicted** moves | −18.4 to +22.3 bp | 8.4 bp when non-zero |
| **Real** moves in the training data | −76.7 to +76.1 bp | 12.5 bp average |

Two observations worth making:

**47% of real events fell inside the ±5 bp flat band.** Nearly half of all news moved
the price barely at all. This is why the system says HOLD so often, and why "flat" is a
genuine third outcome rather than a dumping ground.

**The model's range (±20 bp) is much narrower than reality's (±77 bp).** It is
conservative — it will not call the extremes. That is the expected behaviour of a
regularised model trained on limited data: it hedges toward the middle rather than
making dramatic claims it cannot support. If asked why it never predicts large moves,
that is the answer, and it is the safe failure mode to have.

### How this compares with published work

Published research on news-driven market direction reports roughly **55–58%** accuracy.
A 2024 study on EUR/USD directional prediction reports 58.5%. A large language model
tested zero-shot on stock movement reached 55.4%.

**Do not claim to have beaten them.** Those are *binary* tasks (up/down, 50% baseline).
Ours is three-class with a 33% baseline and a large flat class. Different problem, not
comparable.

The correct use of this comparison is to establish the ceiling:

> Published work sits at 55–58% on binary versions of this task. Nobody reports 90%,
> because if news predicted currencies that reliably the opportunity would already have
> been traded away. A result in the 60s is where a serious attempt at this problem
> lands.

---

## 14. Known limitations

State these before being asked. Every one has a known fix.

### The evidence library is also the training set

Retrieval searches the same table the model learned from. For older events, the system
is effectively showing the model its own homework as corroboration.

It is also small — 78 events — which is why relevance scores average 0.24 and the panel
sometimes surfaces an inflation release next to an oil story.

**Fix:** label the ~1,600 already-analysed articles and use those as a separate,
independent evidence library.

### 78 events is a small sample

24 events in the holdout. That is enough to indicate the model learned something; not
enough to state an accuracy figure with confidence.

**Why so few?** Headlines are free and unlimited. What is expensive is joining each one
to the price at exactly the right moment. That join is the bottleneck.

### The surprise formula is non-standard

Currently `(actual − forecast) ÷ |forecast|`.

The problem: a payrolls miss of 96,000 scores 0.60, while a CPI miss of 0.1 percentage
points scores 0.034. In market terms the CPI miss may well be the larger shock. **The
surprise feature is therefore on a different scale for every indicator**, so the model
cannot learn one consistent weight for it.

**Fix:** divide by the historical standard deviation of that indicator's surprises
instead — the method used in the academic literature and in commercial surprise indices.
It expresses everything as "how many standard deviations did this miss by," which is
comparable across indicator types. This is a one-column change and is the cheapest
available improvement.

### Only 35 of 190 rows have a surprise value

Only scheduled releases have a published consensus forecast. The 153 official policy
headlines have nothing to miss, so the field is empty for them.

### The tone model is not FX-aware

FinBERT was trained on company news. It has no concept of quote conventions and will
always read the rupee inversion backwards. The reaction model covers this, but the tone
score should never be presented as directional on its own.

---

## 15. What was recently fixed

Useful to mention, because each is a case of the system being made more honest.

**The news bias was running on tone, not direction.** Until recently, the thirty-minute
bias applied to arbitrage was computed from FinBERT sentiment alone — which, because of
the rupee inversion, was frequently pointing the *wrong way* for the one pair the system
is built around. It now uses the reaction model's output. Verified on real captured
data: a set of tariff and capital-outflow headlines that previously produced "risk off"
now correctly produces "risk on."

**A permanently-on risk flag.** The "high event risk" warning fired on 100% of articles,
because it triggered whenever calendar evidence was retrieved — and calendar evidence is
always retrieved. It now requires a materially surprising release. It fires on 56.9% of
the archive, so it varies and therefore carries information.

**A self-contradicting explanation.** The written rationale used to read *"Moderate
bullish signal: sentiment −0.87"* — technically correct but reading as a bug. It now
leads with the model's prediction and explains the pair convention explicitly.

**A methodology panel that named the wrong model.** The interface reported "k-NN" for
every analysis regardless of what was actually loaded. After retraining selected a
linear model, the panel was misreporting it. It now reads the model type from the file.

**A dashboard that blanked on failure.** One failed request used to clear all five
panels, even though most of them read from local disk. Panels now fail independently.

**Confidence that barely varied.** The evidence term was effectively a constant, so
confidence collapsed into a narrow band. It now reflects genuine retrieval quality.

Importantly, the signal mix — how many BUY, SELL, HOLD and WAIT verdicts the archive
produces — was **unchanged** by these fixes. The corrections improved honesty and
explanation without altering the system's trading behaviour.

---

## 16. Next steps

Ranked by expected impact. **The bottleneck is data, not model architecture.**

**1. Fix the surprise standardisation.** Divide by historical standard deviation rather
than forecast size. One column, grounded in the literature, and plausibly improves
accuracy on its own because it puts a key feature onto a consistent scale.

**2. Label the existing article archive.** ~1,600 articles already carry timestamps.
Attaching the realised price move to each is a batch job, not research. Free intraday FX
history is available from several providers, and archival macro data services provide
"what was known when" snapshots so the model is not accidentally trained on figures that
were revised later.

**3. Separate the evidence library from the training set.** Once step 2 is done, use the
labelled articles for retrieval and keep the calendar table for training. Two corpora,
no overlap.

**4. Improve the window aggregation.** Averaging many sentiment readings tends to
flatten them toward a constant — a documented effect with published remedies based on
learned per-article weighting.

**5. Model and feature work — last.** Add market-state features (recent volatility,
trading session, spread) so the model knows whether conditions are calm or turbulent.
Only then consider a stronger text encoder. With 78 events a larger model will overfit;
this is a data problem wearing a model costume.

---

## 17. Papers to cite

### The foundation — for justifying the approach

**Andersen, T., Bollerslev, T., Diebold, F. & Vega, C. (2003).** "Micro Effects of Macro
Announcements: Real-Time Price Discovery in Foreign Exchange." *American Economic
Review* 93(1), 38–62.

The canonical paper for this entire problem. It establishes that macroeconomic
announcement surprises produce measurable, immediate jumps in exchange rates — the
premise the whole project rests on. It uses standardised surprises, which is the
correction recommended above. It also documents that bad news moves markets more than
good news, a known asymmetry worth mentioning.

> "Our premise follows Andersen et al. (2003), who show that macro announcement
> surprises produce real-time jumps in FX rates. We use their standardised-surprise
> construction as a model feature."

### The benchmark — for defending the accuracy figure

**"Predicting Foreign Exchange EUR/USD direction using machine learning" (2024),**
arXiv:2409.04471. Reports 58.5% directional accuracy.

The closest published comparable. Use it to establish the achievable range rather than
to claim superiority.

### The roadmap — for future work

**FNSPID: A Comprehensive Financial News Dataset in Time Series.** *KDD 2024.*

Aligns 15.7 million news articles to 29.7 million price records — exactly the join
needed on the unlabelled archive, demonstrated at scale and published at a top venue.

> "Our constraint is labelled events. FNSPID demonstrates aligning millions of articles
> to price series; applying that approach to the 1,600 articles already collected turns
> them into training data and simultaneously provides an evidence library independent of
> the training set."

### Held in reserve

- **Fed IFDP 973** — macroeconomic announcements and **emerging market** exchange rates.
  For the question "does this hold for the rupee, not just the majors?"
- **MANA-Net** (arXiv:2409.05698) — on sentiment aggregation flattening out across many
  articles. Cite only if the thirty-minute averaging is challenged.

**Before citing any of these in written work, read them.** The summaries above are
accurate to the best of current knowledge, but a citation you cannot discuss is worse
than no citation.

---

## 18. Running the system

Start in this order:

```
1.  MetaTrader 5      running and logged in
2.  Redis             required for the scoring pipeline
3.  news service      cd ../news_stream && python serve.py       (defaults to port 9000)
4.  Arbex backend     run_server.bat                             (port 8000)
5.  Semantic engine   run_semantic.bat                           (REQUIRED — see below)
6.  Dashboard         cd arbex-web && npm run dev
```

**Step 5 is not optional.** The arbitrage engine publishes opportunities to one internal
channel and the dashboard listens on another. The semantic engine is what connects them.
Without it, no opportunities reach the screen at all.

**If the news service will not start,** skip it. Steps 4–6 still render everything from
the saved local data. The status indicator will read "service degraded" — point this out
as intentional design rather than letting it be discovered.

**Resource note.** Run exactly one instance of each. Do not run a production build of
the dashboard while the services are running; use the development server. Check for
leftover processes before starting.

---

## 19. Anticipated questions

**"Is 61.4% good?"**
Against a 33% baseline, it is nearly double chance, and the macro F1 of 0.594 confirms
it is not achieved by always guessing the most common answer. But it rests on 24
held-out events, so treat it as evidence the approach works rather than as an
established accuracy figure. Published work on this problem sits at 55–58% on easier
binary versions.

**"Why does a negative headline produce a BUY?"**
Quote convention. USD/INR is rupees per dollar, so bad news for the rupee makes the
number rise. FinBERT reads tone and gets this backwards by design — it was trained on
company news. The reaction model was trained on realised USD/INR moves and learned the
inversion from evidence. That is precisely why there are two models rather than one.

**"Isn't your retrieval just returning your training data?"**
Yes, and that is a real limitation. The retrieval mechanism is correct; the library it
searches is too small and overlaps with training. The fix is to label the 1,600 articles
already collected and use those as a separate evidence library.

**"Why not use a large language model?"**
Four reasons: speed (this sits next to a sub-second trading loop), determinism (the same
headline must always produce the same score for reproducible evaluation), cost
(continuous processing), and the fact that a zero-shot LLM on this task scores 55.4% —
no better. The honest caveat is that an LLM would handle the rupee inversion natively
and give better explanations; the right architecture is probably a small fast model in
the live path with an LLM enriching explanations asynchronously.

**"Have you backtested the profit and loss?"**
No, and deliberately so. The system is positioned as pre-trade intelligence; execution
is simulated. A credible profit backtest needs transaction costs, slippage and a fill
model, and 78 events cannot support one. What can be defended is component-level
evaluation: accuracy, error, and calibration on held-out data. Whether it is *profitable*
is a separate question that would be answered badly right now.

**"What happens if the news service goes down mid-demo?"**
Nothing breaks. There is a four-stage degradation path ending in a neutral bias, and
arbitrage scoring continues untouched. The dashboard serves the last saved analyses from
disk, and the status indicator shows the degraded state rather than silently pretending
everything is fine.

**"Why only 190 rows?"**
Because each row requires joining an event to price data at exactly the right moment,
and that join is the constraint — not finding headlines. 190 rows describe 78 real
events, each measured at up to three time windows. Where price data was unavailable the
row was skipped rather than fabricated.

---

## 20. Glossary

| Term | Meaning |
| --- | --- |
| **Arbitrage** | Buying in one place and selling in another simultaneously, profiting from a brief price disagreement |
| **Accuracy** | Percentage of predictions that were correct |
| **Baseline** | The score achievable without learning anything; 33% here |
| **Basis point (bp)** | One hundredth of one percent; 0.0001 |
| **Calibration** | Whether stated confidence matches real-world correctness |
| **Classification** | Predicting which category something belongs to |
| **Confusion matrix** | Table of what was predicted against what actually happened |
| **Consensus forecast** | The average of economists' predictions before a release |
| **Cosine similarity** | A measure of similarity between two lists of numbers, 0 to 1 |
| **Currency pair** | Two currencies priced against each other, e.g. USD/INR |
| **Embedding** | A list of numbers representing the meaning of text |
| **Feature** | One measurable property of a thing, expressed as a number |
| **FinBERT** | A language model trained on financial text to read sentiment |
| **Hashing trick** | Mapping words to fixed numbered slots without needing a dictionary |
| **Holdout** | Data hidden during training, used only for scoring |
| **Horizon** | The time window over which a move is measured |
| **Inference** | Using a trained model to make a prediction on new data |
| **Label** | The correct answer for one training example |
| **Linear model** | A model that multiplies features by weights and adds them up |
| **Macro F1** | A score rewarding good performance across all classes equally |
| **Model** | A mathematical function that turns inputs into a prediction |
| **Overfitting** | Memorising the training data instead of learning general patterns |
| **Quote convention** | Which currency is priced in terms of which |
| **RAG** | Retrieval-Augmented Generation; using retrieved examples to inform a prediction |
| **Regression** | Predicting a number on a continuous scale |
| **Regularisation** | A penalty on large weights that discourages memorisation |
| **Release** | A scheduled publication of an official statistic |
| **Sentiment** | The emotional tone of a piece of text |
| **Surprise** | How far a released number differed from the consensus forecast |
| **Training** | The process of adjusting weights using examples with known answers |
| **Weight** | A number expressing how strongly one feature pushes toward one outcome |

---

*This document describes the system as verified against the running code. Figures come
from the trained model file, the labelled event table, and the live analysis archive.*
