# Diskusi Robustness MAESTRO-X v1.0

## Temuan yang didukung data

Pada subset terobservasi, profit factor berada pada rentang 0.932-2.946. Timeframe dengan Net PnL negatif pada subset: 30m (-0.482 USD). Angka ini tidak menggantikan hasil Strategy Tester penuh karena baris yang hilang tidak direkonstruksi.

Randomisasi sequence menunjukkan worst maximum drawdown tertinggi di antara 10.000 path per timeframe sebesar 22.645%. Semua terminal return invariant, sesuai sifat permutasi tanpa replacement. Tidak ada path yang melewati ruin threshold USD 20, tetapi hasil tersebut terbatas pada magnitudo PnL dan jumlah trade yang terobservasi.

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
