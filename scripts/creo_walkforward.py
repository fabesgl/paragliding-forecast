"""Walk-forward test of Creo (report only; nothing here touches the daily job, the rules or the dashboard).
Every week from 2025-12-01: train Creo on the days BEFORE that week only (labels and flights from that week or later are never used), predict each day of the week from the forecast
as it was issued 1 and 2 days ahead, add the week, retrain, move on. The rules are scored on exactly the same forecasts and days.
  Training days: the short-lead archive (Historical Forecast API) features, as in the shadow-mode model (the only archive with all variables since 2022). Test inputs: day-ahead forecasts.
  Part A: Open-Meteo Single Runs API (00 UTC run of the day before / two days before; GFS, ECMWF 0.25, ICON), full variables incl. 850 and 700 hPa winds. The API keeps runs only from about
          April 2026, so Part A covers April 2026 on (run scripts/fetch_single_runs.py first).
  Part B: Previous Runs API (already in data/forecast.sqlite; true 1 and 2 days ahead) for the whole period from 2025-12-01. It has NO 850/700 hPa winds, cloud cover or boundary-layer height,
          so Creo and the rules are both run without them (Creo trained with the same variables blanked; the rules' 850 hPa rule cannot score).
  Days: complete months only (incomplete months skipped), tandems excluded, nobody flew = 0 km. Free-day flag 1 at prediction; a rules no-go (XC % 0) forces Creo to 0; comp weights used.
  Scores: rank correlation with the day's best distance; Creo minus rules with a 95% range from resampling whole days (a day counted at 1 and 2 days ahead is resampled as one).
Options: --embargo-days N   train only on days at least N days before the week (a check on labels that reach the archive late: flights are uploaded days after the day);
         --old-westerly     score everything with the rules as they were before the westerly change (a check on that rule having been picked after seeing 2024-2026 flights);
         --out NAME         write data/creo/NAME.md instead of walkforward.md.
Usage: python scripts/creo_walkforward.py [options]      Writes data/creo/walkforward.md"""
import glob, importlib.util, json, sqlite3, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
args = sys.argv[1:]                       # read before creo_test is imported (it rewrites sys.argv for the scoreboard script)
spec = importlib.util.spec_from_file_location("creo_test_script", ROOT / "scripts" / "creo_test.py"); CT = importlib.util.module_from_spec(spec); spec.loader.exec_module(CT)
import lightgbm as lgb
from pgforecast import creo, scoreboard as SB
M = CT.M
opt = lambda n, d: args[args.index(n) + 1] if n in args else d
START = "2025-12-01"; MIN_TRAIN_DAYS = 300; EMBARGO = int(opt("--embargo-days", "0")); OUTNAME = opt("--out", "walkforward")
if "--old-westerly" in args: M.rules["direction"].pop("westerly_launch", None)          # features, rules and training all use the old rule
COLS = list(dict.fromkeys(M.VARS + CT.EXTRA))
SR_MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless"]
BLANK_B = ["wind_speed_850hPa", "wind_direction_850hPa", "wind_speed_700hPa", "wind_direction_700hPa", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "boundary_layer_height"]   # not in the Previous Runs API


def single_run_frames():
    """{lead: raw table} from data/single_runs/<run date>.json: the run of day R gives lead 1 for R+1 and lead 2 for R+2 (flying-window hours only)."""
    out = {1: [], 2: []}
    for f in sorted(glob.glob(str(ROOT / "data" / "single_runs" / "*.json"))):
        j = json.loads(Path(f).read_text()); h = j["hourly"]; t = pd.to_datetime(h["time"]); run = pd.Timestamp(j["run"]).normalize()
        for m in SR_MODELS:
            if f"wind_speed_10m_{m}" not in h: continue                    # this model's run is missing from the archive for that day (recorded as `missing` in the file)
            df = pd.DataFrame({"model": m, "time_utc": t, **{v: h.get(f"{v}_{m}", [None] * len(t)) for v in COLS}})
            for lead in (1, 2): out[lead].append(df[df["time_utc"].dt.normalize() == run + pd.Timedelta(days=lead)])
    return {lead: M.window(pd.concat(parts, ignore_index=True)) for lead, parts in out.items() if parts}


def previous_run_frames():
    db = sqlite3.connect(ROOT / "data" / "forecast.sqlite"); out = {}
    for lead in (1, 2):
        raw = pd.read_sql(f"SELECT model, time_utc, {','.join(COLS)} FROM fc WHERE source='prev' AND lead_days=? AND point_key='mystic_launch' AND time_utc>=? AND model IN ({','.join('?' * len(M.MODELS))})", db, params=[lead, "2025-11-28"] + M.MODELS)
        raw["time_utc"] = pd.to_datetime(raw["time_utc"]); out[lead] = M.window(raw)
    return out


def test_table(frames, blank, lab):
    """One row per (date, lead): the day-ahead forecast features joined to the labels; only days with both leads, complete months, a known distance."""
    rows = []
    for lead, raw in frames.items():
        f = CT.features_from_raw(raw, blank); f["lead"] = lead; rows.append(f)
    t = pd.concat(rows, ignore_index=True); both = t.groupby("date")["lead"].nunique(); t = t[t["date"].isin(both[both == len(frames)].index)]
    t = t.merge(lab[["date", "flown", "best_km", "month_complete", "free"]], on="date", how="inner")
    t = t[t["month_complete"] & t["best_km"].notna() & t["xc_pct"].notna() & (t["date"] >= START)].copy()
    t["xc_pct"] = t["xc_pct"].round(); t["doy"] = pd.to_datetime(t["date"]).dt.day_of_year; t["free"] = 1; return t.reset_index(drop=True)


def walk(train, test, features):
    """Weekly walk-forward. Returns the test rows with creo (km) and the number of retrainings."""
    test = test.copy(); test["week"] = [creo.week_start(d, START) for d in test["date"]]; out = []; fits = 0
    for wk, te in test.groupby("week"):
        cutoff = (pd.Timestamp(wk) - pd.Timedelta(days=EMBARGO)).strftime("%Y-%m-%d"); tr = train[train["date"] < cutoff]
        assert tr.empty or tr["date"].max() < wk and not set(tr["date"]) & set(te["date"]), "training days must all be before the week"
        if len(tr) < MIN_TRAIN_DAYS: continue
        model = lgb.train(CT.PARAMS, lgb.Dataset(tr[features], tr["y"], weight=tr["distance_weight"]), num_boost_round=CT.ROUNDS); fits += 1
        te = te.copy(); te["creo_raw"] = np.clip(np.expm1(model.predict(te[features])), 0, None); te["creo"] = creo.hard_limit(te["creo_raw"], te["xc_pct"]); out.append(te)
    return (pd.concat(out, ignore_index=True) if out else pd.DataFrame()), fits


def row(name, g):
    d, lo, hi = creo.cluster_bootstrap_diff(g["date"], g["best_km"], g["creo"], g["xc_pct"])
    return dict(group=name, days=g["date"].nunique(), rows=len(g), rules=creo.spearman_fast(g["xc_pct"], g["best_km"]), creo=creo.spearman_fast(g["creo"], g["best_km"]), difference=d, range_95=f"{lo:+.3f} to {hi:+.3f}")


def tables(res):
    fm = {"rules": lambda v: f"{v:+.3f}", "creo": lambda v: f"{v:+.3f}", "difference": lambda v: f"{v:+.3f}"}
    lead = pd.DataFrame([row(f"{l} day{'s' if l > 1 else ''} ahead", g) for l, g in sorted(res.groupby("lead"))] + [row("1 and 2 days ahead together", res)])
    mon = pd.DataFrame([row(m, g) for m, g in sorted(res.groupby(res["date"].str[:7]))] + [row("all months", res)])
    return SB.md_table(lead, fm), SB.md_table(mon, fm)


def main():
    lab, _, _ = M.labels(); lab["free"] = [int(SB.is_free_day(d, M.HOLIDAYS)) for d in lab["date"]]
    L = ["# Creo walk-forward test (report only)", "", f"Made {pd.Timestamp.now():%Y-%m-%d}. See the docstring of `scripts/creo_walkforward.py`. Every week from {START}: train on the days before the week, predict that week from forecasts issued 1 and 2 days ahead, retrain.",
         "Training uses the short-lead archive's features (the same recipe as the shadow-mode model); only the test inputs are day-ahead forecasts. Rank correlation with the day's best distance; difference = Creo minus rules; the 95% range resamples whole days.", ""]
    parts = []
    sr = single_run_frames()
    if sr:
        train = CT.dataset()[0]; feats = CT.FEATURES; test = test_table(sr, (), lab); res, fits = walk(train, test, feats)
        parts.append(("A. Single Runs API (full variables), from April 2026", res, fits, train, "GFS, ECMWF 0.25, ICON; all variables incl. 850 and 700 hPa winds, cloud cover, CAPE; boundary-layer height only from GFS (filled into ECMWF and ICON as the dashboard does)."))
    pr = previous_run_frames(); train_b = CT.dataset(CT.features_from_raw(CT.load_hist_raw(), BLANK_B))[0]
    feats_b = [c for c in CT.FEATURES if train_b[c].notna().any()]; test_b = test_table(pr, BLANK_B, lab); res_b, fits_b = walk(train_b, test_b, feats_b)
    parts.append(("B. Previous Runs API (reduced variables), from December 2025", res_b, fits_b, train_b, "GFS, ECMWF 0.25 (ACCESS-G to July 2025). NO 850/700 hPa winds, cloud cover or boundary-layer height: Creo is trained without them and the rules cannot score their 850 hPa wind rule, so BOTH are weaker than in Part A."))
    parts_start = START if True else None
    for name, res, fits, train, note in parts:
        if res.empty: L += [f"## {name}", "", note, "", "No scorable days yet (no complete month with downloaded forecasts).", ""]; continue
        ok = res["date"].nunique(); L += [f"## {name}", "", note, "", f"{ok} days tested ({res['date'].min()} to {res['date'].max()}), {len(res)} predictions, {fits} weekly retrainings (training pool {train[train['date'] < res['date'].min()].shape[0]} days at the start, growing each week). Incomplete months skipped: {', '.join(sorted(set(M.labels()[0].query('not month_complete')['date'].str[:7]) & set(pd.date_range(START if name.startswith('B') else '2026-04-01', res['date'].max()).strftime('%Y-%m')))) or 'none'}.", ""]
        a, b = tables(res); L += ["### By forecast lead (the same days at 1 and 2 days ahead)", "", a, "", "### By month (both leads together)", "", b, ""]
    if len(parts) == 2 and not parts[0][1].empty and not parts[1][1].empty:
        both = parts[1][1][parts[1][1]["date"] >= parts[0][1]["date"].min()]; a = parts[0][1]
        L += ["## What the missing variables cost (same months, Part A vs Part B)", "", SB.md_table(pd.DataFrame([dict(version="A: full variables", **{k: v for k, v in row("", a).items() if k in ("days", "rules", "creo", "difference", "range_95")}),
              dict(version="B: reduced variables", **{k: v for k, v in row("", both[both["date"].isin(a["date"].unique())]).items() if k in ("days", "rules", "creo", "difference", "range_95")})]),
              {"rules": lambda v: f"{v:+.3f}", "creo": lambda v: f"{v:+.3f}", "difference": lambda v: f"{v:+.3f}"}), ""]
    (CT.OUT / f"{OUTNAME}.md").write_text("\n".join(L)); print("\n".join(L)); print(f"\nWritten: {CT.OUT / (OUTNAME + '.md')}")


if __name__ == "__main__": main()
