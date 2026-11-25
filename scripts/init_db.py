"""Create the SQLite DB and load/verify a site's points. Usage: python scripts/init_db.py bright"""
import sqlite3, sys, requests, yaml
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
site_id = sys.argv[1] if len(sys.argv) > 1 else "bright"
cfg = yaml.safe_load((ROOT / "sites" / f"{site_id}.yaml").read_text())
pts = cfg["points"]
r = requests.get("https://api.open-meteo.com/v1/elevation", timeout=30, params={
    "latitude": ",".join(str(p["lat"]) for p in pts.values()),
    "longitude": ",".join(str(p["lon"]) for p in pts.values())})
r.raise_for_status()
elev = dict(zip(pts, r.json()["elevation"]))
db = sqlite3.connect(ROOT / "data" / "forecast.sqlite")
db.executescript((ROOT / "src/pgforecast/schema.sql").read_text())
db.execute("INSERT OR REPLACE INTO site VALUES (?,?,?)", (cfg["id"], cfg["name"], cfg["timezone"]))
for k, p in pts.items():
    db.execute("INSERT OR REPLACE INTO point VALUES (?,?,?,?,?,?,?)",
               (cfg["id"], k, p["lat"], p["lon"], elev[k], p["role"], p["status"]))
    print(f'{k:15s} {p["lat"]:8.3f} {p["lon"]:8.3f} {elev[k]:6.0f} m  [{p["status"]}]')
db.commit()
