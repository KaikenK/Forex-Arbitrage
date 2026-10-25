"""HistData.com bulk 1-minute bars: downloader and event-windowed loader.

Why this exists. The 2026-10-24 pilot (RESEARCH_TRACKER §6) showed that fetching Dukascopy
hour by hour is limited by the server, not the client: ~11 s per pair-hour even with four
workers, and 8% of hours failed under load. HistData publishes a complete year of 1-minute
bars per pair as a single ~3.5 MB zip. One full year of EUR/USD (372,607 bars) downloaded in
2.7 s, so ~8 downloads per pair replace ~5,000 hourly requests.

Data notes:
  - Timestamps are US Eastern wall-clock time *with* DST, despite HistData's "EST without
    DST" description. `parse_histdata_m1_line` converts via America/New_York. A fixed −5 h
    (used until 2026-10-24) labelled every US-summer bar an hour late; it was caught because
    summer releases reacted at +61 min instead of +1 min (RESEARCH_TRACKER §6).
  - Prices are bid-side M1 closes.
  - The study needs only the event windows, so `load_windowed_series` streams each zip and
    keeps bars whose hour is in the plan (~1.2M bars instead of ~12M), keeping memory low.
"""
from __future__ import annotations

import io
import os
import re
import threading
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from research.eventstudy.schema import PriceSeries

from .prices_intraday import parse_histdata_m1_line

PAGE_URL = "https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{slug}/{year}"
GET_URL = "https://www.histdata.com/get.php"
USER_AGENT = "Mozilla/5.0"
BAR_DURATION = timedelta(minutes=1)

HttpGet = Callable[[str, dict[str, str]], bytes]
HttpPost = Callable[[str, bytes, dict[str, str]], bytes]


class HistDataError(RuntimeError):
    """HistData returned something other than the expected zip."""


def _slug(pair: str) -> str:
    return pair.replace("/", "").lower()


def zip_path(cache_dir: Path, pair: str, year: int) -> Path:
    upper = pair.replace("/", "").upper()
    return Path(cache_dir) / upper / f"HISTDATA_COM_ASCII_{upper}_M1_{year}.zip"


def _http_get(url: str, headers: dict[str, str]) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
        return response.read()


def _http_post(url: str, data: bytes, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def _form_fields(html: str) -> dict[str, str]:
    """Hidden fields of the download form, including the per-session token `tk`."""
    form = re.search(r'<form[^>]*id="file_down"[^>]*>.*?</form>', html, re.S)
    if form is None:
        raise HistDataError("download form not found on the HistData page")
    fields = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form.group(0)))
    if not fields.get("tk"):
        raise HistDataError("download token missing from the HistData form")
    return fields


def download_year(
    pair: str,
    year: int,
    cache_dir: Path,
    *,
    http_get: HttpGet | None = None,
    http_post: HttpPost | None = None,
) -> tuple[Path, bool]:
    """Ensure one pair-year zip is cached. Returns (path, downloaded_now).

    Validates that the response is really a zip: on errors HistData can return an HTML page,
    which must never be cached as if it were data. The write is atomic.
    """
    path = zip_path(cache_dir, pair, year)
    if path.exists() and zipfile.is_zipfile(path):
        return path, False

    page = PAGE_URL.format(slug=_slug(pair), year=year)
    html = (http_get or _http_get)(page, {"User-Agent": USER_AGENT}).decode("utf-8", "replace")
    fields = _form_fields(html)
    raw = (http_post or _http_post)(
        GET_URL,
        urllib.parse.urlencode(fields).encode("ascii"),
        {"User-Agent": USER_AGENT, "Referer": page, "Content-Type": "application/x-www-form-urlencoded"},
    )
    if not raw.startswith(b"PK") or not zipfile.is_zipfile(io.BytesIO(raw)):
        raise HistDataError(f"HistData returned no zip for {pair} {year} ({len(raw)} bytes)")

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temp_path.write_bytes(raw)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return path, True


def iter_m1_bars(path: Path) -> Iterator[tuple[datetime, float]]:
    """Stream (UTC bar time, close) from a cached zip without extracting it to disk."""
    with zipfile.ZipFile(path) as archive:
        member = next((name for name in archive.namelist() if name.lower().endswith(".csv")), None)
        if member is None:
            raise HistDataError(f"no CSV inside {path.name}")
        with archive.open(member) as handle:
            for line in io.TextIOWrapper(handle, encoding="utf-8"):
                parsed = parse_histdata_m1_line(line)
                if parsed is not None:
                    yield parsed


def load_windowed_series(
    pair: str,
    hours: Iterable[datetime],
    cache_dir: Path,
) -> tuple[PriceSeries, list[int]]:
    """Minute bars for `pair` restricted to the given UTC hours, stamped at bar **close**.

    HistData labels a bar by its start (13:30) but its close is observed at the end
    (13:30:59). The event study treats a bar's time as when its price became known, so
    bars are re-stamped to start + 1 minute. Without this, for a 13:30 release the "entry"
    bar (first at/after 13:30) would be the 13:30 bar, whose close already contains up to a
    minute of reaction — understating exactly the 1-minute jump the positive control tests.

    Returns the series and the list of years that were needed but not cached, so a caller
    can report missing coverage instead of silently analysing a partial sample.
    """
    keep = {hour.astimezone(UTC).replace(minute=0, second=0, microsecond=0) for hour in hours}
    years = sorted({hour.year for hour in keep})
    times: list[datetime] = []
    prices: list[float] = []
    missing: list[int] = []
    for year in years:
        path = zip_path(cache_dir, pair, year)
        if not path.exists():
            missing.append(year)
            continue
        for stamp, close in iter_m1_bars(path):
            if stamp.replace(minute=0, second=0) in keep:
                times.append(stamp + BAR_DURATION)
                prices.append(close)
    if any(later < earlier for earlier, later in zip(times, times[1:])):
        order = sorted(range(len(times)), key=times.__getitem__)
        times = [times[i] for i in order]
        prices = [prices[i] for i in order]
    return PriceSeries(pair=pair, times=times, prices=prices), missing
