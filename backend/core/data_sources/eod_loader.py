"""
End-of-day (EOD) loaders for the USD/INR basis study.

Zero-KYC, zero-cost historical path: daily settlement / close prices for each leg
from official published data. Runs the *same* Option-A normalisation and basis
maths as the live pipeline, at daily granularity, so a first-pass onshore-offshore
basis analysis is available before any broker account exists.

Loading strategy (per leg):
  1. a local CSV if a path is given  (the reliable, dependency-free core);
  2. otherwise an optional online fetcher (yfinance / jugaad-data), lazily
     imported — never a hard dependency;
  3. otherwise a clear error naming where to get the file.

CSV parsing is column-name tolerant: it recognises common headers from NSE
bhavcopy, CME settlement files, investing.com exports and yfinance dumps.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

LEGS = ("onshore", "offshore", "spot", "reference")

# column-name aliases (lower-cased) -> canonical field
_ALIASES = {
    "date": {"date", "timestamp", "trade_date", "trade date", "traddt", "tstamp",
             "fh_timestamp"},
    "open": {"open", "open_price", "open price", "opnpric", "fh_opening_price"},
    "high": {"high", "high_price", "high price", "hghpric", "fh_trade_high_price"},
    "low": {"low", "low_price", "low price", "lwpric", "fh_trade_low_price"},
    "close": {"close", "close_price", "close price", "clspric", "fh_closing_price",
              "adj close", "price", "last", "ltp", "last_price", "last traded price"},
    "settle": {"settle", "settle_price", "settle price", "settle_pr", "settlement",
               "sttlmpric", "fh_settle_price", "daily settlement price",
               "settlement price"},
    "volume": {"volume", "vol", "vol.", "tot_traded_qty", "qty",
               "fh_tot_traded_qty", "no. of contracts", "no of contracts"},
    "open_interest": {"open_interest", "open interest", "openinterest", "oi",
                      "open_int", "opnintr", "fh_open_int"},
    "expiry": {"expiry", "expiry_dt", "expiry date", "xpirydt", "expiry_date",
               "fh_expiry_dt", "expdt"},
    # NSE's derivatives export puts the full contract code (e.g. 'USDINR 261126')
    # in a column labelled 'Underlyings' or 'Contracts'.
    "contract_code": {"contracts", "contract", "underlyings", "underlying",
                      "tradingsymbol", "instrument_code"},
    "symbol": {"symbol", "ticker", "tckrsymb", "instrument"},
}

_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%b %d, %Y", "%d %b %Y",
                 "%Y%m%d")
# numeric formats like 08/28/2026 are ambiguous — resolved per-file by
# _detect_numeric_date_format() rather than guessed here.


@dataclass(frozen=True)
class EODBar:
    trade_date: date
    leg: str
    source: str
    symbol: str
    close: float                 # close or settlement — see `settle`
    settle: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    expiry: Optional[date] = None

    @property
    def mark(self) -> float:
        """Preferred daily mark: settlement if present, else close."""
        return self.settle if self.settle is not None else self.close

    def to_dict(self) -> dict:
        return {
            "trade_date": self.trade_date.isoformat(),
            "leg": self.leg,
            "source": self.source,
            "symbol": self.symbol,
            "mark": round(self.mark, 5),
            "settle": self.settle,
            "close": self.close,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "open_interest": self.open_interest,
        }




def _parse_date(v: str, numeric_fmt: Optional[str] = None) -> Optional[date]:
    v = (v or "").strip().strip('"')
    if not v:
        return None
    if numeric_fmt and ("/" in v or "." in v):
        try:
            return datetime.strptime(v.split(" ")[0], numeric_fmt).date()
        except ValueError:
            pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v[:24], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(v).date()
    except ValueError:
        return None


def _detect_numeric_date_format(samples: List[str]) -> Optional[str]:
    """
    Resolve ambiguous `NN/NN/YYYY` / `NN.NN.YYYY` dates for one file: if any
    first-field value exceeds 12 it must be the day (D/M/Y); if any second-field
    value exceeds 12 it must be the month-second (M/D/Y). Default to M/D/Y
    (investing.com / US exports) when unresolved.
    """
    sep = None
    parts_list = []
    for s in samples:
        s = s.strip().strip('"').split(" ")[0]
        for c in ("/", "."):
            if s.count(c) == 2:
                sep = c
                parts_list.append(s.split(c))
                break
    if not parts_list:
        return None
    first_max = max((int(p[0]) for p in parts_list if p[0].isdigit()), default=0)
    second_max = max((int(p[1]) for p in parts_list if p[1].isdigit()), default=0)
    if first_max > 12 and second_max <= 12:
        return f"%d{sep}%m{sep}%Y"
    if second_max > 12 and first_max <= 12:
        return f"%m{sep}%d{sep}%Y"
    # unresolved (all fields <= 12) — assume month-first
    return f"%m{sep}%d{sep}%Y"




def _parse_contract_expiry(code: str) -> Optional[date]:
    """
    Extract expiry from an NSE contract code, e.g. 'USDINR 280926' / 'USDINR280926'
    / 'USDINR26SEPFUT' -> 2026-09-28.  Returns None if not recognised.
    """
    import re
    s = (code or "").strip().upper().replace(" ", "")
    m = re.search(r"(\d{6})(?:FUT)?$", s)          # DDMMYY
    if m:
        dd, mm, yy = int(m.group(1)[:2]), int(m.group(1)[2:4]), int(m.group(1)[4:])
        try:
            return date(2000 + yy, mm, dd)
        except ValueError:
            return None
    m = re.search(r"(\d{2})([A-Z]{3})(\d{2})?FUT", s)   # 26SEP / 26SEP26
    if m:
        mon = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
               "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
        try:
            from backend.core.normalization import month_end_expiry_estimate
            yy = int(m.group(3) or m.group(1))
            return month_end_expiry_estimate(2000 + yy, mon[m.group(2)])
        except (KeyError, ValueError, ImportError):
            return None
    return None


def _rescale(bars: List["EODBar"], factor: float) -> List["EODBar"]:
    if factor == 1.0:
        return bars
    out = []
    for b in bars:
        inv = factor < 0                    # factor < 0 flags "invert then scale by |factor|"
        num = abs(factor)
        f = (lambda x: (num / x) if x else x) if inv else (lambda x: x * factor)
        out.append(EODBar(
            trade_date=b.trade_date, leg=b.leg, source=b.source, symbol="USDINR",
            close=f(b.close) if b.close is not None else None,
            settle=f(b.settle) if b.settle is not None else None,
            open=f(b.open) if b.open is not None else None,
            high=f(b.high) if b.high is not None else None,
            low=f(b.low) if b.low is not None else None,
            volume=b.volume, open_interest=b.open_interest, expiry=b.expiry,
        ))
    return out


def normalise_convention(
    bars: List["EODBar"], convention: str = "auto"
) -> List["EODBar"]:
    """
    Bring an offshore/OTC series to the USD/INR convention (~80-100).
      - "USDINR"  : leave as-is
      - "INRUSD"  : reciprocal (CME 6R / E-micro quote USD-per-INR, ~0.012)
      - "USDINR_x100" : divide by 100 (paise)
      - "INRUSD_x10000" : 10000 / price  (investing.com "Indian Rupee Futures"
        quotes 1/USD-INR x 10000, ~104-117; USD/INR = 10000 / quote)
      - "auto"    : infer from the median mark (does NOT pick INRUSD_x10000 -
        its range overlaps a legitimate USD/INR near 100, so pass it explicitly)
    """
    if not bars:
        return bars
    marks = sorted(b.mark for b in bars if b.mark)
    med = marks[len(marks) // 2] if marks else 0.0
    if convention == "auto":
        if 0 < med < 1.0:
            convention = "INRUSD"
        elif med > 1000:
            convention = "USDINR_x100"
        else:
            convention = "USDINR"
    if convention == "USDINR":
        return bars
    if convention == "INRUSD":
        logger.info("[eod_loader] converting INR/USD (med %.5f) -> USD/INR", med)
        return _rescale(bars, -1.0)
    if convention == "USDINR_x100":
        logger.info("[eod_loader] rescaling x100 series (med %.1f) -> USD/INR", med)
        return _rescale(bars, 0.01)
    if convention == "INRUSD_x10000":
        logger.info("[eod_loader] converting 1/USDINR x 10000 (med %.1f) -> USD/INR", med)
        return _rescale(bars, -10000.0)
    raise ValueError(f"unknown convention {convention!r}")


def front_month(bars: List["EODBar"]) -> List["EODBar"]:
    """
    Collapse a multi-contract series to one bar per trade date.

    NSE currency now lists **weekly** USD/INR futures alongside the monthlies, and
    the weeklies are thin (settlement prices drift on low OI). "Nearest expiry"
    would pick those, so we instead pick, per trade date, the **most liquid**
    contract with >= 7 days to expiry — highest open interest, then highest volume,
    then nearest expiry as a last resort. Bars without an expiry are kept as-is.
    """
    by_date: dict = {}
    groups: dict = {}
    for b in bars:
        if b.expiry is None:
            by_date.setdefault(b.trade_date, b)
            continue
        if (b.expiry - b.trade_date).days < 7:
            continue
        groups.setdefault(b.trade_date, []).append(b)

    def _liq(b):
        return (b.open_interest or 0, b.volume or 0, -((b.expiry - b.trade_date).days))

    for d, gs in groups.items():
        if d in by_date:            # an expiry-less bar already claimed this date
            continue
        by_date[d] = max(gs, key=_liq)
    return sorted(by_date.values(), key=lambda x: x.trade_date)


def _num(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s in ("-", "NA", "N/A", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _canon_header(headers: Iterable[str]) -> dict:
    """Map each raw header to a canonical field name where recognised."""
    out = {}
    for h in headers:
        hl = h.strip().lower()
        for canon, names in _ALIASES.items():
            if hl in names:
                out[h] = canon
                break
    return out


def load_eod_csv(
    path: str | Path,
    *,
    leg: str,
    symbol: str = "USDINR",
    source: Optional[str] = None,
    near_month: bool = False,
    convention: str = "auto",
) -> List[EODBar]:
    """
    Load daily bars from a column-tolerant CSV. Rows without a date/mark are
    skipped. If the file lists several contracts per day (NSE derivatives export
    has a 'CONTRACTS' column) pass ``near_month=True`` to keep only the front
    month; expiry is read from an expiry column or parsed from the contract code.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"EOD CSV not found: {path}")
    source = source or path.name

    _head = path.read_text(encoding="utf-8-sig", errors="replace").lstrip()[:200].lower()
    if _head.startswith(("<html", "<!doctype", "<?xml")) or "incapsula" in _head:
        raise ValueError(
            f"{path.name}: this is an HTML page, not a CSV — the download was "
            f"bot-blocked or returned an error page. Open the source in a browser "
            f"and export the CSV by hand (see data/eod/README.md)."
        )

    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cmap = _canon_header(reader.fieldnames or [])
        if "date" not in cmap.values():
            raise ValueError(f"{path.name}: no recognised date column in {reader.fieldnames}")
        if "close" not in cmap.values() and "settle" not in cmap.values():
            raise ValueError(
                f"{path.name}: found a date column but no price column in "
                f"{reader.fieldnames}. Rename the price/settlement column to "
                f"'Close' or 'Settle' (or add its header to eod_loader._ALIASES)."
            )
        rows_raw = [{cmap.get(k, k): v for k, v in raw.items()} for raw in reader]
        numeric_fmt = _detect_numeric_date_format(
            [r.get("date", "") for r in rows_raw[:400]]
        )
        if numeric_fmt:
            logger.info("[eod_loader] %s: numeric date format %s", path.name, numeric_fmt)

        bars: List[EODBar] = []
        for row in rows_raw:
            d = _parse_date(row.get("date", ""), numeric_fmt)
            close = _num(row.get("close"))
            settle = _num(row.get("settle"))
            if d is None or (close is None and settle is None):
                continue
            expiry = (_parse_date(row.get("expiry", ""), numeric_fmt)
                      or _parse_contract_expiry(row.get("contract_code", "")))
            bars.append(EODBar(
                trade_date=d,
                leg=leg,
                source=source,
                symbol=row.get("symbol") or symbol,
                close=close if close is not None else settle,
                settle=settle,
                open=_num(row.get("open")),
                high=_num(row.get("high")),
                low=_num(row.get("low")),
                volume=_num(row.get("volume")),
                open_interest=_num(row.get("open_interest")),
                expiry=expiry,
            ))

    multi = len(bars) > len({b.trade_date for b in bars})
    if near_month or (multi and any(b.expiry for b in bars)):
        n_before = len(bars)
        bars = front_month(bars)
        logger.info("[eod_loader] %s: front-month filter %d -> %d bars",
                    path.name, n_before, len(bars))

    bars = normalise_convention(bars, convention)
    bars.sort(key=lambda b: b.trade_date)
    logger.info("[eod_loader] %s: %d bars %s..%s",
                path.name, len(bars),
                bars[0].trade_date if bars else "-", bars[-1].trade_date if bars else "-")
    return bars


# --------------------------------------------------------------------------
# optional online fetchers — lazily imported, never a hard dependency
# --------------------------------------------------------------------------

def fetch_yfinance(ticker: str, start: str, end: str, *, leg: str) -> List[EODBar]:
    """
    Free, no-account daily OHLC via yfinance. Good for:
      - offshore leg: ticker "INR=F"  (CME Indian Rupee future, continuous)
      - spot / reference: ticker "USDINR=X"
    `pip install yfinance` to enable.
    """
    try:
        import yfinance as yf  # type: ignore
    except ImportError as e:
        raise RuntimeError("yfinance not installed — `pip install yfinance` "
                           "or supply a CSV") from e
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker}")
    # recent yfinance returns MultiIndex columns ('Close', ticker) — flatten
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df = df.droplevel(1, axis=1)

    def _f(row, col):
        try:
            v = row[col]
            return float(v.iloc[0] if hasattr(v, "iloc") else v)
        except (KeyError, TypeError, ValueError):
            return None

    bars: List[EODBar] = []
    for ts, r in df.iterrows():
        c = _f(r, "Close")
        if c is None:
            continue
        bars.append(EODBar(
            trade_date=ts.date(),
            leg=leg,
            source=f"yfinance:{ticker}",
            symbol="USDINR",
            close=c,
            open=_f(r, "Open"),
            high=_f(r, "High"),
            low=_f(r, "Low"),
            volume=_f(r, "Volume"),
        ))
    return bars


def load_leg(
    leg: str,
    *,
    csv_path: Optional[str | Path] = None,
    yf_ticker: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    convention: str = "auto",
) -> List[EODBar]:
    """Resolve one leg's daily bars: CSV first, then optional yfinance."""
    if leg not in LEGS:
        raise ValueError(f"unknown leg {leg!r}")
    if csv_path:
        return load_eod_csv(csv_path, leg=leg, convention=convention)
    if yf_ticker and start and end:
        bars = fetch_yfinance(yf_ticker, start, end, leg=leg)
        return normalise_convention(bars, convention)
    raise ValueError(
        f"leg {leg!r}: give csv_path, or (yf_ticker + start + end). "
        f"See data/eod/README.md for where to get the files."
    )
