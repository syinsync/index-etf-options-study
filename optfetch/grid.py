"""Precompute a regime x term-structure x moneyness grid of backtest results.

192 backtests (underlyings x regimes x OTM x DTE) are too many to run live on
every dashboard interaction, so we compute them once into results/grid.parquet
and let the dashboard read/pivot instantly.

    python -m optfetch.grid            # compute & write results/grid.parquet
"""
from __future__ import annotations

import time

import pandas as pd

from . import config as C
from .backtest import run_short_put

REGIMES: dict[str, tuple[str, str]] = {
    "2011-2015": ("2011-01-01", "2015-12-31"),
    "2016-2019": ("2016-01-01", "2019-12-31"),
    "2020-2025": ("2020-01-01", "2025-08-29"),
    "Full 2011-2025": ("2011-01-01", "2025-08-29"),
}
OTMS = [0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12]  # 0% = ATM
DTES = [7, 14, 21, 28]
UNDERLYINGS = ["SPY", "QQQ", "IWM"]

GRID_PATH = C.PROJECT_ROOT / "results" / "grid.parquet"

# metrics carried from each backtest summary into the grid
_METRICS = [
    "total_return_compounded", "cagr", "vol_annualized", "sharpe_annualized",
    "win_rate", "worst_week", "max_drawdown_daily", "n_trades",
    "n_assigned", "n_breached",
]


def compute_grid(underlyings=UNDERLYINGS, regimes=REGIMES, otms=OTMS, dtes=DTES,
                 log=print) -> pd.DataFrame:
    rows = []
    total = len(underlyings) * len(regimes) * len(otms) * len(dtes)
    i = 0
    for ul in underlyings:
        for rname, (s, e) in regimes.items():
            for otm in otms:
                for dte in dtes:
                    i += 1
                    rec = {"underlying": ul, "regime": rname, "start": s, "end": e,
                           "otm_pct": otm, "dte": dte}
                    try:
                        sm = run_short_put(s, e, underlying=ul, otm_pct=otm,
                                           dte_target=dte)["summary_dict"]
                        rec.update({k: sm.get(k) for k in _METRICS})
                    except Exception as ex:  # noqa: BLE001
                        log(f"[{i}/{total}] FAIL {ul} {rname} otm={otm} dte={dte}: {ex}")
                        rec.update({k: None for k in _METRICS})
                    rows.append(rec)
                    log(f"[{i}/{total}] {ul} {rname} otm={otm:.0%} dte={dte} "
                        f"sharpe={rec.get('sharpe_annualized') or float('nan'):.2f}")
    return pd.DataFrame(rows)


BENCH_PATH = C.PROJECT_ROOT / "results" / "benchmark.parquet"


def compute_benchmarks(underlyings=UNDERLYINGS, regimes=REGIMES) -> pd.DataFrame:
    """Buy-and-hold stats per (underlying, regime) — precomputed so a results-only
    dashboard needs no raw/ref data to show the benchmark rows."""
    rows = []
    for ul in underlyings:
        o = C.get_underlying(ul)
        u = pd.read_parquet(C.underlying_path(ul))
        u = u[u["secid"] == o.secid].copy()
        u["date"] = pd.to_datetime(u["date"])
        u = u.sort_values("date")
        for rname, (s, e) in regimes.items():
            c = u[(u["date"] >= pd.Timestamp(s)) & (u["date"] <= pd.Timestamp(e))]
            c = c.set_index("date")["close"]
            if len(c) < 2:
                continue
            yrs = max((c.index[-1] - c.index[0]).days / 365.25, 1e-9)
            tot = c.iloc[-1] / c.iloc[0] - 1.0
            w = c.resample("W-FRI").last().dropna().pct_change().dropna()
            volw = float(w.std())
            peak = c.cummax()
            rows.append({
                "underlying": ul, "regime": rname,
                "total_return_compounded": float(tot),
                "cagr": float((1 + tot) ** (1 / yrs) - 1),
                "win_rate": float((w > 0).mean()), "worst_week": float(w.min()),
                "vol_annualized": volw * (52 ** 0.5),
                "sharpe_annualized": float(w.mean() / volw * (52 ** 0.5)) if volw else float("nan"),
                "max_drawdown_daily": float(((c - peak) / peak).min()),
            })
    return pd.DataFrame(rows)


def main() -> None:
    t = time.time()
    df = compute_grid()
    GRID_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(GRID_PATH, index=False)
    compute_benchmarks().to_parquet(BENCH_PATH, index=False)
    print(f"\nwrote {len(df)} grid rows -> {GRID_PATH} and benchmarks -> {BENCH_PATH} "
          f"in {(time.time()-t)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
