"""Tests for the EOD onshore-offshore basis runner (Option A)."""

from datetime import date

from backend.core.basis import EODBasisRunner
from backend.core.data_sources.eod_loader import EODBar

T_STAR = date(2026, 9, 29)
CARRY = 0.019


def _onshore(d, fut, expiry=T_STAR):
    return EODBar(trade_date=d, leg="onshore", source="nse", symbol="USDINR",
                  close=fut, settle=fut, expiry=expiry)


def _offshore(d, fut):
    return EODBar(trade_date=d, leg="offshore", source="cme", symbol="INR=F", close=fut)


def _spot(d, s):
    return EODBar(trade_date=d, leg="spot", source="yf", symbol="USDINR=X", close=s)


def test_onshore_offshore_basis_recovered():
    # both futures at T*, onshore 4 pips above offshore
    d = date(2026, 9, 1)
    rows = EODBasisRunner(CARRY).run(
        [_onshore(d, 87.000)], [_offshore(d, 86.960)], [],
    )
    assert len(rows) == 1
    assert rows[0].basis_pips["onshore_offshore"] == 4.0
    assert rows[0].target_expiry == T_STAR


def test_spot_leg_basis_near_zero_when_carry_matches():
    # spot carried to T* should land near a fairly-priced onshore future
    d = date(2026, 9, 1)
    days = (T_STAR - d).days
    fair_fut = 86.50 * (1 + CARRY * days / 365)
    rows = EODBasisRunner(CARRY).run(
        [_onshore(d, fair_fut)], [], [_spot(d, 86.50)],
    )
    assert abs(rows[0].basis_pips["onshore_spot"]) < 0.5


def test_dates_with_one_leg_are_skipped():
    rows = EODBasisRunner(CARRY).run(
        [_onshore(date(2026, 9, 1), 87.0), _onshore(date(2026, 9, 2), 87.1)],
        [_offshore(date(2026, 9, 2), 87.05)],
        [],
    )
    assert [r.trade_date for r in rows] == [date(2026, 9, 2)]


def test_summary_stats():
    runner = EODBasisRunner(CARRY)
    runner.run(
        [_onshore(date(2026, 9, 1), 87.00), _onshore(date(2026, 9, 2), 87.00)],
        [_offshore(date(2026, 9, 1), 86.95), _offshore(date(2026, 9, 2), 87.02)],
        [],
    )
    s = runner.summary()
    assert s["rows"] == 2
    oo = s["basis_stats_pips"]["onshore_offshore"]
    assert oo["n"] == 2
    assert oo["min_pips"] == -2.0 and oo["max_pips"] == 5.0


def test_write_creates_files(tmp_path, monkeypatch):
    import backend.core.basis.eod_basis as mod
    monkeypatch.setattr(mod, "_RESULTS_EOD", tmp_path)
    runner = EODBasisRunner(CARRY)
    runner.run([_onshore(date(2026, 9, 1), 87.0)], [_offshore(date(2026, 9, 1), 86.9)], [])
    out = runner.write(run_id="t")
    assert (out / "basis_eod.jsonl").exists()
    assert (out / "summary.json").exists()
