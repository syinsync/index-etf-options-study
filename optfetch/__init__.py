"""optfetch: WRDS/OptionMetrics SPX option-price fetcher.

Three layers:
  1. landing  (download.py)      immutable parquet, verbatim OptionMetrics rows
  2. curated  (build_curated.py) enriched with dte, forward-moneyness, flags
  3. query    (query.py)         get_options(...) over DuckDB
"""
from .query import get_options  # noqa: F401

__all__ = ["get_options"]
