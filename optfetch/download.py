"""Landing layer: pull option chains from WRDS OptionMetrics into parquet.

The landing layer is immutable and verbatim: exactly the OptionMetrics columns
(see config.OPPRCD_COLUMNS) plus a `download_ts`. All filtering/derivation
happens later in the curated layer, so we never re-download to change a
definition.

Every query filters on the underlying's secid; the per-year opprcd tables hold
the entire US option universe (~380M rows/yr), so an unfiltered pull is a
mistake.

Usage:
    python -m optfetch.download --start 2014 --end 2025               # SPX, full
    python -m optfetch.download --underlying SPY --year 2024
    python -m optfetch.download --year 2024 --min-date 2024-06-03 --max-date 2024-06-05
"""
from __future__ import annotations

import argparse

import pandas as pd
import wrds

from . import config as C


def connect() -> "wrds.Connection":
    return wrds.Connection(wrds_username=C.WRDS_USERNAME)


def _opprcd_query(year: int, secid: int, min_date, max_date) -> str:
    cols = ", ".join(C.OPPRCD_COLUMNS)
    where = [f"secid = {secid}"]
    if min_date:
        where.append(f"date >= '{min_date}'")
    if max_date:
        where.append(f"date <= '{max_date}'")
    if C.MAX_DTE is not None:
        # (exdate - date) is integer days in Postgres; skip long-dated LEAPS.
        where.append(f"(exdate - date) <= {C.MAX_DTE}")
    return (f"select {cols} from {C.OPTIONM_LIBRARY}.opprcd{year} "
            f"where {' and '.join(where)}")


def download_year(
    db: "wrds.Connection",
    year: int,
    *,
    underlying: str = "SPX",
    min_date: str | None = None,
    max_date: str | None = None,
    out_path=None,
    force: bool = False,
) -> pd.DataFrame:
    """Pull one year of option chains and write it to the landing layer.

    Returns the DataFrame written. If the target parquet already exists and
    `force` is False (and no date bounds are given), the download is skipped.
    """
    ul = C.get_underlying(underlying)
    C.ensure_dirs(ul.key)
    path = out_path or C.raw_path(year, ul.key)
    bounded = bool(min_date or max_date)
    if path.exists() and not force and not bounded:
        print(f"[skip] {path} exists (use force=True to re-pull)")
        return pd.read_parquet(path)

    print(f"[pull] {ul.key} opprcd{year} secid={ul.secid} "
          f"{min_date or 'start'}..{max_date or 'end'}")
    df = db.raw_sql(_opprcd_query(year, ul.secid, min_date, max_date))
    df["download_ts"] = pd.Timestamp.utcnow().tz_localize(None)

    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    print(f"[write] {len(df):,} rows -> {path}")
    return df


def _fwdprd_query(year: int, secid: int, min_date, max_date) -> str:
    cols = ", ".join(C.FWDPRD_COLUMNS)
    where = [f"secid = {secid}"]
    if min_date:
        where.append(f"date >= '{min_date}'")
    if max_date:
        where.append(f"date <= '{max_date}'")
    return (f"select {cols} from {C.OPTIONM_LIBRARY}.fwdprd{year} "
            f"where {' and '.join(where)}")


def download_fwd(
    db: "wrds.Connection",
    year: int,
    *,
    underlying: str = "SPX",
    min_date: str | None = None,
    max_date: str | None = None,
    out_path=None,
    force: bool = False,
) -> pd.DataFrame:
    """Pull one year of forward prices into the landing layer."""
    ul = C.get_underlying(underlying)
    C.ensure_dirs(ul.key)
    path = out_path or C.fwd_path(year, ul.key)
    bounded = bool(min_date or max_date)
    if path.exists() and not force and not bounded:
        print(f"[skip] {path} exists")
        return pd.read_parquet(path)
    print(f"[pull] {ul.key} fwdprd{year} secid={ul.secid} "
          f"{min_date or 'start'}..{max_date or 'end'}")
    df = db.raw_sql(_fwdprd_query(year, ul.secid, min_date, max_date))
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    print(f"[write] {len(df):,} fwd rows -> {path}")
    return df


def download_underlying(
    db: "wrds.Connection", start_year: int, end_year: int, *, underlying: str = "SPX"
) -> pd.DataFrame:
    """Pull the underlying (and any bundled) daily OHLC from secprd into ref."""
    ul = C.get_underlying(underlying)
    C.ensure_dirs(ul.key)
    secids = ", ".join(str(s) for s in (ul.secid, *ul.extra_secids))
    q = (
        "select secid, date, open, high, low, close, return "
        f"from {C.OPTIONM_LIBRARY}.secprd "
        f"where secid in ({secids}) "
        f"and date >= '{start_year}-01-01' and date <= '{end_year}-12-31'"
    )
    df = db.raw_sql(q)
    path = C.underlying_path(ul.key)
    df.to_parquet(path, index=False)
    print(f"[write] {ul.key} underlying {len(df):,} rows -> {path}")
    return df


def download_range(start_year: int, end_year: int, *,
                   underlying: str = "SPX", force: bool = False) -> None:
    db = connect()
    try:
        for year in range(start_year, end_year + 1):
            download_year(db, year, underlying=underlying, force=force)
            download_fwd(db, year, underlying=underlying, force=force)
        download_underlying(db, start_year, end_year, underlying=underlying)
    finally:
        db.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download option chains from WRDS.")
    p.add_argument("--underlying", type=str, default="SPX",
                   choices=list(C.UNDERLYINGS))
    p.add_argument("--start", type=int, default=C.DEFAULT_START_YEAR)
    p.add_argument("--end", type=int, default=C.LAST_YEAR)
    p.add_argument("--year", type=int, help="single year (overrides --start/--end)")
    p.add_argument("--min-date", type=str, default=None)
    p.add_argument("--max-date", type=str, default=None)
    p.add_argument("--force", action="store_true")
    return p.parse_args()


def main() -> None:
    a = _parse_args()
    if a.year:
        db = connect()
        try:
            download_year(db, a.year, underlying=a.underlying,
                          min_date=a.min_date, max_date=a.max_date, force=a.force)
            download_fwd(db, a.year, underlying=a.underlying,
                         min_date=a.min_date, max_date=a.max_date, force=a.force)
            download_underlying(db, a.year, a.year, underlying=a.underlying)
        finally:
            db.close()
    else:
        download_range(a.start, a.end, underlying=a.underlying, force=a.force)


if __name__ == "__main__":
    main()
