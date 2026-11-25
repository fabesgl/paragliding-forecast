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
    xcs = [p["xc"] for p in per_model.values() if p["xc"] is not None]
    out_days.append(dict(date=day, xc=avg("xc"), acro=avg("acro"), xc_range=[min(xcs, default=None), max(xcs, default=None)],
        models={m: {"xc": p["xc"], "acro": p["acro"]} for m, p in per_model.items()},
        key=mean_key(per_model, best), xc_rules=merged("xc_rules"), acro_rules=merged("acro_rules"), winds=wind_table(day)))
# 3. log predictions (track record)
logf = OUT/"predictions"/"log.csv"; logf.parent.mkdir(parents=True, exist_ok=True)
new = not logf.exists()
with logf.open("a", newline="") as f:
    w = csv.writer(f)
    if new: w.writerow(["made_at_utc", "target_date", "lead_days", "xc_pct", "acro_pct", "method"])
    for d in out_days:
        lead = (dt.date.fromisoformat(d["date"]) - local_now.date()).days
        w.writerow([now.strftime("%Y-%m-%dT%H:%MZ"), d["date"], lead, d["xc"], d["acro"], "rules-v3"])
# 4. write the dashboard
docs = OUT/"docs"; docs.mkdir(parents=True, exist_ok=True)
(docs/"forecast.json").write_text(json.dumps(dict(updated_local=local_now.strftime("%a %d %b %Y %H:%M %Z"), days=out_days), indent=1))
def color(p): return "#9aa0a6" if p is None else "#c62828" if p < 25 else "#ef6c00" if p < 45 else "#f9a825" if p < 65 else "#2e7d32"
def f1(x, u=""): return "n/a" if x is None else f"{x:.0f}{u}"
def pulled(rules, name):
    """Rules that cost the score the most. Each shows how much of the score that rule alone removes."""
    bad = sorted([r for r in rules if r["factor"] is not None and r["factor"] < 0.9], key=lambda r: r["factor"])
    if not bad: return f'<p class="pd ok"><b>{name}:</b> no rule pulled it down</p>'
    lis = "".join(f'<li>{html.escape(r["label"])}: {html.escape(r["value"])} <b>-{round(100*(1-r["factor"]))}%</b></li>' for r in bad[:4])
    return f'<p class="pd"><b>{name} pulled down by:</b></p><ul class="why">{lis}</ul>'
def wind_html(rows):
    names = {"gfs_seamless": "GFS", "ecmwf_ifs025": "ECMWF", "icon_seamless": "ICON", "bom_access_global": "BOM"}
    out = ['<table class="wt"><tr><th>Height ASL</th>' + "".join(f"<th>{h}</th>" for h in SHOW_HOURS) + "</tr>"]
    foot = []
    for r in rows:
        lab = r["label"] + (f'<small>{r["height_m"]:,} m</small>' if r["height_m"] else "")
        mark = "<sup>*</sup>" if r["partial"] else ""
        if r["partial"]: foot.append(f'{html.escape(r["label"])}: only {", ".join(names.get(m, m) for m in r["models"])}')
        tds = "".join("<td>n/a</td>" if c is None else f'<td><span class="ar">{c["arrow"]}</span> {c["compass"]} <b>{c["kmh"]}</b></td>' for c in r["cells"])
        out.append(f"<tr><td>{lab}{mark}</td>{tds}</tr>")
    out.append("</table>")
    note = f'<p class="note">* not every model has this level; shown from: {"; ".join(foot)}. </p>' if foot else ""
    return '<h3>Wind by height (km/h, local time)</h3>' + "".join(out) + note
cards = []
for d in out_days:
    k = d["key"]; dn = dt.date.fromisoformat(d["date"]).strftime("%a %d %b")
    spread = f'{d["xc_range"][0]}-{d["xc_range"][1]}%' if d["xc_range"][0] is not None else "n/a"
    cards.append(f"""<section class="card"><h2>{dn}</h2><div class="scores">
<div class="s" style="border-color:{color(d['xc'])}"><b>{f1(d['xc'])}%</b><span>XC</span><small>models {spread}</small></div>
<div class="s" style="border-color:{color(d['acro'])}"><b>{f1(d['acro'])}%</b><span>Acro</span><small>experimental</small></div></div>
{pulled(d['xc_rules'], 'XC')}{pulled(d['acro_rules'], 'Acro')}{wind_html(d['winds'])}
<table><tr><td>Launch wind</td><td>{k.get('wind_dir') or ''} {f1(k.get('wind_kmh'),' km/h')} (gust {f1(k.get('gust_kmh'))})</td></tr>
<tr><td>850 hPa wind</td><td>{k.get('wind850_dir') or ''} {f1(k.get('wind850_kmh'),' km/h')}</td></tr>
<tr><td>700 hPa wind</td><td>{f1(k.get('wind700_kmh'),' km/h')}</td></tr>
<tr><td>Cloud base / thermal top</td><td>{f1(k.get('ceiling_asl_m'),' m ASL')}</td></tr>
<tr><td>Wind shear to 850</td><td>{f1(k.get('shear_kmh'),' km/h')}</td></tr>
<tr><td>Thermals (est.)</td><td>{f1(k.get('thermal_ms'),' m/s')} (BL {f1(k.get('blh_m'),' m')})</td></tr>
<tr><td>CAPE / rain</td><td>{f1(k.get('cape'))} / {f1(k.get('rain_mm'))} mm</td></tr></table></section>""")
runs_txt = ', '.join(f"{ {'ecmwf_ifs025':'ECMWF','ncep_gfs025':'GFS','dwd_icon':'ICON'}[m] } {v['run_init_utc'][11:13]}Z" for m, v in runs.items()) or 'unknown'
rows = []
if logf.exists():
    seen = {}
    for r_ in csv.DictReader(logf.open()):
        if r_["target_date"] < today: seen[r_["target_date"]] = r_   # last prediction made for each past day
    rows = [f'<tr><td>{d}</td><td>{r_["xc_pct"]}%</td><td>{r_["acro_pct"]}%</td><td>{r_["lead_days"]} d</td></tr>' for d, r_ in sorted(seen.items())[-14:]]
page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bright flying forecast</title><style>
body{{font:15px system-ui,sans-serif;margin:0;padding:12px;background:#f4f6f8;color:#202124;max-width:720px;margin:auto}}
h1{{font-size:20px;margin:4px 0}} .upd{{color:#5f6368;font-size:13px;margin-bottom:12px}}
.card{{background:#fff;border-radius:10px;padding:12px;margin:10px 0;box-shadow:0 1px 3px #0002}} h2{{margin:0 0 8px;font-size:17px}}
.scores{{display:flex;gap:10px}} .s{{flex:1;border:3px solid;border-radius:10px;padding:8px;text-align:center}}
.s b{{font-size:28px;display:block}} .s span{{font-weight:600}} .s small{{display:block;color:#5f6368;font-size:11px}}
.why{{padding-left:18px;margin:8px 0}} table{{width:100%;border-collapse:collapse;font-size:13px}} td{{padding:3px 0;border-top:1px solid #eee}} td:first-child{{color:#5f6368;width:38%}}
.note{{font-size:12px;color:#5f6368}} h3{{font-size:14px;margin:12px 0 4px}} .wt th{{text-align:left;font-size:12px;color:#5f6368;font-weight:600}} .wt td small{{display:block;color:#5f6368;font-size:11px}} .wt td{{white-space:nowrap}} .ar{{font-size:16px;display:inline-block;width:1.1em}} .pd{{margin:8px 0 2px;font-size:13px}} .pd.ok{{color:#2e7d32}} .why li{{font-size:13px}}</style></head><body>
<h1>Mystic, Bright: flying forecast</h1><div class="upd">Updated {local_now.strftime('%a %d %b %H:%M %Z')} · window 11:00-16:00 · runs {runs_txt} · average of {len(out_days[0]['models']) if out_days else 0} models ({', '.join(out_days[0]['models']) if out_days else ''})</div>
{''.join(cards)}
<section class="card"><h2>Track record</h2>{'<table><tr><td>Day</td><td>XC</td><td>Acro</td><td>Lead</td></tr>'+''.join(rows)+'</table>' if rows else '<p class="note">Predictions are being logged daily. Outcomes will be compared here once flight data or pilot notes are added.</p>'}</section>
<p class="note">Scores come from hand-written rules, not yet learned from flights or calibrated. Each rule scores 0-100% (smoothly) and the day's score is set by the weakest rule, so the % beside a rule is how far it holds the score below 100%. Thermal strength is an estimate from sunshine and boundary-layer depth. Read it as a rough guide. Acro is experimental. Forecast data: Open-Meteo.</p></body></html>"""
(docs/"index.html").write_text(page)
print("ok", [(d["date"], d["xc"], d["acro"]) for d in out_days])
