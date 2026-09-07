"""Keadaan harian job == keadaan engine, hari demi hari.

KENAPA BERKAS INI ADA.

Gerbang v1.4.2 menguji SATU jendela penuh 2019-2026 sekaligus. Cron produksi
tidak pernah melakukan itu: ia menjalankan RANTAI jendela harian, masing-masing
berakhir kemarin. Perbedaan itu menyembunyikan bug terbesar v1.4.3:

  engine.run() mengiterasi `dates[:-1]`, jadi hari data TERAKHIR tidak pernah
  diurus barrier-nya. Posisi yang kena SL/TP atau batas waktu di hari itu ikut
  tertutup-paksa sebagai reason="eod", dan pipeline.still_open() membacanya
  sebagai "masih terbuka sekarang". Slot dan ekuitasnya terkunci, sehingga
  sinyal yang lahir di hari yang sama DIBUANG.

  Di data 2019-2026: 226 dari 298 trade (76%) tidak akan pernah terkirim.

Diperbaiki dengan pipeline.append_sentinel_day(). Tes ini yang menjaganya:
untuk hari-hari yang bentuknya paling berbahaya, `daily_job.collect()` harus
melaporkan posisi terbuka dan sinyal masuk yang SAMA PERSIS dengan yang
dikatakan engine.

Kalau tes ini gagal, jangan tambal collect() sampai cocok — cari tahu dulu mana
yang benar. Yang dipakai backtest adalah engine.
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
import engine
import panel_v14
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

PANEL = panel_v14.build(RAW)
REF = engine.run(PANEL, panel_v14.empty_regime(PANEL), cfg.production_engine_config())
SIG = {d: set(g["symbol"]) for d, g in REF.groupby("signal_date")}
ENT = REF[["symbol", "entry_date", "exit_date"]]
DATES = PANEL.index.get_level_values("date").unique().sort_values()
DATES = DATES[(DATES >= pd.Timestamp(cfg.BACKTEST_START, tz="UTC"))
              & (DATES <= pd.Timestamp(cfg.BACKTEST_END, tz="UTC"))]

print(f"  acuan engine: {len(REF)} trade, {len(DATES)} hari")


def terbuka_seharusnya(d: pd.Timestamp) -> set:
    """Posisi yang masih hidup SESUDAH hari d diurus engine — itulah yang benar-
    benar terbuka saat pasar buka di hari d+1."""
    return set(ENT.loc[(ENT["entry_date"] <= d) & (ENT["exit_date"] > d), "symbol"])


def terbuka_naif(d: pd.Timestamp) -> set:
    """Yang dilaporkan job SEBELUM perbaikan: masih memuat yang exit di hari d."""
    return set(ENT.loc[(ENT["entry_date"] <= d) & (ENT["exit_date"] >= d), "symbol"])


# --- 1. seberapa besar kerusakannya kalau tidak diperbaiki -------------------
print("\n=== 1. Ukuran kerusakan yang dijaga tes ini ===")
hilang = sum(len(w & terbuka_naif(d)) for d, w in SIG.items())
cek("bug ini memang membuang sebagian besar trade", hilang > 0,
    f"{hilang}/{len(REF)} trade akan hilang tanpa hari penanda")

# --- 2. hari-hari yang paling berbahaya, dijalankan lewat collect() ----------
# Hari yang melahirkan sinyal SEKALIGUS menutup posisi: persis bentuk hari yang
# dulu diam-diam dibuang. Ditambah beberapa hari biasa sebagai kontrol.
berbahaya = sorted(d for d, w in SIG.items() if w & terbuka_naif(d))
biasa = sorted(d for d in SIG if d not in set(berbahaya))
CONTOH = sorted(set(berbahaya[:12] + berbahaya[-12:] + biasa[:4] + biasa[-4:]))
print(f"\n=== 2. collect() diuji pada {len(CONTOH)} hari "
      f"({len(berbahaya)} hari berbahaya tersedia) ===")


def jalankan(hari_data: pd.Timestamp) -> dict:
    """collect() seolah dijalankan 00:05 UTC di hari SESUDAH `hari_data`."""
    hari_ini = hari_data + pd.Timedelta(days=1)

    def harga_open_palsu(pair: str):
        return hari_ini, 100.0

    return daily_job.collect(now_utc=hari_ini.to_pydatetime(),
                             raw={s: df[df.index <= hari_data] for s, df in RAW.items()},
                             entry_price_fn=harga_open_palsu)


beda_open, beda_sinyal, beda_hari = [], [], []
for d in CONTOH:
    st = jalankan(d)
    got_open = {p["symbol"] for p in st["open_positions"]}
    got_sig = {e["symbol"] for e in st["new_entries"]}
    if got_open != terbuka_seharusnya(d):
        beda_open.append((d, sorted(terbuka_seharusnya(d)), sorted(got_open)))
    if got_sig != SIG.get(d, set()):
        beda_sinyal.append((d, sorted(SIG.get(d, set())), sorted(got_sig)))
    for p in st["open_positions"]:
        e = pd.Timestamp(p["entry_date"], tz="UTC")
        if p["days_held"] != (d + pd.Timedelta(days=1) - e).days + 1:
            beda_hari.append((d, p["symbol"], p["days_held"]))

for d, want, got in beda_open[:6]:
    print(f"     open  {d.date()}  engine={want}  job={got}")
for d, want, got in beda_sinyal[:6]:
    print(f"     sinyal {d.date()}  engine={want}  job={got}")
cek("posisi terbuka sama persis dengan engine", not beda_open,
    f"{len(beda_open)} hari berbeda")
cek("sinyal masuk sama persis dengan engine", not beda_sinyal,
    f"{len(beda_sinyal)} hari berbeda")
cek("days_held = nomor hari posisi HARI INI", not beda_hari,
    f"{len(beda_hari)} posisi salah hitung")

# --- 3. hari penanda tidak boleh mengubah apa pun di hari data terakhir -----
print("\n=== 3. Hari penanda tidak menyentuh nilai hari data terakhir ===")
d = DATES[-1]
raw_potong = {s: df[df.index <= d] for s, df in RAW.items()}
raw_ext, sentinel = pipeline.append_sentinel_day(raw_potong, d)
p_asli = panel_v14.build(raw_potong)
p_ext = panel_v14.build(raw_ext)
kolom = [c for c in p_asli.columns if c != "symbol"]
sama = p_asli[kolom].equals(p_ext.loc[p_ext.index.get_level_values("date") <= d, kolom])
cek("semua kolom di hari <= data terakhir identik", sama)
cek("hari penanda tepat sehari sesudah data terakhir",
    sentinel == d + pd.Timedelta(days=1), str(sentinel.date()))
cek("open hari penanda NaN -> engine tidak bisa membuka posisi di sana",
    bool(p_ext.loc[(sentinel, cfg.UNIVERSE[0]), "open"] != p_ext.loc[(sentinel, cfg.UNIVERSE[0]), "open"]))
cek("close hari penanda = close sungguhan terakhir -> baris 'eod' tetap lahir",
    float(p_ext.loc[(sentinel, cfg.UNIVERSE[0]), "close"])
    == float(p_asli.loc[(d, cfg.UNIVERSE[0]), "close"]))

print("\n" + ("SEMUA TES KEADAAN HARIAN LOLOS" if ok else "ADA TES YANG GAGAL"))
raise SystemExit(0 if ok else 1)
