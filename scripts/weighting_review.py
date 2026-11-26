"""Did weighting the models help? Compares, against real BOM station readings, the forecast blended with EQUAL weights vs the approved
per-variable WEIGHTS (sites/<site>/model_weights.json), using the raw (uncorrected) forecasts saved in forecast_archive/.
Stations: the points that have a BOM station (Mount Hotham, Falls Creek, Wangaratta). Hours 11:00-16:00 local, lead days 1-5.
Score = error left after the best simple bias correction (add: spread of the error; speed: RMSE after the best multiplier).
Verdict per variable from the mean improvement over stations and a bootstrap over days (helped / no clear difference / hurt).
Writes corrections/weighting_review.json. When 30 target days with readings exist for the first time, the GitHub job opens an issue.
Pure stdlib (the daily job has no numpy). Usage: python scripts/weighting_review.py [site_id]"""
import datetime as dt, gzip, json, math, os, random, sys, yaml
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
from pgforecast import blend, corrections as C
import update_corrections as U

VARS = {"temperature_2m": "add", "dew_point_2m": "add", "wind_speed_10m": "speed", "wind_gusts_10m": "speed"}
HOURS = range(11, 17); MIN_DAYS = 30; NBOOT = 300; MARGIN = 0.02          # a change under 2% is called no clear difference
BUCKETS = {"1-2 days": (1, 2), "3-5 days": (3, 5)}


def stat(rows, kind):
    """rows: list of (forecast, observation). Error left after the best simple bias correction."""
    n = len(rows)
    if n < 5: return None
    if kind == "add":
        e = [f - o for f, o in rows]; m = sum(e) / n; return math.sqrt(sum((x - m) ** 2 for x in e) / n)
    sff = sum(f * f for f, _ in rows); sfo = sum(f * o for f, o in rows)
    if sff <= 0: return None
    k = sfo / sff; return math.sqrt(sum((o - k * f) ** 2 for f, o in rows) / n)


def improvement(by_day, days, kind):
    """by_day: {station: {date: [(f_equal, f_weighted, obs), ...]}}; mean over stations of 1 - score_weighted / score_equal, over the given days."""
    vals = []
    for st, d in by_day.items():
        rows = [r for day in days for r in d.get(day, [])]
        e, w = stat([(a, o) for a, _, o in rows], kind), stat([(b, o) for _, b, o in rows], kind)
        if e and w and e > 0: vals.append(1 - w / e)
    return sum(vals) / len(vals) if vals else None


def verdict(mean_imp, p_better, n_days=99):
    if mean_imp is None or n_days < 10: return "not enough data"
    if mean_imp > MARGIN and p_better >= 0.9: return "helped"
    if mean_imp < -MARGIN and p_better <= 0.1: return "hurt"
    return "no clear difference"


def collect(arch_dir, obs_dir, cfg, table):
    tz = ZoneInfo(cfg["timezone"]); stations = {k: p["bom_station"] for k, p in cfg["points"].items() if p.get("bom_station")}
    obs = {k: U.load_obs(Path(obs_dir) / f"{sid}.csv") for k, sid in stations.items() if (Path(obs_dir) / f"{sid}.csv").exists()}
    data = {}                                   # (var, bucket) -> {station: {date: [(equal, weighted, obs)]}}
    for p in sorted(Path(arch_dir).glob("*.json.gz")):
        j = json.load(gzip.open(p, "rt")); t = dt.datetime.fromisoformat(j["fetched_utc"]); run_day = t.astimezone(tz).date()
        models = j.get("models_ok") or []
        for pk, pobs in obs.items():
            if pk not in j["points"]: continue
            h = j["response"][j["points"].index(pk)]["hourly"]; n = len(h["time"])
            for var, kind in VARS.items():
                series = {m: h[f"{var}_{m}"] for m in models if h.get(f"{var}_{m}")}
                if not series: continue
                eq = blend.blend_series(series, var, table, "equal"); wt = blend.blend_series(series, var, table, "weighted")
                for i, ts in enumerate(h["time"]):
                    lt = dt.datetime.fromisoformat(ts).replace(tzinfo=tz)
                    if lt.hour not in HOURS: continue
                    lead = (lt.date() - run_day).days
                    o = pobs.get(lt.astimezone(dt.UTC).replace(minute=0, second=0, microsecond=0), {}).get(C.VARS[var][0])
                    if o is None or eq[i] is None or wt[i] is None: continue
                    for b, (lo, hi) in BUCKETS.items():
                        if lo <= lead <= hi: data.setdefault((var, b), {}).setdefault(pk, {}).setdefault(lt.date().isoformat(), []).append((eq[i], wt[i], o))
    return data


def review(data, seed=7):
    rng = random.Random(seed); out = {}
    for (var, b), by_day in sorted(data.items()):
        days = sorted({d for st in by_day.values() for d in st}); kind = VARS[var]
        mean = improvement(by_day, days, kind)
        if mean is None: continue
        better = 0; draws = 0
        for _ in range(NBOOT):
            pick = [rng.choice(days) for _ in days]; x = improvement(by_day, pick, kind)
            if x is not None: draws += 1; better += x > 0
        p = better / draws if draws else 0.5
        out.setdefault(var, {})[b] = dict(n_days=len(days), stations=sorted(by_day), mean_improvement_pct=round(100 * mean, 1), p_weighted_better=round(p, 2), verdict=verdict(mean, p, len(days)))
    return out


def score_differences(path):
    """How different are the published scores? From predictions/weighting_log.csv (target date's latest run only)."""
    import csv
    if not Path(path).exists(): return None
    last = {}
    for r in csv.DictReader(open(path)):
        if r["xc_blend_weighted"] != "": last[(r["target_date"], r["lead_days"])] = r
    rows = list(last.values())
    if not rows: return None
    f = lambda a, b: round(sum(abs(float(r[a]) - float(r[b])) for r in rows) / len(rows), 1)
    return dict(n=len(rows), mean_abs_xc_diff_avg_vs_weighted=f("xc_avg_of_models", "xc_blend_weighted"), mean_abs_xc_diff_equal_blend_vs_weighted=f("xc_blend_equal", "xc_blend_weighted"),
                mean_abs_xc_diff_avg_vs_equal_blend=f("xc_avg_of_models", "xc_blend_equal"))


if __name__ == "__main__":
    site = sys.argv[1] if len(sys.argv) > 1 else "bright"
    OUT = ROOT if os.environ.get("GITHUB_ACTIONS") else ROOT / "data" / "local_out"
    cfg = yaml.safe_load((ROOT / "sites" / f"{site}.yaml").read_text()); table = blend.load(ROOT / "sites" / site / "model_weights.json")
    if not table: sys.exit("no sites/%s/model_weights.json" % site)
    arch = OUT / "forecast_archive" if os.environ.get("GITHUB_ACTIONS") else ROOT / "forecast_archive"
    res = review(collect(arch, ROOT / "observations", cfg, table))
    days = max((v[b]["n_days"] for v in res.values() for b in v if b == "1-2 days"), default=0)
    doc = dict(updated=dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ"), min_days=MIN_DAYS, n_days=days, ready=days >= MIN_DAYS, variables=res,
               score_differences=score_differences(OUT / "predictions" / "weighting_log.csv"))
    f = OUT / "corrections" / "weighting_review.json"; f.parent.mkdir(parents=True, exist_ok=True)
    old = json.loads(f.read_text()) if f.exists() else {}; doc["notified"] = bool(old.get("notified"))
    f.write_text(json.dumps(doc, indent=1))
    print(f"weighting review: {days}/{MIN_DAYS} days with station readings")
    for var, v in res.items():
        print(f"  {var:16s}", "  ".join(f"{b}: {x['mean_improvement_pct']:+.1f}% (p={x['p_weighted_better']}) {x['verdict']}" for b, x in v.items()))
    if doc["ready"] and not doc["notified"] and os.environ.get("GITHUB_ACTIONS") and os.environ.get("GH_TOKEN"):     # tell the owner once, with a GitHub issue
        import subprocess
        lines = [f"- {var} ({b}): {x['mean_improvement_pct']:+.1f}% error vs equal weights, weighted better in {int(x['p_weighted_better'] * 100)}% of resamples -> **{x['verdict']}**" for var, v in res.items() for b, x in v.items()]
        body = (f"One month of weighted vs equal blends, scored against BOM stations ({', '.join(sorted({s for v in res.values() for x in v.values() for s in x['stations']}))}), {days} days.\n\n"
                + "\n".join(lines) + f"\n\nScore differences: {json.dumps(doc['score_differences'])}\n\nPositive % = weighting cut the error. Nothing has been changed. To go back to equal weights set the GitHub variable MODEL_WEIGHTING to `equal` "
                  f"(Settings > Variables) or set model_weighting in sites/{site}.yaml. Details: corrections/weighting_review.json.")
        r = subprocess.run(["gh", "issue", "create", "--title", "Weighting review: did weighting the models help?", "--body", body], capture_output=True, text=True)
        if r.returncode == 0: doc["notified"] = True; f.write_text(json.dumps(doc, indent=1)); print("opened a GitHub issue")
        else: print("could not open an issue:", r.stderr[:200])
