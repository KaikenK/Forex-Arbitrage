"""Leak-safe alignment of events to forward FX returns.

Rules (fail-closed, never impute):
  - Entry price = first bar at/after the event's publication time. You cannot trade on
    information before it is public.
  - A horizon return needs a matured exit bar (first bar at/after entry + horizon). If the
    window has not matured yet, that horizon is dropped rather than guessed.
  - If no price is available within `max_entry_lag_minutes` of the event, the event is
    dropped (stale / illiquid gap).
  - If `max_exit_lag_minutes` is set, an exit bar that arrives more than that long after
    its target time is rejected. Without this guard a data gap silently turns a 60-minute
    return into a return over the whole gap (added 2026-10-24). It defaults to off so that
    daily-resolution results, where weekends make multi-day exit lags normal, reproduce
    unchanged; the intraday arm sets it.
"""
from __future__ import annotations

from datetime import timedelta

from .schema import AlignedObservation, Event, PriceSeries


def align_event(
    event: Event,
    series: PriceSeries,
    horizons_minutes: list[int],
    *,
    max_entry_lag_minutes: int = 60,
    max_exit_lag_minutes: int | None = None,
) -> AlignedObservation | None:
    entry = series.price_at_or_after(event.timestamp)
    if entry is None:
        return None
    entry_time, entry_price = entry
    if entry_time - event.timestamp > timedelta(minutes=max_entry_lag_minutes):
        return None
    if entry_price == 0:
        return None

    horizon_returns: dict[int, float] = {}
    for horizon in horizons_minutes:
        target = entry_time + timedelta(minutes=horizon)
        exit_bar = series.price_at_or_after(target)
        if exit_bar is None:
            continue  # window not matured -> fail closed
        exit_time, exit_price = exit_bar
        if max_exit_lag_minutes is not None and exit_time - target > timedelta(minutes=max_exit_lag_minutes):
            continue  # data gap: the bar belongs to a later window -> fail closed
        horizon_returns[horizon] = exit_price / entry_price - 1.0

    if not horizon_returns:
        return None
    return AlignedObservation(
        event=event,
        entry_time=entry_time,
        entry_price=entry_price,
        horizon_returns=horizon_returns,
    )


def align_events(
    events: list[Event],
    series_by_pair: dict[str, PriceSeries],
    horizons_minutes: list[int],
    *,
    max_entry_lag_minutes: int = 60,
    max_exit_lag_minutes: int | None = None,
) -> list[AlignedObservation]:
    observations: list[AlignedObservation] = []
    for event in events:
        series = series_by_pair.get(event.pair)
        if series is None:
            continue
        aligned = align_event(
            event,
            series,
            horizons_minutes,
            max_entry_lag_minutes=max_entry_lag_minutes,
            max_exit_lag_minutes=max_exit_lag_minutes,
        )
        if aligned is not None:
            observations.append(aligned)
    return observations
