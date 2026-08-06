"""Curated layer: enrich the verbatim landing parquet for querying.

Derived (cheap to rebuild from landing, so definitions can evolve freely):
  strike          strike_price / 1000        (index points)
  dte             calendar days exdate-date  (the chosen DTE convention)
  fwd_moneyness   strike / forward_price      (1.0 = ATM-forward; 1.05 = 5% OTM call)
  mid, spread     from best_bid/best_offer
  quality flags   bid_zero, crossed, zero_volume, no_oi, no_iv

Nothing is dropped here — flags are stored so filtering happens at query time.
"""
from __future__ import annotations

import argparse

import duckdb

from . import config as C


# fwd_moneyness is a ratio: strike / forward_to_expiry.
#   1.00 = at-the-forward, 0.95 = 5% below fwd, 1.05 = 5% above fwd.
# root is derived from the symbol prefix ('SPX' vs 'SPXW'); the forward comes
# from fwdprd joined on (secid, date, expiration=exdate, amsettlement=am_settlement).
_CURATE_SQL = """
WITH opt AS (
    SELECT *, split_part(symbol, ' ', 1) AS root
    FROM read_parquet('{opt_src}')
),
fwd AS (
    SELECT secid, CAST(date AS DATE) AS date, CAST(expiration AS DATE) AS expiration,
           amsettlement, forwardprice
    FROM read_parquet('{fwd_src}')
),
-- split adjustment: present-basis factor = cfadj / latest cfadj (per-day, from
-- the underlying's secprd). =1 for indices/ETFs (constant cfadj); scales pre-split
-- strikes/prices down for a stock that later split (e.g. a 4:1 split), so
-- strike/spot/premium stay continuous across the split.
cadj AS (
    SELECT CAST(date AS DATE) AS date,
           cfadj / max(cfadj) OVER () AS adj
    FROM read_parquet('{ref_src}') WHERE secid = {secid}
)
SELECT
    o.secid,
    CAST(o.date AS DATE)                          AS date,
    o.root,
    o.am_settlement,
    o.symbol,
    CAST(o.exdate AS DATE)                         AS exdate,
    CAST(o.last_date AS DATE)                      AS last_date,
    o.cp_flag,
    o.strike_price / {scale} * COALESCE(c.adj, 1.0)  AS strike,
    f.forwardprice * COALESCE(c.adj, 1.0)         AS forward_price,
    datediff('day', CAST(o.date AS DATE), CAST(o.exdate AS DATE)) AS dte,
    CASE WHEN f.forwardprice > 0
         THEN (o.strike_price / {scale}) / f.forwardprice END   AS fwd_moneyness,
    o.best_bid * COALESCE(c.adj, 1.0)             AS best_bid,
    o.best_offer * COALESCE(c.adj, 1.0)           AS best_offer,
    (o.best_bid + o.best_offer) / 2.0 * COALESCE(c.adj, 1.0) AS mid,
    (o.best_offer - o.best_bid) * COALESCE(c.adj, 1.0)       AS spread,
    o.volume,
    o.open_interest,
    o.impl_volatility,
    o.delta, o.gamma, o.vega, o.theta,
    o.optionid,
    o.cfadj,
    COALESCE(c.adj, 1.0)                           AS split_adj,
    o.ss_flag,
    o.expiry_indicator,
    -- quality flags (raw values -> flags unaffected by the price scaling)
    (o.best_bid = 0)                               AS bid_zero,
    (o.best_bid > o.best_offer)                    AS crossed,
    (o.volume = 0 OR o.volume IS NULL)             AS zero_volume,
    (o.open_interest = 0 OR o.open_interest IS NULL) AS no_oi,
    (o.impl_volatility IS NULL)                    AS no_iv,
    (f.forwardprice IS NULL)                       AS no_forward,
    CAST(year(CAST(o.date AS DATE)) AS INTEGER)    AS year
FROM opt o
LEFT JOIN fwd f
  ON o.secid = f.secid
 AND CAST(o.date AS DATE) = f.date
 AND CAST(o.exdate AS DATE) = f.expiration
 AND o.am_settlement = f.amsettlement
LEFT JOIN cadj c ON CAST(o.date AS DATE) = c.date
"""


def build_year(year: int, *, underlying: str = "SPX",
               con: duckdb.DuckDBPyConnection | None = None) -> int:
    """Build the curated parquet for one year from its landing parquet."""
    src = C.raw_path(year, underlying)
    fwd_src = C.fwd_path(year, underlying)
    ref_src = C.underlying_path(underlying)
    if not src.exists():
        print(f"[skip] no landing parquet for {year}: {src}")
        return 0
    if not fwd_src.exists():
        print(f"[skip] no fwdprd parquet for {year}: {fwd_src}")
        return 0
    dst = C.curated_path(year, underlying)
    dst.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb.connect()
    try:
        sql = _CURATE_SQL.format(
            scale=C.STRIKE_SCALE,
            opt_src=str(src).replace("\\", "/"),
            fwd_src=str(fwd_src).replace("\\", "/"),
            ref_src=str(ref_src).replace("\\", "/"),
            secid=C.get_underlying(underlying).secid,
        )
        con.execute(
            f"COPY ({sql}) TO '{str(dst).replace(chr(92), '/')}' "
            f"(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        n = con.execute(
            f"SELECT count(*) FROM read_parquet('{str(dst).replace(chr(92), '/')}')"
        ).fetchone()[0]
    finally:
        if own:
            con.close()
    print(f"[build] {year}: {n:,} rows -> {dst}")
    return n


def build_range(start_year: int, end_year: int, *, underlying: str = "SPX") -> None:
    con = duckdb.connect()
    try:
        for year in range(start_year, end_year + 1):
            build_year(year, underlying=underlying, con=con)
    finally:
        con.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build curated option parquet.")
    p.add_argument("--underlying", type=str, default="SPX",
                   choices=list(C.UNDERLYINGS))
    p.add_argument("--start", type=int, default=C.DEFAULT_START_YEAR)
    p.add_argument("--end", type=int, default=C.LAST_YEAR)
    p.add_argument("--year", type=int, help="single year (overrides --start/--end)")
    return p.parse_args()


def main() -> None:
    a = _parse_args()
    if a.year:
        build_year(a.year, underlying=a.underlying)
    else:
        build_range(a.start, a.end, underlying=a.underlying)


if __name__ == "__main__":
    main()
