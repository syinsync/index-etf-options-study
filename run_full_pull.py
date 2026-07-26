"""Full history pull for SPY + QQQ, 2011 -> 2025 (weekly-option era).

Append-only & resumable: complete years already on disk are skipped, so a
restart continues where it left off. Curated is rebuilt from raw each run.
"""
import time
from optfetch import download, build_curated, config as C

START_YEAR, END_YEAR = 2011, 2025
UNDERLYINGS = ["SPY", "QQQ"]

t_all = time.time()
for ul in UNDERLYINGS:
    print(f"\n########## {ul} {START_YEAR}-{END_YEAR} ##########", flush=True)
    t0 = time.time()
    download.download_range(START_YEAR, END_YEAR, underlying=ul)  # skips existing
    print(f"[{ul}] download phase done in {(time.time()-t0)/60:.1f} min", flush=True)
    t0 = time.time()
    build_curated.build_range(START_YEAR, END_YEAR, underlying=ul)
    print(f"[{ul}] curated build done in {(time.time()-t0)/60:.1f} min", flush=True)

print(f"\nALL DONE in {(time.time()-t_all)/60:.1f} min", flush=True)
