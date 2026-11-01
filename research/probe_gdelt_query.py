"""Print the query name the slice loop should use: "currency" if the API accepts
data/gdelt.CURRENCY_QUERY (valid JSON on one probe), "default" if it refuses it.
Waits out throttling (429 / plain-text refusals) for up to --max-minutes. Counts only."""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

from research.data.gdelt import CURRENCY_QUERY, GdeltQueryRejected, GdeltRateLimited, _request, decode_payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-minutes", type=float, default=90.0)
    args = parser.parse_args()
    deadline = time.monotonic() + args.max_minutes * 60
    request = _request(CURRENCY_QUERY, datetime(2024, 1, 2, 12, tzinfo=UTC), datetime(2024, 1, 2, 13, tzinfo=UTC), 250)
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = decode_payload(response.read().decode("utf-8", "replace"))
            print(f"probe ok: {len(payload.get('articles', []))} articles", file=sys.stderr)
            print("currency")
            return 0
        except GdeltQueryRejected as exc:
            print(f"probe rejected: {exc}", file=sys.stderr)
            print("default")
            return 0
        except (urllib.error.HTTPError, urllib.error.URLError, GdeltRateLimited, TimeoutError) as exc:
            print(f"probe throttled/failed ({exc}); waiting 120 s", file=sys.stderr)
            time.sleep(120)
    print("probe gave up; using default", file=sys.stderr)
    print("default")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
