"""IEEE paper assets: decay figure data, structural checks, number trace, p-value ledger, hashes,
run_decay offset arithmetic and the volatility-control script. Synthetic data only for the two
heavy scripts; the committed artifacts are read for the rest. No network, no matplotlib needed."""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research import run_decay, run_window30_volctl as vc
from research.paper.ieee import build_numbers, check_paper, make_decay_figure, make_hashes, make_pvalue_ledger
from research.reaction.panel import Release, panel_for_pair

IEEE = Path(make_decay_figure.__file__).resolve().parent
ARTIFACTS = IEEE.parents[1] / "artifacts"


# ---------------------------------------------------------------- decay figure data

def _profile(mean, se, n=100):
    return {"mean": mean, "se": se, "p": 0.5, "n": n, "clusters": 50}


def _target(theory):
    return {"direction": {"theory_rule": {"accuracy": theory}}}


def _synthetic_inputs():
    keys = {"r1": "release_1m", "r5": "release_5m", "r15": "release_15m", "r30": "release_30m",
            "r60": "release_60m", "r120": "release_120m",
            "a15": "arrival_15+15m", "a45": "arrival_15+45m", "a105": "arrival_15+105m"}
    reaction = {"horizon_profile": {k: _profile(i + 1.0, 0.5) for i, k in enumerate(keys)},
                "targets": {t: _target(0.5 + 0.01 * i) for i, t in enumerate(keys.values())}}
    block = lambda n, med: {"n": n, "median_min": med, "within_1_min": 0.0, "within_2_min": 0.1,  # noqa: E731
                            "within_5_min": 0.1, "within_15_min": 0.4}
    return reaction, {"effective_latency": block(9, 20.0), "receipt_latency": block(19, 30.0)}


def test_decay_data_windows_intervals_and_cdf():
    reaction, latency = _synthetic_inputs()
    data = make_decay_figure.decay_data(reaction, latency)
    assert [p["window_end_min"] for p in data["release"]] == [1, 5, 15, 30, 60, 120]
    assert [p["window_end_min"] for p in data["arrival"]] == [30, 60, 120]      # 15 + h
    p = data["release"][0]
    assert p["lo_bps"] == pytest.approx(p["mean_bps"] - 1.96 * p["se_bps"])
    assert p["hi_bps"] == pytest.approx(p["mean_bps"] + 1.96 * p["se_bps"])
    assert data["arrival"][1]["theory_accuracy"] == pytest.approx(0.57)         # arrival_15+45m is index 7
    cdf = data["effective"]
    assert cdf["minutes"] == [0.0, 1.0, 2.0, 5.0, 15.0] and cdf["share"] == [0.0, 0.0, 0.1, 0.1, 0.4]
    assert cdf["n"] == 9 and data["receipt"]["n"] == 19


def test_latency_cdf_rejects_decreasing_shares():
    bad = {"n": 3, "median_min": 5.0, "within_1_min": 0.5, "within_2_min": 0.2, "within_5_min": 0.3, "within_15_min": 0.4}
    with pytest.raises(ValueError):
        make_decay_figure.latency_cdf(bad)


def test_decay_data_on_the_committed_artifacts():
    data = make_decay_figure.load_data()
    r1 = data["release"][0]
    assert r1["mean_bps"] == pytest.approx(7.15, abs=0.01) and r1["lo_bps"] > 0
    a45 = data["arrival"][1]
    assert a45["lo_bps"] < 0 < a45["hi_bps"]                                    # nothing left at arrival
    assert data["effective"]["share"][-1] == pytest.approx(4 / 9) and data["effective"]["n"] == 9
    assert all(b >= a for a, b in zip(data["receipt"]["share"], data["receipt"]["share"][1:]))


# ---------------------------------------------------------------- check_paper

GOOD = r"""\documentclass{article}
\begin{document}
\section{A}\label{s:a} See Section~\ref{s:a} and \cite{k1,k2}.
\begin{itemize}\item x \end{itemize}
\end{document}
"""
BIB = "@article{k1,\n title={a}}\n@misc{k2, title={b}}\n"


def test_check_paper_accepts_a_clean_document(tmp_path):
    assert check_paper.check_text(GOOD, bib=BIB, base=tmp_path) == []


@pytest.mark.parametrize("mutation,needle", [
    (lambda s: s.replace(r"\cite{k1,k2}", r"\cite{k1,zz}"), "zz"),
    (lambda s: s.replace(r"\ref{s:a}", r"\ref{nope}"), "nope"),
    (lambda s: s.replace(r"\end{itemize}", ""), "itemize"),
    (lambda s: s.replace(r"\section{A}", r"\section{A"), "unclosed"),
    (lambda s: s.replace("See", r"\tbd{x} See"), "tbd"),
    (lambda s: s.replace("See", r"\textcolor{red}{See}"), "red"),
    (lambda s: s.replace("See", r"\includegraphics{figures/missing.pdf} See"), "missing.pdf"),
    (lambda s: s.replace("See", "TODO See"), "TODO"),
])
def test_check_paper_flags_each_failure(tmp_path, mutation, needle):
    errors = check_paper.check_text(mutation(GOOD), bib=BIB, base=tmp_path)
    assert any(needle in e for e in errors), errors


def test_check_paper_ignores_comments_and_escaped_braces(tmp_path):
    tex = GOOD.replace("See", r"50\% \{ % \tbd{ignored} {" + "\nSee")
    assert check_paper.check_text(tex, bib=BIB, base=tmp_path) == []


def test_the_paper_passes_its_own_checks():
    assert check_paper.check(IEEE / "main.tex") == []
    tex = (IEEE / "main.tex").read_text(encoding="utf-8")
    assert "\\tbd" not in tex and "textcolor" not in tex and "Acknowledg" not in tex


# ---------------------------------------------------------------- build_numbers

def test_every_traced_number_matches_the_artifacts_and_the_tex():
    errors, traced = build_numbers.run()
    assert errors == []
    assert len(traced) > 100


def test_build_numbers_catches_a_changed_number(tmp_path):
    tex = (IEEE / "main.tex").read_text(encoding="utf-8")
    assert "65.4 to 71.3" in tex
    altered = tmp_path / "main.tex"
    altered.write_text(tex.replace("65.4 to 71.3", "65.4 to 70.3"), encoding="utf-8")
    errors, _ = build_numbers.run(altered)
    assert any("65.4 to 71.3" in e for e in errors)


def test_number_formatting_is_half_up():
    assert build_numbers.q(0.6655 * 100, 1) == "66.6" and build_numbers.pct(0.6844, 1) == "68.4"
    assert build_numbers.dot3(-0.0164) == "-.016" and build_numbers.dot3(0.0036) == "+.004"
    assert build_numbers.sci(6.0969e-9) == "6.1\\times10^{-9}"


# ---------------------------------------------------------------- p-value ledger

def test_ledger_rows_families_and_q_values():
    rows = make_pvalue_ledger.build_rows()
    families = {r["family"] for r in rows}
    assert families == {"RQ2", "window30", "C45", "C40", "H1", "H5", "H6", "prospective"}
    count = lambda f: sum(r["family"] == f for r in rows)  # noqa: E731
    assert (count("RQ2"), count("window30"), count("C45"), count("C40")) == (9, 6, 2, 3)
    w30 = [r for r in rows if r["family"] == "window30"]
    assert all(r["q"] is not None and r["q"] >= r["p"] - 1e-12 for r in w30)          # BH never lowers a p
    c40 = {r["test"][:5]: r for r in rows if r["family"] == "C40"}
    assert c40["C40-1"]["p"] is None and c40["C40-1"]["note"] == "not computable"
    assert c40["C40-3"]["q"] == pytest.approx(0.4357821, abs=1e-6)


def test_ledger_latex_fragment():
    assert make_pvalue_ledger.fmt_p(0.0123) == "0.012" and make_pvalue_ledger.fmt_p(None) == "--"
    assert make_pvalue_ledger.fmt_p(6.1e-9) == "$6.1\\times10^{-9}$"
    tex = make_pvalue_ledger.render_latex(make_pvalue_ledger.build_rows())
    assert tex.startswith("\\begin{tabular}") and tex.rstrip().endswith("\\end{tabular}")
    assert tex.count("\\begin{tabular}") == tex.count("\\end{tabular}") == 1
    assert "not computable" in tex
    assert (IEEE / "tables" / "pledger.tex").read_text(encoding="utf-8") == tex   # committed fragment is current


# ---------------------------------------------------------------- hashes

def test_digest_ignores_line_endings_and_record_is_current(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_bytes(b'{"x": 1}\n{"y": 2}\n')
    b.write_bytes(b'{"x": 1}\r\n{"y": 2}\r\n')
    assert make_hashes.digest(a) == make_hashes.digest(b)
    record = json.loads((IEEE / "artifact_hashes.json").read_text(encoding="utf-8"))
    assert record["source_commit"] == "639fcdf"
    assert record["files"] == make_hashes.build()["files"]


# ---------------------------------------------------------------- run_decay offset arithmetic

T0 = datetime(2024, 3, 5, 13, 30, tzinfo=UTC)        # a Tuesday


def _release() -> Release:
    return Release("r1", T0, "United States", "USD", "CPI", "inflation", 1.0, 1)


def test_window_and_label_arithmetic():
    assert run_decay.OFFSETS_MIN == (0, 1, 2, 5, 10, 15, 30) and run_decay.WINDOW_MIN == 45
    assert run_decay.window_for(0) == (0, 45) and run_decay.window_for(30) == (30, 75)
    assert run_decay.label_for(5) == "arrival_5+45m" and run_decay.label_for(15) == "arrival_15+45m"
    with pytest.raises(ValueError):
        run_decay.window_for(-1)


@pytest.mark.parametrize("offset", run_decay.OFFSETS_MIN)
def test_panel_a45_is_the_return_from_offset_to_offset_plus_45(offset):
    t = int(T0.timestamp())
    minutes = np.arange(-30, 200)
    bar_times = (t + minutes * 60).astype(float)
    prices = 1.1 * np.exp(1e-5 * (minutes ** 2) / 10.0)      # curved, so every window differs
    panel = panel_for_pair([_release()], "EUR/USD", bar_times, prices, arrival_offset_min=offset)
    at = lambda m: prices[int(np.where(minutes == m)[0][0])]  # noqa: E731
    expected = math.log(at(offset + 45) / at(offset)) * 1e4
    assert panel["arrival_offset_min"].iloc[0] == offset
    assert panel["a45"].iloc[0] == pytest.approx(expected)
    assert panel["r15"].iloc[0] == pytest.approx(math.log(at(15) / at(0)) * 1e4)   # release-time unaffected


def test_offsets_give_different_windows_on_a_curved_series():
    t = int(T0.timestamp())
    minutes = np.arange(-30, 200)
    bar_times = (t + minutes * 60).astype(float)
    prices = 1.1 * np.exp(1e-5 * (minutes ** 2) / 10.0)
    values = [panel_for_pair([_release()], "EUR/USD", bar_times, prices, arrival_offset_min=o)["a45"].iloc[0]
              for o in run_decay.OFFSETS_MIN]
    assert len(set(np.round(values, 9))) == len(values)


def test_summarise_reads_one_offset_payload():
    label = run_decay.label_for(5)
    payload = {"targets": {label: {
        "direction": {"rows": 100, "moments": 40, "model": {"accuracy": 0.6, "ci95": [0.5, 0.7], "p_vs_coin": 0.04},
                      "theory_rule": {"accuracy": 0.62, "ci95": [0.5, 0.7]}, "majority": {"accuracy": 0.5}},
        "magnitude": {"mean_abs_move_bps": 9.0}}},
        "horizon_profile": {"a45": {"mean": 1.5, "se": 0.4, "p": 0.001}}}
    row = run_decay.summarise(payload, 5)
    assert (row["offset_min"], row["window_start_min"], row["window_end_min"]) == (5, 5, 50)
    assert row["model_accuracy"] == 0.6 and row["directed_move_mean_bps"] == 1.5 and row["theory_accuracy"] == 0.62


# ---------------------------------------------------------------- volatility control (synthetic)

def test_trailing_level_uses_only_windows_that_end_by_t():
    times = np.arange(0, 100) * 1800.0                       # every 30 min, no gaps
    returns = np.zeros(100)
    returns[10] = 0.5                                        # window [10*1800, 11*1800]
    level = vc.trailing_level(times, returns, hours=24, min_windows=24)
    assert np.isnan(level[:24]).all()                        # fewer than 24 windows available
    # window 10 ends at time index 11, so it first counts at t = times[11] (if enough windows) and not before
    small = vc.trailing_level(times, returns, hours=24, min_windows=1)
    assert small[10] == pytest.approx(0.0) and small[11] == pytest.approx(0.5 / 11)
    assert small[11 + 48] == pytest.approx(0.0)              # 24 h later it has left the window


def test_prior_window_and_controls_are_aligned_to_the_signal_time():
    times = np.array([0.0, 1800.0, 3600.0])
    cols = vc.control_columns(times, np.array([0.1, 0.2, 0.3]), np.array([0.1, 0.1, 0.1]))
    assert list(cols.columns) == ["time_s", "prior_abnormal", "level_ratio"]
    assert np.isnan(cols["prior_abnormal"]).all()            # no earlier days, so no baseline


def _confounded_panel(seed=3, planted=0.0):
    rng = np.random.default_rng(seed)
    n = 6000
    dates = np.array([f"2024-{1 + i // 600:02d}-{1 + (i // 25) % 24:02d}" for i in range(n)])
    prior = rng.normal(0, 1, n)                              # log prior abnormal move
    activity = np.exp(0.6 * prior + rng.normal(0, 0.5, n))   # activity follows ongoing volatility
    log_abnormal = 0.5 * prior + planted * np.log1p(activity) + rng.normal(0, 1, n)
    return pd.DataFrame({
        "horizon": 30, "pair": np.where(np.arange(n) % 2, "EUR/USD", "GBP/USD"), "time_s": np.arange(n) * 1800.0,
        "activity": activity, "abnormal": np.exp(log_abnormal), "release": False, "utc_date": dates,
        "log_prior": prior, "log_level": rng.normal(0, 1, n)})


def test_activity_beta_shrinks_when_it_only_proxies_for_prior_volatility():
    out = vc.run_tests(_confounded_panel())["T2_activity_30"]["models"]
    base, with_prior = out["base"], out["plus_prior"]
    assert base["beta"] > 0.1 and base["p"] < 0.05           # looks like an effect without the control
    assert with_prior["shrinkage_vs_base"] > 0.7             # and mostly disappears with it
    assert abs(with_prior["beta"]) < abs(base["beta"])


def test_a_real_activity_effect_survives_the_control():
    out = vc.run_tests(_confounded_panel(planted=0.4))["T2_activity_30"]["models"]
    assert out["plus_both"]["beta"] == pytest.approx(0.4, abs=0.1) and out["plus_both"]["p"] < 0.01
    assert vc.shrinkage(0.04, 0.02) == pytest.approx(0.5)
