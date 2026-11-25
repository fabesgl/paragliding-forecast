"""Download past forecasts from Open-Meteo into data/forecast.sqlite (table fc). Resumable:
re-run any time; finished chunks are skipped. Stops cleanly if the daily free limit is hit.
Usage: python scripts/fetch_forecasts.py [site_id] [--until YYYY-MM-DD]"""
import sqlite3, sys, time, datetime as dt, requests, yaml
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
site = next((a for a in sys.argv[1:] if not a.startswith("-") and not a[0].isdigit()), "bright")
until = dt.date.fromisoformat(sys.argv[sys.argv.index("--until")+1]) if "--until" in sys.argv else dt.date.today() - dt.timedelta(days=3)
cfg = yaml.safe_load((ROOT/"sites"/f"{site}.yaml").read_text())
db = sqlite3.connect(ROOT/"data"/"forecast.sqlite"); db.executescript((ROOT/"src/pgforecast/schema.sql").read_text())
VARS = ["wind_speed_10m","wind_direction_10m","wind_gusts_10m","wind_speed_850hPa","wind_direction_850hPa",
 "wind_speed_700hPa","wind_direction_700hPa","temperature_2m","dew_point_2m","temperature_850hPa","temperature_700hPa",
 "cloud_cover_low","cloud_cover_mid","cloud_cover_high","cape","lifted_index","convective_inhibition",
 "boundary_layer_height","shortwave_radiation","precipitation","pressure_msl"]
PREV_VARS = ["wind_speed_10m","wind_direction_10m","wind_gusts_10m","temperature_2m","dew_point_2m",
 "cape","shortwave_radiation","precipitation","pressure_msl"]   # the only ones Previous Runs offers here
# (source, lead, model, first available date) - found by probing, see PHASE3_NOTES.md
JOBS = [("hist",0,"ecmwf_ifs","2017-01-03"),("hist",0,"gfs_seamless","2021-03-23"),
        ("hist",0,"icon_seamless","2022-11-16"),("hist",0,"bom_access_global","2024-01-03"),
        ("hist",0,"ecmwf_ifs025","2024-02-03"),
        ("prev",1,"gfs_seamless","2021-03-27"),("prev",2,"gfs_seamless","2021-03-27"),
        ("prev",1,"bom_access_global","2024-01-23"),("prev",2,"bom_access_global","2024-01-23"),
        ("prev",1,"ecmwf_ifs025","2024-02-07"),("prev",2,"ecmwf_ifs025","2024-02-07")]
URL = {"hist":"https://historical-forecast-api.open-meteo.com/v1/forecast",
       "prev":"https://previous-runs-api.open-meteo.com/v1/forecast"}
CHUNK = 90
def fetch(source, lead, model, pt, a, b):
    vs = VARS if source=="hist" else PREV_VARS
    names = vs if source=="hist" else [f"{v}_previous_day{lead}" for v in vs]
    for attempt in range(6):
        try:
            r = requests.get(URL[source], timeout=90, params=dict(latitude=pt["lat"], longitude=pt["lon"],
                hourly=",".join(names), models=model, start_date=a, end_date=b, timezone="UTC", wind_speed_unit="kmh"))
        except requests.RequestException:
            time.sleep(10*(attempt+1)); continue
        if r.status_code == 200: return vs, names, r.json()["hourly"]
        reason = r.text[:200]
        if r.status_code == 429:
            if "Daily" in reason or "daily" in reason:
                print("DAILY LIMIT reached - re-run tomorrow, progress is saved."); sys.exit(0)
            print("rate limited, waiting 5 min"); time.sleep(300); continue
        print("HTTP", r.status_code, reason); return None
    print("giving up on chunk"); return None
for source, lead, model, first in JOBS:
    for key, pt in cfg["points"].items():
        d = max(dt.date.fromisoformat(first), dt.date(2017,1,1))
        while d <= until:
            e = min(d + dt.timedelta(days=CHUNK-1), until)
            done = db.execute("SELECT 1 FROM fetch_log WHERE source=? AND lead_days=? AND point_key=? AND model=? AND start_date=?",
                              (source,lead,key,model,d.isoformat())).fetchone()
            if not done:
                res = fetch(source, lead, model, pt, d.isoformat(), e.isoformat())
                if res:
                    vs, names, h = res; n = 0
                    for i, t in enumerate(h["time"]):
                        vals = [h[nm][i] for nm in names]
                        if all(v is None for v in vals): continue
                        db.execute(f"INSERT OR REPLACE INTO fc (source,lead_days,point_key,model,time_utc,{','.join(vs)}) VALUES (?,?,?,?,?,{','.join('?'*len(vs))})",
                                   (source,lead,key,model,t,*vals)); n += 1
                    db.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?,?,?,?)",
                               (source,lead,key,model,d.isoformat(),e.isoformat(),dt.datetime.now(dt.UTC).isoformat(),n))
                    db.commit()
                    print(f"{source} d{lead} {model:18s} {key:14s} {d}..{e} {n} rows", flush=True)
                    time.sleep(1.5)
            d = e + dt.timedelta(days=1)
print("ALL DONE")
