"""Jalankan semua tes + gerbang v1.4.1 (V1_4_SPEC.md sec 5).

Gerbang v1.4.1: semua konstanta hard-coded, NOL TODO, sec 6.1 dipenuhi.
Gerbang v1.4.2: replay 2019-2026 -> 298 trade persis, mean R 0.3182.
Gerbang v1.4.3: pesan masuk memuat harga OCO, alarm hari ke-13, tanpa
                pull_request_target, kredensial tidak bocor ke log, dan keadaan
                harian (posisi terbuka + sinyal) identik dengan engine.
"""
from __future__ import annotations
import os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
fail = []


def bab(t): print(f"\n{'='*70}\n{t}\n{'='*70}")


bab("1. Tes unit")
env = dict(os.environ, PYTHONPATH=SRC)
for t in ("sanity_tests.py", "test_port_fidelity.py", "test_binance_data.py",
          "test_signal_equivalence.py", "test_daily_job.py", "test_collect.py",
          "test_daily_state.py", "test_replay_v142.py"):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", t)],
                       capture_output=True, text=True, env=env, cwd=os.path.join(ROOT, "tests"))
    tail = [l for l in r.stdout.strip().splitlines() if l.strip()][-1:]
    print(f"  {t:26s} exit={r.returncode}  {tail[0].strip() if tail else ''}")
    if r.returncode != 0:
        fail.append(f"{t} exit {r.returncode}")
        print(r.stdout[-1500:], r.stderr[-1500:])

bab("2. Gerbang: nol TODO / FIXME / XXX / placeholder")
# Bentuk penanda pekerjaan tertunda yang sungguhan: "# TODO:" atau "TODO(nama):".
# Prosa seperti "Tidak ada TODO." bukan pekerjaan tertunda, jadi penanda wajib
# diikuti ":" atau "(" supaya gerbang ini tidak menyala oleh kalimat biasa.
pat = re.compile(r"\b(TODO|FIXME|XXX|HACK|PLACEHOLDER|TBD)\s*[:(]")
hits = []
for d, _, fs in os.walk(ROOT):
    if any(x in d for x in (".git", "__pycache__", ".venv")):
        continue
    for f in fs:
        if f == "run_all.py":            # pemindai memuat polanya sendiri
            continue
        if f.endswith((".py", ".md", ".yml", ".yaml", ".txt")):
            p = os.path.join(d, f)
            for i, line in enumerate(open(p, encoding="utf-8", errors="ignore"), 1):
                if pat.search(line):
                    hits.append(f"{os.path.relpath(p, ROOT)}:{i}: {line.strip()[:90]}")
print(f"  ditemukan {len(hits)}")
for h in hits:
    print("   ", h)
if hits:
    fail.append(f"{len(hits)} TODO tersisa")

bab("3. Gerbang sec 6.1: .gitignore lengkap sebelum commit pertama")
gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
for pola in ("*.json", "*.env", "credentials*", "token*", "*.pkl"):
    ok = pola in gi
    print(f"  {pola:16s} {'ADA' if ok else 'HILANG'}")
    if not ok:
        fail.append(f".gitignore kurang {pola}")

bab("4. Gerbang sec 6.1: tidak ada pull_request_target")
bad = []
for d, _, fs in os.walk(ROOT):
    if ".git" in d:
        continue
    for f in fs:
        if f.endswith((".yml", ".yaml")):
            p = os.path.join(d, f)
            if "pull_request_target" in open(p, encoding="utf-8", errors="ignore").read():
                bad.append(os.path.relpath(p, ROOT))
print(f"  workflow memakai pull_request_target: {bad if bad else 'tidak ada'}")
if bad:
    fail.append(f"pull_request_target di {bad}")

bab("5. Gerbang: tidak ada kredensial ter-stage di git")
r = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=ROOT)
tracked = [l for l in r.stdout.splitlines() if l.strip()]
bahaya = [f for f in tracked if re.search(r"\.(json|env|pkl|csv|pem|key)$|credential|token|secret", f, re.I)]
print(f"  berkas ter-track: {len(tracked)}")
print(f"  berpola kredensial: {bahaya if bahaya else 'tidak ada'}")
if bahaya:
    fail.append(f"kredensial ter-track: {bahaya}")

bab("HASIL")
if fail:
    print("  GERBANG v1.4.1 GAGAL:")
    for f in fail:
        print("   -", f)
    raise SystemExit(1)
print("  GERBANG v1.4.1 LOLOS")
