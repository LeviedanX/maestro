"""Deterministic Monte Carlo resampling and equity-path reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal, Sequence

import numpy as np
import pandas as pd

from .config import MONTE_CARLO_SIMULATIONS, RANDOM_SEED, RUIN_EQUITY_FRACTION


class MonteCarloError(ValueError):
    """Konfigurasi atau input Monte Carlo tidak valid."""


@dataclass
class MonteCarloResult:
    """Ringkasan dan distribusi path-risk untuk satu timeframe."""

    summary: dict[str, object]
    max_drawdown_usd: np.ndarray
    max_drawdown_pct: np.ndarray
    terminal_return_pct: np.ndarray
    terminal_return_usd: np.ndarray
    minimum_equity_usd: np.ndarray


ResamplingMethod = Literal["permutation", "bootstrap"]


def _extract_pnl(trades: pd.DataFrame | Sequence[float] | np.ndarray) -> tuple[np.ndarray, str]:
    if isinstance(trades, pd.DataFrame):
        required = {"net_pnl_usd", "is_closed"}
        missing = required - set(trades.columns)
        if missing:
            raise MonteCarloError(f"Kolom wajib tidak tersedia: {sorted(missing)}")
        closed = trades.loc[trades["is_closed"] & trades["net_pnl_usd"].notna()]
        pnl = closed["net_pnl_usd"].to_numpy(dtype=float, copy=True)
        labels = closed["timeframe"].dropna().unique() if "timeframe" in closed else []
        timeframe = str(labels[0]) if len(labels) == 1 else "unspecified"
    else:
        pnl = np.asarray(trades, dtype=float).copy()
        timeframe = "unspecified"

    if pnl.ndim != 1 or len(pnl) < 2:
        raise MonteCarloError("Monte Carlo memerlukan sedikitnya dua trade realized.")
    if not np.isfinite(pnl).all():
        raise MonteCarloError("Net PnL mengandung nilai non-finite.")
    return pnl, timeframe


def run_monte_carlo(
    trades: pd.DataFrame | Sequence[float] | np.ndarray,
    simulations: int = MONTE_CARLO_SIMULATIONS,
    *,
    initial_capital: float = 40.0,
    seed: int = RANDOM_SEED,
    ruin_equity_fraction: float = RUIN_EQUITY_FRACTION,
    method: ResamplingMethod = "permutation",
) -> MonteCarloResult:
    """Resample a trade sequence and reconstruct every simulated equity path.

    ``permutation`` uses every observed trade exactly once without replacement,
    so terminal return is invariant and only path/order risk changes.
    ``bootstrap`` samples N trades with replacement from N observed trades,
    producing an empirical terminal-return distribution under an IID assumption.
    """

    if not isinstance(simulations, int) or simulations <= 0:
        raise MonteCarloError("simulations harus berupa integer positif.")
    if initial_capital <= 0:
        raise MonteCarloError("initial_capital harus lebih besar dari nol.")
    if not 0 < ruin_equity_fraction < 1:
        raise MonteCarloError("ruin_equity_fraction harus berada di antara 0 dan 1.")
    if method not in ("permutation", "bootstrap"):
        raise MonteCarloError(f"Metode resampling tidak didukung: {method!r}.")

    pnl, timeframe = _extract_pnl(trades)
    original = pnl.copy()
    rng = np.random.default_rng(seed)
    if method == "permutation":
        paths = np.empty((simulations, len(pnl)), dtype=float)
        for simulation in range(simulations):
            paths[simulation] = rng.permutation(pnl)
        method_label = "trade_sequence_permutation_without_replacement"
        interval_interpretation = (
            "simulation_percentile_interval; terminal-return interval is degenerate by construction"
        )
    else:
        sampled_indices = rng.integers(
            0,
            len(pnl),
            size=(simulations, len(pnl)),
            endpoint=False,
        )
        paths = pnl[sampled_indices]
        method_label = "iid_trade_bootstrap_with_replacement"
        interval_interpretation = (
            "nonparametric bootstrap percentile interval conditional on observed IID trades"
        )
    if not np.array_equal(pnl, original):
        raise AssertionError("Input PnL termutasi selama simulasi.")

    equity_paths = initial_capital + np.cumsum(paths, axis=1)
    initial_column = np.full((simulations, 1), initial_capital, dtype=float)
    running_peaks = np.maximum.accumulate(np.concatenate((initial_column, equity_paths), axis=1), axis=1)[
        :, 1:
    ]
    drawdowns_usd = running_peaks - equity_paths
    drawdowns_pct = np.divide(
        drawdowns_usd,
        running_peaks,
        out=np.zeros_like(drawdowns_usd),
        where=running_peaks != 0,
    ) * 100
    maximum_drawdown_usd = drawdowns_usd.max(axis=1)
    maximum_drawdown_pct = drawdowns_pct.max(axis=1)
    terminal_return_usd = equity_paths[:, -1] - initial_capital
    terminal_return_pct = terminal_return_usd / initial_capital * 100
    minimum_equity_usd = np.minimum(initial_capital, equity_paths.min(axis=1))
    ruin_threshold = initial_capital * ruin_equity_fraction
    ruin_events = (equity_paths <= ruin_threshold).any(axis=1)
    negative_return_events = terminal_return_usd < 0

    percentile_levels = (1.0, 2.5, 5.0, 25.0, 50.0, 75.0, 95.0, 97.5, 99.0)
    return_percentiles = dict(
        zip(
            percentile_levels,
            np.percentile(terminal_return_pct, percentile_levels),
            strict=True,
        )
    )
    drawdown_usd_percentiles = dict(
        zip(
            percentile_levels,
            np.percentile(maximum_drawdown_usd, percentile_levels),
            strict=True,
        )
    )
    drawdown_pct_percentiles = dict(
        zip(
            percentile_levels,
            np.percentile(maximum_drawdown_pct, percentile_levels),
            strict=True,
        )
    )
    invariant_tolerance = max(1e-12, abs(float(terminal_return_pct[0])) * 1e-12)
    return_is_invariant = bool(np.ptp(terminal_return_pct) <= invariant_tolerance)

    summary: dict[str, object] = {
        "timeframe": timeframe,
        "data_scope": "observed_processed_closed_trades_only",
        "method": method_label,
        "resampling_method": method,
        "simulations": simulations,
        "seed": seed,
        "trade_count": int(len(pnl)),
        "initial_capital_usd": float(initial_capital),
        "observed_terminal_return_usd": float(pnl.sum()),
        "observed_terminal_return_pct": float(pnl.sum() / initial_capital * 100),
        "ruin_definition": f"equity <= {ruin_equity_fraction:.0%} of initial capital",
        "ruin_threshold_usd": float(ruin_threshold),
        "mean_terminal_return_usd": float(np.mean(terminal_return_usd)),
        "median_terminal_return_usd": float(np.median(terminal_return_usd)),
        "mean_terminal_return_pct": float(np.mean(terminal_return_pct)),
        "median_terminal_return_pct": float(np.median(terminal_return_pct)),
        "terminal_return_std_pct": float(np.std(terminal_return_pct, ddof=1)),
        "terminal_return_p01_pct": float(return_percentiles[1.0]),
        "terminal_return_p02_5_pct": float(return_percentiles[2.5]),
        "terminal_return_p05_pct": float(return_percentiles[5.0]),
        "terminal_return_p25_pct": float(return_percentiles[25.0]),
        "terminal_return_p50_pct": float(return_percentiles[50.0]),
        "terminal_return_p75_pct": float(return_percentiles[75.0]),
        "terminal_return_p95_pct": float(return_percentiles[95.0]),
        "terminal_return_p97_5_pct": float(return_percentiles[97.5]),
        "terminal_return_p99_pct": float(return_percentiles[99.0]),
        "terminal_return_interval_95_lower_pct": float(return_percentiles[2.5]),
        "terminal_return_interval_95_upper_pct": float(return_percentiles[97.5]),
        "worst_terminal_return_pct": float(terminal_return_pct.min()),
        "best_terminal_return_pct": float(terminal_return_pct.max()),
        "terminal_return_invariant_under_permutation": return_is_invariant,
        "negative_return_events": int(negative_return_events.sum()),
        "probability_negative_return_pct": float(negative_return_events.mean() * 100),
        "interval_interpretation": interval_interpretation,
        "mean_max_drawdown_usd": float(np.mean(maximum_drawdown_usd)),
        "median_max_drawdown_usd": float(np.median(maximum_drawdown_usd)),
        "max_drawdown_interval_95_lower_usd": float(drawdown_usd_percentiles[2.5]),
        "max_drawdown_interval_95_upper_usd": float(drawdown_usd_percentiles[97.5]),
        "best_max_drawdown_usd": float(maximum_drawdown_usd.min()),
        "worst_max_drawdown_usd": float(maximum_drawdown_usd.max()),
        "mean_max_drawdown_pct": float(np.mean(maximum_drawdown_pct)),
        "median_max_drawdown_pct": float(np.median(maximum_drawdown_pct)),
        "max_drawdown_p01_pct": float(drawdown_pct_percentiles[1.0]),
        "max_drawdown_p02_5_pct": float(drawdown_pct_percentiles[2.5]),
        "max_drawdown_p05_pct": float(drawdown_pct_percentiles[5.0]),
        "max_drawdown_p25_pct": float(drawdown_pct_percentiles[25.0]),
        "max_drawdown_p50_pct": float(drawdown_pct_percentiles[50.0]),
        "max_drawdown_p75_pct": float(drawdown_pct_percentiles[75.0]),
        "max_drawdown_p95_pct": float(drawdown_pct_percentiles[95.0]),
        "max_drawdown_p97_5_pct": float(drawdown_pct_percentiles[97.5]),
        "max_drawdown_p99_pct": float(drawdown_pct_percentiles[99.0]),
        "max_drawdown_interval_95_lower_pct": float(drawdown_pct_percentiles[2.5]),
        "max_drawdown_interval_95_upper_pct": float(drawdown_pct_percentiles[97.5]),
        "best_max_drawdown_pct": float(maximum_drawdown_pct.min()),
        "worst_max_drawdown_pct": float(maximum_drawdown_pct.max()),
        "mean_minimum_equity_usd": float(np.mean(minimum_equity_usd)),
        "median_minimum_equity_usd": float(np.median(minimum_equity_usd)),
        "worst_minimum_equity_usd": float(minimum_equity_usd.min()),
        "best_minimum_equity_usd": float(minimum_equity_usd.max()),
        "ruin_events": int(ruin_events.sum()),
        "ruin_probability_pct": float(ruin_events.mean() * 100),
        "ruin_probability_resolution_pct": float(100 / simulations),
        "probability_resolution_pct": float(100 / simulations),
    }
    if not all(math.isfinite(float(value)) for key, value in summary.items() if isinstance(value, float)):
        raise MonteCarloError("Ringkasan Monte Carlo menghasilkan nilai non-finite.")
    return MonteCarloResult(
        summary=summary,
        max_drawdown_usd=maximum_drawdown_usd,
        max_drawdown_pct=maximum_drawdown_pct,
        terminal_return_pct=terminal_return_pct,
        terminal_return_usd=terminal_return_usd,
        minimum_equity_usd=minimum_equity_usd,
    )


def run_bootstrap_monte_carlo(
    trades: pd.DataFrame | Sequence[float] | np.ndarray,
    simulations: int = MONTE_CARLO_SIMULATIONS,
    *,
    initial_capital: float = 40.0,
    seed: int = RANDOM_SEED,
    ruin_equity_fraction: float = RUIN_EQUITY_FRACTION,
) -> MonteCarloResult:
    """Convenience wrapper for IID bootstrap resampling with replacement."""

    return run_monte_carlo(
        trades,
        simulations=simulations,
        initial_capital=initial_capital,
        seed=seed,
        ruin_equity_fraction=ruin_equity_fraction,
        method="bootstrap",
    )
