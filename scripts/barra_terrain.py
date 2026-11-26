"""BARRA-C2 terrain height at each site point vs the elevation Open-Meteo uses for the same point -> data/forecast_error/elevations.json.
Downloads the 1.5 MB BARRA-C2 orography file once (CC BY 4.0, BOM / NCI). Usage: python scripts/barra_terrain.py [site_id]"""
import json, sys, time, requests, yaml, numpy as np, xarray as xr
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import forecast_error as FE
site = sys.argv[1] if len(sys.argv) > 1 else "bright"
cfg = yaml.safe_load((ROOT / "sites" / f"{site}.yaml").read_text())
OUT = ROOT / "data" / "forecast_error"; OUT.mkdir(parents=True, exist_ok=True)
f = OUT / "orog.nc"
if not f.exists():
    url = "https://thredds.nci.org.au/thredds/fileServer/ob53/output/reanalysis/AUST-04/BOM/ERA5/historical/hres/BARRA-C2/v1/fx/orog/latest/orog_AUST-04_ERA5_historical_hres_BOM_BARRA-C2_v1.nc"
    f.write_bytes(requests.get(url, timeout=120).content)
o = xr.open_dataset(f)["orog"]; lats, lons = o["lat"].values, o["lon"].values
out = {}
for k, p in cfg["points"].items():
    i = np.where((lats >= p["lat"] - 0.2) & (lats <= p["lat"] + 0.2))[0]; j = np.where((lons >= p["lon"] - 0.2) & (lons <= p["lon"] + 0.2))[0]
    z = float(FE.bilinear(lats[i.min():i.max() + 1], lons[j.min():j.max() + 1], o.values[i.min():i.max() + 1, j.min():j.max() + 1][None], p["lat"], p["lon"])[0])
    el = None
    for attempt in range(5):
        try:
            el = requests.get("https://historical-forecast-api.open-meteo.com/v1/forecast", timeout=60, params=dict(latitude=p["lat"], longitude=p["lon"],
                hourly="temperature_2m", models="ecmwf_ifs025", start_date="2025-01-01", end_date="2025-01-01")).json().get("elevation"); break
        except Exception: time.sleep(10)
    out[k] = dict(barra_m=round(z), openmeteo_m=el, diff_m=None if el is None else round(el - z)); print(k, out[k])
(OUT / "elevations.json").write_text(json.dumps(out, indent=1))
