"""Analisis sensitivitas non-optimizing sesuai dimensi yang tersedia."""

from __future__ import annotations

import math

import pandas as pd


class SensitivityError(ValueError):
    """Input sensitivity engine tidak valid."""


METRIC_COLUMNS = (
    "total_trades",
    "net_return_pct",
    "win_rate_pct",
    "profit_factor",
    "expectancy_usd",
    "max_drawdown_pct",
    "trade_sharpe_approx",
)


def run_sensitivity_analysis(
    performance_metrics: pd.DataFrame,
    normalized_trades: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Bangun sensitivity evidence table tanpa memilih parameter terbaik.

    Timeframe tersedia sebagai label antar-backtest, tetapi periode dan cakupan trade
    tidak dikontrol. Karena itu hasilnya deskriptif, bukan causal parameter sensitivity.
    Dua dimensi lain hanya dianalisis bila tercatat pada data; dataset saat ini tidak
    memilikinya dan harus dinyatakan tidak dapat diestimasi.
    """

    required = {"timeframe", "sample_start", "sample_end", *METRIC_COLUMNS}
    missing = required - set(performance_metrics.columns)
    if missing:
        raise SensitivityError(f"Kolom metrik tidak tersedia: {sorted(missing)}")
    if performance_metrics.empty:
        raise SensitivityError("Performance metrics kosong.")

    rows: list[dict[str, object]] = []
    for metric in performance_metrics.to_dict(orient="records"):
        row: dict[str, object] = {
            "analysis_dimension": "timeframe",
            "parameter_value": metric["timeframe"],
            "status": "descriptive_observed_subset",
            "interpretation": (
                "Perbandingan lintas timeframe tidak terkontrol; periode dan kelengkapan sampel berbeda."
            ),
            "sample_start": metric["sample_start"],
            "sample_end": metric["sample_end"],
        }
        row.update({column: metric[column] for column in METRIC_COLUMNS})
        rows.append(row)

    available_columns = set(normalized_trades.columns) if normalized_trades is not None else set()
    missing_dimensions = (
        (
            "htf_bias_timeframe",
            "HTF bias timeframe tidak tercatat per eksperimen pada trade log.",
        ),
        (
            "minimum_entry_score",
            "Minimum entry score tidak tercatat per eksperimen pada trade log.",
        ),
    )
    for dimension, reason in missing_dimensions:
        if dimension in available_columns and normalized_trades is not None:
            observed = normalized_trades[dimension].dropna().unique()
        else:
            observed = []
        if len(observed) > 1:
            status = "available_but_unimplemented_for_current_design"
            interpretation = (
                f"{dimension} memiliki variasi, tetapi desain eksperimen terkontrol belum dibuktikan."
            )
        else:
            status = "not_estimable"
            interpretation = reason
        blocked_row: dict[str, object] = {
            "analysis_dimension": dimension,
            "parameter_value": "not_available",
            "status": status,
            "interpretation": interpretation,
            "sample_start": None,
            "sample_end": None,
        }
        blocked_row.update({column: math.nan for column in METRIC_COLUMNS})
        rows.append(blocked_row)

    columns = [
        "analysis_dimension",
        "parameter_value",
        "status",
        "interpretation",
        "sample_start",
        "sample_end",
        *METRIC_COLUMNS,
    ]
    return pd.DataFrame(rows, columns=columns)
