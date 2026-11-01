"""Article → per-pair reaction model for scheduled releases (RESEARCH_TRACKER §2, RQ1–RQ5).

Given a release (currency, category, standardized surprise z), predict for each study pair:
whether it is affected, the direction, the expected move in bps, a calibrated probability that
the direction is right, a conformal interval for the move size, and the horizon profile.

  panel.py    — release × pair returns and same-clock controls from HistData M1
  model.py    — per-fold fit/predict (direction, magnitude, confidence, interval)
  metrics.py  — accuracy, AUC, ECE, Brier, MAE, Spearman, release-clustered bootstrap
"""
