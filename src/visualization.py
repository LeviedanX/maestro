"""Visualisasi statis yang reproducible untuk output riset."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .monte_carlo import MonteCarloResult


FIGURE_DPI = 160
TIMEFRAME_ORDER = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1D"]


def _prepare_target(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def plot_sensitivity_heatmap(sensitivity: pd.DataFrame, path: Path) -> None:
    """Render heatmap z-score metrik timeframe dengan anotasi nilai asli."""

    subset = sensitivity.loc[
        (sensitivity["analysis_dimension"] == "timeframe")
        & (sensitivity["status"] == "descriptive_observed_subset")
    ].copy()
    if subset.empty:
        raise ValueError("Tidak ada baris timeframe untuk heatmap sensitivitas.")
    subset["parameter_value"] = pd.Categorical(
        subset["parameter_value"], categories=TIMEFRAME_ORDER, ordered=True
    )
    subset = subset.sort_values("parameter_value")
    metric_columns = [
        "net_return_pct",
        "win_rate_pct",
        "profit_factor",
        "expectancy_usd",
        "max_drawdown_pct",
    ]
    values = subset[metric_columns].to_numpy(dtype=float)
    means = np.nanmean(values, axis=0)
    standard_deviations = np.nanstd(values, axis=0)
    safe_std = np.where(standard_deviations == 0, 1.0, standard_deviations)
    standardized = (values - means) / safe_std

    _prepare_target(path)
    figure, axis = plt.subplots(figsize=(10.5, 6.5))
    figure.subplots_adjust(left=0.09, right=0.91, bottom=0.20, top=0.88)
    image = axis.imshow(standardized, aspect="auto", cmap="coolwarm", vmin=-2.0, vmax=2.0)
    axis.set_xticks(range(len(metric_columns)), [
        "Net return\n(%)",
        "Win rate\n(%)",
        "Profit\nfactor",
        "Expectancy\n(USD)",
        "Max DD\n(%)",
    ])
    axis.set_yticks(range(len(subset)), subset["parameter_value"].astype(str))
    axis.set_xlabel("Metrik terobservasi (warna = z-score per kolom)", labelpad=10)
    axis.set_ylabel("Timeframe label")
    axis.set_title(
        "Observed Timeframe Comparison - Bukan Controlled Parameter Sensitivity",
        fontweight="bold",
    )
    for row in range(values.shape[0]):
        for column in range(values.shape[1]):
            value = values[row, column]
            label = f"{value:.2f}" if np.isfinite(value) else "NA"
            color = "white" if abs(standardized[row, column]) > 1.0 else "black"
            axis.text(column, row, label, ha="center", va="center", color=color, fontsize=8)
    colorbar = figure.colorbar(image, ax=axis, shrink=0.85)
    colorbar.set_label("Z-score deskriptif")
    figure.text(
        0.5,
        0.035,
        "Catatan: periode dan kelengkapan trade berbeda; heatmap tidak digunakan untuk optimasi.",
        ha="center",
        fontsize=8,
        color="#444444",
    )
    figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)


def plot_equity_curves(equity_curves: pd.DataFrame, path: Path) -> None:
    """Render delapan equity curve observed-subset dalam panel terpisah."""

    required = {"timeframe", "observed_sequence", "equity_usd", "initial_capital_usd"}
    missing = required - set(equity_curves.columns)
    if missing:
        raise ValueError(f"Kolom equity curve tidak tersedia: {sorted(missing)}")
    _prepare_target(path)
    figure, axes = plt.subplots(4, 2, figsize=(12, 14), constrained_layout=True)
    for axis, timeframe in zip(axes.flat, TIMEFRAME_ORDER, strict=True):
        data = equity_curves.loc[equity_curves["timeframe"] == timeframe]
        if data.empty:
            axis.set_visible(False)
            continue
        initial_capital = float(data["initial_capital_usd"].iloc[0])
        axis.plot(data["observed_sequence"], data["equity_usd"], color="#087fbd", linewidth=1.7)
        axis.axhline(initial_capital, color="#555555", linestyle="--", linewidth=0.8)
        axis.fill_between(
            data["observed_sequence"].to_numpy(dtype=float),
            initial_capital,
            data["equity_usd"].to_numpy(dtype=float),
            color="#087fbd",
            alpha=0.10,
        )
        axis.set_title(f"{timeframe} - {len(data)} closed trade terobservasi", fontweight="bold")
        axis.set_xlabel("Urutan trade terobservasi")
        axis.set_ylabel("Equity rekonstruksi (USD)")
        axis.grid(alpha=0.25)
    figure.suptitle(
        "MAESTRO-X Observed-Subset Equity Curves\nBukan equity curve log lengkap TradingView",
        fontsize=15,
        fontweight="bold",
    )
    figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)


def plot_performance_overview(performance: pd.DataFrame, path: Path) -> None:
    """Render ringkasan metrik observed-subset tanpa memberi peringkat parameter."""

    required = {
        "timeframe",
        "net_return_pct",
        "profit_factor",
        "win_rate_pct",
        "max_drawdown_pct",
    }
    missing = required - set(performance.columns)
    if missing:
        raise ValueError(f"Kolom performance tidak tersedia: {sorted(missing)}")
    ordered = performance.copy()
    ordered["timeframe"] = pd.Categorical(ordered["timeframe"], TIMEFRAME_ORDER, ordered=True)
    ordered = ordered.sort_values("timeframe")
    labels = ordered["timeframe"].astype(str)
    colors = ["#087fbd" if value >= 0 else "#c53b3b" for value in ordered["net_return_pct"]]

    _prepare_target(path)
    figure, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    panels = (
        ("net_return_pct", "Observed Net Return (%)", colors),
        ("profit_factor", "Profit Factor", "#338f63"),
        ("win_rate_pct", "Win Rate (%)", "#725ac1"),
        ("max_drawdown_pct", "Observed Max Drawdown (%)", "#d17b0f"),
    )
    for axis, (column, title, color) in zip(axes.flat, panels, strict=True):
        bars = axis.bar(labels, ordered[column], color=color)
        axis.set_title(title, fontweight="bold")
        axis.grid(axis="y", alpha=0.25)
        axis.bar_label(bars, fmt="%.2f", padding=2, fontsize=8)
    figure.suptitle(
        "MAESTRO-X Performance - Observed Rows Only",
        fontsize=15,
        fontweight="bold",
    )
    figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)


def plot_monte_carlo_drawdowns(results: dict[str, MonteCarloResult], path: Path) -> None:
    """Render distribusi maximum drawdown dari permutasi sequence trade."""

    _prepare_target(path)
    figure, axes = plt.subplots(4, 2, figsize=(12, 14), constrained_layout=True)
    for axis, timeframe in zip(axes.flat, TIMEFRAME_ORDER, strict=True):
        result = results.get(timeframe)
        if result is None:
            axis.set_visible(False)
            continue
        values = result.max_drawdown_pct
        median = float(np.median(values))
        percentile_95 = float(np.percentile(values, 95))
        axis.hist(values, bins=35, color="#087fbd", alpha=0.82, edgecolor="white", linewidth=0.35)
        axis.axvline(median, color="#1b4332", linewidth=1.4, label=f"Median {median:.2f}%")
        axis.axvline(
            percentile_95,
            color="#c53b3b",
            linestyle="--",
            linewidth=1.2,
            label=f"P95 {percentile_95:.2f}%",
        )
        axis.set_title(f"{timeframe} - 10,000 permutasi", fontweight="bold")
        axis.set_xlabel("Maximum drawdown per path (%)")
        axis.set_ylabel("Frekuensi")
        axis.legend(fontsize=8)
        axis.grid(axis="y", alpha=0.2)
    figure.suptitle(
        "Monte Carlo Sequence Risk - Observed Trade Subsets",
        fontsize=15,
        fontweight="bold",
    )
    figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)


def generate_report_figures() -> None:
    """Compatibility guard; orchestration is owned by the canonical pipeline."""

    raise RuntimeError(
        "Gunakan src.run_research_pipeline agar figur memiliki input tervalidasi dan terisolasi."
    )
