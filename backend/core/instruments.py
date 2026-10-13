"""
Instrument registry: the single source of truth for per-pair price conventions.

Every place that turns a price difference into pips, rounds a price for
display, or sizes a contract reads it from here. Before this, the same
`0.01 if "JPY" in symbol else 0.0001` rule was copied around the codebase, and
several copies forgot INR.

Conventions:
- INR pairs use pip = 0.01 INR (one paisa), the unit Indian exchanges and
  dealers quote spreads in. The exchange tick is 0.0025 (a quarter paisa).
- JPY-quoted pairs use pip = 0.01; other pairs use 0.0001.
- JPYINR is quoted per 100 JPY on NSE/BSE (`quote_unit` = 100).

Unknown but well-formed pairs (six letters) fall back to the rule by quote
currency, so a new pair works before it is added here, just without exact
exchange tick or contract data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class FuturesSpec:
    """An exchange-listed currency future (NSE and BSE list the same contracts)."""
    tick_size: float                       # minimum price step
    lot_base_units: float                  # base currency per contract
    exchanges: Tuple[str, ...] = ("NSE", "BSE")


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    base: str
    quote: str
    pip_size: float
    tick_size: float
    price_decimals: int
    contract_size: float = 100_000.0  # units of base per lot/contract
    quote_unit: int = 1               # price is quoted per `quote_unit` of base
    known: bool = True                # False for a fallback built from the quote currency
    ref_price: Optional[float] = None  # rough level, seeds the synthetic generator
    futures: Optional[FuturesSpec] = None

    @property
    def display(self) -> str:
        """'USD/INR', or 'JPY/INR (per 100)'."""
        pair = f"{self.base}/{self.quote}"
        return pair if self.quote_unit == 1 else f"{pair} (per {self.quote_unit})"

    def to_pips(self, price_diff: float) -> float:
        return price_diff / self.pip_size

    def min_executable_pips(self) -> float:
        """One exchange tick in pips: the smallest edge a crossed futures book can show."""
        tick = self.futures.tick_size if self.futures else self.tick_size
        return tick / self.pip_size


def _fx(symbol: str, pip: float, tick: float, decimals: int, ref: float, *,
        contract: float = 100_000.0, quote_unit: int = 1,
        futures: Optional[FuturesSpec] = None) -> InstrumentSpec:
    return InstrumentSpec(symbol, symbol[:3], symbol[3:], pip, tick, decimals, contract,
                          quote_unit, ref_price=ref, futures=futures)


# NSE/BSE currency futures (SEBI contract specs): INR pairs tick 0.25 paise,
# lot 1,000 base (JPYINR 100,000 JPY, priced per 100 JPY); cross-currency
# pairs lot 1,000 base, tick 0.0001 (USDJPY 0.01).
_INR_FUT = FuturesSpec(tick_size=0.0025, lot_base_units=1_000.0)
_CROSS_FUT = FuturesSpec(tick_size=0.0001, lot_base_units=1_000.0)

REGISTRY: Dict[str, InstrumentSpec] = {s.symbol: s for s in (
    _fx("USDINR", 0.01, 0.0025, 4, 88.0, contract=1_000.0, futures=_INR_FUT),
    _fx("EURINR", 0.01, 0.0025, 4, 102.0, contract=1_000.0, futures=_INR_FUT),
    _fx("GBPINR", 0.01, 0.0025, 4, 117.0, contract=1_000.0, futures=_INR_FUT),
    _fx("JPYINR", 0.01, 0.0025, 4, 59.0, contract=100_000.0, quote_unit=100,
        futures=FuturesSpec(tick_size=0.0025, lot_base_units=100_000.0)),
    _fx("EURUSD", 0.0001, 0.00001, 5, 1.16, futures=_CROSS_FUT),
    _fx("GBPUSD", 0.0001, 0.00001, 5, 1.33, futures=_CROSS_FUT),
    _fx("USDJPY", 0.01, 0.001, 3, 150.0, futures=FuturesSpec(tick_size=0.01, lot_base_units=1_000.0)),
    # Spot / OTC only
    _fx("AUDUSD", 0.0001, 0.00001, 5, 0.65),
    _fx("NZDUSD", 0.0001, 0.00001, 5, 0.58),
    _fx("USDCAD", 0.0001, 0.00001, 5, 1.39),
    _fx("USDCHF", 0.0001, 0.00001, 5, 0.80),
    _fx("EURGBP", 0.0001, 0.00001, 5, 0.87),
    _fx("EURJPY", 0.01, 0.001, 3, 174.0),
    _fx("GBPJPY", 0.01, 0.001, 3, 200.0),
)}

# Pairs with futures on NSE and BSE: what the live exchange legs can trade.
EXCHANGE_FUTURES_SYMBOLS: List[str] = [s for s, spec in REGISTRY.items() if spec.futures]

# Named sets accepted in ARBEX_SYMBOLS
SYMBOL_PRESETS: Dict[str, List[str]] = {
    "ALL": list(EXCHANGE_FUTURES_SYMBOLS),
    "INR": [s for s in EXCHANGE_FUTURES_SYMBOLS if s.endswith("INR")],
    "CROSS": [s for s in EXCHANGE_FUTURES_SYMBOLS if not s.endswith("INR")],
}

# Quote currencies whose pip is 0.01 rather than 0.0001
_HUNDREDTH_PIP_QUOTES = {"JPY", "INR"}

_PAIR_RE = re.compile(r"^[A-Z]{6}$")


class UnknownInstrumentError(ValueError):
    pass


def normalize_symbol(symbol: str) -> str:
    """'usd/inr', 'USD_INR', ' USDINR ' -> 'USDINR'."""
    return re.sub(r"[^A-Za-z]", "", symbol or "").upper()


def _fallback(symbol: str) -> InstrumentSpec:
    quote = symbol[3:]
    if quote in _HUNDREDTH_PIP_QUOTES:
        return InstrumentSpec(symbol, symbol[:3], quote, 0.01, 0.001, 3, known=False)
    return InstrumentSpec(symbol, symbol[:3], quote, 0.0001, 0.00001, 5, known=False)


def get_instrument(symbol: str) -> InstrumentSpec:
    """Spec for a pair. Unregistered six-letter pairs get a quote-currency fallback."""
    sym = normalize_symbol(symbol)
    spec = REGISTRY.get(sym)
    if spec is not None:
        return spec
    if not _PAIR_RE.match(sym):
        raise UnknownInstrumentError(f"not a currency pair: {symbol!r}")
    return _fallback(sym)


def pip_size(symbol: str) -> float:
    """Price change of one pip. Tolerates odd symbols (falls back to 0.0001)."""
    try:
        return get_instrument(symbol).pip_size
    except UnknownInstrumentError:
        return 0.0001


def to_pips(price_diff: float, symbol: str) -> float:
    return price_diff / pip_size(symbol)


def parse_symbol_list(raw: str | Iterable[str]) -> List[str]:
    """'USDINR, eur/inr' -> ['USDINR', 'EURINR']. Order kept, duplicates dropped.

    Presets: 'all' (every pair with NSE/BSE futures), 'inr' (the four INR
    pairs), 'cross' (EURUSD, GBPUSD, USDJPY futures).

    Raises UnknownInstrumentError on anything that is not a currency pair, so a
    typo in configuration fails at startup instead of producing an empty feed.
    """
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    out: List[str] = []
    for item in items:
        if not str(item).strip():
            continue
        preset = SYMBOL_PRESETS.get(str(item).strip().upper())
        for sym in preset or [get_instrument(str(item)).symbol]:
            if sym not in out:
                out.append(sym)
    return out


def resolve_symbols(requested: Iterable[str], supported: Iterable[str],
                    fallback: Iterable[str]) -> tuple[List[str], List[str]]:
    """Split the requested pairs into those a feed can serve and those it can't.

    Returns (symbols, unsupported). If none of the requested pairs is
    supported, `symbols` is the feed's `fallback`, so the dashboard shows data
    and the caller can say why, rather than starting an empty feed.
    """
    supported_set = set(parse_symbol_list(supported))
    wanted = parse_symbol_list(requested)
    ok = [s for s in wanted if s in supported_set]
    unsupported = [s for s in wanted if s not in supported_set]
    return (ok or parse_symbol_list(fallback)), unsupported


def futures_lot_units(symbol: str) -> float:
    """Base currency per NSE/BSE futures contract (1,000 when not exchange-listed)."""
    fut = get_instrument(symbol).futures
    return fut.lot_base_units if fut else 1_000.0
