"""Turn data/forecast_error/error_summary.csv (+ pairs.sqlite) into data/forecast_error/report.md: model rankings per variable,
how much worse 1-2 days ahead is than the near-analysis 'hist' forecasts, and big consistent errors. Read-only; run
scripts/forecast_error.py first.   Usage: python scripts/forecast_error_report.py [point]   (default: mystic_launch)"""
import sqlite3, sys
import numpy as np, pandas as pd
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import forecast_error as FE
OUT = ROOT / "data" / "forecast_error"
point = next((a for a in sys.argv[1:] if not a.startswith("-")), "mystic_launch")
res = pd.read_csv(OUT / "error_summary.csv")
MIN_MEAN = {"wind_speed_10m": 8, "wind_gusts_10m": 12, "wind_speed_850hPa": 12, "wind_speed_700hPa": 20, "cape": 50, "boundary_layer_height": 200, "cloud_base_agl": 300}
NAMES = {"wind_speed_10m": "Wind 10 m (launch)", "wind_gusts_10m": "Gusts 10 m", "wind_speed_850hPa": "Wind 850 hPa", "wind_speed_700hPa": "Wind 700 hPa",
         "wind_direction_10m": "Wind direction 10 m", "wind_direction_850hPa": "Wind direction 850 hPa", "wind_direction_700hPa": "Wind direction 700 hPa",
         "temperature_2m": "Temperature 2 m", "dew_point_2m": "Dew point 2 m", "temperature_850hPa": "Temperature 850 hPa", "temperature_700hPa": "Temperature 700 hPa",
         "cloud_base_agl": "Cloud base (above ground)", "boundary_layer_height": "Boundary-layer height", "cape": "CAPE"}


def md(df, cols, fmt=None):
    fmt = fmt or {}
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            cells.append("" if pd.isna(v) else (fmt[c](v) if c in fmt else (f"{v:.2f}" if isinstance(v, float) else str(v))))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def ranking(lead):
    """Best matched basis available: matched_no_bom if it exists and keeps more days, else matched."""
    out = []
    for var in FE.VARIABLES:
        x = res[(res.point == point) & (res.lead == lead) & (res.variable == var) & (res.split == "all")]
        if x.empty: continue
        cand = {b: x[x.basis == b] for b in ("matched", "matched_no_bom") if not x[x.basis == b].empty}
        if not cand: cand = {"own": x[x.basis == "own"]}
        best = max(cand, key=lambda b: (cand[b]["n_days"].max(), b == "matched_no_bom"))
        t = cand[best].sort_values("mae").copy(); t["variable"] = NAMES[var]; t["basis"] = best; t["rank"] = range(1, len(t) + 1)
        out.append(t)
    return pd.concat(out) if out else pd.DataFrame()


def lead_degradation():
    """MAE of the same model on the same hours for hist (near-analysis), 1 day and 2 days ahead."""
    con = sqlite3.connect(OUT / "pairs.sqlite")
    rows = []
    for var in ["wind_speed_10m", "wind_gusts_10m", "temperature_2m", "dew_point_2m", "cape"]:
        kind = FE.VARIABLES[var][0]
        d = pd.read_sql(f"SELECT lead, model, time_utc, {var} AS fc, {var}__bm AS bm FROM pairs WHERE point_key=?", con, params=(point,)).dropna()
        p = d.pivot_table(index=["model", "time_utc"], columns="lead", values=["fc", "bm"])
        for model, g in p.groupby(level=0):
            if not {"hist", "d1", "d2"} <= set(g["fc"].columns): continue
            g = g.dropna(subset=[("fc", "hist"), ("fc", "d1"), ("fc", "d2")])
            if len(g) < 200: continue
            bm = g[("bm", "hist")]
            rows.append(dict(variable=NAMES[var], model=model, n=len(g), mae_hist=(g[("fc", "hist")] - bm).abs().mean(), mae_d1=(g[("fc", "d1")] - bm).abs().mean(),
                             mae_d2=(g[("fc", "d2")] - bm).abs().mean(), bias_hist=(g[("fc", "hist")] - bm).mean(), bias_d1=(g[("fc", "d1")] - bm).mean(), bias_d2=(g[("fc", "d2")] - bm).mean()))
    return pd.DataFrame(rows)


def consistent_errors(where, limit=30):
    """where='all': model-wide errors. where='cells': sector/season cells whose error differs from the model's usual one.
    Kept only if the error has the same sign in >=70% of months (months with >=15 paired hours)."""
    con = sqlite3.connect(OUT / "pairs.sqlite")
    splits = ["all"] if where == "all" else ["sector", "season"]
    x = res[(res.point == point) & (res.basis == "own") & res.enough & res.split.isin(splits) & res.kind.isin(["speed", "add"])].copy()
    overall = res[(res.point == point) & (res.basis == "own") & (res.split == "all")].set_index(["lead", "model", "variable"])
    keep = []
    for r in x.itertuples():
        o = overall.loc[(r.lead, r.model, r.variable)] if (r.lead, r.model, r.variable) in overall.index else None
        big = (abs(r.ratio - 1) >= 0.2 and r.bm_mean >= MIN_MEAN.get(r.variable, 0)) if r.kind == "speed" else (abs(r.bias) >= 1.0)
        if not big: continue
        if where == "cells" and o is not None:
            if r.kind == "speed" and abs(r.ratio - o["ratio"]) < 0.15: continue
            if r.kind == "add" and abs(r.bias - o["bias"]) < 1.0: continue
        q, prm = "SELECT date, {v} AS fc, {v}__bm AS bm FROM pairs WHERE point_key=? AND lead=? AND model=?", [point, r.lead, r.model]
        if r.split != "all": q += f" AND {r.split}=?"; prm.append(r.value)
        d = pd.read_sql(q.format(v=r.variable), con, params=prm).dropna()
        d["m"] = d["date"].str[:7]; d["e"] = d["fc"] - d["bm"]
        mm = d.groupby("m")["e"].agg(["mean", "count"]); mm = mm[mm["count"] >= 15]
        if len(mm) < 4: continue
        agree = float((np.sign(mm["mean"]) == np.sign(r.bias)).mean())
        if agree < 0.7: continue
        keep.append(dict(lead=r.lead, model=r.model, variable=NAMES[r.variable], where=("everywhere" if r.split == "all" else f"{r.split}={r.value}"), n_days=r.n_days,
                         bias=r.bias, ratio=r.ratio, usual=(None if o is None else (o["ratio"] if r.kind == "speed" else o["bias"])),
                         months_agree=f"{agree:.0%} of {len(mm)}", size=abs(np.log(r.ratio)) if r.kind == "speed" else abs(r.bias) / 3))
    k = pd.DataFrame(keep)
    return k.sort_values("size", ascending=False).head(limit).drop(columns="size") if len(k) else k


def coverage():
    x = res[(res.point == point) & (res.basis == "own") & (res.split == "all")]
    return x.groupby(["lead", "model"]).agg(first_day=("first_day", "min"), last_day=("last_day", "max"), variables=("variable", "nunique"), max_days=("n_days", "max")).reset_index()


def main():
    L = [f"# Forecast error vs BARRA-C2 - {point}", "",
         "Window: 11:00-16:00 Victorian local time. error = forecast - BARRA-C2 (positive = forecast too high). BARRA-C2 is a 4.4 km reanalysis, not real observations.",
         "'hist' = Open-Meteo Historical Forecast (first hours of each run, near-analysis, optimistic); d1/d2 = Previous Runs, the forecast made 1 / 2 days before.", "",
         "## Coverage", md(coverage(), ["lead", "model", "first_day", "last_day", "variables", "max_days"])]
    fmt = {"n": lambda v: f"{int(v)}", "n_days": lambda v: f"{int(v)}", "ratio": lambda v: f"{v:.2f}"}
    for lead, title in (("hist", "Near-analysis ('hist')"), ("d1", "1 day ahead"), ("d2", "2 days ahead")):
        t = ranking(lead)
        if t.empty: continue
        L += ["", f"## Ranking - {title}", "Same hours for every model in a row group (matched). Sorted by mean absolute error (MAE).", "",
              md(t, ["variable", "rank", "model", "basis", "n_days", "bias", "sd", "mae", "ratio", "r"], fmt)]
    ld = lead_degradation()
    if len(ld): L += ["", "## How much worse is 1-2 days ahead", "Same model, same hours (MAE in the variable's unit).", "", md(ld, list(ld.columns), fmt)]
    for where, title, note in (("all", "Big consistent errors, whole model", "ratio = forecast mean / BARRA-C2 mean (speed-like) or bias in the variable's unit. Same sign in >=70% of months."),
                               ("cells", "Wind-direction / season cells that behave differently from the model's usual error", "'usual' = the same model's all-conditions ratio (or bias).")):
        ce = consistent_errors(where)
        if len(ce): L += ["", f"## {title}", note, "", md(ce, list(ce.columns), fmt)]
    (OUT / "report.md").write_text("\n".join(L) + "\n"); print(f"wrote {OUT / 'report.md'}")


if __name__ == "__main__":
    main()
