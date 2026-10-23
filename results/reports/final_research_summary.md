# Final Research Summary - MAESTRO-X v1.0

Tanggal audit: 24 Agustus 2026  
Klasifikasi infrastruktur setelah Phase 7.5: **validation_ready**  
Kesiapan publikasi empiris: **not_ready**

## 1. Research objective

MAESTRO-X Research Framework bertujuan memvalidasi secara kuantitatif exported trade logs dari MAESTRO-X v1.0 tanpa mengubah Pine Script, entry/exit rules, risk logic, parameter, atau raw CSV. Studi ini menilai data quality, realized performance, equity-path risk, conditional resampling robustness, dan ketersediaan evidence untuk parameter sensitivity.

Studi ini bukan optimasi strategi, bukan trading recommendation, dan bukan bukti future profitability.

## 2. Dataset

Dataset primer terdiri dari delapan TradingView CSV untuk 1m, 5m, 15m, 30m, 1h, 2h, 4h, dan 1D. Total raw rows adalah 918. Lima file mengandung cumulative append blocks. Dari 325 target candidates, preprocessing produksi menerima 323 rows, mengarantina 2 malformed rows, mempertahankan 1 open trade, dan menggunakan 322 closed trades untuk realized analysis.

Closed sample per timeframe hanya 27-61. Periode berbeda secara ekstrem dan tiga selected segments memiliki trade-ID gaps besar. Exchange feed, timezone, exact chart range, dan complete Strategy Properties tidak tersedia. Dataset karena itu hanya mendukung `observed_processed_closed_trades_only` analysis.

## 3. Methodology

Pipeline produksi meliputi:

1. schema dan inventory validation;
2. trade-ID segmentation dan timeframe detection;
3. datatype/timestamp conversion;
4. semantic validation, provenance, dan quarantine;
5. realized performance dan equity reconstruction;
6. 10.000 sequence permutations serta 10.000 IID bootstrap samples per timeframe;
7. descriptive timeframe comparison;
8. audit definisi HTF Bias Timeframe dan Minimum Entry Score tanpa membuat outcome sintetis.

Permutation mengukur ordering/path risk karena terminal return invariant. Bootstrap percentile distributions conditional pada empirical IID trades dan tidak diperlakukan sebagai forecast atau population confidence interval.

## 4. Experiments and validation

- `pytest validation/` setelah Phase 7.5: 35/35 passed pada environment proyek dan fresh temporary environment.
- Fresh-environment `unittest`: 30/30 passed.
- Python compile validation: passed.
- Fresh runtime dependency install dan `pip check`: passed.
- `src.run_research_pipeline`: canonical production run berhasil dengan 21 artefak terisolasi plus manifest; 0 hash mismatch.
- Pine dan delapan raw CSV: tidak berubah setelah pipeline runs.
- Production Monte Carlo repeatability: lulus pada integration tests.
- Static Pine equivalence lama: tidak applicable langsung karena candidate hash berbeda dan runtime A/B tidak pernah dilakukan.

## 5. Main findings

Arithmetic metrics dan equity reconciliation produksi lulus untuk accepted subset. Tujuh timeframe subsets memiliki positive observed expectancy dan profit factor di atas satu. Subset 30m mempertahankan hasil negatif: net return -1.205%, profit factor 0.932, expectancy -0.0172 USD, dan bootstrap probability of negative terminal return 56.06%.

Enam dari delapan bootstrap terminal-return percentile intervals melintasi nol. Pada conditional experiment, 5m dan 4h intervals tidak melintasi nol, tetapi ini bukan significance evidence dan tidak membenarkan ranking.

Worst maximum drawdown yang muncul pada production bootstrap adalah sekitar 52.25% pada 1m. Satu 1m bootstrap path dari 10.000 menyentuh operational ruin threshold USD 20. Zero breach pada batch lain tidak berarti population ruin probability nol.

Timeframe effects bersifat deskriptif dan non-comparable. HTF Bias Timeframe serta Minimum Entry Score tidak memiliki controlled outcome data; stability dan robustness region tidak dapat diestimasi. Overfitting risk tidak dapat dihitung dan memiliki evidentiary concern tinggi.

## 6. Final audit gaps and Phase 7.5 closure

Audit awal menemukan empat gap integritas paket: `src.run_pipeline` memakai legacy engines, populasi legacy 324 closed rows berbeda dari populasi produksi 322 rows, legacy Monte Carlo hanya menghasilkan 80.000 permutation paths, dan shared output directory memungkinkan overwrite serta stale artifact mixing.

Phase 7.5 menutup gap infrastruktur tersebut. `src.run_research_pipeline` sekarang memanggil production preprocessing, performance, permutation-plus-bootstrap Monte Carlo, sensitivity, dan reporting secara berurutan. Setiap run menggunakan versioned schema gates dan atomic run-scoped publication di `results/runs/YYYYMMDD_HHMMSS/`. Command lama hanya mendelegasikan dengan deprecation warning dan tidak menulis shared production artifacts.

Penutupan ini tidak mengubah hasil eksperimen. Canonical population tetap 323 accepted rows dan 322 closed trades; Monte Carlo tetap 160.000 paths; negative 30m outcome dan seluruh limitations tetap dipertahankan.

Selain itu, dua technical PDFs membuat klaim statistical edge, best timeframe, live allocation, dan parameter changes yang tidak didukung evidence primer. PDF jurnal masih berupa template. Proposal meneliti Multi-Agent LLM validation, sedangkan repository belum mengimplementasikan atau menguji LLM agents/raw-vs-validated signals.

## 7. Limitations

1. Source logs incomplete, cumulative, dan tidak snapshot-equivalent dengan PDF.
2. Initial capital USD 40 berkonflik dengan Pine USD 10.000.
3. Cost dan fill assumptions tidak lengkap.
4. Sample kecil, heterogen, dan tidak dapat dipool.
5. Drawdown hanya berdasarkan closed-trade sequence.
6. Bootstrap mengasumsikan IID dan tidak memodelkan regime shifts.
7. Tidak ada controlled parameter grid, OOS, walk-forward, cross-feed, atau multiple-testing correction.
8. Runtime TradingView compile/A-B equivalence belum tersedia untuk Pine hash saat ini.
9. [Resolved Phase 7.5] Canonical clean-state pipeline dan artifact isolation telah tersedia.
10. Research question proposal belum sesuai dengan eksperimen repository.

## 8. Future work

Future work yang diperlukan, tanpa mengoptimasi strategi:

1. Bekukan complete TradingView exports dan full Strategy Properties.
2. Rekonsiliasi initial capital serta source feed/timezone/date range.
3. Satukan orkestrasi ke production Phase 2-5 path dan tambahkan clean-state artifact isolation.
4. Jalankan runtime A/B equivalence di TradingView.
5. Jalankan preregistered controlled parameter grid, OOS/walk-forward, cost stress, dan cross-feed replication.
6. Jika proposal Multi-Agent LLM dipertahankan, implementasikan controlled raw-versus-validated signal experiment secara terpisah dari immutable strategy validation.
7. Susun manuskrip jurnal nyata dan hapus semua placeholder serta unsupported claims.

## Final classification

Framework **validation-ready sebagai paket validasi internal yang terpadu**. Framework **belum publication-ready** karena gap empiris, provenance, dan controlled experiments tetap material. Klasifikasi machine-readable tersedia di `final_status.json`.
