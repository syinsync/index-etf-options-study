"""Simple weekly short-put backtest over the curated SPX option set.

Strategy (baseline):
  - Every Friday, sell a PM-settled SPXW put expiring the NEXT Friday (~7 DTE).
  - Strike = the listed strike nearest to  spot * (1 - otm_pct)   [5% OTM default].
  - Sell at the option MID (toggle to bid for a conservative fill).
  - Hold to expiry; SPX options are European cash-settled, so
        payoff = premium - max(K - S_expiry, 0)                 (index points)
    with S_expiry = the SPX close on the expiry Friday.
  - Cash-secured basis: weekly return = payoff / K.

Returns are reported both compounded (equity curve) and arithmetic.
The single knob `otm_pct` makes the 5% vs 10% comparison a one-liner.

This is an EOD backtest: fills use the closing quote snapshot. Commission/
slippage is an optional per-contract dollar cost; margin sizing is future work.
"""
from __future__ import annotations

import glob
from dataclasses import dataclass, asdict

import duckdb
import numpy as np
import pandas as pd

from . import config as C
from .query import get_options

WEEKS_PER_YEAR = 52.0
FRIDAY = 4  # pandas: Monday=0 ... Friday=4


def _underlying_ohlc(underlying: str = "SPX") -> pd.DataFrame:
    """Underlying daily OHLC indexed by date, from the ref underlying parquet."""
    ul = C.get_underlying(underlying)
    u = pd.read_parquet(C.underlying_path(ul.key))
    u = u[u["secid"] == ul.secid].copy()
    u["date"] = pd.to_datetime(u["date"])
    return u.set_index("date")[["open", "high", "low", "close"]].sort_index()


def _daily_option_marks(optionids: list[int], start: str, end: str,
                        underlying: str = "SPX") -> pd.DataFrame:
    """Daily closing mids for a set of optionids, from curated parquet.

    Options have no intraday OHLC — only the EOD best_bid/best_offer — so each
    held option is marked once per day at its closing mid.
    """
    files = [f.replace("\\", "/") for f in glob.glob(
        str(C.curated_dir(underlying) / "year=*" / "options.parquet"))]
    files_sql = "[" + ", ".join(f"'{f}'" for f in files) + "]"
    ids = ", ".join(str(int(o)) for o in optionids)
    q = (f"SELECT optionid, date, mid, best_bid, best_offer "
         f"FROM read_parquet({files_sql}) "
         f"WHERE optionid IN ({ids}) "
         f"AND date BETWEEN DATE '{start}' AND DATE '{end}'")
    df = duckdb.connect().execute(q).df()
    df["date"] = pd.to_datetime(df["date"])
    return df


def _daily_equity(trades: pd.DataFrame, marks: pd.DataFrame) -> pd.DataFrame:
    """Chain per-trade daily marks into one daily equity curve (vectorized).

    One position at a time: each date belongs to the single open trade under the
    half-open window [entry, exit). A roll Friday (= exit of trade i = entry of
    trade i+1) is owned by the new trade i+1, whose entry-day mark is ~flat, so
    its value equals trade i's realized equity. Thus:
        equity(date) = R_before_i * (1 + (premium_i - mid_date) / K_i)
    with R_before_i the compounded realized equity of all prior trades. The last
    trade's exit (owned by no successor) is appended as fully realized. Friday
    values reconcile to the weekly compounded curve.
    """
    tr = trades.sort_values("entry_date").reset_index(drop=True)
    tr["R_at"] = (1.0 + tr["ret"]).cumprod()
    tr["R_before"] = tr["R_at"].shift(1, fill_value=1.0)

    info = tr[["optionid", "entry_date", "exit_date", "premium", "strike",
               "R_before", "R_at"]]
    m = marks.merge(info, on="optionid", how="inner")
    m = m[(m["date"] >= m["entry_date"]) & (m["date"] < m["exit_date"])].copy()
    m["equity_daily"] = m["R_before"] * (1.0 + (m["premium"] - m["mid"]) / m["strike"])

    last = tr.iloc[-1]
    final = pd.DataFrame({"date": [last["exit_date"]], "equity_daily": [last["R_at"]]})
    eq = (pd.concat([m[["date", "equity_daily"]], final])
          .dropna(subset=["equity_daily"])
          .drop_duplicates("date", keep="last")
          .sort_values("date").reset_index(drop=True))

    peak = np.maximum.accumulate(eq["equity_daily"].to_numpy())
    eq["drawdown_daily"] = (eq["equity_daily"].to_numpy() - peak) / peak
    return eq


@dataclass
class BacktestSummary:
    n_trades: int
    total_return_compounded: float
    total_return_additive: float
    cagr: float
    mean_weekly: float
    vol_weekly: float
    vol_annualized: float
    sharpe_annualized: float
    win_rate: float
    worst_week: float
    max_drawdown_weekly: float   # from weekly (Friday) equity points
    max_drawdown_daily: float    # from daily mark-to-market (true intra-week)
    n_assigned: int              # expired ITM (settled loss)
    n_breached: int              # index traded below strike intra-week (may recover)


def run_short_put(
    start: str,
    end: str,
    *,
    underlying: str = "SPX",
    otm_pct: float = 0.05,
    dte_target: int = 7,         # maturity sold at entry (7=weekly, 14, 28, ...)
    dte_tol: int = 1,
    fill: str = "mid",          # 'mid' or 'bid' (exit mirrors: mid / ask)
    root: str | None = None,     # default: the underlying's PM weekly root
    contract_multiplier: int | None = None,  # default: the underlying's multiplier
    commission: float = 0.0,     # $ per contract, round turn
    contracts: int = 1,
) -> dict:
    """Weekly-rolled short-put backtest with a constant one-week holding period.

    Every Friday, sell a put at ~dte_target and hold it exactly one week; then
    close it at its then-current mark (mid, or ask under fill='bid') and sell a
    fresh dte_target put. Always ONE position at a time, so DTEs are directly
    comparable — the target just selects which part of the term structure is
    sold. dte_target=7 is the special case where exit == expiry, settled to
    intrinsic. Returns {trades, equity, daily, summary}.
    """
    if fill not in ("mid", "bid"):
        raise ValueError("fill must be 'mid' or 'bid'")

    ul = C.get_underlying(underlying)
    root = root or ul.weekly_root
    contract_multiplier = contract_multiplier or ul.multiplier

    ohlc = _underlying_ohlc(ul.key)
    spx = ohlc["close"]

    # Universe: puts at ~dte_target, entered on a Friday, expiring on a Friday.
    puts = get_options(
        start=start, end=end, root=root, cp="P", underlying=ul.key,
        dte_range=(dte_target - dte_tol, dte_target + dte_tol),
        exclude_flags=("bid_zero", "crossed", "no_forward"),
    )
    puts["date"] = pd.to_datetime(puts["date"])
    puts["exdate"] = pd.to_datetime(puts["exdate"])
    puts = puts[(puts["date"].dt.dayofweek == FRIDAY) &
                (puts["exdate"].dt.dayofweek == FRIDAY)]

    # One entry per Friday: the target-maturity put nearest to the OTM strike.
    entries = []
    for entry_date, day in puts.groupby("date"):
        spot = spx.get(entry_date)
        if spot is None or np.isnan(spot):
            continue
        day = day.assign(_d=(day["dte"] - dte_target).abs())
        expiry = day.sort_values("_d")["exdate"].iloc[0]   # maturity closest to target
        chain = day[day["exdate"] == expiry]
        i = (chain["strike"] - spot * (1.0 - otm_pct)).abs().idxmin()
        opt = chain.loc[i]
        entries.append({
            "entry_date": entry_date, "optionid": int(opt["optionid"]),
            "strike": float(opt["strike"]),
            "premium": float(opt["mid"] if fill == "mid" else opt["best_bid"]),
            "expiry": opt["exdate"], "dte_entry": int(opt["dte"]),
            "spot_entry": round(spot, 2),
        })
    entries.sort(key=lambda e: e["entry_date"])
    if not entries:
        raise RuntimeError("no trades generated — check date range / inputs")

    # Daily marks for every held option (to mark weekly exits + build the curve).
    max_exit = max(e["expiry"] for e in entries)
    marks = _daily_option_marks([e["optionid"] for e in entries],
                                str(entries[0]["entry_date"].date()),
                                str(pd.Timestamp(max_exit).date()), underlying=ul.key)
    exit_col = "mid" if fill == "mid" else "best_offer"   # buy back at mid / ask
    exit_map = {oid: g.set_index("date")[exit_col] for oid, g in marks.groupby("optionid")}

    # Hold each position exactly one week, then close at its mark and re-enter.
    trades = []
    n = len(entries)
    for i, e in enumerate(entries):
        entry_date, oid, K = e["entry_date"], e["optionid"], e["strike"]
        premium, expiry = e["premium"], e["expiry"]
        exit_date = (entries[i + 1]["entry_date"] if i + 1 < n
                     else entry_date + pd.Timedelta(days=7))

        if exit_date >= expiry:                     # holds to expiry -> settle to intrinsic
            exit_eff = expiry
            s_exit = spx.get(expiry)
            if s_exit is None or np.isnan(s_exit):
                continue
            exit_price = max(K - float(s_exit), 0.0)
            settled_at_expiry = True
        else:                                       # close early at the option's mark
            exit_eff = exit_date
            ser = exit_map.get(oid)
            exit_price = None if ser is None else ser.get(exit_date)
            if exit_price is None or np.isnan(exit_price):   # holiday: nearest mark in window
                if ser is not None:
                    cand = ser[(ser.index > entry_date) & (ser.index <= exit_date)]
                    if len(cand):
                        exit_eff = cand.index[-1]
                        exit_price = float(cand.iloc[-1])
            if exit_price is None or np.isnan(exit_price):
                continue
            exit_price = float(exit_price)
            s_exit = spx.get(exit_eff)
            settled_at_expiry = False

        payoff_pts = premium - exit_price
        pnl_dollars = payoff_pts * contract_multiplier * contracts - commission * contracts
        collateral = K * contract_multiplier * contracts
        ret = pnl_dollars / collateral
        window_low = float(ohlc.loc[entry_date:exit_eff, "low"].min())
        trades.append({
            "entry_date": entry_date, "exit_date": exit_eff, "expiry": expiry,
            "optionid": oid, "dte_entry": e["dte_entry"],
            "dte_exit": int((pd.Timestamp(expiry) - pd.Timestamp(exit_eff)).days),
            "spot_entry": e["spot_entry"],
            "spot_exit": None if s_exit is None or np.isnan(s_exit) else round(float(s_exit), 2),
            "strike": K, "moneyness": round(K / e["spot_entry"], 4),
            "premium": round(premium, 2), "exit_price": round(exit_price, 2),
            "payoff_pts": round(payoff_pts, 2), "pnl_dollars": round(pnl_dollars, 2),
            "collateral": collateral, "ret": ret,
            "settled_at_expiry": settled_at_expiry,
            "assigned": settled_at_expiry and exit_price > 0,
            "min_index_low": round(window_low, 2),
            "mae_pts": round(max(K - window_low, 0.0), 2), "breached": window_low < K,
        })

    tdf = pd.DataFrame(trades).sort_values("entry_date").reset_index(drop=True)
    if tdf.empty:
        raise RuntimeError("no trades generated — check date range / inputs")

    # weekly (Friday) equity curve
    tdf["equity_compounded"] = (1.0 + tdf["ret"]).cumprod()
    tdf["cum_return_additive"] = tdf["ret"].cumsum()
    eq = tdf[["entry_date", "ret", "equity_compounded", "cum_return_additive"]].copy()

    daily = _daily_equity(tdf, marks)

    r = tdf["ret"].to_numpy()
    equity_end = float(tdf["equity_compounded"].iloc[-1])
    n = len(tdf)
    peak = np.maximum.accumulate(tdf["equity_compounded"].to_numpy())
    max_dd_weekly = float(((tdf["equity_compounded"].to_numpy() - peak) / peak).min())
    max_dd_daily = float(daily["drawdown_daily"].min())
    vol = float(np.std(r, ddof=1)) if n > 1 else 0.0
    summary = BacktestSummary(
        n_trades=n,
        total_return_compounded=equity_end - 1.0,
        total_return_additive=float(r.sum()),
        cagr=equity_end ** (WEEKS_PER_YEAR / n) - 1.0,
        mean_weekly=float(r.mean()),
        vol_weekly=vol,
        vol_annualized=vol * np.sqrt(WEEKS_PER_YEAR),
        sharpe_annualized=(float(r.mean()) / vol * np.sqrt(WEEKS_PER_YEAR)) if vol else float("nan"),
        win_rate=float((r > 0).mean()),
        worst_week=float(r.min()),
        max_drawdown_weekly=max_dd_weekly,
        max_drawdown_daily=max_dd_daily,
        n_assigned=int(tdf["assigned"].sum()),
        n_breached=int(tdf["breached"].sum()),
    )
    return {"trades": tdf, "equity": eq, "daily": daily, "summary": summary,
            "summary_dict": asdict(summary),
            "params": {"underlying": ul.key, "start": start, "end": end,
                       "otm_pct": otm_pct, "root": root, "fill": fill,
                       "commission": commission}}


if __name__ == "__main__":
    res = run_short_put("2024-04-01", "2024-06-30", otm_pct=0.05)
    import json
    print(res["trades"].to_string(index=False))
    print("\nSUMMARY:", json.dumps(res["summary_dict"], indent=2, default=float))
