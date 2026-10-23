"""Command-line entry point for MAESTRO-X PHASE 5 sensitivity evidence."""

from __future__ import annotations

import argparse
from pathlib import Path
import logging

from .config import FIGURES_DIR, PROCESSED_TRADES_DIR, REPORTS_DIR, ROOT, TABLES_DIR
from .sensitivity_engine import SensitivityEngineError, run_sensitivity_pipeline


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze parameter sensitivity evidence without optimizing or changing MAESTRO-X."
        )
    )
    parser.add_argument(
        "--performance",
        type=Path,
        default=TABLES_DIR / "performance_metrics.csv",
        help="PHASE 3 performance metrics CSV.",
    )
    parser.add_argument(
        "--monte-carlo",
        type=Path,
        default=TABLES_DIR / "monte_carlo_statistics.csv",
        help="PHASE 4 Monte Carlo statistics CSV.",
    )
    parser.add_argument(
        "--trades",
        type=Path,
        default=PROCESSED_TRADES_DIR / "normalized_trades.csv",
        help="PHASE 2 canonical normalized trade CSV.",
    )
    parser.add_argument(
        "--pine",
        type=Path,
        default=ROOT / "strategy" / "MAESTRO-X_v1.0.pine",
        help="Immutable Pine source used only to audit parameter definitions.",
    )
    parser.add_argument(
        "--table-output",
        type=Path,
        default=TABLES_DIR / "parameter_sensitivity.csv",
    )
    parser.add_argument(
        "--figure-output",
        type=Path,
        default=FIGURES_DIR / "parameter_surface.png",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        default=REPORTS_DIR / "sensitivity_report.html",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        summary = run_sensitivity_pipeline(
            performance_path=args.performance,
            monte_carlo_path=args.monte_carlo,
            trades_path=args.trades,
            pine_path=args.pine,
            table_path=args.table_output,
            figure_path=args.figure_output,
            report_path=args.report_output,
        )
    except SensitivityEngineError as exc:
        LOGGER.error("PHASE 5 gagal: %s", exc)
        return 1

    diagnostics = summary["diagnostics"]
    LOGGER.info(
        "Output: %s | %s | %s",
        args.table_output,
        args.figure_output,
        args.report_output,
    )
    LOGGER.info(
        "Evidence: timeframe=descriptive; HTF/score=not_estimable; robustness_region=%s",
        diagnostics["robustness_region"],
    )
    LOGGER.info("Optimization performed: %s", diagnostics["optimization_performed"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

