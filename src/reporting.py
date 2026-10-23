"""Penulisan laporan reproducible untuk hasil pipeline MAESTRO-X."""

from __future__ import annotations

from datetime import datetime
from html import escape
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping

import numpy as np
import pandas as pd


HTML_STYLE = """
body { font-family: Inter, Arial, sans-serif; margin: 36px auto; max-width: 1180px; color: #172033; line-height: 1.55; padding: 0 24px; }
h1, h2, h3 { color: #111827; }
.banner { border-left: 5px solid #b45309; background: #fff7ed; padding: 14px 18px; margin: 20px 0; }
.ok { border-left-color: #047857; background: #ecfdf5; }
table { border-collapse: collapse; width: 100%; margin: 16px 0 28px; font-size: 13px; }
th, td { border: 1px solid #d5dbe5; padding: 7px 9px; text-align: right; }
th { background: #172033; color: white; }
td:first-child, th:first-child { text-align: left; }
img { max-width: 100%; border: 1px solid #d5dbe5; }
code { background: #f3f4f6; padding: 2px 5px; }
.muted { color: #596579; }
"""


def _write_text_atomic(path: Path, content: str) -> None:
    """Atomically replace a text artifact after its content is complete."""

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
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_canonical_run_report(
    *,
    path: Path,
    run_id: str,
    performance: pd.DataFrame,
    monte_carlo: pd.DataFrame,
    sensitivity: pd.DataFrame,
    phase_summaries: Mapping[str, Mapping[str, Any]],
) -> None:
    """Write the run-scoped synthesis without changing empirical results."""

    performance_required = {
        "timeframe",
        "total_trades",
        "net_return_pct",
        "profit_factor",
        "max_drawdown_pct",
        "publication_eligible",
    }
    monte_required = {
        "timeframe",
        "resampling_method",
        "simulations",
        "probability_negative_return_pct",
        "terminal_return_interval_95_lower_pct",
        "terminal_return_interval_95_upper_pct",
    }
    sensitivity_required = {
        "parameter",
        "evidence_status",
        "optimization_performed",
        "robustness_region",
    }
    for label, frame, required in (
        ("performance", performance, performance_required),
        ("monte_carlo", monte_carlo, monte_required),
        ("sensitivity", sensitivity, sensitivity_required),
    ):
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"Input reporting {label} kehilangan kolom: {missing}.")

    performance_rows = []
    for row in performance.itertuples(index=False):
        performance_rows.append(
            "| {timeframe} | {trades:d} | {return_pct:.3f} | {pf:.3f} | {drawdown:.3f} |".format(
                timeframe=row.timeframe,
                trades=int(row.total_trades),
                return_pct=float(row.net_return_pct),
                pf=float(row.profit_factor),
                drawdown=float(row.max_drawdown_pct),
            )
        )

    bootstrap = monte_carlo.loc[
        monte_carlo["resampling_method"].astype(str).eq("bootstrap")
    ].copy()
    negative_timeframes = performance.loc[
        pd.to_numeric(performance["net_return_pct"], errors="raise") < 0,
        "timeframe",
    ].astype(str).tolist()
    blocked_parameters = sorted(
        sensitivity.loc[
            sensitivity["evidence_status"].astype(str).str.startswith("not_estimable"),
            "parameter",
        ].astype(str).unique()
    )
    optimization_performed = bool(
        sensitivity["optimization_performed"].astype(bool).any()
    )
    robustness_regions = sorted(
        sensitivity["robustness_region"].dropna().astype(str).unique()
    )

    preprocessing_summary = phase_summaries["preprocessing"]
    performance_summary = phase_summaries["performance_engine"]
    monte_summary = phase_summaries["monte_carlo_engine"]
    report = f"""# MAESTRO-X Canonical Research Run

Run ID: `{run_id}`  
Pipeline: `python -m src.run_research_pipeline`  
Scope: validation of observed processed trades; no strategy optimization.

## Execution summary

- Raw rows inspected: {int(preprocessing_summary['raw_rows'])}
- Accepted normalized rows: {int(preprocessing_summary['accepted_rows'])}
- Quarantined rows: {int(preprocessing_summary['rejected_rows'])}
- Closed trades analyzed: {int(performance_summary['closed_trades_analyzed'])}
- Monte Carlo paths: {int(monte_summary['total_simulated_paths'])}
- Monte Carlo seed base: {int(monte_summary.get('base_seed', 20_260_822))}
- Optimization performed: `{str(optimization_performed).lower()}`

## Observed performance

| Timeframe | Closed trades | Net return (%) | Profit factor | Max drawdown (%) |
|---|---:|---:|---:|---:|
{chr(10).join(performance_rows)}

Negative observed timeframe subsets: {', '.join(negative_timeframes) if negative_timeframes else 'none'}.
These are retained without filtering or reinterpretation.

## Monte Carlo robustness

Each timeframe uses {int(bootstrap['simulations'].min()) if not bootstrap.empty else 0:,} IID bootstrap paths in addition to sequence permutations. Bootstrap probability of negative terminal return ranges from {float(bootstrap['probability_negative_return_pct'].min()):.2f}% to {float(bootstrap['probability_negative_return_pct'].max()):.2f}% across observed subsets. These conditional distributions are not forecasts and do not establish future profitability.

## Sensitivity evidence

Blocked parameter dimensions: {', '.join(blocked_parameters) if blocked_parameters else 'none'}.
Declared robustness-region result(s): {', '.join(robustness_regions)}.
Timeframe comparisons remain descriptive because samples differ in period and completeness.

## Validation interpretation

The run is internally reproducible and artifact-isolated. It does not resolve the empirical limitations of incomplete TradingView exports, the initial-capital provenance conflict, absent cost/fill provenance, non-comparable timeframe windows, or missing controlled HTF Bias Timeframe and Minimum Entry Score experiments. Publication claims remain blocked.

## Artifact navigation

- `../tables/performance_metrics.csv`
- `../tables/monte_carlo_statistics.csv`
- `../tables/parameter_sensitivity.csv`
- `monte_carlo_report.html`
- `sensitivity_report.html`
- `../manifest.json`
"""
    _write_text_atomic(path, report)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    return value


def write_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(data), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _table(frame: pd.DataFrame, columns: list[str] | None = None) -> str:
    selected = frame[columns] if columns is not None else frame
    return selected.to_html(index=False, border=0, na_rep="NA", float_format=lambda value: f"{value:.4f}")


def write_monte_carlo_report(
    summary: pd.DataFrame,
    validation_report: dict[str, Any],
    path: Path,
) -> None:
    columns = [
        "timeframe",
        "trade_count",
        "simulations",
        "median_terminal_return_pct",
        "median_max_drawdown_pct",
        "max_drawdown_interval_95_lower_pct",
        "max_drawdown_interval_95_upper_pct",
        "worst_max_drawdown_pct",
        "ruin_probability_pct",
    ]
    html = f"""<!doctype html>
<html lang="id"><head><meta charset="utf-8"><title>MAESTRO-X Monte Carlo Report</title><style>{HTML_STYLE}</style></head>
<body>
<h1>MAESTRO-X Monte Carlo Robustness Report</h1>
<div class="banner"><strong>Scope:</strong> observed normalized rows only. Publication claim gate: <code>{escape(str(validation_report['publication_claim_gate']))}</code>. Hasil ini tidak menggunakan log trade lengkap.</div>
<h2>Metode</h2>
<p>Untuk setiap timeframe, PnL realized diacak melalui 10.000 permutasi tanpa replacement dengan seed tetap. Setiap path dimulai dari USD 40. Ruin didefinisikan sebagai equity menyentuh atau melewati 50% initial capital (USD 20).</p>
<p>Karena setiap permutasi memakai himpunan trade yang sama, terminal return bersifat invariant dan interval persentil terminal return degenerat. Analisis utama adalah distribusi maximum drawdown serta frekuensi pelanggaran ruin threshold. Interval yang dilaporkan adalah interval simulasi, bukan confidence interval inferensial.</p>
<h2>Ringkasan hasil</h2>
{_table(summary, columns)}
<h2>Distribusi drawdown</h2>
<img src="../figures/monte_carlo_drawdown_distribution.png" alt="Distribusi Monte Carlo maximum drawdown">
<h2>Batas interpretasi</h2>
<ul>
<li>Permutasi hanya menguji sequence risk; metode ini tidak memodelkan regime shift, perubahan distribusi, spread, slippage, atau trade yang hilang.</li>
<li>Probabilitas ruin 0% berarti tidak ada pelanggaran dalam 10.000 path subset ini, bukan bukti probabilitas ruin populasi sama dengan nol.</li>
<li>Hasil antar-timeframe tidak sebanding secara kausal karena periode dan kelengkapan sampel berbeda.</li>
</ul>
</body></html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def write_methodology_notes(
    validation_report: dict[str, Any],
    path: Path,
) -> None:
    dataset_lines = []
    for dataset in validation_report["datasets"]:
        dataset_lines.append(
            f"| {dataset['timeframe']} | {dataset['raw_shape']['rows']} | {dataset['selected_rows']} | "
            f"{dataset['observed_closed_trades']} | {dataset['missing_trade_id_count_within_range']} | "
            f"{len(dataset['detected_segments'])} |"
        )
    content = """# Metodologi Validasi MAESTRO-X v1.0

## Tujuan dan unit analisis

Framework ini memvalidasi trade log yang tersedia tanpa mengubah Pine Script, aturan entry/exit, risk logic, parameter, atau asumsi backtest asli. Unit analisis adalah baris closed trade yang benar-benar terobservasi setelah normalisasi.

## Audit dan normalisasi data

CSV memakai header kelas DOM ter-minifikasi, sehingga schema dipetakan berdasarkan posisi dan pola nilai. File append kumulatif dipisahkan saat trade ID berhenti menurun; blok terakhir diasumsikan sebagai timeframe yang tercantum pada nama file. Asumsi ini dicatat dan tidak menghapus blok lain dari raw data.

| Timeframe | Raw rows | Selected rows | Closed observed | Missing ID dalam rentang | Segments |
|---|---:|---:|---:|---:|---:|
""" + "\n".join(dataset_lines) + """

Timestamp diparse sebagai datetime naive karena timezone chart tidak tersedia. Posisi `Open` dikeluarkan dari realized performance dan Monte Carlo. Unicode minus, separator ribuan, persen, dan suffix K/M/B dinormalisasi. File raw tidak ditulis ulang.

## Performance engine

- Total trades: jumlah closed trade dengan Net PnL valid.
- Win rate: jumlah PnL positif dibagi seluruh closed trade, termasuk breakeven pada denominator.
- Profit factor: gross positive PnL dibagi absolut gross negative PnL.
- Expectancy: mean Net PnL per observed trade.
- Equity: initial capital + cumulative observed Net PnL, diurutkan berdasarkan exit timestamp.
- Maximum drawdown: selisih maksimum running peak terhadap equity rekonstruksi; persentase memakai running peak sebagai denominator.
- Sharpe approximation: mean trade PnL / sample standard deviation trade PnL x sqrt(N). Ini bukan annualized calendar Sharpe dan tidak mengurangi risk-free rate.

Raw cumulative PnL tidak dipakai untuk mengisi trade yang hilang. Equity curve adalah rekonstruksi subset, bukan equity curve penuh TradingView.

## Monte Carlo

Setiap timeframe menjalankan 10.000 permutasi sequence tanpa replacement menggunakan seed deterministik. Terminal return harus invariant. Distribusi path dipakai untuk median, interval persentil 95%, worst observed maximum drawdown, dan ruin probability. Ruin threshold ditetapkan pada 50% initial capital; threshold ini adalah definisi operasional penelitian, bukan default dari Pine Script.

## Sensitivitas

Timeframe dibandingkan secara deskriptif. Perbandingan tersebut bukan eksperimen terkontrol karena sample period dan coverage tidak sama. HTF bias timeframe serta minimum entry score tidak tercatat pada data, sehingga keduanya berstatus `not_estimable`. Tidak dilakukan optimasi, pemilihan parameter terbaik, atau modifikasi strategi.

## Validitas dan keterbatasan

- Exchange-qualified symbol, timezone, chart range, feed snapshot, dan seluruh Strategy Properties tidak tersedia di CSV.
- Komisi tercatat nol hanya pada sebagian schema; slippage berstatus tidak dikonfigurasi pada metadata. Biaya tidak diimputasi.
- Beberapa trade ID hilang dan beberapa file berisi blok timeframe lain.
- Tidak ada out-of-sample split, walk-forward result, cross-feed test, atau controlled parameter grid.
- Tidak ada uji signifikansi inferensial karena mekanisme sampling dan log lengkap belum tersedia.
- Hasil tidak menjamin profitabilitas atau performa masa depan.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_robustness_discussion(
    performance: pd.DataFrame,
    monte_carlo: pd.DataFrame,
    path: Path,
) -> None:
    negative = performance.loc[performance["net_profit_usd"] < 0, ["timeframe", "net_profit_usd"]]
    negative_text = (
        ", ".join(f"{row.timeframe} ({row.net_profit_usd:+.3f} USD)" for row in negative.itertuples())
        if not negative.empty
        else "tidak ada pada subset"
    )
    min_pf = float(performance["profit_factor"].min())
    max_pf = float(performance["profit_factor"].max())
    max_worst_dd = float(monte_carlo["worst_max_drawdown_pct"].max())
    content = f"""# Diskusi Robustness MAESTRO-X v1.0

## Temuan yang didukung data

Pada subset terobservasi, profit factor berada pada rentang {min_pf:.3f}-{max_pf:.3f}. Timeframe dengan Net PnL negatif pada subset: {negative_text}. Angka ini tidak menggantikan hasil Strategy Tester penuh karena baris yang hilang tidak direkonstruksi.

Randomisasi sequence menunjukkan worst maximum drawdown tertinggi di antara 10.000 path per timeframe sebesar {max_worst_dd:.3f}%. Semua terminal return invariant, sesuai sifat permutasi tanpa replacement. Tidak ada path yang melewati ruin threshold USD 20, tetapi hasil tersebut terbatas pada magnitudo PnL dan jumlah trade yang terobservasi.

## Yang belum dapat disimpulkan

1. Tidak dapat dinyatakan bahwa satu timeframe lebih unggul secara statistik. Periode, jumlah baris, dan coverage ID berbeda.
2. Tidak dapat dinyatakan bahwa HTF bias atau entry score robust karena kedua parameter tidak ada pada dataset eksperimen.
3. Tidak dapat dinyatakan bahwa biaya eksekusi realistis. Commission/slippage/spread dan broker fill behavior tidak lengkap.
4. Tidak dapat dinyatakan bahwa hasil tahan regime shift, data snooping, atau overfitting tanpa OOS, walk-forward, dan multiple-testing control.
5. Tidak dapat dinyatakan bahwa risiko ruin populasi adalah nol hanya karena simulasi subset tidak menyentuh threshold.

## Implikasi publikasi

Framework dan prosedur analitik siap direproduksi, tetapi evidence empiris belum siap untuk klaim superiority, statistical edge, alokasi modal live, atau rekomendasi perubahan parameter. PDF historis di `docs/` diperlakukan sebagai sumber sekunder dan klaimnya tidak digunakan untuk mengisi kekosongan data primer.

## Data tambahan yang diperlukan

- Ekspor List of Trades lengkap per timeframe dengan trade ID kontigu.
- Snapshot Strategy Properties, exchange feed, timezone, dan exact date range.
- Grid eksperimen terkontrol untuk HTF bias timeframe dan minimum entry score, tanpa memilih hasil secara post hoc.
- OOS/walk-forward split yang dibekukan sebelum analisis.
- Cost-stress scenarios dan cross-broker feed replication.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_research_report(
    performance: pd.DataFrame,
    monte_carlo: pd.DataFrame,
    sensitivity: pd.DataFrame,
    validation_report: dict[str, Any],
    path: Path,
) -> None:
    blocked = sensitivity.loc[sensitivity["status"] == "not_estimable", "analysis_dimension"].tolist()
    performance_columns = [
        "timeframe",
        "total_trades",
        "net_profit_usd",
        "net_return_pct",
        "win_rate_pct",
        "profit_factor",
        "expectancy_usd",
        "max_drawdown_pct",
        "trade_sharpe_approx",
    ]
    monte_carlo_columns = [
        "timeframe",
        "median_max_drawdown_pct",
        "max_drawdown_interval_95_lower_pct",
        "max_drawdown_interval_95_upper_pct",
        "worst_max_drawdown_pct",
        "ruin_probability_pct",
    ]
    html = f"""<!doctype html>
<html lang="id"><head><meta charset="utf-8"><title>MAESTRO-X Research Validation</title><style>{HTML_STYLE}</style></head>
<body>
<h1>MAESTRO-X v1.0 - Quantitative Validation Report</h1>
<div class="banner"><strong>Kesimpulan validitas:</strong> data dapat dianalisis pada scope observed rows, tetapi gate klaim publikasi adalah <code>{escape(str(validation_report['publication_claim_gate']))}</code>. Tidak ada klaim profit guarantee, future performance, atau statistical significance.</div>
<h2>Audit data</h2>
<p>Delapan file tersedia. Sebagian file memuat append lintas-timeframe dan sebagian sequence trade ID tidak lengkap. Normalisasi memakai blok terakhir sesuai nama file; raw data tidak diubah.</p>
<p><a href="data_validation.json">Buka data validation JSON</a> | <a href="methodology.md">Metodologi lengkap</a> | <a href="robustness_discussion.md">Diskusi robustness</a></p>
<h2>Observed performance</h2>
{_table(performance, performance_columns)}
<img src="../figures/performance_overview.png" alt="Observed performance overview">
<h2>Equity reconstruction</h2>
<img src="../figures/equity_curves.png" alt="Observed subset equity curves">
<h2>Monte Carlo sequence risk</h2>
{_table(monte_carlo, monte_carlo_columns)}
<p><a href="monte_carlo_report.html">Buka laporan Monte Carlo lengkap</a></p>
<h2>Sensitivitas</h2>
<p>Timeframe hanya dapat dibandingkan secara deskriptif. Dimensi yang tidak dapat diestimasi: <code>{escape(', '.join(blocked))}</code>.</p>
<img src="../figures/timeframe_sensitivity_heatmap.png" alt="Observed timeframe comparison heatmap">
<h2>Interpretasi terbatas</h2>
<p>Perbedaan antartimeframe dapat berasal dari perubahan sample period, missing trades, market regime, atau konfigurasi yang tidak tercatat. Hasil ini tidak digunakan untuk mengoptimasi parameter maupun mengubah Pine Script.</p>
</body></html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
