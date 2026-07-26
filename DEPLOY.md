# Deploying the results-only dashboard

`share_app.py` is a **results-only** Streamlit app: it reads *only* the small
aggregated result files in `results/` and never touches the licensed WRDS raw
data. This makes it safe to host publicly. The source repo can stay **private**
while the app URL is **public**.

## What gets shared

Tracked & deployed:
- `share_app.py`, `optfetch/` (code), `requirements.txt`
- `results/grid.parquet` — strategy stats per (underlying, regime, OTM, DTE)
- `results/benchmark.parquet` — buy-&-hold stats per (underlying, regime)

Never tracked (see `.gitignore`): `data/` (licensed), `.pgpass` (credentials),
`results/premium_surface.parquet` (median premiums — a licensing gray area; the
Premium-reference tab just shows "missing" if it's absent).

## One-time setup

1. **Create a GitHub repo** (private is fine) — e.g. `spx-shortput-study`. Don't
   initialize it with a README (this project already has one).

2. **Push this project:**
   ```bash
   git remote add origin https://github.com/<you>/spx-shortput-study.git
   git branch -M main
   git push -u origin main
   ```

3. **Deploy on Streamlit Community Cloud** (free):
   - Go to https://share.streamlit.io and sign in with GitHub.
   - **New app** → pick your repo, branch `main`.
   - **Main file path:** `share_app.py`  ← important (not `app.py`, which needs data).
   - **Deploy.** You get a public URL like `https://<you>-spx-shortput-study.streamlit.app`.
   - Share that URL with anyone — no WRDS access or login needed to view it.

## Updating the deployed app

Re-run the precomputes locally, then push — Streamlit Cloud auto-redeploys on push:
```bash
python -m optfetch.grid          # rebuilds results/grid.parquet + benchmark.parquet
git commit -am "refresh results"
git push
```

## Notes

- The deploy installs `requirements.txt` (lean: streamlit, pandas, pyarrow,
  duckdb, numpy, matplotlib). For **local** work incl. WRDS downloads and the full
  `app.py`, install `requirements-dev.txt` instead.
- `app.py` (the full local dashboard with the live-backtest Explorer) will **not**
  run on the cloud because `data/` isn't there — that's by design. Keep the cloud
  entry point on `share_app.py`.
- To also share the Premium-reference tab, and only if your OptionMetrics terms
  permit sharing median premiums: `git add -f results/premium_surface.parquet`,
  commit, push.
