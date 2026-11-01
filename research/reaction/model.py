"""Per-fold reaction model (RQ1–RQ5 pre-registration; clarifications C20–C24 in §6).

Cells are keyed by (release currency, category, pair): a USD and a EUR inflation surprise move
EUR/USD in opposite directions, so a category × pair cell that pooled currencies would cancel by
construction (C20). A cell with fewer than MIN_CELL training rows falls back to the
(currency, pair) cell, then abstains (direction) or uses the pair's training mean (magnitude).

  direction   ŝ = sign(β̂·z), β̂ = Σ z·r / Σ z² (through the origin: only its sign is used)
  move        m̂ = a + b·|z| + c·v   (OLS per cell, clipped at 0)
  confidence  p = logistic(γ0 + γ1·|z| + γ2·log(1+|z|)) per category, fallback pooled,
              fitted on the training rows' in-sample correctness (C21)
  interval    split-conformal: the move model refitted without the last CALIBRATION_MONTHS
              training months; q_level = finite-sample quantile of |r| − m̂ on those months (C22)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MIN_CELL = 20
MIN_CLASS = 5
CALIBRATION_MONTHS = 12
LEVELS = (0.8, 0.9)
_RIDGE = 1e-3


def _cell_keys(frame: pd.DataFrame) -> tuple[list, list]:
    cells = list(zip(frame["currency"], frame["category"], frame["pair"]))
    pooled = list(zip(frame["currency"], frame["pair"]))
    return cells, pooled


def _through_origin(z: np.ndarray, r: np.ndarray) -> float | None:
    denom = float(np.dot(z, z))
    return float(np.dot(z, r)) / denom if denom > 0 else None


def _ols(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    design = np.column_stack([np.ones(len(y)), x])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    return coef


def fit_logistic(x: np.ndarray, y: np.ndarray, *, iterations: int = 50) -> np.ndarray | None:
    """Newton–Raphson logistic regression with an intercept and a tiny ridge."""
    if len(y) < MIN_CELL or min(int(y.sum()), int(len(y) - y.sum())) < MIN_CLASS:
        return None
    design = np.column_stack([np.ones(len(y)), x])
    w = np.zeros(design.shape[1])
    for _ in range(iterations):
        p = 1.0 / (1.0 + np.exp(-np.clip(design @ w, -30, 30)))
        grad = design.T @ (p - y) + _RIDGE * w
        hess = (design * (p * (1 - p))[:, None]).T @ design + _RIDGE * np.eye(len(w))
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


def _conf_features(z: np.ndarray) -> np.ndarray:
    a = np.abs(z)
    return np.column_stack([a, np.log1p(a)])


def _quantile(values: np.ndarray, level: float) -> float | None:
    n = len(values)
    if n == 0:
        return None
    rank = math.ceil((n + 1) * level)
    if rank > n:
        return None
    return float(np.sort(values)[rank - 1])


@dataclass
class FoldModel:
    target: str
    control: str
    beta_cell: dict = field(default_factory=dict)
    beta_pooled: dict = field(default_factory=dict)
    move_cell: dict = field(default_factory=dict)
    move_pooled: dict = field(default_factory=dict)
    move_pair_mean: dict = field(default_factory=dict)
    conf_category: dict = field(default_factory=dict)
    conf_pooled: np.ndarray | None = None
    conformal_q: dict = field(default_factory=dict)
    split_move_cell: dict = field(default_factory=dict)
    split_move_pooled: dict = field(default_factory=dict)
    split_pair_mean: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ direction
    def beta(self, currency: str, category: str, pair: str) -> tuple[float | None, str]:
        if (currency, category, pair) in self.beta_cell:
            return self.beta_cell[(currency, category, pair)], "cell"
        if (currency, pair) in self.beta_pooled:
            return self.beta_pooled[(currency, pair)], "pooled"
        return None, "none"

    # ------------------------------------------------------------------ move size
    @staticmethod
    def _move(cell_map, pooled_map, mean_map, currency, category, pair, abs_z, v) -> float | None:
        coef = cell_map.get((currency, category, pair))
        if coef is None:
            coef = pooled_map.get((currency, pair))
        if coef is not None and np.isfinite(v):
            return max(0.0, float(coef[0] + coef[1] * abs_z + coef[2] * v))
        return mean_map.get(pair)


def _fit_moves(frame: pd.DataFrame, target: str, control: str) -> tuple[dict, dict, dict]:
    data = frame[np.isfinite(frame[target])]
    pair_mean = data.groupby("pair")[target].apply(lambda s: float(np.abs(s).mean())).to_dict()
    data = data[np.isfinite(data[control])]
    cells: dict = {}
    pooled: dict = {}
    for key, group in data.groupby(["currency", "category", "pair"]):
        if len(group) >= MIN_CELL:
            cells[key] = _ols(np.column_stack([np.abs(group["z"]), group[control]]), np.abs(group[target].to_numpy()))
    for key, group in data.groupby(["currency", "pair"]):
        if len(group) >= MIN_CELL:
            pooled[key] = _ols(np.column_stack([np.abs(group["z"]), group[control]]), np.abs(group[target].to_numpy()))
    return cells, pooled, pair_mean


def fit_fold(train: pd.DataFrame, *, target: str, control: str) -> FoldModel:
    """Fit every component on `train` (rows of strictly earlier months)."""
    model = FoldModel(target=target, control=control)
    usable = train[np.isfinite(train[target])]
    own = usable[usable["side"].notna()]

    for key, group in own.groupby(["currency", "category", "pair"]):
        if len(group) >= MIN_CELL:
            b = _through_origin(group["z"].to_numpy(), group[target].to_numpy())
            if b is not None:
                model.beta_cell[key] = b
    for key, group in own.groupby(["currency", "pair"]):
        if len(group) >= MIN_CELL:
            b = _through_origin(group["z"].to_numpy(), group[target].to_numpy())
            if b is not None:
                model.beta_pooled[key] = b

    model.move_cell, model.move_pooled, model.move_pair_mean = _fit_moves(usable, target, control)

    # Confidence: in-sample correctness of the training rows under this fold's β̂ (C21).
    signs, keep = [], []
    for row in own.itertuples(index=False):
        b, _ = model.beta(row.currency, row.category, row.pair)
        r = getattr(row, target)
        if b is None or b == 0 or r == 0:
            keep.append(False)
            signs.append(0)
            continue
        keep.append(True)
        signs.append(1 if np.sign(b * row.z) == np.sign(r) else 0)
    mask = np.array(keep, dtype=bool)
    if mask.any():
        conf_rows = own[mask]
        correct = np.array(signs)[mask].astype(float)
        feats = _conf_features(conf_rows["z"].to_numpy())
        model.conf_pooled = fit_logistic(feats, correct)
        for category in sorted(conf_rows["category"].unique()):
            sel = (conf_rows["category"] == category).to_numpy()
            w = fit_logistic(feats[sel], correct[sel])
            if w is not None:
                model.conf_category[category] = w

    # Split-conformal intervals for the move size (C22).
    months = sorted(usable["month"].unique())
    if len(months) > CALIBRATION_MONTHS:
        cutoff = months[-CALIBRATION_MONTHS]
        proper, calib = usable[usable["month"] < cutoff], usable[usable["month"] >= cutoff]
        model.split_move_cell, model.split_move_pooled, model.split_pair_mean = _fit_moves(proper, target, control)
        calib = calib[calib["side"].notna()]
        preds = np.array([
            FoldModel._move(model.split_move_cell, model.split_move_pooled, model.split_pair_mean,
                            r.currency, r.category, r.pair, abs(r.z), getattr(r, control))
            for r in calib.itertuples(index=False)], dtype=float)
        resid = np.abs(np.abs(calib[target].to_numpy()) - preds)
        resid = resid[np.isfinite(resid)]
        for level in LEVELS:
            q = _quantile(resid, level)
            if q is not None:
                model.conformal_q[level] = q
    return model


def _logistic(w: np.ndarray, z: float) -> float:
    x = np.concatenate([[1.0], _conf_features(np.array([z]))[0]])
    return float(1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, float(x @ w))))))


def predict(model: FoldModel, test: pd.DataFrame) -> pd.DataFrame:
    """Per-row predictions; direction fields are NaN for cross pairs or abstentions."""
    out = []
    for row in test.itertuples(index=False):
        v = getattr(row, model.control)
        move = FoldModel._move(model.move_cell, model.move_pooled, model.move_pair_mean,
                               row.currency, row.category, row.pair, abs(row.z), v)
        rec = {"expected_move_bps": np.nan if move is None else move, "s_hat": np.nan, "p_dir": np.nan,
               "signed_move_bps": np.nan, "beta_source": "none"}
        if row.side is not None and not (isinstance(row.side, float) and math.isnan(row.side)):
            b, source = model.beta(row.currency, row.category, row.pair)
            rec["beta_source"] = source
            if b is not None and b != 0:
                rec["s_hat"] = float(np.sign(b * row.z))
                rec["signed_move_bps"] = float(b * row.z)
                w = model.conf_category.get(row.category)
                w = model.conf_pooled if w is None else w
                if w is not None:
                    rec["p_dir"] = _logistic(w, row.z)
            split = FoldModel._move(model.split_move_cell, model.split_move_pooled, model.split_pair_mean,
                                    row.currency, row.category, row.pair, abs(row.z), v)
            for level, q in model.conformal_q.items():
                tag = int(round(level * 100))
                if split is not None:
                    rec[f"lo{tag}"] = max(0.0, split - q)
                    rec[f"hi{tag}"] = split + q
        out.append(rec)
    return pd.DataFrame(out, index=test.index)
