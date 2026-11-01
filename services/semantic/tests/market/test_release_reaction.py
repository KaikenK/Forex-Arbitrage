"""Release extractor (headline → calendar release) and the structured release scorer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from market.release_extractor import CalendarRow, headline_numbers, match_release, parse_calendar_value
from market.release_reaction_scorer import ReleaseReactionModel

RELEASE = datetime(2026, 10, 15, 12, 30, tzinfo=UTC)
SIGMA = {("USD", "CPI m/m"): 0.13, ("USD", "CPI y/y"): 0.14, ("USD", "Core CPI m/m"): 0.11,
         ("USD", "Non-Farm Employment Change"): 92_000.0, ("USD", "Unemployment Rate"): 0.12,
         ("USD", "Federal Funds Rate"): 0.1, ("EUR", "CPI Flash Estimate y/y"): 0.15}


def _cal(*rows):
    return [CalendarRow(currency=c, event=e, utc=RELEASE + timedelta(minutes=dt), forecast=f, impact="high")
            for c, e, f, dt in rows]


US_CPI_DAY = _cal(("USD", "CPI m/m", "0.3%", 0), ("USD", "CPI y/y", "3.4%", 0), ("USD", "Core CPI m/m", "0.3%", 0))
US_JOBS_DAY = _cal(("USD", "Non-Farm Employment Change", "140K", 0), ("USD", "Unemployment Rate", "4.2%", 0))


def test_value_parsing():
    assert parse_calendar_value("180K").value == 180_000 and parse_calendar_value("180K").unit == "count"
    assert parse_calendar_value("-0.1%").value == pytest.approx(-0.1)
    assert parse_calendar_value("52.3").unit == ""
    assert parse_calendar_value("") is None
    values = headline_numbers("Payrolls rose 254,000; jobless rate 4.1%")
    assert (254_000, "count") in [(v.value, v.unit) for v in values]
    assert (4.1, "%") in [(v.value, v.unit) for v in values]


def test_cpi_headline_matches_monthly_row_not_annual():
    m = match_release("US consumer prices rise 0.4% in September, above forecasts",
                      RELEASE + timedelta(minutes=4), US_CPI_DAY, SIGMA)
    assert m.event == "CPI m/m" and m.actual == pytest.approx(0.4)
    assert m.z == pytest.approx(0.1 / 0.13) and m.minutes_since_release == pytest.approx(4)


def test_annual_cue_and_core_cue_select_their_rows():
    yoy = match_release("U.S. inflation climbs to 3.6% year-on-year", RELEASE + timedelta(minutes=3), US_CPI_DAY, SIGMA)
    assert yoy.event == "CPI y/y"
    core = match_release("US core CPI rises 0.2%", RELEASE + timedelta(minutes=3), US_CPI_DAY, SIGMA)
    assert core.event == "Core CPI m/m" and core.z == pytest.approx(-0.1 / 0.11)


def test_jobs_headline_takes_the_first_reported_indicator():
    m = match_release("US economy added 254,000 jobs in September; unemployment rate falls to 4.1%",
                      RELEASE + timedelta(minutes=6), US_JOBS_DAY, SIGMA)
    assert m.event == "Non-Farm Employment Change" and m.actual == 254_000


def test_rate_decision_in_line_gives_zero_surprise():
    cal = _cal(("USD", "Federal Funds Rate", "5.50%", 0))
    m = match_release("Fed holds rates steady at 5.25%-5.50%", RELEASE + timedelta(minutes=2), cal, SIGMA)
    assert m.event == "Federal Funds Rate" and m.z == pytest.approx(0.0)


def test_euro_area_headline_and_abstentions():
    cal = _cal(("EUR", "CPI Flash Estimate y/y", "2.5%", 0))
    eur = match_release("Euro zone inflation slows to 2.3% in October", RELEASE + timedelta(minutes=10), cal, SIGMA)
    assert eur.currency == "EUR" and eur.z == pytest.approx(-0.2 / 0.15)
    assert match_release("Stocks rally as investors cheer earnings", RELEASE, US_CPI_DAY, SIGMA) is None
    assert match_release("US consumer prices rise 0.4%", RELEASE + timedelta(hours=3), US_CPI_DAY, SIGMA) is None
    assert match_release("US consumer prices rise sharply", RELEASE + timedelta(minutes=3), US_CPI_DAY, SIGMA) is None


@pytest.fixture(scope="module")
def model() -> ReleaseReactionModel:
    return ReleaseReactionModel.load()


def test_scorer_orients_a_us_beat_per_pair_and_marks_it_priced_in(model):
    m = match_release("US consumer prices rise 0.5% in September, above forecasts",
                      RELEASE + timedelta(minutes=4), US_CPI_DAY, {**SIGMA, **model.sigma})
    scores = model.score(m, ["EUR/USD", "USD/JPY", "GBP/USD", "USD/CHF"])
    assert scores["EUR/USD"]["direction"] == -1 and scores["GBP/USD"]["direction"] == -1   # USD up
    assert scores["USD/JPY"]["direction"] == 1 and scores["USD/CHF"]["direction"] == 1
    p = scores["EUR/USD"]["p_direction"]
    assert 0.5 < p < 1.0
    assert scores["EUR/USD"]["expected_move_bps"] > 0
    lo, hi = scores["EUR/USD"]["move_interval_bps"]["0.9"]
    assert lo <= scores["EUR/USD"]["expected_move_bps"] <= hi
    assert scores["EUR/USD"]["priced_in"] is True and scores["EUR/USD"]["basis"] == "release"


def test_scorer_confidence_rises_with_surprise_and_negative_indicators_flip(model):
    assert model.p_direction(2.0) > model.p_direction(0.3)
    rate = match_release("US unemployment rate rises to 4.5%", RELEASE + timedelta(minutes=2),
                         US_JOBS_DAY, {**SIGMA, **model.sigma})
    scores = model.score(rate, ["EUR/USD"])
    assert scores["EUR/USD"]["direction"] == 1      # higher unemployment = USD weaker = EUR/USD up


def test_cross_pair_gets_move_but_no_direction(model):
    cal = _cal(("EUR", "CPI Flash Estimate y/y", "2.5%", 0))
    m = match_release("Euro zone inflation jumps to 2.9%", RELEASE + timedelta(minutes=5), cal, {**SIGMA, **model.sigma})
    scores = model.score(m, ["EUR/USD", "USD/JPY"])
    assert scores["EUR/USD"]["direction"] == 1
    assert scores["USD/JPY"]["direction"] == 0 and scores["USD/JPY"]["p_direction"] is None
    assert scores["USD/JPY"]["expected_move_bps"] is not None


# ---------------------------------------------------------------- analyzer integration

class _StubCalendar:
    def __init__(self, rows):
        self.rows = rows

    def releases_between(self, start_ts, end_ts):
        from market.economic_calendar import CalendarRelease
        return [CalendarRelease(utc=r.utc.isoformat(), currency=r.currency, event=r.event, impact=r.impact,
                                forecast=r.forecast, previous="", time_known=True, date_local=r.utc.date().isoformat())
                for r in self.rows if start_ts <= r.utc.timestamp() <= end_ts]


def _analyze_with(augmenter, headline, published):
    import asyncio

    from market.analyzer import UnifiedArticleAnalyzer
    from market.empty_evidence_retriever import EmptyEvidenceRetriever
    from market.schemas import ArticleInput
    from market.service import UnifiedArticleAnalysisService
    from tests.market.test_multi_pair_analysis import CountingSentimentScorer, PerPairReactionScorer

    analyzer = UnifiedArticleAnalyzer(sentiment_scorer=CountingSentimentScorer(),
                                      reaction_scorer=PerPairReactionScorer(),
                                      evidence_retriever=EmptyEvidenceRetriever(),
                                      clock=lambda: published, release_augmenter=augmenter)
    article = ArticleInput(article_text=headline, headline=headline, currency_pair="EUR/USD",
                           currency_pairs=["USD/JPY"], published_at=published)
    return asyncio.run(UnifiedArticleAnalysisService(analyzer=analyzer).analyze_article(article))


def test_analyzer_merges_release_fields_and_keeps_v1_fields(model):
    from market.release_reaction_scorer import ReleaseAugmenter

    augmenter = ReleaseAugmenter(_StubCalendar(US_CPI_DAY), model)
    analysis = _analyze_with(augmenter, "US consumer prices rise 0.5% in September, above forecasts",
                             RELEASE + timedelta(minutes=3))
    eur = analysis.pair_scores["EUR/USD"]
    assert eur["basis"] == "release" and eur["release_direction"] == -1
    assert eur["release"]["release_event"] == "CPI m/m" and eur["priced_in"] is True
    assert {"market_score", "confidence", "direction", "magnitude", "horizon_minutes"} <= set(eur)
    assert analysis.pair_scores["USD/JPY"]["release_direction"] == 1


def test_analyzer_news_basis_without_match_and_flag_off_parity(model):
    from market.release_reaction_scorer import ReleaseAugmenter

    augmenter = ReleaseAugmenter(_StubCalendar(US_CPI_DAY), model)
    news = _analyze_with(augmenter, "Stocks rally as investors cheer earnings", RELEASE + timedelta(minutes=3))
    assert news.pair_scores["EUR/USD"]["basis"] == "news" and news.pair_scores["EUR/USD"]["p_direction"] is None
    off = _analyze_with(None, "US consumer prices rise 0.5% in September", RELEASE + timedelta(minutes=3))
    assert "basis" not in off.pair_scores["EUR/USD"] and "release_direction" not in off.pair_scores["EUR/USD"]


# ---------------------------------------------------------------- agency items and feed metadata

def test_agency_item_matches_from_summary_with_source_currency(model):
    from market.release_reaction_scorer import ReleaseAugmenter

    augmenter = ReleaseAugmenter(_StubCalendar(US_CPI_DAY), model)
    headline = "Consumer Price Index - September 2026"
    body = ("The Consumer Price Index for All Urban Consumers (CPI-U) increased 0.5 percent on a seasonally "
            "adjusted basis in September, after rising 0.2 percent in August.")
    published = RELEASE + timedelta(seconds=30)
    assert augmenter.match(headline, published, body=body) is None            # no currency named
    m = augmenter.match(headline, published, body=body, source_url="https://www.bls.gov/news.release/cpi.nr0.htm")
    assert m is not None and m.event == "CPI m/m" and m.actual == pytest.approx(0.5)


def test_feed_events_keep_publisher_time_source_and_summary():
    import time as _time

    from api.routes.feed import _finnhub_to_event, _rss_to_event

    entry = {"title": "US CPI +0.5% vs +0.3% expected", "summary": "<p>Core  0.3%</p>", "link": "https://x/1",
             "published_parsed": _time.struct_time((2026, 10, 15, 12, 30, 5, 3, 288, 0)),
             "_feed_url": "https://investinglive.com/feed/news"}
    event = _rss_to_event(entry)
    assert event["publisher_time"] == "2026-10-15T12:30:05+00:00"
    assert event["feed_url"] == "https://investinglive.com/feed/news" and event["source"] == "RSS (investinglive.com)"
    assert event["summary"] == "Core 0.3%"
    fh = _finnhub_to_event({"headline": "Euro slips", "datetime": 1760531405, "summary": "s", "source": "X"})
    assert fh["publisher_time"].startswith("2025-10-15T") and fh["feed_url"] == "finnhub:news"


def test_calendar_keeps_one_archive_copy_per_week(tmp_path):
    import json as _json

    from market.economic_calendar import EconomicCalendar

    rows = [{"title": "CPI m/m", "country": "USD", "date": "2026-11-12T08:30:00-05:00", "impact": "high",
             "forecast": "0.3%", "previous": "0.2%"}]
    calendar = EconomicCalendar(tmp_path, fetch=lambda url: _json.dumps(rows).encode(), clock=lambda: 1.8e9)
    assert calendar.refresh(force=True)
    assert [p.name for p in (tmp_path / "archive").iterdir()] == ["ff_calendar_2026-W46.json"]
