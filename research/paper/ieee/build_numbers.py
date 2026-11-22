"""Trace table for the headline numbers in main.tex, diffed against the .tex.

Each entry says: this text, as written in main.tex, is what this artifact key formats to. The
script computes the text from the committed JSON artifact (or checks that a tracker passage
contains it) and fails if main.tex says something else, or if main.tex does not contain the
text at all. Rounding is half-up on the decimal repr of the stored value.

    python -m research.paper.ieee.build_numbers            # check; exit 1 on any mismatch
    python -m research.paper.ieee.build_numbers --write    # also write number_trace.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
ARTIFACTS = RESEARCH / "artifacts"
TRACKER = RESEARCH / "RESEARCH_TRACKER.md"
TEX = HERE / "main.tex"
TRACE_OUT = HERE / "number_trace.md"


# ---------------------------------------------------------------- formatting

def q(x: float, n: int) -> str:
    return str(Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP))


def pct(x: float, n: int = 1) -> str:
    return q(float(x) * 100, n)


def comma(n: int) -> str:
    return f"{int(n):,}"


def dot3(x: float) -> str:
    """-.016 / +.004 (three decimals, no leading zero, explicit sign)."""
    s = q(x, 3)
    sign = "-" if s.startswith("-") else "+"
    return sign + s.lstrip("-").lstrip("0")


def sci(x: float) -> str:
    mantissa, exponent = f"{x:.1e}".split("e")
    return f"{float(mantissa):.1f}\\times10^{{{int(exponent)}}}"


def ci(lo: float, hi: float, n: int = 1, scale: float = 100.0) -> str:
    return f"[{q(lo * scale, n)}, {q(hi * scale, n)}]"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- entries

@dataclass
class Entry:
    tex: str                       # text as written in main.tex
    artifact: str                  # artifact file (or RESEARCH_TRACKER.md)
    key: str                       # JSON key path, for the trace table
    expected: Callable[[dict], str] | None = None   # artifact -> text; None = tracker passage check
    tracker: str | None = None     # passage that must occur in the tracker


def _load(name: str) -> dict:
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


class Loader(dict):
    def __missing__(self, name: str) -> dict:
        self[name] = _load(name)
        return self[name]


def build_entries() -> list[Entry]:
    E: list[Entry] = []

    def add(tex: str, artifact: str, key: str, fn: Callable[[Loader], str]) -> None:
        E.append(Entry(tex, artifact, key, fn))

    def tr(tex: str, passage: str, key: str = "") -> None:
        E.append(Entry(tex, "RESEARCH_TRACKER.md", key or passage, None, passage))

    RE = "reaction_eval.json"
    T = lambda A, k: A[RE]["targets"][k]  # noqa: E731

    # --- direction table
    for target, label in (("release_1m", "release 0--1"), ("release_60m", "release 0--60"),
                          ("release_120m", "release 0--120"), ("arrival_15+15m", "arrival 15--30"),
                          ("arrival_15+105m", "arrival 15--120")):
        def fn(A, target=target, label=label):
            d = T(A, target)["direction"]
            return (f"{label} & {pct(d['model']['accuracy'])} & {pct(d['theory_rule']['accuracy'])} "
                    f"& {pct(d['majority']['accuracy'])}")
        add(fn(Loader()), RE, f"targets.{target}.direction.{{model,theory_rule,majority}}.accuracy", fn)
    for target, label in (("release_15m", "release 0--15$^\\dagger$"), ("arrival_15+45m", "arrival 15--60$^\\dagger$")):
        def fn(A, target=target, label=label):
            d = T(A, target)["direction"]
            return (f"{label} & {pct(d['model']['accuracy'])} {ci(*d['model']['ci95'])} & "
                    f"{pct(d['theory_rule']['accuracy'])} {ci(*d['theory_rule']['ci95'])} & {pct(d['majority']['accuracy'])}")
        add(fn(Loader()), RE, f"targets.{target}.direction.*.accuracy,ci95", fn)

    # --- abstract and direction text
    add("68.4\\% of the time (95\\% interval 65.4 to 71.3, 1,385", RE, "targets.release_15m.direction.theory_rule.{accuracy,ci95}",
        lambda A: f"{pct(T(A, 'release_15m')['direction']['theory_rule']['accuracy'])}\\% of the time (95\\% interval "
                  f"{q(T(A, 'release_15m')['direction']['theory_rule']['ci95'][0] * 100, 1)} to "
                  f"{q(T(A, 'release_15m')['direction']['theory_rule']['ci95'][1] * 100, 1)}, 1,385")
    add("a learned model 63.5\\%", RE, "targets.release_15m.direction.model.accuracy",
        lambda A: f"a learned model {pct(T(A, 'release_15m')['direction']['model']['accuracy'])}\\%")
    add("48.8\\% (46.6 to 51.0)", RE, "targets.arrival_15+45m.direction.model.{accuracy,ci95}",
        lambda A: f"{pct(T(A, 'arrival_15+45m')['direction']['model']['accuracy'])}\\% ("
                  f"{q(T(A, 'arrival_15+45m')['direction']['model']['ci95'][0] * 100, 1)} to "
                  f"{q(T(A, 'arrival_15+45m')['direction']['model']['ci95'][1] * 100, 1)})")
    add("(58\\%, 65\\%, 68\\% across terciles)", RE, "targets.release_15m.direction.by_abs_z_tercile[*].accuracy",
        lambda A: "(" + ", ".join(f"{pct(t['accuracy'], 0)}\\%" for t in T(A, "release_15m")["direction"]["by_abs_z_tercile"])
                  + " across terciles)")
    add("The two agree on 82\\% of rows", RE, "targets.release_15m.direction.model_agrees_with_theory",
        lambda A: f"The two agree on {pct(T(A, 'release_15m')['direction']['model_agrees_with_theory'], 0)}\\% of rows")
    add("primary window ($p=0.27$)", RE, "targets.arrival_15+45m.direction.model.p_vs_coin",
        lambda A: f"primary window ($p={q(T(A, 'arrival_15+45m')['direction']['model']['p_vs_coin'], 2)}$)")

    # --- horizon profile and decay text
    P = lambda A, k: A[RE]["horizon_profile"][k]  # noqa: E731
    add("is 7.2\\bps{} in the first minute", RE, "horizon_profile.r1.mean",
        lambda A: f"is {q(P(A, 'r1')['mean'], 1)}\\bps{{}} in the first minute")
    add("between 7.1 and 8.3\\bps{}", RE, "horizon_profile.r{1..120}.mean (min, max)",
        lambda A: "between " + q(min(P(A, f"r{h}")["mean"] for h in (1, 5, 15, 30, 60, 120)), 1) + " and "
                  + q(max(P(A, f"r{h}")["mean"] for h in (1, 5, 15, 30, 60, 120)), 1) + "\\bps{}")
    add("It is $-0.14$\\bps{} (95\\% interval $-0.80$ to $+0.52$)", RE, "horizon_profile.a45.{mean,se} (mean +/- 1.96 se)",
        lambda A: f"It is ${q(P(A, 'a45')['mean'], 2)}$\\bps{{}} (95\\% interval ${q(P(A, 'a45')['mean'] - 1.96 * P(A, 'a45')['se'], 2)}$ to "
                  f"${'+' + q(P(A, 'a45')['mean'] + 1.96 * P(A, 'a45')['se'], 2)}$)")

    # --- magnitude
    def mag(A, k):
        return T(A, k)["magnitude"]
    add("Spearman 0.29 against 0.19 and 0.24", RE, "targets.release_15m.magnitude.{model,pair_mean,cell_mean}.spearman",
        lambda A: f"Spearman {q(mag(A, 'release_15m')['model']['spearman'], 2)} against "
                  f"{q(mag(A, 'release_15m')['pair_mean']['spearman'], 2)} and {q(mag(A, 'release_15m')['cell_mean']['spearman'], 2)}")
    add("(12.01 against 11.54\\bps{}, difference $+0.47$, $p=0.15$)", RE, "targets.release_15m.magnitude.pair_mean.{mae_bps,model_minus_baseline_abs_error}",
        lambda A: f"({q(mag(A, 'release_15m')['model']['mae_bps'], 2)} against {q(mag(A, 'release_15m')['pair_mean']['mae_bps'], 2)}\\bps{{}}, "
                  f"difference $+{q(mag(A, 'release_15m')['pair_mean']['model_minus_baseline_abs_error']['mean'], 2)}$, "
                  f"$p={q(mag(A, 'release_15m')['pair_mean']['model_minus_baseline_abs_error']['p'], 2)}$)")
    add("(7.11 against 7.40\\bps, $p<0.001$)", RE, "targets.arrival_15+45m.magnitude.{v_fit,model}.mae_bps, v_fit.model_minus_baseline_abs_error.p",
        lambda A: f"({q(mag(A, 'arrival_15+45m')['v_fit']['mae_bps'], 2)} against {q(mag(A, 'arrival_15+45m')['model']['mae_bps'], 2)}\\bps, "
                  f"$p<0.001$)" if mag(A, 'arrival_15+45m')['v_fit']['model_minus_baseline_abs_error']['p'] < 0.001 else "p not < 0.001")
    for target, label in (("release_15m", "release 0--15$^\\dagger$"), ("arrival_15+45m", "arrival 15--60$^\\dagger$")):
        def fn(A, target=target, label=label):
            m = mag(A, target)
            cell = lambda k: f"{q(m[k]['mae_bps'], 2)} / {q(m[k]['spearman'], 2)}"  # noqa: E731
            vfit = cell("v_fit")
            if target.startswith("arrival"):
                vfit = f"\\textbf{{{q(m['v_fit']['mae_bps'], 2)}}} / {q(m['v_fit']['spearman'], 2)}"
            return f"{label} & {cell('model')} & {cell('pair_mean')} & {cell('cell_mean')} & {vfit}"
        add(fn(Loader()), RE, f"targets.{target}.magnitude.*.{{mae_bps,spearman}}", fn)

    # --- confidence
    for target, label in (("release_1m", "release 0--1"), ("release_15m", "release 0--15$^\\dagger$"),
                          ("arrival_15+45m", "arrival 15--60$^\\dagger$")):
        def fn(A, target=target, label=label):
            c = T(A, target)["confidence"]
            return (f"{label} & {q(c['ece'], 3)} & {q(c['brier'], 3)} & {q(c['brier_constant_rate'], 3)} & "
                    f"{pct(c['coverage_80']['coverage'])} & {pct(c['coverage_90']['coverage'])}")
        add(fn(Loader()), RE, f"targets.{target}.confidence.{{ece,brier,brier_constant_rate,coverage_80,coverage_90}}", fn)
    add("0.234 against 0.232 for a constant rate", RE, "targets.release_15m.confidence.{brier,brier_constant_rate}",
        lambda A: f"{q(T(A, 'release_15m')['confidence']['brier'], 3)} against {q(T(A, 'release_15m')['confidence']['brier_constant_rate'], 3)} for a constant rate")
    add("(0.207 against 0.217)", RE, "targets.release_1m.confidence.{brier,brier_constant_rate}",
        lambda A: f"({q(T(A, 'release_1m')['confidence']['brier'], 3)} against {q(T(A, 'release_1m')['confidence']['brier_constant_rate'], 3)})")

    # --- RQ1
    add("recalls 90\\% of abnormal moves at 41\\% precision (F1 0.56)", RE, "rq1_affected.rule.{recall,precision,f1}",
        lambda A: f"recalls {pct(A[RE]['rq1_affected']['rule']['recall'], 0)}\\% of abnormal moves at "
                  f"{pct(A[RE]['rq1_affected']['rule']['precision'], 0)}\\% precision (F1 {q(A[RE]['rq1_affected']['rule']['f1'], 2)})")
    add("41\\% of own-pair rows but 19\\% of cross-pair rows", RE, "rq1_affected.{affected_rate_own,affected_rate_cross}",
        lambda A: f"{pct(A[RE]['rq1_affected']['affected_rate_own'], 0)}\\% of own-pair rows but "
                  f"{pct(A[RE]['rq1_affected']['affected_rate_cross'], 0)}\\% of cross-pair rows")
    add("about 22\\%", RE, "rq1_affected.by_release_currency.EUR.{GBP/USD,USD/JPY} (mean)",
        lambda A: "about " + pct((A[RE]["rq1_affected"]["by_release_currency"]["EUR"]["GBP/USD"]
                                  + A[RE]["rq1_affected"]["by_release_currency"]["EUR"]["USD/JPY"]) / 2, 0) + "\\%")

    # --- H2 table and H3
    H = "h2h3.json"
    for cat in ("inflation", "employment", "growth"):
        def fn(A, cat=cat):
            cells = {c["horizon"]: c for c in A[H]["h2"]["cells"] if c["category"] == cat}
            star = lambda h: f"{q(cells[h]['car_bps'], 1)}$^{{*}}$" if cells[h]["survives"] else q(cells[h]["car_bps"], 1)  # noqa: E731
            return f"{cat} & {star(1)} & {star(15)} & {star(60)} & {star(120)} & {cells[1]['clusters']}"
        add(fn(Loader()), H, f"h2.cells[category={cat}].{{car_bps,survives,clusters}}", fn)
    add("undefined in 98--100\\% of bootstrap draws", H, "h3.half_life.*.undefined_share (min, max)",
        lambda A: f"undefined in {pct(min(v['undefined_share'] for v in A[H]['h3']['half_life'].values()), 0)}--"
                  f"{pct(max(v['undefined_share'] for v in A[H]['h3']['half_life'].values()), 0)}\\% of bootstrap draws")

    # --- arrival latency
    L = "arrival_latency.json"
    add("19 article--feed rows for 9 releases", L, "rows, releases",
        lambda A: f"{A[L]['rows']} article--feed rows for {A[L]['releases']} releases")
    add("37.5 minutes per row and 23.1 minutes per release", L, "receipt_latency.median_min, effective_latency.median_min",
        lambda A: f"{q(A[L]['receipt_latency']['median_min'], 1)} minutes per row and {q(A[L]['effective_latency']['median_min'], 1)} minutes per release")
    add("quartiles 6.7 and 37.5", L, "effective_latency.{p25_min,p75_min}",
        lambda A: f"quartiles {q(A[L]['effective_latency']['p25_min'], 1)} and {q(A[L]['effective_latency']['p75_min'], 1)}")
    add("44\\% of releases had an article within 15 minutes", L, "effective_latency.within_15_min",
        lambda A: f"{pct(A[L]['effective_latency']['within_15_min'], 0)}\\% of releases had an article within 15 minutes")
    add("only 2 of 10 high-impact calendar events, so its coverage is 20\\%", L, "coverage.{matched,calendar_events,coverage}",
        lambda A: f"only {A[L]['coverage']['matched']} of {A[L]['coverage']['calendar_events']} high-impact calendar events, so its coverage is "
                  f"{pct(A[L]['coverage']['coverage'], 0)}\\%")
    add("receipt median 37.5 minutes, effective median 23.1 minutes, 44\\% of releases", L, "receipt/effective median, within_15",
        lambda A: f"receipt median {q(A[L]['receipt_latency']['median_min'], 1)} minutes, effective median "
                  f"{q(A[L]['effective_latency']['median_min'], 1)} minutes, {pct(A[L]['effective_latency']['within_15_min'], 0)}\\% of releases")
    add("the median effective article delay was 23 minutes over 9 measured releases", L, "effective_latency.{median_min,n}",
        lambda A: f"the median effective article delay was {q(A[L]['effective_latency']['median_min'], 0)} minutes over "
                  f"{A[L]['effective_latency']['n']} measured releases")

    # --- window30
    W = "window30_results.json"
    t30 = lambda A, k: A[W]["tests"][k]  # noqa: E731
    add("0.043 at 30 minutes ($q=6.1\\times10^{-9}$) and 0.048 at 60 minutes ($q=1.5\\times10^{-10}$), on 258,237 and 259,081 rows", W,
        "tests.T2_activity_{30,60}.{beta,q_bh,n}",
        lambda A: f"{q(t30(A, 'T2_activity_30')['beta'], 3)} at 30 minutes ($q={sci(t30(A, 'T2_activity_30')['q_bh'])}$) and "
                  f"{q(t30(A, 'T2_activity_60')['beta'], 3)} at 60 minutes ($q={sci(t30(A, 'T2_activity_60')['q_bh'])}$), on "
                  f"{comma(t30(A, 'T2_activity_30')['n'])} and {comma(t30(A, 'T2_activity_60')['n'])} rows")
    add("(hit rate 50.0\\%, $p=0.95$ at 30 minutes and $0.74$ at 60)", W, "tests.T1_direction_{30,60}.{hit_rate,p}",
        lambda A: f"(hit rate {pct(t30(A, 'T1_direction_30')['hit_rate'])}\\%, $p={q(t30(A, 'T1_direction_30')['p'], 2)}$ at 30 minutes and "
                  f"${q(t30(A, 'T1_direction_60')['p'], 2)}$ at 60)")
    add("at 60 minutes ($q=0.044$)", W, "tests.T3_caution_60.q_bh",
        lambda A: f"at 60 minutes ($q={q(t30(A, 'T3_caution_60')['q_bh'], 3)}$)")
    add("(1,445 clusters)", W, "tests.T2_activity_30.n_dates", lambda A: f"({comma(t30(A, 'T2_activity_30')['n_dates'])} clusters)")
    for h, label in ((30, "& 30 &"), (60, "& 60 &")):
        def fn(A, h=h, label=label):
            t = t30(A, f"T2_activity_{h}")
            return f"{label} {comma(t['n'])} & {comma(t['n_dates'])} & {q(t['beta'], 4)} & {q(t['se'], 4)} & ${sci(t['p'])}$"
        add(fn(Loader()), W, f"tests.T2_activity_{h}.{{n,n_dates,beta,se,p}}", fn)

    # --- C45
    C = "c45_results.json"
    for h, label in ((30, "& 30 &"), (60, "& 60$^\\dagger$ &")):
        def fn(A, h=h, label=label):
            t = A[C]["T2_clock"][f"h{h}"]
            p = f"${sci(t['p'])}$" if t["p"] < 0.001 else q(t["p"], 4)
            return f"{label} {comma(t['n'])} & {comma(t['n_dates'])} & {q(t['beta'], 4)} & {q(t['se'], 4)} & {p}"
        add(fn(Loader()), C, f"T2_clock.h{h}.{{n,n_dates,beta,se,p}}", fn)
    add("the 60-minute coefficient is 0.0405 ($p=0.003$)", C, "T2_clock.h60.{beta,p}",
        lambda A: f"the 60-minute coefficient is {q(A[C]['T2_clock']['h60']['beta'], 4)} ($p={q(A[C]['T2_clock']['h60']['p'], 3)}$)")
    add("(0.0405, $p=0.003$)", C, "T2_clock.h60.{beta,p}",
        lambda A: f"({q(A[C]['T2_clock']['h60']['beta'], 4)}, $p={q(A[C]['T2_clock']['h60']['p'], 3)}$)")
    add("(rank IC $-0.008$ and $-0.005$; hit rates 49.4\\% and 49.8\\%)", C, "reported_only.T1_direction_{30,60}.{ic,hit_rate}",
        lambda A: f"(rank IC ${q(A[C]['reported_only']['T1_direction_30']['ic'], 3)}$ and ${q(A[C]['reported_only']['T1_direction_60']['ic'], 3)}$; "
                  f"hit rates {pct(A[C]['reported_only']['T1_direction_30']['hit_rate'])}\\% and {pct(A[C]['reported_only']['T1_direction_60']['hit_rate'])}\\%)")
    add("(IC 0.000, $p=1.00$)", C, "reported_only.T3_caution_60.{ic,p}",
        lambda A: f"(IC {q(abs(A[C]['reported_only']['T3_caution_60']['ic']), 3)}, $p={q(A[C]['reported_only']['T3_caution_60']['p'], 2)}$)")
    add("A second sample of 18 months (2025-05 to 2026-10; 399,810 tagged events, 250,509 canonical)", C, "events, canonical, settings.sample",
        lambda A: f"A second sample of 18 months ({A[C]['settings']['sample'][0][:7].replace('-', '-')} to {A[C]['settings']['sample'][1][:7]}; "
                  f"{comma(A[C]['events'])} tagged events, {comma(A[C]['canonical'])} canonical)")
    add("(2025-05 to 2026-10; 40,196\nand 34,556 rows; 370 UTC dates)".replace("\n", " "), C, "settings.sample, T2_clock.h{30,60}.n, n_dates",
        lambda A: f"({A[C]['settings']['sample'][0][:7]} to {A[C]['settings']['sample'][1][:7]}; {comma(A[C]['T2_clock']['h30']['n'])} "
                  f"and {comma(A[C]['T2_clock']['h60']['n'])} rows; {A[C]['T2_clock']['h30']['n_dates']} UTC dates)")

    # --- H1, H5, H6
    H1 = lambda A: A["h1_results.json"]["sentence_transformer"]["majors"]["finbert_baseline"]  # noqa: E731
    add("On 243,360 rows (234,273 events, 1,717 dates)", "h1_results.json", "sentence_transformer.majors.finbert_baseline.{n_rows,n_events,n_dates}",
        lambda A: f"On {comma(H1(A)['n_rows'])} rows ({comma(H1(A)['n_events'])} events, {comma(H1(A)['n_dates'])} dates)")
    add("($\\beta=-0.968$, SE 0.279, $p=0.0005$)", "h1_results.json", "...finbert_baseline.{novelty_beta,novelty_se,novelty_p}",
        lambda A: f"($\\beta={q(H1(A)['novelty_beta'], 3)}$, SE {q(H1(A)['novelty_se'], 3)}, $p={q(H1(A)['novelty_p'], 4)}$)")
    add("(89.0017 to 88.9892)", "h1_results.json", "...finbert_baseline.{oos_mse_baseline,oos_mse_novelty}",
        lambda A: f"({q(H1(A)['oos_mse_baseline'], 4)} to {q(H1(A)['oos_mse_novelty'], 4)})")
    add("improves by 0.01\\%", "h1_results.json", "...finbert_baseline.{oos_mse_baseline,oos_mse_novelty}",
        lambda A: f"improves by {q((1 - H1(A)['oos_mse_novelty'] / H1(A)['oos_mse_baseline']) * 100, 2)}\\%")
    H1F = "h1_results.json"
    add("The lexicon baseline agrees ($\\beta=-0.937$, $p=0.0007$)", H1F, "sentence_transformer.majors.lexicon_baseline_robustness.{novelty_beta,novelty_p}",
        lambda A: (lambda d: f"The lexicon baseline agrees ($\\beta={q(d['novelty_beta'], 3)}$, $p={q(d['novelty_p'], 4)}$)")(
            A[H1F]["sentence_transformer"]["majors"]["lexicon_baseline_robustness"]))
    add("The USD/INR arm (101,176 events) is not significant ($\\beta=-10.4$, $p=0.54$)", H1F, "sentence_transformer.usd_inr_secondary.{exclusions.events_after,result.novelty_beta,result.novelty_p}",
        lambda A: (lambda d: f"The USD/INR arm ({comma(d['exclusions']['events_after'])} events) is not significant "
                             f"($\\beta={q(d['result']['novelty_beta'], 1)}$, $p={q(d['result']['novelty_p'], 2)}$)")(A[H1F]["sentence_transformer"]["usd_inr_secondary"]))
    add("gives 76 test months", RE, "targets.release_15m.test_months",
        lambda A: f"gives {T(A, 'release_15m')['test_months']} test months")
    H5 = lambda A, b: A["h5_results.json"][b]["primary"]  # noqa: E731
    add("(R$^2$ 0.0073 against $-0.0006$; Spearman 0.123 against $-0.045$; loss-difference $p=0.003$)", "h5_results.json",
        "lexicon.primary.a.{oos_r2_implied,oos_r2_tagged,spearman_implied,spearman_tagged,loss_diff_p}",
        lambda A: f"(R$^2$ {q(H5(A, 'lexicon')['a']['oos_r2_implied'], 4)} against ${q(H5(A, 'lexicon')['a']['oos_r2_tagged'], 4)}$; "
                  f"Spearman {q(H5(A, 'lexicon')['a']['spearman_implied'], 3)} against ${q(H5(A, 'lexicon')['a']['spearman_tagged'], 3)}$; "
                  f"loss-difference $p={q(H5(A, 'lexicon')['a']['loss_diff_p'], 3)}$)")
    add("VIX $p=0.14$, USD/JPY $p=0.80$, USD/CHF with the wrong sign\n($p=0.002$)".replace("\n", " "), "h5_results.json",
        "lexicon.primary.b.{vix_change,ret_USD/JPY,ret_USD/CHF}.p",
        lambda A: f"VIX $p={q(H5(A, 'lexicon')['b']['vix_change']['p'], 2)}$, USD/JPY $p={q(H5(A, 'lexicon')['b']['ret_USD/JPY']['p'], 2)}$, "
                  f"USD/CHF with the wrong sign ($p={q(H5(A, 'lexicon')['b']['ret_USD/CHF']['p'], 3)}$)")
    add("passes only the VIX component\n($p=0.026$)".replace("\n", " "), "h5_results.json", "finbert.primary.b.vix_change.p",
        lambda A: f"passes only the VIX component ($p={q(H5(A, 'finbert')['b']['vix_change']['p'], 3)}$)")
    H6 = lambda A: A["h6_results.json"]["h6a_primary_daily"]["pair_bias"]  # noqa: E731
    add("On 5,006 daily rows over 1,455 dates", "h6_results.json", "h6a_primary_daily.{rows_with_target,pair_bias.criterion_i.n_dates}",
        lambda A: f"On {comma(A['h6_results.json']['h6a_primary_daily']['rows_with_target'])} daily rows over {comma(H6(A)['criterion_i']['n_dates'])} dates")
    add("the pair-bias coefficient is $0.095$ ($p=0.89$), out-of-sample MSE is 2,270.5 against 2,268.4", "h6_results.json",
        "h6a_primary_daily.pair_bias.{criterion_i.beta,criterion_i.p,criterion_ii.mse_full,criterion_ii.mse_baseline}",
        lambda A: f"the pair-bias coefficient is ${q(H6(A)['criterion_i']['beta'], 3)}$ ($p={q(H6(A)['criterion_i']['p'], 2)}$), "
                  f"out-of-sample MSE is {format(float(q(H6(A)['criterion_ii']['mse_full'], 1)), ',.1f')} against "
                  f"{format(float(q(H6(A)['criterion_ii']['mse_baseline'], 1)), ',.1f')}")
    add("the hit rate is 49.4\\%", "h6_results.json", "h6a_primary_daily.pair_bias.criterion_ii.hit_rate_full",
        lambda A: f"the hit rate is {pct(H6(A)['criterion_ii']['hit_rate_full'])}\\%")
    R2 = "rq2_e2e.json"
    add("Of 2,410 candidate headlines (535 release moments) only\n6.6\\% match a calendar release and 4.7\\% carry the correct actual figure.".replace("\n", " "), R2,
        "candidates, candidate_moments, extraction.{matched,correct_actual}",
        lambda A: f"Of {comma(A[R2]['candidates'])} candidate headlines ({A[R2]['candidate_moments']} release moments) only "
                  f"{pct(A[R2]['extraction']['matched'])}\\% match a calendar release and {pct(A[R2]['extraction']['correct_actual'])}\\% carry the correct actual figure.")
    add("structured 41.6\\%, FinBERT 51.6\\% [48.5, 54.4], the earlier text model 53.5\\%\n[49.1, 57.7]".replace("\n", " "), R2,
        "direction.article_time_r60.{structured,finbert,v1_text_model}",
        lambda A: (lambda d: f"structured {pct(d['structured']['accuracy'])}\\%, FinBERT {pct(d['finbert']['accuracy'])}\\% {ci(*d['finbert']['ci95'])}, "
                             f"the earlier text model {pct(d['v1_text_model']['accuracy'])}\\% {ci(*d['v1_text_model']['ci95'])}")(A[R2]["direction"]["article_time_r60"]))

    # --- H6b / RQ5 table and text
    HB = "h6b_results.json"
    RQ5 = "rq5_extension_results.json"

    def rank_row(A, label, rows_days, rk):
        auc, cis = rk["auc"], rk["ci95"]
        def cell(a):
            d = cis[f"{a}_minus_detector"]
            return f"{q(auc[a], 3)} [${dot3(d[0])},{dot3(d[1])}$]"
        return (f"{label} ({rows_days}) & {q(auc['detector'], 3)} & {cell('plus_risk')} & {cell('plus_risk_direction')} & {cell('plus_window')}")
    add(rank_row(Loader(), "H6b locked", "36,668 / 6", Loader()[HB]["locked_window"]["ranking"]), HB, "locked_window.ranking.{auc,ci95}",
        lambda A: rank_row(A, "H6b locked", f"{comma(A[HB]['locked_window']['rows'])} / {A[HB]['locked_window']['days']}", A[HB]["locked_window"]["ranking"]))
    add(rank_row(Loader(), "H6b clean", "29,456 / 4", Loader()[HB]["clean_window"]["ranking"]), HB, "clean_window.ranking.{auc,ci95}",
        lambda A: rank_row(A, "H6b clean", f"{comma(A[HB]['clean_window']['rows'])} / {A[HB]['clean_window']['days']}", A[HB]["clean_window"]["ranking"]))
    add(rank_row(Loader(), "RQ5", "916 / 3", Loader()[RQ5]["rq5_extension"]["ranking"]), RQ5, "rq5_extension.ranking.{auc,ci95}",
        lambda A: rank_row(A, "RQ5", f"{comma(A[RQ5]['rq5_extension']['rows'])} / {A[RQ5]['rq5_extension']['days']}", A[RQ5]["rq5_extension"]["ranking"]))
    add("(by\n0.003 to 0.012)".replace("\n", " "), HB, "{locked,clean,clean_minus_resends}.ranking.auc.{plus_risk,plus_risk_direction} - detector (min, max abs)",
        lambda A: (lambda diffs: f"(by {q(min(diffs), 3)} to {q(max(diffs), 3)})")(
            [abs(A[HB][s]["ranking"]["auc"][k] - A[HB][s]["ranking"]["auc"]["detector"])
             for s in ("locked_window", "clean_window", "clean_window_minus_resends") for k in ("plus_risk", "plus_risk_direction")]))
    add("a mean of 2.3--2.4 pips (median 1.8--1.9)", HB, "*.realised_edge_pips.{mean,median} (min, max)",
        lambda A: (lambda secs: f"a mean of {q(min(s['mean'] for s in secs), 1)}--{q(max(s['mean'] for s in secs), 1)} pips "
                                f"(median {q(min(s['median'] for s in secs), 1)}--{q(max(s['median'] for s in secs), 1)})")(
            [A[HB][s]["realised_edge_pips"] for s in ("locked_window", "clean_window", "clean_window_minus_resends")]))
    add("Most crosses persist (97.8--98.3\\%), so each H6b AUC rests on 448 to 790 negatives", HB, "*.ranking.{positives,n}",
        lambda A: (lambda secs: f"Most crosses persist ({pct(min(s['positives'] / s['n'] for s in secs))}--"
                                f"{pct(max(s['positives'] / s['n'] for s in secs))}\\%), so each H6b AUC rests on "
                                f"{min(s['n'] - s['positives'] for s in secs)} to {max(s['n'] - s['positives'] for s in secs)} negatives")(
            [A[HB][s]["ranking"] for s in ("locked_window", "clean_window", "clean_window_minus_resends")]))
    add("with 42 negatives and a mean edge of 3.07 pips", RQ5, "rq5_extension.ranking.{n,positives}, realised_edge_pips.mean",
        lambda A: f"with {A[RQ5]['rq5_extension']['ranking']['n'] - A[RQ5]['rq5_extension']['ranking']['positives']} negatives and a mean edge of "
                  f"{q(A[RQ5]['rq5_extension']['realised_edge_pips']['mean'], 2)} pips")

    # --- prospective scoring
    V1, V20, V21 = ("live_predictions_score_v1.json", "live_predictions_score_release_v20.json", "live_predictions_score_release_v21.json")
    def prosp(A, name, label, tail=None):
        d = A[name]
        acc = f"{pct(d['accuracy'])} {ci(*d['accuracy_ci95'])}" if tail is None else tail
        return (f"{label} & {d['rows_logged']} & {d['rows_scored']} & {d['date_clusters']} & {acc} & ${q(d['ic_spearman'], 2)}$ & {q(d['ece'], 2)}"
                if d["ic_spearman"] < 0 else
                f"{label} & {d['rows_logged']} & {d['rows_scored']} & {d['date_clusters']} & {acc} & {q(d['ic_spearman'], 2)} & {q(d['ece'], 2)}")
    add(prosp(Loader(), V1, "V1 text"), V1, "rows_logged,rows_scored,date_clusters,accuracy,accuracy_ci95,ic_spearman,ece", lambda A: prosp(A, V1, "V1 text"))
    add(prosp(Loader(), V20, "V2.0 release").replace("100.0", "100"), V20, "rows_logged,rows_scored,date_clusters,accuracy,accuracy_ci95,ic_spearman,ece",
        lambda A: prosp(A, V20, "V2.0 release").replace("100.0", "100"))
    add(prosp(Loader(), V21, "V2.1 release", tail="21 of 23 (4 events)"), V21, "rows_logged,rows_scored,date_clusters,ic_spearman,ece (accuracy = 21 of 23)",
        lambda A: prosp(A, V21, "V2.1 release", tail=f"{round(A[V21]['accuracy'] * A[V21]['rows_scored'])} of {A[V21]['rows_scored']} (4 events)"))
    add("($p=0.55$ against a coin)", V20, "p_vs_coin", lambda A: f"($p={q(A[V20]['p_vs_coin'], 2)}$ against a coin)")
    add("The V1 text model scored 44.4\\% on 124 scored rows, with 4\ndate clusters".replace("\n", " "), V1, "accuracy, rows_scored, date_clusters",
        lambda A: f"The V1 text model scored {pct(A[V1]['accuracy'])}\\% on {A[V1]['rows_scored']} scored rows, with {A[V1]['date_clusters']} date clusters")
    add("The V2.0 release model scored 61.5\\% on 26 rows from 3 date clusters", V20, "accuracy, rows_scored, date_clusters",
        lambda A: f"The V2.0 release model scored {pct(A[V20]['accuracy'])}\\% on {A[V20]['rows_scored']} rows from {A[V20]['date_clusters']} date clusters")
    add("The V2.1 release model got 21 of 23 rows right", V21, "accuracy x rows_scored",
        lambda A: f"The V2.1 release model got {round(A[V21]['accuracy'] * A[V21]['rows_scored'])} of {A[V21]['rows_scored']} rows right")
    add("Live expected calibration error was 0.22\nto 0.30 for all three models".replace("\n", " "), V1, "ece (min, max over V1, V2.0, V2.1)",
        lambda A: f"Live expected calibration error was {q(min(A[n]['ece'] for n in (V1, V20, V21)), 2)} to "
                  f"{q(max(A[n]['ece'] for n in (V1, V20, V21)), 2)} for all three models")

    # --- C40
    C40 = "c40_results.json"
    c40 = lambda A, k: A[C40]["main"]["tests"][k]  # noqa: E731
    add("of 0.495 [0.462, 0.551]", C40, "main.tests.C40-2.{auc_not_still_crossed,auc_ci95}",
        lambda A: f"of {q(c40(A, 'C40-2')['auc_not_still_crossed'], 3)} [{q(c40(A, 'C40-2')['auc_ci95'][0], 3)}, {q(c40(A, 'C40-2')['auc_ci95'][1], 3)}]")
    add("gave 0.531 [0.425, 0.552] on 30,351 opportunities over 6 days ($q=0.44$)", C40, "main.tests.C40-3.{auc_not_still_crossed,auc_ci95,n,n_days,q_bh}",
        lambda A: f"gave {q(c40(A, 'C40-3')['auc_not_still_crossed'], 3)} [{q(c40(A, 'C40-3')['auc_ci95'][0], 3)}, {q(c40(A, 'C40-3')['auc_ci95'][1], 3)}] on "
                  f"{comma(c40(A, 'C40-3')['n'])} opportunities over {c40(A, 'C40-3')['n_days']} days ($q={q(c40(A, 'C40-3')['q_bh'], 2)}$)")
    add("only 3\nwindows with opportunities exist against 30 required".replace("\n", " "), C40, "main.tests.C40-1.release_windows_with_opportunity",
        lambda A: f"only {c40(A, 'C40-1')['release_windows_with_opportunity']} windows with opportunities exist against 30 required")
    add("had an AUC of 0.551 with $p=0.0005$ on 914 rows and 42 vanished crosses", C40, "main.reported_only.C40-3_rq5_extension_part.{auc_not_still_crossed,p_one_sided,n,vanished}",
        lambda A: (lambda d: f"had an AUC of {q(d['auc_not_still_crossed'], 3)} with $p={q(d['p_one_sided'], 4)}$ on {d['n']} rows and {d['vanished']} vanished crosses")(
            A[C40]["main"]["reported_only"]["C40-3_rq5_extension_part"]))
    add("sample is 8 days on a degraded venue", C40, "main.days", lambda A: f"sample is {A[C40]['main']['days']} days on a degraded venue")

    # --- C43
    C43 = "c43_results.json"
    add("recorded 318 rows over 2 days, with 18 not still crossed", C43, "evaluable_rows, a_adjusted_vs_detector.{n_days,not_still_crossed}",
        lambda A: f"recorded {A[C43]['evaluable_rows']} rows over {A[C43]['a_adjusted_vs_detector']['n_days']} days, with "
                  f"{A[C43]['a_adjusted_vs_detector']['not_still_crossed']} not still crossed")
    add("an AUC of 0.863 against 0.844 for the detector", C43, "a_adjusted_vs_detector.auc.{adjusted,detector}",
        lambda A: f"an AUC of {q(A[C43]['a_adjusted_vs_detector']['auc']['adjusted'], 3)} against {q(A[C43]['a_adjusted_vs_detector']['auc']['detector'], 3)} for the detector")
    add("removed 83\\% of the window's items (998 of 1,200\nitem slots over 221 rows with items)".replace("\n", " "), C43, "d_filter_removal.{pooled_share_removed,items_removed,items_kept,rows}",
        lambda A: (lambda d: f"removed {pct(d['pooled_share_removed'], 0)}\\% of the window's items ({d['items_removed']} of "
                             f"{comma(d['items_removed'] + d['items_kept'])} item slots over {d['rows']} rows with items)")(A[C43]["d_filter_removal"]))
    add("agreed on 88\\% of rows", C43, "c_direction_agreement.agree_share",
        lambda A: f"agreed on {pct(A[C43]['c_direction_agreement']['agree_share'], 0)}\\% of rows")

    # --- tracker facts (data section, live setup, downtime, V2.1 inputs)
    tr("1,385 high-impact", "1,385 releases with z ≠ 0", "RQ1–RQ4 RESULTS")
    tr("34,333 events", "34,333 events, 23,636 timed, 22,517 standardized")
    tr("23,636 with a usable time and 22,517 with a standardized surprise", "23,636 timed, 22,517 standardized")
    tr("3,690,637\nextracted, 1,576,465 currency-tagged".replace("\n", " "), "3,690,637 rows read = the extraction total**, 1,576,465 currency-tagged")
    tr("1,569,940 events; 955,570 canonical headlines", "1,569,940 events", "freeze")
    tr("955,570 canonical headlines", "1,562,129 → 955,570 canonical headlines")
    tr("37,604 cross-venue opportunities from 25~Oct to 6~Nov 2026", "37,604 opportunities (25 Oct to 6 Nov UTC)")
    tr("13,311 of the\n14,486 logged predictions (92\\%)".replace("\n", " "), "Of 14,486 rows in `live_predictions.jsonl`")
    tr("13,311 of the 14,486 logged predictions (92\\%)", "**13,311 (92%) are synthetic**")
    tr("190 of 743 USD/INR training rows", "190 of the 743 USD/INR rows")
    tr("8,355 repeated prediction rows", "8,355 of them repeating an earlier headline")
    tr("5,071 rows from 2023 onward were one hour early", "(5,071 rows;")
    tr("all 88 payrolls", "88/88 payrolls")
    tr("$\\beta=-8.99$", "β = −8.99 bps/σ, p = 0.010")
    tr("$p=0.010$", "p = 0.010")
    tr("$+6.51$, $p\\approx10^{-11}$", "pooled oriented 5m +6.51, p = 1e-11")
    tr("placebo $p=0.33$", "(c) placebo p = 0.33")
    tr("$+0.5$\\bps{} on the documented clock and $+8.5$\\bps{}", "summer +8.5 bps at 1m")
    tr("88\\% of kept headlines", "88% of kept headlines fall 23:00–01:00 UTC")
    tr("169-day sample", "in a 169-day sample")
    tr("median daily span is 0.2~h", "median per-day span 0.2 h")
    tr("12 distinct headlines about 4\nreleases over 2 days".replace("\n", " "), "12 distinct headlines about 4 releases")
    tr("In the\nfirst eight analyses after the 5~Nov deployment the median total analysis time was 90\\,ms".replace("\n", " ").replace("In the first", "first"),
       "first 8\n  analyses: total p50 90 ms".replace("\n  ", " "))
    tr("01:19:47 IST on 7~Nov 2026", "last quote 01:19:47 IST")
    tr("about 10.9 days", "about 10.9 days of the registered live window")
    tr("8.65\\,h", "8.65 h")
    tr("16,620\\,s", "Total 16,620 s")
    tr("10,644\\,s", "(10,644 s)")
    tr("134.6\\,s", "134.6 s in total")
    tr("84.6\\,s", "84.6 s")
    tr("4.78\\,h", "4.78 h lost")
    tr("3.39\\,h", "3.39 h, 12,208.1 s")
    tr("2.27\\,h", "2.27 h, 8,183.9 s")
    tr("3.10\\,h", "3.10 h, owner away")
    tr("13.6\\,s gap", "13.6 s (largest gap")
    tr("12.9\\,s", "12.9 s")
    tr("8.8\\,s", "8.8 s")
    tr("1.7\\,s gap", "1.7 s")
    return [e for e in E if e is not None]


# ---------------------------------------------------------------- check

def run(tex_path: Path = TEX, tracker_path: Path = TRACKER) -> tuple[list[str], list[tuple[Entry, str]]]:
    text = norm(re.sub(r"(?<!\\)%.*", "", tex_path.read_text(encoding="utf-8")))
    tracker = norm(tracker_path.read_text(encoding="utf-8")) if tracker_path.exists() else ""
    loader = Loader()
    errors: list[str] = []
    traced: list[tuple[Entry, str]] = []
    for e in build_entries():
        want = norm(e.tex)
        if want not in text:
            errors.append(f"not in main.tex: {want!r}  [{e.artifact}: {e.key}]")
        if e.expected is not None:
            try:
                got = norm(e.expected(loader))
            except Exception as exc:  # noqa: BLE001 - report the failing key, do not crash the whole table
                errors.append(f"cannot compute {e.artifact}:{e.key}: {exc!r}")
                continue
            if got != want:
                errors.append(f"MISMATCH {e.artifact}:{e.key}\n    artifact gives: {got!r}\n    main.tex says:  {want!r}")
            traced.append((e, got))
        else:
            passage = norm(e.tracker or "")
            if passage not in tracker:
                errors.append(f"tracker passage not found: {passage!r} (for {want!r})")
            traced.append((e, passage))
    return errors, traced


def write_table(traced: list[tuple[Entry, str]], path: Path = TRACE_OUT) -> None:
    lines = ["# Number trace (generated by build_numbers.py)", "",
             "| Text in main.tex | Source | Key or tracker passage |", "|---|---|---|"]
    for e, _ in traced:
        key = e.key if e.expected is not None else f"\"{e.tracker}\""
        lines.append(f"| `{norm(e.tex).replace('|', '/')}` | {e.artifact} | {key.replace('|', '/')} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write number_trace.md")
    parser.add_argument("tex", nargs="?", default=str(TEX))
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    errors, traced = run(Path(args.tex))
    for err in errors:
        print("FAIL:", err)
    if args.write:
        write_table(traced)
        print(f"Wrote {TRACE_OUT.name} ({len(traced)} rows)")
    print(f"build_numbers: {'FAILED' if errors else 'ok'} ({len(traced)} entries, {len(errors)} problem(s))")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
