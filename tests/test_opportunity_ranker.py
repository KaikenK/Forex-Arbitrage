"""Tests for OpportunityRanker — both the no-context rank() and rank_with_context()."""

import pytest

from backend.core.arbitrage.arbitrage_engine import ArbitrageOpportunity, ArbitrageType
from backend.core.arbitrage.opportunity_ranker import (
    OpportunityRanker,
    RankingConfig,
    RankingDimension,
)


def _opp(profit_pips=8.0, session="LONDON", buy="reuters", sell="bloomberg",
         confidence=0.7, latency=60.0):
    return ArbitrageOpportunity(
        type=ArbitrageType.CROSS_SOURCE,
        symbols=["USDINR"],
        sources=[buy, sell],
        buy_source=buy,
        sell_source=sell,
        buy_price=86.40,
        sell_price=86.40 + profit_pips * 0.01,
        estimated_profit_pips=profit_pips,
        estimated_profit_pct=profit_pips * 0.01 / 86.4,
        latency_risk_ms=latency,
        confidence_score=confidence,
        session=session,
        timestamp_ms=1_700_000_000_000,
        window_size_ms=200,
    )


@pytest.fixture
def ranker():
    r = OpportunityRanker(RankingConfig(min_composite_score=0.0, max_results=10))
    r.set_source_reliability("bloomberg", 0.98)
    r.set_source_reliability("reuters", 0.95)
    return r


class TestRankNoContext:
    def test_empty_input(self, ranker):
        assert ranker.rank([]) == []

    def test_ranks_and_scores(self, ranker):
        out = ranker.rank([_opp(profit_pips=10.0)])
        assert len(out) == 1
        ro = out[0]
        assert ro.rank == 1
        assert 0.0 <= ro.composite_score <= 100.0
        # every scored dimension uses a real enum member
        for dim in ro.dimension_scores:
            assert isinstance(dim, RankingDimension)
        assert RankingDimension.SESSION_WEIGHT in ro.dimension_scores
        assert RankingDimension.PROFITABILITY in ro.dimension_scores

    def test_more_profit_ranks_higher(self, ranker):
        out = ranker.rank([_opp(profit_pips=2.0, sell="bloomberg", buy="reuters"),
                           _opp(profit_pips=12.0, sell="reuters", buy="bloomberg")])
        assert len(out) == 2
        assert out[0].opportunity.estimated_profit_pips == 12.0
        assert out[0].composite_score > out[1].composite_score
        assert [r.rank for r in out] == [1, 2]

    def test_session_weight_uses_config(self, ranker):
        london = ranker.rank([_opp(session="LONDON")])[0]
        tokyo = ranker.rank([_opp(session="TOKYO")])[0]
        assert (london.dimension_scores[RankingDimension.SESSION_WEIGHT]
                > tokyo.dimension_scores[RankingDimension.SESSION_WEIGHT])

    def test_min_composite_filter(self):
        r = OpportunityRanker(RankingConfig(min_composite_score=99.0))
        assert r.rank([_opp(profit_pips=1.0, confidence=0.1)]) == []


class TestRankWithContext:
    def test_context_promotes_persistent_viable(self, ranker):
        opp = _opp(profit_pips=6.0)
        key = ranker._make_opportunity_key(opp)
        ranked = ranker.rank_with_context(
            [opp],
            persistence_data={key: {"persistence_class": "persistent",
                                    "stability_score": 0.9, "detection_count": 5}},
            execution_assessments={key: {"verdict": "viable", "feasibility_score": 90.0}},
        )
        assert ranked[0].persistence_class == "persistent"
        assert ranked[0].execution_verdict == "viable"
        assert ranked[0].composite_score > 0
