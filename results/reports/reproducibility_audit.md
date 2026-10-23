# Audit Reproducibility MAESTRO-X v1.0

Tanggal audit: 24 Agustus 2026  
Ruang lingkup: environment, dependensi, determinisme, Monte Carlo, integritas sumber, dan konsistensi orkestrasi.  
Objek immutable: `strategy/MAESTRO-X_v1.0.pine` dan `data/raw/trades/*.csv`.

## Kesimpulan

Reproducibility komputasional lokal **lulus secara parsial**, tetapi reproducibility paket end-to-end **belum lulus**. Dua eksekusi `python -m src.run_pipeline` pada environment baru menghasilkan output byte-identik dan tidak mengubah Pine maupun raw CSV. Namun, command tersebut mengorkestrasi jalur implementasi lama yang berbeda dari engine produksi Phase 2-5. Perbedaan ini menghasilkan populasi analisis, metode Monte Carlo, dan schema output yang tidak sama, serta menciptakan ketergantungan pada urutan eksekusi.

## Hasil eksekusi

| Pemeriksaan | Hasil | Bukti |
|---|---|---|
| `pytest validation/` pada `.venv` proyek | Lulus | 30 tests passed dalam 18.29 detik |
| Fresh Python 3.13 environment | Lulus | Seluruh dependency `requirements.txt` terpasang; `pip check` tidak menemukan konflik |
| Clean-environment `unittest` | Lulus | 30 tests passed dalam 18.56 detik |
| Clean-environment `pytest` | Tidak tersedia | `pytest` tidak dideklarasikan di `requirements.txt` |
| `python -m src.run_pipeline`, run 1 | Lulus | Exit 0; 6.94 detik; 325 normalized rows; 324 closed rows; 80.000 paths |
| `python -m src.run_pipeline`, run 2 | Lulus | Exit 0; 5.80 detik; ringkasan identik |
| Hash output run 1 vs run 2 | Lulus | 0 mismatch pada 26 file di reports, tables, dan figures |
| Manifest setelah run 2 | Lulus terbatas | 25 artefak terdaftar; 0 mismatch size/hash |
| Kompilasi Python | Lulus | `python -m compileall -q src validation`, exit 0 |
| Source integrity | Lulus | 0 perubahan hash pada Pine dan delapan raw CSV |

## Environment dan dependensi

Runtime baru dibuat dari Python 3.13 dan hanya diisi dari `requirements.txt`. Import pandas, NumPy, SciPy, Matplotlib, PyYAML, dan Plotly berhasil. Dependency runtime cukup untuk menjalankan `src.run_pipeline` dan test suite melalui standard-library `unittest`.

`pytest` adalah dependency pengujian yang tidak didokumentasikan. Perintah eksplisit pengguna dapat dijalankan hanya karena `.venv` proyek sudah memiliki pytest 9.1.1. README mendokumentasikan `unittest`, sehingga test runner yang terdokumentasi tetap dapat dijalankan pada environment bersih. Untuk reproduksi identik lintas mesin, masih belum tersedia lockfile lengkap, hash paket, deklarasi platform, dan kontrol font/backend Matplotlib. Plotly menggunakan rentang versi, bukan pin exact.

## Determinisme Monte Carlo

Test integrasi menjalankan permutation dan IID bootstrap dua kali pada temporary outputs dan memverifikasi kesamaan ringkasan serta hash CSV, HTML, dan PNG. Jalur produksi menggunakan seed tetap `20260822` sampai `20260837`, 10.000 simulasi per metode/timeframe, atau 160.000 paths total.

`src.run_pipeline`, sebaliknya, hanya menjalankan permutation dengan 10.000 paths per timeframe, atau 80.000 paths total. Command ini deterministik, tetapi tidak mewakili keseluruhan engine Monte Carlo institusional yang dipakai oleh `src.run_monte_carlo`.

`run_manifest.json` dibuat sebelum enam laporan final audit ini ditulis. Integritas 25 entry yang tercatat lulus, tetapi manifest tersebut bukan manifest lengkap untuk final research package.

## Integritas data sumber

SHA-256 Pine saat audit adalah `a4e94a39fdf2806db07b0142ad8a014ad1f60ea76efcead4c648e6ae744e0bc6`. Hash tersebut tetap sama sebelum dan sesudah dua pipeline run. Delapan hash raw CSV juga tidak berubah. Preprocessing produksi melakukan hash sebelum/sesudah, atomic writes, provenance per source row, dan quarantine tanpa menulis ke raw source.

Artefak equivalence lama menyebut candidate hash `c73fb6a864c5d51547ae8758d6eac89753d96fc301d1a16b64c90f3d61eb906b`. Karena hash itu tidak sama dengan Pine saat ini dan runtime A/B TradingView belum dilakukan, equivalence tersebut tidak dapat dijadikan bukti runtime bagi objek riset saat ini.

## Gap kritis: dua pipeline paralel

`src.run_pipeline` memakai `data_loader.py`, `data_validation.py`, `metrics.py`, `monte_carlo.py`, `sensitivity.py`, dan `visualization.py`. Jalur Phase 2-5 yang lebih kuat memakai `preprocessing.py`, `performance_engine.py`, `monte_carlo_engine.py`, dan `sensitivity_engine.py` melalui command terpisah.

Dampaknya material:

- Pipeline end-to-end menerima 325 rows dan menganalisis 324 closed trades.
- Preprocessing produksi mengarantina dua baris invalid, menerima 323 rows, dan menganalisis 322 closed trades.
- Pipeline end-to-end hanya menjalankan permutation; engine produksi juga menjalankan bootstrap.
- Pipeline end-to-end menulis ulang `performance_metrics.csv` dengan schema 29 kolom. Setelah itu, `python -m src.run_sensitivity` gagal karena kolom `calculation_validation_status`, `initial_capital_provenance_status`, dan `processed_rows` hilang.
- Artefak produksi lama seperti `monte_carlo_statistics.csv` dan `parameter_sensitivity.csv` dapat tetap berada berdampingan dengan `performance_metrics.csv` yang baru ditulis jalur lama. Paket hasil kemudian terlihat lengkap tetapi berasal dari populasi dan run yang berbeda.

Ini adalah hidden state dependency pada urutan command dan merupakan blocker utama untuk klasifikasi `validation_ready`.

## Putusan

- Determinisme lokal: **lulus**.
- Dependency runtime utama: **lulus**, dengan gap dependency pytest dan lockfile.
- Perlindungan Pine/raw source: **lulus**.
- Reproducibility Monte Carlo produksi: **lulus pada test terisolasi**.
- Reproducibility paket end-to-end kanonik: **gagal** karena orkestrasi paralel dan artefak silang-run.

Status keseluruhan: **prototype reproducible secara lokal, belum validation-ready sebagai satu research package**.
