# Reproduction map for the IEEE paper

Source of the research content: commit `639fcdf` (branch `semantic-microservice`). Adapted from
`research/REPRODUCE.md`; the decisions and numbers live in `research/RESEARCH_TRACKER.md` (§6 log,
§8 status board). Run every command from the repository root (the folder that contains
`research/`). Hashes of the cited artifacts are in `artifact_hashes.json` (SHA-256, CRLF
normalised to LF; check with `python -m research.paper.ieee.make_hashes --check`).

## Inputs that are not redistributed

| Input | Why | How to obtain it again |
|---|---|---|
| HistData one-minute bars (EUR/USD, GBP/USD, USD/JPY, USD/CHF, 2018 to 2026) | third-party free data, not ours to redistribute | `python -m research.fetch_histdata` (yearly files; `--months 2026:1-10` for the 2026 monthly files used by C45) |
| GDELT GKG headlines (frozen sample 2019-10..2025-04; C45 sample 2025-05..2026-10) | large third-party data | BigQuery extraction SQL in the tracker (§6, DEVIATION 4) and `research/sql/c45_gkg_extract.sql`; `python -m research.gkg_ingest --project <your project>` or the Colab notebooks in `research/colab/` |
| ForexFactory calendar dump | acquired static file | not redistributed; the rebuilt store is described in tracker §6 (2026-10-24) and its manifest is `artifacts/calendar_events.manifest.json` |
| Live recordings (Kraken and TrueFX quotes, prediction log, shadow rows) | raw recordings, large, indicative feed | not redistributed; the artifacts below are the only outputs derived from them |

The result artifacts are committed, so every table and figure in the paper can be rebuilt from
them without the raw inputs (last section).

## Paper item, command, artifact, gate

| Paper item | Command (`python -m ...`) | Artifact (`research/artifacts/`) | Gate |
|---|---|---|---|
| Positive control (Error 1) | `research.reproduce --run positive_control` | `positive_control.json` | calendar, HistData |
| Error 2 (price-file clock), H2 and H3 table | `research.reproduce --run h2h3` | `h2h3.json` (void pre-fix copy: `h2h3_void_pre_dst_fix.json`) | calendar, HistData |
| Tables RQ1 to RQ4, Fig. reliability | `research.reproduce --run reaction_eval` | `reaction_eval.json`, `reaction_model.json` | calendar, HistData |
| Arrival latency, Fig. decay (b) | `research.reproduce --run arrival_latency` | `arrival_latency.json` | after 2026-11-13 23:59 IST |
| Arrival re-test at the observed offset | `research.reproduce --run arrival_retest` | (none) | not run: no offset estimated (fewer than 10 releases) |
| Arrival-delay decay (C47, exploratory): Table decay, Fig. decay (a) | `research.run_decay` (offsets 0, 1, 2, 5, 10, 15, 30 min; ran on the laptop at 1d256f4, 2026-11-22) | `decay_by_offset.json`, `reaction_eval_arrival{0,1,2,5,10,15,30}.json` | calendar, HistData; check: offset 15 reproduces the registered arrival primary |
| Window30 volatility control (C46, exploratory): Table volctl | `research.run_window30_volctl` (ran in Colab on the frozen sample, freeze `d4ef3d7f`; notebook `research/colab/volctl_pipeline.ipynb` on branch `semantic-microservice`; code 1d256f4) | `window30_volctl_results.json` | same inputs as window30 (frozen sample, calendar, HistData); heavy |
| RQ2-E2E | `research.reproduce --run rq2_e2e` | `rq2_e2e.json` | frozen sample, FinBERT scores |
| H1 novelty | `research.reproduce --run h1_primary` then `h1_decide` | `h1_results.json` | scores stamped with the freeze hash |
| H5 currency factor | `research.reproduce --run h5_lexicon`, `h5_finbert`, `h5_decide` | `h5_results.json` | scores stamped with the freeze hash |
| H6 currency bias | `research.reproduce --run h6` | `h6_results.json` | scores stamped with the freeze hash |
| Thirty-minute window (T1 to T3) | `research.reproduce --run window30` | `window30_results.json` | calendar, HistData, dedup and FinBERT scores |
| C45 replication | `research.run_c45 ingest`, `freeze`, `score`, `test` (notebook: `research/colab/c45_pipeline.ipynb`) | `c45_results.json`, `c45_release_clocks.json` | release clocks fixed before extraction |
| H6b ranking | `research.reproduce --run h6b` | `h6b_results.json` | after the H6b window closed |
| RQ5 extension | `research.reproduce --run rq5_extension` | `rq5_extension_results.json` | after 2026-11-18 00:00 IST |
| Prospective scoring V1, V2.0, V2.1 | `research.reproduce --run live_scoring_v1` (and `_release_v20`, `_release_v21`) | `live_predictions_score_{v1,release_v20,release_v21}.json` | after 2026-11-18 00:00 IST |
| C40 news as risk | `research.reproduce --run c40` | `c40_results.json` | after 2026-11-18 00:00 IST |
| C43 shadow engine | `research.reproduce --run c43` | `c43_results.json` | after 2026-11-18 00:00 IST |
| Downtime table (Appendix B) | none (transcribed from the tracker status board) | `RESEARCH_TRACKER.md` §8 | none |

`research.reproduce --list` shows which steps exist and which are blocked. Steps marked heavy
load price data or transformer models: run them one at a time.

## Rebuilding the paper assets from the committed artifacts

These read committed artifacts only and need no raw data:

```bash
python -m research.paper.ieee.make_decay_figure     # figures/decay.pdf (needs matplotlib)
python -m research.paper.ieee.make_pvalue_ledger    # tables/pledger.tex
python -m research.paper.ieee.make_hashes           # artifact_hashes.json  (--check to verify)
python -m research.paper.ieee.build_numbers --write # number_trace.md; fails if main.tex disagrees
python -m research.paper.ieee.check_paper           # braces, environments, citations, refs, files
python -m pytest research/tests/test_ieee_assets.py
```

The two exploratory runs above were executed on the owner's data machines on 2026-11-22/23 and
their artifacts are committed; the paper reads the artifacts only. Their tracker entries are C46
(pre-registered 2026-11-22, result 2026-11-23) and C47 (entry and result 2026-11-23);
`VOLCTL_PREREG_DRAFT.md` is the superseded draft of C46. The decay figure and its table are built
from `decay_by_offset.json` and `arrival_latency.json`.

## Pre-registration

The protocol is an append-only, version-controlled log (`RESEARCH_TRACKER.md` §2 and §6), not an
external registry. Its timing can be checked only from the repository history.
