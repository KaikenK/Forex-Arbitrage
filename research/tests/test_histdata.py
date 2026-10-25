"""HistData bulk download + windowed loading, and the alignment exit-lag guard.

No network: HTTP is injected.
"""
from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research.data.histdata import HistDataError, download_year, iter_m1_bars, load_windowed_series, zip_path
from research.data.prices_intraday import parse_histdata_m1_line
from research.eventstudy.alignment import align_event
from research.eventstudy.schema import Event, PriceSeries

FORM_PAGE = b"""<html><form id="file_down" action="/get.php" method="post">
<input type="hidden" name="tk" value="token123" />
<input type="hidden" name="date" value="2018" />
<input type="hidden" name="datemonth" value="2018" />
<input type="hidden" name="platform" value="ASCII" />
<input type="hidden" name="timeframe" value="M1" />
<input type="hidden" name="fxpair" value="EURUSD" />
</form></html>"""


def _zip(lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("DAT_ASCII_EURUSD_M1_2018.csv", "\n".join(lines))
        archive.writestr("DAT_ASCII_EURUSD_M1_2018.txt", "status report")
    return buffer.getvalue()


# HistData stamps are New York wall clock with DST. 08:30 EST (winter) == 13:30 UTC.
LINES = [
    "20180202 082800;1.24950;1.24950;1.24940;1.24947;0",
    "20180202 082900;1.24947;1.24950;1.24900;1.24902;0",
    "20180202 083000;1.24902;1.24902;1.24790;1.24795;0",
    "20180202 093000;1.24600;1.24600;1.24600;1.24600;0",  # 14:30 UTC: a different hour
    "not a bar line",
]


class FakeHttp:
    def __init__(self, post_body: bytes) -> None:
        self.post_body = post_body
        self.gets = 0
        self.posts: list[tuple[str, bytes, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str]) -> bytes:
        self.gets += 1
        return FORM_PAGE

    def post(self, url: str, data: bytes, headers: dict[str, str]) -> bytes:
        self.posts.append((url, data, headers))
        return self.post_body


def test_line_parser_converts_new_york_wall_clock_to_utc_with_dst() -> None:
    stamp, close = parse_histdata_m1_line("20180202 083000;1.1;1.1;1.1;1.24795;0")
    assert stamp == datetime(2018, 2, 2, 13, 30, tzinfo=UTC)
    # Summer: the file is on EDT, so the 08:30 payrolls bar is 12:30 UTC. The old fixed-EST
    # parse put it at 13:30, an hour late, and summer reactions surfaced at +61 min.
    assert parse_histdata_m1_line("20180706 083000;1;1;1;1.17;0")[0] == datetime(2018, 7, 6, 12, 30, tzinfo=UTC)
    # Day after the March 2018 switch (Sunday 11 March): already EDT.
    assert parse_histdata_m1_line("20180312 083000;1;1;1;1.23;0")[0] == datetime(2018, 3, 12, 12, 30, tzinfo=UTC)
    # A fixed offset can still be forced explicitly.
    assert parse_histdata_m1_line("20180706 083000;1;1;1;1.17;0", tz_offset_hours=-5)[0] == \
        datetime(2018, 7, 6, 13, 30, tzinfo=UTC)
    assert close == pytest.approx(1.24795)
    assert parse_histdata_m1_line("garbage") is None


def test_download_posts_the_form_token_and_caches_the_zip(tmp_path: Path) -> None:
    http = FakeHttp(_zip(LINES))
    path, downloaded = download_year("EUR/USD", 2018, tmp_path, http_get=http.get, http_post=http.post)

    assert downloaded and path == zip_path(tmp_path, "EUR/USD", 2018) and path.exists()
    url, data, headers = http.posts[0]
    assert b"tk=token123" in data
    assert headers["Referer"].endswith("/eurusd/2018"), "HistData requires the page as Referer"


def test_second_download_is_served_from_cache(tmp_path: Path) -> None:
    download_year("EUR/USD", 2018, tmp_path, http_get=FakeHttp(_zip(LINES)).get, http_post=FakeHttp(_zip(LINES)).post)
    http = FakeHttp(_zip(LINES))
    _, downloaded = download_year("EUR/USD", 2018, tmp_path, http_get=http.get, http_post=http.post)
    assert not downloaded and http.gets == 0 and not http.posts


def test_html_error_page_is_never_cached(tmp_path: Path) -> None:
    http = FakeHttp(b"<html>Too many requests</html>")
    with pytest.raises(HistDataError):
        download_year("EUR/USD", 2018, tmp_path, http_get=http.get, http_post=http.post)
    assert not zip_path(tmp_path, "EUR/USD", 2018).exists()
    assert not any(tmp_path.rglob("*.tmp"))


def test_page_without_form_is_an_error(tmp_path: Path) -> None:
    def no_form(url: str, headers: dict[str, str]) -> bytes:
        return b"<html>maintenance</html>"

    with pytest.raises(HistDataError):
        download_year("EUR/USD", 2018, tmp_path, http_get=no_form, http_post=FakeHttp(b"").post)


def test_windowed_loader_keeps_only_planned_hours(tmp_path: Path) -> None:
    download_year("EUR/USD", 2018, tmp_path, http_get=FakeHttp(_zip(LINES)).get, http_post=FakeHttp(_zip(LINES)).post)
    hours = [datetime(2018, 2, 2, 13, 0, tzinfo=UTC)]
    series, missing = load_windowed_series("EUR/USD", hours, tmp_path)

    assert missing == []
    # Re-stamped to bar close: the 13:28 bar's price is known at 13:29, and so on.
    assert [t.strftime("%H:%M") for t in series.times] == ["13:29", "13:30", "13:31"]
    assert len(list(iter_m1_bars(zip_path(tmp_path, "EUR/USD", 2018)))) == 4, "malformed line skipped"


def test_entry_for_a_release_is_the_pre_release_price(tmp_path: Path) -> None:
    """For a 13:30 release, entry must be the 13:29 bar's close, not the 13:30 bar's.

    The 13:30 bar closes at 13:30:59 and already contains the reaction; using it as entry
    would understate the 1-minute jump.
    """
    download_year("EUR/USD", 2018, tmp_path, http_get=FakeHttp(_zip(LINES)).get, http_post=FakeHttp(_zip(LINES)).post)
    series, _ = load_windowed_series("EUR/USD", [datetime(2018, 2, 2, 13, 0, tzinfo=UTC)], tmp_path)
    release = datetime(2018, 2, 2, 13, 30, tzinfo=UTC)

    observation = align_event(_event(release), series, [1])
    assert observation.entry_price == pytest.approx(1.24902), "close of the 13:29 bar"
    assert observation.horizon_returns[1] == pytest.approx(1.24795 / 1.24902 - 1.0)


def test_windowed_loader_reports_uncached_years(tmp_path: Path) -> None:
    _, missing = load_windowed_series("EUR/USD", [datetime(2019, 5, 1, 12, tzinfo=UTC)], tmp_path)
    assert missing == [2019]


def _event(at: datetime) -> Event:
    return Event(event_id="e", timestamp=at, pair="EUR/USD", categories=("cpi",), surprise=1.0, source="calendar")


def test_exit_lag_guard_rejects_a_return_that_spans_a_data_gap() -> None:
    """A 60-minute horizon must not silently become a return over a multi-hour gap."""
    start = datetime(2018, 2, 2, 13, 30, tzinfo=UTC)
    series = PriceSeries(
        pair="EUR/USD",
        times=[start, start + timedelta(minutes=5), start + timedelta(hours=5)],  # gap after +5m
        prices=[1.2000, 1.2010, 1.2500],
    )
    guarded = align_event(_event(start), series, [5, 60], max_exit_lag_minutes=5)
    assert guarded is not None
    assert set(guarded.horizon_returns) == {5}, "the 60m exit bar is 4h late and must be dropped"

    unguarded = align_event(_event(start), series, [5, 60])
    assert set(unguarded.horizon_returns) == {5, 60}, "default behaviour is unchanged"
