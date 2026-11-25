"""Check the valley-wind estimate against observations. For each finished day with no rain: the station's mean north wind over 12:00-16:00 local
versus the thermal strength the evening-before forecast gave for the same hours. Fits  north wind = k x thermal  and reports k.
It only REPORTS: the factor in sites/<site>/rules.yaml is never changed automatically.
Writes corrections/valley_wind_calibration.json. When 30 usable days exist for the first time, the GitHub job opens an issue to tell you.
Usage: python scripts/calibrate_valley_wind.py [site_id]"""
import csv, datetime as dt, gzip, json, os, sys, yaml
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import valley
from pgforecast.rules import score_day
from pgforecast.rules_config import load_rules

COMPASS = {c: i * 22.5 for i, c in enumerate(["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"])}
MODELS = ("gfs_seamless", "ecmwf_ifs025", "icon_seamless")
VARS = ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "wind_speed_900hPa", "wind_direction_900hPa", "wind_speed_850hPa", "wind_direction_850hPa", "wind_speed_800hPa",
        "wind_direction_800hPa", "wind_speed_700hPa", "temperature_2m", "dew_point_2m", "boundary_layer_height", "shortwave_radiation", "cape", "cloud_cover_low", "precipitation"]

def forecast_thermal(hourly, day, rules, launch_elev):
    """Mean over the models of the 12-16 local thermal estimate for `day`, from one archived forecast (cross-model fill like the dashboard does)."""
    times = hourly["time"]; idx = [i for i, t in enumerate(times) if t[:10] == day and "12:00" <= t[11:16] <= "16:00"]
    if not idx: return None
    vals = []
    for m in MODELS:
        h = {}
        for v in VARS:
            own = hourly.get(f"{v}_{m}") or [None] * len(times); oth = [hourly.get(f"{v}_{o}") or [None] * len(times) for o in MODELS if o != m]; col = []
            for i in idx:
                x = own[i]
                if x is None:
                    vs = [o[i] for o in oth if o[i] is not None]; x = sum(vs) / len(vs) if vs else None
                col.append(x)
            h[v] = col
        if all(x is None for x in h["wind_speed_10m"]): continue
        th = score_day(h, rules, launch_elev)["key"]["thermal_ms"]
        if th is not None: vals.append(th)
    return sum(vals) / len(vals) if vals else None

def run(arch_dir, obs_file, tz, rules, launch_elev, today=None):
    tzi = ZoneInfo(tz); today = today or dt.datetime.now(tzi).date()
    files = []
    for p in sorted(Path(arch_dir).glob("*.json.gz")):
        j = json.load(gzip.open(p, "rt")); files.append((dt.datetime.fromisoformat(j["fetched_utc"]), j))
    rows = list(csv.DictReader(open(obs_file))) if Path(obs_file).exists() else []
    by_day = {}
    for r in rows:
        t = dt.datetime.strptime(r["obs_time_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC).astimezone(tzi)
        if 12 <= t.hour < 16: by_day.setdefault(t.date(), []).append(r)
    pairs = []
    for D, rs in sorted(by_day.items()):
        if D >= today or len(rs) < 6: continue                                           # unfinished day, or too few observations
        try: wet = any(float(r["rain_trace"]) > 0 for r in rs if r.get("rain_trace") not in ("", "-", None))
        except ValueError: wet = False
        if wet: continue                                                                  # rain days say nothing about the thermic valley wind
        prior = [(t, j) for t, j in files if t.astimezone(tzi).date() == D - dt.timedelta(days=1)]
        if not prior: continue
        _, j = max(prior, key=lambda x: x[0])
        if "mystic_launch" not in j["points"]: continue
        th = forecast_thermal(j["response"][j["points"].index("mystic_launch")]["hourly"], D.isoformat(), rules, launch_elev)
        norths = []
        for r in rs:
            try: spd, dr = float(r["wind_spd_kmh"]), COMPASS.get(r["wind_dir"])
            except (ValueError, TypeError): continue
            if dr is not None: norths.append(valley.north_kmh(spd, dr))
        if th is not None and norths: pairs.append((th, sum(norths) / len(norths)))
    return valley.fit(pairs)

if __name__ == "__main__":
    site = sys.argv[1] if len(sys.argv) > 1 else "bright"
    OUT = ROOT if os.environ.get("GITHUB_ACTIONS") else ROOT / "data" / "local_out"
    cfg = yaml.safe_load((ROOT / "sites" / f"{site}.yaml").read_text()); rules = load_rules(site); vs = cfg.get("valley_wind_station") or {}
    if not vs.get("id"): sys.exit("No valley_wind_station set in the site file.")
    res = run(OUT / "forecast_archive", ROOT / "observations" / f"{vs['id']}.csv", cfg["timezone"], rules, cfg["launch_height_m"])
    cur = rules["direction"]["southerly_launch"]["valley_wind_kmh_per_thermal_ms"]
    res.update(station=vs["id"], station_note=vs.get("note", ""), current_setting=cur, min_days=valley.MIN_DAYS, updated=dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ"))
    f = OUT / "corrections" / "valley_wind_calibration.json"; f.parent.mkdir(parents=True, exist_ok=True)
    old = json.loads(f.read_text()) if f.exists() else {}; res["notified"] = bool(old.get("notified"))
    f.write_text(json.dumps(res, indent=1))
    print(f"valley wind calibration ({vs['id']}): {res['n_days']}/{valley.MIN_DAYS} usable days" + (f", k={res['k']} km/h per m/s (setting is {cur}), R2={res['r2']}" if res["k"] is not None else ""))
    if res["ready"] and not res["notified"] and os.environ.get("GITHUB_ACTIONS") and os.environ.get("GH_TOKEN"):     # tell the owner once, with a GitHub issue
        import subprocess
        body = (f"The valley-wind estimate can now be checked against observations.\n\n- Station: {vs['id']} {vs.get('note', '')}\n- Usable dry days: {res['n_days']}\n"
                f"- Observed afternoon north wind per 1 m/s of estimated thermal strength: **{res['k']} km/h** (the rule currently uses {cur})\n- R2: {res['r2']}\n"
                f"- By thermal strength: {json.dumps(res['by_thermal'])}\n\nNothing has been changed. To adopt it, edit direction.southerly_launch.valley_wind_kmh_per_thermal_ms in sites/{site}/rules.yaml.")
        r = subprocess.run(["gh", "issue", "create", "--title", "Valley wind calibration is ready", "--body", body], capture_output=True, text=True)
        if r.returncode == 0: res["notified"] = True; f.write_text(json.dumps(res, indent=1)); print("opened a GitHub issue")
        else: print("could not open an issue:", r.stderr[:200])
