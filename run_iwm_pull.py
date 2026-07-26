"""Full history pull for IWM (Russell 2000 ETF), 2011 -> 2025."""
import time
from optfetch import download, build_curated

UL = "IWM"
t0 = time.time()
download.download_range(2011, 2025, underlying=UL)   # opprcd + fwdprd + ref (IWM+RVX)
print(f"[{UL}] download done in {(time.time()-t0)/60:.1f} min", flush=True)
t0 = time.time()
build_curated.build_range(2011, 2025, underlying=UL)
print(f"[{UL}] curated build done in {(time.time()-t0)/60:.1f} min", flush=True)
print("DONE", flush=True)
