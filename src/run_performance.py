"""Command-line entry point for PHASE 3 performance analysis only."""

from __future__ import annotations

import argparse
import json
import logging

from .performance_engine import PerformanceEngineError, run_performance_analysis


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate MAESTRO-X observed-subset performance metrics."
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
        summary = run_performance_analysis()
    except PerformanceEngineError as exc:
        logging.getLogger(__name__).error("Performance analysis gagal: %s", exc)
        return 1
    except Exception:
        logging.getLogger(__name__).exception(
            "Performance analysis gagal karena error tak terduga."
        )
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
