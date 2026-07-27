# optionprice_fetcher

SPX option-price pipeline over **WRDS / OptionMetrics IvyDB US**, for research on
SPX & VIX and SPX index options. Pulls daily option chains once, stores them as
partitioned parquet, and serves filtered slices (e.g. *30 DTE, ±5% OTM, past 5
years*) through a single `get_options(...)` call backed by DuckDB. Includes a
weekly short-put backtest (SPY/QQQ/IWM, 2011–2025) and two Streamlit dashboards.

## ⚠️ Code is open; data is NOT included

**This repository contains source code and *aggregated result statistics* only.**
The underlying option/index data comes from **WRDS / OptionMetrics (IvyDB US)**,
which is **licensed** and is **not** distributed here. To run the data pipeline
(`optfetch.download`, the full local `app.py`) you need **your own WRDS /
OptionMetrics subscription and access** (a `~/.pgpass` entry for WRDS). The
`data/` directory is git-ignored and never committed; per-record option data must
not be redistributed. Only the derived, aggregated results
(`results/grid.parquet`, `results/benchmark.parquet`) are shared — these back the
public results-only dashboard (`share_app.py`). Code is licensed **MIT**
(see [LICENSE](LICENSE)); the data license is separate and governed by your
OptionMetrics agreement.

## Design decisions

| Area | Decision |
|---|---|
| Source | OptionMetrics IvyDB US via WRDS (`optionm.opprcd<YYYY>`, `fwdprd<YYYY>`, `secprd`). SPX `secid = 108105`, VIX `102492`, SPY `109820`, QQQ `107899`. Coverage currently runs through **2025-08-29**. |
| Underlyings | Parameterized registry (`SPX`, `SPY`, `QQQ`) in `config.py`. Every layer takes `underlying=` (default `SPX`); adding one is a single registry entry that flows through download → curated → query → backtest → dashboard. Data lands under `data/**/<ul>/`. SPX = European cash-settled; SPY/QQQ = American, physical, early exercise possible (not modeled). |
| Scope | SPX (AM monthly) **and** SPXW (PM weekly/EOM). VIX *index level* stored; VIX *options* out of scope for now. |
| IV / Greeks | Raw quotes only. OM's `impl_volatility`/Greeks are stored verbatim (free ride) but nothing depends on them yet. |
| Moneyness | Forward-based: `fwd_moneyness = strike / forward_to_expiry` (1.00 = at-forward). ±5% → `[0.95, 1.05]`. |
| DTE | **Calendar** days, `exdate - date`. Landing pull is capped at `MAX_DTE = 180` days (6mo; keeps all weeklies/monthlies, drops LEAPS so ~20y stays manageable; set `None` in `config.py` to keep everything). |
| Horizon | up to ~20yr (default `DEFAULT_START_YEAR = 2014`; extend as needed). |
| Storage | Partitioned parquet (landing → curated), queried with DuckDB. No DB server. |

### Schema gotchas (verified against WRDS, not assumed)
- `strike_price` is stored **×1000** (divided out to `strike` in the curated layer).
- The `root` and `forward_price` columns in `opprcd` are **always NULL for the SPX
  index**. `root` is derived from the `symbol` prefix (`SPX` vs `SPXW`); the forward
  comes from `fwdprd`, joined on `(secid, date, expiration=exdate, amsettlement=am_settlement)`.
- `am_settlement` **is** populated: `1` = AM-settled (SPX), `0` = PM-settled (SPXW).
- `impl_volatility`/Greeks are NULL where OM did not compute them (deep / near-expiry).
- `opprcd<YYYY>` holds the entire US option universe (~380M rows/yr) — every pull
  filters `secid = 108105`.
- **No intraday OHLC for options** — `opprcd` carries only EOD `best_bid`/`best_offer`,
  so a held option is marked once per day at its closing mid. The **underlying**
  (`secprd`) *does* have `open/high/low/close`, so index intra-day range is available
  (used for the breach / max-adverse-excursion flag).

## Layout

```
optfetch/
  config.py         identifiers, paths, extract columns   (single source of truth)
  download.py       landing layer: verbatim OM parquet     (immutable, append-only)
  build_curated.py  curated layer: dte, fwd_moneyness, flags
  query.py          get_options(...) over DuckDB
data/                                                      (git-ignored, regenerable)
  raw/<ul>/year=YYYY/opprcd.parquet      <ul> = spx | spy
  raw/<ul>/year=YYYY/fwdprd.parquet
  curated/<ul>/year=YYYY/options.parquet
  ref/<ul>_underlying.parquet            underlying daily OHLC (+ VIX for spx)
```

Three layers so definitions can evolve without re-downloading: **landing** is
verbatim and immutable; **curated** is cheap to rebuild; **query** is lazy.
Quality issues are flagged (`bid_zero`, `crossed`, `zero_volume`, `no_oi`,
`no_iv`, `no_forward`) but **not dropped** — you filter at query time.

## Setup

WRDS credentials come from `~/.pgpass` (host `wrds-pgdata.wharton.upenn.edu:9737`).
```
pip install -r requirements.txt
```

## Usage

**1. Download** (once; append-only — existing years are skipped unless `--force`):
```bash
python -m optfetch.download --start 2014 --end 2025            # SPX (default)
python -m optfetch.download --underlying SPY --start 2014 --end 2025
# one year, or a small slice for testing:
python -m optfetch.download --year 2024 --min-date 2024-06-03 --max-date 2024-06-05
```

**2. Build curated:**
```bash
python -m optfetch.build_curated --start 2014 --end 2025
python -m optfetch.build_curated --underlying SPY --start 2014 --end 2025
```

**3. Query:**
```python
from optfetch import get_options

df = get_options(
    start="2019-01-01", end="2024-12-31",
    dte_range=(25, 35),                 # ~30 DTE, calendar days
    moneyness=(0.95, 1.05),             # ±5% around the forward
    cp="P", root="SPXW", underlying="SPX",   # underlying="SPY" for SPY
    exclude_flags=("bid_zero", "crossed", "no_forward"),
)
```

> **Selection vs. tracking.** The `moneyness` filter is for *selecting* / analyzing a
> cross-section. Position **tracking** (daily MTM) must follow the *same option by
> `optionid`* across the full chain — never a moneyness-filtered subset — because a
> sold 10%-OTM put can drift to 20% ITM after a move, outside any fixed band. The
> curated layer stores **every** strike (moneyness ~0.03–2.3), so tracking never
> loses the option; only queries narrow it.

## Backtest (weekly short put)

A simple strategy engine lives in `optfetch/backtest.py`:

- Every Friday, sell a PM-settled **SPXW** put expiring the next Friday (~7 DTE).
- Strike = nearest listed to `spot × (1 − otm_pct)` (5% OTM default).
- Sell at **mid** (toggle `fill="bid"`); hold to expiry; European cash settlement
  → `payoff = premium − max(K − S_expiry, 0)`, `S_expiry` = SPX close.
- **Cash-secured** basis: weekly `return = payoff / K`. `otm_pct` is one knob, so
  5% vs 10% is a one-line change. Optional per-contract `commission`.
- Reports returns **both** compounded (equity curve) and additive (arithmetic).
- **Daily mark-to-market**: each held put is marked at its daily closing mid to
  build a *daily* equity curve and honest intra-week drawdown (the weekly curve
  hides intra-week paths). Friday values reconcile to the weekly curve.
- **Breach / MAE**: the index daily `low` over the holding window flags whether
  price traded below the strike intra-week (`breached`) and how far (`mae_pts`),
  distinct from `assigned` (expired ITM).

```python
from optfetch.backtest import run_short_put
res = run_short_put("2024-04-01", "2024-06-30", underlying="SPX", otm_pct=0.05, fill="mid")
# underlying="SPY" runs the same strategy on SPY (root/multiplier auto-selected)
res["trades"]        # blotter: strike, premium, payoff, min_index_low, mae_pts, breached
res["equity"]        # weekly (Friday) compounded curve
res["daily"]         # daily mark-to-market curve + drawdown_daily
res["summary_dict"]  # CAGR, win rate, max_drawdown_weekly vs _daily, n_assigned/n_breached, ...
```

*Assumptions:* EOD fills (closing quote snapshot, no intraday/slippage),
SPXW-only (uniform PM settlement), cash-secured sizing. Margin sizing and
transaction-cost modeling are planned next.

## Viewing results

- **CSV + PNG** (`optfetch/report.py`): `save_csv(res, label)` writes the blotter,
  equity curve, and summary to `results/`; `plot_equity({label: res, ...})` renders
  an overlaid equity-curve PNG.
- **Streamlit dashboard** (`app.py`): interactive — pick date range, OTM levels to
  compare, fill, and commission; get KPI tiles, an equity overlay, a summary table,
  and a downloadable blotter. It calls the same `run_short_put` engine.
  ```bash
  streamlit run app.py
  ```

## Refresh / gotchas
- Landing is **append-only per year**: to add recent dates to the current year,
  re-pull it with `--force` (whole-year replace), then rebuild that year's curated.
- OptionMetrics back-revises history; `download_ts` records when each batch was pulled.
- Curated is disposable — delete `data/curated` and rebuild anytime definitions change.

## Not yet included (future)
- VIX options (different conventions: Wed expiry, VRO settle, VIX-future forward).
- Recomputed Greeks from a chosen rate/dividend model (`zerocd` / `idxdvd`).
- Trading-day DTE (needs an exchange calendar).
- Incremental within-year append (current refresh is whole-year replace).
