"""Fetch today's forecasts, archive the raw response, score the next 6 days, write docs/ (dashboard).
Usage: python scripts/daily_forecast.py [site_id]"""
import csv, datetime as dt, gzip, html, json, math, os, sys, time, requests, yaml
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parent.parent
# Only the GitHub job writes into the repo (docs/, forecast_archive/, predictions/). Local test runs go to data/local_out/ (git-ignored).
OUT = ROOT if os.environ.get("GITHUB_ACTIONS") else ROOT / "data" / "local_out"
sys.path.insert(0, str(ROOT / "src"))
from pgforecast.rules import score_day, compass, acro_window_score
from pgforecast.rules_config import load_rules
from pgforecast import corrections as corr, render, fronts, blend
try: from pgforecast import creo                   # needs numpy (comes with lightgbm); without it the Creo shadow step is simply skipped
except Exception: creo = None
site = sys.argv[1] if len(sys.argv) > 1 else "bright"
cfg = yaml.safe_load((ROOT/"sites"/f"{site}.yaml").read_text())
tz = ZoneInfo(cfg["timezone"]); now = dt.datetime.now(dt.UTC); local_now = now.astimezone(tz)
MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "bom_access_global"]   # BOM ACCESS-G is skipped automatically while Open-Meteo returns no data for it
VARS = ["wind_speed_10m","wind_direction_10m","wind_gusts_10m","wind_speed_850hPa","wind_direction_850hPa",
 "wind_speed_900hPa","wind_direction_900hPa","wind_speed_800hPa","wind_direction_800hPa","wind_speed_700hPa","wind_direction_700hPa","temperature_2m","dew_point_2m","temperature_850hPa","temperature_700hPa",
 "cloud_cover_low","cloud_cover_mid","cloud_cover_high","cape","lifted_index","convective_inhibition",
 "boundary_layer_height","shortwave_radiation","precipitation","pressure_msl"]
LEVELS = [("~1,000 m", 900), ("~1,500 m", 850), ("~2,000 m", 800), ("~3,000 m", 700)]   # nominal height ASL, pressure level
LEVEL_VARS = [f"{a}_{l}hPa" for _, l in LEVELS for a in ("wind_speed", "wind_direction", "geopotential_height")]
cfg["notes"] = (ROOT / "sites" / site / "notes.md").read_text() if (ROOT / "sites" / site / "notes.md").exists() else ""   # local pilot knowledge (not used by the scoring)
cfg["rules"] = load_rules(site)                         # sites/<site>/rules.yaml: every rule threshold lives there
from pgforecast import summary as _summary
_summary.configure(cfg["rules"])
# Model weighting: 'weighted' = per-variable weights from sites/<site>/model_weights.json (ACCESS-G dropped); 'equal' = the plain average of the models, as before.
# Set it in sites/<site>.yaml (model_weighting) or, without a commit, with the GitHub variable MODEL_WEIGHTING. Both blends are always logged side by side.
WEIGHTING = (os.environ.get("MODEL_WEIGHTING") or cfg.get("model_weighting") or "weighted").strip().lower()
WTABLE = blend.load(ROOT / "sites" / site / "model_weights.json")
if WEIGHTING not in ("weighted", "equal"): print("unknown model_weighting", WEIGHTING, "- using equal"); WEIGHTING = "equal"
if WEIGHTING == "weighted" and not WTABLE: print("model_weights.json missing or unreadable - using equal weights"); WEIGHTING = "equal"
print("model weighting:", WEIGHTING)
keys = list(cfg["points"]); pts = cfg["points"]
HOURLY = ",".join(VARS + [v for v in LEVEL_VARS if v not in VARS])
def fetch_model(m):
    """One model's forecast for all points, with every variable renamed to <name>_<model> (Open-Meteo leaves the suffix off when only one model is asked for). None if it still fails after retries."""
    for attempt in range(4):
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=60, params=dict(
                latitude=",".join(str(pts[k]["lat"]) for k in keys), longitude=",".join(str(pts[k]["lon"]) for k in keys),
                hourly=HOURLY, models=m, forecast_days=7, timezone=cfg["timezone"], wind_speed_unit="kmh"))
            if r.status_code == 200:
                res = r.json(); res = res if isinstance(res, list) else [res]
                for p in res: p["hourly"] = {(k if k == "time" or k.endswith(f"_{m}") else f"{k}_{m}"): v for k, v in p["hourly"].items()}
                return res
            print(f"Open-Meteo {m}: HTTP", r.status_code, r.text[:100])
        except (requests.RequestException, ValueError) as e:
            print(f"Open-Meteo {m}: error", type(e).__name__)
        if attempt < 3: time.sleep(10 * (attempt + 1))
    return None
got = {m: fetch_model(m) for m in MODELS}
ok_models = [m for m in MODELS if got[m]]; failed_models = [m for m in MODELS if not got[m]]
if not ok_models: sys.exit("Open-Meteo unavailable after retries: every model failed")
if failed_models: print("MODELS UNAVAILABLE this update:", failed_models, "- carrying on with", ok_models)
raw = [dict(got[ok_models[0]][i], hourly={"time": got[ok_models[0]][i]["hourly"]["time"]}) for i in range(len(keys))]      # one entry per point, all models merged
for m in ok_models:
    for i in range(len(keys)): raw[i]["hourly"].update({k: v for k, v in got[m][i]["hourly"].items() if k != "time"})
# 0. fronts: spotted from the model-average pressure, 850 hPa temperature and wind direction at Mystic (information only; never changes a score)
_mh = raw[keys.index("mystic_launch")]["hourly"]
front_list = fronts.detect(_mh["time"], fronts.series(_mh, ok_models), cfg["rules"]["fronts"])
print("fronts:", [(f["kind"], f["time"], f["strength"]) for f in front_list] or "none")
# 1. archive the raw forecast (this is the real, honest forecast-by-lead record we train on later)
mr = OUT/"model_runs"/"latest.json"
runs = json.loads(mr.read_text()) if mr.exists() else {}       # which model run each forecast comes from (written by wait_for_models.py)
arch = OUT/"forecast_archive"; arch.mkdir(parents=True, exist_ok=True)
with gzip.open(arch/f"{now:%Y-%m-%dT%H%MZ}.json.gz", "wt") as f:
    json.dump({"fetched_utc": now.isoformat(), "model_runs": runs, "models_ok": ok_models, "models_failed": failed_models, "fronts": front_list, "points": keys, "response": raw}, f, separators=(",", ":"))
# 1b. apply the bias corrections (the archive above keeps the raw, uncorrected forecast)
cf = OUT/"corrections"/"corrections.json"
cstate = json.loads(cf.read_text()) if cf.exists() else corr.new_state()
n_corrected = 0
for pk in keys:
    for m in MODELS:
        n_corrected += corr.apply_hourly(cstate, m, pk, raw[keys.index(pk)]["hourly"])
print("corrected values:", n_corrected, corr.summary(cstate))
# 2. score Mystic for each model and day
mystic = raw[keys.index("mystic_launch")]["hourly"]
times = mystic["time"]; win = cfg.get("flying_window_local", ["11:00", "16:00"])
days = sorted({t[:10] for t in times})
today = local_now.date().isoformat()
out_days = []; wlog = []; creo_feats = {}                  # creo_feats: day -> each model's Creo input features (shadow mode, step 3b)
def fill(day_idx, v, m):
    """Model m's hourly values for variable v; gaps are filled with the mean of the other models (e.g. ECMWF has no boundary-layer height)."""
    own = mystic.get(f"{v}_{m}", [None] * len(times))
    others = [mystic.get(f"{v}_{o}", [None] * len(times)) for o in MODELS if o != m]
    out = []
    for i in day_idx:
        x = own[i]
        if x is None:
            vals = [o[i] for o in others if o[i] is not None]
            x = sum(vals) / len(vals) if vals else None
        out.append(x)
    return out
def mean_key(per_model, best):
    """Key numbers shown on the page: average over models (directions come from the best model)."""
    if not best: return {}
    out = dict(best["key"])
    for k, v in best["key"].items():
        if isinstance(v, (int, float)):
            vs = [p["key"][k] for p in per_model.values() if p["key"].get(k) is not None]
            out[k] = sum(vs) / len(vs) if vs else None
    return out
ARROWS = ["↓", "↙", "←", "↖", "↑", "↗", "→", "↘"]   # wind FROM N, NE, E, SE, S, SW, W, NW -> arrow shows where it blows TO
SHOW_HOURS = ["11:00", "13:00", "15:00"]
def vec_avg(pairs, wts=None):
    """Average (speed, direction-from) pairs as vectors, optionally weighted; returns (speed, direction)."""
    wts = wts or [1.0] * len(pairs); tot = sum(wts)
    u = sum(-w * s_ * math.sin(math.radians(d)) for (s_, d), w in zip(pairs, wts)) / tot
    v = sum(-w * s_ * math.cos(math.radians(d)) for (s_, d), w in zip(pairs, wts)) / tot
    return math.hypot(u, v), math.degrees(math.atan2(-u, -v)) % 360
def wind_table(day):
    rows = [(f"Launch ({cfg['launch_height_m']} m)", "wind_speed_10m", "wind_direction_10m", None)] + [
        (lab, f"wind_speed_{l}hPa", f"wind_direction_{l}hPa", f"geopotential_height_{l}hPa") for lab, l in LEVELS]
    table = []
    for lab, sv, dv, hv in rows:
        cells, heights, used = [], [], set()
        for hh in SHOW_HOURS:
            i = next((i for i, t in enumerate(times) if t == f"{day}T{hh}"), None)
            pairs, ms, wts = [], [], []
            mw = blend.weights_for(WTABLE, sv, MODELS, WEIGHTING)           # {model: weight}; in 'equal' mode every model counts the same
            for m in MODELS:
                if i is None: break
                sp, dr = mystic.get(f"{sv}_{m}", [None] * len(times))[i], mystic.get(f"{dv}_{m}", [None] * len(times))[i]
                if sp is None or dr is None or mw.get(m, 0) <= 0: continue
                pairs.append((sp, dr)); ms.append(m); wts.append(mw[m])
                if hv and mystic.get(f"{hv}_{m}", [None] * len(times))[i] is not None: heights.append(mystic[f"{hv}_{m}"][i])
            if not pairs: cells.append(None); continue
            sp, dr = vec_avg(pairs, wts); used.update(ms)
            cells.append(dict(kmh=round(sp), deg=round(dr), compass=compass(dr), arrow=ARROWS[int(((dr % 360) + 22.5) // 45) % 8], models=ms))
        table.append(dict(label=lab, height_m=round(sum(heights) / len(heights)) if heights else None, cells=cells, models=sorted(used)))
    have = {m for m in MODELS if (WEIGHTING == "equal" or m in WTABLE["models"]) and any(mystic.get(f"wind_speed_10m_{m}", [None] * len(times))[i] is not None for i in range(len(times)) if times[i][:10] == day)}
    for r in table: r["partial"] = bool(r["models"]) and set(r["models"]) != have
    return table
for day in [d for d in days if d >= today][:6]:
    per_model = {}
    for m in MODELS:
        idx = [i for i, t in enumerate(times) if t[:10] == day and win[0] <= t[11:16] <= win[1]]
        if all(mystic.get(f"wind_speed_10m_{m}", [None] * len(times))[i] is None for i in idx): continue   # model has no data (e.g. BOM ACCESS-G)
        h = {v: fill(idx, v, m) for v in VARS}
        per_model[m] = score_day(h, cfg["rules"], cfg["launch_height_m"])
        try:
            if creo: creo_feats.setdefault(day, []).append(creo.model_features(h, per_model[m], cfg["launch_height_m"]))
        except Exception as e: print("Creo shadow: features failed for", day, m, type(e).__name__, e)      # shadow mode must never stop the job
    def avg(k):
        v = [p[k] for p in per_model.values() if p[k] is not None]; return round(sum(v)/len(v)) if v else None
    best = per_model.get("gfs_seamless") or next(iter(per_model.values()), None)
    def merged(rk):                      # average each rule's factor across models; keep the wording from the best model
        out = []
        for r in (best[rk] if best else []):
            fs = [q["factor"] for p in per_model.values() for q in p[rk] if q["key"] == r["key"] and q["factor"] is not None]
            ns = [q["num"] for p in per_model.values() for q in p[rk] if q["key"] == r["key"] and q["num"] is not None]
            r = {**r, "num": sum(ns)/len(ns) if ns else r["num"], "factor": round(sum(fs)/len(fs), 3) if fs else None}
            r["value"] = (r["want"] if r["num"] is None and r["unit"] == "" and r["nd"] == 0 and r["key"] == "direction" else
                          ("n/a" if r["num"] is None else f'{r["num"]:.{r["nd"]}f}{r["unit"]}') + r["want"])
            out.append(r)
        return out
    hourly = []                                   # the XC rules applied to each hour on its own, 9am-6pm, averaged over models
    for hh in range(9, 18):
        i = next((i for i, t in enumerate(times) if t == f"{day}T{hh:02d}:00"), None)
        sc = [] if i is None else [score_day({v: fill([i], v, m) for v in VARS}, cfg["rules"], cfg["launch_height_m"]) for m in per_model]
        avg_ = lambda vals: round(sum(vals) / len(vals)) if vals else None
        acro_w = [acro_window_score(x["acro"], x["key"], cfg["rules"]) for x in sc]      # acro window only: also needs established thermals and climb height
        hourly.append(dict(h=hh, xc=avg_([x["xc"] for x in sc if x["xc"] is not None]), acro=avg_([a for a in acro_w if a is not None])))
    xcs = [p["xc"] for p in per_model.values() if p["xc"] is not None]
    day_idx = [i for i, t in enumerate(times) if t[:10] == day and win[0] <= t[11:16] <= win[1]]
    def blended_score(idx, mode):             # blend the models' hourly values per variable first, then apply the rules once
        return score_day({v: blend.blend_series({m: mystic.get(f"{v}_{m}", [None] * len(times)) for m in per_model}, v, WTABLE, mode, idx) for v in VARS}, cfg["rules"], cfg["launch_height_m"])
    sc_equal = blended_score(day_idx, "equal"); sc_weighted = blended_score(day_idx, "weighted") if WTABLE else None
    avg_xc, avg_acro = avg("xc"), avg("acro")                                      # the old way: score each model, then average the scores
    wlog.append([day, WEIGHTING, avg_xc, avg_acro, sc_equal["xc"], sc_equal["acro"], sc_weighted["xc"] if sc_weighted else "", sc_weighted["acro"] if sc_weighted else "", "+".join(per_model)])
    entry = dict(date=day, xc=avg_xc, acro=avg_acro, xc_range=[min(xcs, default=None), max(xcs, default=None)],
        models={m: {"xc": p["xc"], "acro": p["acro"]} for m, p in per_model.items()},
        key=mean_key(per_model, best), xc_rules=merged("xc_rules"), acro_rules=merged("acro_rules"), winds=wind_table(day), hourly=hourly, front=fronts.label_day(front_list, day, cfg["rules"]["fronts"]))
    if WEIGHTING == "weighted":                                                    # published numbers come from the weighted blend
        def finish(rules):                                                         # same display text as merged() gives the averaged rules
            out = []
            for r in rules:
                r = {**r, "factor": None if r["factor"] is None else round(r["factor"], 3)}
                r["value"] = (r["want"] if r["num"] is None and r["unit"] == "" and r["nd"] == 0 and r["key"] == "direction" else
                              ("n/a" if r["num"] is None else f'{r["num"]:.{r["nd"]}f}{r["unit"]}') + r["want"])
                out.append(r)
            return out
        entry.update(xc=sc_weighted["xc"], acro=sc_weighted["acro"], key=sc_weighted["key"], xc_rules=finish(sc_weighted["xc_rules"]), acro_rules=finish(sc_weighted["acro_rules"]))
        entry["hourly"] = []
        for hh in range(9, 18):
            i = next((i for i, t in enumerate(times) if t == f"{day}T{hh:02d}:00"), None)
            x = None if i is None else blended_score([i], "weighted")
            entry["hourly"].append(dict(h=hh, xc=None if x is None or x["xc"] is None else round(x["xc"]), acro=None if x is None else (lambda a: None if a is None else round(a))(acro_window_score(x["acro"], x["key"], cfg["rules"]))))
    out_days.append(entry)
# 3. log predictions (track record)
logf = OUT/"predictions"/"log.csv"; logf.parent.mkdir(parents=True, exist_ok=True)
LOG_COLS = ["made_at_utc", "target_date", "lead_days", "xc_pct", "acro_pct", "method", "models", "front_status", "front_kind", "front_time"]
old_rows = []
if logf.exists():                                    # older files lack the models column: add it (blank for old rows)
    with logf.open(newline="") as f: old_rows = [[r.get(c, "") for c in LOG_COLS] for r in csv.DictReader(f)]
with logf.open("w", newline="") as f:
    w = csv.writer(f); w.writerow(LOG_COLS); w.writerows(old_rows)
    for d in out_days:                               # models = the ones that actually went into the scores (a model that failed to download or had no data is left out)
        lead = (dt.date.fromisoformat(d["date"]) - local_now.date()).days
        w.writerow([now.strftime("%Y-%m-%dT%H:%MZ"), d["date"], lead, d["xc"], d["acro"], "rules-v3", "+".join(d["models"]), d["front"]["status"] or "none", d["front"]["kind"] or "", d["front"]["time"] or ""])
wf = OUT/"predictions"/"weighting_log.csv"           # the three ways of combining the models, side by side, for the one-month review (scripts/weighting_review.py)
W_COLS = ["made_at_utc", "target_date", "lead_days", "setting", "xc_avg_of_models", "acro_avg_of_models", "xc_blend_equal", "acro_blend_equal", "xc_blend_weighted", "acro_blend_weighted", "models"]
new_file = not wf.exists()
with wf.open("a", newline="") as f:
    w = csv.writer(f)
    if new_file: w.writerow(W_COLS)
    for r in wlog: w.writerow([now.strftime("%Y-%m-%dT%H:%MZ"), r[0], (dt.date.fromisoformat(r[0]) - local_now.date()).days, *r[1:]])
# 3b. Creo (beta): predict each day's best distance, log it next to the rules' XC % (the shadow log, kept for creo_review.py) and give the dashboard a rating word and a short why (never km).
# It never changes a score, and a failure here never stops the job: the panel is just left out.
CREO_COLS = ["made_at_utc", "target_date", "lead_days", "rules_xc_pct", "creo_km", "creo_raw_km", "no_go", "n_models", "model"]
try:
    import hashlib, lightgbm as lgb
    if creo is None: raise ImportError("creo helpers need numpy")
    cm = ROOT / "sites" / site / "creo_model.txt"
    booster = lgb.Booster(model_file=str(cm)); version = hashlib.sha1(cm.read_bytes()).hexdigest()[:8]
    calib = json.loads((ROOT / "sites" / site / "creo_model.json").read_text())["versions"][version]; cuts = calib["rating_cuts_km"]
    crows = []
    for d in out_days:
        fs = creo_feats.get(d["date"])
        if not fs: continue
        p_ = creo.predict_day(booster, fs, d["date"], d["xc"])
        try:                                                  # the dashboard panel: a rating word and up to four short lines (templates, no km)
            key = d.get("key"); word = creo.rating(p_["creo_km"], cuts)
            aloft = creo.wind_at_height(d.get("winds"), (key or {}).get("ceiling_asl_m"))            # wind aloft is described at the top of the day's climbs; the model still sees 850 and 700 hPa
            weather = {k_: v_ for k_, v_ in p_["contrib"].items() if not (k_.startswith("rule_") or k_ == "xc_pct")}      # what the weather alone says, for the "why Creo differs" line
            nogo = creo.describe(creo.reasons(p_["contrib"], p_["day"], key), _summary.dealbreaker(d["xc_rules"])[0] or "The rules' deal-breaker") if p_["no_go"] else None
            lines = creo.panel_lines(word, creo.curve_share(calib["xc_curve"], p_["creo_raw_km"]), _summary.best_window(d["hourly"], "xc"), creo.reasons(p_["contrib"], p_["day"], key, n=6, aloft=aloft),
                                     d["xc"], creo.reasons(weather, p_["day"], key, n=6, aloft=aloft), nogo, (key or {}).get("ceiling_asl_m"), (key or {}).get("thermal_ms"))
            d["creo"] = dict(rating=word, lines=lines, text=" ".join(lines))
        except Exception as e: print("Creo panel failed for", d["date"], type(e).__name__, e)
        crows.append([now.strftime("%Y-%m-%dT%H:%MZ"), d["date"], (dt.date.fromisoformat(d["date"]) - local_now.date()).days, d["xc"], round(p_["creo_km"], 1), round(p_["creo_raw_km"], 1), int(p_["no_go"]), len(fs), version])
    cf_ = OUT/"predictions"/"creo_log.csv"; new_ = not cf_.exists()
    with cf_.open("a", newline="") as f:
        w = csv.writer(f)
        if new_: w.writerow(CREO_COLS)
        w.writerows(crows)
    print("Creo shadow: logged", [(r[1], r[3], r[4]) for r in crows])
except Exception as e:
    print("Creo shadow skipped:", type(e).__name__, e)
# 4. write the dashboard
docs = OUT/"docs"; docs.mkdir(parents=True, exist_ok=True)
(docs/"forecast.json").write_text(json.dumps(dict(updated_local=local_now.strftime("%a %d %b %Y %H:%M %Z"), days=out_days), indent=1))
from pgforecast import summary
for d in out_days: d["summary"] = dict(xc=summary.xc_text(d), acro=summary.acro_text(d), pulled=summary.pulled_text(d), agreement=summary.agreement(d))
(docs/"forecast.json").write_text(json.dumps(dict(updated_local=local_now.strftime("%a %d %b %Y %H:%M %Z"), days=out_days), indent=1))
track_rows = []
if logf.exists():
    seen = {}
    for r_ in csv.DictReader(logf.open()):
        if r_["target_date"] < today: seen[r_["target_date"]] = r_   # last prediction made for each past day
    track_rows = [f'<tr><td>{d}</td><td>{r_["xc_pct"]}%</td><td>{r_["acro_pct"]}%</td><td>{r_["lead_days"]} d</td></tr>' for d, r_ in sorted(seen.items())[-14:]]
(docs/"index.html").write_text(render.render_page(out_days, cfg, local_now, local_now.date(), cstate, runs, track_rows, generated_utc=now, failed_models=failed_models, calib=(json.loads((OUT/"corrections"/"valley_wind_calibration.json").read_text()) if (OUT/"corrections"/"valley_wind_calibration.json").exists() else None)))
import shutil
shutil.copytree(ROOT/"pwa", docs, dirs_exist_ok=True)     # app manifest, service worker and icons (kept in pwa/ and published with the page)
print("ok", [(d["date"], d["xc"], d["acro"]) for d in out_days])
