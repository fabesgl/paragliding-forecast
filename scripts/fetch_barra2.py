"""Download small boxes of BARRA-C2 (BOM 4.4 km reanalysis) from NCI THREDDS via OPeNDAP.
Only the boxes we need are transferred (~1.5 GB total for 2008-01..2026-07, 20 variables).
Resumable: finished files in data/barra2/ are skipped. Usage: python scripts/fetch_barra2.py [site]
Licence: CC BY 4.0 - credit Bureau of Meteorology / NCI (doi:10.25914/jz7g-x858)."""
import sys, time, yaml, numpy as np, xarray as xr
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
site = sys.argv[1] if len(sys.argv) > 1 else "bright"
WORKER, NWORK = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (0, 1)   # split variables across parallel workers
cfg = yaml.safe_load((ROOT/"sites"/f"{site}.yaml").read_text())
OUT = ROOT/"data"/"barra2"; OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://thredds.nci.org.au/thredds/dodsC/ob53/output/reanalysis/AUST-04/BOM/ERA5/historical/hres/BARRA-C2/v1/1hr"
VARS = ["uas","vas","wsgs","ua850","va850","ua700","va700","tas","ta850","ta700","hurs",
        "cll","clm","clh","CAPE","CIN","zmla","rsds","pr","psl"]
VARS = VARS[WORKER::NWORK]
# boxes (lat_min, lat_max, lon_min, lon_max): one for the ranges near Bright, one patch per upwind point
BOXES = {"bright_area": (-37.05, -36.65, 146.70, 147.35),
         "upwind_nw": (cfg["points"]["upwind_nw"]["lat"]-0.07, cfg["points"]["upwind_nw"]["lat"]+0.07,
                       cfg["points"]["upwind_nw"]["lon"]-0.07, cfg["points"]["upwind_nw"]["lon"]+0.07),
         "upwind_sw": (cfg["points"]["upwind_sw"]["lat"]-0.07, cfg["points"]["upwind_sw"]["lat"]+0.07,
                       cfg["points"]["upwind_sw"]["lon"]-0.07, cfg["points"]["upwind_sw"]["lon"]+0.07)}
months = [(y, m) for y in range(2008, 2027) for m in range(1, 13) if (y, m) <= (2026, 7)]
def url(v, y, m):
    ym = f"{y}{m:02d}"
    return f"{BASE}/{v}/latest/{v}_AUST-04_ERA5_historical_hres_BOM_BARRA-C2_v1_1hr_{ym}-{ym}.nc"
idx = {}   # lat/lon index slices, worked out once from the grid
def slices(ds, box):
    la, lo = ds["lat"].values, ds["lon"].values
    i = np.where((la >= box[0]) & (la <= box[1]))[0]; j = np.where((lo >= box[2]) & (lo <= box[3]))[0]
    return slice(i.min(), i.max()+1), slice(j.min(), j.max()+1)
done = skipped = 0; total = len(months)*len(VARS)*len(BOXES)
for (y, m) in months:
    for v in VARS:
        todo = [b for b in BOXES if not (OUT/f"{b}__{v}__{y}{m:02d}.nc").exists()]
        if not todo: skipped += len(BOXES); continue
        for attempt in range(5):
            try:
                ds = xr.open_dataset(url(v, y, m), engine="netcdf4", decode_times=True)
                for b in todo:
                    if b not in idx: idx[b] = slices(ds, BOXES[b])
                    sub = ds[v].isel(lat=idx[b][0], lon=idx[b][1]).load()
                    tmp = OUT/f"{b}__{v}__{y}{m:02d}.tmp"
                    sub.astype("float32").to_netcdf(tmp, engine="netcdf4", encoding={v: {"zlib": True, "complevel": 4}})
                    tmp.rename(OUT/f"{b}__{v}__{y}{m:02d}.nc"); done += 1
                ds.close(); break
            except Exception as e:
                print(f"retry {v} {y}-{m:02d} ({type(e).__name__}: {str(e)[:100]})", flush=True)
                time.sleep(20*(attempt+1))
        else:
            print(f"GAVE UP {v} {y}-{m:02d}", flush=True)
        time.sleep(1)
    print(f"worker {WORKER}: {y}-{m:02d} done  ({done+skipped}/{total} files)", flush=True)
print("ALL DONE")
