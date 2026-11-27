"""Creo, first test (report only: nothing here touches the daily job, the rules or the dashboard).
LightGBM predicts a day's best flown distance (log scale) from the forecast-side weather of the 11:00-16:00 window, the XC rules' scores, day of year and a free-day flag.
  - Days: labelled days 2022 on, tandems excluded, only months whose XContest pages are complete (all other months dropped entirely), no IGC-only days (no distance).
  - Weights: the comp-day distance weights from the labels (count_weight is not used: no flight counts are predicted or used as features).
  - Prediction: the free-day flag is set to 1 on every day, so Creo rates the weather, not the crowd. A day where the rules say no-go (XC % = 0) predicts 0.
  - Test: leave-one-year-out. Creo and the rules are compared on the same days with the scoreboard's rank correlation (XC % or predicted km vs best km, nobody flew = 0).
Shallow trees, strong regularisation, settings fixed in advance (not tuned on the test years).
Usage: python scripts/creo_test.py      Writes data/creo/report.md. Needs data/forecast.sqlite, xcontest/igc databases, """
import importlib.util, sqlite3, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.argv = ["scoreboard", "--from", "2022-01-01"]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("scoreboard_script", ROOT / "scripts" / "scoreboard.py"); M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
import lightgbm as lgb
from pgforecast import creo, scoreboard as SB
from pgforecast.rules import score_day

OUT = ROOT / "data" / "creo"; OUT.mkdir(parents=True, exist_ok=True)
EXTRA = ["cloud_cover_mid", "cloud_cover_high", "wind_direction_700hPa"]
ROUNDS = 250
PARAMS = dict(objective="regression", learning_rate=0.03, num_leaves=4, max_depth=3, min_data_in_leaf=25, lambda_l2=20.0, lambda_l1=1.0, bagging_fraction=0.7, bagging_freq=1,
              feature_fraction=0.7, seed=0, verbosity=-1, num_threads=2, deterministic=True, force_row_wise=True)
WEATHER = [f"{w}_{s}" for w in ("wind10", "wind850", "wind700") for s in ("speed", "sin", "cos")] + ["gust", "temp", "dew_point", "cloud_base_asl", "cape", "rain", "cloud_low", "cloud_mid", "cloud_high"]
FEATURES = WEATHER + [f"rule_{k}" for k in creo.XC_RULES] + ["xc_pct", "doy", "free"]


def forecast_features():
    """One row per day: the models' flying-window features averaged (each model scored with the live rules first)."""
    db = sqlite3.connect(ROOT / "data" / "forecast.sqlite")
    cols = list(dict.fromkeys(M.VARS + EXTRA))
    raw = pd.read_sql(f"SELECT model, time_utc, {','.join(cols)} FROM fc WHERE source='hist' AND lead_days=0 AND point_key='mystic_launch' AND model IN ({','.join('?' * len(M.MODELS))})", db, params=M.MODELS)
    raw["time_utc"] = pd.to_datetime(raw["time_utc"]); raw = M.window(raw[raw["time_utc"] >= pd.Timestamp(M.FIRST) - pd.Timedelta(days=1)]); rows = []
    for date, g in raw.groupby("date"):
        hours = sorted(g["time_utc"].unique()); piv = {m: g[g["model"] == m].set_index("time_utc").reindex(hours) for m in M.MODELS if (g["model"] == m).any()}; per = []
        for m, p in piv.items():
            if p["wind_speed_10m"].notna().sum() < M.MIN_HOURS: continue
            h = {}
            for v in cols:                            # gaps (e.g. no boundary-layer height from ECMWF) are filled from the other models, as the dashboard does
                own = p[v].tolist(); others = [piv[o][v].tolist() for o in piv if o != m]; col = []
                for i, x in enumerate(own):
                    if pd.isna(x):
                        vals = [o[i] for o in others if not pd.isna(o[i])]; x = sum(vals) / len(vals) if vals else None
                    col.append(None if x is None or pd.isna(x) else float(x))
                h[v] = col
            r = score_day(h, M.rules, M.LAUNCH)
            if r["xc"] is not None: per.append(creo.model_features(h, r, M.LAUNCH))
        if per: rows.append(dict(date=date, n_models=len(per), **creo.day_features(per)))
    return pd.DataFrame(rows)


def dataset():
    lab, _, _ = M.labels()
    lab["free"] = [int(SB.is_free_day(d, M.HOLIDAYS)) for d in lab["date"]]
    df = lab.merge(forecast_features(), on="date", how="inner")
    df["year"] = df["date"].str[:4].astype(int); df["doy"] = pd.to_datetime(df["date"]).dt.day_of_year
    n0 = len(df); df = df[df["month_complete"] & df["best_km"].notna() & df["xc_pct"].notna()].reset_index(drop=True)
    df["xc_pct"] = df["xc_pct"].round(); df["y"] = np.log1p(df["best_km"])
    return df, n0


def loyo(df, years):
    """Leave-one-year-out predictions for `years` (the other listed years train). Returns the test rows with creo (km), rules (XC %) and the feature gains."""
    out, gains = [], []
    for y in years:
        tr, te = df[(df["year"] != y) & df["year"].isin(years)], df[df["year"] == y].copy()
        model = lgb.train(PARAMS, lgb.Dataset(tr[FEATURES], tr["y"], weight=tr["distance_weight"]), num_boost_round=ROUNDS)
        g = model.feature_importance("gain"); gains.append(pd.Series(g, index=FEATURES) / g.sum())
        te["free"] = 1                                           # rate the weather, not the crowd
        te["creo_raw"] = np.clip(np.expm1(model.predict(te[FEATURES])), 0, None)
        te["creo"] = creo.hard_limit(te["creo_raw"], te["xc_pct"]); out.append(te)
    return pd.concat(out, ignore_index=True), pd.concat(gains, axis=1).mean(axis=1).sort_values(ascending=False)


def compare(res):
    rows = []
    for name, g in [(str(y), res[res["year"] == y]) for y in sorted(res["year"].unique())] + [("all years pooled", res)]:
        d, lo, hi = creo.bootstrap_diff(g["best_km"], g["creo"], g["xc_pct"])
        rows.append(dict(period=name, days=len(g), rules=creo.spearman_fast(g["xc_pct"], g["best_km"]), creo=creo.spearman_fast(g["creo"], g["best_km"]), difference=d, range_95=f"{lo:+.3f} to {hi:+.3f}"))
    return pd.DataFrame(rows)


def bands(res):
    rules = SB.band_table(res.assign(x=res["xc_pct"]), "x")
    res = res.assign(cb=creo.matched_bands(res["creo"].to_numpy(), res["xc_pct"].to_numpy())); rows = []
    for b in ("0-20", "20-40", "40-60", "60-80", "80-100"):
        g = res[res["cb"] == b]; fl = g[g["flown"]]
        rows.append(dict(band=b + "%", days=len(g), pct_flown=100 * len(fl) / len(g) if len(g) else np.nan, median_best_km=float(fl["best_km"].median()) if len(fl) else np.nan))
    return rules, pd.DataFrame(rows)


def main():
    df, n0 = dataset()
    L = ["# Creo, first test (report only)", "", f"Made {pd.Timestamp.now():%Y-%m-%d}. LightGBM (shallow: {PARAMS['num_leaves']} leaves, depth {PARAMS['max_depth']}, {ROUNDS} rounds at {PARAMS['learning_rate']}, L2 {PARAMS['lambda_l2']}); settings fixed in advance, not tuned on the test years. Target: log(1 + best distance km) of the day, tandems excluded, nobody flew = 0 km.",
         "Features: forecast-side weather for 11:00-16:00 (wind at 10 m, 850 and 700 hPa as speed and direction sin/cos, gusts, temperature, dew point, cloud base, CAPE, rain, low/mid/high cloud), each XC rule's score, the XC %, day of year and a weekend/holiday flag. At prediction the flag is set to free day for every day. Rules' no-go (XC % = 0) forces Creo to 0.",
         f"Training days: labelled days 2022-2026 in months with complete XContest data ({len(df)} of {n0} with forecasts; every day of an incomplete month is dropped, flown days included). Comp-day distance weights applied (flight counts are not used, so `count_weight` plays no part).", ""]
    pooled = {}
    for name, years in (("A. All years 2022-2026", [2022, 2023, 2024, 2025, 2026]), ("B. Without 2022 (not trained on, not tested): 2023-2026", [2023, 2024, 2025, 2026])):
        res, imp = loyo(df, years); cmp_ = compare(res); rb, cb = bands(res); pooled[name] = res
        z = res[res["xc_pct"] <= 0]
        L += [f"## {name}", "", f"{len(res)} days tested leave-one-year-out. Days the hard limit zeroes: {len(z)}, of which flown {int(z['flown'].sum())}.", "",
              "### Rank correlation with best distance (higher is better); difference = Creo minus rules, with a resampled 95% range", "",
              SB.md_table(cmp_, {"rules": lambda v: f"{v:+.3f}", "creo": lambda v: f"{v:+.3f}", "difference": lambda v: f"{v:+.3f}"}), "",
              "### Bands, rules (XC %)", "", SB.md_table(rb, {"pct_flown": lambda v: f"{v:.0f}%", "median_best_km": lambda v: f"{v:.0f}"}), "",
              "### Bands, Creo (same number of days in each band as the rules, by predicted distance)", "", SB.md_table(cb, {"pct_flown": lambda v: f"{v:.0f}%", "median_best_km": lambda v: f"{v:.0f}"}), "",
              "### What Creo relies on (share of the trees' total gain, averaged over the folds)", "", SB.md_table(imp.head(12).round(3).rename("share").rename_axis("feature").reset_index(), {"share": lambda v: f"{100 * v:.1f}%"}), ""]
    (OUT / "report.md").write_text("\n".join(L)); print("\n".join(L)); print(f"\nWritten: {OUT / 'report.md'}")


if __name__ == "__main__": main()
