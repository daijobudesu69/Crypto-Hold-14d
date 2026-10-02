"""Ambil lilin harian dari Binance. Endpoint PUBLIK — tanpa API key.

Dipakai dua tempat dengan tuntutan berbeda:
  * replay v1.4.2  — menarik seluruh 2019-2026 sekaligus, harus mereproduksi
                     CSV V1.3 persis
  * cron v1.4.3    — menarik jendela harian tiap 00:05 UTC

Tiga jebakan yang ditangani di sini, ketiganya sanggup menggagalkan gerbang
v1.4.2 dengan diam-diam:

  1. LILIN YANG BELUM TUTUP. Panggilan jam 00:05 UTC mengembalikan lilin hari
     ini yang baru berumur 5 menit. Memakainya berarti menghitung sinyal dari
     harga yang masih bergerak — bentuk lookahead paling halus yang ada. Baris
     hanya dipakai kalau `close_time < sekarang`, bukan sekadar "buang baris
     terakhir" (yang salah kalau API mengembalikan lebih sedikit dari dugaan).

  2. days_listed ADALAH NOMOR BARIS, bukan umur koin. features.add_features()
     mengisinya dengan np.arange(len(df)). Menarik data lebih awal dari CSV V1.3
     akan menggeser seluruh kolom itu dan mengubah kapan gate 60-hari lolos.
     Karena itu rentang tarikan wajib eksplisit, tidak boleh "sebanyak-banyaknya".

  3. HALAMAN & DUPLIKAT. Batas API 1000 lilin per panggilan. Batas halaman bisa
     tumpang-tindih di sambungannya, jadi dedup pada open_time itu wajib, bukan
     pemanis.

Skema kolom mengikuti crypto_v1_2_data_acquisition.py (commit a04b02f) supaya
data ini dan CSV V1.3 punya bentuk yang sama.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone

import pandas as pd
import requests

import config_v14 as cfg

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]
FLOAT_COLS = ["open", "high", "low", "close", "volume",
              "quote_volume", "taker_buy_base", "taker_buy_quote"]
OHLCV_COLS = ["open", "high", "low", "close", "volume", "quote_volume"]

MAX_LIMIT = 1000
REQUEST_TIMEOUT = 20
PAGE_SLEEP = 0.3            # pacing sopan antar halaman
MAX_RETRIES = 5
# Ditolak di tingkat host, bukan permintaan yang salah: coba host berikutnya.
HOST_BLOCKED = frozenset({401, 403, 451})


def _ms(ts: str | pd.Timestamp) -> int:
    t = pd.Timestamp(ts, tz="UTC") if not isinstance(ts, pd.Timestamp) else ts
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return int(t.timestamp() * 1000)


def _get(session: requests.Session, params: dict,
         hosts: tuple[str, ...] | None = None) -> list | dict:
    """GET dengan backoff, mencoba tiap host berurutan.

    Perbedaan penanganan yang disengaja:
      * Gagal JARINGAN (timeout, DNS, connection refused) -> host itu dianggap
        tidak terjangkau, lanjut ke host berikutnya. Inilah bentuk geo-blocking
        Binance: bukan HTTP 403, melainkan koneksi yang menggantung sampai
        timeout.
      * Ditolak di TINGKAT HOST (401/403/451) -> lanjut ke host berikutnya.
        Geo-blocking tidak selalu berbentuk koneksi menggantung; sebagian titik
        keluar dibalas 451 "unavailable for legal reasons" secara langsung.
        Kalau itu dilempar apa adanya, daftar host cadangan -- satu-satunya
        alasan daftar ini ada -- tidak pernah terpakai.
      * Gagal HTTP dengan respons sah (400 simbol salah, dsb) -> LANGSUNG
        dilempar tanpa mencoba host lain. Permintaan yang salah akan sama
        salahnya di host mana pun; mencoba ulang cuma menyembunyikan bug.
      * 429/418/5xx -> ditunggu lalu diulang di host yang sama.
    """
    kegagalan = []
    for host in hosts or cfg.BINANCE_KLINES_HOSTS:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = session.get(host, params=params, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                kegagalan.append(f"{host.split('/')[2]}: {type(e).__name__}")
                break                      # host tak terjangkau -> host berikutnya
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 418) or r.status_code >= 500:
                if attempt == MAX_RETRIES:
                    kegagalan.append(f"{host.split('/')[2]}: HTTP {r.status_code}")
                    break
                time.sleep(2 * attempt)
                continue
            if r.status_code in HOST_BLOCKED:
                kegagalan.append(f"{host.split('/')[2]}: HTTP {r.status_code} (host ini menolak)")
                break                      # diblokir di sini -> host berikutnya
            r.raise_for_status()           # permintaan salah: jangan pura-pura pulih
            # Status non-200 yang tidak dilempar raise_for_status (3xx, 204):
            # jangan diam-diam mengulang sampai habis lalu melapor tanpa sebab.
            raise RuntimeError(f"{host.split('/')[2]}: HTTP {r.status_code} tak terduga")
    raise RuntimeError("semua host data Binance gagal -> " +
                       ("; ".join(kegagalan) if kegagalan else "tanpa sebab tercatat"))


def fetch_klines(symbol: str, start: str, end: str,
                 session: requests.Session | None = None,
                 now_ms: int | None = None) -> pd.DataFrame:
    """Lilin harian [start, end] inklusif, HANYA yang sudah tutup.

    `now_ms` bisa disuntik untuk pengujian; default waktu dinding sekarang.
    """
    session = session or requests.Session()
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms, end_ms = _ms(start), _ms(end)
    # end inklusif: lilin hari `end` dibuka jam 00:00 dan tutup 23:59:59.999
    end_ms += 24 * 60 * 60 * 1000 - 1

    rows, cursor = [], start_ms
    while cursor <= end_ms:
        batch = _get(session, {"symbol": symbol, "interval": cfg.KLINE_INTERVAL,
                               "startTime": cursor, "endTime": end_ms, "limit": MAX_LIMIT})
        if not batch:
            break
        rows.extend(batch)
        last_open = int(batch[-1][0])
        if len(batch) < MAX_LIMIT:
            break
        cursor = last_open + 1                     # +1ms: hindari lilin terakhir terulang
        time.sleep(PAGE_SLEEP)

    if not rows:
        raise RuntimeError(f"{symbol}: nol lilin untuk {start}..{end}")

    df = pd.DataFrame(rows, columns=KLINE_COLUMNS)
    df["open_time"] = pd.to_numeric(df["open_time"]).astype("int64")
    df["close_time"] = pd.to_numeric(df["close_time"]).astype("int64")
    for c in FLOAT_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # jebakan 1 — buang lilin yang belum tutup
    n_before = len(df)
    df = df[df["close_time"] < now_ms]
    n_open = n_before - len(df)

    # jebakan 3 — dedup sambungan halaman
    df = df.drop_duplicates(subset="open_time", keep="first").sort_values("open_time")

    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.normalize()
    df = df.set_index("date")
    df.attrs["dropped_open_candles"] = n_open
    return df[OHLCV_COLS].astype(float)


def current_open(symbol: str, session: requests.Session | None = None,
                 now_ms: int | None = None) -> tuple[pd.Timestamp, float]:
    """(tanggal, harga OPEN) dari lilin hari ini yang MASIH BERJALAN.

    Ini satu-satunya tempat lilin belum-tutup boleh dibaca, dan hanya kolom
    OPEN-nya. Sah, bukan lookahead: harga open sebuah lilin sudah final sejak
    detik lilin itu dibuka — ia tidak berubah sepanjang hari, tidak seperti
    high/low/close.

    Dibutuhkan karena backtest mengeksekusi di `open hari t+1`. Saat job jalan
    00:05 UTC, "t+1" adalah hari ini, lilinnya baru berumur 5 menit, dan
    harganya belum ada di data lilin-tertutup.
    """
    session = session or requests.Session()
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    today = pd.Timestamp(now_ms, unit="ms", tz="UTC").normalize()
    batch = _get(session, {"symbol": symbol, "interval": cfg.KLINE_INTERVAL,
                           "startTime": int(today.timestamp() * 1000), "limit": 1})
    if not batch:
        raise RuntimeError(f"{symbol}: lilin hari ini ({today.date()}) belum tersedia")
    got = pd.Timestamp(int(batch[0][0]), unit="ms", tz="UTC").normalize()
    if got != today:
        raise RuntimeError(f"{symbol}: diminta lilin {today.date()}, dapat {got.date()}")
    return got, float(batch[0][1])


# Host yang sama dengan klines, urutan fallback yang sama -- termasuk alasan
# data-api.binance.vision didahulukan (lihat config_v14.BINANCE_KLINES_HOSTS).
TICKER_HOSTS: tuple[str, ...] = tuple(
    h.replace("/klines", "/ticker/price") for h in cfg.BINANCE_KLINES_HOSTS)


def current_price(symbol: str, session: requests.Session | None = None) -> float:
    """Harga terakhir SAAT INI -- bukan harga model.

    Hanya untuk PENGUKURAN (sheet `entries`): seberapa jauh harga sudah bergerak
    dari open 00:00 UTC saat pesan sinyal benar-benar terkirim. Cron dijadwalkan
    00:05 UTC tapi di produksi jalan 03:46-05:05 UTC; harga ini tidak pernah
    masuk ke sinyal, SL/TP, atau ukuran posisi.
    """
    session = session or requests.Session()
    data = _get(session, {"symbol": symbol}, hosts=TICKER_HOSTS)
    return float(data["price"])


def assert_contiguous(df: pd.DataFrame, symbol: str) -> None:
    """Lilin harian harus tanpa lubang. Lubang berarti data hilang, dan sistem
    harus berhenti keras — bukan diam-diam menghitung momentum yang melompati
    hari (mom_28 akan mengukur 29 hari kalender tanpa memberi tahu siapa pun)."""
    missing = pd.date_range(df.index.min(), df.index.max(), freq="D").difference(df.index)
    if len(missing):
        raise RuntimeError(
            f"{symbol}: {len(missing)} hari hilang, contoh {[str(d.date()) for d in missing[:5]]}")


def load(symbols=None, start: str | None = None, end: str | None = None,
         verbose: bool = True) -> dict[str, pd.DataFrame]:
    """{'BTC': df, 'ETH': df, ...} siap dipakai panel_v14.build().

    Simbol shadow (§2.4) ikut ditarik: kolom shadow-nya perlu dihitung, dan gate
    universe yang mencegahnya jadi trade.
    """
    symbols = symbols or (tuple(cfg.UNIVERSE) + tuple(cfg.SHADOW_SYMBOLS))
    start = start or cfg.BACKTEST_START
    end = end or cfg.BACKTEST_END
    session = requests.Session()
    out = {}
    for sym in symbols:
        pair = f"{sym}{cfg.QUOTE_ASSET}"
        df = fetch_klines(pair, start, end, session=session)
        assert_contiguous(df, pair)
        out[sym] = df
        if verbose:
            print(f"  {pair:10s} {len(df):5d} lilin  {df.index.min().date()} -> "
                  f"{df.index.max().date()}  (buang {df.attrs['dropped_open_candles']} belum tutup)")
    return out
