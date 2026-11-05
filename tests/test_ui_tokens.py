"""The dashboard (@theme in globals.css) and /live (frontend/arbex-theme.css) share one palette."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GLOBALS = ROOT / "arbex-web" / "src" / "app" / "globals.css"
THEME = ROOT / "frontend" / "arbex-theme.css"
TOKEN = re.compile(r"(--(?:color|text|radius)-[a-z0-9-]+)\s*:\s*([^;]+);")


def _block(text: str, opener: str) -> str:
    start = text.index(opener)
    return text[start:text.index("}", start)]


def test_dashboard_and_live_tokens_match():
    dashboard = dict(TOKEN.findall(_block(GLOBALS.read_text(encoding="utf-8"), "@theme {")))
    live = dict(TOKEN.findall(_block(THEME.read_text(encoding="utf-8"), ":root {")))
    assert dashboard, "no tokens found in the @theme block"
    assert {k: v.strip() for k, v in dashboard.items()} == {k: v.strip() for k, v in live.items()}
