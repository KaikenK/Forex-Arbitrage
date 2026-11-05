# 18 Nov 2026 runbook (from 00:00 IST = 2026-11-17 18:30 UTC)

All five steps refuse to run before 00:00 IST. Run them from the worktree root, in this order, one at a time. Log each result in `RESEARCH_TRACKER.md` §6 as read, before running the next step. Do not re-run a step with changed settings; any change needs its own §6 entry first.

```
python -m research.reproduce --list
python -m research.reproduce --run live_scoring_v1
python -m research.reproduce --run live_scoring_release_v20
python -m research.reproduce --run live_scoring_release_v21
python -m research.reproduce --run rq5_extension
python -m research.reproduce --run c40
```

| Step | Reads | Writes (`research/artifacts/`) |
|---|---|---|
| `live_scoring_v1` | prediction log to 2026-11-01 21:09 UTC; Yahoo hourly bars | `live_predictions_score_v1.json` |
| `live_scoring_release_v20` | prediction log, V2 deploy → C38 deploy (2026-11-04 18:37:56 UTC) | `live_predictions_score_release_v20.json` |
| `live_scoring_release_v21` (primary) | prediction log, C38 deploy → 2026-11-18 00:00 IST | `live_predictions_score_release_v21.json` |
| `rq5_extension` | recordings after the H6b window, to 18 Nov 00:00 IST | `rq5_extension_results.json` |
| `c40` | recordings, quote files, calendar archive, prediction log (C39 section) | `c40_results.json` |

After all five are logged, do the following:
1. Switch on the Phase 2 flags (`ARBEX_APPLY_SEMANTIC=risk`, `SEMANTIC_DIRECTION_SOURCE=currency_index`, `SEMANTIC_FX_RELEVANCE_FILTER=1`, and `SEMANTIC_RELEASE_ACTUALS=1` if built). Each one already has its own §6 entry.
2. Restart the services.
3. Log the switch-on time and the recorder gap in §8.
4. Commit the result JSONs on `semantic-microservice`.
