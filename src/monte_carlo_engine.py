"""Institutional-style Monte Carlo robustness engine for PHASE 4.

The engine runs two distinct empirical resampling regimes per timeframe:

1. full trade-sequence permutation without replacement; and
2. IID bootstrap resampling with replacement.

Every simulated sequence is converted into an equity path from the documented
initial-capital assumption.  Outputs remain descriptive of the incomplete
observed sample and must not be interpreted as future-profit forecasts.
"""

from __future__ import annotations

from base64 import b64encode
from hashlib import sha256
from html import escape
from io import BytesIO, StringIO
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
    MONTE_CARLO_SIMULATIONS,
    PROCESSED_TRADES_DIR,
    RANDOM_SEED,
    REPORTS_DIR,
    TABLES_DIR,
    TIMEFRAME_SPECS,
    get_initial_capital,
    load_metadata,
)
from .monte_carlo import MonteCarloResult, run_monte_carlo
from .performance_engine import load_processed_trades


LOGGER = logging.getLogger(__name__)
ENGINE_VERSION = "2.0.0"
MINIMUM_SIMULATIONS = 10_000
TIMEFRAME_ORDER = tuple(spec.label for spec in TIMEFRAME_SPECS)
METHOD_ORDER = ("permutation", "bootstrap")

STATISTICS_COLUMNS = (
    "timeframe",
    "resampling_method",
    "method",
    "data_scope",
    "simulations",
    "seed",
    "trade_count",
    "initial_capital_usd",
    "observed_terminal_return_usd",
    "observed_terminal_return_pct",
    "mean_terminal_return_usd",
    "median_terminal_return_usd",
    "mean_terminal_return_pct",
    "median_terminal_return_pct",
    "terminal_return_std_pct",
    "terminal_return_p01_pct",
    "terminal_return_p02_5_pct",
    "terminal_return_p05_pct",
    "terminal_return_p25_pct",
    "terminal_return_p50_pct",
    "terminal_return_p75_pct",
    "terminal_return_p95_pct",
    "terminal_return_p97_5_pct",
    "terminal_return_p99_pct",
    "terminal_return_interval_95_lower_pct",
    "terminal_return_interval_95_upper_pct",
    "worst_terminal_return_pct",
    "best_terminal_return_pct",
    "negative_return_events",
    "probability_negative_return_pct",
    "mean_max_drawdown_usd",
    "median_max_drawdown_usd",
    "max_drawdown_interval_95_lower_usd",
    "max_drawdown_interval_95_upper_usd",
    "best_max_drawdown_usd",
    "worst_max_drawdown_usd",
    "mean_max_drawdown_pct",
    "median_max_drawdown_pct",
    "max_drawdown_p01_pct",
    "max_drawdown_p02_5_pct",
    "max_drawdown_p05_pct",
    "max_drawdown_p25_pct",
    "max_drawdown_p50_pct",
    "max_drawdown_p75_pct",
    "max_drawdown_p95_pct",
    "max_drawdown_p97_5_pct",
    "max_drawdown_p99_pct",
    "max_drawdown_interval_95_lower_pct",
    "max_drawdown_interval_95_upper_pct",
    "best_max_drawdown_pct",
    "worst_max_drawdown_pct",
    "mean_minimum_equity_usd",
    "median_minimum_equity_usd",
    "worst_minimum_equity_usd",
    "best_minimum_equity_usd",
    "ruin_definition",
    "ruin_threshold_usd",
    "ruin_events",
    "ruin_probability_pct",
    "terminal_return_invariant_under_permutation",
    "interval_interpretation",
    "probability_resolution_pct",
    "confidence_interval_label",
    "initial_capital_provenance_status",
    "source_completeness_status",
    "future_profitability_claimed",
)


class InstitutionalMonteCarloError(RuntimeError):
    """Controlled failure in simulation, validation, reporting, or I/O."""


def _hash_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise InstitutionalMonteCarloError(f"Gagal hashing {path}: {exc}") from exc
    return digest.hexdigest()


def _directory_hashes(directory: Path) -> dict[str, str]:
    paths = sorted(path for path in directory.glob("*") if path.is_file())
    if not paths:
        raise InstitutionalMonteCarloError(f"Tidak ada input di {directory}.")
    return {path.name: _hash_file(path) for path in paths}


def _validate_statistics(table: pd.DataFrame, simulations: int) -> None:
    if len(table) != len(TIMEFRAME_ORDER) * len(METHOD_ORDER):
        raise InstitutionalMonteCarloError("Jumlah baris statistik Monte Carlo tidak lengkap.")
    if not (table["simulations"] == simulations).all():
        raise InstitutionalMonteCarloError("Jumlah simulasi tidak konsisten.")
    if table[list(STATISTICS_COLUMNS)].isna().any().any():
        missing = table.columns[table.isna().any()].tolist()
        raise InstitutionalMonteCarloError(f"Statistik mengandung nilai kosong: {missing}.")

    return_percentile_columns = [
        "terminal_return_p01_pct",
        "terminal_return_p02_5_pct",
        "terminal_return_p05_pct",
        "terminal_return_p25_pct",
        "terminal_return_p50_pct",
        "terminal_return_p75_pct",
        "terminal_return_p95_pct",
        "terminal_return_p97_5_pct",
        "terminal_return_p99_pct",
    ]
    drawdown_percentile_columns = [
        "max_drawdown_p01_pct",
        "max_drawdown_p02_5_pct",
        "max_drawdown_p05_pct",
        "max_drawdown_p25_pct",
        "max_drawdown_p50_pct",
        "max_drawdown_p75_pct",
        "max_drawdown_p95_pct",
        "max_drawdown_p97_5_pct",
        "max_drawdown_p99_pct",
    ]
    for columns in (return_percentile_columns, drawdown_percentile_columns):
        values = table[columns].to_numpy(dtype=float)
        if not (np.diff(values, axis=1) >= -1e-12).all():
            raise InstitutionalMonteCarloError("Urutan percentile tidak monoton.")
    probabilities = table[["probability_negative_return_pct", "ruin_probability_pct"]]
    if not ((probabilities >= 0) & (probabilities <= 100)).all().all():
        raise InstitutionalMonteCarloError("Probabilitas simulasi berada di luar [0, 100].")

    permutation = table.loc[table["resampling_method"] == "permutation"]
    if not permutation["terminal_return_invariant_under_permutation"].all():
        raise InstitutionalMonteCarloError("Terminal return permutation tidak invariant.")
    if not np.allclose(
        permutation["median_terminal_return_pct"],
        permutation["observed_terminal_return_pct"],
        atol=1e-10,
    ):
        raise InstitutionalMonteCarloError("Permutation tidak terekonsiliasi dengan observed return.")


def run_simulations(
    trades: pd.DataFrame,
    *,
    simulations: int = MONTE_CARLO_SIMULATIONS,
    initial_capital: float,
    base_seed: int = RANDOM_SEED,
) -> tuple[pd.DataFrame, dict[tuple[str, str], MonteCarloResult]]:
    """Run permutation and bootstrap simulations for every configured timeframe."""

    if not isinstance(simulations, int) or simulations < MINIMUM_SIMULATIONS:
        raise InstitutionalMonteCarloError(
            f"Institutional run memerlukan minimal {MINIMUM_SIMULATIONS} simulasi per metode/timeframe."
        )
    if initial_capital <= 0:
        raise InstitutionalMonteCarloError("Initial capital harus positif.")

    rows: list[dict[str, object]] = []
    results: dict[tuple[str, str], MonteCarloResult] = {}
    for timeframe_index, timeframe in enumerate(TIMEFRAME_ORDER):
        closed = trades.loc[
            (trades["timeframe"] == timeframe) & trades["is_closed"]
        ].copy()
        if len(closed) < 2:
            raise InstitutionalMonteCarloError(
                f"Timeframe {timeframe} tidak memiliki minimal dua closed trade."
            )
        for method_index, method in enumerate(METHOD_ORDER):
            seed = base_seed + timeframe_index * len(METHOD_ORDER) + method_index
            result = run_monte_carlo(
                closed,
                simulations=simulations,
                initial_capital=initial_capital,
                seed=seed,
                method=method,  # type: ignore[arg-type]
            )
            result.summary.update(
                {
                    "confidence_interval_label": (
                        "95% simulation percentile interval (degenerate terminal return)"
                        if method == "permutation"
                        else "95% nonparametric bootstrap percentile interval"
                    ),
                    "initial_capital_provenance_status": (
                        "supported_by_export_but_conflicts_with_pine_declaration"
                    ),
                    "source_completeness_status": "partial_observed_target_segment",
                    "future_profitability_claimed": False,
                }
            )
            results[(timeframe, method)] = result
            rows.append(result.summary)
            LOGGER.info(
                "%s %s: %d simulations, median return %.3f%%, median max DD %.3f%%",
                timeframe,
                method,
                simulations,
                float(result.summary["median_terminal_return_pct"]),
                float(result.summary["median_max_drawdown_pct"]),
            )

    table = pd.DataFrame(rows).loc[:, STATISTICS_COLUMNS]
    _validate_statistics(table, simulations)
    return table, results


def _render_distribution_figure(
    results: Mapping[tuple[str, str], MonteCarloResult],
) -> bytes:
    """Render bootstrap terminal-return and both-method drawdown distributions."""

    figure, axes = plt.subplots(4, 4, figsize=(18, 14), constrained_layout=False)
    figure.subplots_adjust(
        left=0.055,
        right=0.985,
        bottom=0.075,
        top=0.935,
        hspace=0.43,
        wspace=0.30,
    )
    colors = {
        "bootstrap": "#1565C0",
        "permutation": "#EF6C00",
        "observed": "#263238",
        "interval": "#90CAF9",
    }

    for index, timeframe in enumerate(TIMEFRAME_ORDER):
        row = index // 2
        column = (index % 2) * 2
        return_axis = axes[row, column]
        drawdown_axis = axes[row, column + 1]
        bootstrap = results[(timeframe, "bootstrap")]
        permutation = results[(timeframe, "permutation")]
        bootstrap_summary = bootstrap.summary

        returns = bootstrap.terminal_return_pct
        return_axis.hist(
            returns,
            bins=38,
            density=True,
            color=colors["bootstrap"],
            alpha=0.82,
            edgecolor="white",
            linewidth=0.35,
        )
        lower = float(bootstrap_summary["terminal_return_interval_95_lower_pct"])
        upper = float(bootstrap_summary["terminal_return_interval_95_upper_pct"])
        observed = float(bootstrap_summary["observed_terminal_return_pct"])
        median = float(bootstrap_summary["median_terminal_return_pct"])
        return_axis.axvspan(lower, upper, color=colors["interval"], alpha=0.18)
        return_axis.axvline(
            observed,
            color=colors["observed"],
            linewidth=1.4,
            linestyle="--",
            label=f"Observed {observed:.1f}%",
        )
        return_axis.axvline(
            median,
            color="#0D47A1",
            linewidth=1.2,
            label=f"Median {median:.1f}%",
        )
        return_axis.axvline(0.0, color="#B71C1C", linewidth=0.8, alpha=0.65)
        return_axis.set_title(f"{timeframe} - Bootstrap terminal return", fontweight="bold", fontsize=9.5)
        return_axis.set_xlabel("Terminal return (%)")
        return_axis.set_ylabel("Density")
        return_axis.grid(axis="y", alpha=0.18)
        return_axis.legend(fontsize=6.8, frameon=False)

        drawdown_axis.hist(
            permutation.max_drawdown_pct,
            bins=36,
            density=True,
            color=colors["permutation"],
            alpha=0.55,
            label="Permutation",
        )
        drawdown_axis.hist(
            bootstrap.max_drawdown_pct,
            bins=36,
            density=True,
            color=colors["bootstrap"],
            alpha=0.48,
            label="Bootstrap",
        )
        drawdown_axis.axvline(
            float(permutation.summary["max_drawdown_p95_pct"]),
            color="#E65100",
            linewidth=1.0,
            linestyle="--",
            label="Permutation P95",
        )
        drawdown_axis.axvline(
            float(bootstrap.summary["max_drawdown_p95_pct"]),
            color="#0D47A1",
            linewidth=1.0,
            linestyle="--",
            label="Bootstrap P95",
        )
        drawdown_axis.set_title(f"{timeframe} - Maximum drawdown", fontweight="bold", fontsize=9.5)
        drawdown_axis.set_xlabel("Maximum drawdown (%)")
        drawdown_axis.set_ylabel("Density")
        drawdown_axis.grid(axis="y", alpha=0.18)
        drawdown_axis.legend(fontsize=6.5, frameon=False, ncol=2)

    figure.suptitle(
        "MAESTRO-X v1.0 - Monte Carlo Robustness Distributions",
        fontsize=16,
        fontweight="bold",
        y=0.982,
    )
    figure.text(
        0.5,
        0.022,
        "10,000 simulations per method/timeframe | Observed closed trades only | Bootstrap assumes IID trades | Not a forecast",
        ha="center",
        fontsize=9,
        color="#455A64",
    )
    buffer = BytesIO()
    figure.savefig(
        buffer,
        format="png",
        dpi=175,
        bbox_inches="tight",
        metadata={
            "Title": "MAESTRO-X Monte Carlo Robustness Distributions",
            "Author": "MAESTRO-X Research Framework",
            "Description": "PHASE 4 deterministic permutation and bootstrap distributions",
            "Software": "matplotlib",
        },
    )
    plt.close(figure)
    return buffer.getvalue()


def _comparison_table(statistics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for timeframe in TIMEFRAME_ORDER:
        permutation = statistics.loc[
            (statistics["timeframe"] == timeframe)
            & (statistics["resampling_method"] == "permutation")
        ].iloc[0]
        bootstrap = statistics.loc[
            (statistics["timeframe"] == timeframe)
            & (statistics["resampling_method"] == "bootstrap")
        ].iloc[0]
        rows.append(
            {
                "TF": timeframe,
                "Trades": int(bootstrap["trade_count"]),
                "Observed return (%)": float(bootstrap["observed_terminal_return_pct"]),
                "Bootstrap mean (%)": float(bootstrap["mean_terminal_return_pct"]),
                "Bootstrap median (%)": float(bootstrap["median_terminal_return_pct"]),
                "Bootstrap 95% interval (%)": (
                    f"[{float(bootstrap['terminal_return_interval_95_lower_pct']):.2f}, "
                    f"{float(bootstrap['terminal_return_interval_95_upper_pct']):.2f}]"
                ),
                "P(negative) bootstrap (%)": float(bootstrap["probability_negative_return_pct"]),
                "Permutation median max DD (%)": float(permutation["median_max_drawdown_pct"]),
                "Permutation P95 max DD (%)": float(permutation["max_drawdown_p95_pct"]),
                "Bootstrap median max DD (%)": float(bootstrap["median_max_drawdown_pct"]),
                "Bootstrap P95 max DD (%)": float(bootstrap["max_drawdown_p95_pct"]),
            }
        )
    return pd.DataFrame(rows)


def _html_table(frame: pd.DataFrame) -> str:
    return frame.to_html(
        index=False,
        border=0,
        classes="data-table",
        na_rep="NA",
        float_format=lambda value: f"{value:.4f}",
        escape=True,
    )


def _build_html_report(
    statistics: pd.DataFrame,
    figure_bytes: bytes,
    *,
    simulations: int,
    initial_capital: float,
    processed_rows: int,
    closed_rows: int,
    open_rows: int,
    quarantined_rows: int,
    analysis_fingerprint: str,
) -> str:
    comparison = _comparison_table(statistics)
    compact_columns = [
        "timeframe",
        "resampling_method",
        "simulations",
        "trade_count",
        "mean_terminal_return_pct",
        "median_terminal_return_pct",
        "terminal_return_interval_95_lower_pct",
        "terminal_return_interval_95_upper_pct",
        "worst_terminal_return_pct",
        "best_terminal_return_pct",
        "probability_negative_return_pct",
        "median_max_drawdown_pct",
        "max_drawdown_p95_pct",
        "worst_max_drawdown_pct",
    ]
    embedded_figure = b64encode(figure_bytes).decode("ascii")
    bootstrap = statistics.loc[statistics["resampling_method"] == "bootstrap"]
    intervals_cross_zero = int(
        (
            (bootstrap["terminal_return_interval_95_lower_pct"] <= 0)
            & (bootstrap["terminal_return_interval_95_upper_pct"] >= 0)
        ).sum()
    )
    style = """
:root { --ink:#172033; --muted:#5d687b; --line:#d9e0ea; --blue:#1459a6; --navy:#0f2747; --soft:#f4f7fb; --warn:#fff4dd; }
* { box-sizing:border-box; }
body { margin:0; color:var(--ink); background:#eef2f7; font-family:Inter,Segoe UI,Arial,sans-serif; line-height:1.55; }
.page { width:min(1220px,calc(100% - 32px)); margin:28px auto; background:white; box-shadow:0 12px 38px rgba(23,32,51,.09); }
header { padding:42px 48px 34px; color:white; background:linear-gradient(135deg,#0f2747,#1459a6); }
header h1 { margin:0 0 8px; font-size:30px; letter-spacing:.2px; }
header p { margin:0; color:#dce9fa; max-width:900px; }
main { padding:34px 48px 48px; }
h2 { margin:34px 0 12px; color:var(--navy); border-bottom:2px solid #e7edf5; padding-bottom:7px; }
h3 { color:var(--navy); margin-top:24px; }
.alert { padding:15px 18px; border-left:5px solid #d08800; background:var(--warn); margin:0 0 26px; }
.cards { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; margin:20px 0 28px; }
.card { border:1px solid var(--line); border-radius:8px; padding:15px; background:var(--soft); }
.card strong { display:block; color:var(--blue); font-size:23px; }
.card span { color:var(--muted); font-size:12px; }
.table-wrap { width:100%; overflow-x:auto; border:1px solid var(--line); border-radius:7px; }
.data-table { width:100%; border-collapse:collapse; font-size:12px; white-space:nowrap; }
.data-table th { position:sticky; top:0; background:var(--navy); color:white; text-align:right; padding:10px 9px; }
.data-table th:first-child,.data-table td:first-child { text-align:left; }
.data-table td { padding:8px 9px; text-align:right; border-bottom:1px solid #e9edf3; }
.data-table tr:nth-child(even) td { background:#f8fafc; }
.figure { width:100%; display:block; border:1px solid var(--line); border-radius:8px; margin:16px 0; }
code { background:#eef2f7; padding:2px 5px; border-radius:4px; }
ul,ol { padding-left:22px; }
.method { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }
.method article { border:1px solid var(--line); border-radius:8px; padding:16px; }
.method h3 { margin-top:0; }
.meta { color:var(--muted); font-size:12px; overflow-wrap:anywhere; }
footer { padding:20px 48px; background:#f4f7fb; color:var(--muted); font-size:12px; border-top:1px solid var(--line); }
@media (max-width:900px) { .cards { grid-template-columns:repeat(2,1fr); } .method { grid-template-columns:1fr; } header,main { padding-left:24px; padding-right:24px; } }
@media print { body { background:white; } .page { width:100%; margin:0; box-shadow:none; } .data-table { font-size:9px; } }
"""
    return f"""<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>MAESTRO-X Monte Carlo Robustness Report</title>
<style>{style}</style>
</head>
<body><div class="page">
<header>
  <h1>MAESTRO-X v1.0 - Monte Carlo Robustness Report</h1>
  <p>Permutation sequence risk, IID bootstrap resampling, dan rekonstruksi equity path untuk closed trade yang benar-benar terobservasi.</p>
</header>
<main>
<div class="alert"><strong>Batas interpretasi:</strong> hasil ini mengukur sensitivitas terhadap resampling empirical subset yang tidak lengkap. Hasil tidak memprediksi pasar, tidak membuktikan statistical edge, dan tidak menjamin profitabilitas masa depan.</div>

<section class="cards">
  <div class="card"><strong>{simulations:,}</strong><span>simulasi per metode/timeframe</span></div>
  <div class="card"><strong>{simulations * 16:,}</strong><span>total simulated paths</span></div>
  <div class="card"><strong>{closed_rows}</strong><span>closed trade teranalisis</span></div>
  <div class="card"><strong>{open_rows}</strong><span>open trade dikeluarkan</span></div>
  <div class="card"><strong>{intervals_cross_zero}/8</strong><span>bootstrap interval melintasi nol</span></div>
</section>

<h2>Ringkasan lintas timeframe</h2>
<p>Setiap timeframe adalah eksperimen backtest independen dengan initial capital {initial_capital:.2f} USD. Tidak dilakukan pooling atau pembentukan portofolio lintas-timeframe.</p>
<div class="table-wrap">{_html_table(comparison)}</div>

<h2>Metodologi</h2>
<div class="method">
  <article><h3>1. Trade sequence permutation</h3><p>Setiap path memakai seluruh N trade tepat satu kali tanpa replacement, tetapi urutannya diacak. Total PnL dan terminal return invariant; distribusi yang informatif adalah maximum drawdown, minimum equity, dan path-order risk.</p></article>
  <article><h3>2. IID bootstrap resampling</h3><p>Setiap path mengambil N trade dari empirical PnL dengan replacement. Metode ini menghasilkan variasi komposisi, terminal return, dan drawdown. Percentile interval 95% adalah bootstrap interval conditional pada sample terobservasi dan asumsi IID.</p></article>
  <article><h3>3. Equity curve simulation</h3><p>Untuk setiap sequence, equity dihitung sebagai initial capital ditambah cumulative PnL. Running peak menghasilkan maximum drawdown USD/persen; terminal equity menghasilkan return dan event return negatif.</p></article>
</div>

<h3>Definisi statistik</h3>
<ul>
  <li><strong>Median/mean return:</strong> pusat distribusi terminal return dari seluruh simulated paths.</li>
  <li><strong>Percentile distribution:</strong> P1, P2.5, P5, P25, P50, P75, P95, P97.5, dan P99.</li>
  <li><strong>Worst/best case:</strong> minimum/maksimum hasil yang muncul di {simulations:,} simulasi; bukan batas populasi.</li>
  <li><strong>Maximum drawdown:</strong> penurunan terbesar dari running peak pada masing-masing path.</li>
  <li><strong>Probability of negative return:</strong> proporsi path dengan terminal PnL di bawah nol; resolusi simulasi {100/simulations:.4f}%.</li>
  <li><strong>Confidence interval:</strong> interval percentile 2.5%-97.5%. Untuk permutation, return interval degenerat secara matematis. Untuk bootstrap, istilah yang lebih tepat adalah nonparametric bootstrap percentile interval.</li>
</ul>

<h2>Distribusi simulasi</h2>
<img class="figure" src="data:image/png;base64,{embedded_figure}" alt="Monte Carlo terminal return and maximum drawdown distributions for eight timeframes">

<h2>Statistik per metode</h2>
<div class="table-wrap">{_html_table(statistics.loc[:, compact_columns])}</div>

<h2>Asumsi dan keterbatasan</h2>
<ol>
  <li>Input berisi {processed_rows} processed rows: {closed_rows} closed, {open_rows} open, dan {quarantined_rows} malformed rows dikarantina upstream.</li>
  <li>Trade-ID dan periode sumber tidak lengkap; simulated paths tidak mengisi observasi yang hilang.</li>
  <li>IID bootstrap mengabaikan serial dependence, volatility clustering, regime shifts, dan perubahan distribusi.</li>
  <li>Permutation mengubah urutan tetapi tidak mengubah frekuensi atau magnitudo PnL observed.</li>
  <li>Commission/slippage/feed/timezone tidak lengkap. Tidak ada cost stress tambahan.</li>
  <li>Initial capital 40 USD didukung export, tetapi masih berkonflik dengan deklarasi Pine 10,000.</li>
  <li>Interval simulasi bukan confidence statement tentang profitabilitas masa depan dan tidak boleh dipakai sebagai jaminan return.</li>
</ol>

<p class="meta"><strong>Reproducibility fingerprint:</strong> <code>{escape(analysis_fingerprint)}</code><br>
Engine version: {ENGINE_VERSION}; base seed: {RANDOM_SEED}; wall-clock timestamp intentionally omitted.</p>
</main>
<footer>MAESTRO-X Research Framework - PHASE 4 - Observed subset robustness only</footer>
</div></body></html>
"""


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


def _write_atomic(path: Path, payload: bytes) -> None:
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
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    except OSError as exc:
        raise InstitutionalMonteCarloError(f"Gagal menulis output {path}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def run_institutional_monte_carlo(
    *,
    processed_dir: Path = PROCESSED_TRADES_DIR,
    report_path: Path = REPORTS_DIR / "monte_carlo_report.html",
    statistics_path: Path = TABLES_DIR / "monte_carlo_statistics.csv",
    figure_path: Path = FIGURES_DIR / "monte_carlo_distribution.png",
    simulations: int = MONTE_CARLO_SIMULATIONS,
    base_seed: int = RANDOM_SEED,
) -> dict[str, Any]:
    """Execute PHASE 4 with deterministic outputs and processed-input hash guards."""

    input_hashes_before = _directory_hashes(processed_dir)
    trades = load_processed_trades(processed_dir / "normalized_trades.csv")
    closed_rows = int(trades["is_closed"].sum())
    open_rows = int((~trades["is_closed"]).sum())
    rejected_path = processed_dir / "rejected_trades.csv"
    quarantined_rows = len(pd.read_csv(rejected_path)) if rejected_path.is_file() else 0
    initial_capital = get_initial_capital(load_metadata())

    statistics, results = run_simulations(
        trades,
        simulations=simulations,
        initial_capital=initial_capital,
        base_seed=base_seed,
    )
    figure_bytes = _render_distribution_figure(results)
    fingerprint_source = "|".join(
        [
            ENGINE_VERSION,
            str(simulations),
            str(base_seed),
            *(f"{name}:{digest}" for name, digest in sorted(input_hashes_before.items())),
        ]
    )
    fingerprint = sha256(fingerprint_source.encode("utf-8")).hexdigest()
    report_html = _build_html_report(
        statistics,
        figure_bytes,
        simulations=simulations,
        initial_capital=initial_capital,
        processed_rows=len(trades),
        closed_rows=closed_rows,
        open_rows=open_rows,
        quarantined_rows=quarantined_rows,
        analysis_fingerprint=fingerprint,
    )

    _write_atomic(statistics_path, _csv_text(statistics).encode("utf-8"))
    _write_atomic(figure_path, figure_bytes)
    _write_atomic(report_path, report_html.encode("utf-8"))

    input_hashes_after = _directory_hashes(processed_dir)
    if input_hashes_before != input_hashes_after:
        raise InstitutionalMonteCarloError(
            "Hash data/processed berubah selama Monte Carlo; hasil tidak dapat dipercaya."
        )
    bootstrap = statistics.loc[statistics["resampling_method"] == "bootstrap"]
    return {
        "engine_version": ENGINE_VERSION,
        "status": "completed_observed_subset_no_future_claim",
        "timeframes": len(TIMEFRAME_ORDER),
        "methods": len(METHOD_ORDER),
        "simulations_per_method_timeframe": simulations,
        "total_simulated_paths": simulations * len(TIMEFRAME_ORDER) * len(METHOD_ORDER),
        "closed_trades_used": closed_rows,
        "open_trades_excluded": open_rows,
        "upstream_quarantined_rows": quarantined_rows,
        "bootstrap_negative_return_probability_min_pct": float(
            bootstrap["probability_negative_return_pct"].min()
        ),
        "bootstrap_negative_return_probability_max_pct": float(
            bootstrap["probability_negative_return_pct"].max()
        ),
        "processed_hashes_unchanged": True,
        "future_profitability_claimed": False,
        "report_path": report_path.as_posix(),
        "statistics_path": statistics_path.as_posix(),
        "figure_path": figure_path.as_posix(),
        "analysis_fingerprint": fingerprint,
    }
