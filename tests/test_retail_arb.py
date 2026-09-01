"""Retail-arb mode: options-chain resolver, UpstoxOptionsSource, retail pipeline legs."""

from datetime import date, datetime, timezone

import pytest

from backend.core.data_sources.upstox_instruments import usdinr_options, resolve_near_month
from backend.core.data_sources.upstox_options_source import UpstoxOptionsSource
from backend.core.interfaces.data_source import DataSourceConfig
from backend.core.normalization import discount_factor
from backend.core.basis.basis_event import LEG_PAIRS, SCHEMA_VERSION


def _ms(y, m, d):
    return int(datetime(y, m, d, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)


_MASTER = [
    {"segment": "NCD_FO", "name": "USDINR", "instrument_type": "FUT", "weekly": False,
     "instrument_key": "NCD_FO|1769", "trading_symbol": "USDINR FUT 28 SEP 26",
     "expiry": _ms(2026, 9, 28), "lot_size": 1, "qty_multiplier": 1000.0},
    {"segment": "NCD_FO", "name": "USDINR", "instrument_type": "FUT", "weekly": False,
     "instrument_key": "NCD_FO|1284", "trading_symbol": "USDINR FUT 28 OCT 26",
     "expiry": _ms(2026, 10, 28), "lot_size": 1, "qty_multiplier": 1000.0},
]
for _k, _t in ((95.00, "CE"), (95.00, "PE"), (95.25, "CE"), (95.25, "PE"),
               (95.50, "CE"), (95.50, "PE")):
    _MASTER.append({
        "segment": "NCD_FO", "name": "USDINR", "instrument_type": _t, "weekly": False,
        "instrument_key": f"NCD_FO|{int(_k*100)}{_t}", "strike_price": _k,
        "trading_symbol": f"USDINR {_k} {_t} 28 SEP 26", "expiry": _ms(2026, 9, 28),
    })


def test_schema_v12_has_retail_leg_pairs():
    assert SCHEMA_VERSION == "1.2"
    assert "future_options" in LEG_PAIRS and "future_far" in LEG_PAIRS


def test_usdinr_options_filters_by_expiry():
    opts = usdinr_options(date(2026, 9, 28), master_json=_MASTER)
    assert len(opts) == 6
    assert {o.strike for o in opts} == {95.00, 95.25, 95.50}
    assert {o.option_type for o in opts} == {"CE", "PE"}
    assert usdinr_options(date(2026, 12, 29), master_json=_MASTER) == []


def test_resolve_far_month():
    near = resolve_near_month(as_of=date(2026, 8, 31), master_json=_MASTER)
    far = resolve_near_month(as_of=date(2026, 8, 31), nth=1, master_json=_MASTER)
    assert near.instrument_key == "NCD_FO|1769"
    assert far.instrument_key == "NCD_FO|1284"


class _StubOptionsSource(UpstoxOptionsSource):
    """Feeds a canned quote response instead of hitting Upstox."""

    def __init__(self, cfg, F_syn):
        super().__init__(cfg, access_token="stub")
        self._F_syn = F_syn
        self._expiry = date(2026, 9, 28)
        self._fut_key = "NCD_FO|1769"
        self._opt_by_key = {
            f"NCD_FO|{int(k*100)}{t}": type("O", (), {
                "instrument_key": f"NCD_FO|{int(k*100)}{t}", "strike": k, "option_type": t})()
            for k in (95.00, 95.25, 95.50) for t in ("CE", "PE")
        }

    def _quote(self, keys):
        days = (self._expiry - datetime.now(timezone.utc).date()).days
        df = discount_factor(days, self._rate)
        out = {}
        if self._fut_key in keys or any(self._fut_key == k for k in keys):
            out[self._fut_key] = {"instrument_token": self._fut_key,
                                  "depth": {"buy": [{"price": 95.25}], "sell": [{"price": 95.26}]}}
        for key, o in self._opt_by_key.items():
            if key not in keys:
                continue
            cp = df * (self._F_syn - o.strike)          # C - P consistent with F_syn
            c_mid, p_mid = 0.60, 0.60 - cp
            mid = c_mid if o.option_type == "CE" else p_mid
            out[key] = {"instrument_token": key,
                        "depth": {"buy": [{"price": mid - 0.02}], "sell": [{"price": mid + 0.02}]}}
        return out


def test_options_source_emits_implied_forward_tick():
    cfg = DataSourceConfig(source_id="nse_usdinr_opt", source_type="retail_options",
                           symbols=["USDINR"])
    s = _StubOptionsSource(cfg, F_syn=95.34)
    s._fetch_once()
    t = s.get_tick("USDINR")
    assert t is not None
    assert t.extra["leg"] == "options"
    assert t.extra["instrument_kind"] == "options_forward"
    assert (t.bid + t.ask) / 2 == pytest.approx(95.34, abs=0.02)   # recovers F_syn
    assert t.extra["strikes_used"]


def test_retail_pipeline_wiring():
    from backend.core.data_sources.basis_pipeline import build_retail_arb_pipeline
    p = build_retail_arb_pipeline()
    assert p.legs == ("future", "options", "far")
    assert p.log_prefix == "retail"
    assert p.engine.config.enabled_leg_pairs == ("future_options", "future_far")
    assert [s.source_type for s in p.sources] == ["retail_future", "retail_options", "retail_far"]
    assert p.carry_calibrator is not None   # calibrate_carry_from_curve = True


# -- validity gates + carry calibration in the tick loop -------------------

import io  # noqa: E402
import time  # noqa: E402

from backend.core.data_sources.basis_pipeline import BasisPipeline  # noqa: E402
from backend.core.basis.basis_engine import BasisArbitrageEngine, BasisPersistenceTracker  # noqa: E402
from backend.core.normalization import CarryCalibrator, InstrumentNormalizer  # noqa: E402
from backend.core.interfaces.data_source import RawTick  # noqa: E402
from backend.config import RETAIL_ARB_CONFIG  # noqa: E402

_T_STAR = date(2026, 9, 28)
_FAR_EXP = date(2026, 10, 28)


class _StubLeg:
    def __init__(self, sid, leg, bid, ask, kind="future", expiry=_T_STAR, age_ms=0):
        self.source_id = sid
        self.source_type = f"retail_{leg}"
        self.is_connected = True
        self._leg, self._bid, self._ask, self._kind = leg, bid, ask, kind
        self._expiry, self._age_ms = expiry, age_ms

    def connect(self): self.is_connected = True
    def disconnect(self): self.is_connected = False

    def get_tick(self, _sym):
        return RawTick(
            symbol="USDINR", bid=self._bid, ask=self._ask,
            timestamp_ms=int(time.time() * 1000) - self._age_ms,
            source_id=self.source_id, last=(self._bid + self._ask) / 2,
            extra={"leg": self._leg, "instrument_kind": self._kind,
                   "expiry": self._expiry.isoformat() if self._expiry else None},
        )


def _pipeline(sources):
    p = BasisPipeline(
        sources=sources,
        normalizer=InstrumentNormalizer(target_expiry=_T_STAR,
                                        carry_rate_annual=RETAIL_ARB_CONFIG.carry_rate_annual),
        legs=("future", "options", "far"), log_prefix="retail",
        carry_calibrator=CarryCalibrator(fallback_annual=RETAIL_ARB_CONFIG.carry_rate_annual,
                                         ema_alpha=1.0),
        engine=BasisArbitrageEngine(RETAIL_ARB_CONFIG),
        tracker=BasisPersistenceTracker(RETAIL_ARB_CONFIG, mode="duration"),
    )
    p._out = io.StringIO()
    return p


def test_carry_calibrated_from_curve_kills_phantom_calendar_basis():
    # far priced 0.30 above near = a consistent ~3.8% forward premium, NOT arbitrage.
    # With the assumed 1.9% carry this used to report a ~15 pip "persistent" basis.
    p = _pipeline([
        _StubLeg("fut", "future", 94.9600, 94.9750),
        _StubLeg("far", "far", 95.2600, 95.2750, expiry=_FAR_EXP),
    ])
    row, events = p._tick_once()
    assert row["carry_source"] == "calibrated"
    assert 0.035 < row["carry_rate_annual"] < 0.042
    # near/far now agree at T* -> basis within a pip, nothing fires
    assert abs(row["basis_pips"]["future_far"]) < 1.5
    assert events == []


def test_wide_options_leg_is_suppressed_not_compared():
    p = _pipeline([
        _StubLeg("fut", "future", 94.9600, 94.9750),
        _StubLeg("opt", "options", 94.8000, 95.1500, kind="options_forward"),  # 35p wide
        _StubLeg("far", "far", 95.2600, 95.2750, expiry=_FAR_EXP),
    ])
    row, events = p._tick_once()
    assert "options" in row["suppressed_legs"]
    assert "future_options" not in row["basis_pips"]
    assert all(e.leg_pair != "future_options" for e in events)


def test_degenerate_ltp_quote_is_suppressed():
    p = _pipeline([
        _StubLeg("fut", "future", 94.9600, 94.9750),
        _StubLeg("far", "far", 95.10, 95.10, expiry=_FAR_EXP),   # bid == ask (LTP fallback)
    ])
    row, _ = p._tick_once()
    assert "far" in row["suppressed_legs"]
    assert "degenerate" in row["suppressed_legs"]["far"]


def test_stale_feed_is_suppressed():
    p = _pipeline([
        _StubLeg("fut", "future", 94.9600, 94.9750),
        _StubLeg("far", "far", 95.2600, 95.2750, expiry=_FAR_EXP, age_ms=60_000),  # 60s old
    ])
    row, _ = p._tick_once()
    assert "far" in row["suppressed_legs"]
    assert "stale" in row["suppressed_legs"]["far"]
