"""Observed-subset performance analysis for PHASE 3.

The engine consumes only normalized records from ``data/processed``.  It does
not read raw trade exports, mutate the Pine strategy, optimize parameters, or
run robustness/sensitivity analysis.  Every metric is calculated separately
per timeframe because the files represent independent TradingView backtests.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
import logging
import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import (
    FIGURES_DIR,
    PROCESSED_TRADES_DIR,
    TABLES_DIR,
    TIMEFRAME_SPECS,
    get_initial_capital,
    load_metadata,
)
from .metrics import MetricsError, build_equity_curve, calculate_performance_metrics
from .schema import PERFORMANCE_SCHEMA, validate_dataframe_schema


LOGGER = logging.getLogger(__name__)
ENGINE_VERSION = "1.0.0"
TIMEFRAME_ORDER = tuple(spec.label for spec in TIMEFRAME_SPECS)

REQUIRED_COLUMNS = frozenset(
    {
        "record_id",
        "timeframe",
        "trade_id",
        "side",
        "entry_timestamp",
        "exit_timestamp",
        "net_pnl_usd",
        "raw_cumulative_pnl_usd",
        "raw_cumulative_pnl_pct",
        "is_closed",
        "trade_status",
        "source_file",
        "source_sha256",
        "source_row",
    }
)

NUMERIC_COLUMNS = (
    "trade_id",
    "entry_price",
    "exit_price",
    "contracts",
    "position_value_usd",
    "net_pnl_usd",
    "net_pnl_pct",
    "raw_cumulative_pnl_usd",
    "raw_cumulative_pnl_pct",
    "bars_in_trade",
)

# Audited secondary reference from the comparison table rendered on page 8 of
# both docs/laporanindikator.pdf and docs/laporanbacktest.pdf.  These values are
# not treated as ground truth because their trade counts do not match the
# processed snapshot.
TRADINGVIEW_REPORT_REFERENCE: Mapping[str, Mapping[str, float | int]] = {
    "1m": {"total_trades": 262, "net_profit_usd": 10.47, "profit_factor": 1.129, "win_rate_pct": 48.85, "max_drawdown_pct": 15.68},
    "5m": {"total_trades": 221, "net_profit_usd": 21.89, "profit_factor": 1.332, "win_rate_pct": 51.13, "max_drawdown_pct": 14.20},
    "15m": {"total_trades": 222, "net_profit_usd": 3.19, "profit_factor": 1.047, "win_rate_pct": 48.65, "max_drawdown_pct": 16.73},
    "30m": {"total_trades": 299, "net_profit_usd": 12.09, "profit_factor": 1.155, "win_rate_pct": 48.49, "max_drawdown_pct": 19.57},
    "1h": {"total_trades": 429, "net_profit_usd": 25.22, "profit_factor": 1.203, "win_rate_pct": 49.88, "max_drawdown_pct": 15.42},
    "2h": {"total_trades": 227, "net_profit_usd": 24.49, "profit_factor": 1.378, "win_rate_pct": 52.42, "max_drawdown_pct": 10.21},
    "4h": {"total_trades": 282, "net_profit_usd": 66.93, "profit_factor": 1.627, "win_rate_pct": 57.45, "max_drawdown_pct": 9.16},
    "1D": {"total_trades": 496, "net_profit_usd": 14.12, "profit_factor": 1.093, "win_rate_pct": 47.18, "max_drawdown_pct": 27.87},
}

OUTPUT_COLUMNS = PERFORMANCE_SCHEMA.columns


class PerformanceEngineError(RuntimeError):
    """Controlled failure in processed-data loading, calculation, or output."""


def _hash_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PerformanceEngineError(f"Gagal membaca {path} untuk hashing: {exc}") from exc
    return digest.hexdigest()


def _processed_hashes(directory: Path) -> dict[str, str]:
    files = sorted(path for path in directory.glob("*") if path.is_file())
    if not files:
        raise PerformanceEngineError(f"Tidak ada input processed di {directory}.")
    return {path.name: _hash_file(path) for path in files}


def _parse_boolean_series(series: pd.Series, field: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    normalized = series.astype("string").str.strip().str.casefold()
    mapped = normalized.map({"true": True, "false": False})
    if mapped.isna().any():
        rows = (np.flatnonzero(mapped.isna().to_numpy()) + 2).tolist()
        raise PerformanceEngineError(f"{field} bukan boolean valid pada baris CSV {rows}.")
    return mapped.astype(bool)


def load_processed_trades(path: Path) -> pd.DataFrame:
    """Load and strongly validate the canonical PHASE 2 normalized dataset."""

    if not path.is_file():
        raise PerformanceEngineError(f"Dataset processed tidak ditemukan: {path}")
    try:
        frame = pd.read_csv(path, encoding="utf-8")
    except (OSError, UnicodeError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise PerformanceEngineError(f"Gagal membaca {path}: {exc}") from exc
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise PerformanceEngineError(f"Kolom processed wajib tidak tersedia: {sorted(missing)}")
    if frame.empty:
        raise PerformanceEngineError("Dataset processed kosong.")

    for field in ("entry_timestamp", "exit_timestamp"):
        parsed = pd.to_datetime(frame[field], format="ISO8601", errors="coerce")
        if parsed.isna().any():
            rows = (np.flatnonzero(parsed.isna().to_numpy()) + 2).tolist()
            raise PerformanceEngineError(f"{field} invalid pada baris CSV {rows}.")
        frame[field] = parsed
    for field in NUMERIC_COLUMNS:
        if field not in frame:
            continue
        parsed_numeric = pd.to_numeric(frame[field], errors="coerce")
        if parsed_numeric.isna().any():
            rows = (np.flatnonzero(parsed_numeric.isna().to_numpy()) + 2).tolist()
            raise PerformanceEngineError(f"{field} invalid pada baris CSV {rows}.")
        if not np.isfinite(parsed_numeric.to_numpy(dtype=float)).all():
            raise PerformanceEngineError(f"{field} mengandung nilai non-finite.")
        frame[field] = parsed_numeric
    frame["is_closed"] = _parse_boolean_series(frame["is_closed"], "is_closed")

    if frame["record_id"].isna().any() or frame["record_id"].duplicated().any():
        raise PerformanceEngineError("record_id kosong atau tidak unik.")
    observed_timeframes = set(frame["timeframe"].astype(str))
    expected_timeframes = set(TIMEFRAME_ORDER)
    if observed_timeframes != expected_timeframes:
        raise PerformanceEngineError(
            f"Inventori timeframe processed tidak lengkap: {sorted(observed_timeframes)}."
        )
    if (frame["exit_timestamp"] < frame["entry_timestamp"]).any():
        raise PerformanceEngineError("Terdapat exit_timestamp sebelum entry_timestamp.")
    if not (frame["is_closed"] == frame["trade_status"].astype(str).eq("closed")).all():
        raise PerformanceEngineError("is_closed tidak konsisten dengan trade_status.")
    return frame


def _load_quarantine_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {timeframe: 0 for timeframe in TIMEFRAME_ORDER}
    try:
        rejected = pd.read_csv(path, encoding="utf-8")
    except (OSError, UnicodeError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise PerformanceEngineError(f"Gagal membaca quarantine {path}: {exc}") from exc
    if rejected.empty:
        return {timeframe: 0 for timeframe in TIMEFRAME_ORDER}
    if "expected_timeframe" not in rejected:
        raise PerformanceEngineError("Quarantine tidak memiliki expected_timeframe.")
    counts = rejected.groupby("expected_timeframe").size().to_dict()
    return {timeframe: int(counts.get(timeframe, 0)) for timeframe in TIMEFRAME_ORDER}


def _implied_initial_capital(trades: pd.DataFrame) -> float:
    usable = trades.loc[trades["raw_cumulative_pnl_pct"].abs() > 1e-12]
    if usable.empty:
        raise PerformanceEngineError("Initial capital tidak dapat divalidasi dari cumulative PnL.")
    implied = (
        usable["raw_cumulative_pnl_usd"]
        / usable["raw_cumulative_pnl_pct"]
        * 100.0
    )
    finite = implied[np.isfinite(implied.to_numpy(dtype=float))]
    if finite.empty:
        raise PerformanceEngineError("Rasio implied initial capital tidak finite.")
    return float(finite.median())


def _validate_metric_identities(
    metrics: Mapping[str, object],
    curve: pd.DataFrame,
) -> None:
    total = int(metrics["total_trades"])
    wins = int(metrics["winning_trades"])
    losses = int(metrics["losing_trades"])
    breakeven = int(metrics["breakeven_trades"])
    if total != wins + losses + breakeven:
        raise PerformanceEngineError("Total trade tidak sama dengan win + loss + breakeven.")
    gross_profit = float(metrics["gross_profit_usd"])
    gross_loss = float(metrics["gross_loss_usd"])
    net_profit = float(metrics["net_profit_usd"])
    if not math.isclose(net_profit, gross_profit - gross_loss, abs_tol=1e-9):
        raise PerformanceEngineError("Net profit tidak terekonsiliasi dengan gross profit/loss.")
    expectancy = float(metrics["expectancy_usd"])
    if not math.isclose(expectancy * total, net_profit, abs_tol=1e-9):
        raise PerformanceEngineError("Expectancy tidak terekonsiliasi dengan net profit.")
    ending_equity = float(curve["equity_usd"].iloc[-1])
    expected_ending = float(metrics["initial_capital_usd"]) + net_profit
    if not math.isclose(ending_equity, expected_ending, abs_tol=1e-9):
        raise PerformanceEngineError("Ending equity tidak terekonsiliasi dengan net profit.")
    if not math.isclose(
        float(curve["max_drawdown_usd"].iloc[0])
        if "max_drawdown_usd" in curve
        else float(curve["drawdown_usd"].max()),
        float(metrics["max_drawdown_usd"]),
        abs_tol=1e-9,
    ):
        raise PerformanceEngineError("Maximum drawdown tidak terekonsiliasi dengan equity curve.")


def build_performance_table(
    trades: pd.DataFrame,
    *,
    initial_capital: float,
    quarantine_counts: Mapping[str, int] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate per-timeframe performance and return table plus equity curves."""

    if initial_capital <= 0:
        raise PerformanceEngineError("Initial capital harus positif.")
    quarantine_counts = quarantine_counts or {}
    rows: list[dict[str, Any]] = []
    curves: list[pd.DataFrame] = []

    for timeframe in TIMEFRAME_ORDER:
        group = trades.loc[trades["timeframe"] == timeframe].copy()
        if group.empty:
            raise PerformanceEngineError(f"Tidak ada processed row untuk timeframe {timeframe}.")
        try:
            metrics = calculate_performance_metrics(group, initial_capital)
            curve = build_equity_curve(group, initial_capital)
        except MetricsError as exc:
            raise PerformanceEngineError(f"Metrik {timeframe} gagal: {exc}") from exc

        curve["max_drawdown_usd"] = float(curve["drawdown_usd"].max())
        _validate_metric_identities(metrics, curve)
        curves.append(curve)

        closed = group.loc[group["is_closed"]].sort_values(
            ["exit_timestamp", "trade_id"], kind="stable"
        )
        implied_capital = _implied_initial_capital(group)
        cumulative_endpoint = float(closed["raw_cumulative_pnl_usd"].iloc[-1])
        endpoint_delta = float(metrics["net_profit_usd"]) - cumulative_endpoint
        endpoint_matches = math.isclose(endpoint_delta, 0.0, abs_tol=0.01)

        reference = TRADINGVIEW_REPORT_REFERENCE[timeframe]
        tv_count_matches = int(metrics["total_trades"]) == int(reference["total_trades"])
        validation_status = (
            "comparable_trade_count_match"
            if tv_count_matches
            else "not_comparable_snapshot_mismatch"
        )
        row = dict(metrics)
        row.update(
            {
                "data_scope": "observed_processed_closed_trades_only",
                "initial_capital_source": "data/metadata/dataset_info.yaml plus exported cumulative-PnL ratio",
                "initial_capital_provenance_status": "supported_by_export_but_conflicts_with_pine_declaration",
                "implied_initial_capital_median_usd": implied_capital,
                "implied_initial_capital_matches_assumption": math.isclose(
                    implied_capital, initial_capital, abs_tol=0.10
                ),
                "processed_rows": len(group),
                "open_trades_excluded": int((~group["is_closed"]).sum()),
                "quarantined_rows_upstream": int(quarantine_counts.get(timeframe, 0)),
                "average_loss_abs_usd": abs(float(metrics["average_loss_usd"])),
                "ending_equity_usd": float(curve["equity_usd"].iloc[-1]),
                "equity_reconciliation_passed": True,
                "raw_cumulative_endpoint_usd": cumulative_endpoint,
                "observed_net_minus_raw_endpoint_usd": endpoint_delta,
                "raw_cumulative_endpoint_matches_observed_net": endpoint_matches,
                "tv_report_total_trades": int(reference["total_trades"]),
                "tv_report_net_profit_usd": float(reference["net_profit_usd"]),
                "tv_report_profit_factor": float(reference["profit_factor"]),
                "tv_report_win_rate_pct": float(reference["win_rate_pct"]),
                "tv_report_max_drawdown_pct": float(reference["max_drawdown_pct"]),
                "tv_trade_count_difference": int(metrics["total_trades"])
                - int(reference["total_trades"]),
                "tv_snapshot_comparable": tv_count_matches,
                "tv_validation_status": validation_status,
                "tv_reference_source": "docs/laporanindikator.pdf:p8 and docs/laporanbacktest.pdf:p8",
                "calculation_validation_status": "passed_internal_identities",
                "publication_eligible": False,
            }
        )
        rows.append(row)
        LOGGER.info(
            "%s: %d closed trades, net %.3f USD, PF %.4f, max DD %.3f USD",
            timeframe,
            int(metrics["total_trades"]),
            float(metrics["net_profit_usd"]),
            float(metrics["profit_factor"]),
            float(metrics["max_drawdown_usd"]),
        )

    table = pd.DataFrame(rows)
    table = table.loc[:, OUTPUT_COLUMNS]
    equity_curves = pd.concat(curves, ignore_index=True)
    return table, equity_curves


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


def _write_text_atomic(path: Path, text: str) -> None:
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
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    except OSError as exc:
        raise PerformanceEngineError(f"Gagal menulis {path}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _render_equity_figure(
    curves: pd.DataFrame,
    performance: pd.DataFrame,
    path: Path,
) -> None:
    """Render independent observed equity curves for all eight timeframes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(4, 2, figsize=(13, 15), constrained_layout=False)
    figure.subplots_adjust(
        left=0.075,
        right=0.985,
        bottom=0.075,
        top=0.94,
        hspace=0.54,
        wspace=0.14,
    )
    palette = {
        "equity": "#1565C0",
        "peak": "#455A64",
        "drawdown": "#EF5350",
        "initial": "#2E7D32",
    }
    for axis, timeframe in zip(axes.flat, TIMEFRAME_ORDER, strict=True):
        curve = curves.loc[curves["timeframe"] == timeframe].copy()
        metric = performance.loc[performance["timeframe"] == timeframe].iloc[0]
        initial_capital = float(curve["initial_capital_usd"].iloc[0])
        sequence = np.concatenate(([0.0], curve["observed_sequence"].to_numpy(dtype=float)))
        equity = np.concatenate(([initial_capital], curve["equity_usd"].to_numpy(dtype=float)))
        peaks = np.maximum.accumulate(equity)

        axis.plot(sequence, equity, color=palette["equity"], linewidth=1.8, label="Equity")
        axis.plot(sequence, peaks, color=palette["peak"], linewidth=0.9, linestyle="--", label="Running peak")
        axis.fill_between(
            sequence,
            equity,
            peaks,
            where=peaks > equity,
            color=palette["drawdown"],
            alpha=0.18,
            interpolate=True,
            label="Drawdown",
        )
        axis.axhline(
            initial_capital,
            color=palette["initial"],
            linewidth=0.8,
            linestyle=":",
        )
        axis.set_title(
            f"{timeframe} | {int(metric['total_trades'])} closed trades | Net {float(metric['net_profit_usd']):+.2f} USD",
            fontsize=10.5,
            fontweight="bold",
        )
        axis.set_xlabel("Observed closed-trade sequence")
        axis.set_ylabel("Reconstructed equity (USD)")
        axis.grid(alpha=0.22)
        axis.text(
            0.015,
            0.04,
            f"Max DD: {float(metric['max_drawdown_usd']):.2f} USD ({float(metric['max_drawdown_pct']):.2f}%)\n"
            f"Recovery: {float(metric['recovery_factor']):.2f}",
            transform=axis.transAxes,
            fontsize=8,
            va="bottom",
            bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "alpha": 0.82, "edgecolor": "#B0BEC5"},
        )
        axis.legend(loc="upper left", fontsize=7, frameon=False, ncol=3)

    figure.suptitle(
        "MAESTRO-X v1.0 - Observed Processed Equity Curves",
        fontsize=16,
        fontweight="bold",
        y=0.985,
    )
    figure.text(
        0.5,
        0.022,
        "Independent timeframe backtests | Initial capital assumption: 40 USD each | Partial observed records, not complete TradingView histories",
        ha="center",
        fontsize=8.5,
        color="#455A64",
    )
    figure.savefig(
        path,
        format="png",
        dpi=180,
        bbox_inches="tight",
        metadata={
            "Title": "MAESTRO-X Observed Processed Equity Curves",
            "Author": "MAESTRO-X Research Framework",
            "Description": "PHASE 3 deterministic observed-subset equity visualization",
            "Software": "matplotlib",
        },
    )
    plt.close(figure)


def _write_figure_atomic(
    path: Path,
    curves: pd.DataFrame,
    performance: pd.DataFrame,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".png",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        _render_equity_figure(curves, performance, temporary)
        os.replace(temporary, path)
    except OSError as exc:
        raise PerformanceEngineError(f"Gagal menulis figure {path}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def run_performance_analysis(
    *,
    processed_dir: Path = PROCESSED_TRADES_DIR,
    metrics_path: Path = TABLES_DIR / "performance_metrics.csv",
    figure_path: Path = FIGURES_DIR / "equity_curve.png",
) -> dict[str, Any]:
    """Execute PHASE 3 only, with deterministic outputs and input hash guards."""

    hashes_before = _processed_hashes(processed_dir)
    trades_path = processed_dir / "normalized_trades.csv"
    rejected_path = processed_dir / "rejected_trades.csv"
    trades = load_processed_trades(trades_path)
    quarantine_counts = _load_quarantine_counts(rejected_path)
    initial_capital = get_initial_capital(load_metadata())
    performance, curves = build_performance_table(
        trades,
        initial_capital=initial_capital,
        quarantine_counts=quarantine_counts,
    )

    validate_dataframe_schema(performance, PERFORMANCE_SCHEMA)
    _write_text_atomic(metrics_path, _csv_text(performance))
    _write_figure_atomic(figure_path, curves, performance)

    hashes_after = _processed_hashes(processed_dir)
    if hashes_before != hashes_after:
        raise PerformanceEngineError(
            "Hash data/processed berubah selama analisis; output tidak dapat dipercaya."
        )
    return {
        "engine_version": ENGINE_VERSION,
        "status": "completed_observed_subset",
        "timeframes": len(performance),
        "processed_rows": len(trades),
        "closed_trades_analyzed": int(performance["total_trades"].sum()),
        "open_trades_excluded": int(performance["open_trades_excluded"].sum()),
        "upstream_quarantined_rows": int(performance["quarantined_rows_upstream"].sum()),
        "tradingview_comparable_timeframes": int(performance["tv_snapshot_comparable"].sum()),
        "metrics_path": metrics_path.as_posix(),
        "figure_path": figure_path.as_posix(),
        "processed_hashes_unchanged": True,
    }
