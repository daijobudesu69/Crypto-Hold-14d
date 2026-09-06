# Catatan Pembangunan V1.4 — v1.4.0 sampai v1.4.3

**Tanggal:** 22 Agustus 2026
**Cakupan:** perbaikan V1.3 (v1.4.0), repo produksi (v1.4.1), replay historis (v1.4.2), job harian (v1.4.3 langkah 1)
**Dokumen induk:** `V1_4_SPEC.md`, `results/KOREKSI_V1_3.md`, `results/V1_4_0_RESULTS.md`

> **Ringkasan sejujurnya:** pipa produksi hidup dan terbukti mereproduksi backtest sampai digit terakhir.
> Tapi **grid robustness T8 gagal (16.7%, syarat 70%)**, jadi **tidak ada konfigurasi yang boleh disebut robust.**
> Selama pembangunan ditemukan **10 bug**, semuanya di kode yang tesnya hijau, dan **tidak satu pun ditemukan oleh tes**.

---

## 1. Peta versi

| Versi | Isi | Status | Gerbang |
|---|---|---|---|
| v1.4.0 | Perbaikan V1.3: tes no-lookahead, pemilihan overlay, T8 | ✅ Selesai | ❌ **Tidak terpenuhi** — T8 gagal |
| v1.4.1 | Repo publik, port `features`/`engine`, config frozen | ✅ Selesai | ✅ Lolos |
| v1.4.2 | Replay 2019–2026 lewat pipa produksi | ✅ Selesai | ✅ **Lolos, identik bit-per-bit** |
| v1.4.3 | Cron harian, Telegram, Sheets, OCO, alarm hari ke-13 | 🔄 Kode siap, cron aktif | ⏳ Butuh 7 hari berturut |
| v1.4.4 | Shadow log 90 hari, nol modal | Belum | — |
| v1.4.5 | Modal mikro, eksekusi manual | Belum | — |

---

## 2. v1.4.0 — perbaikan V1.3

Tiga tindakan dari `KOREKSI_V1_3.md` §8.

### 2.1 (a) Tes no-lookahead — LOLOS

`sanity_tests.py` sebelumnya hanya menguji `engine.py`. Itulah celah yang meloloskan K1: `btc_dd_shallow` dibangun di `run_registry.py`, di luar jangkauan 13 tes yang ada.

Metodenya **prefix-invariance**: potong input di hari *T*, bangun ulang, dan semua nilai pada hari ≤ *T* harus identik dengan hasil bangun full-history. Statistik full-sample apa pun langsung ketahuan.

| Tes | Isi |
|---|---|
| T13 | **Kontrol positif** — harness wajib menyala pada median full-sample dan diam pada `expanding_median_split` |
| T14 | 32 kolom `features.add_features` kausal |
| T15 | `features.btc_regime_columns` kausal |
| T16 | `btc_tsmom_120_pos` kausal; `btc_dd_shallow` **tetap ditandai bocor** (regression lock) |
| T17 | `next_open` persis `open[t+1]`, dijaga di luar feature set kausal |

**13 → 18 tes, semua lolos.** T13 penting: tanpa kontrol positif, detektor yang rusak meloloskan semua kolom secara hampa.

Dijalankan pada data BTC nyata, mereproduksi `KOREKSI_V1_3.md` §2 persis:

| | Nilai |
|---|---|
| Median full-sample `dd_from_90d_high` | −0.1171 |
| Gate ON — asli / causal | 48.4% / 44.8% |
| Hari keputusan berbeda | 8.6% |

Konstruksi kolom regime diekstrak jadi fungsi (`features.btc_regime_columns`, `run_registry.add_btc_regime_columns`, `features.expanding_median_split`) — **ekstraksi murni, nol perubahan numerik**, diverifikasi terhadap `btc_regime.pkl` dan `regime_full.pkl`.

### 2.2 (b) Pemilihan overlay — hasilnya BERLAWANAN dengan dugaan

Aturan V1.3 punya **dua** cacat. Yang kedua tidak disebut di KOREKSI:

1. "Tanpa overlay" tidak pernah jadi kandidat (ini K2)
2. Kandidat diperingkat memakai OOS mean R milik base config **berbeda** (44 koin, C2/C5 aktif, 3 slot) dari Track yang akan memakainya — perbandingan tidak setara

Aturan v1.4.0: jalankan base config **milik Track itu sendiri** untuk tiap kandidat **dan** tanpa overlay, lalu ambil OOS mean R tertinggi.

Hasil Track B (BTC+ETH):

| overlay | n | mean R | growth | maxDD | Calmar | OOS mean R | OOS n |
|---|---|---|---|---|---|---|---|
| **T3b — BTC > SMA50** | 267 | 0.3876 | **17.75x** | **−22.5%** | **2.48** | **+0.1891** | 80 |
| no_overlay | 298 | 0.3182 | 13.45x | −27.0% | 1.82 | +0.1508 | 88 |
| T3a — BTC > SMA200 | 256 | 0.2651 | 6.25x | −23.9% | 1.42 | +0.1508 | 88 |
| T3c — BTC tsmom120 | 288 | 0.3192 | 12.44x | −27.0% | 1.75 | +0.1508 | 88 |
| T3d — dd_shallow | 261 | 0.3203 | 9.87x | −30.3% | 1.39 | +0.1203 | 87 |

**Memilih T3b, bukan "no overlay"** — berlawanan dengan kesimpulan KOREKSI §3.

**Tapi metriknya nyaris tidak membedakan apa pun.** Sejak 2024 BTC hampir selalu di atas rata-ratanya, sehingga **no_overlay, T3a, dan T3c menghasilkan 88 trade OOS yang IDENTIK** — bukan mirip, persis sama. T3b berbeda hanya karena membuang **8 trade** dari 88.

| overlay | OOS n | OOS mean R | OOS t-stat |
|---|---|---|---|
| no_overlay | 88 | +0.1508 | 1.07 |
| T3b | 80 | +0.1891 | **1.25** |
| T3d | 87 | +0.1203 | 0.83 |

Tidak satu pun signifikan. **Keputusan: tetap tanpa overlay** — bukan karena overlay terbukti merugikan (tidak terbukti), tapi karena di antara kandidat yang tak terbedakan, tanpa-overlay punya design freedom nol dan trade terbanyak.

Track A tetap memilih T3d — dan T3d memakai kolom berlookahead. Track altcoin sudah ditutup di V1.3, jadi tidak berdampak produksi, tapi dicatat.

### 2.3 (c) T8 grid NSE — GAGAL

| Config | Sel lolos | Rate | Verdict |
|---|---|---|---|
| BTC+ETH+SOL, tanpa overlay, 3% | 108 / 648 | **16.7%** | ❌ (syarat ≥70%) |
| BTC+ETH, tanpa overlay, 3% | 0 / 648 | **0.0%** | ❌ |

Sel referensi mereproduksi **32.49x / −29.5% / OOS +0.0708** persis. Yang gagal adalah tetangganya.

Per kriteria: maxDD<35% 70.8% | return ≥ BTC B&H **18.5%** | OOS>0 54.2%

Pemecah utama:

| Dimensi | Rate |
|---|---|
| rebalance harian | 50.0% |
| rebalance Senin / Rabu | **0.0% / 0.0%** |
| cost 0.2% / 0.3% / 0.6% / 1.0% | 83% / 83% / 33% / **0%** |
| min_history 60 / 120 / 365 | 62% / 62% / **25%** |
| entry next_open / next_close | 67% / 33% |

**Verdict tidak bergantung pada perdebatan kriteria 2**: dibuang seluruhnya pun BTC+ETH+SOL hanya 50.0% (62.5% subgrid harian).

**Temuan sampingan yang menguatkan pilihan produksi:**

| Config | maxDD+OOS, 648 sel | maxDD+OOS, harian | maxDD terburuk |
|---|---|---|---|
| BTC+ETH+SOL | 50.0% | 62.5% | −47.6% |
| **BTC+ETH (produksi)** | **83.3%** | **100.0%** | **−35.6%** |

BTC+ETH lebih kokoh; satu-satunya yang membuatnya 0/648 adalah ia tidak pernah mengalahkan BTC B&H.

**Catatan metode — 648 sel ≠ 648 konfigurasi.** Dimensi `universe_col` tidak mengikat sama sekali (BTC/ETH/SOL semuanya top-20 dan bukan memecoin). Untuk BTC+ETH, `liq_floor` juga tidak pernah mengikat.

| Config | Sel grid | Sel berbeda | Engine run berbeda |
|---|---|---|---|
| BTC+ETH+SOL | 648 | 168 | 42 |
| BTC+ETH | 648 | 72 | 18 |

Rate tidak berubah, tapi cakupan grid lebih sempit dari kesan angka 648.

**Efisiensi (dua ekuivalensi eksak, bukan aproksimasi):**

- **E1** panel dipotong ke simbol yang diperdagangkan — sah karena `corr_cap=None` dan ranking hanya di dalam himpunan eligible
- **E2** biaya adalah post-processing murni — `fee_side`/`slip_side` hanya masuk di akuntansi exit, jadi `R_net(cost) = R_gross − cost / sl_dist_pct`

Diverifikasi: **16/16 sel sampel** dijalankan ulang dengan panel penuh dan biaya asli, cocok persis (toleransi 1e-12). Waktu turun ~3,5 jam → 7,5 menit.

### 2.4 Konsekuensi

> **Lolos bar ≠ robust.** Angka 32.49x adalah hasil satu sel, bukan sifat yang bertahan di lingkungan parameternya. Hal yang sama berlaku untuk 13.45x.

---

## 3. Revisi `V1_4_SPEC.md` (keputusan Dew)

**Tidak satu pun angka konfigurasi berubah.** Universe, momentum 28/120, SL/TP 1.5/3.0, hold 14 hari, maks 2 posisi, risk 3%, spot tanpa leverage, overlay tidak ada — semua tetap.

| # | Yang direvisi | Dari | Menjadi |
|---|---|---|---|
| 1 | §3.3 (baru) | implisit "lolos bar" | **tidak boleh disebut robust** — T8 16.7% |
| 2 | §2.1 baris 14 | "overlay regime BTC merugikan" | "tidak ada overlay yang terbukti membantu" |
| 3 | §2.3 | satu baris menggabung SMA200 + SMA50 | **dua baris terpisah** |
| 4 | §8 | — | catatan desain grid T8 |
| 5 | §2.7 (baru) | — | "maks 2 posisi" tidak pernah mengikat |
| 6 | §2.8 (baru) | — | alur keluar: OCO + alarm hari ke-13 |

### Kenapa SMA dipisah

Larangan lama menggabungkan keduanya, padahal datanya berlawanan arah:

| Saklar | Trade dibuang | Rata-rata mutu yang dibuang | Hasil |
|---|---|---|---|
| BTC > SMA200 | 54 | **+0.53R** — membuang trade terbaik | 6.25x |
| BTC > SMA50 | 62 | +0.20R — membuang di bawah rata-rata | 17.75x |

Sepanjang **2022 SMA200 mati 365 hari penuh** — melewatkan seluruh fase pemulihan awal. SMA50 menyala lagi dalam hitungan minggu.

Ditumpuk pun merusak: SMA200 **dan** SMA50 bersama = 8.41x, di bawah SMA50 saja (17.75x).

### §2.7 — "maks 2 posisi" tidak pernah mengikat

Plafon 2 sudah dipaksakan oleh universe 2 koin + aturan "koin yang dipegang dicoret dari kandidat".

| `max_positions` | Trade | Identik? |
|---|---|---|
| 2 (produksi) | 298 | — |
| 3 | 298 | ✅ |
| 9 | 298 | ✅ |

Hunian harian: **0 posisi 48.5%**, 1 posisi 18.4%, 2 posisi 33.1%. Maksimum yang pernah terjadi: 2.

Frekuensi sinyal tersaring di sini, bukan oleh aturan posisi: dari **1.234 hari** yang punya minimal satu koin lolos 3 syarat, hanya **261 hari** menghasilkan entry. Sisanya terbuang karena koinnya sedang dipegang.

### §2.8 — alur keluar

| Cara keluar | n (dari 298) | Mekanisme |
|---|---|---|
| Kena SL | 126 (42%) | **OCO di Binance**, dipasang saat masuk |
| Kena TP | 101 (34%) | **OCO di Binance** |
| Habis 14 hari | 71 (24%) | **Tutup manual**, dipicu alarm hari ke-13 |

OCO wajib, bukan opsional: job harian tidak mengintip intraday, jadi tanpa OCO asumsi backtest soal harga exit tidak berdasar.

Efek samping menguntungkan: backtest memakai aturan konservatif (SL duluan kalau satu hari menyentuh keduanya); OCO asli mengambil yang benar-benar lebih dulu. Bias yang aman.

---

## 4. v1.4.1 — repo produksi

| Berkas | Isi |
|---|---|
| `.gitignore` | **Dibuat paling pertama** — §6.1 mewajibkan lengkap sebelum commit pertama |
| `src/config_v14.py` | Semua konstanta §2 hard-coded + `validate()` jalan saat import |
| `src/features.py`, `src/engine.py` | **Byte-identical** dengan V1.3 (SHA256 dicek) |
| `src/panel_v14.py` | Kolom yang di V1.3 lahir di `run_registry.py` |
| `tests/` | 18 sanity + kesetiaan port + pemeriksa gerbang |

`run_registry.py` (orkestrator riset 26 tes, 44 koin) sengaja **tidak** diport.

**Temuan keamanan:** repo ternyata sudah ada sejak 16 Agustus dengan 1 commit, dan `.gitignore` lamanya **melanggar §6.1 di kelima pola** (`*.json`, `*.env`, `credentials*`, `token*`, `*.pkl` — hilang semua). Repo sudah publik selama itu.

Diaudit: riwayat lama bersih, nol kredensial pernah ter-commit, jadi **tidak ada kebocoran**. Celah ditutup sebelum v1.4.3 memperkenalkan token Telegram dan kunci Google. Riwayat lama dipertahankan lewat merge, bukan ditimpa.

---

## 5. v1.4.2 — replay historis (GERBANG UTAMA)

Dijalankan **dua lapis** supaya bug kode dan selisih data tidak bisa saling menutupi:

| Lapis | Data | Hasil |
|---|---|---|
| A — isolasi kesalahan kode | CSV V1.3 | 298 trade, mean R 0.3182 ✅ |
| B — jalur produksi | REST API Binance | 298 trade, mean R 0.3182 ✅ |
| B vs A | | **Identik trade-per-trade** |

Array `R_net` sama **bit-per-bit**: `0.31819032193190866`.

### Data API tidak identik dengan CSV V1.3 — tapi terbukti tidak berdampak

| Selisih | Kenapa tidak berdampak |
|---|---|
| `volume` beda di 9 hari | **Tidak dipakai fitur mana pun** |
| `quote_volume` beda s/d 2.5% di 200 hari | Ambang $5jt, nilai terendah BTC $82jt / ETH $28jt — margin 6–16× |
| Lilin 2026-08-16 low & close beda | Lihat bawah |

**CSV V1.3 menangkap lilin terakhir sebelum hari itu selesai** — close tercatat $63.110, nilai finalnya $62.900. Backtest V1.3 memakai satu lilin yang belum tutup.

Tidak berdampak **karena kebetulan**: exit terakhir jatuh 2025-11-04, jadi tidak ada posisi terbuka di ujung data. Dicatat sebagai batasan, bukan jaminan struktural.

### Tiga jebakan di `binance_data.py`

1. **Lilin belum tutup** — dibuang lewat `close_time < now`, bukan "buang baris terakhir" (salah kalau API mengembalikan jumlah tak terduga)
2. **Rentang tanggal eksplisit** — `days_listed` itu **nomor baris**, bukan umur koin; menarik lebih awal menggeser kapan gate 60-hari lolos
3. **Dedup sambungan halaman** — batas API 1.000 lilin/panggilan

Lubang tanggal **gagal keras**; kalau didiamkan, `mom_28` diam-diam mengukur 29 hari kalender.

---

## 6. v1.4.3 — job harian

| Berkas | Isi |
|---|---|
| `src/binance_data.py` | Ambil lilin, endpoint publik tanpa API key, fallback antar-host |
| `src/pipeline.py` | Jalur produksi + cap eksposur portofolio |
| `src/shadow.py` | Kolom shadow §2.4 — dicatat, tidak pernah memblokir |
| `src/notify.py` | Telegram: sinyal masuk (dengan OCO), alarm hari ke-13, heartbeat, error |
| `src/sheets.py` | Shadow log ke Google Sheets |
| `src/daily_job.py` | Orkestrator cron |
| `.github/workflows/daily-signal.yml` | Cron 00:05 UTC |

### Keputusan desain: job TANPA STATE

Setiap hari sistem me-replay **seluruh riwayat sejak 2019**, bukan menyimpan "posisi saya sekarang".

Alasannya: gerbang v1.4.2 membuktikan replay = backtest. Kalau job harian memakai jalur lain (state tersimpan yang diperbarui bertahap), bukti itu tidak berlaku untuk yang benar-benar jalan tiap hari. Replay penuh cuma ~10 detik dan datanya gratis.

### Duplikasi seleksi sinyal — dan bunganya

`engine.py` mengiterasi `dates[:-1]` karena butuh baris hari berikutnya sebagai harga eksekusi. Di produksi "hari berikutnya" adalah **hari ini**, lilinnya baru dibuka. Jadi seleksi kandidat harus ditulis ulang di `pipeline.signals_for_day()`.

Utang itu dibayar `tests/test_signal_equivalence.py`: untuk **2.784 hari**, entri yang dipilih harus sama persis dengan yang benar-benar diambil engine. **Nol beda.**

### Infrastruktur

| Layanan | Kredensial | Untuk apa |
|---|---|---|
| Binance klines | tidak ada | Data harga (endpoint publik) |
| Telegram Bot | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Sinyal + heartbeat |
| Google Sheets | `GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID` | Shadow log |
| GitHub Actions | — | Penjadwal |

**Tidak ada API key trading di mana pun.** Eksekusi manual sampai v1.4.5.

### Run sungguhan pertama

| Run | Hasil |
|---|---|
| Manual #1 | Telegram ✅, Sheets ❌ (`SHEET_ID` membawa `/edit#gid=0`) |
| Manual #2 | ✅ **Job selesai tanpa masalah** |

```
data s/d          : 2026-08-21
sinyal masuk baru : 2        (BTC & ETH)
posisi terbuka    : 0
baris shadow log  : 3
sinyal terakhir   : 2025-10-26 (299 hari lalu)
```

---

## 7. Sepuluh bug — dan cara masing-masing ditemukan

**Tidak satu pun ditemukan oleh tes.** Semuanya muncul saat sesuatu dijalankan sungguhan, atau saat ditanya pertanyaan yang tepat.

| # | Bug | Akibat kalau lolos | Ketemu saat |
|---|---|---|---|
| 1 | Sinyal hari ini tak pernah terkirim (`dates[:-1]`) | Sistem diam selamanya sambil terlihat sehat | Uji kering pertama |
| 2 | Alarm hari ke-13 tak pernah berbunyi (posisi terbuka ditandai `eod`) | 24% trade tidak tahu kapan keluar | Uji kering pertama |
| 3 | Job mati gara-gara mencetak emoji (Windows cp1252) | Job gagal karena logging | Uji kering pertama |
| 4 | Binance geo-block **HTTP 451** | Cron gagal **setiap hari** | Jalankan ulang tes |
| 5 | Kapasitas Sheet habis dalam 11 bulan | Baris berhenti tertulis diam-diam | Dew tanya soal sheet kosong |
| 6 | Pesan error gagal terkirim (parse HTML) | Job mati tanpa pemberitahuan | Dew tanya "yakin tidak ada bug?" |
| 7 | Hijau palsu di CI tanpa secret | Gerbang 7 hari "lulus" tanpa kirim apa pun | Dew tanya soal trigger |
| 8 | Error Sheets = 8.000 karakter HTML | Penyebab tenggelam di CSS | Run manual pertama |
| 9 | **Cap eksposur portofolio tidak pernah diterapkan** | Disuruh beli **130.7% ekuitas** | Screenshot Telegram Dew |
| 10 | **HTTP 503 Sheets tanpa coba-ulang, dan salah didiagnosis** | Baris shadow_log hilang permanen; waktu habis memeriksa share yang sudah benar | Pesan error Gsheet dari Dew |

### Catatan bug #10 — gangguan beberapa detik, kerugian permanen

Pesan yang muncul:

```
gagal tulis shadow_log: RuntimeError: tidak bisa membuka spreadsheet.
APIError: [503]: The service is currently unavailable.
  Email yang harus di-share: creypto-ver1@crypto-ver1.iam.gserviceaccount.com
  SHEET_ID dipakai: 1jjiY1h-...QwE0 (44 karakter)
```

Dua cacat berbeda dalam satu pesan.

**Cacat 1 — tidak ada coba-ulang.** HTTP **503** = *Service Unavailable*: backend Google sedang bermasalah, hilang dalam hitungan detik. `notify.py` sudah punya `MAX_RETRIES = 3` sejak awal; `sheets.py` tidak punya apa-apa. Satu kedipan cukup untuk membuang baris shadow_log hari itu — dan job besok menulis hari besok, tidak pernah menambal hari yang bolong. Untuk forward test yang dirancang berjalan bertahun-tahun, itu lubang permanen dari gangguan beberapa detik.

**Cacat 2 — diagnosis salah arah.** `_client()` menerjemahkan **semua** kegagalan `open_by_key` jadi "belum di-share / SHEET_ID salah", lengkap dengan email service account dan panjang ID. Jadi gangguan di pihak Google terbaca seperti kesalahan konfigurasi, dan yang diperiksa adalah share dan ID yang sebenarnya sudah benar (44 karakter — memang sudah lolos `check_sheet_id`). Diagnosis yang salah arah lebih mahal daripada tidak ada diagnosis: ia mengarahkan pencarian ke tempat yang salah.

Perbaikan di `src/sheets.py`:

* `is_transient()` memisahkan **429/500/502/503/504** dan putus jaringan (boleh diulang) dari 401/403/404 dan JSON rusak (tidak boleh diulang — mengulangi salah konfigurasi cuma menunda pesan error).
* `_retry()` membungkus **setiap** panggilan Sheets — buka spreadsheet, cari/buat worksheet, baca judul, tulis baris. 5 percobaan, jeda 2/4/8/16 detik + jitter, ~30 detik total; job punya jatah 15 menit, jadi menunggu tidak berbiaya.
* Kalau tetap gagal, yang dilempar adalah `SheetsUnavailable`, bukan `RuntimeError` generik, dengan pesan yang menyatakan **"gangguan di sisi Google, BUKAN salah share/SHEET_ID"** — dan tanpa petunjuk konfigurasi yang menyesatkan itu.
* `_diagnose()` memeriksa status sementara **paling awal**. Balasan 5xx bisa berbentuk halaman HTML, dan pola HTML-lah yang dulu menangkapnya duluan lalu melaporkannya sebagai "belum di-share".
* Spreadsheet dibuka **sekali per proses** (cache `_BOOK`). Sebelumnya `append_shadow()` dan `append_run()` masing-masing autentikasi + `open_by_key` sendiri: dua kali kesempatan kena 503 untuk pekerjaan yang sama.

Bonus yang ketahuan saat memperbaiki: `_worksheet()` dulu menangkap `Exception` apa pun sebelum memanggil `add_worksheet()`. Artinya satu 503 **saat mencari** sheet akan membuat job **membuat worksheet kedua bernama sama**, dan sejak hari itu baris harian terbelah dua tanpa ada yang sadar. Sekarang hanya `gspread.exceptions.WorksheetNotFound` yang boleh sampai ke sana.

**Yang belum diperbaiki:** kalau 5 percobaan habis, baris hari itu tetap hilang — tidak ada mekanisme tambal-belakangan. Menambalnya butuh state di luar job (§ "job ini tanpa state"), jadi ditunda sampai ada bukti 503 sanggup bertahan lebih dari 30 detik.

### Catatan bug #4 — bukti dari riwayat repo sendiri

Run `31967010866` (16 Agt 2026) di runner GitHub Actions gagal dengan:

```
451 Client Error: for url: https://api.binance.com/api/v3/exchangeInfo
```

HTTP **451** = *Unavailable For Legal Reasons* — geo-block. Runner GitHub berlokasi di AS.

Skrip V1.2 sempat melapor *"api.binance.com is reachable"* tepat sebelum gagal: koneksi TCP nyambung, panggilan API-nya yang ditolak. **Probe-nya menguji hal yang salah.**

Perbaikan: `data-api.binance.vision` jadi host utama (endpoint data-publik resmi Binance, tanpa batasan wilayah), dengan fallback berurutan. Kesetaraan data dibuktikan gerbang v1.4.2 yang tetap lolos lewat host baru.

### Catatan bug #9 — yang paling konsekuensial

Pesan Telegram sungguhan menyuruh membeli **73.8% (BTC) + 56.9% (ETH) = 130.7% ekuitas**. Mustahil di spot tanpa leverage.

Sebabnya: `position_size_frac()` membatasi **satu** posisi di 100%, tapi tidak ada yang membatasi **jumlahnya**. Ukuran = 3% risk ÷ jarak SL, dan jarak SL median cuma ~7% — satu posisi saja median 42% ekuitas.

**Tidak mungkin tertangkap gerbang v1.4.2**: `engine.py` tidak pernah memodelkan ukuran posisi, ia hanya menghasilkan R-multiple. Cap 100% hidup di §2.6 sebagai hasil analisis spot-vs-perp, tapi tidak pernah ikut diport.

Butuh sinyal ganda pertama dalam 299 hari untuk memunculkannya.

Perbaikan `pipeline.apply_exposure_cap()` — alokasi kronologis, melepas ekuitas saat posisi exit, memotong posisi baru ke sisa yang ada (§2.6: *"ambil sisa yang ada; jangan lewati sinyal, jangan naikkan risk"*).

Diverifikasi terhadap tolok ukur §2.6:

```
eksposur puncak 2019-2026 : 100.0%  (tidak pernah bocor)
trade dipotong            : 44/298 = 14.8%   (acuan spec 43/298 = 14.4%)
```

Kasus 22 Agt setelah perbaikan: ETH 56.9% (mom_28 tertinggi, penuh) + BTC 43.1% (sisa) = **100.0%**.

---

## 8. Keadaan sekarang

### Cron aktif

| | |
|---|---|
| Jadwal | **00:05 UTC = 07:05 WIB** |
| Gerbang v1.4.3 | 7 hari berturut tanpa kegagalan job |
| Pemeriksa otomatis | Routine cloud, **01:00 UTC = 08:00 WIB**, baca-saja |

Ritme hariannya: lilin tutup 07:00 WIB → sinyal dihitung → Telegram masuk 07:05 WIB → harga acuan entry = harga 07:00 WIB.

### Kekeringan 299 hari baru berakhir

Sinyal terakhir sebelumnya **26 Oktober 2025**. Per 21 Agustus 2026 sistem sudah sepi **299 hari** — nyaris memecahkan rekornya sendiri (299 hari).

Sekarang BTC dan ETH dua-duanya memberi sinyal. **Tapi `mom_120` BTC cuma +0.10%** — baru menyentuh nol dari bawah. Sinyal setipis itu; jangan dibaca sebagai keyakinan tinggi.

### Status tes

```
sanity_tests.py            18/18
test_port_fidelity.py      PORT SETIA (+ cap eksposur)
test_binance_data.py       13/13
test_signal_equivalence.py SETARA (2.784 hari)
test_daily_job.py          LOLOS (termasuk escape HTML + coba-ulang 503)
test_replay_v142.py        LOLOS
gerbang                    nol TODO, §6.1 lengkap, nol kredensial ter-track
```

---

## 9. Batasan yang harus disebut bersamaan

1. **T8 gagal.** Tidak ada konfigurasi yang boleh disebut robust. 32.49x dan 13.45x boleh dikutip sebagai hasil backtest satu titik, tidak lebih.
2. **Konfigurasi produksi gagal kriteria 2** — tidak pernah mengalahkan BTC buy-and-hold. Diterima sadar atas dasar risk-adjusted (§3.1).
3. **Ekspektasi forward adalah mean R +0.151**, bukan +0.3182. Angka headline didominasi era 2019–2021.
4. **90 hari menghasilkan ~11 trade.** Untuk mendeteksi edge +0.151 dengan t=2 dibutuhkan ~312 trade ≈ 6,8 tahun. **v1.4.4 adalah uji infrastruktur, bukan uji strategi.**
5. **Bagian yang belum pernah dieksekusi masih berisiko.** Sepuluh bug muncul dengan pola konsisten: yang belum pernah dijalankan, patah. Cron harian otomatis belum pernah jalan.
6. **Sisi short tidak pernah diuji.** TSMOM di literatur biasanya dua arah; V1.1–V1.3 hanya menguji sisi beli. Bukan ditolak — memang tidak pernah masuk registry.
7. **Koreksi survivorship masih diblokir data.** Semua angka positif tetap batas atas.

---

## 10. Yang berikutnya

| Kapan | Apa |
|---|---|
| Tiap hari 08:00 WIB | Routine cloud melapor status cron dan hitungan X/7 |
| Setelah 7/7 | Gerbang v1.4.3 terpenuhi → mulai v1.4.4 (shadow log 90 hari, nol modal) |
| Setelah 90 hari | v1.4.5 — modal mikro, eksekusi manual |

**Paralel, jangan menunggu:** §7 spec secara khusus menyuruh menjalankan riset lanjutan bersamaan dengan v1.4.4. Menunggu 90 hari adalah 90 hari terbuang.

Riset yang masih terbuka ada di `V1_4_SPEC.md` §8 — termasuk desain ulang kriteria T8, dan SMA50 yang kini berstatus shadow (sudah terekam otomatis lewat `c2_pass`).

---

**Ditulis 22 Agustus 2026. Registry 26-tes tetap frozen; dokumen ini tidak menambah hipotesis dan tidak mengubah satu p-value pun.**
