"""Tests for the column-tolerant EOD CSV loader."""

from datetime import date

import pytest

from backend.core.data_sources.eod_loader import EODBar, load_eod_csv, load_leg


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text.strip() + "\n", encoding="utf-8")
    return p


def test_loads_nse_style_columns(tmp_path):
    csv = _write(tmp_path, "nse.csv", """
Date,Symbol,Expiry,Open,High,Low,Close,Settle,OpenInterest
2026-08-26,USDINR,2026-08-27,86.60,86.72,86.55,86.68,86.68,201000
2026-08-27,USDINR,2026-08-27,86.68,86.80,86.61,86.75,86.75,150000
""")
    bars = load_eod_csv(csv, leg="onshore")
    assert len(bars) == 2
    b = bars[0]
    assert b.trade_date == date(2026, 8, 26)
    assert b.expiry == date(2026, 8, 27)
    assert b.settle == 86.68
    assert b.mark == 86.68           # settle preferred
    assert b.open_interest == 201000


def test_loads_yfinance_style_columns(tmp_path):
    csv = _write(tmp_path, "yf.csv", """
Date,Open,High,Low,Close,Adj Close,Volume
2026-08-26,86.40,86.55,86.35,86.50,86.50,8000
2026-08-27,86.50,86.60,86.44,86.58,86.58,7400
""")
    bars = load_eod_csv(csv, leg="offshore")
    assert [b.mark for b in bars] == [86.50, 86.58]   # no settle -> close
    assert bars[0].expiry is None


def test_skips_rows_without_date_or_price(tmp_path):
    csv = _write(tmp_path, "gappy.csv", """
Date,Close
2026-08-26,86.50
,86.60
2026-08-28,
2026-08-29,86.70
""")
    bars = load_eod_csv(csv, leg="spot")
    assert [b.trade_date for b in bars] == [date(2026, 8, 26), date(2026, 8, 29)]


def test_rejects_csv_without_date_column(tmp_path):
    csv = _write(tmp_path, "bad.csv", "foo,bar\n1,2\n")
    with pytest.raises(ValueError):
        load_eod_csv(csv, leg="spot")


def test_load_leg_requires_a_source():
    with pytest.raises(ValueError):
        load_leg("onshore")
    with pytest.raises(ValueError):
        load_leg("nonsense", csv_path="x")


def test_nse_derivatives_export_front_month(tmp_path):
    # NSE "Historical Data - Derivatives" CSV: many contracts per trade date,
    # expiry encoded in CONTRACTS (DDMMYY), settlement in its own column.
    csv = _write(tmp_path, "nse_deriv.csv", """
TRADE DATE,INSTRUMENT,CONTRACTS,OPTION TYPE,STRIKE PRICE,OPEN PRICE,HIGH PRICE,LOW PRICE,CLOSE PRICE,DAILY SETTLEMENT PRICE,OI,NO. OF CONTRACTS,TURNOVER
18-DEC-2025,FUTCUR,USDINR 261126,-,-,-,-,-,91.7525,92.5825,10,0,0
18-DEC-2025,FUTCUR,USDINR 291225,-,-,90.30,90.55,90.20,90.42,90.48,8000,120,900
18-DEC-2025,FUTCUR,USDINR 280126,-,-,-,-,-,90.90,91.10,300,2,20
19-DEC-2025,FUTCUR,USDINR 291225,-,-,90.40,90.62,90.35,90.55,90.59,7400,110,800
19-DEC-2025,FUTCUR,USDINR 280126,-,-,-,-,-,91.00,91.20,320,3,25
""")
    bars = load_eod_csv(csv, leg="onshore")
    assert len(bars) == 2                          # one per trade date
    b = bars[0]
    assert b.trade_date == date(2025, 12, 18)
    assert b.expiry == date(2025, 12, 29)          # front month, parsed from code
    assert b.settle == 90.48                       # DAILY SETTLEMENT PRICE
    assert b.mark == 90.48
    assert b.open_interest == 8000
    assert bars[1].settle == 90.59


def test_front_month_helper():
    from backend.core.data_sources.eod_loader import front_month
    d = date(2026, 1, 5)
    bars = [
        EODBar(d, "onshore", "s", "USDINR", 90.0, expiry=date(2026, 3, 26)),
        EODBar(d, "onshore", "s", "USDINR", 89.5, expiry=date(2026, 1, 28)),  # front
        EODBar(d, "onshore", "s", "USDINR", 88.0, expiry=date(2025, 12, 29)),  # expired
    ]
    fm = front_month(bars)
    assert len(fm) == 1 and fm[0].close == 89.5


def test_parse_contract_expiry():
    from backend.core.data_sources.eod_loader import _parse_contract_expiry
    assert _parse_contract_expiry("USDINR 280926") == date(2026, 9, 28)
    assert _parse_contract_expiry("USDINR291225") == date(2025, 12, 29)
    assert _parse_contract_expiry("nonsense") is None


def test_bars_are_sorted_by_date(tmp_path):
    csv = _write(tmp_path, "unsorted.csv", """
Date,Close
2026-08-29,86.70
2026-08-26,86.50
2026-08-27,86.60
""")
    bars = load_eod_csv(csv, leg="spot")
    assert [b.trade_date.day for b in bars] == [26, 27, 29]
