"""Menjalankan daily_job.collect() secara OFFLINE, di hari-hari yang berbeda bentuk.

KENAPA BERKAS INI ADA. Cron produksi pertama (23 Agt 2026) mati dengan
KeyError: 'size_frac_used'. Penyebabnya sepele — satu kunci tidak disalin ke
dict posisi terbuka — tapi lolos dari SELURUH suite tes karena collect() dulu
hanya bisa jalan dengan menembak jaringan, jadi tidak pernah ikut diuji.

Yang membuatnya makin licin: bug itu hanya muncul pada hari yang punya POSISI
TERBUKA. Pada hari tanpa posisi, sum() atas daftar kosong mengembalikan 0 tanpa
pernah menyentuh kunci yang hilang. Run manual pertama kebetulan jatuh di hari
tanpa posisi, jadi hijau; cron keesokan harinya jatuh di hari dengan posisi,
langsung merah.

Karena itu tes ini WAJIB mencakup ketiga bentuk hari:
  - hari tanpa posisi terbuka
  - hari DENGAN posisi terbuka          <- yang dulu meledak
  - hari yang memicu alarm hari ke-13
"""
from __future__ import annotations
import os, sys

import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config_v14 as cfg
import daily_job
import pipeline
from test_replay_v142 import load_csv

ok = True


def cek(nama: str, kondisi: bool, ket: str = "") -> None:
    global ok
    print(f"  {'PASS' if kondisi else 'GAGAL'}  {nama}" + (f"  [{ket}]" if ket else ""))
    ok = ok and kondisi


RAW = load_csv(tuple(cfg.UNIVERSE) + tuple(cfg.SHADOW_SYMBOLS))
if not RAW:
    print("  SKIP: data V1.3 historis tidak tersedia")
    raise SystemExit(0)


def potong(sampai: pd.Timestamp) -> dict:
    """Data sampai lilin tertutup terakhir `sampai` — lilin sesudahnya belum ada."""
    return {s: df[df.index <= sampai] for s, df in RAW.items()}


def jalankan(hari_ini) -> dict:
    """Job seolah-olah dijalankan 00:05 UTC pada tanggal `hari_ini`.

    Lilin tertutup terakhir adalah KEMARIN — begitulah cron produksi berjalan,
    dan hanya bentuk itu yang menguji kontrak "sinyal di close t, fill di open
    t+1". Fixture lama menyamakan "hari ini" dengan lilin terakhir dan
    mengembalikan harga open bertanggal 2030; dua-duanya menyembunyikan
    penyimpangan hari eksekusi.
    """
    hari_ini = pd.Timestamp(hari_ini, tz="UTC") if pd.Timestamp(hari_ini).tz is None         else pd.Timestamp(hari_ini)
    kemarin = hari_ini - pd.Timedelta(days=1)

    def harga_open_palsu(pair: str):
        """Pengganti binance_data.current_open() supaya tes tidak kena jaringan."""
        return hari_ini, 100.0

    return daily_job.collect(now_utc=hari_ini.to_pydatetime(),
                             raw=potong(kemarin), entry_price_fn=harga_open_palsu)


# Cari tanggal yang bentuknya kita butuhkan, dari trade log sungguhan.
TRADES = pipeline.with_execution_plan(pipeline.build_trades(RAW)).sort_values("entry_date")
panjang = TRADES[TRADES["days_held"] >= cfg.TIME_EXIT_WARNING_DAY].iloc[-1]
# Tanggal-tanggal ini adalah HARI JOB BERJALAN, bukan lilin terakhir.
# entry_date = hari ke-1, jadi hari ke-N = entry_date + (N-1).
HARI_ALARM = (panjang["entry_date"] + pd.Timedelta(days=cfg.TIME_EXIT_WARNING_DAY - 1))
HARI_TERBUKA = (panjang["entry_date"] + pd.Timedelta(days=2))

print("=== 1. Hari DENGAN posisi terbuka (kasus yang membunuh cron pertama) ===")
s = jalankan(HARI_TERBUKA)
cek("collect() tidak melempar KeyError", True, f"tanggal {HARI_TERBUKA.date()}")
cek("ada posisi terbuka terdeteksi", len(s["open_positions"]) > 0,
    f"{len(s['open_positions'])} posisi")
for p in s["open_positions"]:
    cek(f"posisi {p['symbol']} punya size_frac_used", "size_frac_used" in p,
        f"{p.get('size_frac_used', 0):.1%}")
    cek(f"posisi {p['symbol']} punya days_held wajar", 1 <= p["days_held"] <= cfg.HOLD_MAX_DAYS,
        f"hari ke-{p['days_held']}")

print("\n=== 2. Cap eksposur dihormati saat ada posisi terbuka ===")
total = sum(p["size_frac_used"] for p in s["open_positions"]) \
        + sum(e["size_frac_used"] for e in s["new_entries"])
cek("total eksposur <= 100%", total <= cfg.MAX_EXPOSURE_FRAC + 1e-9, f"{total:.1%}")

print("\n=== 3. Hari yang memicu alarm hari ke-13 ===")
s2 = jalankan(HARI_ALARM)
cek("alarm hari ke-13 terpicu", len(s2["alarms"]) > 0, f"{len(s2['alarms'])} alarm")
for a in s2["alarms"]:
    cek(f"alarm {a['symbol']} tepat di hari ke-{cfg.TIME_EXIT_WARNING_DAY}",
        a["days_held"] == cfg.TIME_EXIT_WARNING_DAY)

print("\n=== 4. Hari TANPA posisi terbuka (kasus yang dulu hijau palsu) ===")
# 2019-01-31: mom_120 belum ada sama sekali, jadi dijamin nol posisi.
s3 = jalankan("2019-03-01")
cek("collect() jalan tanpa posisi", len(s3["open_positions"]) == 0)
cek("nol sinyal", s3["n_signals"] == 0)
cek("shadow log tetap ditulis", s3["n_shadow_rows"] > 0, f"{s3['n_shadow_rows']} baris")

print("\n=== 5. Semua state bisa dibentuk jadi pesan tanpa meledak ===")
import notify
for st in (s, s2, s3):
    notify.heartbeat_message(st)
    for e in st["new_entries"]:
        notify.entry_message(e)
    for a in st["alarms"]:
        notify.hold_alarm_message(a)
cek("heartbeat/entry/alarm terbentuk untuk ketiga bentuk hari", True)

print("\n=== 6. Baris shadow log lengkap ===")
kolom = set(s["shadow_rows"][0]) if s["shadow_rows"] else set()
kurang = [c for c in cfg.SHADOW_COLUMNS if c not in kolom]
cek("semua kolom shadow §2.4 ada", not kurang, str(kurang) if kurang else f"{len(kolom)} kolom")

print("\n" + ("SEMUA TES COLLECT LOLOS" if ok else "ADA TES YANG GAGAL"))
raise SystemExit(0 if ok else 1)
