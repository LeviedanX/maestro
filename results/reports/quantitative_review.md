# Quantitative Validation Review

Tanggal audit: 24 Agustus 2026

## Basis evaluasi

Review utama menggunakan keluaran preprocessing produksi: 918 raw rows, 325 target candidates, 323 accepted rows, 2 quarantined rows, 322 closed trades, dan 1 open trade. Angka 325/324 dari `src.run_pipeline` diperlakukan sebagai hasil jalur lama karena memasukkan dua record yang ditolak pipeline produksi.

## Kecukupan sampel

Jumlah closed trade per timeframe hanya 27-61: 1m 61, 5m 61, 15m 30, 30m 28, 1h 60, 2h 28, 4h 27, dan 1D 27. Total 322 tidak boleh diperlakukan sebagai satu sample karena setiap timeframe berasal dari backtest independen, periode berbeda, dan konfigurasi/feed tidak terbukti identik.

Ukuran tersebut cukup untuk pemeriksaan arithmetic, sanity checks, dan exploratory resampling. Ukurannya tidak cukup untuk estimasi tail risk stabil, klaim edge populasi, ranking timeframe, atau generalisasi lintas rezim. Bootstrap dari 27-61 observasi pada dasarnya mengulang empirical support yang sempit; ketidakpastian model dan trade yang hilang tidak tercakup.

## Komparabilitas timeframe

Timeframe tidak comparable secara kausal. Window terobservasi berkisar dari beberapa hari pada intraday sampai 1975-1977 pada 1D. Terdapat append blocks, trade-ID gaps besar pada 1m, 5m, dan 1h, timezone tidak tersedia, serta exact feed, chart range, dan Strategy Properties tidak dibekukan.

Perbedaan hasil dapat berasal dari market regime, coverage, data vendor, biaya, atau setting yang tidak tercatat. Karena itu, 4h tidak boleh dinyatakan sebagai timeframe terbaik dan 30m tidak boleh dijadikan bukti bahwa parameter timeframe tertentu intrinsik buruk.

## Reliabilitas metrik

Engine produksi memverifikasi identitas internal: total = win + loss + breakeven, net profit = gross profit - gross loss, expectancy x N = net profit, ending equity = initial capital + net profit, dan maximum drawdown sesuai equity reconstruction. Secara arithmetic, metrik lulus.

Reliabilitas eksternal tetap terbatas:

- Initial capital USD 40 pada metadata/export berkonflik dengan `initial_capital=10000` di Pine.
- Komisi tidak tersedia pada enam schema 20 kolom; slippage, spread, financing, dan fill behavior tidak lengkap.
- Equity dan drawdown dibangun dari observed rows, bukan trade history lengkap. Missing adverse trades dapat membuat drawdown terlalu rendah; missing favorable trades dapat memberi bias sebaliknya.
- Profit factor dan recovery factor sangat tidak stabil pada sample kecil.
- `trade_sharpe_approx` adalah Sharpe per trade, bukan annualized calendar Sharpe, dan tidak memakai risk-free rate.
- Tidak satu pun jumlah trade processed cocok dengan snapshot PDF, sehingga validasi like-for-like terhadap TradingView tidak dapat dilakukan.

## Temuan observed subset

Pada hasil produksi sebelum artefaknya ditimpa jalur end-to-end, tujuh dari delapan subset memiliki expectancy positif dan profit factor di atas satu. Subset 30m negatif: net return -1.205%, profit factor 0.932, expectancy -0.0172 USD, dan bootstrap probability of negative return 56.06%.

Temuan tersebut harus dipertahankan karena merupakan negative result yang sah. Ia tidak membuktikan future underperformance dan tidak boleh dihapus atau dikecualikan.

## Interpretasi drawdown

Observed maximum drawdown produksi berada pada kisaran 2.99%-15.31% berdasarkan asumsi modal USD 40. Ini bukan TradingView account drawdown penuh. Nilainya hanya menggambarkan urutan closed trade yang lolos preprocessing dan tidak memasukkan intra-trade equity path, open-position excursion, margin requirements, ataupun missing trades.

Permutation median maximum drawdown berkisar sekitar 2.90%-8.99%. Worst permutation path tertinggi sekitar 24.01% pada 5m. Bootstrap menghasilkan worst simulated drawdown hingga 52.25% pada 1m. Nilai worst adalah ekstrem yang muncul dari 10.000 simulated samples, bukan batas risiko populasi.

## Interpretasi Monte Carlo

Permutation tanpa replacement hanya mengukur sequence risk. Terminal return invariant, sehingga percentile interval terminal return permutation degenerat dan tidak mengandung informasi inferensial.

IID bootstrap mengubah komposisi trade dan menghasilkan distribusi terminal return. Enam dari delapan interval percentile 95% melintasi nol. Interval 5m dan 4h tidak melintasi nol dalam resampling conditional tersebut, tetapi hal ini bukan uji signifikansi, tidak mengoreksi selection bias atau multiple testing, dan tidak mengatasi nonstationarity.

Satu dari 10.000 bootstrap paths 1m melewati threshold equity USD 20, atau 0.01%. Tidak adanya breach pada batch lain berarti nol kejadian pada simulasi terbatas, bukan probability of ruin populasi sama dengan nol.

Asumsi IID mengabaikan serial dependence, volatility clustering, regime shifts, dan conditional position sizing. Karena missing rows dan data provenance belum selesai, distribusi Monte Carlo tidak boleh dipakai sebagai forecast.

## Sensitivitas dan overfitting

Chart timeframe hanya tersedia sebagai external descriptive axis dengan sample tidak seragam. HTF Bias Timeframe dan Minimum Entry Score tidak memiliki outcome-tagged controlled runs, sehingga stability, gradient, robustness region, dan interaction effect berstatus `not_estimable`.

Risiko overfitting adalah **tidak terukur dengan evidentiary concern tinggi**. Pine memiliki banyak degrees of freedom, sementara tidak tersedia preregistered grid, complete sweep history, multiple-testing correction, out-of-sample split, walk-forward result, cost stress, atau cross-feed replication.

## Putusan kuantitatif

Framework mendukung validasi deskriptif observed subset dan audit kalkulasi internal. Evidence belum mendukung statistical edge, superiority antar-timeframe, parameter robustness, portfolio allocation, atau future profitability.
