"""Tes job harian: bentuk pesan, mode kering, dan pengaman kredensial.

Yang diuji di sini adalah hal-hal yang kalau salah tidak akan terlihat sampai
uang sungguhan bergerak — atau sampai kredensial bocor.
"""
from __future__ import annotations
import os, sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import config_v14 as cfg
import notify
import sheets

ok = True


def cek(nama: str, kondisi: bool, ket: str = "") -> None:
    global ok
    print(f"  {'PASS' if kondisi else 'GAGAL'}  {nama}" + (f"  [{ket}]" if ket else ""))
    ok = ok and kondisi


# Fixture DIHITUNG dari config, tidak diketik tangan. Angka yang diketik dari
# tampilan sudah terlanjur dibulatkan dan membuat tes gagal karena selisih 0.01
# pada fixture-nya sendiri, bukan pada kodenya.
_PX, _ATR = 78338.03, 2124.27
_SL, _TP = cfg.barriers(_PX, _ATR)
_WANT, _USED = cfg.position_size_frac(_PX, _ATR)
TRADE = {"symbol": "BTC", "signal_date": "2026-08-21", "entry_date": "2026-08-22",
         "entry_px": _PX, "atr14": _ATR,
         "oco_stop_loss": _SL, "oco_take_profit": _TP,
         "size_frac_wanted": _WANT, "size_frac_used": _USED, "size_frac": _USED / _WANT,
         "hold_warning_date": "2026-09-03", "hold_force_exit_date": "2026-09-04"}

print("=== 1. Pesan masuk WAJIB memuat harga OCO (gerbang v1.4.3) ===")
m = notify.entry_message(TRADE)
cek("memuat harga Take Profit", f"{_TP:,.2f}" in m, f"{_TP:,.2f}")
cek("memuat harga Stop Loss", f"{_SL:,.2f}" in m, f"{_SL:,.2f}")
cek("memuat harga entry", f"{_PX:,.2f}" in m)
cek("memuat ukuran posisi", f"{100*_USED:,.1f}" in m)
cek("memuat tanggal tutup paksa", "2026-09-04" in m)
cek("menyebut OCO secara eksplisit", "OCO" in m)
cek("menyatakan ini forward test tanpa modal", "tanpa modal" in m.lower())

print("\n=== 2. Barrier di pesan berasal dari config, bukan angka lepas ===")
cek("SL = entry - 1.5xATR", abs(_SL - (_PX - cfg.SL_ATR_MULT * _ATR)) < 1e-9, f"{_SL:.2f}")
cek("TP = entry + 3.0xATR", abs(_TP - (_PX + cfg.TP_ATR_MULT * _ATR)) < 1e-9, f"{_TP:.2f}")
cek("jarak TP tepat 2x jarak SL", abs((_TP - _PX) - 2 * (_PX - _SL)) < 1e-9)
cek("ukuran = risk / jarak SL",
    abs(_WANT - cfg.RISK_PER_TRADE / (cfg.SL_ATR_MULT * _ATR / _PX)) < 1e-12, f"{_USED:.4f}")
cek("ukuran tidak pernah lewat 100% ekuitas (spot, tanpa leverage)",
    cfg.position_size_frac(100.0, 0.5)[1] <= cfg.MAX_EXPOSURE_FRAC)

print("\n=== 3. Alarm hari ke-13 ===")
POS = dict(TRADE, days_held=13, entry_date="2026-08-22")
a = notify.hold_alarm_message(POS)
cek("menyebut hari ke-13", "hari ke-13" in a)
cek("menyuruh tutup besok", "tutup paksa" in a.lower())
cek("mengingatkan membatalkan OCO", "batalkan" in a.lower())

print("\n=== 4. Heartbeat tetap terkirim walau tidak ada apa-apa ===")
h = notify.heartbeat_message({
    "run_date": "2026-08-22", "run_time": "00:05", "data_through": "2026-08-21",
    "open_positions": [], "n_signals": 0, "n_shadow_rows": 3,
    "last_signal_date": "2025-10-26", "days_since_last_signal": 299})
cek("menyatakan posisi terbuka tidak ada", "tidak ada" in h)
cek("melaporkan sinyal nol", "Sinyal hari ini: <b>0</b>" in h)
cek("menenangkan saat sepi panjang", "bukan berarti rusak" in h,
    "penting: 299 hari sepi itu normal secara historis")

print("\n=== 5. Mode kering aktif kalau kredensial tidak ada ===")
simpan = {k: os.environ.pop(k, None) for k in
          ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GOOGLE_SERVICE_ACCOUNT_JSON",
           "SHEET_ID", "DRY_RUN")}
try:
    cek("Telegram kering tanpa kredensial", notify.is_dry_run())
    cek("Sheets kering tanpa kredensial", sheets.is_dry_run())
    os.environ["TELEGRAM_BOT_TOKEN"] = "x"
    cek("token saja belum cukup (chat id masih kosong)", notify.is_dry_run())
    os.environ["TELEGRAM_CHAT_ID"] = "y"
    cek("token + chat id -> mode basah", not notify.is_dry_run())
    os.environ["DRY_RUN"] = "1"
    cek("DRY_RUN=1 memaksa kering walau kredensial lengkap", notify.is_dry_run())
finally:
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DRY_RUN"):
        os.environ.pop(k, None)
    for k, v in simpan.items():
        if v is not None:
            os.environ[k] = v

print("\n=== 6. Kredensial tidak boleh bocor ke log ===")
import inspect
src_notify = inspect.getsource(notify)
src_sheets = inspect.getsource(sheets)
cek("notify tidak pernah mencetak isi respons mentah",
    "print(r.text" not in src_notify and "print(r.content" not in src_notify)
cek("notify tidak pernah mencetak token atau URL berisi token",
    "print(token" not in src_notify and "print(API.format" not in src_notify)
cek("sheets tidak pernah mencetak isi service account",
    "print(sa_raw" not in src_sheets and "{sa_raw}" not in src_sheets)
cek("header shadow_log cocok dengan kolom §2.4",
    all(c in sheets.SHADOW_HEADER for c in cfg.SHADOW_COLUMNS),
    f"{len(sheets.SHADOW_HEADER)} kolom")

print("\n=== 7. Workflow: tidak ada pull_request_target (§6.1 aturan 1) ===")
wf_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".github", "workflows")
bad = []
for f in os.listdir(wf_dir):
    if f.endswith((".yml", ".yaml")):
        txt = open(os.path.join(wf_dir, f), encoding="utf-8").read()
        # abaikan baris komentar yang justru MELARANGnya
        aktif = [l for l in txt.splitlines()
                 if "pull_request_target" in l and not l.strip().startswith("#")]
        if aktif:
            bad.append(f)
cek("tidak ada workflow memakai pull_request_target", not bad, str(bad))

print("\n=== 8. Teks dinamis wajib di-escape sebelum masuk pesan HTML ===")
# Pesan dikirim parse_mode=HTML. Satu '<' yang lolos membuat Telegram menolak
# SELURUH pesan dengan 400 "can't parse entities" -- pesannya hilang tanpa jejak.
# Paling berbahaya di pesan ERROR: nama tipe exception Python berbentuk
# <class '...'>, jadi pemberitahuan kegagalan ikut gagal justru saat dibutuhkan.
JAHAT = "<script>&\"'"
TAG_SAH = ("<b>", "</b>", "<i>", "</i>")
KASUS = {
    "entry": lambda: notify.entry_message(dict(
        TRADE, symbol=JAHAT, signal_date=JAHAT,
        hold_warning_date=JAHAT, hold_force_exit_date=JAHAT)),
    "alarm": lambda: notify.hold_alarm_message(dict(
        TRADE, symbol=JAHAT, entry_date=JAHAT, days_held=13,
        hold_force_exit_date=JAHAT)),
    "heartbeat": lambda: notify.heartbeat_message({
        "run_date": JAHAT, "run_time": JAHAT, "data_through": JAHAT,
        "open_positions": [{"symbol": JAHAT, "days_held": 3, "entry_px": 1.0,
                            "oco_take_profit": 2.0, "oco_stop_loss": 0.5}],
        "n_signals": 0, "n_shadow_rows": 3,
        "last_signal_date": JAHAT, "days_since_last_signal": 299}),
    "error": lambda: notify.error_message("tarik data", "ValueError: <class 'x'> & <BTC>"),
}
for nama, fn in KASUS.items():
    sisa = fn()
    for t in TAG_SAH:
        sisa = sisa.replace(t, "")
    bocor = [c for c in ("<", ">") if c in sisa]
    cek(f"pesan {nama}: nol tag liar", not bocor, str(bocor) if bocor else "bersih")

print("\n=== 9. Jaring pengaman: kirim ulang sebagai teks polos ===")
# Kalau suatu saat ada field baru yang lupa di-escape, pesan harus tetap sampai
# tanpa huruf tebal -- lebih baik daripada tidak sampai sama sekali.
polos = notify._strip_tags("<b>SINYAL</b> &amp; <i>tes</i> &lt;BTC&gt;")
cek("tag dibuang", "<b>" not in polos and "<i>" not in polos, repr(polos))
cek("entitas dikembalikan ke bentuk asli", "&" in polos and "<BTC>" in polos)
cek("send() mencoba dua format", 'for parse_mode in ("HTML", None)' in inspect.getsource(notify.send))

print("\n=== 10. Google Sheets 503: dicoba ulang, dan didiagnosis dengan benar ===")
# Kejadian nyata di produksi: satu HTTP 503 (server Google, hilang beberapa
# detik) membuang baris shadow_log hari itu SELAMANYA, lalu dilaporkan sebagai
# "spreadsheet belum di-share / SHEET_ID salah" — sehingga yang diperiksa adalah
# konfigurasi yang sebenarnya sudah benar. Dua-duanya diuji di sini.
sheets.BASE_DELAY = 0.0          # jangan biarkan tes ikut menunggu 30 detik


class _Resp:
    def __init__(self, code): self.status_code = code


class _FakeAPIError(Exception):
    """Bentuk gspread.APIError: pesan "[kode]: teks" + respons dengan status."""
    def __init__(self, code, teks):
        super().__init__(f"APIError: [{code}]: {teks}")
        self.response = _Resp(code)


E503 = _FakeAPIError(503, "The service is currently unavailable.")
E403 = _FakeAPIError(403, "The caller does not have permission")
E404 = _FakeAPIError(404, "Requested entity was not found.")

cek("503 dikenali sementara", sheets.is_transient(E503))
cek("429 dikenali sementara", sheets.is_transient(_FakeAPIError(429, "Quota exceeded")))
cek("403 TIDAK dianggap sementara", not sheets.is_transient(E403))
cek("404 TIDAK dianggap sementara", not sheets.is_transient(E404))
# Tanpa objek respons, status masih terbaca dari teksnya.
cek("status terbaca dari teks saja",
    sheets.http_status(Exception("APIError: [503]: The service is currently unavailable.")) == 503)
cek("timeout jaringan dianggap sementara",
    sheets.is_transient(Exception("HTTPSConnectionPool: Read timed out.")))

panggilan = {"n": 0}


def _gagal_dua_kali():
    panggilan["n"] += 1
    if panggilan["n"] <= 2:
        raise E503
    return "berhasil"


cek("503 dicoba ulang sampai berhasil",
    sheets._retry("tes", _gagal_dua_kali) == "berhasil", f"{panggilan['n']} panggilan")

panggilan["n"] = 0


def _selalu_503():
    panggilan["n"] += 1
    raise E503


try:
    sheets._retry("tulis baris", _selalu_503)
    cek("503 terus-menerus akhirnya menyerah", False, "tidak melempar apa-apa")
except sheets.SheetsUnavailable as e:
    cek("503 terus-menerus akhirnya menyerah", True)
    cek("dicoba tepat MAX_ATTEMPTS kali", panggilan["n"] == sheets.MAX_ATTEMPTS,
        f"{panggilan['n']} panggilan")
    # Inti perbaikannya: pesan menyerah TIDAK BOLEH menuduh share/SHEET_ID.
    pesan = str(e)
    cek("pesan menyebut ini sisi Google", "sisi Google" in pesan)
    cek("pesan tidak menuduh share/SHEET_ID salah",
        "belum di-share" not in pesan and "SHEET_ID salah" not in pesan)
except Exception as e:
    cek("503 terus-menerus akhirnya menyerah", False, type(e).__name__)

panggilan["n"] = 0


def _403():
    panggilan["n"] += 1
    raise E403


try:
    sheets._retry("tes", _403)
except sheets.SheetsUnavailable:
    cek("salah izin tidak dicoba ulang", False, "malah jadi SheetsUnavailable")
except Exception:
    cek("salah izin dilempar apa adanya, tanpa coba ulang", panggilan["n"] == 1,
        f"{panggilan['n']} panggilan")

d503 = sheets._diagnose(E503, "x" * 44)
cek("diagnosis 503 menyebut SEMENTARA", "SEMENTARA" in d503)
cek("diagnosis 503 tidak menyuruh cek share",
    "BELUM di-share" not in d503 and "SHEET_ID salah" not in d503, d503[:60])
# 5xx dari frontend Google bisa datang sebagai halaman HTML. Kalau pola HTML
# menang duluan, gangguan sementara kembali terbaca sebagai salah konfigurasi.
d503_html = sheets._diagnose(
    _FakeAPIError(503, "<!DOCTYPE html><html><body>Service unavailable</body></html>"),
    "x" * 44)
cek("503 berbentuk halaman HTML tetap didiagnosis sementara",
    "SEMENTARA" in d503_html)
cek("403 tetap didiagnosis sebagai izin", "EDITOR" in sheets._diagnose(E403, "x" * 44))

src_sheets_now = inspect.getsource(sheets)
cek("semua penulisan Sheets lewat _retry",
    src_sheets_now.count("_retry(") >= 8,
    f"{src_sheets_now.count('_retry(')} pemakaian")
cek("worksheet hanya dibuat saat memang belum ada",
    "except gspread.exceptions.WorksheetNotFound" in src_sheets_now)

print()
print("=== 11. Sebab kegagalan wajib muncul sebagai annotation GitHub ===")
# Log mentah Actions disajikan dari blob storage yang sering diblokir kebijakan
# egress, jadi pemeriksa otomatis di luar GitHub cuma bisa membaca endpoint
# annotations -- dan endpoint itu dulu hanya berisi "Process completed with exit
# code 1". Pemeriksa harian 8 Sep 2026 karena itu menuduh HTTP 451 Binance untuk
# kegagalan yang sebenarnya HTTP 503 Google Sheets.
import contextlib
import io as _io

import daily_job


def _tangkap(env_ci, tahap, pesan):
    lama = os.environ.get("GITHUB_ACTIONS")
    if env_ci is None:
        os.environ.pop("GITHUB_ACTIONS", None)
    else:
        os.environ["GITHUB_ACTIONS"] = env_ci
    buf = _io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            daily_job._ci_error(tahap, pesan)
    finally:
        os.environ.pop("GITHUB_ACTIONS", None)
        if lama is not None:
            os.environ["GITHUB_ACTIONS"] = lama
    return buf.getvalue()


SEBAB = ("gagal tulis shadow_log: RuntimeError: tidak bisa membuka spreadsheet."
         + chr(10) + "APIError: [503]: 100% unavailable")
keluar = _tangkap("true", "pengiriman", SEBAB)
cek("annotation ditulis saat jalan di GitHub Actions", keluar.startswith("::error::"),
    keluar.strip()[:70])
cek("sebab sebenarnya ikut terbawa, bukan cuma exit code",
    "503" in keluar and "shadow_log" in keluar)
cek("tahap ikut disebut", "pengiriman" in keluar)
# Perintah workflow GitHub HARUS satu baris; newline mentah memotong pesannya.
cek("tepat satu baris", len(keluar.strip().splitlines()) == 1,
    f"{len(keluar.strip().splitlines())} baris")
cek("newline di-encode jadi %0A", "%0A" in keluar)
cek("persen di-encode jadi %25", "%25" in keluar)
cek("di luar GitHub Actions tidak mencetak apa-apa",
    _tangkap(None, "pengiriman", SEBAB) == ""
    and _tangkap("false", "pengiriman", SEBAB) == "")

src_job = inspect.getsource(daily_job)
cek("ketiga jalur gagal memanggil _ci_error", src_job.count("_ci_error(") >= 4,
    f"{src_job.count('_ci_error(')} pemakaian (1 definisi + 3 pemanggilan)")

print("\n=== 12. Jam kirim + harga saat kirim tercatat di sheet 'entries' ===")
# Cron dijadwalkan 00:05 UTC, tapi di produksi (Agt-Okt 2026) jalan 03:46-05:05
# UTC. Shadow log mencatat harga OPEN 00:00; pesan sampai berjam-jam kemudian.
# Selisih itu harus terukur, dan pengukurannya tidak boleh menahan sinyal.
from datetime import datetime, timezone

JAM_KIRIM = datetime(2026, 8, 22, 4, 30, tzinfo=timezone.utc)
STATE = {"run_date": "2026-08-22", "run_time": "04:29", "data_through": "2026-08-21",
         "new_entries": [TRADE], "open_positions": [], "alarms": [],
         "shadow_rows": [], "n_signals": 1, "n_shadow_rows": 0,
         "last_signal_date": "2026-08-21", "days_since_last_signal": 0}


def _dispatch_kering(price_fn):
    simpan = {k: os.environ.pop(k, None) for k in
              ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
               "GOOGLE_SERVICE_ACCOUNT_JSON", "SHEET_ID")}
    tulis = {}
    asli = sheets.append_entries
    sheets.append_entries = lambda rows: tulis.setdefault("rows", rows) is not None
    buf = _io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            masalah = daily_job.dispatch(STATE, price_fn=price_fn, clock=lambda: JAM_KIRIM)
    finally:
        sheets.append_entries = asli
        for k, v in simpan.items():
            if v is not None:
                os.environ[k] = v
    return masalah, tulis.get("rows", []), buf.getvalue()


_PX_KIRIM = _PX * 1.02
masalah, baris, log = _dispatch_kering(lambda pair: _PX_KIRIM)
cek("dispatch kering tanpa masalah", masalah == [], str(masalah))
cek("satu baris per sinyal masuk", len(baris) == 1, f"{len(baris)} baris")
b = baris[0] if baris else {}
cek("header entries lengkap", set(b) == set(sheets.ENTRIES_HEADER),
    str(set(sheets.ENTRIES_HEADER) ^ set(b)))
cek("jam kirim aktual tercatat", b.get("sent_utc") == "2026-08-22 04:30:00",
    str(b.get("sent_utc")))
cek("lag dihitung dari open 00:00 UTC hari fill", b.get("lag_min") == 270,
    str(b.get("lag_min")))
cek("harga model tetap open, bukan harga saat kirim", b.get("model_entry_px") == _PX)
cek("harga saat kirim tercatat", b.get("px_at_send") == _PX_KIRIM)
cek("drift +2%", abs(b.get("drift_pct", 0) - 2.0) < 1e-6, str(b.get("drift_pct")))
cek("pesan Telegram memuat harga saat kirim", f"{_PX_KIRIM:,.2f}" in log)
cek("SL/TP di pesan tetap dari acuan open",
    f"{_SL:,.2f}" in log and f"{_TP:,.2f}" in log)


def _harga_gagal(pair):
    raise RuntimeError("semua host data Binance gagal")


masalah, baris, log = _dispatch_kering(_harga_gagal)
cek("harga saat kirim gagal TIDAK menahan sinyal",
    masalah == [] and "SINYAL MASUK" in log, str(masalah))
cek("baris tetap ditulis, harga kosong",
    len(baris) == 1 and baris[0]["px_at_send"] is None and baris[0]["drift_pct"] is None)
cek("pesan tanpa harga saat kirim tidak memuat baris itu",
    "Harga saat pesan ini dikirim" not in notify.entry_message(TRADE))

print("\n=== 13. Sheet 'trades': buku besar forward test ===")
import pandas as pd


def _t(sym, sig, ent, ext, reason, R, net, size=0.5):
    ts = lambda s: pd.Timestamp(s, tz="UTC")
    return dict(symbol=sym, signal_date=ts(sig), entry_date=ts(ent), exit_date=ts(ext),
                reason=reason, days_held=(ts(ext) - ts(ent)).days + 1,
                entry_px=100.0, exit_px=100.0 * (1 + net), oco_stop_loss=95.0,
                oco_take_profit=110.0, size_frac_used=size, net_ret=net, R_net=R)


TR = pd.DataFrame([
    _t("BTC", "2025-10-26", "2025-10-27", "2025-11-04", "time", 0.2, 0.01),   # backtest
    _t("ETH", "2026-08-21", "2026-08-22", "2026-08-23", "sl", -1.06, -0.056),
    _t("BTC", "2026-08-21", "2026-08-22", "2026-09-04", "time", 0.35, 0.014, 0.431),
    _t("ETH", "2026-09-15", "2026-09-16", "2026-09-21", "tp", 1.95, 0.124),
    _t("ETH", "2026-09-21", "2026-09-22", "2026-10-02", "eod", -0.49, -0.028),
])
rows = daily_job.forward_trade_rows(TR)
cek("trade jendela backtest tidak ikut", len(rows) == 4 and
    all(r["signal_date"] > cfg.BACKTEST_END for r in rows), f"{len(rows)} baris")
cek("kolom persis TRADES_HEADER", all(set(r) == set(sheets.TRADES_HEADER) for r in rows))
cek("urut tanggal masuk", [r["entry_date"] for r in rows]
    == sorted(r["entry_date"] for r in rows))
buka = [r for r in rows if r["status"] == "open"]
cek("posisi 'eod' ditandai open, bukan hasil",
    len(buka) == 1 and buka[0]["reason"] == "mark-to-market" and buka[0]["cum_R_closed"] is None)
tutup = sorted((r for r in rows if r["status"] == "closed"), key=lambda r: r["exit_date"])
cek("cum_R menjumlah trade tutup urut tanggal keluar",
    [r["cum_R_closed"] for r in tutup] == [-1.06, -0.71, 1.24],
    str([r["cum_R_closed"] for r in tutup]))
cek("equity_pct = ukuran dipakai x return bersih",
    abs(tutup[1]["equity_pct"] - 100 * 0.431 * 0.014) < 1e-9, str(tutup[1]["equity_pct"]))
cek("tanpa trade -> nol baris", daily_job.forward_trade_rows(TR.iloc[0:0]) == []
    and daily_job.forward_trade_rows(None) == [])

print("\n" + ("SEMUA TES JOB HARIAN LOLOS" if ok else "ADA TES YANG GAGAL"))
raise SystemExit(0 if ok else 1)
