"""Canonical, run-isolated orchestrator for the MAESTRO-X research package."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile
from typing import Any, Mapping

import matplotlib
import numpy as np
import pandas as pd
import plotly
import scipy
import yaml

from .config import (
    MONTE_CARLO_SIMULATIONS,
    RANDOM_SEED,
    RAW_TRADES_DIR,
    ROOT,
    RUNS_DIR,
)
from .monte_carlo_engine import run_institutional_monte_carlo
from .performance_engine import run_performance_analysis
from .preprocessing import hash_file, run_preprocessing
from .reporting import write_canonical_run_report, write_json
from .schema import (
    ARTIFACT_SCHEMA,
    ArtifactSpec,
    SchemaCompatibilityError,
    schema_catalog,
    validate_artifact_tree,
    validate_manifest_payload,
)
from .sensitivity_engine import run_sensitivity_pipeline


LOGGER = logging.getLogger(__name__)
PIPELINE_VERSION = "1.0.0"
CANONICAL_COMMAND = "python -m src.run_research_pipeline"
RUN_ID_PATTERN = re.compile(r"\d{8}_\d{6}")
PINE_PATH = ROOT / "strategy" / "MAESTRO-X_v1.0.pine"


class CanonicalPipelineError(RuntimeError):
    """Controlled failure before a canonical run can be published."""


def _new_run_id() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def _validate_run_id(run_id: str) -> None:
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise CanonicalPipelineError(
            f"Run ID harus mengikuti YYYYMMDD_HHMMSS; diterima {run_id!r}."
        )
    try:
        datetime.strptime(run_id, "%Y%m%d_%H%M%S")
    except ValueError as exc:
        raise CanonicalPipelineError(f"Run ID bukan timestamp valid: {run_id!r}.") from exc


def _relative_source_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _source_snapshot(raw_dir: Path, pine_path: Path) -> dict[str, Any]:
    raw_files = sorted(path for path in raw_dir.glob("*.csv") if path.is_file())
    if not raw_files:
        raise CanonicalPipelineError(f"Tidak ada raw CSV di {raw_dir}.")
    if not pine_path.is_file():
        raise CanonicalPipelineError(f"Pine immutable tidak ditemukan: {pine_path}.")
    return {
        "strategy": {
            "path": _relative_source_path(pine_path),
            "size_bytes": pine_path.stat().st_size,
            "sha256": hash_file(pine_path),
        },
        "raw_sources": [
            {
                "path": _relative_source_path(path),
                "size_bytes": path.stat().st_size,
                "sha256": hash_file(path),
            }
            for path in raw_files
        ],
    }


def _assert_source_unchanged(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    if before != after:
        raise CanonicalPipelineError(
            "Hash Pine atau raw CSV berubah selama pipeline; run dibatalkan."
        )


def _portable_summary(value: Any, staging_root: Path) -> Any:
    """Remove transient staging prefixes before persisting phase summaries."""

    if isinstance(value, Mapping):
        return {
            str(_portable_summary(key, staging_root)): _portable_summary(item, staging_root)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_portable_summary(item, staging_root) for item in value]
    if isinstance(value, Path):
        value = value.as_posix()
    if isinstance(value, str):
        normalized = value.replace("\\", "/")
        prefixes = [staging_root.resolve().as_posix().rstrip("/") + "/"]
        try:
            prefixes.append(
                staging_root.resolve().relative_to(ROOT.resolve()).as_posix().rstrip("/")
                + "/"
            )
        except ValueError:
            pass
        for prefix in prefixes:
            if normalized.startswith(prefix):
                return normalized[len(prefix) :]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _artifact_entry(root: Path, spec: ArtifactSpec) -> dict[str, Any]:
    path = root / Path(spec.relative_path)
    entry: dict[str, Any] = {
        "path": spec.relative_path,
        "media_type": spec.media_type,
        "size_bytes": path.stat().st_size,
        "sha256": hash_file(path),
    }
    if spec.tabular_schema is not None:
        entry["tabular_schema"] = spec.tabular_schema
    return entry


def _runtime_versions() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "matplotlib": matplotlib.__version__,
        "plotly": plotly.__version__,
        "pyyaml": yaml.__version__,
    }


def _manifest_payload(
    *,
    run_id: str,
    staging_root: Path,
    protected_sources: Mapping[str, Any],
    phase_summaries: Mapping[str, Mapping[str, Any]],
    simulations: int,
    base_seed: int,
) -> dict[str, Any]:
    artifacts = [
        _artifact_entry(staging_root, spec) for spec in ARTIFACT_SCHEMA.artifacts
    ]
    strategy = dict(protected_sources["strategy"])
    strategy["modified_by_pipeline"] = False
    raw_sources = []
    for source in protected_sources["raw_sources"]:
        item = dict(source)
        item["modified_by_pipeline"] = False
        raw_sources.append(item)
    return {
        "schema_version": "1.0.0",
        "artifact_schema": {
            "name": ARTIFACT_SCHEMA.name,
            "version": ARTIFACT_SCHEMA.version,
        },
        "pipeline": {
            "name": "MAESTRO-X canonical research pipeline",
            "version": PIPELINE_VERSION,
            "command": CANONICAL_COMMAND,
            "runtime_versions": _runtime_versions(),
        },
        "run_id": run_id,
        "status": "completed_with_empirical_limitations",
        "schemas": schema_catalog(),
        "parameters": {
            "monte_carlo_simulations_per_method_timeframe": simulations,
            "base_random_seed": base_seed,
            "strategy_parameters_optimized": False,
        },
        "strategy": strategy,
        "raw_sources": raw_sources,
        "phase_summaries": _portable_summary(phase_summaries, staging_root),
        "artifacts": artifacts,
        "limitations": [
            "Processed trades are observed target-segment rows, not complete TradingView backtests.",
            "Initial-capital provenance conflicts with the immutable Pine declaration.",
            "Timeframe samples are not controlled for identical periods or completeness.",
            "HTF Bias Timeframe and Minimum Entry Score lack controlled outcome grids.",
            "Monte Carlo results are conditional resampling diagnostics, not future-profit forecasts.",
            "Publication claims remain blocked pending complete provenance and controlled experiments.",
        ],
    }


def _publish_staging(staging_root: Path, final_root: Path) -> None:
    if final_root.exists():
        raise CanonicalPipelineError(f"Run directory sudah ada: {final_root}.")
    try:
        os.replace(staging_root, final_root)
    except OSError as exc:
        raise CanonicalPipelineError(
            f"Gagal mempublikasikan run secara atomik ke {final_root}: {exc}"
        ) from exc


def run_research_pipeline(
    *,
    runs_dir: Path = RUNS_DIR,
    run_id: str | None = None,
    simulations: int = MONTE_CARLO_SIMULATIONS,
    base_seed: int = RANDOM_SEED,
    raw_dir: Path = RAW_TRADES_DIR,
    pine_path: Path = PINE_PATH,
) -> dict[str, Any]:
    """Execute all production engines and atomically publish one isolated run."""

    selected_run_id = run_id or _new_run_id()
    _validate_run_id(selected_run_id)
    if simulations < 10_000:
        raise CanonicalPipelineError("Canonical Monte Carlo membutuhkan minimal 10.000 simulasi.")

    runs_dir = runs_dir.resolve()
    runs_dir.mkdir(parents=True, exist_ok=True)
    final_root = runs_dir / selected_run_id
    if final_root.exists():
        raise CanonicalPipelineError(
            f"Run {selected_run_id} sudah ada; artefak tidak akan ditimpa."
        )
    staging_root = Path(
        tempfile.mkdtemp(prefix=f".{selected_run_id}.", suffix=".staging", dir=runs_dir)
    ).resolve()
    if staging_root.parent != runs_dir:
        raise CanonicalPipelineError("Staging directory keluar dari results/runs.")

    published = False
    try:
        figures_dir = staging_root / "figures"
        reports_dir = staging_root / "reports"
        tables_dir = staging_root / "tables"
        processed_dir = tables_dir / "processed"
        for directory in (figures_dir, reports_dir, tables_dir, processed_dir):
            directory.mkdir(parents=True, exist_ok=False)

        protected_before = _source_snapshot(raw_dir, pine_path)
        LOGGER.info("Canonical run %s dimulai di staging terisolasi.", selected_run_id)
        preprocessing_summary = run_preprocessing(
            raw_dir=raw_dir,
            processed_dir=processed_dir,
            report_path=reports_dir / "preprocessing_report.json",
            summary_path=tables_dir / "processed_dataset_summary.csv",
        )
        LOGGER.info("Stage preprocessing selesai.")

        performance_summary = run_performance_analysis(
            processed_dir=processed_dir,
            metrics_path=tables_dir / "performance_metrics.csv",
            figure_path=figures_dir / "equity_curve.png",
        )
        LOGGER.info("Stage performance_engine selesai.")

        monte_carlo_summary = run_institutional_monte_carlo(
            processed_dir=processed_dir,
            report_path=reports_dir / "monte_carlo_report.html",
            statistics_path=tables_dir / "monte_carlo_statistics.csv",
            figure_path=figures_dir / "monte_carlo_distribution.png",
            simulations=simulations,
            base_seed=base_seed,
        )
        monte_carlo_summary = {**monte_carlo_summary, "base_seed": base_seed}
        LOGGER.info("Stage monte_carlo_engine selesai.")

        sensitivity_summary = run_sensitivity_pipeline(
            performance_path=tables_dir / "performance_metrics.csv",
            monte_carlo_path=tables_dir / "monte_carlo_statistics.csv",
            trades_path=processed_dir / "normalized_trades.csv",
            pine_path=pine_path,
            table_path=tables_dir / "parameter_sensitivity.csv",
            figure_path=figures_dir / "parameter_surface.png",
            report_path=reports_dir / "sensitivity_report.html",
        )
        LOGGER.info("Stage sensitivity_engine selesai.")

        phase_summaries: dict[str, Mapping[str, Any]] = {
            "preprocessing": preprocessing_summary,
            "performance_engine": performance_summary,
            "monte_carlo_engine": monte_carlo_summary,
            "sensitivity_engine": sensitivity_summary,
        }
        write_canonical_run_report(
            path=reports_dir / "research_report.md",
            run_id=selected_run_id,
            performance=pd.read_csv(tables_dir / "performance_metrics.csv"),
            monte_carlo=pd.read_csv(tables_dir / "monte_carlo_statistics.csv"),
            sensitivity=pd.read_csv(tables_dir / "parameter_sensitivity.csv"),
            phase_summaries=phase_summaries,
        )
        phase_summaries["reporting"] = {
            "status": "completed",
            "report": "reports/research_report.md",
            "future_profitability_claimed": False,
        }
        LOGGER.info("Stage reporting selesai.")

        protected_after = _source_snapshot(raw_dir, pine_path)
        _assert_source_unchanged(protected_before, protected_after)
        validate_artifact_tree(staging_root, ARTIFACT_SCHEMA)

        manifest = _manifest_payload(
            run_id=selected_run_id,
            staging_root=staging_root,
            protected_sources=protected_before,
            phase_summaries=phase_summaries,
            simulations=simulations,
            base_seed=base_seed,
        )
        validate_manifest_payload(manifest)
        write_json(manifest, staging_root / "manifest.json")
        persisted_manifest = json.loads(
            (staging_root / "manifest.json").read_text(encoding="utf-8")
        )
        validate_manifest_payload(persisted_manifest)

        _publish_staging(staging_root, final_root)
        published = True
    except (SchemaCompatibilityError, OSError, ValueError) as exc:
        raise CanonicalPipelineError(f"Canonical run gagal: {exc}") from exc
    finally:
        if not published and staging_root.exists():
            if staging_root.parent != runs_dir or not staging_root.name.endswith(".staging"):
                raise CanonicalPipelineError("Cleanup staging dibatalkan karena target tidak aman.")
            shutil.rmtree(staging_root)

    LOGGER.info("Canonical run %s dipublikasikan: %s", selected_run_id, final_root)
    return {
        "status": "completed_with_empirical_limitations",
        "run_id": selected_run_id,
        "run_directory": final_root.as_posix(),
        "manifest": (final_root / "manifest.json").as_posix(),
        "accepted_rows": int(preprocessing_summary["accepted_rows"]),
        "closed_trades_analyzed": int(performance_summary["closed_trades_analyzed"]),
        "total_simulated_paths": int(monte_carlo_summary["total_simulated_paths"]),
        "artifacts": len(ARTIFACT_SCHEMA.artifacts),
        "source_integrity_verified": True,
        "optimization_performed": False,
        "publication_claim_gate": "blocked",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the canonical isolated MAESTRO-X research pipeline."
    )
    parser.add_argument(
        "--simulations",
        type=int,
        default=MONTE_CARLO_SIMULATIONS,
        help="Paths per method/timeframe; minimum 10000.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional YYYYMMDD_HHMMSS identifier; existing runs are never overwritten.",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=RUNS_DIR,
        help="Run root; defaults to results/runs.",
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
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        summary = run_research_pipeline(
            runs_dir=args.runs_dir,
            run_id=args.run_id,
            simulations=args.simulations,
        )
    except Exception:
        LOGGER.exception("Canonical research pipeline gagal.")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
