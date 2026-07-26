"""Precompute a VIX-conditioned "fair premium" surface.

For each underlying, bucket historical PUT quotes by (DTE, OTM%, vol-index level)
and record the MEDIAN premium as a % of the forward price. Because it's a % (not
raw $), it stays comparable across 15 years of price levels — today's dollar
premium is roughly  median% x today's price.

    "VXN is 16 today; a 2%-OTM 14-DTE QQQ put has historically fetched ~0.3% of
     spot" -> multiply by today's QQQ price for a ballpark premium.

    python -m optfetch.premium_surface     # -> results/premium_surface.parquet
"""
from __future__ import annotations

import time

import duckdb
import pandas as pd

from . import config as C

SURFACE_PATH = C.PROJECT_ROOT / "results" / "premium_surface.parquet"

UNDERLYINGS = ["SPY", "QQQ", "IWM"]
DTE_TARGETS = [7, 14, 21, 28]
DTE_TOL = 2
OTM_TARGETS = [0, 2, 4, 6, 8, 10, 12]     # % below forward (0 = ATM)
OTM_TOL = 1.0
# vol-index (VIX/VXN/RVX) buckets, with display labels
VOL_BUCKETS = [(0, 15), (15, 18), (18, 22), (22, 28), (28, 200)]
VOL_LABELS = ["<15", "15-18", "18-22", "22-28", "28+"]


def _case(expr: str, targets, tol) -> str:
    parts = [f"WHEN abs({expr} - {t}) <= {tol} THEN {t}" for t in targets]
    return "CASE " + " ".join(parts) + " END"


def _vol_case() -> str:
    conds = []
    for (lo, hi), lab in zip(VOL_BUCKETS, VOL_LABELS):
        if lo == 0:
            conds.append(f"WHEN vol < {hi} THEN '{lab}'")
        elif hi >= 200:
            conds.append(f"WHEN vol >= {lo} THEN '{lab}'")
        else:
            conds.append(f"WHEN vol >= {lo} AND vol < {hi} THEN '{lab}'")
    return "CASE " + " ".join(conds) + " END"


def compute_surface(underlyings=UNDERLYINGS, log=print) -> pd.DataFrame:
    con = duckdb.connect()
    frames = []
    for ul in underlyings:
        o = C.get_underlying(ul)
        cur = str(C.curated_dir(ul) / "year=*" / "options.parquet").replace("\\", "/")
        ref = str(C.underlying_path(ul)).replace("\\", "/")
        dte_t = _case("dte", DTE_TARGETS, DTE_TOL)
        otm_t = _case("(1 - fwd_moneyness) * 100", OTM_TARGETS, OTM_TOL)
        q = f"""
        WITH v AS (
            SELECT CAST(date AS DATE) AS date, close AS vol
            FROM read_parquet('{ref}') WHERE secid = {o.vol_secid}
        ),
        o AS (
            SELECT CAST(date AS DATE) AS date, dte, fwd_moneyness,
                   mid / forward_price * 100 AS prem_pct
            FROM read_parquet('{cur}')
            WHERE cp_flag = 'P' AND mid > 0 AND forward_price > 0
              AND NOT no_forward AND NOT bid_zero
        ),
        j AS (
            SELECT {dte_t} AS dte, {otm_t} AS otm, {_vol_case()} AS vol_bucket,
                   o.prem_pct
            FROM o JOIN v USING (date)
        )
        SELECT '{ul}' AS underlying, dte, otm, vol_bucket,
               median(prem_pct) AS median_prem_pct, count(*) AS n
        FROM j
        WHERE dte IS NOT NULL AND otm IS NOT NULL AND vol_bucket IS NOT NULL
        GROUP BY dte, otm, vol_bucket
        """
        df = con.execute(q).df()
        frames.append(df)
        log(f"{ul}: {len(df)} surface cells, {int(df['n'].sum()):,} quotes")
    con.close()
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    t = time.time()
    df = compute_surface()
    SURFACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(SURFACE_PATH, index=False)
    print(f"wrote {len(df)} rows -> {SURFACE_PATH} in {(time.time()-t)/60:.1f} min",
          flush=True)


if __name__ == "__main__":
    main()
