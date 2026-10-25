"""Generate additional forex-focused training rows and append to the training JSONL."""
from __future__ import annotations

import json
import random
from pathlib import Path

OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "usdinr_reaction_training_rows.jsonl"
HORIZONS = (60, 240, 1440)

# Each template: (headline, sentiment_label, base_reaction for USD/INR, event_category, pair)
#   reaction > 0 → USD strengthens vs INR (INR bearish)
#   reaction < 0 → USD weakens vs INR (INR bullish)
USDINR_TEMPLATES: list[tuple[str, float, float, str, str]] = [
    # ═══ Capital flows ═══
    ("FII outflows from Indian equities hit $1.2B this week", -0.7, 0.004, "capital_flows", "USD/INR"),
    ("FII outflows surge to $2.5B in March, worst month in a year", -0.8, 0.006, "capital_flows", "USD/INR"),
    ("Foreign investors pull $800M from Indian bonds in Q1", -0.6, 0.003, "capital_flows", "USD/INR"),
    ("FPI outflows from Indian markets cross $3B in 2024 so far", -0.75, 0.005, "capital_flows", "USD/INR"),
    ("FII selling pressure intensifies as DII buying fails to offset", -0.65, 0.004, "capital_flows", "USD/INR"),
    ("FPI net selling reaches $1.5B in April, highest since October", -0.7, 0.004, "capital_flows", "USD/INR"),
    ("Massive FII outflows weigh on rupee, Sensex drops 600 points", -0.8, 0.006, "capital_flows", "USD/INR"),
    ("FII inflows return to Indian markets, $600M net buy this week", 0.7, -0.003, "capital_flows", "USD/INR"),
    ("Foreign investors pour $2B into Indian equities in February", 0.8, -0.005, "capital_flows", "USD/INR"),
    ("FPI buying accelerates, $1.2B net inflow in first two weeks", 0.7, -0.004, "capital_flows", "USD/INR"),
    ("India sees record FDI inflows of $12B in Q1, boosting rupee", 0.85, -0.005, "capital_flows", "USD/INR"),
    ("Foreign portfolio investors turn net buyers in Indian debt", 0.6, -0.002, "capital_flows", "USD/INR"),

    # ═══ Forex reserves ═══
    ("India's forex reserves drop $5B to $619B, lowest in 6 months", -0.6, 0.004, "reserves", "USD/INR"),
    ("RBI forex reserves fall for fourth straight week, rupee under pressure", -0.65, 0.003, "reserves", "USD/INR"),
    ("India's forex reserves climb to $648B, providing cushion for rupee", 0.7, -0.002, "reserves", "USD/INR"),
    ("India forex reserves hit all-time high of $660B", 0.8, -0.004, "reserves", "USD/INR"),
    ("RBI intervention depletes forex reserves by $8B in March", -0.5, 0.003, "reserves", "USD/INR"),

    # ═══ Trade data ═══
    ("India trade deficit widens to $24B in February on rising oil imports", -0.6, 0.004, "trade_data", "USD/INR"),
    ("India's current account deficit hits 3.5% of GDP in Q3", -0.7, 0.005, "trade_data", "USD/INR"),
    ("India's trade deficit narrows sharply to $15B on falling gold imports", 0.6, -0.003, "trade_data", "USD/INR"),
    ("India posts rare trade surplus as export growth accelerates", 0.75, -0.004, "trade_data", "USD/INR"),
    ("India crude oil import bill surges 25% on high prices, pressure on CAD", -0.55, 0.003, "trade_data", "USD/INR"),
    ("India services exports hit record $30B in Q1, offsetting trade deficit", 0.5, -0.002, "trade_data", "USD/INR"),

    # ═══ RBI decisions ═══
    ("RBI keeps repo rate unchanged at 6.50%, maintains withdrawal of accommodation", -0.1, 0.001, "rate_decision", "USD/INR"),
    ("RBI surprises with 25bp rate cut, repo rate now 6.25%", 0.6, 0.003, "rate_decision", "USD/INR"),
    ("RBI hikes repo rate by 25bp to 6.75%, hawkish stance surprises markets", -0.3, -0.003, "rate_decision", "USD/INR"),
    ("RBI shifts stance to 'neutral' from 'withdrawal', signaling future cuts", 0.4, 0.002, "rate_decision", "USD/INR"),
    ("RBI Governor warns inflation risks tilted upward, rupee steadies", -0.2, -0.001, "central_bank", "USD/INR"),
    ("RBI announces special swap window of $5B to stabilize rupee", 0.5, -0.004, "intervention", "USD/INR"),
    ("RBI sells $3B in spot market to defend rupee at 84 level", 0.3, -0.002, "intervention", "USD/INR"),

    # ═══ India macro ═══
    ("India GDP growth accelerates to 7.8% in Q3, beating estimates", 0.8, -0.004, "macro_data", "USD/INR"),
    ("India GDP growth slows to 5.4% in Q2, worst in 7 quarters", -0.7, 0.005, "macro_data", "USD/INR"),
    ("India manufacturing PMI rises to 58.3, expansion broadens", 0.6, -0.002, "macro_data", "USD/INR"),
    ("India CPI inflation surges to 6.2%, above RBI tolerance band", -0.5, 0.003, "macro_data", "USD/INR"),
    ("India's industrial production growth slumps to 0.5% in December", -0.55, 0.002, "macro_data", "USD/INR"),
    ("India services PMI hits 61.2, job creation at 18-year high", 0.65, -0.003, "macro_data", "USD/INR"),
    ("India fiscal deficit on track at 5.6% of GDP in April-December", 0.3, -0.001, "macro_data", "USD/INR"),
    ("India wholesale inflation turns negative for fourth month", 0.2, -0.001, "macro_data", "USD/INR"),
    ("Sensex crashes 1000 points on global sell-off, rupee weakens past 84", -0.8, 0.005, "macro_data", "USD/INR"),
    ("Rupee hits record low of 84.50 against dollar on broad weakness", -0.9, 0.006, "macro_data", "USD/INR"),
    ("Indian rupee appreciates to 82.80 per dollar on strong FII inflows", 0.7, -0.004, "macro_data", "USD/INR"),

    # ═══ US macro (affecting USD/INR) ═══
    ("US CPI comes in hot at 3.5%, crushing rate cut hopes, dollar rallies", -0.7, 0.005, "macro_data", "USD/INR"),
    ("US non-farm payrolls surge by 303K, smashing expectations", -0.5, 0.003, "macro_data", "USD/INR"),
    ("US unemployment rate ticks up to 4.0%, weakening dollar bid", 0.4, -0.002, "macro_data", "USD/INR"),
    ("US GDP growth slows to 1.6% in Q1, weaker than forecast", 0.5, -0.003, "macro_data", "USD/INR"),
    ("US consumer confidence drops sharply in March survey", 0.35, -0.002, "macro_data", "USD/INR"),
    ("US ISM manufacturing PMI contracts to 49.2, recession fears grow", 0.4, -0.003, "macro_data", "USD/INR"),

    # ═══ Fed decisions ═══
    ("Fed holds rates at 5.25-5.50%, signals higher for longer", -0.6, 0.004, "fed_decision", "USD/INR"),
    ("Fed signals June rate cut likely, dot plot shows 3 cuts in 2024", 0.7, -0.005, "fed_decision", "USD/INR"),
    ("Fed Chair Powell: 'We need greater confidence inflation is moving toward 2%'", -0.3, 0.002, "fed_decision", "USD/INR"),
    ("FOMC minutes reveal divided committee, most members still see cuts this year", 0.3, -0.002, "fed_decision", "USD/INR"),
    ("Fed surprises with 50bp rate cut amid banking sector stress", 0.8, -0.006, "fed_decision", "USD/INR"),
    ("Fed Governor Waller backs gradual rate cuts, dollar steadies", 0.2, -0.001, "fed_decision", "USD/INR"),

    # ═══ Oil / commodities (major input for INR) ═══
    ("Brent crude tops $95 on OPEC+ cuts, pressure on India's import bill", -0.65, 0.005, "commodity", "USD/INR"),
    ("Oil prices crash 5% on surprise US inventory build", 0.5, -0.003, "commodity", "USD/INR"),
    ("Gold surges past $2,400 amid safe-haven demand", -0.2, 0.001, "commodity", "USD/INR"),
    ("Crude oil falls to $75, easing India's trade deficit concerns", 0.6, -0.003, "commodity", "USD/INR"),
    ("OPEC extends output cuts, Brent jumps 3% to $90", -0.5, 0.003, "commodity", "USD/INR"),
    ("Oil hits $100 per barrel for first time since 2022, India's CAD widens sharply", -0.8, 0.007, "commodity", "USD/INR"),
    ("OPEC+ surprises with production increase, Brent drops 8% to $72", 0.7, -0.005, "commodity", "USD/INR"),
    ("India raises windfall tax on crude oil exports by 20%", -0.3, 0.002, "commodity", "USD/INR"),
    ("Gold prices hit $2,800 as central banks diversify reserves away from dollar", 0.1, -0.001, "commodity", "USD/INR"),
    ("Natural gas prices surge 25% on Europe supply crisis, global inflation fears rise", -0.4, 0.003, "commodity", "USD/INR"),

    # ═══ Geopolitical ═══
    ("Middle East tensions escalate, oil prices spike 4%, risk-off in EMs", -0.7, 0.005, "geopolitical", "USD/INR"),
    ("US-China trade tensions flare up, emerging market currencies weaken", -0.6, 0.004, "geopolitical", "USD/INR"),
    ("Global risk sentiment improves as US-China trade deal hopes rise", 0.6, -0.003, "geopolitical", "USD/INR"),
    ("Geopolitical risks subside, emerging market bond inflows resume", 0.5, -0.002, "geopolitical", "USD/INR"),
    ("India-Pakistan border tensions escalate, defense spending pressure on rupee", -0.6, 0.004, "geopolitical", "USD/INR"),
    ("India-China border standoff triggers FII outflows from Indian markets", -0.7, 0.005, "geopolitical", "USD/INR"),
    ("Russia-Ukraine ceasefire talks progress, risk appetite lifts emerging markets", 0.5, -0.003, "geopolitical", "USD/INR"),
    ("New US sanctions on Russia disrupt global energy flows, oil jumps 6%", -0.6, 0.005, "geopolitical", "USD/INR"),
    ("Iran-Israel conflict escalation sends oil above $100, emerging markets sell off", -0.8, 0.007, "geopolitical", "USD/INR"),
    ("North Korea missile test triggers risk-off, safe-haven dollar strengthens vs INR", -0.5, 0.003, "geopolitical", "USD/INR"),
    ("Taiwan strait tensions ease after diplomatic talks, risk assets rally", 0.5, -0.003, "geopolitical", "USD/INR"),
    ("India-US strategic partnership deepens with defense deal worth $8B", 0.4, -0.002, "geopolitical", "USD/INR"),
    ("BRICS summit pushes de-dollarization agenda, dollar faces headwinds", 0.3, -0.002, "geopolitical", "USD/INR"),
    ("Red Sea shipping crisis intensifies, global supply chain costs spike 40%", -0.5, 0.004, "geopolitical", "USD/INR"),
    ("Peace deal in Ukraine lifts global risk appetite, EM currencies rally sharply", 0.7, -0.005, "geopolitical", "USD/INR"),
    ("India-EU free trade agreement finalized, market access improves for Indian exports", 0.5, -0.003, "geopolitical", "USD/INR"),
    ("US imposes sanctions on Indian companies over Russia trade, rupee dips", -0.5, 0.004, "geopolitical", "USD/INR"),

    # ═══ Famous Individuals / Leaders ═══
    ("PM Modi announces Make in India 2.0 with $50B incentive package", 0.6, -0.004, "policy", "USD/INR"),
    ("PM Modi meets President Trump, bilateral trade deal boosts rupee confidence", 0.5, -0.003, "policy", "USD/INR"),
    ("Finance Minister Sitharaman: India on track for 8% GDP growth this year", 0.6, -0.003, "policy", "USD/INR"),
    ("Finance Minister Sitharaman announces surprise income tax cuts in budget", 0.4, -0.002, "policy", "USD/INR"),
    ("RBI Governor Malhotra pledges price stability as top priority in first address", 0.3, -0.001, "central_bank", "USD/INR"),
    ("RBI Governor Das warns rupee depreciation could fuel imported inflation", -0.3, 0.002, "central_bank", "USD/INR"),
    ("Fed Chair Powell signals patience on rate cuts, dollar strengthens broadly", -0.5, 0.004, "fed_decision", "USD/INR"),
    ("Fed Chair Powell: Economy no longer sending signals of imminent recession", -0.3, 0.002, "fed_decision", "USD/INR"),
    ("Trump threatens 25% tariffs on all Indian imports citing trade imbalance", -0.8, 0.007, "geopolitical", "USD/INR"),
    ("Trump calls for weaker dollar, says strong greenback hurting US exports", 0.5, -0.004, "policy", "USD/INR"),
    ("Trump signs executive order imposing new tariffs on Asian imports", -0.6, 0.005, "geopolitical", "USD/INR"),
    ("Trump reverses India tariff threat after bilateral trade talks succeed", 0.6, -0.004, "geopolitical", "USD/INR"),
    ("Elon Musk announces $10B Tesla factory investment in India, FDI boost", 0.7, -0.004, "capital_flows", "USD/INR"),
    ("Elon Musk says India will be Tesla's largest market outside US by 2030", 0.5, -0.003, "capital_flows", "USD/INR"),
    ("Tim Cook: Apple plans to manufacture 50% of iPhones in India by 2026", 0.5, -0.003, "capital_flows", "USD/INR"),
    ("Jeff Bezos announces $15B Amazon expansion in India, logistics and cloud", 0.6, -0.003, "capital_flows", "USD/INR"),
    ("Warren Buffett's Berkshire increases India portfolio allocation to $5B", 0.5, -0.003, "capital_flows", "USD/INR"),
    ("George Soros warns emerging markets face currency crisis as dollar surges", -0.6, 0.004, "risk_sentiment", "USD/INR"),
    ("Janet Yellen warns of global recession risks, safe-haven dollar rallies", -0.5, 0.003, "macro_data", "USD/INR"),
    ("IMF Chief Georgieva praises India's economic resilience, upgrades growth forecast", 0.5, -0.003, "macro_data", "USD/INR"),
    ("World Bank President upgrades India GDP forecast to 7.5% for 2026", 0.5, -0.003, "macro_data", "USD/INR"),
    ("Larry Fink says India is BlackRock's top emerging market bet for the decade", 0.6, -0.003, "capital_flows", "USD/INR"),
    ("Adani Group announces $20B renewable energy investment plan", 0.4, -0.002, "macro_data", "USD/INR"),
    ("Reliance Industries signs $15B deal with Saudi Aramco, FDI flows to India", 0.5, -0.003, "capital_flows", "USD/INR"),
    ("Tata Group wins $11B semiconductor fab contract, India chip ambitions advance", 0.5, -0.003, "capital_flows", "USD/INR"),
    ("Sundar Pichai: Google to invest $10B in India's digital infrastructure", 0.5, -0.003, "capital_flows", "USD/INR"),
    ("Satya Nadella: Microsoft expanding India data centers with $5B investment", 0.5, -0.003, "capital_flows", "USD/INR"),

    # ═══ Elections / Political Events ═══
    ("BJP wins India general elections with landslide majority, markets rally sharply", 0.8, -0.006, "policy", "USD/INR"),
    ("Hung parliament in India elections triggers market sell-off, rupee drops 1.5%", -0.8, 0.007, "policy", "USD/INR"),
    ("India state elections deliver surprise result, policy uncertainty weighs on INR", -0.4, 0.003, "policy", "USD/INR"),
    ("US presidential election uncertainty drives dollar volatility, INR whipsaws", -0.3, 0.002, "policy", "USD/INR"),
    ("Trump wins US presidential election, dollar surges on expected fiscal expansion", -0.6, 0.005, "policy", "USD/INR"),
    ("Democratic sweep in US elections, dollar weakens on stimulus spending expectations", 0.4, -0.003, "policy", "USD/INR"),

    # ═══ Market Crashes / Rallies ═══
    ("Global stock market crash: Dow drops 1500 points, safe-haven dollar surges", -0.9, 0.008, "risk_sentiment", "USD/INR"),
    ("Black Monday for Indian markets: Sensex crashes 2000 points on FII exodus", -0.9, 0.007, "risk_sentiment", "USD/INR"),
    ("Flash crash in Asian currencies, rupee drops 2% in hours as panic spreads", -0.85, 0.008, "risk_sentiment", "USD/INR"),
    ("Global risk rally: emerging market currencies surge as VIX drops below 15", 0.7, -0.005, "risk_sentiment", "USD/INR"),
    ("US banking crisis fears reignite, dollar drops as Fed intervention expected", 0.5, -0.004, "risk_sentiment", "USD/INR"),
    ("Silicon Valley Bank-style contagion fears hit global markets, flight to safety", -0.7, 0.005, "risk_sentiment", "USD/INR"),
    ("Global markets rebound after central banks pledge coordinated support", 0.6, -0.004, "risk_sentiment", "USD/INR"),
    ("Indian stock market circuit breaker triggered after 10% Nifty crash", -0.9, 0.008, "risk_sentiment", "USD/INR"),
    ("EM currency crisis deepens: Turkey, Argentina contagion spreads to India", -0.7, 0.006, "risk_sentiment", "USD/INR"),
    ("Risk-on wave: India attracts $5B in single week as global sentiment flips", 0.8, -0.005, "risk_sentiment", "USD/INR"),

    # ═══ Credit rating / sovereign ═══
    ("S&P upgrades India's outlook to positive from stable", 0.7, -0.004, "sovereign", "USD/INR"),
    ("Moody's warns of potential India downgrade if fiscal slippage persists", -0.7, 0.005, "sovereign", "USD/INR"),
    ("Fitch affirms India at BBB-, cites strong growth fundamentals", 0.4, -0.002, "sovereign", "USD/INR"),
    ("Moody's upgrades India to Baa2, first upgrade in 14 years", 0.8, -0.005, "sovereign", "USD/INR"),
    ("S&P downgrades India outlook to negative citing rising government debt", -0.7, 0.005, "sovereign", "USD/INR"),

    # ═══ DXY / dollar index ═══
    ("Dollar index hits six-month high on hawkish Fed expectations", -0.6, 0.004, "dxy", "USD/INR"),
    ("Dollar index retreats from highs as PCE inflation cools", 0.5, -0.003, "dxy", "USD/INR"),
    ("US dollar weakens broadly on dovish Fed pivot expectations", 0.6, -0.004, "dxy", "USD/INR"),
    ("DXY surges past 107 as US yields jump, EM currencies under pressure", -0.7, 0.005, "dxy", "USD/INR"),
    ("Dollar index crashes below 100 for first time in 2 years as Fed pivots", 0.7, -0.005, "dxy", "USD/INR"),
    ("DXY holds above 105 despite mixed US data, rupee stays under pressure", -0.4, 0.003, "dxy", "USD/INR"),

    # ═══ Bond yields ═══
    ("US 10-year yield surges above 4.7%, strongest dollar in months", -0.6, 0.004, "yields", "USD/INR"),
    ("US treasury yields plunge 20bp on soft jobs data, dollar sells off", 0.6, -0.004, "yields", "USD/INR"),
    ("India-US yield spread narrows to 250bp, reducing carry trade appeal", -0.4, 0.002, "yields", "USD/INR"),
    ("India 10-year bond yield falls below 7% on rate cut speculation", 0.3, 0.001, "yields", "USD/INR"),
    ("US 10-year yield hits 5% for first time since 2007, global sell-off accelerates", -0.7, 0.006, "yields", "USD/INR"),
    ("US yield curve uninverts, recession signal fades, dollar strengthens", -0.3, 0.002, "yields", "USD/INR"),

    # ═══ India Structural / Sector ═══
    ("India becomes world's 3rd largest economy by GDP, overtaking Japan", 0.8, -0.005, "macro_data", "USD/INR"),
    ("India's UPI transactions cross 15 billion monthly, digital payments boom", 0.3, -0.001, "macro_data", "USD/INR"),
    ("India's IT sector revenue crosses $300B, dollar inflows support rupee", 0.5, -0.003, "macro_data", "USD/INR"),
    ("India's renewable energy capacity crosses 200 GW, green investment surges", 0.3, -0.001, "macro_data", "USD/INR"),
    ("India semiconductor fab goes live in Gujarat, reducing chip import dependence", 0.4, -0.002, "macro_data", "USD/INR"),
    ("India's defense exports triple to $5B, strategic autonomy strengthens", 0.3, -0.002, "trade_data", "USD/INR"),
    ("India startup ecosystem raises $30B in 2025, record year for tech funding", 0.4, -0.002, "capital_flows", "USD/INR"),
    ("Major drought hits India, food prices surge and rural demand collapses", -0.7, 0.005, "macro_data", "USD/INR"),
    ("India's banking NPAs drop to 15-year low, credit growth accelerates to 16%", 0.5, -0.003, "macro_data", "USD/INR"),
    ("India auto sales surge 20% YoY, consumer confidence at multi-year high", 0.4, -0.002, "macro_data", "USD/INR"),

    # ═══ US Political / Policy ═══
    ("US government shutdown looms, dollar volatility spikes", -0.3, 0.002, "us_political", "USD/INR"),
    ("US debt ceiling crisis resolved, markets breathe sigh of relief", 0.4, -0.002, "us_political", "USD/INR"),
    ("US-India semiconductor partnership signed, $25B investment corridor created", 0.5, -0.003, "trade_relations", "USD/INR"),
    ("Congress passes $2T stimulus bill, inflation fears push dollar higher initially", -0.3, 0.002, "us_political", "USD/INR"),
]


def _jitter(value: float, pct: float = 0.25) -> float:
    """Add random noise to a value."""
    delta = abs(value) * pct
    return round(value + random.uniform(-delta, delta), 6)


def _make_row(
    article_text: str,
    pair: str,
    horizon: int,
    sentiment_label: float,
    reaction_label: float,
    event_category: str,
    base_date: str,
) -> dict:
    return {
        "article_text": article_text,
        "timestamp": base_date,
        "pair": pair,
        "horizon_minutes": horizon,
        "sentiment_label": round(sentiment_label, 4),
        "reaction_label": _jitter(reaction_label),
        "magnitude_label": round(abs(_jitter(reaction_label)), 6),
        "source_metadata": {
            "source": "forex_domain_expansion",
            "event_category": event_category,
        },
    }


def main() -> None:
    random.seed(42)

    all_templates = USDINR_TEMPLATES
    base_dates = [
        f"2024-{month:02d}-{day:02d}T00:00:00+00:00"
        for month in range(1, 13)
        for day in (5, 15, 25)
    ]

    rows: list[dict] = []
    for idx, (headline, sentiment, reaction, category, pair) in enumerate(all_templates):
        date = base_dates[idx % len(base_dates)]
        for horizon in HORIZONS:
            # Scale reaction by horizon (longer horizon = more movement)
            horizon_scale = {60: 1.0, 240: 1.8, 1440: 3.0}[horizon]
            rows.append(
                _make_row(
                    article_text=headline,
                    pair=pair,
                    horizon=horizon,
                    sentiment_label=_jitter(sentiment, 0.1),
                    reaction_label=reaction * horizon_scale,
                    event_category=category,
                    base_date=date,
                )
            )

    # Append to existing file
    with open(OUTPUT_PATH, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    print(f"Appended {len(rows)} new training rows to {OUTPUT_PATH}")
    print(f"  {len(USDINR_TEMPLATES)} USD/INR templates x 3 horizons = {len(USDINR_TEMPLATES)*3}")


if __name__ == "__main__":
    main()
