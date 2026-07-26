"""Central configuration for the option-price fetcher.

Single source of truth for identifiers, paths, and the WRDS extract shape.
Everything else imports from here so there are no magic constants scattered
around the codebase.

Underlyings are parameterized via a small registry (SPX, SPY). All paths and
downloads take an `underlying` key (default 'SPX'), so the same pipeline serves
both without code changes — only the secid, roots, and multiplier differ.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- WRDS connection -------------------------------------------------------
def _wrds_user_from_pgpass() -> str:
    """Read the WRDS username from ~/.pgpass (host:port:db:user:pass) so it
    never has to be hardcoded in the repo. Returns '' if not found."""
    try:
        with open(os.path.expanduser("~/.pgpass")) as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) >= 5 and "wrds" in parts[0].lower():
                    return parts[3]
    except OSError:
        pass
    return ""


# Prefer $WRDS_USERNAME; otherwise derive from ~/.pgpass. No username in the repo.
WRDS_USERNAME = os.environ.get("WRDS_USERNAME") or _wrds_user_from_pgpass()

# Price tables are split by year: optionm.opprcd<YYYY>, optionm.fwdprd<YYYY>.
OPTIONM_LIBRARY = "optionm"

# Years available in the WRDS optionm price tables.
FIRST_YEAR = 1996
LAST_YEAR = 2025

# Default download horizon (the "10yr+" decision). Override per-call as needed.
DEFAULT_START_YEAR = 2014

# Cap days-to-expiry at the landing pull to skip long-dated LEAPS (smaller/faster
# downloads, so ~20y of history stays manageable). Applied as
# (exdate - date) <= MAX_DTE in the WRDS query. Set to None to keep every expiry.
MAX_DTE = 180  # 6 months; keeps all weeklies/monthlies, drops LEAPS

# CBOE volatility indices (secprd secids). Each underlying maps to its matching
# gauge: VIX for S&P (SPX/SPY), VXN for the Nasdaq-100 (QQQ).
SECID_VIX = 117801  # CBOE Volatility Index (S&P 500)
SECID_VXN = 112569  # CBOE NASDAQ-100 Volatility Index
SECID_RVX = 126879  # CBOE Russell 2000 Volatility Index


# --- Underlying registry ---------------------------------------------------
@dataclass(frozen=True)
class Underlying:
    """An option underlying and its market/contract conventions."""
    key: str                 # 'SPX', 'SPY'
    secid: int               # OptionMetrics secid of the underlying
    roots: tuple[str, ...]   # option roots to expect (derived from symbol prefix)
    weekly_root: str         # root used by the weekly strategy (PM-settled)
    multiplier: int          # contract multiplier
    style: str               # 'european' | 'american'
    settlement: str          # human note
    has_forward: bool        # whether optionm.fwdprd covers this secid
    vol_secid: int = SECID_VIX   # matching CBOE vol index (secprd secid)
    vol_ticker: str = "VIX"      # its display ticker
    extra_secids: tuple[int, ...] = field(default_factory=tuple)  # bundled ref levels


UNDERLYINGS: dict[str, Underlying] = {
    "SPX": Underlying(
        key="SPX", secid=108105, roots=("SPX", "SPXW"), weekly_root="SPXW",
        multiplier=100, style="european",
        settlement="cash; SPXW=PM (close), SPX=AM (SET)",
        has_forward=True, vol_secid=SECID_VIX, vol_ticker="VIX",
        extra_secids=(SECID_VIX,),
    ),
    "SPY": Underlying(
        key="SPY", secid=109820, roots=("SPY",), weekly_root="SPY",
        multiplier=100, style="american",
        settlement="physical; PM close; early exercise possible (not modeled)",
        has_forward=True, vol_secid=SECID_VIX, vol_ticker="VIX",
        extra_secids=(SECID_VIX,),
    ),
    "QQQ": Underlying(
        key="QQQ", secid=107899, roots=("QQQ",), weekly_root="QQQ",
        multiplier=100, style="american",
        settlement="physical; PM close; early exercise possible (not modeled)",
        has_forward=True, vol_secid=SECID_VXN, vol_ticker="VXN",
        extra_secids=(SECID_VXN,),
    ),
    "IWM": Underlying(
        key="IWM", secid=106445, roots=("IWM",), weekly_root="IWM",
        multiplier=100, style="american",
        settlement="physical; PM close; early exercise possible (not modeled)",
        has_forward=True, vol_secid=SECID_RVX, vol_ticker="RVX",
        extra_secids=(SECID_RVX,),
    ),
}


def get_underlying(underlying: str | Underlying = "SPX") -> Underlying:
    if isinstance(underlying, Underlying):
        return underlying
    try:
        return UNDERLYINGS[underlying.upper()]
    except KeyError:
        raise ValueError(f"unknown underlying {underlying!r}; "
                         f"known: {list(UNDERLYINGS)}") from None


# Back-compat convenience constant.
SECID_SPX = UNDERLYINGS["SPX"].secid

# --- Storage layout --------------------------------------------------------
# data/
#   raw/<ul>/year=YYYY/opprcd.parquet      immutable landing layer (verbatim OM)
#   raw/<ul>/year=YYYY/fwdprd.parquet      forward prices for the join
#   curated/<ul>/year=YYYY/options.parquet enriched: dte, moneyness, flags
#   ref/<ul>_underlying.parquet            underlying daily OHLC (secprd)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_ROOT = DATA_DIR / "raw"
CURATED_ROOT = DATA_DIR / "curated"
REF_DIR = DATA_DIR / "ref"


def raw_dir(underlying: str = "SPX") -> Path:
    return RAW_ROOT / get_underlying(underlying).key.lower()


def curated_dir(underlying: str = "SPX") -> Path:
    return CURATED_ROOT / get_underlying(underlying).key.lower()


def raw_path(year: int, underlying: str = "SPX") -> Path:
    return raw_dir(underlying) / f"year={year}" / "opprcd.parquet"


def fwd_path(year: int, underlying: str = "SPX") -> Path:
    return raw_dir(underlying) / f"year={year}" / "fwdprd.parquet"


def curated_path(year: int, underlying: str = "SPX") -> Path:
    return curated_dir(underlying) / f"year={year}" / "options.parquet"


def underlying_path(underlying: str = "SPX") -> Path:
    return REF_DIR / f"{get_underlying(underlying).key.lower()}_underlying.parquet"


def ensure_dirs(underlying: str = "SPX") -> None:
    for d in (raw_dir(underlying), curated_dir(underlying), REF_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --- Extract shape ---------------------------------------------------------
# Columns pulled verbatim from optionm.opprcd<YYYY>.
# NOTE: the schema also has `root` and `forward_price` columns, but for the SPX
# index secid both are ALWAYS NULL (verified). So we derive `root` from the
# `symbol` prefix and take the forward from optionm.fwdprd<YYYY> via join.
# `am_settlement` IS populated (SPXW->0/PM, SPX->1/AM) and is used directly.
OPPRCD_COLUMNS = [
    "secid",
    "date",
    "am_settlement",   # populated: 1 = AM-settled (SPX), 0 = PM-settled (SPXW)
    "symbol",          # e.g. 'SPXW 240603C1400000' -> root from prefix
    "exdate",
    "last_date",
    "cp_flag",         # 'C' / 'P'
    "strike_price",    # stored x1000
    "best_bid",
    "best_offer",
    "volume",
    "open_interest",
    "impl_volatility",  # OM-computed; NULL where OM did not compute (free ride)
    "delta",
    "gamma",
    "vega",
    "theta",
    "optionid",
    "cfadj",
    "ss_flag",
    "expiry_indicator",
]

# Forward-price table: forward to each expiration, keyed by settlement type.
# Joined to opprcd on (secid, date, expiration=exdate, amsettlement=am_settlement).
FWDPRD_COLUMNS = ["secid", "date", "expiration", "amsettlement", "forwardprice"]

STRIKE_SCALE = 1000.0  # strike_price is stored x1000
