"""Streamlit dashboard for the weekly short-put backtest.

Run:  streamlit run app.py

Thin UI layer only — every number comes from optfetch.backtest.run_short_put,
the same engine used on the command line, so the dashboard and scripts never
diverge. Pick date range, OTM levels to compare, fill, and cost; get KPI tiles,
an interactive equity overlay, and a downloadable trade blotter.
"""
from __future__ import annotations

import glob

import duckdb
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from matplotlib.colors import LinearSegmentedColormap
import streamlit as st

# subtle, on-theme heat ramps: reward = soft green, risk = soft red, neutral = gold.
# They stop at medium tones (not near-black) so tables stay light and legible.
REWARD_CMAP = LinearSegmentedColormap.from_list("reward", ["#fcf8f0", "#bcd8bf", "#5f9e6a"])
RISK_CMAP = LinearSegmentedColormap.from_list("risk", ["#fcf8f0", "#eec3b6", "#c1524a"])
NEUTRAL_CMAP = LinearSegmentedColormap.from_list("neutral", ["#fcf8f0", "#e6cfa0", "#c19a5b"])

from optfetch import config as C
from optfetch.backtest import run_short_put
from optfetch.grid import REGIMES, OTMS, DTES
from optfetch.premium_surface import VOL_LABELS

OTM_CHOICES = [int(o * 100) for o in OTMS]   # [0,2,4,6,8,10,12]

# Bright beige palette — clear, warm series that sit well on cream.
SERIES = ["#2f86ad", "#d3703a", "#5f9e6a", "#c79a4a", "#9268a0"]  # teal, orange, green, gold, mauve
SPX_COLOR = "#9a9078"   # warm gray for the market-level panel
VIX_COLOR = "#c1524a"   # brighter red = fear gauge
INK = "#33302a"
MUTED = "#8a8172"
LINE = "#ece2d0"        # soft warm hairline
CREAM = "#fffdf8"

# underlyings offered in the dashboard (SPX options dropped — SPY/QQQ only)
DASH_UNDERLYINGS = [k for k in C.UNDERLYINGS if k != "SPX"]

st.set_page_config(page_title="Weekly Short-Put Backtest", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Inter:wght@400;500;600&display=swap');
:root { --ink:#33302a; --muted:#8a8172; --accent:#c19a5b; --line:#ece2d0; --cream:#fffdf8; }
html, body, [class*="css"], .stApp, .stMarkdown, p, div, span, label, input, select, textarea {
    font-family: 'Inter', system-ui, sans-serif; color: var(--ink);
}
h1, h2, h3, h4 { font-family: 'Fraunces', Georgia, serif !important; font-weight: 600; letter-spacing:.2px; }
.stApp { background-color: #fdf9f0; }
section[data-testid="stSidebar"] { background-color: #faf4e8; border-right: 1px solid var(--line); }
h1 { border-bottom: 2px solid var(--accent); padding-bottom:.25rem; }
[data-testid="stMetric"] {
    background: var(--cream); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px;
}
[data-testid="stMetricValue"] { font-family:'Fraunces', Georgia, serif; }
[data-testid="stMetricLabel"] p { color: var(--muted); text-transform: uppercase; letter-spacing:.6px; font-size:.72rem; }
[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:10px; }
.stDownloadButton button, .stButton button { background: var(--accent); color:#fff; border:none; border-radius:8px; }
.stDownloadButton button:hover, .stButton button:hover { background:#84683f; color:#fff; }
[data-testid="stCaptionContainer"] { color: var(--muted); font-style: italic; }
/* folder-style tabs — obviously clickable */
.stTabs [data-baseweb="tab-list"] { gap: 6px; border-bottom: 2px solid var(--accent); }
.stTabs [data-baseweb="tab"] {
    background: #f1e7d2; border: 1px solid var(--line); border-bottom: none;
    border-radius: 12px 12px 0 0; padding: 9px 20px; margin-bottom: -2px;
    font-family: 'Fraunces', Georgia, serif; font-size: 1rem; color: var(--muted);
}
.stTabs [data-baseweb="tab"]:hover { background: #ede0c6; color: var(--ink); }
.stTabs [aria-selected="true"] {
    background: var(--cream) !important; color: var(--accent) !important;
    border: 2px solid var(--accent); border-bottom: 2px solid var(--cream);
    font-weight: 600;
}
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def available_range(underlying: str) -> tuple[str, str]:
    files = glob.glob(str(C.curated_dir(underlying) / "year=*" / "options.parquet"))
    if not files:
        return ("", "")
    files_sql = "[" + ", ".join(f"'{f.replace(chr(92),'/')}'" for f in files) + "]"
    df = duckdb.connect().execute(
        f"SELECT min(date) lo, max(date) hi FROM read_parquet({files_sql})"
    ).df()
    return (str(df["lo"].iloc[0])[:10], str(df["hi"].iloc[0])[:10])


@st.cache_data(show_spinner=False)
def market_context(underlying: str, lo: str, hi: str) -> pd.DataFrame:
    """The selected underlying's level + its matching vol index over [lo, hi].

    Both come from the underlying's own ref parquet (level + bundled vol index:
    VIX for SPX/SPY, VXN for QQQ). Implausible vol prints are clipped to NaN.
    Columns: date, <ul.key>, <ul.vol_ticker>.
    """
    ul = C.get_underlying(underlying)
    lvl_path = C.underlying_path(underlying)
    if not lvl_path.exists():
        return pd.DataFrame()
    u = pd.read_parquet(lvl_path)
    u["date"] = pd.to_datetime(u["date"])
    lvl = u[u["secid"] == ul.secid][["date", "close"]].rename(columns={"close": ul.key})
    vol = u[u["secid"] == ul.vol_secid][["date", "close"]].rename(columns={"close": ul.vol_ticker})
    vol[ul.vol_ticker] = vol[ul.vol_ticker].where(vol[ul.vol_ticker].between(1, 200))

    df = lvl.merge(vol, on="date", how="left")
    df = df[(df["date"] >= pd.to_datetime(lo)) & (df["date"] <= pd.to_datetime(hi))]
    return df.sort_values("date")


@st.cache_data(show_spinner=False)
def buy_hold_benchmark(underlying: str, start: str, end: str) -> dict:
    """Buy-and-hold stats for the underlying over [start, end], as a comparison
    baseline: total/CAGR, weekly win rate & worst week, annualized vol, max DD."""
    ul = C.get_underlying(underlying)
    u = pd.read_parquet(C.underlying_path(underlying))
    u = u[u["secid"] == ul.secid].copy()
    u["date"] = pd.to_datetime(u["date"])
    u = u[(u["date"] >= pd.to_datetime(start)) & (u["date"] <= pd.to_datetime(end))]
    u = u.sort_values("date")
    if len(u) < 2:
        return {}
    close = u.set_index("date")["close"]
    yrs = max((close.index[-1] - close.index[0]).days / 365.25, 1e-9)
    total = close.iloc[-1] / close.iloc[0] - 1.0
    wret = close.resample("W-FRI").last().dropna().pct_change().dropna()
    volw = float(wret.std())
    peak = close.cummax()
    return {
        "total_return_compounded": float(total),
        "cagr": float((1 + total) ** (1 / yrs) - 1),
        "win_rate": float((wret > 0).mean()),
        "worst_week": float(wret.min()),
        "vol_annualized": volw * (52 ** 0.5),
        # raw Sharpe (mean/vol annualized, no risk-free) — same formula as strategy
        "sharpe_annualized": float(wret.mean() / volw * (52 ** 0.5)) if volw else float("nan"),
        "max_drawdown_daily": float(((close - peak) / peak).min()),
    }


@st.cache_data(show_spinner=True)
def cached_run(underlying, start, end, otm, fill, commission, dte_target):
    return run_short_put(start, end, underlying=underlying, otm_pct=otm, fill=fill,
                         commission=commission, dte_target=dte_target)


@st.cache_data(show_spinner=False)
def load_grid() -> pd.DataFrame:
    p = C.PROJECT_ROOT / "results" / "grid.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


@st.cache_data(show_spinner=False)
def load_surface() -> pd.DataFrame:
    p = C.PROJECT_ROOT / "results" / "premium_surface.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


# summary-row formatting shared by the Explorer and Cross-underlying tables
def _fmt_row(d: dict, is_bench: bool) -> dict:
    def signed(k):
        v = d.get(k)
        return "—" if v is None or pd.isna(v) else f"{v * 100:+.2f}%"

    def plain(k, dp=2):
        v = d.get(k)
        return "—" if v is None or pd.isna(v) else f"{v * 100:.{dp}f}%"

    sh = d.get("sharpe_annualized")
    return {
        "Total return": signed("total_return_compounded"),
        "CAGR": signed("cagr"),
        "Ann. vol": plain("vol_annualized"),
        "Sharpe": "—" if sh is None or pd.isna(sh) else f"{sh:.2f}",
        "Win rate": plain("win_rate", 1),
        "Worst week": signed("worst_week"),
        "Max DD": signed("max_drawdown_daily"),
        # strings (not ints) so mixed benchmark "—" rows serialize cleanly to Arrow
        "Trades": "—" if is_bench else str(int(d["n_trades"])),
        "Assigned": "—" if is_bench else str(int(d["n_assigned"])),
        "Breached": "—" if is_bench else str(int(d["n_breached"])),
    }


def _hl_bench(row):
    hit = str(row.name).strip().startswith("Buy & Hold")
    return [f"background-color:{'#f6ecd4' if hit else 'transparent'}; "
            f"font-style:{'italic' if hit else 'normal'}"] * len(row)


# metric registry for the grid heatmaps.
#   palette: "reward" (green, more=better) or "risk" (red, more=riskier)
#   gmap:    "value" colors by the number; "neg" colors by its magnitude of loss
GRID_METRICS = {
    "Sharpe":       dict(col="sharpe_annualized",       pct=False, fmt="{:.2f}",  palette="reward", gmap="value"),
    "CAGR":         dict(col="cagr",                    pct=True,  fmt="{:+.1f}%", palette="reward", gmap="value"),
    "Total return": dict(col="total_return_compounded", pct=True,  fmt="{:+.0f}%", palette="reward", gmap="value"),
    "Win rate":     dict(col="win_rate",                pct=True,  fmt="{:.0f}%",  palette="reward", gmap="value"),
    "Ann. vol":     dict(col="vol_annualized",          pct=True,  fmt="{:.1f}%",  palette="risk",   gmap="value"),
    "Max DD":       dict(col="max_drawdown_daily",      pct=True,  fmt="{:+.1f}%", palette="risk",   gmap="neg"),
}


def render_explorer():
    with st.sidebar:
        st.header("Explorer settings")
        underlying = st.selectbox("Underlying", DASH_UNDERLYINGS, index=0)
        lo, hi = available_range(underlying)
        start = st.text_input("Start date", lo)
        end = st.text_input("End date", hi)
        otm_levels = st.multiselect("OTM levels to compare (%)",
                                    OTM_CHOICES, default=[2, 8])
        fill = st.radio("Fill price", ["mid", "bid"], horizontal=True)
        commission = st.number_input("Commission ($/contract)", 0.0, 50.0, 0.0, 0.5)
        dte_target = st.selectbox("DTE at entry", DTES, index=0)
        st.caption("Cash-secured: weekly return = payoff / strike. Constant one-week "
                   "hold; DTE selects the maturity sold.")

    if not lo:
        st.error(f"No curated {underlying} data. Run download + build_curated first.")
        return
    if not otm_levels:
        st.warning("Pick at least one OTM level in the sidebar.")
        return

    ul = C.get_underlying(underlying)
    st.caption(f"{underlying} curated {lo} → {hi} · {ul.weekly_root} weekly · "
               f"cash-secured · 1-week hold at {int(dte_target)} DTE · {ul.settlement}")

    runs = {}
    for pct in sorted(otm_levels):
        try:
            runs[f"{pct}% OTM"] = cached_run(underlying, start, end, pct / 100.0,
                                             fill, commission, int(dte_target))
        except Exception as e:  # noqa: BLE001
            st.error(f"{pct}% OTM failed: {e}")
    if not runs:
        return

    # --- return chart with aligned level / vol context panels ---
    curve = st.radio("Equity curve", ["daily (mark-to-market)", "weekly (compounded)"],
                     horizontal=True)
    is_daily = curve.startswith("daily")
    vt = ul.vol_ticker
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04,
        row_heights=[0.54, 0.23, 0.23],
        subplot_titles=("Cumulative return (%)", underlying, vt),
        specs=[[{}], [{"secondary_y": True}], [{}]],
    )
    for i, (label, res) in enumerate(runs.items()):
        if is_daily:
            eq = res["daily"]; x, y = eq["date"], (eq["equity_daily"] - 1.0) * 100.0
        else:
            eq = res["equity"]; x, y = eq["entry_date"], (eq["equity_compounded"] - 1.0) * 100.0
        fig.add_trace(go.Scatter(x=x, y=y, name=label, mode="lines",
                      line=dict(color=SERIES[i % len(SERIES)], width=2),
                      hovertemplate="%{y:.2f}%<extra>" + label + "</extra>"), row=1, col=1)

    ctx = market_context(underlying, start, end)
    if not ctx.empty and underlying in ctx.columns:
        fig.add_trace(go.Scatter(x=ctx["date"], y=ctx[underlying], name=underlying,
                      mode="lines", line=dict(color=SPX_COLOR, width=1.5), showlegend=False,
                      hovertemplate=f"{underlying} " + "%{y:.2f}<extra></extra>"),
                      row=2, col=1, secondary_y=False)
        lvl0 = ctx[underlying].dropna().iloc[0]
        fig.add_trace(go.Scatter(x=ctx["date"], y=(ctx[underlying] / lvl0 - 1.0) * 100.0,
                      mode="lines", line=dict(color="rgba(0,0,0,0)", width=0),
                      showlegend=False, hoverinfo="skip"), row=2, col=1, secondary_y=True)
        fig.add_trace(go.Scatter(x=ctx["date"], y=ctx[vt], name=vt, mode="lines",
                      line=dict(color=VIX_COLOR, width=1.5), showlegend=False,
                      hovertemplate=f"{vt} " + "%{y:.2f}<extra></extra>"), row=3, col=1)

    fig.update_layout(template="simple_white", height=720, hovermode="x unified",
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Inter, system-ui, sans-serif", color=INK, size=12),
                      legend=dict(orientation="h", y=1.06, font=dict(family="Inter")),
                      margin=dict(t=50, l=60, r=20, b=30),
                      hoverlabel=dict(bgcolor=CREAM, bordercolor=LINE,
                                      font=dict(family="Inter", color=INK)))
    fig.update_xaxes(gridcolor=LINE, linecolor="#d8ccb4", zeroline=False)
    fig.update_yaxes(gridcolor=LINE, linecolor="#d8ccb4", zeroline=False)
    fig.update_yaxes(title_text="%", hoverformat=".2f", row=1, col=1)
    fig.update_yaxes(hoverformat=".2f", row=2, col=1, secondary_y=False)
    fig.update_yaxes(ticksuffix="%", showgrid=False, title_text="vs start",
                     row=2, col=1, secondary_y=True)
    fig.update_yaxes(hoverformat=".1f", row=3, col=1)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"{underlying} & {vt} panels share the return chart's timeline — line up "
               "strategy drawdowns with the underlying's selloffs and vol spikes.")

    # --- summary comparison (buy & hold on top) ---
    st.subheader("Summary comparison")
    rows = {}
    bench = buy_hold_benchmark(underlying, start, end)
    if bench:
        rows[f"Buy & Hold {underlying}"] = _fmt_row(bench, True)
    for lab, res in runs.items():
        rows[lab] = _fmt_row(res["summary_dict"], False)
    table = pd.DataFrame(rows).T
    st.dataframe(table.style.apply(_hl_bench, axis=1), use_container_width=True)

    # --- blotter ---
    st.subheader("Trade blotter")
    pick = st.selectbox("Show trades for", list(runs.keys()))
    blotter = runs[pick]["trades"]
    st.dataframe(blotter, use_container_width=True, height=340)
    st.download_button("Download blotter CSV", blotter.to_csv(index=False).encode(),
                       file_name=f"{pick.replace('% ','pct_').replace('%','')}_trades.csv",
                       mime="text/csv")


def render_grid_tab(grid: pd.DataFrame):
    st.subheader("Regime × term-structure grid")
    c1, c2 = st.columns([1, 1])
    ul = c1.selectbox("Underlying", sorted(grid["underlying"].unique()), key="grid_ul")
    metric = c2.selectbox("Metric", list(GRID_METRICS), key="grid_metric")
    m = GRID_METRICS[metric]
    cmap = REWARD_CMAP if m["palette"] == "reward" else RISK_CMAP
    tone = "green = better" if m["palette"] == "reward" else "red = riskier"
    st.caption(f"{metric} by OTM (rows) × DTE at entry (cols), one table per regime "
               f"({tone}). Each cell is a 1-week-hold weekly roll.")

    sub = grid[grid["underlying"] == ul]
    for regime in list(REGIMES):
        d = sub[sub["regime"] == regime]
        if d.empty:
            continue
        piv = d.pivot(index="otm_pct", columns="dte", values=m["col"]).sort_index()
        if m["pct"]:
            piv = piv * 100.0
        gmap = -piv if m["gmap"] == "neg" else piv
        piv.index = [f"{o*100:.0f}% OTM" for o in piv.index]
        piv.columns = [f"{c}d" for c in piv.columns]
        gmap.index, gmap.columns = piv.index, piv.columns
        st.markdown(f"**{regime}**")
        st.dataframe(
            piv.style.background_gradient(cmap=cmap, gmap=gmap, axis=None).format(m["fmt"]),
            use_container_width=True)


def render_cross_tab(grid: pd.DataFrame):
    st.subheader("Cross-underlying comparison")
    c1, c2, c3 = st.columns(3)
    regime = c1.selectbox("Regime", list(REGIMES), index=len(REGIMES) - 1, key="x_regime")
    otm = c2.selectbox("OTM %", [int(o * 100) for o in OTMS], index=1, key="x_otm")
    dte = c3.selectbox("DTE at entry", DTES, index=1, key="x_dte")
    s, e = REGIMES[regime]
    st.caption(f"{otm}% OTM · {dte} DTE · {regime} — strategy vs buy & hold, per underlying.")

    rows = {}
    for u in DASH_UNDERLYINGS:
        g = grid[(grid["underlying"] == u) & (grid["regime"] == regime)
                 & (grid["otm_pct"] == otm / 100.0) & (grid["dte"] == dte)]
        if len(g):
            rows[f"{u} put {otm}%/{dte}d"] = _fmt_row(g.iloc[0].to_dict(), False)
        bh = buy_hold_benchmark(u, s, e)
        if bh:
            rows[f"Buy & Hold {u}"] = _fmt_row(bh, True)
    if not rows:
        st.info("No grid rows for this selection.")
        return
    st.dataframe(pd.DataFrame(rows).T.style.apply(_hl_bench, axis=1),
                 use_container_width=True)
    st.caption("Buy & Hold rows (shaded) are each underlying's own return over the same "
               "window — the risk-adjusted baseline (compare the Sharpe column).")


def render_premium_tab(surface: pd.DataFrame):
    st.subheader("Premium reference — what a put historically fetched")
    c1, c2, c3 = st.columns(3)
    ul = c1.selectbox("Underlying", sorted(surface["underlying"].unique()), key="prem_ul")
    vt = C.get_underlying(ul).vol_ticker
    buckets = [b for b in VOL_LABELS if b in set(surface["vol_bucket"])]
    vb = c2.selectbox(f"{vt} bucket", buckets, index=min(1, len(buckets) - 1), key="prem_vb")
    price = c3.number_input(f"Today's {ul} price ($, optional)", 0.0, 100000.0, 0.0, 1.0)

    sub = surface[(surface["underlying"] == ul) & (surface["vol_bucket"] == vb)]
    if sub.empty:
        st.info("No data for this selection.")
        return
    pct = sub.pivot(index="dte", columns="otm", values="median_prem_pct").sort_index()
    pct.index = [f"{d}d" for d in pct.index]
    pct.columns = [f"{c}% OTM" for c in pct.columns]

    st.markdown(f"**Median premium — % of forward** · {ul} · {vt} {vb}")
    st.dataframe(pct.style.background_gradient(cmap=NEUTRAL_CMAP, axis=None).format("{:.2f}%"),
                 use_container_width=True)

    if price > 0:
        usd = pct * price / 100.0
        st.markdown(f"**≈ Dollar premium per share** at {ul} = ${price:,.2f} "
                    "(× 100 for one contract)")
        st.dataframe(usd.style.background_gradient(cmap=NEUTRAL_CMAP, axis=None).format("${:.2f}"),
                     use_container_width=True)

    cnt = sub.pivot(index="dte", columns="otm", values="n").sort_index()
    with st.expander("Sample sizes (number of historical quotes per cell)"):
        cnt.index = [f"{d}d" for d in cnt.index]
        cnt.columns = [f"{c}%" for c in cnt.columns]
        st.dataframe(cnt.astype(int), use_container_width=True)
    st.caption("Historical medians 2011–2025, premium as % of the forward price "
               "(time-stable). Dollar estimate ≈ median% × today's price; multiply by "
               "100 for one contract. A rough pricing reference, not a live quote.")


st.title("Weekly Short-Put Backtest")
tab_explore, tab_grid, tab_cross, tab_prem = st.tabs(
    ["📈 Explorer", "🔲 Regime × Grid", "🆚 Cross-underlying", "💵 Premium reference"])

with tab_explore:
    render_explorer()

grid = load_grid()
for tab, render in ((tab_grid, render_grid_tab), (tab_cross, render_cross_tab)):
    with tab:
        if grid.empty:
            st.info("Grid not computed yet — run `python -m optfetch.grid`.")
        else:
            render(grid)

with tab_prem:
    surface = load_surface()
    if surface.empty:
        st.info("Premium surface not computed yet — run "
                "`python -m optfetch.premium_surface`.")
    else:
        render_premium_tab(surface)
