"""Job harian — dijalankan GitHub Actions cron 00:05 UTC (V1_4_SPEC.md §5).

Alur: tarik data -> bangun panel -> replay penuh -> sinyal hari ini + alarm hold
-> Telegram + Google Sheets -> catat eksekusi (jam kirim + harga saat kirim di
sheet `entries`).

--------------------------------------------------------------------------------
KEPUTUSAN DESAIN TERPENTING: JOB INI TANPA STATE.

Setiap hari sistem me-replay SELURUH riwayat sejak 2019-01-01, bukan menyimpan
"posisi saya sekarang" di suatu tempat lalu memperbaruinya. Posisi terbuka hari
ini dibaca dari hasil replay itu.

Kenapa begitu:
  * Gerbang v1.4.2 membuktikan replay = backtest, persis. Kalau job harian
    memakai jalur lain (state tersimpan yang diperbarui bertahap), bukti itu
    tidak berlaku untuk yang benar-benar jalan tiap hari.
  * State tersimpan bisa melenceng diam-diam: satu job gagal, satu baris gagal
    tulis, satu kali retry ganda — dan posisi tercatat jadi tidak cocok dengan
    yang seharusnya, tanpa ada yang tahu.
  * Replay penuh cuma ~10 detik dan datanya gratis. Tidak ada alasan menukar
    kebenaran dengan kecepatan di sini.

Konsekuensi yang harus diterima: posisi yang dilacak sistem adalah posisi
SIMULASI, memakai harga model. Kalau eksekusi manual Dew meleset dari harga itu,
shadow log tetap mencatat versi sistem. Itu memang yang diinginkan di v1.4.4 —
yang diukur adalah apa yang SISTEM katakan, bukan apa yang Dew lakukan. Selisih
antara keduanya baru diukur di v1.4.5.
--------------------------------------------------------------------------------
"""
from __future__ import annotations
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta

import pandas as pd

# Log job memuat emoji dan tanda kutip tipografis. Konsol Windows default cp1252
# akan melempar UnicodeEncodeError dan MEMBUNUH job hanya karena mencetak. Runner
# GitHub Actions memakai UTF-8, tapi pengembangan lokal tidak — dan job yang mati
# saat logging adalah kegagalan paling konyol yang bisa terjadi.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config_v14 as cfg
import binance_data
import notify
import panel_v14
import pipeline
import shadow
import sheets

WARMUP_TAIL_DAYS = 5     # berapa hari ke belakang dianggap "baru saja" untuk sinyal


def _ci_error(tahap: str, pesan: str) -> None:
    """Tulis sebab kegagalan sebagai ANNOTATION GitHub Actions.

    KENAPA INI ADA. Log mentah GitHub Actions disajikan dari blob storage
    (productionresultssa18.blob.core.windows.net). Pemeriksa otomatis yang
    berjalan di luar GitHub -- termasuk routine harian yang melacak gerbang
    v1.4.3 -- sering diblokir kebijakan egress ke host itu, sehingga satu-
    satunya yang bisa mereka baca adalah endpoint annotations. Selama ini
    endpoint itu cuma berisi "Process completed with exit code 1": nol
    informasi tentang APA yang gagal.

    Akibatnya sudah terjadi sungguhan. Pemeriksa harian 8 Sep 2026 menuduh
    kegagalan 5 Sep disebabkan HTTP 451 Binance, padahal sebabnya HTTP 503
    Google Sheets -- ia menebak dari pesan commit karena tidak punya cara lain,
    lalu mengirim tebakan itu sebagai notifikasi. Diagnosis yang salah arah
    lebih mahal daripada tidak ada diagnosis sama sekali.

    Satu baris di sini membuat sebabnya terbaca lewat API, permanen, tanpa
    kredensial apa pun. Perintah workflow GitHub harus SATU baris, jadi newline
    dan '%' wajib di-encode.
    """
    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        return
    teks = f"{tahap}: {pesan}"
    aman = (teks.replace("%", "%25").replace(chr(13), "%0D").replace(chr(10), "%0A"))
    print(f"::error::{aman[:900]}", flush=True)


def _fmt(d) -> str:
    return pd.Timestamp(d).date().isoformat()


def collect(now_utc: datetime | None = None, end: str | None = None,
            raw: dict | None = None, entry_price_fn=None) -> dict:
    """Tarik data, replay, dan susun semua yang perlu dikirim hari ini.

    `raw` dan `entry_price_fn` bisa disuntik untuk pengujian. Bukan kemewahan:
    selama fungsi ini hanya bisa jalan dengan menembak jaringan, ia tidak pernah
    ikut diuji — dan itulah yang meloloskan KeyError 'size_frac_used' ke cron
    produksi pertama. Bug-nya hanya muncul pada hari yang punya posisi terbuka,
    karena sum() atas daftar kosong tidak pernah menyentuh kunci yang hilang.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    # Lilin kemarin adalah lilin terakhir yang sudah tutup saat job jalan 00:05 UTC.
    # binance_data juga menyaring lewat close_time; ini sekadar batas atas permintaan.
    end = end or _fmt(now_utc.date())
    entry_price_fn = entry_price_fn or binance_data.current_open

    syms = tuple(cfg.UNIVERSE) + tuple(cfg.SHADOW_SYMBOLS)
    if raw is None:
        raw = binance_data.load(syms, start=cfg.BACKTEST_START, end=end, verbose=True)
    syms = tuple(s for s in syms if s in raw)

    # data_through ditentukan HANYA oleh universe produksi. Simbol shadow (§2.4)
    # tidak boleh ikut menentukannya: kalau SOL sehari saja telat/di-halt di
    # Binance, min() atas seluruh simbol akan memundurkan hari sinyal produksi
    # satu hari penuh -- shadow berubah dari pencatat jadi pemblokir, persis yang
    # dilarang §2.4. Baris shadow untuk hari itu memang lalu kosong; itu jujur.
    produksi = [s for s in cfg.UNIVERSE if s in raw]
    if not produksi:
        raise RuntimeError(f"tidak satu pun simbol produksi {cfg.UNIVERSE} ada di data")
    data_through = min(raw[s].index.max() for s in produksi)

    # Hari penanda: memaksa engine mengurus barrier di hari data terakhir.
    # Lihat pipeline.append_sentinel_day() -- tanpa ini 76% sinyal hilang.
    raw_engine, sentinel_day = pipeline.append_sentinel_day(raw, data_through)

    panel = panel_v14.build(raw_engine)
    panel = shadow.add_shadow_columns(panel)
    regime = panel_v14.empty_regime(panel)

    c = cfg.production_engine_config()
    c.end = _fmt(sentinel_day)
    import engine
    trades = pipeline.with_execution_plan(engine.run(panel, regime, c))
    signal_day = data_through

    # --- posisi yang MASIH terbuka -----------------------------------------
    # engine menutup paksa yang masih hidup di tanggal terakhir (= hari penanda)
    # dan menandainya "eod". Di backtest itu artinya akhir jendela; di sini
    # artinya benar-benar masih terbuka saat pasar dibuka hari ini -- barrier
    # hari data terakhir sudah diurus lebih dulu, berkat hari penanda.
    # `days_held` pada baris itu = nomor hari posisi HARI INI (lihat
    # pipeline.append_sentinel_day), jadi alarm hari ke-13 jatuh tepat sehari
    # sebelum tutup paksa, sesuai bunyi pesannya.
    open_pos, alarms, open_symbols = [], [], set()
    for _, t in pipeline.still_open(trades).iterrows():
        p = {"symbol": t["symbol"], "entry_date": _fmt(t["entry_date"]),
             "entry_px": float(t["entry_px"]),
             "oco_stop_loss": float(t["oco_stop_loss"]),
             "oco_take_profit": float(t["oco_take_profit"]),
             "days_held": int(t["days_held"]),
             # size_frac_used WAJIB ikut: ekuitas yang sudah dipakai posisi ini
             # dikurangkan dari jatah kandidat baru di bawah. Tanpa kunci ini,
             # job GAGAL dengan KeyError -- tapi hanya pada hari yang punya
             # posisi terbuka, karena sum() atas daftar kosong tidak error.
             "size_frac_used": float(t["size_frac_used"]),
             "size_frac": float(t["size_frac"]),
             "hold_force_exit_date": _fmt(t["hold_force_exit_date"])}
        open_pos.append(p)
        open_symbols.add(t["symbol"])
        if int(t["days_held"]) == cfg.TIME_EXIT_WARNING_DAY:
            alarms.append(p)

    # --- sinyal yang lahir di close hari terakhir -> dieksekusi HARI INI -----
    # Tidak bisa dibaca dari trade log: engine butuh baris hari berikutnya untuk
    # harga eksekusi, dan hari berikutnya itu HARI INI yang lilinnya baru dibuka.
    # Harga entry diambil dari OPEN lilin berjalan — sah, karena open sebuah
    # lilin sudah final sejak detik pertama. Kesetaraan seleksi dengan engine
    # dijaga tests/test_signal_equivalence.py.
    new_entries = []
    slots = cfg.MAX_POSITIONS - len(open_symbols)
    cand = pipeline.signals_for_day(panel, signal_day, open_symbols, slots,
                                    require_entry_price=False)
    # Ekuitas yang sudah terpakai posisi terbuka. Sisanya dibagi ke kandidat baru
    # menurut urutan peringkat. Tanpa ini, dua sinyal serentak menghasilkan total
    # eksposur > 100% -- mustahil di spot tanpa leverage, dan memaksa Dew
    # berimprovisasi (lihat pipeline.apply_exposure_cap).
    bebas = max(cfg.MAX_EXPOSURE_FRAC - sum(p["size_frac_used"] for p in open_pos), 0.0)
    for sym, row in cand.iterrows():
        try:
            day_open, entry_px = entry_price_fn(f"{sym}{cfg.QUOTE_ASSET}")
        except Exception as e:
            raise RuntimeError(f"harga open hari ini untuk {sym} tidak terbaca: {e}") from None
        # Kontrak §2.2: sinyal di close hari t DIISI di open hari t+1, tanpa
        # pengecualian. Kalau lilin yang dipakai bukan hari sesudah signal_day --
        # misalnya Binance telat menerbitkan lilin kemarin, sehingga data_through
        # mundur -- maka yang dieksekusi bukan lagi trade yang sama dengan yang
        # di-backtest. Berhenti keras; diam-diam menggeser hari eksekusi adalah
        # persis jenis penyimpangan yang membuat v1.4.4 kehilangan arti.
        if pd.Timestamp(day_open).normalize() != sentinel_day:
            raise RuntimeError(
                f"{sym}: harga eksekusi diambil dari lilin {_fmt(day_open)}, "
                f"padahal sinyal lahir di {_fmt(signal_day)} sehingga fill WAJIB "
                f"di {_fmt(sentinel_day)} (open t+1). Data pasar kemungkinan telat.")
        atr = float(row["atr14"])
        sl, tp = cfg.barriers(entry_px, atr)
        wanted, _ = cfg.position_size_frac(entry_px, atr)
        used = min(wanted, bebas)          # §2.6: ambil sisa yang ada, jangan naikkan risk
        bebas = max(bebas - used, 0.0)
        entry_date = day_open
        new_entries.append({
            "symbol": sym, "signal_date": _fmt(signal_day),
            "entry_date": _fmt(entry_date), "entry_px": entry_px,
            "atr14": atr, "oco_stop_loss": sl, "oco_take_profit": tp,
            "size_frac_wanted": wanted, "size_frac_used": used,
            "size_frac": (used / wanted) if wanted > 0 else 1.0,
            "hold_warning_date": _fmt(entry_date + timedelta(days=cfg.TIME_EXIT_WARNING_DAY - 1)),
            "hold_force_exit_date": _fmt(entry_date + timedelta(days=cfg.TIME_EXIT_DAY - 1)),
        })

    # --- shadow log: SETIAP kandidat, termasuk yang tidak jadi trade --------
    shadow_rows = [r for s in syms
                   if (r := shadow.row_for(panel, s, signal_day))]

    # Sinyal yang lahir HARI INI tidak ada di trade log: engine tidak bisa
    # membukanya (harga eksekusi baru ada hari ini). Tanpa baris ini heartbeat
    # bisa menulis "sinyal terakhir 299 hari lalu" di hari yang barusan
    # mengirim sinyal.
    last_sig = trades["signal_date"].max() if len(trades) else None
    if new_entries:
        last_sig = signal_day
    return {
        "run_utc": now_utc,
        "run_date": now_utc.strftime("%Y-%m-%d"),
        "run_time": now_utc.strftime("%H:%M"),
        "data_through": _fmt(data_through),
        "new_entries": new_entries,
        "open_positions": open_pos,
        "alarms": alarms,
        "shadow_rows": shadow_rows,
        "n_signals": len(new_entries),
        "n_shadow_rows": len(shadow_rows),
        "last_signal_date": _fmt(last_sig) if last_sig is not None else None,
        "days_since_last_signal": int((signal_day - last_sig).days) if last_sig is not None else None,
        "trades": trades,
    }


def _harga_saat_kirim(sym: str, price_fn) -> float | None:
    """Harga pasar sekarang, atau None. Kegagalan di sini TIDAK boleh menahan
    sinyal: ini pengukuran, bukan bagian dari keputusan trading."""
    try:
        return float(price_fn(f"{sym}{cfg.QUOTE_ASSET}"))
    except Exception as e:
        print(f"  harga saat kirim {sym} tidak terbaca ({type(e).__name__}) -- sinyal tetap dikirim")
        return None


def entry_record(e: dict, sent_utc: datetime, sent_ok: bool) -> dict:
    """Satu baris sheet `entries`: jarak waktu dan harga antara model dan kiriman."""
    fill_day = datetime.fromisoformat(e["entry_date"]).replace(tzinfo=timezone.utc)
    px = e.get("px_at_send")
    return {
        "sent_utc": sent_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": e["symbol"], "signal_date": e["signal_date"],
        "entry_date": e["entry_date"], "model_entry_px": e["entry_px"],
        "px_at_send": px,
        "drift_pct": None if px is None else round(100 * (px / e["entry_px"] - 1), 4),
        "lag_min": int((sent_utc - fill_day).total_seconds() // 60),
        "oco_stop_loss": e["oco_stop_loss"], "oco_take_profit": e["oco_take_profit"],
        "size_frac_used": e["size_frac_used"], "sent_ok": sent_ok,
    }


def dispatch(state: dict, price_fn=None, clock=None) -> list[str]:
    """Kirim semua pesan + tulis Sheets. Kembalikan daftar kegagalan.

    `price_fn` dan `clock` bisa disuntik untuk pengujian, sama seperti collect().
    """
    price_fn = price_fn or binance_data.current_price
    clock = clock or (lambda: datetime.now(timezone.utc))
    problems = []

    entries = []
    for e in state["new_entries"]:
        e = dict(e, px_at_send=_harga_saat_kirim(e["symbol"], price_fn))
        terkirim = notify.send(notify.entry_message(e))
        if not terkirim:
            problems.append(f"gagal kirim sinyal masuk {e['symbol']}")
        entries.append(entry_record(e, clock(), terkirim))

    try:
        sheets.append_entries(entries)
    except Exception as e:
        problems.append(f"gagal tulis entries: {type(e).__name__}: {e}")

    for a in state["alarms"]:
        if not notify.send(notify.hold_alarm_message(a)):
            problems.append(f"gagal kirim alarm hold {a['symbol']}")

    try:
        sheets.append_shadow(state["shadow_rows"])
    except Exception as e:
        problems.append(f"gagal tulis shadow_log: {type(e).__name__}: {e}")

    # Heartbeat dikirim TERAKHIR supaya ia melaporkan hasil sebenarnya, bukan
    # niat. Kalau ada yang gagal di atas, itu ikut kelihatan di pesan error.
    if not notify.send(notify.heartbeat_message(state)):
        problems.append("gagal kirim heartbeat")

    try:
        sheets.append_run(status="ok" if not problems else "partial",
                          data_through=state["data_through"],
                          n_signals=state["n_signals"],
                          n_open=len(state["open_positions"]),
                          n_shadow=state["n_shadow_rows"],
                          n_alarms=len(state["alarms"]),
                          note="; ".join(problems)[:400])
    except Exception as e:
        problems.append(f"gagal tulis runs: {type(e).__name__}: {e}")

    return problems


def assert_not_silently_dry() -> None:
    """Di CI, kredensial yang hilang WAJIB menggagalkan job.

    Tanpa ini ada mode gagal yang sangat berbahaya: cron jalan tanpa secret ->
    notify/sheets diam-diam masuk mode kering -> pesan cuma tercetak ke log ->
    job keluar dengan kode 0 -> centang hijau di GitHub.

    Akibatnya gerbang v1.4.3 ("7 hari berturut tanpa kegagalan job") bisa LULUS
    selama seminggu penuh sementara nol pesan pernah terkirim dan nol baris
    pernah masuk Sheets. Hijau palsu itu lebih buruk daripada merah jujur.

    DRY_RUN=1 tetap dihormati -- itu permintaan eksplisit, bukan kelalaian.
    """
    if os.environ.get("DRY_RUN", "").strip() not in ("", "0", "false", "False"):
        return
    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        return
    hilang = [n for n, v in (
        ("TELEGRAM_BOT_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN")),
        ("TELEGRAM_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID")),
        ("GOOGLE_SERVICE_ACCOUNT_JSON", os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")),
        ("SHEET_ID", os.environ.get("SHEET_ID"))) if not v]
    if hilang:
        _ci_error("kredensial", "secret hilang di GitHub Actions: " + ", ".join(hilang))
        raise SystemExit(
            "GAGAL: berjalan di GitHub Actions tanpa secret " + ", ".join(hilang) + ".\n"
            "Job akan diam-diam masuk mode kering dan melapor sukses tanpa mengirim\n"
            "apa pun. Pasang secret-nya, atau set DRY_RUN=1 kalau memang disengaja.")


def main() -> int:
    assert_not_silently_dry()
    kering = notify.is_dry_run() or sheets.is_dry_run()
    print(f"=== Job harian {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
          f"{'  [MODE KERING]' if kering else ''} ===")
    if kering:
        print("  Telegram kering:", notify.is_dry_run(), "| Sheets kering:", sheets.is_dry_run())

    try:
        state = collect()
    except Exception as e:
        # Kegagalan tarik/hitung berarti sinyal hari ini tidak bisa dipercaya.
        # Wajib berisik: diam adalah mode gagal yang paling berbahaya di sini.
        print(traceback.format_exc())
        _ci_error("tarik data / replay", f"{type(e).__name__}: {e}")
        notify.send(notify.error_message("tarik data / replay", f"{type(e).__name__}: {e}"))
        try:
            sheets.append_run("error", "-", 0, 0, 0, 0, f"{type(e).__name__}: {e}")
        except Exception:
            pass
        return 1

    print(f"\n  data s/d          : {state['data_through']}")
    print(f"  sinyal masuk baru : {state['n_signals']}")
    print(f"  posisi terbuka    : {len(state['open_positions'])}")
    print(f"  alarm hari ke-13  : {len(state['alarms'])}")
    print(f"  baris shadow log  : {state['n_shadow_rows']}")
    if state["days_since_last_signal"] is not None:
        print(f"  sinyal terakhir   : {state['last_signal_date']} "
              f"({state['days_since_last_signal']} hari lalu)")
    print()

    problems = dispatch(state)
    if problems:
        print("\n  MASALAH:")
        for p in problems:
            print("   -", p)
        _ci_error("pengiriman", "; ".join(problems))
        notify.send(notify.error_message("pengiriman", "; ".join(problems)))
        return 1
    print("\n  Job selesai tanpa masalah.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
