"""Export & plot backtest results: CSV blotter + a static equity-curve PNG.

CSV is the archival/Excel path; the PNG is a quick static look. Interactive
exploration lives in the Streamlit app (app.py), which calls the same engine.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt

from . import config as C

RESULTS_DIR = C.PROJECT_ROOT / "results"

# dataviz reference palette — validated categorical slots 1..n (light surface)
_SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
_SURFACE = "#fcfcfb"
_INK = "#0b0b0b"
_MUTED = "#898781"
_GRID = "#e1e0d9"


def save_csv(res: dict, label: str, outdir: Path | None = None) -> Path:
    """Write trades + equity CSVs for one backtest run; return the folder."""
    outdir = outdir or RESULTS_DIR
    outdir.mkdir(parents=True, exist_ok=True)
    res["trades"].to_csv(outdir / f"{label}_trades.csv", index=False)
    res["equity"].to_csv(outdir / f"{label}_equity_weekly.csv", index=False)
    res["daily"].to_csv(outdir / f"{label}_equity_daily.csv", index=False)
    (outdir / f"{label}_summary.txt").write_text(
        "\n".join(f"{k}: {v}" for k, v in res["summary_dict"].items())
    )
    return outdir


def plot_equity(runs: dict[str, dict], outpath: Path | None = None,
                curve: str = "daily", title: str | None = None) -> Path:
    """Overlay equity curves. `runs` maps label -> backtest result.

    curve='daily' uses the daily mark-to-market curve (recommended — shows
    intra-week paths); 'weekly' uses the Friday-only compounded curve.
    """
    outpath = outpath or (RESULTS_DIR / f"equity_curve_{curve}.png")
    outpath.parent.mkdir(parents=True, exist_ok=True)
    if title is None:
        title = (f"Weekly short-put — cumulative return "
                 f"({'daily mark-to-market' if curve == 'daily' else 'weekly compounded'})")

    fig, ax = plt.subplots(figsize=(9, 5), dpi=130)
    fig.patch.set_facecolor(_SURFACE)
    ax.set_facecolor(_SURFACE)

    for i, (label, res) in enumerate(runs.items()):
        color = _SERIES[i % len(_SERIES)]
        if curve == "daily":
            eq = res["daily"]
            x, y = eq["date"], (eq["equity_daily"] - 1.0) * 100.0
        else:
            eq = res["equity"]
            x, y = eq["entry_date"], (eq["equity_compounded"] - 1.0) * 100.0
        ax.plot(x, y, color=color, lw=2, label=label)
        ax.annotate(f" {label}: {y.iloc[-1]:+.2f}%",
                    xy=(x.iloc[-1], y.iloc[-1]),
                    color=color, fontsize=9, va="center", weight="bold")

    ax.axhline(0, color=_GRID, lw=1)
    ax.grid(True, color=_GRID, lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c3c2b7")
    ax.tick_params(colors=_MUTED, labelsize=9)
    ax.set_ylabel("Cumulative return (%)", color=_MUTED, fontsize=10)
    ax.set_title(title, color=_INK, fontsize=12, weight="bold", loc="left")
    ax.margins(x=0.08)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(outpath, facecolor=_SURFACE)
    plt.close(fig)
    return outpath
