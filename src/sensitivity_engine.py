"""Evidence-bounded parameter sensitivity analysis for MAESTRO-X PHASE 5.

The engine consumes the immutable outputs produced by the preprocessing,
performance, and Monte Carlo phases.  It never executes or changes the Pine
strategy, never searches a parameter space, and never selects a best setting.

Only chart timeframe has observed outcomes in the current exports.  Those
outcomes come from non-comparable backtest windows, so their comparison is
descriptive.  The Pine definitions for HTF Bias Timeframe and Minimum Entry
Score are audited, but their sensitivities remain non-estimable until a
controlled experiment grid is supplied.
"""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from hashlib import sha256
from html import escape
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping, Sequence
import logging
import math
import os
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, TwoSlopeNorm
import numpy as np
import pandas as pd

from .config import (
    FIGURES_DIR,
    PROCESSED_TRADES_DIR,
    REPORTS_DIR,
    ROOT,
    TABLES_DIR,
    TIMEFRAME_SPECS,
)


LOGGER = logging.getLogger(__name__)
ENGINE_VERSION = "1.0.0"
TIMEFRAME_ORDER = tuple(spec.label for spec in TIMEFRAME_SPECS)

PERFORMANCE_REQUIRED = frozenset(
    {
        "timeframe",
        "data_scope",
        "sample_start",
        "sample_end",
        "initial_capital_usd",
        "initial_capital_provenance_status",
        "processed_rows",
        "total_trades",
        "win_rate_pct",
        "expectancy_usd",
        "net_return_pct",
        "profit_factor",
        "max_drawdown_pct",
        "recovery_factor",
        "calculation_validation_status",
    }
)

MONTE_CARLO_REQUIRED = frozenset(
    {
        "timeframe",
        "resampling_method",
        "simulations",
        "trade_count",
        "observed_terminal_return_pct",
        "mean_terminal_return_pct",
        "terminal_return_interval_95_lower_pct",
        "terminal_return_interval_95_upper_pct",
        "probability_negative_return_pct",
        "median_max_drawdown_pct",
        "max_drawdown_interval_95_upper_pct",
        "confidence_interval_label",
    }
)

TRADE_REQUIRED = frozenset({"record_id", "timeframe", "is_closed"})
PARAMETER_OUTCOME_ALIASES = frozenset(
    {
        "htf_bias_timeframe",
        "higher_timeframe",
        "higherTimeframeInput",
        "minimum_entry_score",
        "minimum_primary_score",
        "minimumPrimaryScoreInput",
        "entry_score_threshold",
    }
)

DESCRIPTIVE_METRICS: tuple[tuple[str, str], ...] = (
    ("win_rate_pct", "Win rate (%)"),
    ("profit_factor", "Profit factor"),
    ("expectancy_usd", "Expectancy (USD)"),
    ("max_drawdown_pct", "Max drawdown (%)"),
    ("recovery_factor", "Recovery factor"),
    ("probability_negative_return_pct", "P(negative) (%)"),
    ("bootstrap_interval_width_pct", "Bootstrap 95% width (pp)"),
)

OUTPUT_COLUMNS = (
    "parameter",
    "parameter_label",
    "parameter_value",
    "parameter_value_label",
    "parameter_default",
    "parameter_domain",
    "definition_source",
    "is_default",
    "analysis_role",
    "evidence_status",
    "observed_outcome",
    "controlled_experiment",
    "sample_comparable",
    "sample_start",
    "sample_end",
    "total_trades",
    "win_rate_pct",
    "profit_factor",
    "expectancy_usd",
    "net_return_pct",
    "max_drawdown_pct",
    "recovery_factor",
    "bootstrap_simulations",
    "bootstrap_mean_terminal_return_pct",
    "bootstrap_interval_95_lower_pct",
    "bootstrap_interval_95_upper_pct",
    "bootstrap_interval_width_pct",
    "bootstrap_interval_crosses_zero",
    "probability_negative_return_pct",
    "bootstrap_median_max_drawdown_pct",
    "robust_z_win_rate",
    "robust_z_profit_factor",
    "robust_z_expectancy",
    "robust_z_max_drawdown",
    "robust_z_recovery_factor",
    "robust_z_probability_negative_return",
    "robust_z_bootstrap_interval_width",
    "descriptive_deviation_magnitude",
    "sensitivity_concentration_share_pct",
    "sensitivity_concentration_hhi",
    "sensitivity_effective_contributors",
    "parameter_stability",
    "robustness_region",
    "overfitting_risk",
    "optimization_performed",
    "future_profitability_claimed",
    "limitations",
)


class SensitivityEngineError(RuntimeError):
    """Controlled failure in sensitivity inputs, calculations, or outputs."""


@dataclass(frozen=True)
class ParameterDefinition:
    """Immutable parameter definition audited from Pine or observed exports."""

    key: str
    label: str
    values: tuple[str, ...]
    default: str | None
    source: str
    value_labels: tuple[str, ...]


@dataclass(frozen=True)
class SensitivityResult:
    """Sensitivity evidence table plus non-inferential diagnostics."""

    table: pd.DataFrame
    diagnostics: Mapping[str, Any]


def _hash_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SensitivityEngineError(f"Gagal membaca {path} untuk hashing: {exc}") from exc
    return digest.hexdigest()


def _provenance_label(path: Path) -> str:
    """Prefer portable repo-relative labels while preserving external custom paths."""

    try:
        resolved = path.resolve()
    except OSError:
        return path.as_posix()
    for index, part in enumerate(resolved.parts):
        if part.startswith(".") and part.endswith(".staging"):
            remainder = resolved.parts[index + 1 :]
            return Path(*remainder).as_posix() if remainder else path.name
    try:
        relative = resolved.relative_to(ROOT.resolve())
    except ValueError:
        return path.as_posix()
    return relative.as_posix()


def _load_csv(path: Path, label: str) -> pd.DataFrame:
    if not path.is_file():
        raise SensitivityEngineError(f"Input {label} tidak ditemukan: {path}")
    try:
        frame = pd.read_csv(path, encoding="utf-8")
    except (OSError, UnicodeError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise SensitivityEngineError(f"Gagal membaca {label} {path}: {exc}") from exc
    if frame.empty:
        raise SensitivityEngineError(f"Input {label} kosong: {path}")
    return frame


def _require_columns(frame: pd.DataFrame, required: frozenset[str], label: str) -> None:
    missing = required - set(frame.columns)
    if missing:
        raise SensitivityEngineError(f"Kolom wajib {label} tidak tersedia: {sorted(missing)}")


def _numeric(frame: pd.DataFrame, columns: Sequence[str], label: str) -> pd.DataFrame:
    converted = frame.copy()
    for column in columns:
        values = pd.to_numeric(converted[column], errors="coerce")
        invalid = values.isna() | ~np.isfinite(values.to_numpy(dtype=float))
        if invalid.any():
            rows = (np.flatnonzero(invalid.to_numpy()) + 2).tolist()
            raise SensitivityEngineError(
                f"{label}.{column} bukan numerik finite pada baris CSV {rows}."
            )
        converted[column] = values
    return converted


def _validate_timeframe_inventory(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    values = frame["timeframe"].astype(str)
    if values.duplicated().any():
        duplicates = sorted(values.loc[values.duplicated(keep=False)].unique())
        raise SensitivityEngineError(f"Timeframe duplikat pada {label}: {duplicates}")
    if set(values) != set(TIMEFRAME_ORDER):
        raise SensitivityEngineError(
            f"Inventori timeframe {label} tidak lengkap: {sorted(values.unique())}."
        )
    ordered = frame.assign(
        timeframe=pd.Categorical(values, categories=TIMEFRAME_ORDER, ordered=True)
    ).sort_values("timeframe", kind="stable")
    ordered["timeframe"] = ordered["timeframe"].astype("string")
    return ordered.reset_index(drop=True)


def validate_phase_inputs(
    performance: pd.DataFrame,
    monte_carlo: pd.DataFrame,
    trades: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Strongly validate Phase 2-4 inputs and their cross-file identities."""

    _require_columns(performance, PERFORMANCE_REQUIRED, "performance")
    performance_numeric = (
        "initial_capital_usd",
        "processed_rows",
        "total_trades",
        "win_rate_pct",
        "expectancy_usd",
        "net_return_pct",
        "profit_factor",
        "max_drawdown_pct",
        "recovery_factor",
    )
    performance = _numeric(performance, performance_numeric, "performance")
    performance = _validate_timeframe_inventory(performance, "performance")
    if not (performance["calculation_validation_status"] == "passed_internal_identities").all():
        raise SensitivityEngineError("Performance input belum lulus identity validation internal.")
    if (performance["total_trades"] <= 0).any():
        raise SensitivityEngineError("Setiap timeframe harus memiliki closed trade positif.")

    _require_columns(monte_carlo, MONTE_CARLO_REQUIRED, "monte_carlo")
    bootstrap = monte_carlo.loc[
        monte_carlo["resampling_method"].astype(str).eq("bootstrap")
    ].copy()
    monte_numeric = (
        "simulations",
        "trade_count",
        "observed_terminal_return_pct",
        "mean_terminal_return_pct",
        "terminal_return_interval_95_lower_pct",
        "terminal_return_interval_95_upper_pct",
        "probability_negative_return_pct",
        "median_max_drawdown_pct",
        "max_drawdown_interval_95_upper_pct",
    )
    bootstrap = _numeric(bootstrap, monte_numeric, "monte_carlo.bootstrap")
    bootstrap = _validate_timeframe_inventory(bootstrap, "monte_carlo.bootstrap")
    if (bootstrap["simulations"] < 10_000).any():
        raise SensitivityEngineError("Bootstrap input harus memiliki minimal 10.000 simulasi.")
    if (
        bootstrap["terminal_return_interval_95_lower_pct"]
        > bootstrap["terminal_return_interval_95_upper_pct"]
    ).any():
        raise SensitivityEngineError("Batas bawah interval bootstrap melebihi batas atas.")
    if not bootstrap["probability_negative_return_pct"].between(0.0, 100.0).all():
        raise SensitivityEngineError("Probability negative return harus berada pada [0, 100].")

    _require_columns(trades, TRADE_REQUIRED, "normalized_trades")
    if trades["record_id"].isna().any() or trades["record_id"].duplicated().any():
        raise SensitivityEngineError("record_id normalized kosong atau tidak unik.")
    observed_trade_timeframes = set(trades["timeframe"].astype(str))
    if observed_trade_timeframes != set(TIMEFRAME_ORDER):
        raise SensitivityEngineError("Inventori timeframe normalized_trades tidak lengkap.")
    overlapping_aliases = sorted(PARAMETER_OUTCOME_ALIASES & set(trades.columns))
    if overlapping_aliases:
        raise SensitivityEngineError(
            "Kolom parameter ditemukan tetapi metadata controlled-run tidak tersedia; "
            f"jangan dianalisis secara implisit: {overlapping_aliases}."
        )

    closed_text = trades["is_closed"].astype("string").str.strip().str.casefold()
    if not closed_text.isin({"true", "false"}).all():
        raise SensitivityEngineError("is_closed normalized_trades bukan boolean valid.")
    closed = closed_text.eq("true")
    group_rows = trades.groupby(trades["timeframe"].astype(str)).size()
    group_closed = trades.loc[closed].groupby(trades.loc[closed, "timeframe"].astype(str)).size()
    for record in performance.itertuples(index=False):
        timeframe = str(record.timeframe)
        if int(record.processed_rows) != int(group_rows.get(timeframe, 0)):
            raise SensitivityEngineError(f"processed_rows tidak rekonsiliasi untuk {timeframe}.")
        if int(record.total_trades) != int(group_closed.get(timeframe, 0)):
            raise SensitivityEngineError(f"total_trades tidak rekonsiliasi untuk {timeframe}.")

    merged = performance[["timeframe", "total_trades", "net_return_pct"]].merge(
        bootstrap[
            ["timeframe", "trade_count", "observed_terminal_return_pct"]
        ],
        on="timeframe",
        validate="one_to_one",
    )
    if not np.array_equal(
        merged["total_trades"].to_numpy(dtype=int),
        merged["trade_count"].to_numpy(dtype=int),
    ):
        raise SensitivityEngineError("Jumlah trade Performance dan Monte Carlo tidak rekonsiliasi.")
    if not np.allclose(
        merged["net_return_pct"].to_numpy(dtype=float),
        merged["observed_terminal_return_pct"].to_numpy(dtype=float),
        rtol=0.0,
        atol=1e-8,
    ):
        raise SensitivityEngineError("Observed return Performance dan Monte Carlo tidak rekonsiliasi.")

    return performance, bootstrap, trades.copy()


def parse_pine_parameter_definitions(pine_text: str) -> Mapping[str, ParameterDefinition]:
    """Audit exact HTF and entry-score domains from immutable Pine source text."""

    htf_match = re.search(
        r'higherTimeframeInput\s*=\s*input\.string\(\s*"(?P<default>[^"]+)"\s*,\s*'
        r'"HTF Bias Timeframe"\s*,\s*options=\[(?P<options>[^\]]+)\]',
        pine_text,
    )
    if htf_match is None:
        raise SensitivityEngineError("Definisi HTF Bias Timeframe tidak ditemukan di Pine.")
    htf_values = tuple(re.findall(r'"([^"]+)"', htf_match.group("options")))
    if not htf_values:
        raise SensitivityEngineError("Domain HTF Bias Timeframe kosong di Pine.")
    htf_default = htf_match.group("default")
    if htf_default not in htf_values:
        raise SensitivityEngineError("Default HTF Bias Timeframe tidak berada dalam options.")

    score_match = re.search(
        r'minimumPrimaryScoreInput\s*=\s*input\.int\(\s*(?P<default>\d+)\s*,\s*'
        r'"Minimum Entry Score \(Max 6\)"\s*,\s*minval=(?P<minimum>\d+)\s*,\s*'
        r'maxval=(?P<maximum>\d+)',
        pine_text,
    )
    if score_match is None:
        raise SensitivityEngineError("Definisi Minimum Entry Score tidak ditemukan di Pine.")
    score_minimum = int(score_match.group("minimum"))
    score_maximum = int(score_match.group("maximum"))
    score_default = score_match.group("default")
    if score_minimum > score_maximum:
        raise SensitivityEngineError("Domain Minimum Entry Score terbalik.")
    score_values = tuple(str(value) for value in range(score_minimum, score_maximum + 1))
    if score_default not in score_values:
        raise SensitivityEngineError("Default Minimum Entry Score berada di luar domain.")

    timeframe_definition = ParameterDefinition(
        key="timeframe",
        label="Chart Timeframe",
        values=TIMEFRAME_ORDER,
        default=None,
        source="TradingView chart context and Phase 2-4 timeframe labels",
        value_labels=TIMEFRAME_ORDER,
    )
    htf_labels = tuple({"60": "1h", "240": "4h", "D": "1D"}.get(value, value) for value in htf_values)
    return {
        "timeframe": timeframe_definition,
        "htf_bias_timeframe": ParameterDefinition(
            key="htf_bias_timeframe",
            label="HTF Bias Timeframe",
            values=htf_values,
            default=htf_default,
            source="strategy/MAESTRO-X_v1.0.pine:higherTimeframeInput",
            value_labels=htf_labels,
        ),
        "minimum_entry_score": ParameterDefinition(
            key="minimum_entry_score",
            label="Minimum Entry Score",
            values=score_values,
            default=score_default,
            source="strategy/MAESTRO-X_v1.0.pine:minimumPrimaryScoreInput",
            value_labels=score_values,
        ),
    }


def _robust_z(values: pd.Series) -> np.ndarray:
    data = values.to_numpy(dtype=float)
    median = float(np.median(data))
    mad = float(np.median(np.abs(data - median)))
    scale = 1.4826 * mad
    if not math.isfinite(scale) or scale <= 1e-12:
        q25, q75 = np.percentile(data, [25.0, 75.0])
        scale = float((q75 - q25) / 1.349)
    if not math.isfinite(scale) or scale <= 1e-12:
        scale = float(np.std(data, ddof=1)) if len(data) > 1 else 0.0
    if not math.isfinite(scale) or scale <= 1e-12:
        return np.zeros_like(data, dtype=float)
    return (data - median) / scale


def build_sensitivity_evidence(
    performance: pd.DataFrame,
    bootstrap: pd.DataFrame,
    definitions: Mapping[str, ParameterDefinition],
) -> SensitivityResult:
    """Build a non-optimizing evidence table and descriptive concentration metrics."""

    for key in ("timeframe", "htf_bias_timeframe", "minimum_entry_score"):
        if key not in definitions:
            raise SensitivityEngineError(f"Definisi parameter hilang: {key}")
    performance = _validate_timeframe_inventory(performance.copy(), "performance.analysis")
    bootstrap = _validate_timeframe_inventory(bootstrap.copy(), "bootstrap.analysis")
    merged = performance.merge(
        bootstrap[
            [
                "timeframe",
                "simulations",
                "mean_terminal_return_pct",
                "terminal_return_interval_95_lower_pct",
                "terminal_return_interval_95_upper_pct",
                "probability_negative_return_pct",
                "median_max_drawdown_pct",
            ]
        ],
        on="timeframe",
        validate="one_to_one",
    )
    merged["bootstrap_interval_width_pct"] = (
        merged["terminal_return_interval_95_upper_pct"]
        - merged["terminal_return_interval_95_lower_pct"]
    )

    z_columns: dict[str, np.ndarray] = {}
    for metric, _ in DESCRIPTIVE_METRICS:
        if metric not in merged:
            raise SensitivityEngineError(f"Metrik deskriptif tidak tersedia: {metric}")
        z_columns[metric] = _robust_z(merged[metric])
    z_matrix = np.column_stack([z_columns[key] for key, _ in DESCRIPTIVE_METRICS])
    deviation_magnitude = np.sqrt(np.mean(np.square(z_matrix), axis=1))
    magnitude_sum = float(deviation_magnitude.sum())
    if magnitude_sum <= 0 or not math.isfinite(magnitude_sum):
        concentration_share = np.full(len(merged), 1.0 / len(merged))
    else:
        concentration_share = deviation_magnitude / magnitude_sum
    concentration_hhi = float(np.square(concentration_share).sum())
    normalized_hhi = float(
        (concentration_hhi - 1.0 / len(merged)) / (1.0 - 1.0 / len(merged))
    )
    effective_contributors = float(1.0 / concentration_hhi)

    timeframe_definition = definitions["timeframe"]
    domain = "|".join(timeframe_definition.values)
    rows: list[dict[str, Any]] = []
    z_output_names = (
        "robust_z_win_rate",
        "robust_z_profit_factor",
        "robust_z_expectancy",
        "robust_z_max_drawdown",
        "robust_z_recovery_factor",
        "robust_z_probability_negative_return",
        "robust_z_bootstrap_interval_width",
    )
    for index, record in enumerate(merged.to_dict(orient="records")):
        lower = float(record["terminal_return_interval_95_lower_pct"])
        upper = float(record["terminal_return_interval_95_upper_pct"])
        row: dict[str, Any] = {
            "parameter": "timeframe",
            "parameter_label": timeframe_definition.label,
            "parameter_value": str(record["timeframe"]),
            "parameter_value_label": str(record["timeframe"]),
            "parameter_default": "",
            "parameter_domain": domain,
            "definition_source": timeframe_definition.source,
            "is_default": "",
            "analysis_role": "observed_external_axis",
            "evidence_status": "descriptive_observed_noncontrolled",
            "observed_outcome": True,
            "controlled_experiment": False,
            "sample_comparable": False,
            "sample_start": record["sample_start"],
            "sample_end": record["sample_end"],
            "total_trades": int(record["total_trades"]),
            "win_rate_pct": float(record["win_rate_pct"]),
            "profit_factor": float(record["profit_factor"]),
            "expectancy_usd": float(record["expectancy_usd"]),
            "net_return_pct": float(record["net_return_pct"]),
            "max_drawdown_pct": float(record["max_drawdown_pct"]),
            "recovery_factor": float(record["recovery_factor"]),
            "bootstrap_simulations": int(record["simulations"]),
            "bootstrap_mean_terminal_return_pct": float(record["mean_terminal_return_pct"]),
            "bootstrap_interval_95_lower_pct": lower,
            "bootstrap_interval_95_upper_pct": upper,
            "bootstrap_interval_width_pct": float(record["bootstrap_interval_width_pct"]),
            "bootstrap_interval_crosses_zero": lower <= 0.0 <= upper,
            "probability_negative_return_pct": float(record["probability_negative_return_pct"]),
            "bootstrap_median_max_drawdown_pct": float(record["median_max_drawdown_pct"]),
            "descriptive_deviation_magnitude": float(deviation_magnitude[index]),
            "sensitivity_concentration_share_pct": float(concentration_share[index] * 100.0),
            "sensitivity_concentration_hhi": concentration_hhi,
            "sensitivity_effective_contributors": effective_contributors,
            "parameter_stability": "descriptive_mixed_outcomes_not_formal",
            "robustness_region": "not_identifiable_noncontrolled_samples",
            "overfitting_risk": "indeterminate_elevated_concern_no_oos_or_controlled_grid",
            "optimization_performed": False,
            "future_profitability_claimed": False,
            "limitations": (
                "Different sample periods and incomplete trade logs; chart timeframe is not a "
                "controlled one-factor-at-a-time experiment."
            ),
        }
        row.update(
            {
                output_name: float(z_matrix[index, metric_index])
                for metric_index, output_name in enumerate(z_output_names)
            }
        )
        rows.append(row)

    for parameter_key in ("htf_bias_timeframe", "minimum_entry_score"):
        definition = definitions[parameter_key]
        parameter_domain = "|".join(definition.values)
        for value, value_label in zip(definition.values, definition.value_labels, strict=True):
            row = {column: math.nan for column in OUTPUT_COLUMNS}
            row.update(
                {
                    "parameter": parameter_key,
                    "parameter_label": definition.label,
                    "parameter_value": value,
                    "parameter_value_label": value_label,
                    "parameter_default": definition.default or "",
                    "parameter_domain": parameter_domain,
                    "definition_source": definition.source,
                    "is_default": value == definition.default,
                    "analysis_role": "declared_unobserved_axis",
                    "evidence_status": "not_estimable_no_controlled_outcomes",
                    "observed_outcome": False,
                    "controlled_experiment": False,
                    "sample_comparable": False,
                    "parameter_stability": "not_estimable",
                    "robustness_region": "not_identifiable",
                    "overfitting_risk": "indeterminate_elevated_concern_no_oos_or_controlled_grid",
                    "optimization_performed": False,
                    "future_profitability_claimed": False,
                    "limitations": (
                        "Parameter definition exists in immutable Pine, but no outcome-tagged "
                        "controlled run is present in Phase 2-4 data."
                    ),
                }
            )
            rows.append(row)

    table = pd.DataFrame(rows).loc[:, OUTPUT_COLUMNS]
    positive_expectancy = int((merged["expectancy_usd"] > 0).sum())
    profitable_pf = int((merged["profit_factor"] > 1).sum())
    interval_crosses = (
        (merged["terminal_return_interval_95_lower_pct"] <= 0.0)
        & (merged["terminal_return_interval_95_upper_pct"] >= 0.0)
    )
    descending = np.argsort(concentration_share)[::-1]
    top_two = [str(merged.iloc[index]["timeframe"]) for index in descending[:2]]
    diagnostics: dict[str, Any] = {
        "engine_version": ENGINE_VERSION,
        "analysis_scope": "descriptive_observed_outputs_only",
        "methodology_status": "no_parameter_optimization",
        "timeframes_observed": len(merged),
        "controlled_parameter_grids": 0,
        "parameters_fully_estimable": 0,
        "parameters_descriptive_only": 1,
        "parameters_not_estimable": 2,
        "positive_expectancy_timeframes": positive_expectancy,
        "profit_factor_above_one_timeframes": profitable_pf,
        "bootstrap_intervals_crossing_zero": int(interval_crosses.sum()),
        "bootstrap_intervals_excluding_zero": int((~interval_crosses).sum()),
        "dispersion_concentration_hhi": concentration_hhi,
        "dispersion_concentration_normalized_hhi": normalized_hhi,
        "dispersion_effective_contributors": effective_contributors,
        "largest_dispersion_contributor": str(merged.iloc[descending[0]]["timeframe"]),
        "largest_dispersion_share_pct": float(concentration_share[descending[0]] * 100.0),
        "top_two_dispersion_contributors": top_two,
        "top_two_dispersion_share_pct": float(concentration_share[descending[:2]].sum() * 100.0),
        "parameter_stability": "not_formally_estimable_descriptive_outcomes_mixed",
        "robustness_region": "not_identifiable_without_controlled_grid",
        "overfitting_risk": "indeterminate_with_elevated_evidence_gap",
        "optimization_performed": False,
        "future_profitability_claimed": False,
    }
    return SensitivityResult(table=table, diagnostics=diagnostics)


def _csv_text(frame: pd.DataFrame) -> str:
    buffer = StringIO()
    frame.to_csv(
        buffer,
        index=False,
        encoding="utf-8",
        lineterminator="\n",
        na_rep="",
        float_format="%.10g",
    )
    return buffer.getvalue()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    except OSError as exc:
        raise SensitivityEngineError(f"Gagal menulis {path}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    except OSError as exc:
        raise SensitivityEngineError(f"Gagal menulis {path}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def render_parameter_surface(result: SensitivityResult) -> bytes:
    """Render a deterministic evidence surface, never a synthetic response surface."""

    observed = result.table.loc[result.table["parameter"] == "timeframe"].copy()
    if len(observed) != len(TIMEFRAME_ORDER):
        raise SensitivityEngineError("Evidence surface memerlukan delapan timeframe.")
    z_columns = [
        "robust_z_win_rate",
        "robust_z_profit_factor",
        "robust_z_expectancy",
        "robust_z_max_drawdown",
        "robust_z_recovery_factor",
        "robust_z_probability_negative_return",
        "robust_z_bootstrap_interval_width",
    ]
    z_matrix = observed[z_columns].to_numpy(dtype=float)
    if not np.isfinite(z_matrix).all():
        raise SensitivityEngineError("Robust-z matrix mengandung nilai non-finite.")

    figure = plt.figure(figsize=(15, 10.2), constrained_layout=False)
    grid = figure.add_gridspec(
        2,
        2,
        height_ratios=(3.3, 1.35),
        width_ratios=(4.4, 1.8),
        left=0.075,
        right=0.975,
        bottom=0.095,
        top=0.89,
        hspace=0.47,
        wspace=0.29,
    )
    heat_axis = figure.add_subplot(grid[0, 0])
    share_axis = figure.add_subplot(grid[0, 1])
    evidence_axis = figure.add_subplot(grid[1, :])

    color_limit = max(2.5, float(np.ceil(np.max(np.abs(z_matrix)) * 2.0) / 2.0))
    heat = heat_axis.imshow(
        z_matrix,
        aspect="auto",
        cmap="RdBu_r",
        norm=TwoSlopeNorm(vmin=-color_limit, vcenter=0.0, vmax=color_limit),
    )
    heat_axis.set_xticks(np.arange(len(DESCRIPTIVE_METRICS)))
    heat_axis.set_xticklabels(
        [label.replace(" ", "\n", 1) for _, label in DESCRIPTIVE_METRICS],
        fontsize=8.2,
    )
    heat_axis.set_yticks(np.arange(len(observed)))
    heat_axis.set_yticklabels(observed["parameter_value"], fontsize=9.5)
    heat_axis.set_title(
        "A. Observed deviation from cross-timeframe median",
        loc="left",
        fontsize=11.5,
        fontweight="bold",
        pad=12,
    )
    heat_axis.set_ylabel("Chart timeframe (non-comparable samples)")
    heat_axis.tick_params(length=0)
    for row_index in range(z_matrix.shape[0]):
        for column_index in range(z_matrix.shape[1]):
            value = z_matrix[row_index, column_index]
            text_color = "white" if abs(value) > color_limit * 0.56 else "#17212b"
            heat_axis.text(
                column_index,
                row_index,
                f"{value:+.2f}",
                ha="center",
                va="center",
                fontsize=7.8,
                color=text_color,
                fontweight="bold" if abs(value) > color_limit * 0.8 else "normal",
            )
    colorbar = figure.colorbar(heat, ax=heat_axis, fraction=0.028, pad=0.025)
    colorbar.set_label("Robust z = (value - median) / (1.4826 x MAD)", fontsize=8.5)
    colorbar.ax.tick_params(labelsize=8)

    shares = observed["sensitivity_concentration_share_pct"].to_numpy(dtype=float)
    y_positions = np.arange(len(observed))
    share_axis.barh(y_positions, shares, color="#355C7D", alpha=0.92)
    share_axis.axvline(100.0 / len(observed), color="#C06C84", linestyle="--", linewidth=1.1)
    share_axis.set_yticks(y_positions)
    share_axis.set_yticklabels(observed["parameter_value"], fontsize=9)
    share_axis.invert_yaxis()
    share_axis.set_xlabel("Share of total deviation magnitude (%)", fontsize=8.5)
    share_axis.set_title(
        "B. Sensitivity concentration",
        loc="left",
        fontsize=11.5,
        fontweight="bold",
        pad=12,
    )
    share_axis.grid(axis="x", alpha=0.22)
    share_axis.spines[["top", "right"]].set_visible(False)
    for y_position, share in zip(y_positions, shares, strict=True):
        share_axis.text(share + 0.35, y_position, f"{share:.1f}%", va="center", fontsize=8.5)
    share_axis.text(
        0.02,
        -0.13,
        f"HHI {float(result.diagnostics['dispersion_concentration_hhi']):.3f} | "
        f"effective contributors {float(result.diagnostics['dispersion_effective_contributors']):.2f}",
        transform=share_axis.transAxes,
        fontsize=8.5,
        color="#455A64",
    )

    readiness = np.array(
        [
            [1, 1, 0, 0, 0, 0],
            [1, 0, 0, 0, 0, 0],
            [1, 0, 0, 0, 0, 0],
        ],
        dtype=int,
    )
    evidence_axis.imshow(
        readiness,
        aspect="auto",
        cmap=ListedColormap(["#E8EDF2", "#3A7D44"]),
        vmin=0,
        vmax=1,
    )
    evidence_axis.set_xticks(np.arange(readiness.shape[1]))
    evidence_axis.set_xticklabels(
        [
            "Definition\nknown",
            "Outcome values\nobserved",
            "Controlled\ngrid",
            "Same feed/range/\ncost controls",
            "Replicated\nwindows",
            "Out-of-\nsample",
        ],
        fontsize=8.5,
    )
    evidence_axis.set_yticks(np.arange(readiness.shape[0]))
    evidence_axis.set_yticklabels(
        ["Chart timeframe", "HTF Bias Timeframe", "Minimum Entry Score"],
        fontsize=9.2,
    )
    evidence_axis.set_title(
        "C. Evidence readiness by requested parameter dimension",
        loc="left",
        fontsize=11.5,
        fontweight="bold",
        pad=10,
    )
    evidence_axis.tick_params(length=0)
    for row_index in range(readiness.shape[0]):
        for column_index in range(readiness.shape[1]):
            available = bool(readiness[row_index, column_index])
            evidence_axis.text(
                column_index,
                row_index,
                "YES" if available else "NO",
                ha="center",
                va="center",
                fontsize=8.5,
                fontweight="bold",
                color="white" if available else "#52606D",
            )
    for x in np.arange(-0.5, readiness.shape[1], 1.0):
        evidence_axis.axvline(x, color="white", linewidth=1.5)
    for y in np.arange(-0.5, readiness.shape[0], 1.0):
        evidence_axis.axhline(y, color="white", linewidth=1.5)

    figure.suptitle(
        "MAESTRO-X PHASE 5 - Parameter Sensitivity Evidence Surface",
        fontsize=17,
        fontweight="bold",
        y=0.972,
    )
    figure.text(
        0.5,
        0.925,
        "NO OPTIMIZATION | timeframe is descriptive only | missing HTF/score outcomes are not interpolated",
        ha="center",
        fontsize=10,
        color="#A23B3B",
        fontweight="bold",
    )
    figure.text(
        0.5,
        0.025,
        "No robustness region is identifiable without a controlled grid, identical sampling controls, and OOS evidence. "
        "Robust z indicates deviation, not desirability.",
        ha="center",
        fontsize=8.8,
        color="#455A64",
    )

    buffer = BytesIO()
    figure.savefig(
        buffer,
        format="png",
        dpi=180,
        bbox_inches="tight",
        metadata={
            "Title": "MAESTRO-X Parameter Sensitivity Evidence Surface",
            "Author": "MAESTRO-X Research Framework",
            "Software": f"MAESTRO-X Sensitivity Engine {ENGINE_VERSION}",
        },
    )
    plt.close(figure)
    return buffer.getvalue()


def _format_number(value: Any, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    if isinstance(value, (bool, np.bool_)):
        return "Ya" if bool(value) else "Tidak"
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    if isinstance(value, (float, np.floating)):
        return f"{float(value):,.{digits}f}"
    return escape(str(value))


def _html_table(frame: pd.DataFrame, columns: Sequence[tuple[str, str, int]]) -> str:
    header = "".join(f"<th scope=\"col\">{escape(label)}</th>" for _, label, _ in columns)
    rows: list[str] = []
    for record in frame.to_dict(orient="records"):
        cells = "".join(
            f"<td>{_format_number(record[column], digits)}</td>"
            for column, _, digits in columns
        )
        rows.append(f"<tr>{cells}</tr>")
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + header
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def render_sensitivity_report(
    result: SensitivityResult,
    figure_png: bytes,
    input_hashes: Mapping[str, str],
) -> str:
    """Render a self-contained, deterministic, academically bounded HTML report."""

    observed = result.table.loc[result.table["parameter"] == "timeframe"].copy()
    unavailable = result.table.loc[
        result.table["parameter"].isin(["htf_bias_timeframe", "minimum_entry_score"])
    ].copy()
    parameter_summary = (
        unavailable.groupby(
            ["parameter_label", "parameter_domain", "parameter_default", "evidence_status"],
            dropna=False,
            as_index=False,
        )
        .size()
        .rename(columns={"size": "defined_values"})
    )
    figure_data = b64encode(figure_png).decode("ascii")
    diagnostics = result.diagnostics
    hash_rows = "".join(
        "<tr><td><code>" + escape(name) + "</code></td><td><code>" + escape(value) + "</code></td></tr>"
        for name, value in input_hashes.items()
    )
    top_two = ", ".join(str(value) for value in diagnostics["top_two_dispersion_contributors"])

    timeframe_columns = (
        ("parameter_value", "Timeframe", 0),
        ("total_trades", "Closed trades", 0),
        ("win_rate_pct", "Win rate (%)", 2),
        ("profit_factor", "Profit factor", 3),
        ("expectancy_usd", "Expectancy (USD)", 4),
        ("max_drawdown_pct", "Max DD (%)", 2),
        ("recovery_factor", "Recovery", 3),
        ("bootstrap_interval_95_lower_pct", "Bootstrap P2.5 (%)", 2),
        ("bootstrap_interval_95_upper_pct", "Bootstrap P97.5 (%)", 2),
        ("probability_negative_return_pct", "P(negative) (%)", 2),
        ("sensitivity_concentration_share_pct", "Dispersion share (%)", 2),
    )
    parameter_columns = (
        ("parameter_label", "Parameter", 0),
        ("parameter_domain", "Defined domain", 0),
        ("parameter_default", "Pine default", 0),
        ("defined_values", "Defined values", 0),
        ("evidence_status", "Evidence status", 0),
    )

    css = """
:root { --ink:#17212b; --muted:#52606d; --line:#d7dee5; --panel:#f7f9fb; --accent:#244f73; --warn:#8c2f39; --ok:#2f6b3a; }
* { box-sizing:border-box; }
body { margin:0; background:#eef2f5; color:var(--ink); font-family:Inter,Segoe UI,Arial,sans-serif; line-height:1.55; }
main { max-width:1180px; margin:0 auto; padding:38px 34px 60px; background:white; min-height:100vh; box-shadow:0 0 28px rgba(23,33,43,.08); }
h1 { margin:0 0 4px; font-size:2rem; line-height:1.18; color:#183a55; }
.subtitle { margin:0 0 24px; color:var(--muted); }
.banner { border-left:5px solid var(--warn); background:#fff4f3; padding:14px 16px; margin:20px 0 24px; }
.cards { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:22px 0 28px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }
.card .value { display:block; color:var(--accent); font-size:1.55rem; font-weight:750; line-height:1.2; }
.card .label { color:var(--muted); font-size:.82rem; }
h2 { margin:34px 0 10px; font-size:1.35rem; color:#183a55; border-bottom:1px solid var(--line); padding-bottom:5px; }
h3 { margin:22px 0 7px; font-size:1.02rem; }
p,li { font-size:.95rem; }
code { font-family:Consolas,monospace; font-size:.87em; overflow-wrap:anywhere; }
.table-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:6px; }
table { border-collapse:collapse; width:100%; min-width:760px; font-size:.79rem; }
th { background:#eaf0f5; color:#183a55; text-align:left; position:sticky; top:0; }
th,td { padding:8px 9px; border-bottom:1px solid var(--line); white-space:nowrap; }
tbody tr:nth-child(even) { background:#fafbfd; }
figure { margin:18px 0; }
figure img { display:block; width:100%; height:auto; border:1px solid var(--line); border-radius:5px; }
figcaption { color:var(--muted); font-size:.82rem; margin-top:7px; }
.method { background:#f3f7fa; border:1px solid var(--line); padding:14px 18px; border-radius:7px; }
.status-no { color:var(--warn); font-weight:700; }
.status-ok { color:var(--ok); font-weight:700; }
footer { margin-top:38px; padding-top:14px; border-top:1px solid var(--line); color:var(--muted); font-size:.78rem; }
@media (max-width:800px) { main { padding:25px 17px 45px; } h1 { font-size:1.55rem; } .cards { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:480px) { .cards { grid-template-columns:1fr; } }
"""
    return f"""<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>MAESTRO-X PHASE 5 - Sensitivity Report</title>
<style>{css}</style>
</head>
<body><main>
<h1>MAESTRO-X PHASE 5</h1>
<p class="subtitle">Parameter Sensitivity and Robustness Evidence Report | Engine {ENGINE_VERSION} | <strong>NO OPTIMIZATION</strong></p>
<div class="banner"><strong>Batas kesimpulan:</strong> tidak ada controlled parameter grid pada data saat ini. Timeframe hanya dianalisis secara deskriptif; sensitivitas HTF Bias Timeframe dan Minimum Entry Score berstatus <code>not_estimable</code>. Dokumen ini tidak mengoptimasi parameter dan tidak menyatakan profitabilitas masa depan.</div>
<section class="cards" aria-label="Ringkasan bukti">
  <div class="card"><span class="value">8</span><span class="label">timeframe terobservasi, non-comparable</span></div>
  <div class="card"><span class="value">2/3</span><span class="label">dimensi parameter tidak dapat diestimasi</span></div>
  <div class="card"><span class="value">0</span><span class="label">controlled parameter grids</span></div>
  <div class="card"><span class="value">NO</span><span class="label">optimasi atau pemilihan parameter</span></div>
</section>

<h2>Kesimpulan metodologis</h2>
<ul>
  <li><strong>Parameter stability:</strong> <code>{escape(str(diagnostics['parameter_stability']))}</code>. Sebanyak {int(diagnostics['positive_expectancy_timeframes'])}/8 timeframe memiliki expectancy positif dan {int(diagnostics['profit_factor_above_one_timeframes'])}/8 memiliki profit factor di atas 1, tetapi satu perubahan tanda dan perbedaan periode sampel mencegah klaim stabilitas formal.</li>
  <li><strong>Robustness region:</strong> <code>{escape(str(diagnostics['robustness_region']))}</code>. Tidak ada wilayah parameter yang defensibel untuk ditandai robust.</li>
  <li><strong>Overfitting risk:</strong> <code>{escape(str(diagnostics['overfitting_risk']))}</code>. Risiko tidak dapat dihitung sebagai probabilitas tanpa riwayat sweep, multiple-testing record, OOS, dan walk-forward.</li>
  <li><strong>Sensitivity concentration:</strong> HHI deskriptif {float(diagnostics['dispersion_concentration_hhi']):.4f}; effective contributors {float(diagnostics['dispersion_effective_contributors']):.2f} dari 8. {escape(top_two)} bersama-sama menyumbang {float(diagnostics['top_two_dispersion_share_pct']):.2f}% dari magnitudo dispersi. Ini menunjukkan kontribusi terhadap perbedaan, bukan kualitas atau ranking.</li>
</ul>

<h2>Parameter evidence surface</h2>
<figure>
  <img src="data:image/png;base64,{figure_data}" alt="Evidence surface sensitivitas parameter MAESTRO-X tanpa optimasi">
  <figcaption>Robust z mengukur jarak dari median lintas-timeframe. Tanda dan besar nilainya bukan utility score. Matriks kesiapan memperlihatkan mengapa response surface tiga parameter tidak dapat dibangun secara sah.</figcaption>
</figure>

<h2>Observed timeframe evidence</h2>
{_html_table(observed, timeframe_columns)}
<p>Interval bootstrap adalah interval persentil simulasi conditional pada observed IID trades dari Phase 4, bukan confidence interval inferensial dan bukan bukti signifikansi. {int(diagnostics['bootstrap_intervals_crossing_zero'])}/8 interval melintasi nol; {int(diagnostics['bootstrap_intervals_excluding_zero'])} yang tidak melintasi nol tidak boleh ditafsirkan sebagai jaminan atau superiority.</p>

<h2>Unobserved parameter axes</h2>
{_html_table(parameter_summary, parameter_columns)}
<p>Domain parameter diverifikasi dari Pine immutable: HTF Bias Timeframe memiliki nilai <code>60|240|D</code> (default <code>60</code>), sedangkan Minimum Entry Score memiliki nilai integer <code>2|3|4|5|6</code> (default <code>4</code>). Definisi domain bukan hasil eksperimen.</p>

<h2>Metodologi</h2>
<div class="method">
<ol>
  <li>Rekonsiliasi jumlah closed trades dan observed terminal return antara Phase 2, Phase 3, dan bootstrap Phase 4.</li>
  <li>Untuk tujuh metrik deskriptif, hitung <code>robust z = (x - median) / (1.4826 x MAD)</code>. Fallback IQR atau sample standard deviation digunakan hanya bila MAD nol.</li>
  <li>Magnitudo deviasi per timeframe adalah root-mean-square dari tujuh robust z. Share konsentrasi adalah magnitudo dibagi total magnitudo.</li>
  <li>HHI adalah jumlah kuadrat share; effective contributors adalah <code>1 / HHI</code>. Tidak ada cutoff inferensial atau fungsi objektif.</li>
  <li>HTF Bias Timeframe dan Minimum Entry Score tidak diberi nilai performa, tidak diinterpolasi, dan tidak diranking karena outcome-tagged controlled runs tidak tersedia.</li>
</ol>
</div>

<h2>Keterbatasan dan prasyarat robustness region</h2>
<ul>
  <li>Periode chart, jumlah trade, dan kelengkapan trade ID berbeda antartimeframe; perbandingan tidak kausal.</li>
  <li>Initial capital USD 40 pada metadata/export masih konflik dengan deklarasi Pine USD 10.000.</li>
  <li>Tidak tersedia exact feed snapshot, timezone, date range seragam, biaya eksekusi lengkap, repeated windows, OOS, atau walk-forward.</li>
  <li>Robustness region memerlukan grid yang dipraregistrasi dan identik untuk seluruh kombinasi parameter, tanpa memilih kombinasi berdasarkan hasil.</li>
  <li>Hasil historis dan simulasi tidak menjamin performa masa depan.</li>
</ul>

<h2>Input provenance</h2>
<div class="table-wrap"><table><thead><tr><th>Input</th><th>SHA-256</th></tr></thead><tbody>{hash_rows}</tbody></table></div>
<footer>Deterministic report generated only from immutable Phase 2-4 outputs and Pine parameter definitions. Optimization performed: <span class="status-no">false</span>. Future profitability claimed: <span class="status-no">false</span>.</footer>
</main></body></html>
"""


def run_sensitivity_pipeline(
    *,
    performance_path: Path = TABLES_DIR / "performance_metrics.csv",
    monte_carlo_path: Path = TABLES_DIR / "monte_carlo_statistics.csv",
    trades_path: Path = PROCESSED_TRADES_DIR / "normalized_trades.csv",
    pine_path: Path | None = None,
    table_path: Path = TABLES_DIR / "parameter_sensitivity.csv",
    figure_path: Path = FIGURES_DIR / "parameter_surface.png",
    report_path: Path = REPORTS_DIR / "sensitivity_report.html",
) -> Mapping[str, Any]:
    """Validate inputs, calculate evidence, and atomically publish PHASE 5 outputs."""

    if pine_path is None:
        pine_path = Path(__file__).resolve().parents[1] / "strategy" / "MAESTRO-X_v1.0.pine"
    performance = _load_csv(performance_path, "performance")
    monte_carlo = _load_csv(monte_carlo_path, "monte_carlo")
    trades = _load_csv(trades_path, "normalized_trades")
    performance, bootstrap, _ = validate_phase_inputs(performance, monte_carlo, trades)
    try:
        pine_text = pine_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SensitivityEngineError(f"Gagal membaca Pine immutable {pine_path}: {exc}") from exc
    definitions = parse_pine_parameter_definitions(pine_text)
    result = build_sensitivity_evidence(performance, bootstrap, definitions)
    figure_png = render_parameter_surface(result)

    input_hashes = {
        _provenance_label(performance_path): _hash_file(performance_path),
        _provenance_label(monte_carlo_path): _hash_file(monte_carlo_path),
        _provenance_label(trades_path): _hash_file(trades_path),
        _provenance_label(pine_path): _hash_file(pine_path),
    }
    report_html = render_sensitivity_report(result, figure_png, input_hashes)

    _atomic_write_text(table_path, _csv_text(result.table))
    _atomic_write_bytes(figure_path, figure_png)
    _atomic_write_text(report_path, report_html)

    output_hashes = {
        _provenance_label(table_path): _hash_file(table_path),
        _provenance_label(figure_path): _hash_file(figure_path),
        _provenance_label(report_path): _hash_file(report_path),
    }
    LOGGER.info(
        "PHASE 5 selesai: %d rows; robustness region %s; optimization=%s",
        len(result.table),
        result.diagnostics["robustness_region"],
        result.diagnostics["optimization_performed"],
    )
    return {
        "rows": len(result.table),
        "diagnostics": dict(result.diagnostics),
        "input_hashes": input_hashes,
        "output_hashes": output_hashes,
    }
