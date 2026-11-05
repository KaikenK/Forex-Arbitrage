"""A training window that is constant up to float rounding must not produce huge z-scores (H6 intraday)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.semantic.currency_bias import zscore_from_mask


def _series(values):
    return pd.Series(values, index=np.arange(len(values), dtype=float))


def test_near_constant_training_window_gives_no_zscore():
    train = [0.1 + 0.2] * 50 + [0.3] * 50          # equal up to rounding: std ~1e-17, not 0
    raw = _series(train + [0.5, -0.4])
    mask = np.array([True] * 100 + [False, False])
    z = zscore_from_mask(raw, mask, no_data_value="z_zero")
    tail = z.to_numpy()[-2:]
    assert np.all(~np.isfinite(tail) | (np.abs(tail) < 1e3)), tail


def test_normal_training_window_unchanged():
    rng = np.random.default_rng(0)
    values = rng.normal(2.0, 0.5, 200)
    raw = _series(values)
    mask = np.arange(200) < 150
    z = zscore_from_mask(raw, mask, no_data_value="z_zero").to_numpy()
    expected = (values - values[:150].mean()) / values[:150].std(ddof=1)
    np.testing.assert_allclose(z, expected, rtol=0, atol=1e-12)
