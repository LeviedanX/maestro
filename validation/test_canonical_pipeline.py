from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from src.config import RAW_TRADES_DIR, RESULTS_DIR, ROOT, TIMEFRAME_SPECS
from src.performance_engine import OUTPUT_COLUMNS
from src.preprocessing import NORMALIZED_COLUMNS, process_dataset
from src.run_pipeline import LEGACY_OUTPUT_POLICY
from src.run_research_pipeline import CanonicalPipelineError, run_research_pipeline
from src.schema import (
    ARTIFACT_SCHEMA,
    DATASET_SCHEMA,
    PERFORMANCE_SCHEMA,
    SchemaCompatibilityError,
    validate_artifact_tree,
    validate_dataframe_schema,
    validate_manifest_payload,
)


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _protected_hashes() -> dict[str, str]:
    paths = [
        ROOT / "strategy" / "MAESTRO-X_v1.0.pine",
        *sorted(RAW_TRADES_DIR.glob("*.csv")),
    ]
    return {_relative(path): _hash(path) for path in paths}


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _shared_artifact_hashes() -> dict[str, str]:
    paths: list[Path] = []
    for name in ("figures", "reports", "tables"):
        directory = RESULTS_DIR / name
        paths.extend(path for path in directory.rglob("*") if path.is_file())
    return {_relative(path): _hash(path) for path in sorted(paths)}


class SchemaContractTests(unittest.TestCase):
    def test_engine_column_constants_have_one_versioned_source(self) -> None:
        self.assertEqual(NORMALIZED_COLUMNS, DATASET_SCHEMA.columns)
        self.assertEqual(OUTPUT_COLUMNS, PERFORMANCE_SCHEMA.columns)
        self.assertEqual(DATASET_SCHEMA.version, "1.0.0")
        self.assertEqual(PERFORMANCE_SCHEMA.version, "1.0.0")
        self.assertEqual(ARTIFACT_SCHEMA.version, "1.0.0")

    def test_dataset_schema_fails_closed_on_column_drift(self) -> None:
        spec = TIMEFRAME_SPECS[0]
        accepted = process_dataset(RAW_TRADES_DIR / spec.filename, spec).accepted
        validate_dataframe_schema(accepted, DATASET_SCHEMA)
        with self.assertRaises(SchemaCompatibilityError):
            validate_dataframe_schema(accepted.drop(columns=["record_id"]), DATASET_SCHEMA)


class CanonicalPipelineIntegrationTests(unittest.TestCase):
    def test_run_is_isolated_manifested_and_source_safe(self) -> None:
        protected_before = _protected_hashes()
        shared_before = _shared_artifact_hashes()
        with TemporaryDirectory() as temporary_directory:
            runs_dir = Path(temporary_directory) / "runs"
            result = run_research_pipeline(
                runs_dir=runs_dir,
                run_id="20260824_120000",
            )
            run_root = Path(result["run_directory"])
            self.assertEqual(
                {path.name for path in run_root.iterdir()},
                {"figures", "reports", "tables", "manifest.json"},
            )
            self.assertEqual(result["accepted_rows"], 323)
            self.assertEqual(result["closed_trades_analyzed"], 322)
            self.assertEqual(result["total_simulated_paths"], 160_000)
            self.assertEqual(result["artifacts"], len(ARTIFACT_SCHEMA.artifacts))
            self.assertTrue(result["source_integrity_verified"])
            self.assertFalse(result["optimization_performed"])

            validate_artifact_tree(run_root, ARTIFACT_SCHEMA)
            manifest = json.loads(
                (run_root / "manifest.json").read_text(encoding="utf-8")
            )
            validate_manifest_payload(manifest)
            incompatible_manifest = json.loads(json.dumps(manifest))
            incompatible_manifest["artifacts"][0]["media_type"] = "application/octet-stream"
            with self.assertRaises(SchemaCompatibilityError):
                validate_manifest_payload(incompatible_manifest)
            self.assertEqual(
                set(manifest["phase_summaries"]),
                {
                    "preprocessing",
                    "performance_engine",
                    "monte_carlo_engine",
                    "sensitivity_engine",
                    "reporting",
                },
            )
            self.assertFalse(manifest["parameters"]["strategy_parameters_optimized"])
            self.assertTrue(all(not item["modified_by_pipeline"] for item in manifest["raw_sources"]))
            self.assertFalse(manifest["strategy"]["modified_by_pipeline"])
            self.assertFalse(any(path.name.endswith(".staging") for path in runs_dir.iterdir()))
            text_artifacts = [
                run_root / item.relative_path
                for item in ARTIFACT_SCHEMA.artifacts
                if item.media_type.startswith("text/")
                or item.media_type == "application/json"
            ]
            self.assertTrue(
                all(".staging" not in path.read_text(encoding="utf-8") for path in text_artifacts)
            )

            normalized = pd.read_csv(
                run_root / "tables" / "processed" / "normalized_trades.csv"
            )
            performance = pd.read_csv(run_root / "tables" / "performance_metrics.csv")
            self.assertEqual(tuple(normalized.columns), DATASET_SCHEMA.columns)
            self.assertEqual(tuple(performance.columns), PERFORMANCE_SCHEMA.columns)

            with self.assertRaises(CanonicalPipelineError):
                run_research_pipeline(
                    runs_dir=runs_dir,
                    run_id="20260824_120000",
                )

        self.assertEqual(protected_before, _protected_hashes())
        self.assertEqual(shared_before, _shared_artifact_hashes())

    def test_legacy_orchestrator_policy_is_explicit(self) -> None:
        self.assertEqual(
            LEGACY_OUTPUT_POLICY,
            "delegates_to_isolated_canonical_pipeline",
        )

    def test_failed_run_does_not_leave_partial_artifacts(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runs_dir = root / "runs"
            with self.assertRaises(CanonicalPipelineError):
                run_research_pipeline(
                    runs_dir=runs_dir,
                    run_id="20260824_120001",
                    raw_dir=root / "missing-raw-directory",
                )
            self.assertTrue(runs_dir.is_dir())
            self.assertEqual(list(runs_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
