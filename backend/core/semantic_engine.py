import asyncio
import logging
import time
from typing import Dict, Any

from backend.core.redis_client import redis_client
from backend.core.arbitrage.arbitrage_engine import ArbitrageOpportunity, ArbitrageType
from backend.core.arbitrage.opportunity_ranker import OpportunityRanker
from backend.core.arbitrage.opportunity_tracker import OpportunityTracker
from backend.core.execution.execution_filter import SimulatedExecutionFilter

logger = logging.getLogger(__name__)

class SemanticEngine:
    """
    Standalone microservice that listens for raw arbitrage opportunities,
    tracks their persistence, evaluates execution feasibility, and
    broadcasts scored opportunities.
    """
    def __init__(self):
        self._tracker = OpportunityTracker()
        self._filter = SimulatedExecutionFilter()
        self._ranker = OpportunityRanker()
        self._is_running = False

    async def start(self):
        self._is_running = True
        logger.info("Starting SemanticEngine microservice...")
        
        # Subscribe to raw opportunities from Redis
        await redis_client.subscribe("arbex.raw_opps", self.process_raw_opp)
        
        # Keep alive loop
        while self._is_running:
            await asyncio.sleep(1)
            
    async def stop(self):
        self._is_running = False
        logger.info("Stopping SemanticEngine...")

    async def process_raw_opp(self, raw_msg: Dict[str, Any]):
        """
        Process a raw opportunity payload from Redis.
        """
        try:
            logger.info(f"SemanticEngine received raw opportunity: {raw_msg.get('event_id')}")
            opp_dict = raw_msg.get("opportunity")
            if not opp_dict:
                return
                
            # Reconstruct ArbitrageOpportunity
            # Ensure type is converted back to enum
            opp_dict_copy = opp_dict.copy()
            opp_dict_copy.pop("id", None) # Remove properties from dict
            if "type" in opp_dict_copy:
                try:
                    opp_dict_copy["type"] = ArbitrageType(opp_dict_copy["type"])
                except ValueError:
                    pass
                    
            opp = ArbitrageOpportunity(**opp_dict_copy)
            
            # Step 1: Track persistence
            tracked = self._tracker.update(opp)
            
            # Step 2: Assess execution
            assessment = self._filter.assess(tracked)
            
            # Step 3: Rank
            persistence_data = {
                tracked.key: {
                    "class": tracked.persistence_class.value,
                    "stability_score": tracked.stability_score,
                    "detection_count": tracked.detection_count,
                    "first_seen_ts": tracked.first_seen_ts,
                    "duration_ms": tracked.cumulative_duration_ms,
                    "persistence_class": tracked.persistence_class.value, # for ranker compatibility
                }
            }
            
            execution_data = {
                tracked.key: {
                    "verdict": assessment.execution_verdict.value,
                    "feasibility_score": assessment.execution_feasibility_score,
                    "expected_slippage_pips": assessment.expected_slippage_pips,
                    "verdict_reasons": assessment.verdict_reasons,
                }
            }
            
            ranked = self._ranker.rank_with_context(
                opportunities=[opp],
                persistence_data=persistence_data,
                execution_assessments=execution_data
            )
            
            if ranked:
                best = ranked[0]
                
                # Publish scored opportunity to Redis
                scored_msg = {
                    "event_id": raw_msg.get("event_id"),
                    "timestamp": time.time(),
                    "opportunity": best.opportunity.to_dict(),
                    "composite_score": best.composite_score,
                    "dimension_scores": best.dimension_scores,
                    "ranking_reason": best.ranking_reason,
                    "rank": best.rank,
                    "persistence": persistence_data[tracked.key],
                    "execution": execution_data[tracked.key]
                }
                
                await redis_client.publish("arbex.scored_opps", scored_msg)
                
        except Exception as e:
            logger.error(f"SemanticEngine Error processing raw opportunity: {e}")

async def main():
    logging.basicConfig(level=logging.INFO)
    engine = SemanticEngine()
    try:
        await engine.start()
    except KeyboardInterrupt:
        await engine.stop()

if __name__ == "__main__":
    asyncio.run(main())
