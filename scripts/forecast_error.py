"""Forecast error against BARRA-C2 (read-only analysis; never touches the dashboard).
For every point, model and lead time, compare the archived forecasts in data/forecast.sqlite with BARRA-C2 for the same hours
(11:00-16:00 Victorian local time) and write the error statistics to data/forecast_error/.
  error = forecast - BARRA-C2.  Wind direction sectors use the model's own 10 m wind direction (what the correction layer knows).
Usage: python scripts/forecast_error.py [site_id] [--rebuild-barra] [--no-height-correction]
Needs data/forecast_error/elevations.json (scripts/barra_terrain.py).
Needs data/barra2/ (scripts/fetch_barra2.py) and data/forecast.sqlite (scripts/fetch_forecasts.py)."""
import datetime as dt, json, sqlite3, sys, yaml
import numpy as np, pandas as pd, xarray as xr
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import forecast_error as FE

site = next((a for a in sys.argv[1:] if not a.startswith("-")), "bright")
cfg = yaml.safe_load((ROOT / "sites" / f"{site}.yaml").read_text())
TZ = cfg.get("timezone", "Australia/Melbourne")
OUT = ROOT / "data" / "forecast_error"; OUT.mkdir(parents=True, exist_ok=True)
BARRA_DIR = ROOT / "data" / "barra2"
FIRST_MONTH, LAST_MONTH = (2017, 1), (2026, 7)          # forecasts start 2017-01; BARRA-C2 download ends 2026-07
POINT_BOX = {k: ("upwind_nw" if k == "upwind_nw" else "upwind_sw" if k == "upwind_sw" else "bright_area") for k in cfg["points"]}
MIN_N, MIN_DAYS = 150, 25      # a breakdown cell is "enough data" with this many paired hours on this many different days


def months():
    y, m = FIRST_MONTH
    while (y, m) <= LAST_MONTH:
        yield f"{y}{m:02d}"; m += 1
        if m == 13: y, m = y + 1, 1


def load_box(box, var):
    parts = []
    for ym in months():
        f = BARRA_DIR / f"{box}__{var}__{ym}.nc"
        if f.exists():
            with xr.open_dataset(f) as ds: parts.append(ds[var].load())
    return xr.concat(parts, dim="time")


def barra_at_points():
    """BARRA-C2 interpolated (bilinear) to every point; one row per point and UTC hour. Cached in data/forecast_error/barra_points.pkl."""
    cache = OUT / "barra_points.pkl"
    if cache.exists() and "--rebuild-barra" not in sys.argv: return pd.read_pickle(cache)
    names = ["uas", "vas", "wsgs", "ua850", "va850", "ua700", "va700", "tas", "ta850", "ta700", "hurs", "zmla", "CAPE"]
    frames = []
    for box in sorted(set(POINT_BOX.values())):
        pts = [k for k, b in POINT_BOX.items() if b == box]
        series = {k: {} for k in pts}; times = None
        for v in names:
            da = load_box(box, v); times = da["time"].values; lats, lons = da["lat"].values, da["lon"].values
            for k in pts:
                series[k][v] = FE.bilinear(lats, lons, da.values, cfg["points"][k]["lat"], cfg["points"][k]["lon"])
            print(f"  BARRA {box} {v}", flush=True)
        for k in pts:
            s = series[k]; d = pd.DataFrame({"time_utc": pd.to_datetime(times), "point_key": k})
            d["wind_speed_10m"], d["wind_direction_10m"] = [x for x in FE.uv_to_speed_dir(s["uas"], s["vas"])]
            d["wind_speed_10m"] *= FE.KMH
            d["wind_gusts_10m"] = s["wsgs"] * FE.KMH
            for lev in (850, 700):
                sp, di = FE.uv_to_speed_dir(s[f"ua{lev}"], s[f"va{lev}"])
                d[f"wind_speed_{lev}hPa"], d[f"wind_direction_{lev}hPa"] = sp * FE.KMH, di
                d[f"temperature_{lev}hPa"] = s[f"ta{lev}"] - 273.15
            d["temperature_2m"] = s["tas"] - 273.15
            d["dew_point_2m"] = FE.dew_point(d["temperature_2m"], s["hurs"])
            d["boundary_layer_height"] = s["zmla"]; d["cape"] = s["CAPE"]
            frames.append(FE.add_derived(d))
    out = pd.concat(frames, ignore_index=True); out.to_pickle(cache); return out


def load_forecasts():
    db = sqlite3.connect(ROOT / "data" / "forecast.sqlite", timeout=60)
    cols = [c for c in FE.VARIABLES if c != "cloud_base_agl"]
    df = pd.read_sql(f"SELECT source, lead_days, point_key, model, time_utc, {','.join(cols)} FROM fc", db)
    df["time_utc"] = pd.to_datetime(df["time_utc"])
    return FE.add_derived(df)


def local_window(df):
    """Keep only hours 11:00-16:00 local; add local date, season and month."""
    loc = df["time_utc"].dt.tz_localize("UTC").dt.tz_convert(TZ)
    keep = loc.dt.hour.isin(list(FE.LOCAL_HOURS))
    df = df[keep].copy(); loc = loc[keep]
    df["date"] = loc.dt.strftime("%Y-%m-%d"); df["season"] = loc.dt.month.map(FE.season_of)
    return df


def lead_label(source, lead): return "hist" if source == "hist" else f"d{lead}"


def main():
    print("BARRA-C2 at the points ..."); bm = barra_at_points()
    print("Forecasts ..."); fc = load_forecasts()
    bm = local_window(bm); fc = local_window(fc)
    if "--no-height-correction" not in sys.argv:        # move BARRA-C2's 2 m temperature to the elevation Open-Meteo used (6.5 C per km) at Mystic, the valley and the flat points only; other variables unchanged
        el = json.loads((OUT / "elevations.json").read_text())        # from scripts/barra_terrain.py
        adj = bm["point_key"].map(FE.height_adjustments(cfg["points"], el))      # not at the ridge points (role ridge_ref)
        bm["temperature_2m"] = bm["temperature_2m"] + adj
    bm = bm.rename(columns={c: f"{c}__bm" for c in FE.VARIABLES})
    paired = fc.merge(bm.drop(columns=["date", "season"]), on=["point_key", "time_utc"], how="inner")
    paired["lead"] = [lead_label(s, l) for s, l in zip(paired["source"], paired["lead_days"])]
    paired["sector"] = FE.sector_of(paired["wind_direction_10m"].to_numpy())
    print(f"paired rows: {len(paired):,}")

    # which timestamps every model offers (per lead/point/variable): the fair basis for ranking the models
    rows = []
    for var, (kind, unit) in FE.VARIABLES.items():
        d = paired[list(dict.fromkeys(["lead", "point_key", "model", "time_utc", "date", "season", "sector", var, f"{var}__bm", "wind_speed_10m__bm"]))].copy()
        d = d.rename(columns={var: "fc", f"{var}__bm": "bm"}).dropna(subset=["fc", "bm"])
        d["err"] = FE.error_columns(d["fc"], d["bm"], kind, d["wind_speed_10m__bm"] if var.startswith("wind_direction") else None)
        d = d.dropna(subset=["err"])
        for (lead, pt), sub in d.groupby(["lead", "point_key"]):
            n_models = sub["model"].nunique()
            counts = sub.groupby("time_utc")["model"].nunique()
            common = counts[counts == n_models].index
            bases = [("own", sub)]
            if n_models >= 2: bases.append(("matched", sub[sub["time_utc"].isin(common)]))
            if "bom_access_global" in set(sub["model"]) and n_models >= 3:       # BOM ACCESS-G stops in 2025-07: also compare the rest without it
                rest = sub[sub["model"] != "bom_access_global"]
                c2 = rest.groupby("time_utc")["model"].nunique(); c2 = c2[c2 == n_models - 1].index
                bases.append(("matched_no_bom", rest[rest["time_utc"].isin(c2)]))
            for basis, frame in bases:
                for model, mg in frame.groupby("model"):
                    for st, sv, g in FE.split_groups(mg):
                        s = FE.summarise(g, kind)
                        if s is None: continue
                        s.update(lead=lead, point=pt, model=model, variable=var, unit=unit, kind=kind, basis=basis, split=st, value=sv,
                                 enough=bool(s["n"] >= MIN_N and s["n_days"] >= MIN_DAYS), n_models=int(n_models),
                                 first_day=g["date"].min(), last_day=g["date"].max())
                        s["prior"] = FE.prior_correction(kind, s)
                        rows.append(s)
        print(f"  {var}: done", flush=True)
    res = pd.DataFrame(rows)
    front = ["point", "lead", "basis", "model", "variable", "unit", "split", "value", "n", "n_days", "enough", "bias", "median_err",
             "sd", "mae", "rmse", "p10", "p90", "fc_mean", "bm_mean", "ratio", "r", "prior", "kind", "n_models", "first_day", "last_day"]
    res = res[front]
    res.to_csv(OUT / "error_summary.csv", index=False, float_format="%.4g")
    # hourly paired table for model training (launch point only keeps it small)
    keep = ["lead", "point_key", "model", "time_utc", "date", "season", "sector"] + list(FE.VARIABLES) + [f"{v}__bm" for v in FE.VARIABLES]
    tr = paired[paired["point_key"].isin(["mystic_launch", "upwind_nw", "upwind_sw"])][keep]
    con = sqlite3.connect(OUT / "pairs.sqlite"); tr.to_sql("pairs", con, if_exists="replace", index=False); con.close()
    write_priors(res)
    print(f"wrote {len(res):,} summary rows to {OUT}")


# Cells left out of the slim priors (priors_slim.json); the full set stays in data/forecast_error/priors.json
SLIM_DROP_MODELS = {"bom_access_global"}                                   # dropped from the weights too (Fabian, 2026-11-26)
SLIM_DROP_CELLS = {("wind_gusts_10m", "gfs_seamless"): "GFS gusts are left out",
                   ("cape", None): "BARRA-C2's CAPE definition is not checked yet",
                   ("wind_speed_10m", None): "BARRA's 10 m wind reads 22-43% weaker than the BOM station anemometers (Hotham, Falls Creek, Wangaratta): a prior toward BARRA would push forecasts the wrong way"}
SLIM_DROP_RIDGE_POINTS = {"mount_hotham", "falls_creek", "mount_buffalo"}   # BARRA's terrain is 240-800 m below these ridges: the 6.5 C/km height correction is not reliable there
SLIM_DROP_RIDGE_VARS = {"temperature_2m", "dew_point_2m", "cloud_base_agl"}  # the height-sensitive variables


def slim_priors(res):
    r = res[(res["basis"] == "own") & res["enough"] & res["prior"].notna() & res["split"].isin(["all", "sector"]) & ~res["model"].isin(SLIM_DROP_MODELS)]
    drop = np.zeros(len(r), dtype=bool)
    for (var, model), _ in SLIM_DROP_CELLS.items():
        drop |= ((r["variable"] == var) & ((r["model"] == model) if model else True)).to_numpy()
    drop |= (r["variable"].isin(SLIM_DROP_RIDGE_VARS) & r["point"].isin(SLIM_DROP_RIDGE_POINTS)).to_numpy()
    return r[~drop]


def write_priors(res):
    """Priors for the correction layer: one entry per model|point|variable|lead|sector, only cells with enough data.
    'mul' = multiply the forecast by corr; 'add' = add corr. BARRA-C2 is the reference, NOT real observations.
    Full set -> data/forecast_error/priors.json (local); slim set -> data/forecast_error/priors_slim.json (local; copy it to sites/<site>/priors.json by hand if the committed reference should change)."""
    def doc(r, note):
        bins = {f"{x.model}|{x.point}|{x.variable}|{x.lead}|{x.value}": dict(kind="mul" if x.kind == "speed" else "add", corr=round(float(x.prior), 3), n_days=int(x.n_days))
                for x in r.itertuples()}
        return dict(version=1, made_utc=dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ"), truth="BARRA-C2 reanalysis (not observations)", window_local="11:00-16:00",
                    temperature_2m="BARRA-C2 moved to the forecast's elevation (6.5 C per km) before comparing", note=note, bins=bins)
    full = res[(res["basis"] == "own") & res["enough"] & res["prior"].notna() & res["split"].isin(["all", "sector"])]
    (OUT / "priors.json").write_text(json.dumps(doc(full, "corr turns a forecast into a BARRA-C2-like value: mul -> forecast*corr, add -> forecast+corr"), indent=1, sort_keys=True))
    slim = slim_priors(res)
    why = "; ".join(f"{k[0]}{'/' + k[1] if k[1] else ''}: {v}" for k, v in SLIM_DROP_CELLS.items())
    d = doc(slim, "DO NOT APPLY TO FORECASTS: checked 2026-11-26 against the Wangaratta station (fitted before 2025-10, tested after), these BARRA-based corrections made the real error 11-20% WORSE for temperature, dew point and 10 m wind (scripts/validate_corrections.py). Kept for reference; use corrections learned from real station readings instead. mul -> forecast*corr, add -> forecast+corr. Left out: ACCESS-G; " + why +
            "; 2 m temperature, dew point and cloud base at the mountain points (BARRA terrain far below the ridges). Dew point and cloud base are NOT height-adjusted anywhere. Not read by the daily job yet.")
    (OUT / "priors_slim.json").write_text(json.dumps(d, separators=(",", ":"), sort_keys=True))
    print(f"priors: {len(full)} cells (local), {len(slim)} cells (data/forecast_error/priors_slim.json)")


if __name__ == "__main__":
    main()
