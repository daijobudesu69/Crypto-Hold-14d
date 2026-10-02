# Crypto-Trade — forward test V1.4

Sinyal harian BTC/ETH, **spot, long-only, tanpa leverage**. Tahap saat ini: **v1.4.4 — shadow log 90 hari, nol modal, sedang berjalan.** Cron produksi hidup sejak 22 Agt 2026; jendela 90 hari dihitung dari **8 Sep 2026** (hari pertama sesudah perbaikan bug 76% sinyal hilang) sampai **~7 Des 2026**.

> **Ini bukan nasihat keuangan dan bukan sistem yang terbukti menguntungkan.**
> Grid robustness pre-registered (T8) **gagal**: hanya 16.7% sel bertahan, syaratnya 70%.
> Angka backtest 13.45x **tidak boleh** dibaca sebagai proyeksi. Detailnya di §Status.

---

## Apa yang dilakukan sistem ini

Tiap hari (dijadwalkan 00:05 UTC — lihat catatan keterlambatan di bawah), untuk BTC dan ETH:

1. Ambil lilin harian dari Binance (endpoint publik, **tanpa API key**)
2. Cek tiga syarat: harga hari ini lebih tinggi dari **28 hari lalu**, **dan** lebih tinggi dari **120 hari lalu**, dan likuiditas 20 hari ≥ $5 juta
3. Kalau lolos → kirim sinyal ke Telegram + catat ke Google Sheets

Tidak ada RSI, MACD, atau SMA sebagai penentu. Tidak ada regime filter. Tidak ada LLM.

**Keterlambatan cron.** Jadwalnya 00:05 UTC, tapi cron GitHub di produksi (Agt–Okt 2026) mulai **03:46–05:05 UTC** setiap hari, bukan "beberapa menit". Sinyal tidak berubah karenanya — acuan entry tetap OPEN 00:00 UTC — tapi harga pasar saat pesan sampai sudah bergerak. Sejak versi ini selisih itu **diukur**: pesan sinyal menyebut harga saat dikirim, dan sheet `entries` mencatat jam kirim, harga saat kirim, drift (%), dan lag (menit) per sinyal.

**Eksekusi manual.** Repo ini **tidak pernah** memasang order. Tidak ada API key dengan izin trading di mana pun dalam desainnya.

## Mengapa sesederhana ini

Tiga putaran riset (V1.1–V1.3) menguji 26 hipotesis terdaftar dengan koreksi Benjamini-Hochberg. Yang bertahan cuma momentum dua kaki pada BTC/ETH. Yang gagal — dan karenanya **dilarang ditambahkan** — ada di `V1_4_SPEC.md` §2.3: gate RSI, gate ekstensi, filter volatilitas, RS-vs-BTC, semua makro (DXY, real yield, M2, Nasdaq), ETF flow, funding rate, seleksi altcoin, dan regime filter berbasis SMA.

Menambahkan salah satunya berarti mengulang pekerjaan yang sudah terbukti gagal.

## Struktur

| Berkas | Isi |
|---|---|
| `src/config_v14.py` | **Semua** konstanta produksi, hard-coded. Nol TODO. `validate()` jalan saat import |
| `src/features.py` | Indikator. **Diport byte-identical** dari V1.3 |
| `src/engine.py` | Event loop backtest. **Diport byte-identical** dari V1.3 |
| `src/panel_v14.py` | Kolom yang di V1.3 lahir di orkestrator riset (`mom_120`, gate universe, `next_open`) |
| `tests/sanity_tests.py` | 18 tes: 13 engine, 4 kausalitas fitur, 1 integritas config |
| `src/binance_data.py` | Ambil lilin harian, endpoint publik tanpa API key. Membuang lilin yang belum tutup |
| `src/pipeline.py` | Jalur produksi: data -> panel -> trade. Satu-satunya tempat sinyal lahir |
| `tests/test_port_fidelity.py` | Membuktikan kode yang diport mereproduksi 298 trade / mean R 0.3182 |
| `tests/test_binance_data.py` | Lilin belum tutup, rentang tanggal, paginasi, lubang data |
| `tests/test_replay_v142.py` | **Gerbang v1.4.2** — replay 2019-2026 lewat pipa produksi |
| `src/shadow.py` | Kolom shadow §2.4 — dicatat, **tidak pernah** memblokir |
| `src/notify.py` | Pesan Telegram: sinyal masuk (dengan harga OCO), alarm hari ke-13, heartbeat, error |
| `src/sheets.py` | Google Sheets: `shadow_log` (kandidat harian), `runs` (bukti cron hidup), `entries` (jam & harga saat sinyal dikirim), `trades` (buku besar forward test — ditulis ulang penuh tiap hari dari replay) |
| `src/yearly_report.py` | P&L backtest per tahun (workflow *Laporan backtest per tahun*, hanya jendela ≤ `BACKTEST_END`) |
| `src/daily_job.py` | Orkestrator cron harian. **Tanpa state** — replay penuh tiap hari |
| `tests/test_signal_equivalence.py` | Membuktikan seleksi sinyal live == engine, 2.784 hari |
| `tests/test_daily_job.py` | Bentuk pesan, mode kering, pengaman kredensial, pencatatan `entries` |
| `tests/test_collect.py` | `collect()` offline di hari tanpa posisi, dengan posisi, dan hari alarm |
| `tests/test_daily_state.py` | Rantai jendela harian == engine (penjaga bug 76% sinyal hilang) |
| `tests/run_all.py` | Jalankan semua tes + periksa gerbang |

`features.py` dan `engine.py` **tidak boleh ditulis ulang** (`V1_4_SPEC.md` §2.2). Gerbang v1.4.2 mengharuskan pipa ini mereproduksi backtest persis; menulis ulang berarti menguji sistem yang berbeda.

## Menyalakan (v1.4.3)

> Sudah dilakukan untuk repo ini — empat secret terpasang dan cron jalan sejak 22 Agt 2026. Bagian ini untuk memasang ulang atau fork.

Tanpa kredensial, semuanya jalan dalam **mode kering**: pesan Telegram dan baris Sheets dibentuk lengkap lalu dicetak ke layar, tidak dikirim ke mana pun.

```bash
DRY_RUN=1 PYTHONPATH=src python src/daily_job.py
```

Untuk menyalakan sungguhan, pasang empat secret di **Settings → Secrets and variables → Actions**:

| Secret | Dari |
|---|---|
| `TELEGRAM_BOT_TOKEN` | @BotFather |
| `TELEGRAM_CHAT_ID` | `api.telegram.org/bot<TOKEN>/getUpdates` setelah mengirim pesan ke bot |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Isi **utuh** file JSON service account, bukan path-nya |
| `SHEET_ID` | Bagian tengah URL spreadsheet |

Dua hal yang paling sering tersendat: spreadsheet harus **di-share** ke email service account (`...@....iam.gserviceaccount.com`) sebagai Editor, dan **Google Sheets API harus di-enable** di project Google Cloud-nya. Membuat service account saja tidak cukup.

## Menjalankan tes

```bash
python tests/sanity_tests.py
```

```bash
python tests/test_port_fidelity.py
```

Atau semuanya sekaligus: `python tests/run_all.py`.

Tes yang butuh data historis otomatis di-skip kalau data historis tidak ada — CSV masuk `.gitignore`, jadi repo publik ini tidak membawa data apa pun.

## Keamanan (repo publik)

Empat aturan tidak bisa ditawar, dari `V1_4_SPEC.md` §6.1:

1. **Jangan pernah pakai `pull_request_target`** di workflow mana pun. Itu satu-satunya trigger yang memberi secret ke kode dari fork — jalur pencurian kredensial yang sudah terdokumentasi. `pull_request` biasa aman
2. `.gitignore` wajib lengkap **sebelum commit pertama**. Service-account JSON Google dan bot token Telegram adalah kredensial penuh; sekali ter-commit ke repo publik, harus dianggap **bocor selamanya** — riwayat git tetap menyimpannya walau filenya dihapus
3. Spreadsheet ID, Telegram chat ID, dan isi shadow log **tidak boleh masuk repo** — bukan kredensial, tapi mengungkap posisi terbuka dan ukurannya secara real-time
4. Nyalakan **GitHub Secret Scanning + Push Protection**

Semua secret hidup di GitHub Actions Secrets, tidak pernah di repo.

Strateginya sendiri terbuka dan memang tidak apa-apa: time-series momentum sudah dipublikasikan luas (Moskowitz-Ooi-Pedersen, JFE 2012). Yang dijaga adalah kredensial dan posisi, bukan logikanya.

## Status & ekspektasi jujur

| Hal | Angka |
|---|---|
| Backtest 2019–2026 | 298 trade, mean R +0.3182, 13.45x, maxDD −27.0% |
| **Ekspektasi forward** | **mean R +0.151** (era 2024–26), **bukan** +0.3182 |
| Frekuensi | 3.82 trade/bulan; **48.5% hari tanpa posisi** |
| Jeda terpanjang tanpa sinyal | **299 hari** — itu **bukan** kerusakan |
| Kekeringan sinyal terakhir | 26 Okt 2025 → **21 Agt 2026** (299 hari, menyamai rekor) — berakhir dengan sinyal BTC + ETH sekaligus |
| Kriteria pre-registered | Lolos 3 dari 4. **Gagal** kriteria return ≥ BTC buy-and-hold |
| Grid robustness T8 | **GAGAL — 16.7%**, syarat ≥70% |

Sistem lebih sering rugi daripada untung (win rate 51.7%); yang membuatnya positif adalah menangnya hampir 2× lebih besar dari kalahnya.

Forward test 90 hari menghasilkan ~11 trade. Untuk mendeteksi edge sebesar +0.151 dengan t=2 dibutuhkan ~312 trade ≈ 6.8 tahun. **Karena itu v1.4.4 adalah uji infrastruktur, bukan uji strategi.** Kalau di akhir 90 hari pertanyaannya "jadi untung atau tidak?", pertanyaannya salah.

## Rencana

| Versi | Isi | Status |
|---|---|---|
| v1.4.0 | Perbaikan V1.3, tes no-lookahead, T8 | ✅ Selesai (T8 gagal, dicatat) |
| v1.4.1 | Repo, port, config | ✅ Selesai |
| v1.4.2 | Replay 2019–2026, harus persis 298 trade | ✅ Selesai — lolos, identik bit-per-bit |
| v1.4.3 | Cron harian, Sheets, Telegram, OCO + alarm hari ke-13 | ✅ Hidup sejak 22 Agt 2026. Gagal 2× (27 Agt, 5 Sep — Sheets 503, diperbaiki); hijau berturut sejak 6 Sep |
| **v1.4.4** | Shadow log 90 hari, **nol modal** | **Berjalan** — 8 Sep → ~7 Des 2026 |
| v1.4.5 | Modal mikro, eksekusi manual | |

## Dokumen

| Berkas | Isi |
|---|---|
| [`docs/VERSION_1_4_BUILD_LOG.md`](docs/VERSION_1_4_BUILD_LOG.md) | **Catatan pembangunan v1.4.0-v1.4.3** — hasil T8, revisi spec, 9 bug yang ditemukan dan cara masing-masing ketemu |
| `V1_4_SPEC.md` | Spesifikasi produksi (di luar repo) |
| `results/KOREKSI_V1_3.md` | Dua cacat implementasi V1.3 (di luar repo) |
| `results/V1_4_0_RESULTS.md` | Hasil lengkap v1.4.0 (di luar repo) |
