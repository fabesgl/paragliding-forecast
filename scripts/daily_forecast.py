"""Fetch today's forecasts, archive the raw response, score the next 5 days, write docs/ (dashboard).
Usage: python scripts/daily_forecast.py [site_id]"""
import csv, datetime as dt, gzip, html, json, math, os, sys, time, requests, yaml
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parent.parent
# Only the GitHub job writes into the repo (docs/, forecast_archive/, predictions/). Local test runs go to data/local_out/ (git-ignored).
OUT = ROOT if os.environ.get("GITHUB_ACTIONS") else ROOT / "data" / "local_out"
sys.path.insert(0, str(ROOT / "src"))
from pgforecast.rules import score_day, compass
from pgforecast import corrections as corr, ai_opinion, render
site = sys.argv[1] if len(sys.argv) > 1 else "bright"
cfg = yaml.safe_load((ROOT/"sites"/f"{site}.yaml").read_text())
tz = ZoneInfo(cfg["timezone"]); now = dt.datetime.now(dt.UTC); local_now = now.astimezone(tz)
MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "bom_access_global"]   # BOM ACCESS-G is skipped automatically while Open-Meteo returns no data for it
VARS = ["wind_speed_10m","wind_direction_10m","wind_gusts_10m","wind_speed_850hPa","wind_direction_850hPa",
 "wind_speed_700hPa","wind_direction_700hPa","temperature_2m","dew_point_2m","temperature_850hPa","temperature_700hPa",
 "cloud_cover_low","cloud_cover_mid","cloud_cover_high","cape","lifted_index","convective_inhibition",
 "boundary_layer_height","shortwave_radiation","precipitation","pressure_msl"]
LEVELS = [("~1,000 m", 900), ("~1,500 m", 850), ("~2,000 m", 800), ("~3,000 m", 700)]   # nominal height ASL, pressure level
LEVEL_VARS = [f"{a}_{l}hPa" for _, l in LEVELS for a in ("wind_speed", "wind_direction", "geopotential_height")]
keys = list(cfg["points"]); pts = cfg["points"]
r = None
for attempt in range(6):
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=60, params=dict(
            latitude=",".join(str(pts[k]["lat"]) for k in keys), longitude=",".join(str(pts[k]["lon"]) for k in keys),
            hourly=",".join(VARS + [v for v in LEVEL_VARS if v not in VARS]), models=",".join(MODELS), forecast_days=7, timezone=cfg["timezone"], wind_speed_unit="kmh"))
        if r.status_code == 200: break
        print("Open-Meteo HTTP", r.status_code, r.text[:150])
    except requests.RequestException as e:
        print("Open-Meteo error:", type(e).__name__)
    time.sleep(20 * (attempt + 1))
if r is None or r.status_code != 200: sys.exit("Open-Meteo unavailable after retries")
raw = r.json()
# 1. archive the raw forecast (this is the real, honest forecast-by-lead record we train on later)
mr = OUT/"model_runs"/"latest.json"
runs = json.loads(mr.read_text()) if mr.exists() else {}       # which model run each forecast comes from (written by wait_for_models.py)
arch = OUT/"forecast_archive"; arch.mkdir(parents=True, exist_ok=True)
with gzip.open(arch/f"{now:%Y-%m-%dT%H%MZ}.json.gz", "wt") as f:
    json.dump({"fetched_utc": now.isoformat(), "model_runs": runs, "points": keys, "response": raw}, f, separators=(",", ":"))
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
out_days = []
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
def vec_avg(pairs):
    """Average (speed, direction-from) pairs as vectors; returns (speed, direction)."""
    u = sum(-s_ * math.sin(math.radians(d)) for s_, d in pairs) / len(pairs)
    v = sum(-s_ * math.cos(math.radians(d)) for s_, d in pairs) / len(pairs)
    return math.hypot(u, v), math.degrees(math.atan2(-u, -v)) % 360
def wind_table(day):
    rows = [(f"Launch ({cfg['launch_height_m']} m)", "wind_speed_10m", "wind_direction_10m", None)] + [
        (lab, f"wind_speed_{l}hPa", f"wind_direction_{l}hPa", f"geopotential_height_{l}hPa") for lab, l in LEVELS]
    table = []
    for lab, sv, dv, hv in rows:
        cells, heights, used = [], [], set()
        for hh in SHOW_HOURS:
            i = next((i for i, t in enumerate(times) if t == f"{day}T{hh}"), None)
            pairs, ms = [], []
            for m in MODELS:
                if i is None: break
                sp, dr = mystic.get(f"{sv}_{m}", [None] * len(times))[i], mystic.get(f"{dv}_{m}", [None] * len(times))[i]
                if sp is None or dr is None: continue
                pairs.append((sp, dr)); ms.append(m)
                if hv and mystic.get(f"{hv}_{m}", [None] * len(times))[i] is not None: heights.append(mystic[f"{hv}_{m}"][i])
            if not pairs: cells.append(None); continue
            sp, dr = vec_avg(pairs); used.update(ms)
            cells.append(dict(kmh=round(sp), deg=round(dr), compass=compass(dr), arrow=ARROWS[int(((dr % 360) + 22.5) // 45) % 8], models=ms))
        table.append(dict(label=lab, height_m=round(sum(heights) / len(heights)) if heights else None, cells=cells, models=sorted(used)))
    have = {m for m in MODELS if any(mystic.get(f"wind_speed_10m_{m}", [None])[i] is not None for i in range(len(times)) if times[i][:10] == day)}
    for r in table: r["partial"] = bool(r["models"]) and set(r["models"]) != have
    return table
for day in [d for d in days if d >= today][:5]:
    per_model = {}
    for m in MODELS:
        idx = [i for i, t in enumerate(times) if t[:10] == day and win[0] <= t[11:16] <= win[1]]
        if all(mystic.get(f"wind_speed_10m_{m}", [None])[i] is None for i in idx): continue   # model has no data (e.g. BOM ACCESS-G)
        h = {v: fill(idx, v, m) for v in VARS}
        per_model[m] = score_day(h, tuple(cfg["launch_wind_directions_deg"][0]), tuple(cfg["unflyable_synoptic_deg"][0]), cfg["launch_height_m"])
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
        vals = [] if i is None else [score_day({v: fill([i], v, m) for v in VARS}, tuple(cfg["launch_wind_directions_deg"][0]),
                                               tuple(cfg["unflyable_synoptic_deg"][0]), cfg["launch_height_m"])["xc"] for m in per_model]
        vals = [v for v in vals if v is not None]
        hourly.append(dict(h=hh, xc=round(sum(vals) / len(vals)) if vals else None))
    xcs = [p["xc"] for p in per_model.values() if p["xc"] is not None]
    out_days.append(dict(date=day, xc=avg("xc"), acro=avg("acro"), xc_range=[min(xcs, default=None), max(xcs, default=None)],
        models={m: {"xc": p["xc"], "acro": p["acro"]} for m, p in per_model.items()},
        key=mean_key(per_model, best), xc_rules=merged("xc_rules"), acro_rules=merged("acro_rules"), winds=wind_table(day), hourly=hourly))
# 2b. AI second opinion. Read-only: it never changes the rule scores above, and any failure here just means no panel.
active = [f"{k.split('|')[1]} {k.split('|')[0]} {k.split('|')[2]} ({k.split('|')[3]}): " + (f"{b['corr']:+.1f}" if b["kind"] == "add" else f"x{b['corr']:.2f}")
          for k, b in cstate["bins"].items() if corr.status(b) == "active"]
ai, ai_status = ai_opinion.opinions(out_days, {d["date"]: d["winds"] for d in out_days}, active, corr.summary(cstate), cfg)
print("AI opinion:", ai_status)
for d in out_days: d["ai"] = ai.get(d["date"])
# 3. log predictions (track record)
logf = OUT/"predictions"/"log.csv"; logf.parent.mkdir(parents=True, exist_ok=True)
new = not logf.exists()
with logf.open("a", newline="") as f:
    w = csv.writer(f)
    if new: w.writerow(["made_at_utc", "target_date", "lead_days", "xc_pct", "acro_pct", "method"])
    for d in out_days:
        lead = (dt.date.fromisoformat(d["date"]) - local_now.date()).days
        w.writerow([now.strftime("%Y-%m-%dT%H:%MZ"), d["date"], lead, d["xc"], d["acro"], "rules-v3"])
aif = OUT/"predictions"/"ai_log.csv"                 # same keys as log.csv, plus the rule scores, so both can be compared with outcomes
AI_COLS = ["made_at_utc", "target_date", "lead_days", "model", "xc_call", "acro_call", "xc_rule_pct", "acro_rule_pct", "limit_forced", "reasoning"]
if ai:
    rows_old = []
    if aif.exists():                                  # older files lack limit_forced: add the column (blank for old rows)
        with aif.open(newline="") as f: rd = list(csv.DictReader(f))
        rows_old = [[r.get(c, "") for c in AI_COLS] for r in rd]
    with aif.open("w", newline="") as f:
        w = csv.writer(f); w.writerow(AI_COLS); w.writerows(rows_old)
        for d in out_days:
            if d["ai"]: w.writerow([now.strftime("%Y-%m-%dT%H:%MZ"), d["date"], (dt.date.fromisoformat(d["date"]) - local_now.date()).days, ai_opinion.MODEL,
                                    d["ai"]["xc_call"], d["ai"]["acro_call"], d["xc"], d["acro"], d["ai"].get("forced", ""), d["ai"]["reasoning"]])
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
(docs/"index.html").write_text(render.render_page(out_days, cfg, local_now, local_now.date(), cstate, runs, ai_status, track_rows, generated_utc=now))
import shutil
shutil.copytree(ROOT/"pwa", docs, dirs_exist_ok=True)     # app manifest, service worker and icons (kept in pwa/ and published with the page)
print("ok", [(d["date"], d["xc"], d["acro"]) for d in out_days])
