"""Tulis shadow log ke Google Sheets. Mode kering kalau kredensial tidak ada.

Dua sheet:
  shadow_log  satu baris per kandidat per hari — TERMASUK yang tidak jadi trade.
              Inilah data yang 6-12 bulan lagi diuji dengan BH-FDR yang benar.
  runs        satu baris per eksekusi job — untuk membuktikan cron benar-benar
              jalan tiap hari, dan membedakan "tidak ada sinyal" dari "job mati".

gspread diimpor MALAS (di dalam fungsi) supaya mode kering tetap jalan di mesin
yang tidak memasang paketnya — penting untuk tes dan untuk pengembangan lokal.

Kredensial dibaca dari environment. SHEET_ID ikut jadi secret walau bukan
kredensial: ia mengungkap posisi terbuka dan ukurannya secara real-time
(§6.1 aturan 3).

--------------------------------------------------------------------------------
KENAPA ADA COBA-ULANG DI SINI (v1.4.3, sesudah HTTP 503 pertama di produksi)

Google Sheets sesekali membalas 500/502/503/504 — server sedang bermasalah
beberapa detik, lalu pulih sendiri. notify.py sudah mencoba ulang sejak awal;
sheets.py tidak, jadi satu kedipan 503 cukup untuk membuang baris shadow_log
hari itu. Baris itu TIDAK BISA diambil lagi: job berikutnya menulis hari
berikutnya, tidak pernah menambal hari yang bolong. Lubang di forward test yang
dirancang berjalan bertahun-tahun adalah kerugian permanen, dan penyebabnya
cuma gangguan beberapa detik.

Sama pentingnya: 503 dulu didiagnosis SALAH. Semua kegagalan open_by_key
diterjemahkan jadi "spreadsheet belum di-share / SHEET_ID salah", lengkap dengan
email service account — sehingga gangguan sementara di pihak Google terbaca
seperti kesalahan konfigurasi, dan waktu habis memeriksa share dan ID yang
sebenarnya sudah benar. Diagnosis yang salah arah lebih mahal daripada tidak ada
diagnosis sama sekali.
--------------------------------------------------------------------------------
"""
from __future__ import annotations
import json
import os
import random
import re
import time
from datetime import datetime, timezone

import config_v14 as cfg

SHADOW_SHEET = "shadow_log"
RUNS_SHEET = "runs"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHADOW_HEADER = (["date", "symbol"] + list(cfg.SHADOW_COLUMNS)
                 + [f"mom_{cfg.MOM_SHORT_DAYS}", f"mom_{cfg.MOM_LONG_DAYS}",
                    "close", "atr14", "med_qvol_20", "days_listed", "tsmom_pos"])
RUNS_HEADER = ["run_utc", "data_through", "n_signals", "n_open_positions",
               "n_shadow_rows", "n_alarms", "status", "note"]

# Sheet terpisah, bukan kolom tambahan di `runs`: menambah kolom ke sheet yang
# sudah berisi baris berarti judul lama tidak punya kolom itu, dan _worksheet()
# hanya menulis judul ke sheet kosong.
#
# Satu baris per sinyal masuk. Mengukur jarak antara harga MODEL (open 00:00
# UTC, yang dicatat shadow log dan dipakai backtest) dan harga yang tersedia
# saat pesan benar-benar sampai -- cron 00:05 UTC di produksi jalan jam
# 03:46-05:05 UTC. Tanpa sheet ini selisih itu tidak terukur sampai v1.4.5.
ENTRIES_SHEET = "entries"
ENTRIES_HEADER = ["sent_utc", "symbol", "signal_date", "entry_date",
                  "model_entry_px", "px_at_send", "drift_pct", "lag_min",
                  "oco_stop_loss", "oco_take_profit", "size_frac_used", "sent_ok"]

# Status HTTP yang artinya "coba lagi nanti", bukan "konfigurasimu salah".
#   429              kuota per menit terlampaui
#   500/502/503/504  backend Google sedang bermasalah
TRANSIENT_HTTP = frozenset({429, 500, 502, 503, 504})

# 5 percobaan dengan jeda 2, 4, 8, 16 detik (+jitter) = menunggu ~30 detik total.
# Gangguan 503 Google praktis selalu selesai jauh di bawah itu, sementara job ini
# punya jatah 15 menit di GitHub Actions — jadi menunggu tidak berbiaya.
MAX_ATTEMPTS = 5
BASE_DELAY = 2.0        # detik; tes menurunkannya ke 0 supaya tidak ikut menunggu


class SheetsUnavailable(RuntimeError):
    """Google Sheets tidak bisa dihubungi walau sudah dicoba ulang.

    Kelas terpisah supaya pesan di Telegram membedakannya dari salah konfigurasi:
    yang satu berarti "tunggu, Google sedang sakit", yang lain berarti "ada yang
    harus kamu perbaiki".
    """


def credentials() -> tuple[str | None, str | None]:
    return os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"), os.environ.get("SHEET_ID")


def is_dry_run() -> bool:
    if os.environ.get("DRY_RUN", "").strip() not in ("", "0", "false", "False"):
        return True
    sa, sheet_id = credentials()
    return not (sa and sheet_id)


def check_sheet_id(sheet_id: str) -> str | None:
    """Kesalahan format SHEET_ID yang bisa dideteksi sebelum memanggil API.

    Google membalas kesalahan ini dengan HALAMAN HTML, bukan pesan error yang
    berguna — jadi lebih baik ditangkap di sini.
    """
    if not sheet_id:
        return "SHEET_ID kosong"
    s = sheet_id.strip()
    if s != sheet_id:
        return "SHEET_ID punya spasi/baris baru di ujung — salin ulang tanpa spasi"
    if s.startswith("http"):
        return ("SHEET_ID berisi URL lengkap. Ambil HANYA bagian di antara "
                "'/d/' dan '/edit', misal 1AbCdEf...XyZ")
    if "/" in s:
        return "SHEET_ID mengandung '/' — itu potongan URL, bukan ID-nya"
    if len(s) < 30:
        return f"SHEET_ID cuma {len(s)} karakter; ID Google Sheets biasanya ~44"
    return None


def http_status(err: Exception) -> int | None:
    """Status HTTP di balik sebuah kegagalan gspread, kalau ada.

    gspread.APIError menyimpan respons aslinya; kalau tidak ada, statusnya masih
    bisa dibaca dari teks "APIError: [503]: ...". Membaca teks memang rapuh, tapi
    di sini rapuh berarti "jatuh ke jalur non-sementara" — aman, bukan salah.
    """
    code = getattr(getattr(err, "response", None), "status_code", None)
    if isinstance(code, int):
        return code
    m = re.search(r"\[(\d{3})\]", str(err))
    return int(m.group(1)) if m else None


def is_transient(err: Exception) -> bool:
    """True kalau kegagalan ini pantas dicoba ulang apa adanya.

    Yang TIDAK boleh masuk sini: 401/403 (izin), 404 (ID salah), JSON rusak.
    Mencoba ulang kesalahan konfigurasi cuma menunda pesan error 30 detik.
    """
    code = http_status(err)
    if code is not None:
        return code in TRANSIENT_HTTP
    t = str(err).lower()
    return any(k in t for k in (
        "timed out", "timeout", "connection reset", "connection aborted",
        "connection refused", "remote end closed", "temporarily unavailable",
        "service is currently unavailable", "eof occurred"))


def _ringkas(err: Exception) -> str:
    """Satu baris pendek untuk log. Balasan Google bisa satu halaman HTML penuh."""
    return " ".join(str(err).split())[:160]


def _total_wait() -> int:
    return int(sum(BASE_DELAY * 2 ** i for i in range(MAX_ATTEMPTS - 1)))


def _retry(what: str, fn):
    """Jalankan panggilan Sheets, ulangi selama kegagalannya sementara.

    Kegagalan non-sementara dilempar apa adanya supaya _client() dan pemanggil
    lain bisa mendiagnosisnya — coba-ulang tidak boleh mengaburkan salah
    konfigurasi, dan tidak boleh menunda pesan error yang sudah pasti benar.
    """
    terakhir: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as e:
            if not is_transient(e):
                raise
            terakhir = e
            if attempt == MAX_ATTEMPTS:
                break
            jeda = BASE_DELAY * 2 ** (attempt - 1) + random.uniform(0, 1)
            print(f"  Sheets {what}: {_ringkas(e)}")
            print(f"    gangguan sementara di sisi Google — percobaan "
                  f"{attempt}/{MAX_ATTEMPTS} gagal, ulangi dalam {jeda:.1f}s")
            time.sleep(jeda)
    raise SheetsUnavailable(
        f"{what}: Google Sheets tidak merespons setelah {MAX_ATTEMPTS} percobaan "
        f"(~{_total_wait()}s).\n"
        f"  {_ringkas(terakhir) if terakhir else ''}\n"
        "  Ini gangguan di sisi Google, BUKAN salah share/SHEET_ID. Konfigurasi "
        "tidak perlu disentuh; jalankan ulang workflow kalau baris hari ini "
        "penting.") from None


def _diagnose(err: Exception, sheet_id: str) -> str:
    """Terjemahkan kegagalan gspread jadi sebab dan tindakan yang jelas.

    Google sering membalas dengan halaman HTML lengkap saat spreadsheet tidak
    bisa dibuka. Menyalin halaman itu ke log dan ke pesan Telegram membuat
    penyebab sebenarnya tenggelam di ribuan karakter CSS.
    """
    t = str(err)
    # Cek sementara HARUS pertama: balasan 5xx pun bisa berupa halaman HTML, dan
    # kalau pola HTML di bawah menangkapnya duluan, gangguan beberapa detik di
    # pihak Google akan dilaporkan sebagai "belum di-share" — persis yang terjadi
    # pada 503 pertama di produksi.
    code = http_status(err)
    if code in TRANSIENT_HTTP:
        return (f"Google membalas HTTP {code} — server Sheets sedang bermasalah "
                "atau kuota per menit terlampaui. Ini SEMENTARA dan bukan salah "
                "konfigurasi: share dan SHEET_ID tidak perlu diubah.")
    html_page = "<!DOCTYPE html" in t or "<html" in t
    if html_page or "unable to open the file" in t or "Page Not Found" in t:
        return ("Google membalas halaman 'tidak bisa membuka file'. Dua sebab, "
                "urut dari yang paling sering:\n"
                "  1. Spreadsheet BELUM di-share ke email service account "
                "(client_email di file JSON) sebagai Editor\n"
                "  2. SHEET_ID salah — harus bagian antara '/d/' dan '/edit', "
                "bukan URL lengkap")
    if "has not been used in project" in t or "SERVICE_DISABLED" in t:
        return ("Google Sheets API belum di-enable di project Google Cloud yang "
                "memiliki service account ini.")
    if "PERMISSION_DENIED" in t or code == 403:
        return ("Service account tidak punya izin tulis. Share spreadsheet "
                "sebagai EDITOR, bukan Viewer.")
    if "invalid_grant" in t or "JWT" in t:
        return ("Kredensial service account ditolak. Kemungkinan key sudah "
                "di-revoke, atau isi JSON kepotong saat ditempel.")
    return t[:300]


# Spreadsheet dibuka sekali per proses. append_shadow() dan append_run() sama-
# sama memanggil _client(); tanpa cache ini satu job melakukan dua kali
# autentikasi + open_by_key, artinya dua kali kesempatan kena 503 untuk pekerjaan
# yang sama.
_BOOK: tuple[str, object] | None = None


def reset_client() -> None:
    """Buang spreadsheet yang di-cache. Dipakai tes yang mengganti environment."""
    global _BOOK
    _BOOK = None


def _client():
    """Buka spreadsheet. Diimpor malas — lihat docstring modul."""
    global _BOOK
    import gspread
    from google.oauth2.service_account import Credentials

    sa_raw, sheet_id = credentials()
    salah = check_sheet_id(sheet_id or "")
    if salah:
        raise RuntimeError(salah)
    if _BOOK is not None and _BOOK[0] == sheet_id:
        return _BOOK[1]
    try:
        info = json.loads(sa_raw)
    except json.JSONDecodeError as e:
        # jangan pernah cetak sa_raw: itu kunci privat penuh
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON bukan JSON yang sah. Tempelkan ISI file "
            f"JSON-nya secara utuh, bukan path-nya. ({e.msg})") from None
    email = info.get("client_email", "(client_email tidak ada di JSON)")
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    gc = gspread.authorize(creds)
    try:
        book = _retry("buka spreadsheet", lambda: gc.open_by_key(sheet_id))
    except SheetsUnavailable:
        # Pesannya sudah benar dan sudah menyatakan ini bukan salah konfigurasi.
        # Menempelkan petunjuk share/SHEET_ID di sini justru yang menyesatkan.
        raise
    except Exception as e:
        raise RuntimeError(
            f"tidak bisa membuka spreadsheet.\n{_diagnose(e, sheet_id)}\n"
            f"  Email yang harus di-share: {email}\n"
            f"  SHEET_ID dipakai: {sheet_id[:8]}...{sheet_id[-4:]} "
            f"({len(sheet_id)} karakter)") from None
    _BOOK = (sheet_id, book)
    return book


# shadow_log tumbuh 3 baris/hari (BTC + ETH + SOL). Default gspread 1000 baris
# habis dalam ~11 bulan, dan forward test ini dirancang berjalan bertahun-tahun.
# Kegagalannya akan muncul jauh di kemudian hari, dalam bentuk baris yang diam-
# diam tidak tertulis -- persis jenis kegagalan senyap yang paling mahal di sini.
# 20.000 baris cukup untuk ~18 tahun dan tidak memakan kuota apa pun kalau kosong.
INITIAL_ROWS = 20_000


def _worksheet(book, title: str, header: list[str]):
    """Ambil worksheet, buat kalau belum ada, dan pastikan barisnya berjudul."""
    import gspread

    try:
        ws = _retry(f"cari sheet '{title}'", lambda: book.worksheet(title))
    except gspread.exceptions.WorksheetNotFound:
        # HANYA "memang belum ada" yang boleh sampai ke add_worksheet. Dulu blok
        # ini menangkap Exception apa pun, jadi satu 503 saat mencari sheet akan
        # membuat job MEMBUAT SHEET KEDUA bernama sama — dan sejak itu baris
        # harian terbelah dua tanpa ada yang sadar.
        ws = _retry(f"buat sheet '{title}'", lambda: book.add_worksheet(
            title=title, rows=INITIAL_ROWS, cols=max(len(header), 26)))
        _retry(f"tulis judul '{title}'",
               lambda: ws.append_row(header, value_input_option="RAW"))
        return ws
    if not _retry(f"baca judul '{title}'", lambda: ws.row_values(1)):
        _retry(f"tulis judul '{title}'",
               lambda: ws.append_row(header, value_input_option="RAW"))
    return ws


def _rows_to_lists(rows: list[dict], header: list[str]) -> list[list]:
    out = []
    for r in rows:
        out.append(["" if r.get(k) is None else r.get(k) for k in header])
    return out


def append_shadow(rows: list[dict]) -> bool:
    """Tambahkan baris shadow log. True kalau berhasil (atau tercetak saat kering)."""
    if not rows:
        return True
    data = _rows_to_lists(rows, SHADOW_HEADER)
    if is_dry_run():
        print(f"--- GOOGLE SHEETS '{SHADOW_SHEET}' (MODE KERING, tidak ditulis) ---")
        print("  " + " | ".join(SHADOW_HEADER))
        for r in data:
            print("  " + " | ".join("" if v == "" else
                                    (f"{v:.6g}" if isinstance(v, float) else str(v))
                                    for v in r))
        print("-" * 70)
        return True
    book = _client()
    ws = _worksheet(book, SHADOW_SHEET, SHADOW_HEADER)
    _retry(f"tulis {len(data)} baris ke '{SHADOW_SHEET}'",
           lambda: ws.append_rows(data, value_input_option="RAW"))
    return True


def append_entries(rows: list[dict]) -> bool:
    """Catat waktu kirim + harga saat kirim tiap sinyal masuk (lihat ENTRIES_HEADER)."""
    if not rows:
        return True
    data = _rows_to_lists(rows, ENTRIES_HEADER)
    if is_dry_run():
        print(f"--- GOOGLE SHEETS '{ENTRIES_SHEET}' (MODE KERING, tidak ditulis) ---")
        print("  " + " | ".join(ENTRIES_HEADER))
        for r in data:
            print("  " + " | ".join("" if v == "" else
                                    (f"{v:.6g}" if isinstance(v, float) else str(v))
                                    for v in r))
        print("-" * 70)
        return True
    book = _client()
    ws = _worksheet(book, ENTRIES_SHEET, ENTRIES_HEADER)
    _retry(f"tulis {len(data)} baris ke '{ENTRIES_SHEET}'",
           lambda: ws.append_rows(data, value_input_option="RAW"))
    return True


def append_run(status: str, data_through: str, n_signals: int, n_open: int,
               n_shadow: int, n_alarms: int, note: str = "") -> bool:
    """Catat satu eksekusi job. Inilah bukti cron hidup — tanpa ini, 'tidak ada
    sinyal' dan 'job mati' terlihat sama persis di Sheets."""
    row = [datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), data_through,
           n_signals, n_open, n_shadow, n_alarms, status, note]
    if is_dry_run():
        print(f"--- GOOGLE SHEETS '{RUNS_SHEET}' (MODE KERING, tidak ditulis) ---")
        print("  " + " | ".join(RUNS_HEADER))
        print("  " + " | ".join(str(v) for v in row))
        print("-" * 70)
        return True
    book = _client()
    ws = _worksheet(book, RUNS_SHEET, RUNS_HEADER)
    _retry(f"tulis 1 baris ke '{RUNS_SHEET}'",
           lambda: ws.append_row(row, value_input_option="RAW"))
    return True
