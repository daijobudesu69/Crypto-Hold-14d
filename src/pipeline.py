"""Pipa produksi: data mentah -> panel -> trade.

Satu jalur kode dipakai dua-duanya:
  * replay v1.4.2 — diberi 2019-2026 sekaligus, hasilnya dibanding backtest V1.3
  * cron v1.4.3   — diberi jendela sampai kemarin, diambil sinyal hari ini saja

Kalau kedua pemakaian itu lewat jalur berbeda, gerbang v1.4.2 tidak membuktikan
apa pun tentang yang jalan tiap hari. Karena itu `build_trades()` di bawah adalah
SATU-SATUNYA tempat sinyal lahir.
"""
from __future__ import annotations
import pandas as pd

import config_v14 as cfg
import engine
import panel_v14


def build_trades(raw: dict[str, pd.DataFrame], start: str | None = None,
                 end: str | None = None) -> pd.DataFrame:
    """OHLCV mentah -> trade log lengkap, memakai config produksi frozen."""
    panel = panel_v14.build(raw)
    regime = panel_v14.empty_regime(panel)
    c = cfg.production_engine_config()
    if start:
        c.start = start
    if end:
        c.end = end
    return engine.run(panel, regime, c)


SENTINEL_OPEN_COLS = ("open", "high", "low", "volume", "quote_volume")


def append_sentinel_day(raw: dict[str, pd.DataFrame], data_through: pd.Timestamp):
    """Tambahkan SATU baris penanda sesudah lilin tertutup terakhir. -> (raw, tanggal)

    KENAPA INI HARUS ADA (bug terbesar v1.4.3).

    engine.run() mengiterasi `dates[:-1]`, karena hari terakhir tidak punya baris
    hari-berikutnya untuk harga eksekusi. Akibatnya hari data terakhir TIDAK
    PERNAH diurus barrier-nya: posisi yang kena SL/TP atau batas waktu di hari
    itu tidak ditutup, melainkan ikut ditutup-paksa dengan reason="eod" -- dan
    pipeline.still_open() membacanya sebagai "masih terbuka sekarang".

    Di backtest itu tidak kelihatan: hari terakhir 2026-08-16 memang ujung
    jendela uji. Di job harian, hari terakhir itu KEMARIN, dan tiap hari.

    Akibatnya, tiap hari job harian:
      * melaporkan posisi yang sebenarnya sudah tutup kemarin sebagai terbuka,
      * mengunci slot dan ekuitasnya, sehingga sinyal masuk hari itu DIBUANG.

    Diukur pada data 2019-2026: 226 dari 298 trade (76%) tidak akan pernah
    terkirim, karena hampir semua entri lahir di hari yang juga menutup posisi
    sebelumnya. Gerbang v1.4.2 tidak menangkapnya -- gerbang itu menguji replay
    satu jendela penuh, bukan rantai jendela harian.

    Perbaikannya sengaja TIDAK menyentuh engine.py (§2.2 melarangnya, dan bukti
    gerbang v1.4.2 bersandar padanya). Yang dilakukan: memberi engine SATU hari
    tambahan di ujung, supaya `dates[:-1]` mencakup hari data terakhir yang
    sesungguhnya.

    Baris penanda itu:
      * open = NaN  -> engine TIDAK BISA membuka posisi di hari data terakhir
        (filter `day[entry_price_col].notna()`), jadi seleksi sinyal tetap
        sepenuhnya milik pipeline.signals_for_day() seperti sebelumnya;
      * high/low = NaN -> tidak pernah dibaca, karena hari penanda sendiri tidak
        ikut diiterasi;
      * close = close terakhir yang SUNGGUHAN -> tutup-paksa posisi yang memang
        masih hidup tetap menghasilkan baris "eod", dengan harga mark-to-market
        yang sama persis seperti sebelum perbaikan ini.

    Efek sampingan yang menguntungkan: `days_held` pada baris "eod" menjadi
    (hari penanda - entry) + 1, yaitu nomor hari posisi itu HARI INI -- persis
    yang dimaksud pesan alarm ("hari ini hari ke-13, besok tutup paksa").
    Sebelumnya angka itu terhitung sampai kemarin dan meleset satu hari.

    Nilai di hari data terakhir tidak berubah sedikit pun: seluruh indikator
    menoleh ke belakang, dan next_open di hari itu memang sudah NaN sebelumnya
    (ia baris terakhir). Yang berubah hanya: engine sekarang sampai ke sana.
    """
    data_through = pd.Timestamp(data_through)
    sentinel = data_through + pd.Timedelta(days=1)
    out = {}
    for sym, df in raw.items():
        # Potong dulu di data_through. Kalau satu simbol kebetulan sudah punya
        # lilin sesudahnya (mis. lilin ETH kemarin belum terbit saat BTC sudah),
        # baris itu akan menjadi tanggal terakhir panel dan hari penanda kehilangan
        # gunanya: engine kembali tidak mengurus barrier di hari data terakhir,
        # dan lebih buruk lagi ia bisa membuka posisi sendiri di sana sehingga
        # sinyal hari itu tertelan sebagai "posisi terbuka" dan tidak pernah dikirim.
        df = df[df.index <= data_through]
        if df.empty or df.index.max() != data_through:
            out[sym] = df          # simbol yang datanya tertinggal: biarkan apa adanya
            continue
        row = {c: float("nan") for c in df.columns}
        row["close"] = float(df["close"].iloc[-1])
        penanda = pd.DataFrame([row], columns=df.columns,
                               index=pd.DatetimeIndex([sentinel], name=df.index.name))
        out[sym] = pd.concat([df, penanda])
    return out, sentinel


def still_open(trades: pd.DataFrame) -> pd.DataFrame:
    """Posisi yang MASIH TERBUKA di ujung data.

    engine.run() menutup paksa semua posisi yang masih hidup di tanggal terakhir
    dan menandainya reason="eod". Jadi di backtest itu artinya "ditutup di akhir
    jendela uji", tapi di job harian artinya "posisi ini sebenarnya masih
    terbuka sekarang". Harga dan R pada baris itu adalah tanda-mati-sementara,
    bukan hasil sungguhan.

    HANYA sah dipakai kalau tanggal terakhir yang dilihat engine adalah hari
    penanda dari append_sentinel_day(). Tanpa itu, baris "eod" juga memuat posisi
    yang sebenarnya sudah kena SL/TP/batas waktu di hari data terakhir, karena
    engine tidak pernah mengurus barrier di tanggal terakhirnya.
    """
    if trades.empty:
        return trades
    return trades[trades["reason"] == "eod"]


def signals_for_day(panel: pd.DataFrame, day: pd.Timestamp, open_symbols,
                    n_slots: int, require_entry_price: bool = True) -> pd.DataFrame:
    """Kandidat masuk yang lahir di close tanggal `day`, terurut peringkat.

    KENAPA LOGIKA INI DITULIS ULANG DI SINI, bukan dipanggil dari engine.py:

    engine.py diport byte-identical (§2.2) dan filter kelayakannya tertanam di
    dalam loop harian, tidak bisa dipanggil sendirian. Mengekstraknya berarti
    mengubah engine.py dan membatalkan bukti gerbang v1.4.2.

    Dan job harian TIDAK BISA memakai engine apa adanya untuk sinyal hari ini:
    engine hanya mengiterasi `dates[:-1]` karena butuh baris hari berikutnya
    untuk harga eksekusi. Di produksi, "hari berikutnya" itu HARI INI dan
    lilinnya baru saja dibuka — belum ada di data lilin-tertutup.

    Duplikasi ini dijaga oleh tests/test_signal_equivalence.py, yang menuntut
    fungsi ini menghasilkan entri yang PERSIS SAMA dengan yang benar-benar
    diambil engine, untuk setiap hari 2019-2026. Kalau salah satu bergeser,
    tes itu gagal.

    `require_entry_price=False` dipakai di jalur produksi: harga eksekusi
    (open hari ini) diambil dari lilin berjalan, bukan dari kolom next_open.
    """
    try:
        rows = panel.xs(day, level="date")
    except KeyError:
        return panel.iloc[0:0]

    elig = rows[(rows["days_listed"] >= cfg.MIN_HISTORY_DAYS)
                & (rows["med_qvol_20"] >= cfg.LIQ_FLOOR_USD)
                & rows["atr14"].notna()
                & rows[cfg.RANK_COL].notna()]
    if require_entry_price:
        elig = elig[elig[cfg.ENTRY_PRICE_COL].notna()]
    for gc in ("is_production_universe", "tsmom_pos"):
        elig = elig[elig[gc].fillna(False).astype(bool)]
    elig = elig[~elig.index.isin(set(open_symbols))]
    if elig.empty or n_slots <= 0:
        return elig.iloc[0:0]
    return elig.sort_values(cfg.RANK_COL, ascending=cfg.RANK_ASCENDING).head(n_slots)


def apply_exposure_cap(trades: pd.DataFrame) -> pd.DataFrame:
    """Potong ukuran posisi supaya total eksposur tidak pernah lewat 100% ekuitas.

    KENAPA WAJIB: ukuran posisi = 3% risk / jarak SL. Jarak SL median cuma 7%,
    jadi SATU posisi saja median 42% ekuitas dan bisa sampai 100%. Kalau BTC dan
    ETH sama-sama memberi sinyal di hari yang sama, penjumlahannya rutin lewat
    100% — pada 22 Agt 2026 hasilnya 73.8% + 56.9% = 130.7%.

    Di spot tanpa leverage itu MUSTAHIL. Tanpa fungsi ini, pesan Telegram
    menyuruh Dew membeli lebih banyak daripada uang yang ada, dan dia terpaksa
    berimprovisasi — padahal seluruh proyek ini bersandar pada eksekusi yang
    deterministik dan bisa dibandingkan dengan backtest.

    Aturan §2.6, apa adanya: "Ketika ekuitas bebas tidak cukup untuk ukuran
    penuh, ambil sisa yang ada; jangan lewati sinyal, jangan naikkan risk."

    Dialokasikan kronologis menurut entry_date, persis seperti simulasi §2.6.
    Kolom `size_frac` (dipakai ÷ diinginkan) adalah diagnostik yang diminta
    §2.6: kalau jauh lebih sering < 1 daripada ~14%, ada bug pelacakan ekuitas.
    """
    if trades.empty:
        return trades
    t = trades.sort_values(["entry_date", "symbol"]).copy()
    terbuka: list[tuple[pd.Timestamp, int, float]] = []      # (exit_date, idx, ukuran)
    dipakai, frac = {}, {}
    for idx, row in t.iterrows():
        d = row["entry_date"]
        terbuka = [p for p in terbuka if p[0] > d]           # lepaskan yang sudah exit
        bebas = max(cfg.MAX_EXPOSURE_FRAC - sum(p[2] for p in terbuka), 0.0)
        ingin = row["size_frac_wanted"]
        ambil = min(ingin, bebas)
        dipakai[idx] = ambil
        frac[idx] = (ambil / ingin) if ingin > 0 else 1.0
        terbuka.append((row["exit_date"], idx, ambil))
    t["size_frac_used"] = pd.Series(dipakai)
    t["size_frac"] = pd.Series(frac)
    t["notional_usd_at_start"] = t["size_frac_used"] * cfg.ACCOUNT_START_USD
    return t.reindex(trades.index)


def with_execution_plan(trades: pd.DataFrame) -> pd.DataFrame:
    """Tambahkan kolom yang dibutuhkan manusia untuk mengeksekusi manual:
    harga SL/TP, ukuran posisi, dan tanggal alarm hold (§2.8).

    Barrier dihitung ulang dari config, bukan diambil dari engine, supaya
    angka yang dikirim ke Telegram terbukti berasal dari konstanta frozen.
    """
    if trades.empty:
        return trades
    t = trades.copy()
    # engine menyimpan sl_dist_pct = sl_atr * atr / entry -> balikkan jadi atr
    atr = t["sl_dist_pct"] * t["entry_px"] / cfg.SL_ATR_MULT
    t["atr14_at_signal"] = atr
    t["oco_stop_loss"] = t["entry_px"] - cfg.SL_ATR_MULT * atr
    t["oco_take_profit"] = t["entry_px"] + cfg.TP_ATR_MULT * atr
    sizes = [cfg.position_size_frac(e, a) for e, a in zip(t["entry_px"], atr)]
    t["size_frac_wanted"] = [w for w, _ in sizes]
    t["hold_warning_date"] = t["entry_date"] + pd.Timedelta(days=cfg.TIME_EXIT_WARNING_DAY - 1)
    t["hold_force_exit_date"] = t["entry_date"] + pd.Timedelta(days=cfg.TIME_EXIT_DAY - 1)
    # Cap portofolio, bukan cap per posisi. cfg.position_size_frac() hanya
    # membatasi SATU posisi di 100%; yang membuat total dua posisi lewat 100%
    # justru penjumlahannya. Lihat apply_exposure_cap().
    return apply_exposure_cap(t)
