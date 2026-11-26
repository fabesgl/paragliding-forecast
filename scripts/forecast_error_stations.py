"""Check the model ranking against real BOM station readings (read-only analysis; dashboard untouched).
Stations: Mount Hotham, Falls Creek, Wangaratta (valley stand-in: BOM has no Bright station). Data: scripts/fetch_bom_dwo.py
(Daily Weather Observations: the 3 pm reading + the day's maximum gust; about 13 months). Forecast side: data/forecast.sqlite at the
nearest forecast point. For the SAME hours the forecast is also scored against BARRA-C2, so the two rankings can be compared directly.
Score used for ranking = error left after the best simple bias correction (forecast_error.corrected_rmse).
Usage: python scripts/forecast_error_stations.py [--hour 15]      Writes data/forecast_error/station_summary.csv, station_vs_barra.csv"""
import io, json, sqlite3, sys
import numpy as np, pandas as pd
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import forecast_error as FE
OUT = ROOT / "data" / "forecast_error"; DWO = ROOT / "data" / "bom_dwo"
HOUR = int(sys.argv[sys.argv.index("--hour") + 1]) if "--hour" in sys.argv else 15
TZ = "Australia/Melbourne"
STATIONS = {"hotham": ("3055", "mount_hotham"), "falls_creek": ("3027", "falls_creek"), "wangaratta": ("3081", "upwind_nw")}
VARS = {"wind_speed_10m": "speed", "wind_gusts_10m": "speed", "temperature_2m": "add", "dew_point_2m": "add"}
RANK_MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]          # the daily job's models without ACCESS-G
COLS = ["Date", "tmin", "tmax", "rain", "evap", "sun", "gust_dir", "gust", "gust_time", "t9", "rh9", "c9", "wd9", "ws9", "p9", "t3", "rh3", "c3", "wd3", "ws3", "p3"]
BARRA_END = pd.Timestamp("2026-07-31")


def read_dwo(sid):
    frames = []
    for f in sorted(DWO.glob(f"IDCJDW{sid}.*.csv")):
        lines = f.read_text(encoding="latin-1").splitlines()
        k = next(i for i, l in enumerate(lines) if l.startswith(',"Date"'))
        d = pd.read_csv(io.StringIO("\n".join(lines[k + 1:])), header=None, usecols=range(1, 22), names=COLS)
        frames.append(d)
    d = pd.concat(frames, ignore_index=True)
    d["date"] = pd.to_datetime(d["Date"], format="%Y-%m-%d", errors="coerce").dt.strftime("%Y-%m-%d")
    for c in ["t3", "rh3", "ws3", "gust"]: d[c] = pd.to_numeric(d[c], errors="coerce")
    d["wind_speed_10m"] = d["ws3"]; d["temperature_2m"] = d["t3"]
    d["dew_point_2m"] = FE.dew_point(d["t3"], d["rh3"]); d["wind_gusts_10m"] = d["gust"]
    return d.dropna(subset=["date"]).drop_duplicates("date", keep="last").set_index("date")[list(VARS)]


def day_values(df, tcol="time_utc"):
    """From hourly rows (any number of points/models): the value at HOUR local, and the day's max gust (needs >= 20 hours of that day)."""
    loc = df[tcol].dt.tz_localize("UTC").dt.tz_convert(TZ); df = df.assign(date=loc.dt.strftime("%Y-%m-%d"), hour=loc.dt.hour)
    at = df[df["hour"] == HOUR].drop(columns=["hour"])
    g = df.groupby(["point_key", "model", "date"]).agg(gust_max=("wind_gusts_10m", "max"), n=("wind_gusts_10m", "count")).reset_index() if "model" in df else None
    return at, g


def main():
    el = json.loads((OUT / "elevations.json").read_text())
    db = sqlite3.connect(ROOT / "data" / "forecast.sqlite", timeout=60)
    pts = [p for _, p in STATIONS.values()]
    q = f"SELECT source, lead_days, point_key, model, time_utc, wind_speed_10m, wind_gusts_10m, temperature_2m, dew_point_2m FROM fc WHERE point_key IN ({','.join('?' * len(pts))})"
    fc = pd.read_sql(q, db, params=pts); fc["time_utc"] = pd.to_datetime(fc["time_utc"])
    fc["lead"] = [("hist" if s == "hist" else f"d{l}") for s, l in zip(fc["source"], fc["lead_days"])]
    bm = pd.read_pickle(OUT / "barra_points.pkl"); bm = bm[bm["point_key"].isin(pts)].copy()
    bm["temperature_2m"] += bm["point_key"].map({k: FE.height_correction_c(v["openmeteo_m"], v["barra_m"]) for k, v in el.items()})
    bm["model"] = "barra"
    rows = []
    for st, (sid, pt) in STATIONS.items():
        obs = read_dwo(sid)
        for lead, f in fc[fc["point_key"] == pt].groupby("lead"):
            at, gm = day_values(f.drop(columns=["source", "lead_days"]))
            for model, a in at.groupby("model"):
                a = a.set_index("date"); gg = gm[(gm.model == model) & (gm.n >= 20)].set_index("date")["gust_max"]
                a = a.assign(wind_gusts_10m=gg.reindex(a.index))
                for var, kind in VARS.items():
                    j = pd.DataFrame({"fc": a[var], "obs": obs[var]}).dropna()
                    j["station"], j["lead"], j["model"], j["variable"] = st, lead, model, var
                    rows.append(j.reset_index())
        bat, bg = day_values(bm[bm["point_key"] == pt][["point_key", "model", "time_utc", "wind_speed_10m", "wind_gusts_10m", "temperature_2m", "dew_point_2m"]])
        b = bat.set_index("date"); gg = bg[bg.n >= 20].set_index("date")["gust_max"]; b = b.assign(wind_gusts_10m=gg.reindex(b.index))
        for var in VARS:
            j = pd.DataFrame({"fc": b[var], "obs": obs[var]}).dropna(); j["station"], j["lead"], j["model"], j["variable"] = st, "barra_vs_obs", "barra", var
            rows.append(j.reset_index())
    pairs = pd.concat(rows, ignore_index=True)
    # BARRA value for the same day, to score each forecast against BARRA on identical rows
    bsets = []
    for st, (sid, pt) in STATIONS.items():
        bat, bg = day_values(bm[bm["point_key"] == pt][["point_key", "model", "time_utc", "wind_speed_10m", "wind_gusts_10m", "temperature_2m", "dew_point_2m"]])
        b = bat.set_index("date"); gg = bg[bg.n >= 20].set_index("date")["gust_max"]; b = b.assign(wind_gusts_10m=gg.reindex(b.index))
        for var in VARS: bsets.append(pd.DataFrame({"date": b.index, "station": st, "variable": var, "bm": b[var].values}))
    pairs = pairs.merge(pd.concat(bsets), on=["date", "station", "variable"], how="left")
    pairs.to_pickle(OUT / "station_pairs.pkl")

    summ, cmp_rows = [], []
    real = pairs[pairs["lead"] != "barra_vs_obs"]
    for (st, lead, var), g in real.groupby(["station", "lead", "variable"]):
        kind = VARS[var]; cand = g[g["model"].isin(RANK_MODELS)]
        piv = cand.pivot_table(index="date", columns="model", values="fc")
        common = piv.dropna().index if piv.shape[1] >= 2 else piv.index
        for basis, days in (("own", None), ("matched", common)):
            for model, m in g.groupby("model"):
                m = m if days is None else m[m["date"].isin(days)]
                m = m.dropna(subset=["fc", "obs"])
                if len(m) < 20: continue
                e = m["fc"] - m["obs"]
                summ.append(dict(station=st, lead=lead, variable=var, model=model, basis=basis, n=len(m), bias=e.mean(), sd=e.std(), mae=e.abs().mean(),
                                 rmse=np.sqrt((e ** 2).mean()), ratio=(m["fc"].mean() / m["obs"].mean() if kind == "speed" and m["obs"].mean() > 0 else np.nan),
                                 r=np.corrcoef(m["fc"], m["obs"])[0, 1], rmse_corr=FE.corrected_rmse(m["fc"], m["obs"], kind)))
        # same rows (matched, before BARRA ends): station-based score vs BARRA-based score for each model
        same = [d for d in common if pd.Timestamp(d) <= BARRA_END]
        for model in sorted(set(cand["model"])):
            m = cand[(cand["model"] == model) & cand["date"].isin(same)].dropna(subset=["fc", "obs", "bm"])
            if len(m) >= 20:
                cmp_rows.append(dict(station=st, lead=lead, variable=var, model=model, n=len(m), score_station=FE.corrected_rmse(m["fc"], m["obs"], kind),
                                     score_barra=FE.corrected_rmse(m["fc"], m["bm"], kind)))
    S = pd.DataFrame(summ); S.to_csv(OUT / "station_summary.csv", index=False, float_format="%.4g")
    C = pd.DataFrame(cmp_rows)
    for col in ("score_station", "score_barra"): C["rank_" + col.split("_")[1]] = C.groupby(["station", "lead", "variable"])[col].rank()
    C.to_csv(OUT / "station_vs_barra.csv", index=False, float_format="%.4g")
    # BARRA itself vs the stations (is BARRA a fair judge here?)
    bo = pairs[pairs["lead"] == "barra_vs_obs"].dropna(subset=["fc", "obs"]).groupby(["station", "variable"]).apply(
        lambda m: pd.Series(dict(n=len(m), bias=(m.fc - m.obs).mean(), sd=(m.fc - m.obs).std(), r=np.corrcoef(m.fc, m.obs)[0, 1],
                                 ratio=(m.fc.mean() / m.obs.mean() if m.obs.mean() > 0 else np.nan))), include_groups=False).reset_index()
    bo.to_csv(OUT / "barra_vs_station.csv", index=False, float_format="%.4g")
    print(f"hour={HOUR}: {len(S)} station rows, {len(C)} comparison rows"); print(bo.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
