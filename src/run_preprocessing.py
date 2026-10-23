"""Command-line entry point for PHASE 2 preprocessing only."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .preprocessing import PreprocessingError, run_preprocessing


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize MAESTRO-X TradingView CSV exports without running analysis."
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Console logging threshold (default: INFO).",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        summary = run_preprocessing()
    except PreprocessingError as exc:
        logging.getLogger(__name__).error("Preprocessing gagal: %s", exc)
        return 1
    except Exception:
        logging.getLogger(__name__).exception("Preprocessing gagal karena error tak terduga.")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
