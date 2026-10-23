# Metodologi Validasi MAESTRO-X v1.0

## Tujuan dan unit analisis

Framework ini memvalidasi trade log yang tersedia tanpa mengubah Pine Script, aturan entry/exit, risk logic, parameter, atau asumsi backtest asli. Unit analisis adalah baris closed trade yang benar-benar terobservasi setelah normalisasi.

## Audit dan normalisasi data

CSV memakai header kelas DOM ter-minifikasi, sehingga schema dipetakan berdasarkan posisi dan pola nilai. File append kumulatif dipisahkan saat trade ID berhenti menurun; blok terakhir diasumsikan sebagai timeframe yang tercantum pada nama file. Asumsi ini dicatat dan tidak menghapus blok lain dari raw data.

| Timeframe | Raw rows | Selected rows | Closed observed | Missing ID dalam rentang | Segments |
|---|---:|---:|---:|---:|---:|
| 1m | 61 | 61 | 61 | 212 | 1 |
| 5m | 61 | 61 | 61 | 168 | 1 |
| 15m | 91 | 30 | 30 | 0 | 2 |
| 30m | 119 | 28 | 28 | 0 | 3 |
| 1h | 61 | 61 | 60 | 371 | 1 |
| 2h | 147 | 28 | 28 | 0 | 4 |
| 4h | 175 | 28 | 28 | 0 | 5 |
| 1D | 203 | 28 | 28 | 0 | 6 |

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
