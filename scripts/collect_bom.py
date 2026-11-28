"""Save BOM half-hourly observations to observations/<station>.csv (append, de-duplicated). Run by the GitHub job; on the laptop it writes to data/local_out/observations/ instead.
Usage: python scripts/collect_bom.py [site_id]. Safe to re-run any time."""
import csv, os, sys, time, requests, yaml
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
site = sys.argv[1] if len(sys.argv) > 1 else "bright"
stations = yaml.safe_load((ROOT / "sites" / f"{site}.yaml").read_text())["bom_stations"]
OUT = (ROOT if os.environ.get("GITHUB_ACTIONS") else ROOT / "data" / "local_out") / "observations"; OUT.mkdir(parents=True, exist_ok=True)      # only the GitHub job writes the committed observations/
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
COLS = ["obs_time_utc", "local_time", "air_temp", "apparent_t", "dewpt", "rel_hum", "wind_dir",
        "wind_spd_kmh", "gust_kmh", "press_qnh", "press_msl", "rain_trace", "cloud", "vis_km"]
failed = []
for sid, name in stations.items():
    url = f"https://www.bom.gov.au/fwo/IDV60801/IDV60801.{sid}.json"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30); r.raise_for_status()
        data = r.json()["observations"]["data"]
    except Exception as e:
        print(f"FAIL {sid} {name}: {e}"); failed.append(sid); continue
    f = OUT / f"{sid}.csv"
    rows = {}
    if f.exists():
        rows = {r["obs_time_utc"]: r for r in csv.DictReader(f.open())}
    new = 0
    for d in data:
        t = d["aifstime_utc"]  # YYYYMMDDHHMMSS
        iso = f"{t[:4]}-{t[4:6]}-{t[6:8]}T{t[8:10]}:{t[10:12]}:00Z"
        if iso not in rows: new += 1
        rows[iso] = {"obs_time_utc": iso, "local_time": d["local_date_time_full"], "air_temp": d["air_temp"],
            "apparent_t": d["apparent_t"], "dewpt": d["dewpt"], "rel_hum": d["rel_hum"], "wind_dir": d["wind_dir"],
            "wind_spd_kmh": d["wind_spd_kmh"], "gust_kmh": d["gust_kmh"], "press_qnh": d["press_qnh"],
            "press_msl": d["press_msl"], "rain_trace": d["rain_trace"], "cloud": d["cloud"], "vis_km": d["vis_km"]}
    with f.open("w", newline="") as fh:
        w = csv.DictWriter(fh, COLS); w.writeheader()
        for k in sorted(rows): w.writerow(rows[k])
    print(f"ok   {sid} {name}: +{new} new, {len(rows)} total")
    time.sleep(2)
if failed: sys.exit(1)
