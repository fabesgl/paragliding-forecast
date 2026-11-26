"""PROPOSED per-variable model weights (read-only: nothing in the daily job reads this yet).
Weight of model m for a variable = (1 / s_m^2) normalised to sum 1, where s_m is the error left after the best simple bias correction
(forecast_error.corrected_rmse): a model with a steady bias is not penalised, one that is noisy is.
  * 10 m wind, gusts, 2 m temperature, dew point: scored against the BOM stations (Hotham, Falls Creek, Wangaratta) at the 3 pm reading
    (gusts: the day's maximum); each station gets equal say. Needs scripts/forecast_error_stations.py to have been run.
  * everything else: scored against BARRA-C2 at Mystic launch, 11:00-16:00 local (data/forecast_error/pairs.sqlite).
Rules from Fabian (2026-11-26): ACCESS-G dropped; GFS left out of gusts; CAPE weights are provisional (BARRA's CAPE definition unchecked).
Weights come from the 'hist' (near-analysis) lead because ICON has no archived 1-2 day data; d1/d2 agree where both exist.
Uncertainty: 10th-90th percentile of weights over 300 resamples of days.
Usage: python scripts/model_weights.py          Writes data/forecast_error/model_weights.json and prints the table."""
import json, sqlite3, sys
import numpy as np, pandas as pd
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import forecast_error as FE
OUT = ROOT / "data" / "forecast_error"
MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]          # the daily job's models without BOM ACCESS-G
EXCLUDE = {"wind_gusts_10m": {"gfs_seamless"}}
STATION_VARS = {"wind_speed_10m": "speed", "wind_gusts_10m": "speed", "temperature_2m": "add", "dew_point_2m": "add"}
BARRA_VARS = {v: k for v, (k, _) in FE.VARIABLES.items() if v not in STATION_VARS}       # kind: speed / add / dir
PROVISIONAL = {"cape": "BARRA's CAPE definition is not checked; scale-corrected score is used so a constant definition factor does not matter, but treat as provisional"}
NBOOT = 300
rng = np.random.default_rng(7)


def sums(f, t):
    f, t = np.asarray(f, float), np.asarray(t, float)
    return np.array([len(f), (f - t).sum(), ((f - t) ** 2).sum(), (f * f).sum(), (f * t).sum(), (t * t).sum()])


def score(s, kind):
    n, se, see, sff, sft, stt = s
    if n < 3: return np.nan
    if kind in ("add", "dir"): return float(np.sqrt(max(see / n - (se / n) ** 2, 0)))
    if sff <= 0: return np.nan
    k = sft / sff
    return float(np.sqrt(max((stt - 2 * k * sft + k * k * sff) / n, 0)))


def weights_from_scores(sc):
    inv = {m: 1.0 / (s ** 2) for m, s in sc.items() if s and np.isfinite(s) and s > 0}
    tot = sum(inv.values())
    return {m: v / tot for m, v in inv.items()}


def bootstrap(day_sums, kind, models):
    """day_sums: {model: DataFrame indexed by day with 6 sum columns}. Resample days (same days for all models); return {model: (lo, hi)}."""
    days = day_sums[models[0]].index.values; arrs = {m: day_sums[m].loc[days].to_numpy() for m in models}
    res = {m: [] for m in models}
    for _ in range(NBOOT):
        pick = rng.integers(0, len(days), len(days))
        w = weights_from_scores({m: score(arrs[m][pick].sum(axis=0), kind) for m in models})
        for m in models: res[m].append(w.get(m, np.nan))
    return {m: (float(np.nanpercentile(v, 10)), float(np.nanpercentile(v, 90))) for m, v in res.items()}


def per_day_sums(df, kind):
    """df: columns day, fc, truth. One row of sufficient statistics per day."""
    out = {}
    for d, g in df.groupby("day"): out[d] = sums(g["fc"], g["truth"])
    return pd.DataFrame(out, index=["n", "se", "see", "sff", "sft", "stt"]).T


def station_weights():
    P = pd.read_pickle(OUT / "station_pairs.pkl"); P = P[(P["lead"] == "hist") & P["model"].isin(MODELS)]
    res = {}
    for var, kind in STATION_VARS.items():
        models = [m for m in MODELS if m not in EXCLUDE.get(var, set())]
        per_station, ds_all = {}, {}
        for st, g in P[P["variable"] == var].groupby("station"):
            piv = g[g["model"].isin(models)].pivot_table(index="date", columns="model", values="fc"); obs = g.groupby("date")["obs"].first()
            piv = piv.dropna(); obs = obs.reindex(piv.index)
            if len(piv) < 50 or set(piv.columns) != set(models): continue
            ds = {m: per_day_sums(pd.DataFrame({"day": piv.index, "fc": piv[m].values, "truth": obs.values}), kind) for m in models}
            sc = {m: score(ds[m].sum().to_numpy(), kind) for m in models}
            per_station[st] = dict(n_days=int(len(piv)), scores=sc, weights=weights_from_scores(sc)); ds_all[st] = ds
        w = {m: float(np.mean([per_station[s]["weights"][m] for s in per_station])) for m in models}
        lo_hi = {m: [] for m in models}
        for _ in range(NBOOT):                                            # bootstrap days inside each station, then average the stations' weights
            ws = []
            for st, ds in ds_all.items():
                days = ds[models[0]].index.values; pick = rng.integers(0, len(days), len(days))
                ws.append(weights_from_scores({m: score(ds[m].to_numpy()[pick].sum(axis=0), kind) for m in models}))
            for m in models: lo_hi[m].append(np.mean([x[m] for x in ws]))
        res[var] = dict(source="BOM stations (3 pm; gusts = day max)", kind=kind, models={m: dict(weight=w[m], p10=float(np.percentile(lo_hi[m], 10)), p90=float(np.percentile(lo_hi[m], 90))) for m in models},
                        per_station={s: dict(n_days=v["n_days"], weights=v["weights"], scores=v["scores"]) for s, v in per_station.items()})
    return res


def barra_weights():
    con = sqlite3.connect(OUT / "pairs.sqlite"); res = {}
    for var, kind in BARRA_VARS.items():
        d = pd.read_sql(f"SELECT model, time_utc, date, {var} AS fc, {var}__bm AS truth, wind_speed_10m__bm AS bspd FROM pairs WHERE point_key='mystic_launch' AND lead='hist'", con)
        d = d[d["model"].isin(MODELS)].dropna(subset=["fc", "truth"])
        if var.startswith("wind_direction"):
            d = d[d["bspd"] >= FE.DIR_MIN_SPEED]; d["fc"] = FE.circ_diff(d["fc"], d["truth"]); d["truth"] = 0.0
        piv = d.pivot_table(index="time_utc", columns="model", values="fc"); models = [m for m in MODELS if m in piv.columns and piv[m].notna().sum() > 200]
        if not models: continue
        keep = piv[models].dropna().index; d = d[d["time_utc"].isin(keep) & d["model"].isin(models)]
        if len(models) == 1:
            res[var] = dict(source="BARRA-C2 at Mystic", kind=kind, models={models[0]: dict(weight=1.0, p10=1.0, p90=1.0)}, note="only one daily-job model provides this variable"); continue
        ds = {m: per_day_sums(d[d["model"] == m].rename(columns={"date": "day"})[["day", "fc", "truth"]], kind) for m in models}
        sc = {m: score(ds[m].sum().to_numpy(), kind) for m in models}; w = weights_from_scores(sc); bs = bootstrap(ds, kind, models)
        res[var] = dict(source="BARRA-C2 at Mystic", kind=kind, n_days=int(len(ds[models[0]])), scores=sc,
                        models={m: dict(weight=w[m], p10=bs[m][0], p90=bs[m][1]) for m in models}, **({"provisional": PROVISIONAL[var]} if var in PROVISIONAL else {}))
    return res


def main():
    res = {**station_weights(), **barra_weights()}
    doc = dict(version=1, status="PROPOSED - not used by the daily job", lead="hist (near-analysis)", models_considered=MODELS, dropped=["bom_access_global"],
               excluded={k: sorted(v) for k, v in EXCLUDE.items()}, variables=res)
    (OUT / "model_weights.json").write_text(json.dumps(doc, indent=1))
    short = {"ecmwf_ifs025": "ECMWF", "gfs_seamless": "GFS", "icon_seamless": "ICON"}
    print(f"{'variable':24s} {'source':8s}  " + "  ".join(f"{short[m]:>15s}" for m in MODELS))
    for v, r in res.items():
        cells = []
        for m in MODELS:
            x = r["models"].get(m); cells.append(f"{'-':>15s}" if x is None else f"{x['weight']:.2f} ({x['p10']:.2f}-{x['p90']:.2f})".rjust(15))
        print(f"{v:24s} {'stations' if 'stations' in r['source'] else 'BARRA':8s}  " + "  ".join(cells) + ("  *provisional" if "provisional" in r else ""))


if __name__ == "__main__":
    main()
