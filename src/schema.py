"""Versioned data and artifact contracts for the canonical research pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re
from typing import Any, Literal, Mapping, Sequence

import pandas as pd


SCHEMA_REGISTRY_VERSION = "1.0.0"
SchemaKind = Literal["string", "integer", "numeric", "boolean", "datetime"]


class SchemaCompatibilityError(RuntimeError):
    """Raised when an in-memory dataset or artifact set violates its contract."""


@dataclass(frozen=True)
class ColumnSpec:
    """One typed column in a versioned tabular contract."""

    name: str
    kind: SchemaKind
    nullable: bool = False


@dataclass(frozen=True)
class TabularSchema:
    """Exact ordered dataframe schema used at an engine write boundary."""

    name: str
    version: str
    fields: tuple[ColumnSpec, ...]
    minimum_rows: int = 1

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)


@dataclass(frozen=True)
class ArtifactSpec:
    """One required run-scoped artifact and its declared media type."""

    relative_path: str
    media_type: str
    tabular_schema: str | None = None


@dataclass(frozen=True)
class ArtifactSchema:
    """Exact file inventory for a successfully published canonical run."""

    name: str
    version: str
    artifacts: tuple[ArtifactSpec, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(item.relative_path for item in self.artifacts)


def _field(name: str, kind: SchemaKind, nullable: bool = False) -> ColumnSpec:
    return ColumnSpec(name=name, kind=kind, nullable=nullable)


DATASET_SCHEMA = TabularSchema(
    name="maestro_x_normalized_trade_dataset",
    version="1.0.0",
    fields=(
        _field("record_id", "string"),
        _field("timeframe", "string"),
        _field("timeframe_minutes", "integer"),
        _field("trade_id", "integer"),
        _field("side", "string"),
        _field("entry_timestamp", "datetime"),
        _field("exit_timestamp", "datetime", nullable=True),
        _field("entry_timestamp_precision", "string"),
        _field("exit_timestamp_precision", "string", nullable=True),
        _field("entry_signal", "string"),
        _field("exit_signal", "string", nullable=True),
        _field("entry_price", "numeric"),
        _field("exit_price", "numeric", nullable=True),
        _field("contracts", "numeric"),
        _field("position_value_usd", "numeric"),
        _field("net_pnl_usd", "numeric", nullable=True),
        _field("net_pnl_pct", "numeric", nullable=True),
        _field("commission_usd", "numeric", nullable=True),
        _field("max_runup_usd", "numeric", nullable=True),
        _field("max_runup_pct", "numeric", nullable=True),
        _field("max_drawdown_usd", "numeric", nullable=True),
        _field("max_drawdown_pct", "numeric", nullable=True),
        _field("raw_cumulative_pnl_usd", "numeric", nullable=True),
        _field("raw_cumulative_pnl_pct", "numeric", nullable=True),
        _field("bars_in_trade", "numeric", nullable=True),
        _field("elapsed_minutes", "numeric", nullable=True),
        _field("is_closed", "boolean"),
        _field("trade_status", "string"),
        _field("timeframe_consistent", "boolean"),
        _field("source_file", "string"),
        _field("source_sha256", "string"),
        _field("source_row", "integer"),
        _field("source_segment", "integer"),
        _field("detected_timeframe", "string"),
        _field("data_scope", "string"),
    ),
)


PERFORMANCE_SCHEMA = TabularSchema(
    name="maestro_x_performance_metrics",
    version="1.0.0",
    fields=(
        _field("timeframe", "string"),
        _field("data_scope", "string"),
        _field("sample_start", "string"),
        _field("sample_end", "string"),
        _field("initial_capital_usd", "numeric"),
        _field("initial_capital_source", "string"),
        _field("initial_capital_provenance_status", "string"),
        _field("implied_initial_capital_median_usd", "numeric"),
        _field("implied_initial_capital_matches_assumption", "boolean"),
        _field("processed_rows", "integer"),
        _field("open_trades_excluded", "integer"),
        _field("quarantined_rows_upstream", "integer"),
        _field("total_trades", "integer"),
        _field("winning_trades", "integer"),
        _field("losing_trades", "integer"),
        _field("breakeven_trades", "integer"),
        _field("win_rate_pct", "numeric"),
        _field("average_win_usd", "numeric", nullable=True),
        _field("average_loss_usd", "numeric", nullable=True),
        _field("average_loss_abs_usd", "numeric", nullable=True),
        _field("expectancy_usd", "numeric"),
        _field("gross_profit_usd", "numeric"),
        _field("gross_loss_usd", "numeric"),
        _field("net_profit_usd", "numeric"),
        _field("net_return_pct", "numeric"),
        _field("profit_factor", "numeric"),
        _field("max_drawdown_usd", "numeric"),
        _field("max_drawdown_pct", "numeric"),
        _field("recovery_factor", "numeric"),
        _field("ending_equity_usd", "numeric"),
        _field("equity_reconciliation_passed", "boolean"),
        _field("raw_cumulative_endpoint_usd", "numeric"),
        _field("observed_net_minus_raw_endpoint_usd", "numeric"),
        _field("raw_cumulative_endpoint_matches_observed_net", "boolean"),
        _field("tv_report_total_trades", "integer"),
        _field("tv_report_net_profit_usd", "numeric"),
        _field("tv_report_profit_factor", "numeric"),
        _field("tv_report_win_rate_pct", "numeric"),
        _field("tv_report_max_drawdown_pct", "numeric"),
        _field("tv_trade_count_difference", "integer"),
        _field("tv_snapshot_comparable", "boolean"),
        _field("tv_validation_status", "string"),
        _field("tv_reference_source", "string"),
        _field("calculation_validation_status", "string"),
        _field("publication_eligible", "boolean"),
    ),
)


_TIMEFRAME_OUTPUTS = (
    "trades_1m.csv",
    "trades_5m.csv",
    "trades_15m.csv",
    "trades_30m.csv",
    "trades_1h.csv",
    "trades_2h.csv",
    "trades_4h.csv",
    "trades_1d.csv",
)

ARTIFACT_SCHEMA = ArtifactSchema(
    name="maestro_x_canonical_run_artifacts",
    version="1.0.0",
    artifacts=(
        *tuple(
            ArtifactSpec(
                f"tables/processed/{filename}",
                "text/csv",
                DATASET_SCHEMA.name,
            )
            for filename in _TIMEFRAME_OUTPUTS
        ),
        ArtifactSpec(
            "tables/processed/normalized_trades.csv",
            "text/csv",
            DATASET_SCHEMA.name,
        ),
        ArtifactSpec("tables/processed/rejected_trades.csv", "text/csv"),
        ArtifactSpec("tables/processed_dataset_summary.csv", "text/csv"),
        ArtifactSpec(
            "tables/performance_metrics.csv",
            "text/csv",
            PERFORMANCE_SCHEMA.name,
        ),
        ArtifactSpec("tables/monte_carlo_statistics.csv", "text/csv"),
        ArtifactSpec("tables/parameter_sensitivity.csv", "text/csv"),
        ArtifactSpec("figures/equity_curve.png", "image/png"),
        ArtifactSpec("figures/monte_carlo_distribution.png", "image/png"),
        ArtifactSpec("figures/parameter_surface.png", "image/png"),
        ArtifactSpec("reports/preprocessing_report.json", "application/json"),
        ArtifactSpec("reports/monte_carlo_report.html", "text/html"),
        ArtifactSpec("reports/sensitivity_report.html", "text/html"),
        ArtifactSpec("reports/research_report.md", "text/markdown"),
    ),
)


def _validate_contract_definition(schema: TabularSchema) -> None:
    if re.fullmatch(r"\d+\.\d+\.\d+", schema.version) is None:
        raise SchemaCompatibilityError(
            f"Versi schema {schema.name!r} bukan semantic version: {schema.version!r}."
        )
    if len(schema.columns) != len(set(schema.columns)):
        raise SchemaCompatibilityError(f"Schema {schema.name!r} memiliki nama kolom duplikat.")


def _kind_matches(series: pd.Series, kind: SchemaKind) -> bool:
    if kind == "string":
        return bool(
            pd.api.types.is_object_dtype(series.dtype)
            or pd.api.types.is_string_dtype(series.dtype)
        )
    if kind == "integer":
        return bool(pd.api.types.is_integer_dtype(series.dtype))
    if kind == "numeric":
        return bool(
            pd.api.types.is_numeric_dtype(series.dtype)
            and not pd.api.types.is_bool_dtype(series.dtype)
        )
    if kind == "boolean":
        return bool(pd.api.types.is_bool_dtype(series.dtype))
    return bool(pd.api.types.is_datetime64_any_dtype(series.dtype))


def validate_dataframe_schema(frame: pd.DataFrame, schema: TabularSchema) -> None:
    """Fail closed unless ``frame`` exactly matches a versioned contract."""

    _validate_contract_definition(schema)
    actual = tuple(str(column) for column in frame.columns)
    if actual != schema.columns:
        missing = [column for column in schema.columns if column not in actual]
        unexpected = [column for column in actual if column not in schema.columns]
        raise SchemaCompatibilityError(
            f"Schema {schema.name}@{schema.version} tidak kompatibel; "
            f"missing={missing}, unexpected={unexpected}, ordered_match={actual == schema.columns}."
        )
    if len(frame) < schema.minimum_rows:
        raise SchemaCompatibilityError(
            f"Schema {schema.name}@{schema.version} membutuhkan minimal "
            f"{schema.minimum_rows} baris; ditemukan {len(frame)}."
        )
    for field in schema.fields:
        series = frame[field.name]
        if not field.nullable and series.isna().any():
            raise SchemaCompatibilityError(
                f"Kolom non-nullable {schema.name}.{field.name} mengandung missing value."
            )
        if not _kind_matches(series, field.kind):
            raise SchemaCompatibilityError(
                f"Tipe {schema.name}.{field.name} tidak kompatibel: "
                f"expected={field.kind}, actual={series.dtype}."
            )
        if field.kind in {"integer", "numeric"}:
            non_missing = pd.to_numeric(series, errors="coerce").dropna()
            if any(not math.isfinite(float(value)) for value in non_missing):
                raise SchemaCompatibilityError(
                    f"Kolom {schema.name}.{field.name} mengandung nilai non-finite."
                )


def validate_artifact_tree(run_root: Path, schema: ArtifactSchema) -> None:
    """Require an exact, non-empty artifact inventory before manifest publication."""

    if re.fullmatch(r"\d+\.\d+\.\d+", schema.version) is None:
        raise SchemaCompatibilityError(
            f"Versi artifact schema bukan semantic version: {schema.version!r}."
        )
    root = run_root.resolve()
    expected = set(schema.paths)
    discovered = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    missing = sorted(expected - discovered)
    unexpected = sorted(discovered - expected)
    if missing or unexpected:
        raise SchemaCompatibilityError(
            f"Artifact schema {schema.name}@{schema.version} tidak kompatibel; "
            f"missing={missing}, unexpected={unexpected}."
        )
    empty = sorted(
        relative_path
        for relative_path in expected
        if (root / Path(relative_path)).stat().st_size <= 0
    )
    if empty:
        raise SchemaCompatibilityError(f"Artefak kosong tidak diizinkan: {empty}.")
    non_portable: list[str] = []
    for artifact in schema.artifacts:
        if artifact.media_type.startswith("text/") or artifact.media_type == "application/json":
            path = root / Path(artifact.relative_path)
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeError as exc:
                raise SchemaCompatibilityError(
                    f"Artefak teks bukan UTF-8 valid: {artifact.relative_path}."
                ) from exc
            if ".staging" in content:
                non_portable.append(artifact.relative_path)
    if non_portable:
        raise SchemaCompatibilityError(
            f"Artefak menyimpan transient staging path: {sorted(non_portable)}."
        )


def validate_manifest_payload(
    payload: Mapping[str, Any],
    schema: ArtifactSchema = ARTIFACT_SCHEMA,
) -> None:
    """Validate manifest identity and its one-to-one artifact declarations."""

    required_keys = {
        "schema_version",
        "artifact_schema",
        "pipeline",
        "run_id",
        "status",
        "schemas",
        "parameters",
        "strategy",
        "raw_sources",
        "phase_summaries",
        "artifacts",
        "limitations",
    }
    missing_keys = sorted(required_keys - set(payload))
    if missing_keys:
        raise SchemaCompatibilityError(f"Manifest kehilangan key wajib: {missing_keys}.")
    descriptor = payload.get("artifact_schema")
    if descriptor != {"name": schema.name, "version": schema.version}:
        raise SchemaCompatibilityError("Manifest mendeklarasikan artifact schema yang salah.")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
        raise SchemaCompatibilityError("Manifest artifacts harus berupa sequence.")
    expected_by_path = {item.relative_path: item for item in schema.artifacts}
    paths: list[str] = []
    for item in artifacts:
        if not isinstance(item, Mapping):
            raise SchemaCompatibilityError("Setiap manifest artifact harus berupa mapping.")
        if not {"path", "media_type", "size_bytes", "sha256"}.issubset(item):
            raise SchemaCompatibilityError(f"Manifest artifact tidak lengkap: {item!r}.")
        path = str(item["path"])
        digest = str(item["sha256"])
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise SchemaCompatibilityError(f"SHA-256 artefak tidak valid: {path}.")
        if int(item["size_bytes"]) <= 0:
            raise SchemaCompatibilityError(f"Ukuran artefak tidak valid: {path}.")
        expected = expected_by_path.get(path)
        if expected is None:
            raise SchemaCompatibilityError(f"Artefak tidak terdaftar dalam schema: {path}.")
        if str(item["media_type"]) != expected.media_type:
            raise SchemaCompatibilityError(f"Media type artefak tidak kompatibel: {path}.")
        if item.get("tabular_schema") != expected.tabular_schema:
            raise SchemaCompatibilityError(f"Tabular schema artefak tidak kompatibel: {path}.")
        paths.append(path)
    if set(paths) != set(schema.paths) or len(paths) != len(set(paths)):
        raise SchemaCompatibilityError("Inventori artefak manifest tidak cocok dengan artifact schema.")


def schema_catalog() -> dict[str, dict[str, Any]]:
    """Return the version identifiers persisted in every canonical manifest."""

    return {
        "registry": {"version": SCHEMA_REGISTRY_VERSION},
        "dataset": {"name": DATASET_SCHEMA.name, "version": DATASET_SCHEMA.version},
        "performance": {
            "name": PERFORMANCE_SCHEMA.name,
            "version": PERFORMANCE_SCHEMA.version,
        },
        "artifact": {"name": ARTIFACT_SCHEMA.name, "version": ARTIFACT_SCHEMA.version},
    }
