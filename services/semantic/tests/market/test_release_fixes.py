"""Engine update C38 (2026-11-04): release headlines whose number or sign was read wrong."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from market.release_extractor import CalendarRow, find_numbers, match_release
from market.release_reaction_scorer import ReleaseReactionModel

RELEASE = datetime(2026, 11, 14, 13, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def model() -> ReleaseReactionModel:
    return ReleaseReactionModel.load()


@pytest.mark.parametrize("text, expected", [
    ("US retail sales fall 0.3% in October", [-0.3]),
    ("Retail sales fell by 0.3% m/m", [-0.3]),
    ("Industrial output declined 1.2% vs 0.4% expected", [-1.2, 0.4]),
    ("UK unemployment rate falls to 4.1%", [4.1]),            # a level: keeps its sign
    ("Retail sales rise 0.3%, after falling 0.2%", [0.3, -0.2]),
    ("Sales fell -0.3%", [-0.3]),                              # already signed: not flipped twice
    ("Payrolls fell 25,000 in October", [-25_000.0]),
])
def test_a_falling_verb_makes_the_number_negative(text, expected):
    assert [n.value for n in find_numbers(text)] == pytest.approx(expected)


def test_a_fall_below_forecast_is_a_negative_surprise(model):
    calendar = [CalendarRow(currency="USD", event="Retail Sales m/m", utc=RELEASE, forecast="0.2%", impact="high")]
    match = match_release("US retail sales fall 0.3% in October", RELEASE + timedelta(minutes=3), calendar,
                          {("USD", "Retail Sales m/m"): 0.5})
    assert match is not None and match.actual == pytest.approx(-0.3) and match.z == pytest.approx(-1.0)


@pytest.mark.parametrize("currency, event", [
    ("GBP", "Unemployment Rate"), ("EUR", "Unemployment Rate"), ("EUR", "German Unemployment Change"),
    ("CHF", "Unemployment Rate"), ("JPY", "Unemployment Rate"), ("USD", "Continuing Jobless Claims"),
])
def test_unemployment_type_releases_are_bad_news_when_higher_everywhere(model, currency, event):
    assert model.news_sign(currency, event) == -1


def test_the_exported_research_signs_are_kept(model):
    for currency, event in model.negative:
        assert model.news_sign(currency, event) == -1
    for currency, event in model.ambiguous:
        assert model.news_sign(currency, event) is None
    assert model.news_sign("USD", "Non-Farm Employment Change") == 1
    assert model.news_sign("CAD", "Employment Change") == 1


def test_p_direction_never_falls_as_the_surprise_grows(model):
    grid = [i / 10 for i in range(0, 51)]
    p = [model.p_direction(z) for z in grid]
    assert all(b >= a - 1e-12 for a, b in zip(p, p[1:]))
    assert model.p_direction(5.0) == pytest.approx(model.p_direction(2.3), abs=1e-3)
    assert model.p_direction(-3.0) == model.p_direction(3.0)
