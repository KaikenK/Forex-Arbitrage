"""Generates a realistic labeled training JSONL for reaction model training.

Uses varied forex articles across multiple pairs, horizons, event categories,
and directional outcomes to produce a dataset large enough for meaningful
time-based train/validation/test splits.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "reaction_training_rows.jsonl"
SEED = 42
NUM_ROWS = 200

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF", "NZD/USD", "EUR/GBP"]
HORIZONS = [15, 30, 60, 120]
SOURCES = ["RSS", "FINNHUB", "REDDIT"]

EVENT_CATEGORIES = ["central-bank", "macro", "geopolitical", "corporate", "trade"]
MACRO_EVENT_TYPES = [
    "rate-decision", "inflation", "jobs", "growth", "minutes",
    "guidance", "trade-balance", "retail-sales", "housing", "pmi",
]

HEADLINE_TEMPLATES: list[tuple[str, str, float, float]] = [
    # (template, category, sentiment_bias, magnitude_bias)
    # Central bank hawkish
    ("{cb} signals higher rates, {pair} reacts", "central-bank", 0.7, 0.35),
    ("{cb} unexpectedly raises rates by 50bps", "central-bank", 0.85, 0.50),
    ("{cb} holds rates but strikes hawkish tone in statement", "central-bank", 0.5, 0.25),
    ("{cb} minutes reveal split vote with hawkish dissent", "central-bank", 0.4, 0.20),
    # Central bank dovish
    ("{cb} cuts rates by 25bps citing slowing growth", "central-bank", -0.6, 0.30),
    ("{cb} signals more easing ahead as inflation undershoots", "central-bank", -0.7, 0.40),
    ("{cb} unexpectedly dovish, markets reprice rate path", "central-bank", -0.8, 0.45),
    ("{cb} holds but drops tightening bias from statement", "central-bank", -0.5, 0.22),
    # Inflation
    ("Hot CPI print pushes {pair} lower as rate expectations shift", "macro", -0.5, 0.30),
    ("Core inflation undershoots consensus, {pair} rallies", "macro", 0.6, 0.28),
    ("Inflation surprises to the upside at 4.2% year-over-year", "macro", -0.4, 0.25),
    ("Consumer prices fall for the first time in 18 months", "macro", 0.5, 0.20),
    ("{country} PPI rises sharply, raising pipeline inflation concerns", "macro", -0.3, 0.18),
    # Jobs / labor
    ("Nonfarm payrolls blow past expectations at 350K", "macro", 0.7, 0.35),
    ("Unemployment rate rises to 4.5%, highest since 2021", "macro", -0.6, 0.30),
    ("Soft payrolls weaken the dollar as {pair} climbs", "macro", -0.4, 0.22),
    ("Initial jobless claims spike to 280K, labor market cooling", "macro", -0.5, 0.25),
    ("Wage growth accelerates to 5.1%, keeping pressure on {cb}", "macro", 0.4, 0.20),
    # Growth / PMI
    ("{country} PMI falls into contraction territory at 48.2", "macro", -0.6, 0.28),
    ("{country} GDP beats at 3.2% annualized, {pair} rallies", "macro", 0.65, 0.32),
    ("Eurozone PMIs disappoint, {pair} trades lower in early reaction", "macro", -0.4, 0.24),
    ("Manufacturing PMI surges to 54.7, services sector also strong", "macro", 0.5, 0.22),
    ("Retail sales plunge 1.3% as consumers pull back spending", "macro", -0.5, 0.25),
    ("{country} Q3 GDP contracts 0.2%, recession fears mount", "macro", -0.7, 0.38),
    # Trade / geopolitical
    ("Trade deficit widens to record $98B as imports surge", "trade", -0.3, 0.15),
    ("New tariffs on {country} goods rattle currency markets", "geopolitical", -0.5, 0.30),
    ("Trade deal breakthrough lifts risk sentiment, {pair} benefits", "geopolitical", 0.6, 0.28),
    ("Escalating tensions in Middle East push safe havens higher", "geopolitical", -0.4, 0.25),
    ("Sanctions threat on {country} sends {pair} sharply lower", "geopolitical", -0.7, 0.35),
    # Neutral / mixed
    ("{cb} meeting passes without surprises, {pair} range-bound", "central-bank", 0.05, 0.05),
    ("Markets consolidate ahead of tomorrow's key data releases", "macro", 0.0, 0.03),
    ("{pair} holds steady as traders await {cb} decision", "central-bank", 0.02, 0.04),
    ("Mixed signals from {country} data keep {pair} directionless", "macro", -0.05, 0.06),
]

BODY_FRAGMENTS = [
    "Market participants are now pricing in a {pct}% probability of a rate change at the next meeting.",
    "The move came after the data showed {metric} {direction} from the prior reading.",
    "Analysts at major banks revised their forecasts following the release.",
    "The reaction was swift, with {pair} moving {pips} pips within the first hour.",
    "Traders noted thin liquidity conditions amplified the initial move.",
    "Options markets showed increased demand for {pair} puts after the announcement.",
    "The yield spread between {c1} and {c2} widened by {bps} basis points.",
    "Risk reversals shifted sharply, indicating growing demand for downside protection.",
    "The move challenges the prevailing consensus that had expected a more muted response.",
    "Technical levels at {level} provided initial support but were quickly breached.",
    "Positioning data suggests speculative accounts were caught offside by the release.",
    "Volatility spiked with 1-week implied vol on {pair} jumping by {vol_jump}%.",
]

CB_MAP = {
    "EUR/USD": ("ECB", "Eurozone"),
    "GBP/USD": ("BoE", "UK"),
    "USD/JPY": ("Fed", "US"),
    "AUD/USD": ("RBA", "Australia"),
    "USD/CHF": ("SNB", "Switzerland"),
    "NZD/USD": ("RBNZ", "New Zealand"),
    "EUR/GBP": ("ECB", "Eurozone"),
}


def _generate_body(pair: str, direction: str) -> str:
    c1, c2 = pair.split("/")
    frags = random.sample(BODY_FRAGMENTS, k=min(4, len(BODY_FRAGMENTS)))
    body_parts = []
    for frag in frags:
        text = frag.format(
            pair=pair,
            pct=random.randint(20, 90),
            metric=random.choice(["CPI", "GDP", "employment", "PMI", "retail sales"]),
            direction=direction,
            pips=random.randint(15, 120),
            c1=c1,
            c2=c2,
            bps=random.randint(2, 25),
            level=f"{random.uniform(0.9, 1.6):.4f}",
            vol_jump=f"{random.uniform(0.5, 4.0):.1f}",
        )
        body_parts.append(text)
    return " ".join(body_parts)


def generate_rows() -> list[dict]:
    random.seed(SEED)
    rows = []
    base_time = datetime(2025, 6, 1, 8, 0, 0, tzinfo=timezone.utc)

    for i in range(NUM_ROWS):
        pair = random.choice(PAIRS)
        horizon = random.choice(HORIZONS)
        source = random.choice(SOURCES)
        cb, country = CB_MAP.get(pair, ("Fed", "US"))

        template, category, sentiment_bias, magnitude_bias = random.choice(HEADLINE_TEMPLATES)
        macro_type = random.choice(MACRO_EVENT_TYPES)

        headline = template.format(pair=pair, cb=cb, country=country)

        # Add noise to sentiment and reaction
        noise = random.gauss(0, 0.15)
        sentiment = max(-1.0, min(1.0, sentiment_bias + noise))

        # Reaction correlates with sentiment but has its own noise
        reaction_noise = random.gauss(0, 0.1)
        reaction = max(-1.0, min(1.0, sentiment_bias * 0.8 + reaction_noise))

        magnitude = max(0.01, abs(magnitude_bias + random.gauss(0, 0.08)))

        direction_word = "higher" if reaction > 0 else "lower"
        body = _generate_body(pair, direction_word)

        timestamp = base_time + timedelta(
            hours=i * random.randint(4, 12),
            minutes=random.randint(0, 59),
        )

        rows.append({
            "article_text": f"{headline}\n{body}",
            "timestamp": timestamp.isoformat(),
            "pair": pair,
            "horizon_minutes": horizon,
            "sentiment_label": round(sentiment, 4),
            "reaction_label": round(reaction, 4),
            "magnitude_label": round(magnitude, 4),
            "source_metadata": {
                "event_id": f"train-{i+1:04d}",
                "source": source,
                "event_category": category,
                "macro_event_type": macro_type,
            },
        })

    # Sort by timestamp for temporal consistency
    rows.sort(key=lambda r: r["timestamp"])
    return rows


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = generate_rows()
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print(f"Wrote {len(rows)} training rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
