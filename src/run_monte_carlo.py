"""Command-line entry point for PHASE 4 Monte Carlo robustness testing."""

from __future__ import annotations

import argparse
import json
import logging

from .monte_carlo_engine import (
    InstitutionalMonteCarloError,
    MINIMUM_SIMULATIONS,
    run_institutional_monte_carlo,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run MAESTRO-X permutation and bootstrap Monte Carlo analysis."
    )
    parser.add_argument(
        "--simulations",
        type=int,
        default=MINIMUM_SIMULATIONS,
        help=f"Paths per method/timeframe; minimum {MINIMUM_SIMULATIONS}.",
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
        summary = run_institutional_monte_carlo(simulations=args.simulations)
    except InstitutionalMonteCarloError as exc:
        logging.getLogger(__name__).error("Monte Carlo gagal: %s", exc)
        return 1
    except Exception:
        logging.getLogger(__name__).exception(
            "Monte Carlo gagal karena error tak terduga."
        )
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
