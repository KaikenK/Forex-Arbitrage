"""Study pairs, their price tickers, and where each pair's assets live in the repo.

One table so the live labeller, the seed exporter and the Arbex bridge agree on what
"the study pairs" are. Tickers are Yahoo Finance symbols; note the USD-base majors are
quoted as the foreign currency alone (JPY=X is USD/JPY, CHF=X is USD/CHF, INR=X is USD/INR).
"""
from __future__ import annotations

import os
from pathlib import Path

STUDY_PAIRS: tuple[str, ...] = ("USD/INR", "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF")

PAIR_TICKERS: dict[str, str] = {
    "USD/INR": "INR=X",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "USD/CHF": "CHF=X",
}

# services/semantic/src/market/pair_assets.py -> repo root is four levels up.
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[4]


def normalize_pair(value: str) -> str:
    normalized = value.strip().upper().replace("_", "/")
    if "/" in normalized:
        return normalized
    if len(normalized) == 6 and normalized.isalpha():
        return f"{normalized[:3]}/{normalized[3:]}"
    return normalized


TEXT_TOKEN_SCOPES = frozenset({"shared", "pair_group"})


def text_token_group(pair: str) -> str:
    """Which group's text weights a pair uses under the "pair_group" token scope.

    USD/INR's labelled rows are real wire headlines from essentially one regime (April
    2026), the majors' rows are calendar releases. With shared text weights, words seen only
    in USD/INR rows ("iran", "rupee", "reuters") steered the majors too.
    """
    return "inr" if normalize_pair(pair) == "USD/INR" else "majors"


def scoped_text_token(token: str, pair: str, scope: str) -> str:
    """The hashed key for one text token. Training and the live scorer both call this."""
    if scope == "pair_group":
        return f"{text_token_group(pair)}|{token}"
    return token


def pair_slug(pair: str) -> str:
    """'USD/INR' -> 'usdinr' (the directory name under sentiment_assets/)."""
    return normalize_pair(pair).replace("/", "").lower()


def ticker_for(pair: str) -> str:
    normalized = normalize_pair(pair)
    try:
        return PAIR_TICKERS[normalized]
    except KeyError:
        raise KeyError(
            f"No price ticker configured for {normalized}; known pairs: {', '.join(PAIR_TICKERS)}. "
            "Pass --ticker explicitly."
        ) from None


def repo_root() -> Path:
    """The Arbex repo root; ARBEX_ROOT overrides it (e.g. when the service runs elsewhere)."""
    override = os.environ.get("ARBEX_ROOT")
    return Path(override) if override and override.strip() else _DEFAULT_REPO_ROOT


def sentiment_assets_dir(pair: str, root: Path | None = None) -> Path:
    return (root or repo_root()) / "sentiment_assets" / pair_slug(pair)


def runtime_archive_dir(pair: str, root: Path | None = None) -> Path:
    """Where the Arbex bridge appends that pair's live feed / analysis archive."""
    return (root or repo_root()) / "sentiment_assets" / "runtime" / pair_slug(pair)
