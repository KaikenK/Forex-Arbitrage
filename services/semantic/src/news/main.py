from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from pathlib import Path

import structlog

from .factory import build_pipeline


def parse_args() -> argparse.Namespace:
    """Parses command-line arguments for the news pipeline."""
    parser = argparse.ArgumentParser(description="Run the news pipeline.")
    parser.add_argument("--config", required=True, type=Path, help="Path to the TOML config file.")
    return parser.parse_args()


def configure_logging() -> None:
    """Configures structlog with JSON output."""
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
    )


async def run_main(config_path: Path) -> None:
    """Builds and runs the news pipeline."""
    pipeline = build_pipeline(config_path)
    run_task = asyncio.create_task(pipeline.run())

    try:
        await run_task
    except asyncio.CancelledError:
        await pipeline.stop()
        raise
    finally:
        if not run_task.done():
            run_task.cancel()
            with suppress(asyncio.CancelledError):
                await run_task


def main() -> None:
    """Runs the news pipeline entry point."""
    args = parse_args()
    configure_logging()

    try:
        asyncio.run(run_main(args.config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
