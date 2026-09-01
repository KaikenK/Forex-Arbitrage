"""CarryCalibrator — implied forward premium from a two-point futures curve."""

from datetime import date

from backend.core.normalization import CarryCalibrator


NEAR_EXP = date(2026, 9, 28)
FAR_EXP = date(2026, 10, 28)   # 30 days later


def test_implied_annual_matches_hand_calc():
    # far 0.30 richer than near over 30 days -> ~3.8% annualised
    r = CarryCalibrator.implied_annual(94.97, NEAR_EXP, 95.27, FAR_EXP)
    assert r is not None
    assert abs(r - ((95.27 / 94.97 - 1.0) * 365.0 / 30.0)) < 1e-9
    assert 0.037 < r < 0.040


def test_bad_inputs_return_none():
    assert CarryCalibrator.implied_annual(0.0, NEAR_EXP, 95.0, FAR_EXP) is None
    assert CarryCalibrator.implied_annual(95.0, FAR_EXP, 95.2, NEAR_EXP) is None  # dt<=0


def test_falls_back_before_any_sample():
    c = CarryCalibrator(fallback_annual=0.019)
    assert c.current == 0.019
    assert c.source == "assumed"


def test_ema_converges_and_marks_calibrated():
    c = CarryCalibrator(fallback_annual=0.019, ema_alpha=0.5)
    for _ in range(20):
        c.update(94.97, NEAR_EXP, 95.27, FAR_EXP)
    assert c.source == "calibrated"
    assert abs(c.current - 0.0384) < 0.002
    assert c.samples == 20


def test_out_of_band_sample_is_rejected():
    c = CarryCalibrator(fallback_annual=0.019, min_annual=-0.03, max_annual=0.15)
    # 40% annualised -> nonsense, ignored
    c.update(90.0, NEAR_EXP, 94.0, FAR_EXP)
    assert c.source == "assumed"
    assert c.current == 0.019
