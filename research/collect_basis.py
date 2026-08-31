"""
Always-on collector for the live 3-leg onshore-offshore USD/INR basis.

Runs ``build_basis_pipeline()`` (Upstox onshore + Yahoo SIR=F offshore + Yahoo /
Frankfurter OTC spot — all free) during NSE currency hours and appends every
snapshot and every detected dislocation event to a day-stamped file. Built to
run for weeks: it tolerates a failed poll, reconnects dead sources, rolls the
output file at IST midnight, and writes a ``status.json`` heartbeat.

    python research/collect_basis.py                 # collect during market hours, forever
    python research/collect_basis.py --dry-run       # ignore hours, run ~90 s (smoke test)
    python research/collect_basis.py --force         # ignore hours, run forever (holiday / debug)

Output (default ``research/results/collect/``):
    basis_YYYYMMDD.jsonl    one JSON snapshot per tick
    events_YYYYMMDD.jsonl   one JSON per detected BasisEvent
    status.json             heartbeat: last tick, counts, last basis, errors

Market window: Mon-Fri 09:00-17:00 IST (NSE holidays are not excluded here -
filter them in analysis by trading date). Outside the window the collector idles.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
except Exception:
    pass

import logging  # noqa: E402

from backend.core.data_sources.basis_pipeline import build_basis_pipeline  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
_MKT_OPEN = (9, 0)     # 09:00 IST
_MKT_CLOSE = (17, 0)   # 17:00 IST

log = logging.getLogger("collect_basis")


def _in_market_hours(now_ist: datetime) -> bool:
    if now_ist.weekday() >= 5:                       # Sat/Sun
        return False
    o = now_ist.replace(hour=_MKT_OPEN[0], minute=_MKT_OPEN[1], second=0, microsecond=0)
    c = now_ist.replace(hour=_MKT_CLOSE[0], minute=_MKT_CLOSE[1], second=0, microsecond=0)
    return o <= now_ist <= c


def _next_open(now_ist: datetime) -> datetime:
    d = now_ist
    o = d.replace(hour=_MKT_OPEN[0], minute=_MKT_OPEN[1], second=0, microsecond=0)
    if now_ist >= o or now_ist.weekday() >= 5:
        o = o + timedelta(days=1)
    while o.weekday() >= 5:
        o = o + timedelta(days=1)
    return o


class DayFiles:
    """Append-only day-stamped output, rolled at IST midnight."""

    def __init__(self, outdir: Path):
        self.outdir = outdir
        self.outdir.mkdir(parents=True, exist_ok=True)
        self._day = ""
        self.snap = None
        self.evt = None

    def for_day(self, day: str):
        if day != self._day:
            self.close()
            self.snap = open(self.outdir / f"basis_{day}.jsonl", "a", encoding="utf-8")
            self.evt = open(self.outdir / f"events_{day}.jsonl", "a", encoding="utf-8")
            self._day = day
            log.info("writing to %s", self.snap.name)
        return self.snap, self.evt

    def close(self):
        for f in (self.snap, self.evt):
            try:
                if f:
                    f.close()
            except Exception:
                pass


def run(outdir: Path, poll_s: float, *, ignore_hours: bool, max_seconds: float | None):
    pipeline = build_basis_pipeline()
    for s in pipeline.sources:
        try:
            s.connect()
        except Exception as e:
            log.warning("connect %s failed: %s", getattr(s, "source_id", "?"), e)

    files = DayFiles(outdir)
    status_path = outdir / "status.json"
    started = time.time()
    stop = {"v": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("v", True))
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))
    except (ValueError, AttributeError):
        pass

    n_snap = n_evt = n_err = 0
    n_empty_streak = 0
    last_tick_iso = None
    last_basis = {}
    last_idle_log = 0.0
    # event dedup: every snapshot already carries the basis time series, so only
    # log an event on a *state change* for its (leg_pair|direction) key -
    # new episode, persistence-class transition, or a >=2 pip move since last logged.
    seen_events: dict = {}   # key -> (persistence_class, basis_pips)

    while not stop["v"]:
        if max_seconds and (time.time() - started) > max_seconds:
            break

        now_ist = datetime.now(IST)
        if not ignore_hours and not _in_market_hours(now_ist):
            if time.time() - last_idle_log > 3600:
                log.info("outside NSE hours (%s IST) - idling; next open %s",
                         now_ist.strftime("%a %H:%M"), _next_open(now_ist).strftime("%a %d %b %H:%M"))
                last_idle_log = time.time()
            _sleep(60, stop)
            continue

        day = now_ist.strftime("%Y%m%d")
        snap_f, evt_f = files.for_day(day)
        pipeline._out, pipeline._events_out = snap_f, evt_f

        try:
            row, events = pipeline._tick_once()          # writes `row` to snap_f internally
        except Exception as e:
            n_err += 1
            log.warning("tick error: %s", e)
            _sleep(poll_s, stop)
            continue

        if row is None:
            n_empty_streak += 1
            if n_empty_streak in (10, 30) or n_empty_streak % 120 == 0:
                log.warning("%d consecutive empty ticks - reconnecting sources", n_empty_streak)
                for s in pipeline.sources:
                    try:
                        s.disconnect(); s.connect()
                    except Exception as e:
                        log.warning("reconnect %s: %s", getattr(s, "source_id", "?"), e)
        else:
            n_empty_streak = 0
            n_snap += 1
            last_tick_iso = datetime.now(timezone.utc).isoformat()
            last_basis = row.get("basis_pips", {})
            wrote = False
            for ev in events or ():
                key = f"{ev.leg_pair}|{ev.buy_leg}"
                prev = seen_events.get(key)
                changed = (prev is None
                           or prev[0] != ev.persistence_class
                           or abs(prev[1] - ev.basis_pips) >= 2.0)
                if changed:
                    evt_f.write(json.dumps(ev.to_dict()) + "\n")
                    n_evt += 1
                    wrote = True
                seen_events[key] = (ev.persistence_class, ev.basis_pips)
            # forget keys not seen this tick (episode ended)
            live_keys = {f"{e.leg_pair}|{e.buy_leg}" for e in (events or ())}
            for k in list(seen_events):
                if k not in live_keys:
                    del seen_events[k]
            if wrote:
                evt_f.flush()
            pipeline.tracker.tick(time.time())

        if n_snap % 30 == 0 or events:
            _write_status(status_path, {
                "updated": datetime.now(timezone.utc).isoformat(),
                "last_tick": last_tick_iso,
                "snapshots": n_snap, "events": n_evt, "errors": n_err,
                "empty_streak": n_empty_streak,
                "last_basis_pips": last_basis,
                "legs_live": row.get("forwards", {}) and sorted(row["forwards"].keys()) if row else [],
                "day_file": day,
            })
        _sleep(poll_s, stop)

    log.info("stopping - %d snapshots, %d events, %d errors", n_snap, n_evt, n_err)
    files.close()
    for s in pipeline.sources:
        try:
            s.disconnect()
        except Exception:
            pass


def _sleep(seconds: float, stop: dict):
    end = time.time() + seconds
    while time.time() < end and not stop["v"]:
        time.sleep(min(1.0, end - time.time()))


def _write_status(path: Path, d: dict):
    try:
        path.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Always-on onshore-offshore USD/INR basis collector")
    ap.add_argument("--outdir", default=str(REPO / "research" / "results" / "collect"))
    ap.add_argument("--poll", type=float, default=2.0, help="seconds between ticks (default 2)")
    ap.add_argument("--dry-run", action="store_true", help="ignore market hours, run ~90 s")
    ap.add_argument("--force", action="store_true", help="ignore market hours, run forever")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(outdir / "collect.log", encoding="utf-8")],
    )

    # top-level supervisor: never let one crash end the run
    while True:
        try:
            run(Path(args.outdir), args.poll,
                ignore_hours=args.dry_run or args.force,
                max_seconds=90.0 if args.dry_run else None)
            return
        except KeyboardInterrupt:
            return
        except Exception:
            log.error("collector crashed - restarting in 30 s\n%s", traceback.format_exc())
            time.sleep(30)


if __name__ == "__main__":
    main()
