"""Pesan Telegram. Mode kering kalau kredensial tidak ada.

Empat jenis pesan:
  ENTRY     sinyal masuk — WAJIB memuat harga OCO (SL+TP) dan ukuran posisi (§2.8)
  ALARM     hari ke-13, besok tutup paksa (§2.8)
  HEARTBEAT kabar harian walau tidak ada apa-apa
  ERROR     job gagal

Kenapa heartbeat wajib ada: jeda terpanjang tanpa sinyal di data historis adalah
299 hari, dan per 16 Agt 2026 sistem sudah sepi 294 hari. Tanpa kabar harian,
sistem yang sedang DIAM tidak bisa dibedakan dari sistem yang MATI — dan itu
persis kegagalan yang tidak akan Anda sadari sampai berbulan-bulan kemudian
(§4).

Kredensial dibaca dari environment, tidak pernah dari repo. Nilainya tidak
pernah dicetak, termasuk saat error.
"""
from __future__ import annotations
import html
import os
import re
import time
from datetime import datetime, timezone

import requests

import config_v14 as cfg

API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = 20
MAX_RETRIES = 3
# Coba-ulang tanpa jeda bukan coba-ulang: tiga POST dalam sepersekian detik akan
# menabrak gangguan yang sama persis, dan 429 Telegram justru diperparah. Jeda
# 2 dan 4 detik; total terburuk ~12 detik untuk dua format, jauh di bawah jatah
# 15 menit workflow.
RETRY_BACKOFF = 2.0


def credentials() -> tuple[str | None, str | None]:
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def is_dry_run() -> bool:
    """Kering kalau diminta eksplisit ATAU kredensial belum ada."""
    if os.environ.get("DRY_RUN", "").strip() not in ("", "0", "false", "False"):
        return True
    token, chat = credentials()
    return not (token and chat)


def send(text: str) -> bool:
    """Kirim satu pesan. Di mode kering: cetak, jangan kirim.

    Return True kalau terkirim (atau tercetak di mode kering).
    """
    if is_dry_run():
        print("--- TELEGRAM (MODE KERING, tidak dikirim) " + "-" * 28)
        print(text)
        print("-" * 70)
        return True
    token, chat = credentials()
    # Dua percobaan format: HTML dulu, lalu teks polos.
    #
    # Ini jaring pengaman, bukan pengganti _e(). Semua teks dinamis SUDAH
    # di-escape, tapi kalau suatu saat ada field baru yang lupa di-escape,
    # akibatnya adalah pesan yang hilang tanpa jejak — dan pesan yang paling
    # mungkin memuat karakter aneh justru pesan ERROR. Lebih baik pesan sampai
    # tanpa huruf tebal daripada tidak sampai sama sekali.
    for parse_mode in ("HTML", None):
        payload = {"chat_id": chat, "disable_web_page_preview": True,
                   "text": text if parse_mode else _strip_tags(text)}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = requests.post(API.format(token=token), json=payload, timeout=TIMEOUT)
                if r.status_code == 200:
                    return True
                # jangan pernah cetak respons mentah: URL-nya memuat token
                print(f"  Telegram HTTP {r.status_code} "
                      f"(format={parse_mode or 'polos'}, percobaan {attempt}/{MAX_RETRIES})")
                if r.status_code == 400:
                    break            # format ditolak -> coba format berikutnya
            except requests.RequestException as e:
                print(f"  Telegram gagal kirim: {type(e).__name__} "
                      f"(format={parse_mode or 'polos'}, percobaan {attempt}/{MAX_RETRIES})")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * 2 ** (attempt - 1))
    return False


def _strip_tags(text: str) -> str:
    """Buang tag HTML dan kembalikan entitas ke bentuk aslinya, untuk kiriman
    ulang sebagai teks polos."""
    return html.unescape(re.sub(r"</?[a-zA-Z][^>]*>", "", text))


def _f(x: float, d: int = 2) -> str:
    return f"{x:,.{d}f}"


def _e(x) -> str:
    """Escape teks dinamis sebelum masuk pesan HTML.

    Tanpa ini, satu karakter '<' saja membuat Telegram menolak seluruh pesan
    dengan 400 "can't parse entities". Yang paling berbahaya adalah pesan ERROR:
    nama tipe exception Python hampir selalu berbentuk <class '...'>, sehingga
    pemberitahuan kegagalan justru gagal terkirim -- kegagalan senyap tepat di
    saat Anda paling perlu tahu.
    """
    return html.escape(str(x), quote=False)


def entry_message(trade: dict) -> str:
    """Pesan sinyal masuk. WAJIB memuat harga OCO — tanpa itu Dew tidak tahu
    di mana memasang order, dan asumsi backtest soal harga exit jadi tidak
    berdasar (§2.8)."""
    sym = _e(trade["symbol"])
    # Harga saat kirim: hanya informasi. Cron di produksi jalan jam-jam sesudah
    # open, jadi harga pasar sudah bergerak dari acuan -- SL/TP tetap dari acuan.
    sekarang = ""
    if trade.get("px_at_send") is not None:
        drift = 100 * (trade["px_at_send"] / trade["entry_px"] - 1)
        sekarang = (f"Harga saat pesan ini dikirim: {_f(trade['px_at_send'])} "
                    f"({'+' if drift >= 0 else '−'}{_f(abs(drift))}% dari acuan)\n")
    return (
        f"🟢 <b>SINYAL MASUK — {sym}</b>\n"
        f"Sinyal: {_e(trade['signal_date'])} (close 00:00 UTC)\n"
        f"\n"
        f"<b>BELI di harga open hari ini</b>\n"
        f"Acuan entry : <b>{_f(trade['entry_px'])}</b>\n"
        f"{sekarang}"
        f"\n"
        f"<b>Pasang SATU order OCO sekarang:</b>\n"
        f"  Take Profit : <b>{_f(trade['oco_take_profit'])}</b>  "
        f"(+{_f(100*(trade['oco_take_profit']/trade['entry_px']-1))}%)\n"
        f"  Stop Loss   : <b>{_f(trade['oco_stop_loss'])}</b>  "
        f"(−{_f(100*(1-trade['oco_stop_loss']/trade['entry_px']))}%)\n"
        f"\n"
        f"Ukuran      : <b>{_f(100*trade['size_frac_used'],1)}% ekuitas</b>\n"
        f"Risiko      : {_f(100*cfg.RISK_PER_TRADE,1)}% akun kalau kena SL\n"
        f"\n"
        f"Tutup paksa : {_e(trade['hold_force_exit_date'])} (hari ke-{cfg.HOLD_MAX_DAYS})\n"
        f"Alarm       : {_e(trade['hold_warning_date'])}\n"
        f"\n"
        f"<i>Forward test tanpa modal. Spot, long-only.</i>"
    )


def hold_alarm_message(pos: dict) -> str:
    """Alarm hari ke-13. OCO tidak bisa menangani batas waktu — tidak ada jenis
    order yang berbunyi 'tutup kalau sudah 14 hari' (§2.8)."""
    return (
        f"🟡 <b>ALARM HOLD — {_e(pos['symbol'])}</b>\n"
        f"Posisi masuk {_e(pos['entry_date'])}, hari ini <b>hari ke-{_e(pos['days_held'])}</b>.\n"
        f"\n"
        f"<b>Besok ({_e(pos['hold_force_exit_date'])}) tutup paksa</b> di harga berapa pun,\n"
        f"lalu <b>batalkan order OCO-nya</b> supaya tidak menggantung.\n"
        f"\n"
        f"Entry {_f(pos['entry_px'])} | TP {_f(pos['oco_take_profit'])} | "
        f"SL {_f(pos['oco_stop_loss'])}\n"
        f"\n"
        f"<i>24% trade historis berakhir lewat batas waktu ini, bukan lewat SL/TP.</i>"
    )


def heartbeat_message(state: dict) -> str:
    """Kabar harian. Sengaja tetap dikirim walau tidak ada apa-apa."""
    lines = [f"⚪ <b>Heartbeat</b> — {_e(state['run_date'])} {_e(state['run_time'])} UTC",
             f"Data s/d: {_e(state['data_through'])}"]
    if state["open_positions"]:
        lines.append("")
        lines.append(f"<b>Posisi terbuka ({len(state['open_positions'])}):</b>")
        for p in state["open_positions"]:
            lines.append(f"  {_e(p['symbol'])} — hari ke-{_e(p['days_held'])}, entry {_f(p['entry_px'])}, "
                         f"TP {_f(p['oco_take_profit'])}, SL {_f(p['oco_stop_loss'])}")
    else:
        lines.append("")
        lines.append("Posisi terbuka: <b>tidak ada</b>")
    lines.append("")
    lines.append(f"Sinyal hari ini: <b>{state['n_signals']}</b>")
    if state.get("days_since_last_signal") is not None:
        d = state["days_since_last_signal"]
        lines.append(f"Sinyal terakhir: {_e(state['last_signal_date'])} "
                     f"(<b>{d} hari lalu</b>)")
        if d > 200:
            lines.append(f"<i>Jeda terpanjang historis 299 hari. Diam bukan berarti rusak.</i>")
    lines.append("")
    lines.append(f"Shadow log: {state['n_shadow_rows']} baris ditulis")
    return "\n".join(lines)


def error_message(stage: str, err: str) -> str:
    # _e() WAJIB di sini. Nama tipe exception Python berbentuk <class '...'> dan
    # pesan error sering memuat '<', '>', '&'. Tanpa escape, Telegram menolak
    # seluruh pesan dengan 400 "can't parse entities" -- pemberitahuan kegagalan
    # ikut gagal, tepat di saat Anda paling perlu tahu.
    return (f"🔴 <b>JOB GAGAL</b> — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n"
            f"Tahap: {_e(stage)}\n"
            f"Sebab: {_e(err[:400])}\n\n"
            f"<i>Sinyal hari ini TIDAK dapat dipercaya. Periksa GitHub Actions.</i>")
