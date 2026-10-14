"""
Get today's Kite Connect access token and write it into .env.

Kite access tokens expire every day (~06:00 IST). Run this once per day:

    python research/kite_login.py

1. It prints a login URL. Open it, log in to Zerodha.
2. The browser is sent to your app's Redirect URL with ?request_token=... in the
   address bar (with http://127.0.0.1 the page itself shows "can't connect";
   that is fine). Copy the whole address and paste it here.
3. It exchanges the request token for an access token (needs KITE_API_SECRET)
   and saves KITE_ACCESS_TOKEN in .env. No restart needed for the Live button.

Needs in .env: KITE_API_KEY and KITE_API_SECRET (from developers.kite.trade).
Read-only: only the session endpoint is called.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / ".env"
API = "https://api.kite.trade"


def _read_env() -> dict:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _set_env(key: str, value: str) -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def request_token_from(text: str) -> str:
    text = text.strip()
    if "request_token=" in text:
        return urllib.parse.parse_qs(urllib.parse.urlparse(text).query)["request_token"][0]
    return text


def exchange(api_key: str, api_secret: str, request_token: str) -> str:
    checksum = hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()
    body = urllib.parse.urlencode({"api_key": api_key, "request_token": request_token,
                                   "checksum": checksum}).encode()
    req = urllib.request.Request(f"{API}/session/token", data=body, method="POST",
                                 headers={"X-Kite-Version": "3"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())["data"]["access_token"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Kite rejected the token exchange ({e.code}): {e.read().decode()[:300]}\n"
                         "Request tokens are single-use and expire in minutes: log in again and retry.")


def main() -> int:
    env = {**_read_env(), **{k: v for k, v in os.environ.items() if k.startswith("KITE_")}}
    api_key, api_secret = env.get("KITE_API_KEY"), env.get("KITE_API_SECRET")
    if not api_key or not api_secret:
        print("Put KITE_API_KEY and KITE_API_SECRET (from developers.kite.trade -> your app) in .env first.")
        return 1
    print("1. Open this URL and log in to Zerodha:\n")
    print(f"   https://kite.zerodha.com/connect/login?v=3&api_key={api_key}\n")
    pasted = sys.argv[1] if len(sys.argv) > 1 else input("2. Paste the address you were redirected to: ")
    token = exchange(api_key, api_secret, request_token_from(pasted))
    _set_env("KITE_ACCESS_TOKEN", token)
    print(f"Saved KITE_ACCESS_TOKEN to {ENV} (valid until ~06:00 IST tomorrow).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
