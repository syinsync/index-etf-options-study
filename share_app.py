"""Results-only dashboard — SAFE TO SHARE / DEPLOY.

Reads ONLY the small precomputed result files (results/*.parquet); it never
touches the licensed WRDS/OptionMetrics raw or curated data. Shows the regime
grid, the cross-underlying comparison, and the premium reference — the
aggregated statistics — with no live-backtest Explorer.

    streamlit run share_app.py

To deploy (e.g. Streamlit Community Cloud): commit share_app.py, optfetch/, and
results/grid.parquet + results/benchmark.parquet (+ premium_surface.parquet only
if you're comfortable sharing median premiums). Never commit data/.
"""
from __future__ import annotations

import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
import streamlit as st

from optfetch import config as C
from optfetch.grid import REGIMES, OTMS, DTES
from optfetch.premium_surface import VOL_LABELS

REWARD_CMAP = LinearSegmentedColormap.from_list("reward", ["#fcf8f0", "#bcd8bf", "#5f9e6a"])
RISK_CMAP = LinearSegmentedColormap.from_list("risk", ["#fcf8f0", "#eec3b6", "#c1524a"])
NEUTRAL_CMAP = LinearSegmentedColormap.from_list("neutral", ["#fcf8f0", "#e6cfa0", "#c19a5b"])
RESULTS = C.PROJECT_ROOT / "results"

GRID_METRICS = {
    "Sharpe":       dict(col="sharpe_annualized",       pct=False, fmt="{:.2f}",  palette="reward", gmap="value"),
    "CAGR":         dict(col="cagr",                    pct=True,  fmt="{:+.1f}%", palette="reward", gmap="value"),
    "Total return": dict(col="total_return_compounded", pct=True,  fmt="{:+.0f}%", palette="reward", gmap="value"),
    "Win rate":     dict(col="win_rate",                pct=True,  fmt="{:.0f}%",  palette="reward", gmap="value"),
    "Ann. vol":     dict(col="vol_annualized",          pct=True,  fmt="{:.1f}%",  palette="risk",   gmap="value"),
    "Max DD":       dict(col="max_drawdown_daily",      pct=True,  fmt="{:+.1f}%", palette="risk",   gmap="neg"),
}

st.set_page_config(page_title="Short-Put Study — Results", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600&family=Inter:wght@400;500;600&display=swap');
:root { --ink:#33302a; --muted:#8a8172; --accent:#c19a5b; --line:#ece2d0; --cream:#fffdf8; }
html, body, [class*="css"], .stApp, p, div, span, label { font-family:'Inter', system-ui, sans-serif; color:var(--ink); }
h1,h2,h3,h4 { font-family:'Fraunces', Georgia, serif !important; font-weight:600; }
.stApp { background-color:#fdf9f0; }
h1 { border-bottom:2px solid var(--accent); padding-bottom:.25rem; }
[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:10px; }
[data-testid="stCaptionContainer"] { color:var(--muted); font-style:italic; }
.stTabs [data-baseweb="tab-list"] { gap:6px; border-bottom:2px solid var(--accent); }
.stTabs [data-baseweb="tab"] { background:#f1e7d2; border:1px solid var(--line); border-bottom:none;
    border-radius:12px 12px 0 0; padding:9px 20px; margin-bottom:-2px;
    font-family:'Fraunces', Georgia, serif; font-size:1rem; color:var(--muted); }
.stTabs [aria-selected="true"] { background:var(--cream) !important; color:var(--accent) !important;
    border:2px solid var(--accent); border-bottom:2px solid var(--cream); font-weight:600; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load(name):
    p = RESULTS / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def _fmt_row(d, is_bench):
    def sg(k):
        v = d.get(k); return "—" if v is None or pd.isna(v) else f"{v*100:+.2f}%"
    def pl(k, dp=2):
        v = d.get(k); return "—" if v is None or pd.isna(v) else f"{v*100:.{dp}f}%"
    sh = d.get("sharpe_annualized")
    return {"Total return": sg("total_return_compounded"), "CAGR": sg("cagr"),
            "Ann. vol": pl("vol_annualized"),
            "Sharpe": "—" if sh is None or pd.isna(sh) else f"{sh:.2f}",
            "Win rate": pl("win_rate", 1), "Worst week": sg("worst_week"),
            "Max DD": sg("max_drawdown_daily"),
            "Trades": "—" if is_bench else str(int(d["n_trades"]))}


def _hl(row):
    hit = str(row.name).startswith("Buy & Hold")
    return [f"background-color:{'#f6ecd4' if hit else 'transparent'}; "
            f"font-style:{'italic' if hit else 'normal'}"] * len(row)


def grid_tab(grid):
    st.subheader("Regime × term-structure grid")
    c1, c2 = st.columns(2)
    ul = c1.selectbox("Underlying", sorted(grid["underlying"].unique()))
    metric = c2.selectbox("Metric", list(GRID_METRICS))
    m = GRID_METRICS[metric]
    cmap = REWARD_CMAP if m["palette"] == "reward" else RISK_CMAP
    tone = "green = better" if m["palette"] == "reward" else "red = riskier"
    st.caption(f"{metric} by OTM (rows) × DTE at entry (cols), per regime ({tone}). "
               "Each cell is a 1-week-hold weekly short-put roll.")
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
        st.dataframe(piv.style.background_gradient(cmap=cmap, gmap=gmap, axis=None).format(m["fmt"]),
                     use_container_width=True)


def cross_tab(grid, bench):
    st.subheader("Cross-underlying comparison")
    c1, c2, c3 = st.columns(3)
    regime = c1.selectbox("Regime", list(REGIMES), index=len(REGIMES) - 1)
    otm = c2.selectbox("OTM %", [int(o * 100) for o in OTMS], index=1)
    dte = c3.selectbox("DTE at entry", DTES, index=1)
    st.caption(f"{otm}% OTM · {dte} DTE · {regime} — strategy vs buy & hold, per underlying.")
    rows = {}
    for u in sorted(grid["underlying"].unique()):
        g = grid[(grid["underlying"] == u) & (grid["regime"] == regime)
                 & (grid["otm_pct"] == otm / 100.0) & (grid["dte"] == dte)]
        if len(g):
            rows[f"{u} put {otm}%/{dte}d"] = _fmt_row(g.iloc[0].to_dict(), False)
        b = bench[(bench["underlying"] == u) & (bench["regime"] == regime)]
        if len(b):
            rows[f"Buy & Hold {u}"] = _fmt_row(b.iloc[0].to_dict(), True)
    if rows:
        st.dataframe(pd.DataFrame(rows).T.style.apply(_hl, axis=1), use_container_width=True)
    else:
        st.info("No rows for this selection.")


def premium_tab(surface):
    st.subheader("Premium reference — what a put historically fetched")
    c1, c2, c3 = st.columns(3)
    ul = c1.selectbox("Underlying", sorted(surface["underlying"].unique()), key="p_ul")
    vt = C.get_underlying(ul).vol_ticker
    buckets = [b for b in VOL_LABELS if b in set(surface["vol_bucket"])]
    vb = c2.selectbox(f"{vt} bucket", buckets, index=min(1, len(buckets) - 1))
    price = c3.number_input(f"Today's {ul} price ($, optional)", 0.0, 100000.0, 0.0, 1.0)
    sub = surface[(surface["underlying"] == ul) & (surface["vol_bucket"] == vb)]
    if sub.empty:
        st.info("No data for this selection."); return
    pct = sub.pivot(index="dte", columns="otm", values="median_prem_pct").sort_index()
    pct.index = [f"{d}d" for d in pct.index]
    pct.columns = [f"{c}% OTM" for c in pct.columns]
    st.markdown(f"**Median premium — % of forward** · {ul} · {vt} {vb}")
    st.dataframe(pct.style.background_gradient(cmap=NEUTRAL_CMAP, axis=None).format("{:.2f}%"),
                 use_container_width=True)
    if price > 0:
        usd = pct * price / 100.0
        st.markdown(f"**≈ Dollar premium per share** at ${price:,.2f} (× 100 per contract)")
        st.dataframe(usd.style.background_gradient(cmap=NEUTRAL_CMAP, axis=None).format("${:.2f}"),
                     use_container_width=True)
    st.caption("Historical medians 2011–2025, premium as % of forward (time-stable). "
               "A rough pricing reference, not a live quote.")


st.title("Weekly Short-Put Study — Results")
st.caption("Aggregated results only (SPY · QQQ · IWM, 2011–2025). Cash-secured, "
           "1-week hold; DTE selects the maturity sold. No live backtesting here.")

grid, bench, surface = load("grid"), load("benchmark"), load("premium_surface")
tabs = st.tabs(["🔲 Regime × Grid", "🆚 Cross-underlying", "💵 Premium reference"])
with tabs[0]:
    grid_tab(grid) if not grid.empty else st.info("Missing results/grid.parquet.")
with tabs[1]:
    cross_tab(grid, bench) if not grid.empty and not bench.empty else st.info("Missing grid/benchmark parquet.")
with tabs[2]:
    premium_tab(surface) if not surface.empty else st.info("Missing results/premium_surface.parquet.")
