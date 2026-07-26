"""Query layer: retrieve filtered slices of the curated SPX option set.

The one function your analysis calls. DuckDB reads only the year-partition
files it needs and pushes the date/dte/moneyness predicates down to parquet
row groups, so "30 DTE, +/-5% OTM, past 5 years" is fast and lazy.

Example:
    from optfetch import get_options
    df = get_options(
        start="2019-01-01", end="2024-12-31",
        dte_range=(25, 35),          # ~30 DTE (calendar days)
        moneyness=(0.95, 1.05),      # +/-5% around the forward
        cp="P", root="SPXW",
        exclude_flags=("bid_zero", "crossed"),
    )
"""
from __future__ import annotations

import glob
from typing import Iterable, Sequence

import duckdb
import pandas as pd

from . import config as C

_ALL_FLAGS = ("bid_zero", "crossed", "zero_volume", "no_oi", "no_iv", "no_forward")


def _year_files(start: str | None, end: str | None,
                underlying: str = "SPX") -> list[str]:
    y0 = int(start[:4]) if start else C.FIRST_YEAR
    y1 = int(end[:4]) if end else C.LAST_YEAR
    files = []
    for year in range(y0, y1 + 1):
        p = C.curated_path(year, underlying)
        if p.exists():
            files.append(str(p).replace("\\", "/"))
    if not files:  # fall back to whatever curated files exist
        files = [f.replace("\\", "/") for f in glob.glob(
            str(C.curated_dir(underlying) / "year=*" / "options.parquet"))]
    return files


def get_options(
    start: str | None = None,
    end: str | None = None,
    *,
    dte_range: tuple[float, float] | None = None,
    moneyness: tuple[float, float] | None = None,
    cp: str | None = None,
    root: str | None = None,
    exclude_flags: Iterable[str] = (),
    columns: Sequence[str] | None = None,
    limit: int | None = None,
    underlying: str = "SPX",
) -> pd.DataFrame:
    """Retrieve curated options matching the given filters.

    Parameters
    ----------
    start, end     inclusive date bounds, 'YYYY-MM-DD'.
    dte_range      inclusive (min, max) calendar days to expiry.
    moneyness      inclusive (min, max) on strike/forward (1.0 = at-forward).
    cp             'C' or 'P'.
    root           e.g. 'SPX'/'SPXW' (SPX) or 'SPY' (SPY).
    exclude_flags  quality flags to require False, e.g. ('bid_zero', 'crossed').
    columns        subset of columns to return (default: all).
    limit          optional row cap (debugging).
    underlying     'SPX' or 'SPY'.
    """
    files = _year_files(start, end, underlying)
    if not files:
        raise FileNotFoundError(
            f"No curated parquet found under {C.curated_dir(underlying)}. "
            "Run download + build_curated first."
        )

    sel = ", ".join(columns) if columns else "*"
    where: list[str] = []
    if start:
        where.append(f"date >= DATE '{start}'")
    if end:
        where.append(f"date <= DATE '{end}'")
    if dte_range:
        where.append(f"dte BETWEEN {dte_range[0]} AND {dte_range[1]}")
    if moneyness:
        where.append(f"fwd_moneyness BETWEEN {moneyness[0]} AND {moneyness[1]}")
    if cp:
        where.append(f"cp_flag = '{cp.upper()}'")
    if root:
        where.append(f"root = '{root}'")
    for f in exclude_flags:
        if f not in _ALL_FLAGS:
            raise ValueError(f"unknown flag {f!r}; known: {_ALL_FLAGS}")
        where.append(f"NOT {f}")

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    files_sql = "[" + ", ".join(f"'{f}'" for f in files) + "]"
    sql = (
        f"SELECT {sel} FROM read_parquet({files_sql}) {clause} "
        f"ORDER BY date, exdate, cp_flag, strike"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"

    con = duckdb.connect()
    try:
        return con.execute(sql).df()
    finally:
        con.close()
