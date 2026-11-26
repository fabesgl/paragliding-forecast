"""Do bias corrections improve REAL accuracy? Two tests against the BOM stations' 3 pm readings (data/forecast_error/station_pairs.pkl, from
scripts/forecast_error_stations.py). Read-only analysis; changes nothing.
  A. BARRA priors: the correction fitted against BARRA-C2 (before 2025-10, at the flat upwind point near Wangaratta) applied to forecasts after
     2025-10, scored against the Wangaratta station. (Gusts are not tested: the station gives a day maximum, the prior is for hourly gusts.)
  B. Station-learned walk-forward: for each day, the correction = mean error (or ratio) over the previous 30 days at that station, applied
     to that day. This is what corrections.py does with BOM readings; it shows what a Mystic station would give.
Usage: python scripts/validate_corrections.py"""
import sqlite3, sys
import numpy as np, pandas as pd
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "forecast_error"
KIND = {"temperature_2m": "add", "dew_point_2m": "add", "wind_speed_10m": "mul", "wind_gusts_10m": "mul"}
SPLIT, WINDOW, MIN_PRIOR = "2025-10-01", 30, 15
MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]


def fit(fc, truth, kind):
    fc, truth = np.asarray(fc, float), np.asarray(truth, float)
    if kind == "add": return float(np.mean(truth - fc))
    return float(np.clip(truth.sum() / fc.sum(), 0.5, 2.0)) if fc.sum() > 0 else 1.0


def apply(fc, c, kind):
    return fc + c if kind == "add" else np.maximum(fc * c, 0)


def test_a(SP):
    con = sqlite3.connect(OUT / "pairs.sqlite"); rows = []
    for var in ("temperature_2m", "dew_point_2m", "wind_speed_10m"):
        d = pd.read_sql(f"SELECT lead, model, date, {var} fc, {var}__bm bm FROM pairs WHERE point_key='upwind_nw'", con).dropna(); d = d[d.date < SPLIT]
        for (lead, model), g in d.groupby(["lead", "model"]):
            t = SP[(SP.variable == var) & (SP.lead == lead) & (SP.model == model) & (SP.date >= SPLIT)].dropna(subset=["fc", "obs"])
            if model not in MODELS or len(g) < 300 or len(t) < 60: continue
            c = fit(g.fc, g.bm, KIND[var]); f2 = apply(t.fc.to_numpy(), c, KIND[var])
            rows.append(dict(test="A barra-prior", variable=var, lead=lead, model=model, n=len(t), correction=c, mae_raw=(t.fc - t.obs).abs().mean(), mae_corrected=np.abs(f2 - t.obs.to_numpy()).mean()))
    return rows


def test_b(SP):
    rows = []
    for (st, var, lead, model), g in SP.groupby(["station", "variable", "lead", "model"]):
        if model not in MODELS: continue
        g = g.dropna(subset=["fc", "obs"]).sort_values("date").reset_index(drop=True); kind = KIND[var]; raw, cor = [], []
        for i in range(len(g)):
            prev = g.iloc[max(0, i - WINDOW):i]
            if len(prev) < MIN_PRIOR: continue
            c = fit(prev.fc, prev.obs, kind); f = g.fc[i]
            raw.append(abs(f - g.obs[i])); cor.append(abs(float(apply(np.array([f]), c, kind)[0]) - g.obs[i]))
        if len(raw) >= 60: rows.append(dict(test="B station-learned", station=st, variable=var, lead=lead, model=model, n=len(raw), mae_raw=np.mean(raw), mae_corrected=np.mean(cor)))
    return rows


if __name__ == "__main__":
    SP = pd.read_pickle(OUT / "station_pairs.pkl"); SP = SP[SP.lead.isin(["hist", "d1"])]
    A = pd.DataFrame(test_a(SP[SP.station == "wangaratta"])); B = pd.DataFrame(test_b(SP))
    for D in (A, B): D["change_pct"] = 100 * (D.mae_corrected / D.mae_raw - 1)
    R = pd.concat([A, B], ignore_index=True); R.to_csv(OUT / "correction_validation.csv", index=False, float_format="%.4g")
    pd.set_option("display.width", 200)
    print("A. BARRA priors vs the Wangaratta station (fitted before 2025-10, tested after). change_pct < 0 = better\n", A.groupby("variable")[["mae_raw", "mae_corrected", "change_pct"]].mean().round(2).to_string())
    print("\nB. Station-learned 30-day walk-forward\n", B.groupby(["variable", "lead"])[["mae_raw", "mae_corrected", "change_pct"]].mean().round(2).to_string())
    print("\nB by station and variable (mean over models, d1/hist)\n", B.groupby(["station", "variable"])["change_pct"].mean().round(1).unstack().to_string())
