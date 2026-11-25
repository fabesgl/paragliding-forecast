"""Fetch today's forecasts, archive the raw response, score the next 5 days, write docs/ (dashboard).
Usage: python scripts/daily_forecast.py [site_id]"""
import csv, datetime as dt, gzip, html, json, sys, time, requests, yaml
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast.rules import score_day
site = sys.argv[1] if len(sys.argv) > 1 else "bright"
cfg = yaml.safe_load((ROOT/"sites"/f"{site}.yaml").read_text())
tz = ZoneInfo(cfg["timezone"]); now = dt.datetime.now(dt.UTC); local_now = now.astimezone(tz)
MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "bom_access_global"]   # BOM ACCESS-G is skipped automatically while Open-Meteo returns no data for it
VARS = ["wind_speed_10m","wind_direction_10m","wind_gusts_10m","wind_speed_850hPa","wind_direction_850hPa",
 "wind_speed_700hPa","wind_direction_700hPa","temperature_2m","dew_point_2m","temperature_850hPa","temperature_700hPa",
 "cloud_cover_low","cloud_cover_mid","cloud_cover_high","cape","lifted_index","convective_inhibition",
 "boundary_layer_height","shortwave_radiation","precipitation","pressure_msl"]
keys = list(cfg["points"]); pts = cfg["points"]
for attempt in range(4):
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=90, params=dict(
        latitude=",".join(str(pts[k]["lat"]) for k in keys), longitude=",".join(str(pts[k]["lon"]) for k in keys),
        hourly=",".join(VARS), models=",".join(MODELS), forecast_days=7, timezone=cfg["timezone"], wind_speed_unit="kmh"))
    if r.status_code == 200: break
    time.sleep(30 * (attempt + 1))
r.raise_for_status()
raw = r.json()
# 1. archive the raw forecast (this is the real, honest forecast-by-lead record we train on later)
arch = ROOT/"forecast_archive"; arch.mkdir(exist_ok=True)
with gzip.open(arch/f"{now:%Y-%m-%dT%H%MZ}.json.gz", "wt") as f:
    json.dump({"fetched_utc": now.isoformat(), "points": keys, "response": raw}, f, separators=(",", ":"))
# 2. score Mystic for each model and day
mystic = raw[keys.index("mystic_launch")]["hourly"]
times = mystic["time"]; win = cfg.get("flying_window_local", ["11:00", "16:00"])
days = sorted({t[:10] for t in times})
today = local_now.date().isoformat()
out_days = []
for day in [d for d in days if d >= today][:5]:
    per_model = {}
    for m in MODELS:
        idx = [i for i, t in enumerate(times) if t[:10] == day and win[0] <= t[11:16] <= win[1]]
        h = {v: [mystic.get(f"{v}_{m}", [None]*len(times))[i] for i in idx] for v in VARS}
        if all(x is None for x in h["wind_speed_10m"]): continue
        per_model[m] = score_day(h, tuple(cfg["launch_wind_directions_deg"][0]), tuple(cfg["unflyable_synoptic_deg"][0]))
    def avg(k):
        v = [p[k] for p in per_model.values() if p[k] is not None]; return round(sum(v)/len(v)) if v else None
    best = per_model.get("gfs_seamless") or next(iter(per_model.values()), None)   # GFS has the most variables
    out_days.append(dict(date=day, xc=avg("xc"), acro=avg("acro"),
        xc_range=[min([p["xc"] for p in per_model.values() if p["xc"] is not None], default=None), max([p["xc"] for p in per_model.values() if p["xc"] is not None], default=None)],
        models={m: {"xc": p["xc"], "acro": p["acro"]} for m, p in per_model.items()},
        key=({**best["key"], "gust_kmh": max(best["key"]["gust_kmh"] or 0, best["key"]["wind_kmh"] or 0)} if best else {}), why=best["why"] if best else []))
# 3. log predictions (track record)
logf = ROOT/"predictions"/"log.csv"; logf.parent.mkdir(exist_ok=True)
new = not logf.exists()
with logf.open("a", newline="") as f:
    w = csv.writer(f)
    if new: w.writerow(["made_at_utc", "target_date", "lead_days", "xc_pct", "acro_pct", "method"])
    for d in out_days:
        lead = (dt.date.fromisoformat(d["date"]) - local_now.date()).days
        w.writerow([now.strftime("%Y-%m-%dT%H:%MZ"), d["date"], lead, d["xc"], d["acro"], "rules-v1"])
# 4. write the dashboard
docs = ROOT/"docs"; docs.mkdir(exist_ok=True)
(docs/"forecast.json").write_text(json.dumps(dict(updated_local=local_now.strftime("%a %d %b %Y %H:%M %Z"), days=out_days), indent=1))
def color(p): return "#9aa0a6" if p is None else "#c62828" if p < 25 else "#ef6c00" if p < 45 else "#f9a825" if p < 65 else "#2e7d32"
def f1(x, u=""): return "n/a" if x is None else f"{x:.0f}{u}"
cards = []
for d in out_days:
    k = d["key"]; dn = dt.date.fromisoformat(d["date"]).strftime("%a %d %b")
    spread = f'{d["xc_range"][0]}-{d["xc_range"][1]}%' if d["xc_range"][0] is not None else "n/a"
    cards.append(f"""<section class="card"><h2>{dn}</h2><div class="scores">
<div class="s" style="border-color:{color(d['xc'])}"><b>{f1(d['xc'])}%</b><span>XC</span><small>models {spread}</small></div>
<div class="s" style="border-color:{color(d['acro'])}"><b>{f1(d['acro'])}%</b><span>Acro</span><small>experimental</small></div></div>
<ul class="why">{''.join(f'<li>{html.escape(x)}</li>' for x in d['why'])}</ul>
<table><tr><td>Launch wind</td><td>{k.get('wind_dir') or ''} {f1(k.get('wind_kmh'),' km/h')} (gust {f1(k.get('gust_kmh'))})</td></tr>
<tr><td>850 hPa wind</td><td>{k.get('wind850_dir') or ''} {f1(k.get('wind850_kmh'),' km/h')}</td></tr>
<tr><td>700 hPa wind</td><td>{f1(k.get('wind700_kmh'),' km/h')}</td></tr>
<tr><td>Cloud base</td><td>{f1(k.get('cloudbase_agl_m'),' m AGL')}</td></tr>
<tr><td>Thermals</td><td>BL {f1(k.get('blh_m'),' m')} / sun {f1(k.get('sw_wm2'),' W/m²')}</td></tr>
<tr><td>CAPE / rain</td><td>{f1(k.get('cape'))} / {f1(k.get('rain_mm'))} mm</td></tr></table></section>""")
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
.note{{font-size:12px;color:#5f6368}}</style></head><body>
<h1>Mystic, Bright: flying forecast</h1><div class="upd">Updated {local_now.strftime('%a %d %b %H:%M %Z')} · window 11:00-16:00 · average of {len(out_days[0]['models']) if out_days else 0} models ({', '.join(out_days[0]['models']) if out_days else ''})</div>
{''.join(cards)}
<section class="card"><h2>Track record</h2>{'<table><tr><td>Day</td><td>XC</td><td>Acro</td><td>Lead</td></tr>'+''.join(rows)+'</table>' if rows else '<p class="note">Predictions are being logged daily. Outcomes will be compared here once flight data or pilot notes are added.</p>'}</section>
<p class="note">Scores come from simple hand-written rules (wind direction and strength, cloud base, thermals, rain), not yet learned from flights or calibrated, so read them as a rough guide. Acro is experimental. Forecast data: Open-Meteo.</p></body></html>"""
(docs/"index.html").write_text(page)
print("ok", [(d["date"], d["xc"], d["acro"]) for d in out_days])
