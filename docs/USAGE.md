# MAESTRO-X Research Framework

Framework validasi kuantitatif reproducible untuk trade log MAESTRO-X v1.0 pada XAUUSD. Pine Script di `strategy/MAESTRO-X_v1.0.pine` adalah objek riset immutable. Pipeline tidak mengubah logika strategi, parameter, entry, exit, risk management, atau CSV di `data/raw/trades/`.

## Installation

Gunakan Python 3.13. Buat environment terisolasi dari root proyek:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip check
```

`requirements.txt` berisi exact-pinned runtime dependencies. `requirements-dev.txt` memasukkan runtime dependencies tersebut dan exact-pinned `pytest`. Untuk eksekusi tanpa test tooling, instal hanya `requirements.txt`.

## Execution

Jalankan dari root proyek:

```powershell
python -m src.run_research_pipeline
```

`python -m src.run_research_pipeline` adalah satu-satunya orchestrator resmi. Urutannya:

1. `preprocessing`;
2. `performance_engine`;
3. `monte_carlo_engine`;
4. `sensitivity_engine`;
5. `reporting`;
6. validasi source hash, artifact schema, dan manifest;
7. atomic publication ke direktori run final.

Command lama `python -m src.run_pipeline` hanya compatibility shim. Command tersebut menampilkan deprecation warning dan mendelegasikan ke orchestrator resmi; ia tidak lagi menulis artefak bersama secara diam-diam.

Compatibility verification bila diperlukan:

```powershell
python -m src.run_pipeline
```

Setiap pemanggilan penuh membuat run baru dan tidak menimpa run yang sudah ada. Karena itu, gunakan compatibility command hanya untuk verifikasi eksplisit.

## Isolasi artefak

Setiap eksekusi membuat direktori unik:

```text
results/runs/YYYYMMDD_HHMMSS/
├── figures/
│   ├── equity_curve.png
│   ├── monte_carlo_distribution.png
│   └── parameter_surface.png
├── reports/
│   ├── preprocessing_report.json
│   ├── monte_carlo_report.html
│   ├── sensitivity_report.html
│   └── research_report.md
├── tables/
│   ├── processed/
│   │   ├── normalized_trades.csv
│   │   ├── rejected_trades.csv
│   │   └── trades_<timeframe>.csv
│   ├── processed_dataset_summary.csv
│   ├── performance_metrics.csv
│   ├── monte_carlo_statistics.csv
│   └── parameter_sensitivity.csv
└── manifest.json
```

Pipeline bekerja di staging directory baru dan hanya mempublikasikan seluruh run setelah semua tahap, source-integrity check, serta schema validation lulus. Run ID yang sudah ada ditolak; artefak lama tidak ditimpa atau dicampur.

`manifest.json` menyimpan versi pipeline/schema, versi runtime, parameter simulasi, hash Pine/raw source, phase summaries, dan SHA-256 setiap artefak. Manifest bersifat run-specific; hasil simulasi tetap deterministic untuk input, dependency, seed, dan konfigurasi backend yang sama.

## Project structure

```text
strategy/                 Pine Script immutable
data/raw/trades/          TradingView exports immutable
data/metadata/            provenance dan asumsi dataset
data/processed/           output standalone historis, bukan canonical run
src/
  preprocessing.py       loader, validation, normalization, quarantine
  performance_engine.py  realized metrics dan equity reconstruction
  monte_carlo_engine.py   permutation dan IID bootstrap orchestration
  sensitivity_engine.py   non-optimizing sensitivity evidence
  reporting.py            report rendering
  schema.py               dataset, performance, dan artifact contracts
  run_research_pipeline.py canonical orchestrator
validation/               unit dan integration tests
results/runs/             canonical isolated executions
results/invalid_runs/     quarantined non-canonical QA evidence
results/reports/          audit historis dan laporan validasi framework
tmp/                      ignored derived working files; bukan canonical output
```

CLI engine standalone (`src.run_preprocessing`, `src.run_performance`, `src.run_monte_carlo`, dan `src.run_sensitivity`) dipertahankan untuk diagnosis tahap tertentu. Output default CLI tersebut bukan paket canonical dan tidak boleh dicampur sebagai satu run riset.

## Testing

```powershell
python -m pip install -r requirements-dev.txt
python -m pip check
python -m compileall -q src validation
python -m pytest validation/
python -m src.run_research_pipeline
```

`python -m pytest` direkomendasikan karena mengikat test runner ke interpreter environment aktif dan menghindari launcher absolut yang tertinggal ketika folder virtual environment dipindahkan.

Untuk audit integritas, bandingkan SHA-256 sebelum dan sesudah eksekusi:

```powershell
Get-FileHash strategy/MAESTRO-X_v1.0.pine -Algorithm SHA256
Get-ChildItem data/raw/trades/*.csv | Sort-Object Name | Get-FileHash -Algorithm SHA256
```

Test suite memeriksa schema drift, rekonsiliasi metrik, determinisme Monte Carlo, run isolation, manifest completeness, collision refusal, serta tidak berubahnya Pine/raw source dan shared legacy artifacts.

Path input/output dibangun dari lokasi package, bukan current working directory atau home-directory environment variable. Matplotlib memakai backend non-interaktif `Agg`, dan Monte Carlo memakai fixed seed. Reproducibility byte-level lintas sistem operasi tetap dapat dipengaruhi font/rendering stack. TradingView timestamp memakai singkatan bulan Inggris; environment non-English perlu memverifikasi locale parsing melalui test suite sebelum menjalankan riset.

## Metodologi ringkas

Preprocessing memilih observed target segment berdasarkan reset trade ID dan evidence timeframe, lalu mengarantina row invalid tanpa imputasi. Realized performance hanya memakai closed accepted rows. Monte Carlo menjalankan 10.000 sequence permutations dan 10.000 IID bootstrap samples per timeframe dengan seed dasar `20260822`. Permutation mengukur path-order risk; bootstrap mengukur conditional resampling uncertainty pada empirical trades.

Sensitivity engine tidak mengoptimasi parameter. Timeframe dianalisis secara deskriptif. HTF Bias Timeframe dan Minimum Entry Score tetap `not_estimable` karena tidak ada outcome-tagged controlled experiment grid.

## Batasan empiris

- Lima export berisi cumulative append blocks dan sebagian trade-ID sequence tidak lengkap.
- Sampel closed per timeframe kecil dan periode observasinya tidak comparable.
- Satu posisi 1h berstatus open dan dikeluarkan dari realized metrics.
- Initial capital USD 40 dari metadata/export berkonflik dengan deklarasi Pine USD 10.000.
- Exchange feed, chart timezone, exact date range, commission, slippage, dan fill provenance belum lengkap.
- Closed-trade drawdown mengabaikan intratrade equity path.
- IID bootstrap tidak memodelkan serial dependence atau regime shift.
- Controlled HTF/entry-score grid, preregistered OOS/walk-forward, cost stress, cross-feed replication, dan runtime TradingView A/B equivalence belum tersedia.

Framework dapat menghasilkan paket validasi internal yang reproducible, tetapi keterbatasan data tetap memblokir klaim publication-ready, statistical superiority, parameter optimal, profit guarantee, future performance, dan rekomendasi alokasi modal live.
