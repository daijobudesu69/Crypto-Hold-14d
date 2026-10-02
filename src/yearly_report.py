"""Laporan backtest per tahun kalender — replay 2019 s/d BACKTEST_END lewat pipa produksi.

Menjawab "berapa P&L per tahun?" yang tidak bisa dijawab dari angka headline
13.45x. Angka itu dihitung engine.summarize() sebagai cumprod(1 + 3% x R): setiap
trade diberi risiko 3% penuh, BERURUTAN, tanpa cap eksposur 100%. Di sini ada
dua kolom supaya keduanya bisa dibandingkan:

  model_3pctR   cumprod(1 + 0.03 x R_net)            -> mereproduksi 13.45x
  portofolio    cumprod(1 + size_frac_used x net_ret) -> ukuran SESUDAH cap 100%
                                                         (pipeline.apply_exposure_cap)

Keduanya dibukukan per tanggal EXIT dan dimajemukkan berurutan; itu pendekatan
(posisi yang tumpang-tindih tidak dihitung dari ekuitas yang sama persis), tapi
pendekatan yang sama dengan angka headline.

KEAMANAN (§6.1 aturan 3): laporan ini HANYA mencakup jendela backtest
(<= BACKTEST_END), yang angkanya memang sudah publik di README. Trade forward
test tidak pernah ikut — mereka mengungkap posisi, dan repo ini publik.

Jalan di GitHub Actions (workflow backtest-report.yml): runner bisa menjangkau
Binance, dan hasilnya diterbitkan sebagai check run supaya terbaca lewat API
tanpa harus membuka log mentah.
"""
from __future__ import annotations
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config_v14 as cfg
import pipeline


def yearly_table(trades: pd.DataFrame, closes: dict[str, pd.Series]) -> pd.DataFrame:
    """Satu baris per tahun kalender (menurut tanggal exit) + baris TOTAL."""
    t = trades.sort_values(["exit_date", "symbol"]).copy()
    t["year"] = t["exit_date"].dt.year
    t["model"] = 1 + 0.03 * t["R_net"]
    t["porto"] = 1 + t["size_frac_used"] * t["net_ret"]

    def dd(g: pd.Series) -> float:
        eq = g.cumprod()
        return float((eq / np.maximum(eq.cummax(), 1.0) - 1).min())

    rows = []
    for y, g in t.groupby("year"):
        r = {"tahun": str(y), "trade": len(g), "win_pct": 100 * (g["R_net"] > 0).mean(),
             "sum_R": g["R_net"].sum(), "mean_R": g["R_net"].mean(),
             "model_3pctR_pct": 100 * (g["model"].prod() - 1),
             "portofolio_pct": 100 * (g["porto"].prod() - 1),
             "maxdd_porto_pct": 100 * dd(g["porto"])}
        for sym, c in closes.items():
            cy = c[c.index.year == y]
            # buy & hold dari close akhir tahun sebelumnya (atau close pertama)
            prev = c[c.index.year < y]
            base = prev.iloc[-1] if len(prev) else cy.iloc[0]
            r[f"bh_{sym}_pct"] = 100 * (cy.iloc[-1] / base - 1) if len(cy) else np.nan
        rows.append(r)

    tahun = (t["exit_date"].max() - t["entry_date"].min()).days / 365.25
    tot = {"tahun": "TOTAL", "trade": len(t), "win_pct": 100 * (t["R_net"] > 0).mean(),
           "sum_R": t["R_net"].sum(), "mean_R": t["R_net"].mean(),
           "model_3pctR_pct": 100 * (t["model"].prod() - 1),
           "portofolio_pct": 100 * (t["porto"].prod() - 1),
           "maxdd_porto_pct": 100 * dd(t["porto"])}
    for sym, c in closes.items():
        tot[f"bh_{sym}_pct"] = 100 * (c.iloc[-1] / c.iloc[0] - 1)
    rows.append(tot)
    out = pd.DataFrame(rows)
    out.attrs["years"] = tahun
    out.attrs["cagr_model_pct"] = 100 * (t["model"].prod() ** (1 / tahun) - 1)
    out.attrs["cagr_porto_pct"] = 100 * (t["porto"].prod() ** (1 / tahun) - 1)
    return out


def to_markdown(tab: pd.DataFrame, n_trades: int, mean_r: float) -> str:
    cols = list(tab.columns)
    fmt = {"trade": "{:.0f}", "win_pct": "{:.1f}", "sum_R": "{:+.2f}", "mean_R": "{:+.3f}"}
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in tab.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if c == "tahun":
                cells.append(f"**{v}**" if v == "TOTAL" else str(v))
            elif isinstance(v, float) and np.isnan(v):
                cells.append("–")
            else:
                cells.append(fmt.get(c, "{:+.1f}").format(v))
        lines.append("| " + " | ".join(cells) + " |")
    cocok = (n_trades == cfg.REPLAY_EXPECTED_N_TRADES
             and round(mean_r, 4) == cfg.REPLAY_EXPECTED_MEAN_R)
    return "\n".join([
        f"Replay {cfg.BACKTEST_START} s/d {cfg.BACKTEST_END}: {n_trades} trade, "
        f"mean R {mean_r:+.4f} -> "
        + ("COCOK dengan gerbang v1.4.2" if cocok else "TIDAK COCOK dengan gerbang v1.4.2 — angka di bawah TIDAK sah"),
        "",
        f"CAGR model 3%R: {tab.attrs['cagr_model_pct']:+.1f}%/thn | "
        f"CAGR portofolio (cap 100%): {tab.attrs['cagr_porto_pct']:+.1f}%/thn | "
        f"rentang {tab.attrs['years']:.2f} thn",
        "",
        *lines,
        "",
        "Tahun = tahun tanggal EXIT. bh_* = buy & hold close-ke-close tahun itu. "
        "maxdd = drawdown trade-ke-trade, bukan harian.",
    ])


def publish_check(title: str, summary: str) -> None:
    """Terbitkan sebagai check run di commit ini. Diam di luar GitHub Actions."""
    token, repo, sha = (os.environ.get(k) for k in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_SHA"))
    if not (token and repo and sha):
        return
    import requests
    r = requests.post(f"https://api.github.com/repos/{repo}/check-runs", timeout=30,
                      headers={"Authorization": f"Bearer {token}",
                               "Accept": "application/vnd.github+json"},
                      data=json.dumps({"name": "Laporan backtest per tahun", "head_sha": sha,
                                       "status": "completed", "conclusion": "neutral",
                                       "output": {"title": title, "summary": summary[:60000]}}))
    print(f"check run: HTTP {r.status_code}")


def main() -> int:
    import binance_data
    raw = binance_data.load(tuple(cfg.UNIVERSE) + tuple(cfg.SHADOW_SYMBOLS),
                            start=cfg.BACKTEST_START, end=cfg.BACKTEST_END)
    trades = pipeline.with_execution_plan(pipeline.build_trades(raw))
    closes = {s: raw[s]["close"] for s in cfg.UNIVERSE}
    tab = yearly_table(trades, closes)
    md = to_markdown(tab, len(trades), float(trades["R_net"].mean()))
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(md + "\n")
    publish_check(f"CAGR portofolio {tab.attrs['cagr_porto_pct']:+.1f}%/thn, "
                  f"model 3%R {tab.attrs['cagr_model_pct']:+.1f}%/thn", md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
