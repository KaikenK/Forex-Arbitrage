"""OtcSpotSource — free key-less USD/INR spot leg (Yahoo primary, Frankfurter fallback)."""

import backend.core.data_sources.otc_spot_source as otc
from backend.core.data_sources.otc_spot_source import OtcSpotSource
from backend.core.interfaces.data_source import DataSourceConfig


def _src(**kw):
    cfg = DataSourceConfig(source_id="otc_usdinr_spot", source_type="basis_otc",
                           symbols=["USDINR"])
    return OtcSpotSource(cfg, **kw)


def test_synthesises_bid_ask_from_mid(monkeypatch):
    monkeypatch.setattr(otc, "fetch_yahoo", lambda: 95.20)
    s = _src(spread_pips=2.0)
    s._fetch_once()
    t = s.get_tick("USDINR")
    assert t.last == 95.20
    assert t.bid == 95.19 and t.ask == 95.21          # ±1 pip (2-pip spread)
    assert t.extra["leg"] == "otc" and t.extra["instrument_kind"] == "spot"
    assert t.extra["synthetic_spread"] is True
    assert t.extra["source"] == "yahoo"


def test_falls_back_to_frankfurter_when_yahoo_down(monkeypatch):
    monkeypatch.setattr(otc, "fetch_yahoo", lambda: None)
    monkeypatch.setattr(otc, "fetch_frankfurter", lambda: 95.42)
    s = _src()
    s._fetch_once()
    t = s.get_tick("USDINR")
    assert t.last == 95.42
    assert t.extra["source"] == "frankfurter"


def test_no_tick_when_both_sources_down(monkeypatch):
    monkeypatch.setattr(otc, "fetch_yahoo", lambda: None)
    monkeypatch.setattr(otc, "fetch_frankfurter", lambda: None)
    s = _src()
    s._fetch_once()
    assert s.get_tick("USDINR") is None


def test_spread_pips_env_override(monkeypatch):
    monkeypatch.setattr(otc, "fetch_yahoo", lambda: 90.0)
    monkeypatch.setenv("OTC_SPOT_SPREAD_PIPS", "6")
    s = _src()
    s._fetch_once()
    t = s.get_tick("USDINR")
    assert t.bid == 90.0 - 0.03 and t.ask == 90.0 + 0.03    # 6-pip spread -> ±3 pips
