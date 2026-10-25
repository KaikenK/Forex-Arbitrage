"""Precision regression tests for the tagger.

Every "must not tag" case below is a real false positive observed in the GDELT event store.
Mistagged stories inject unrelated price paths into the event study and bias estimates toward
zero, so precision is guarded explicitly.
"""
from __future__ import annotations

import pytest

from research.data.tagging import is_fx_relevant, tag_categories, tag_currencies

# (headline, why it was wrongly tagged before)
FALSE_POSITIVES = [
    ("Championship player Kevin OConnor scoops one million euro in lottery raffle", "euro amount"),
    ("[ PICS ] Dublin hotel sends British blogger invoice for over five million euro", "euro amount"),
    ("No brotherly love : Belgian fans say knocking off rival France would be sweet", "franc in France"),
    ("Three - star Reus targets World Cup after Dortmund leveller - France", "franc in France"),
    ("France confirms Koscielny will miss WC - Article", "franc in France"),
    ("Federal Bureau of Investigation warned about Florida gunman but failed to act", "fed in Federal"),
    ("Triple treat : Tried and true in motorbikes , now in more cars", "spurious"),
    ("Nassau Coliseum | The Vanderbilt | Sanders Equity", "spurious"),
]

TRUE_POSITIVES = [
    ("Dollar Drops Against Yen After BoJ Leaves Rates Unchanged", {"USD", "JPY"}),
    ("Eurozone bond yields , euro rise on ECB jitters - Business News", {"EUR"}),
    ("Forex Analysis - Canada BoC Poloz - Economy will warrant higher rates", {"CAD"}),
    ("Cut & Thrust : RUPEE ROUT sends distress signals", {"INR"}),
    ("Asian markets rally after US gains , yuan edges up", {"CNY"}),
    ("Swiss franc strengthens as SNB holds rates amid market turmoil", {"CHF"}),
]


@pytest.mark.parametrize("headline,reason", FALSE_POSITIVES)
def test_known_false_positives_are_rejected(headline, reason):
    assert tag_currencies(headline) == [], f"should not tag ({reason}): {headline}"


@pytest.mark.parametrize("headline,expected", TRUE_POSITIVES)
def test_genuine_fx_news_still_tagged(headline, expected):
    tagged = set(tag_currencies(headline))
    assert expected <= tagged, f"missed {expected - tagged} in: {headline}"


def test_word_boundary_prevents_substring_matches():
    # "franc" must not fire inside "France"/"franchise"; "fed" not inside "Federal Bureau".
    assert "CHF" not in tag_currencies("France wins the franchise rights", require_context=False)
    assert "USD" not in tag_currencies("Federal Bureau raids office", require_context=False)


def test_fx_context_requirement():
    assert not is_fx_relevant("Player scoops one million euro in lottery")
    assert is_fx_relevant("Euro falls as ECB signals rate cut")
    # A currency word with no market signal is not an FX event.
    assert tag_currencies("She paid five hundred euro for the ticket") == []


def test_iso_codes_matched_on_boundaries():
    assert "USD" in tag_currencies("USD/INR hits record high as markets slide")
    assert "INR" in tag_currencies("USD/INR hits record high as markets slide")


def test_categories_unaffected_by_currency_gating():
    labels = tag_categories("ECB rate decision: 25 bps hike as inflation stays hot")
    assert {"rate_decision", "central_bank", "inflation"} <= set(labels)
