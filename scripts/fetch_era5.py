"""ERA5 raw-data collector: downloads and verifies raw monthly netCDF files. Nothing else (no tables, no weather types).

Area N -20, W 110, S -50, E 160 (0.25 deg grid, 121 x 201 points), every 3 h (00..21 UTC), 2008-01 to the latest available day.
  single levels   : msl (mean sea level pressure), tcwv (total column water vapour)
  pressure levels : z 500 hPa; u, v, t 850 hPa; u, v 700 hPa
CDS cannot pick single variable/level pairs, so one pressure-levels request asks for z, u, v, t at 500, 700 and 850 hPa
(12 fields, 6 of them unused extras). That keeps it at one request per dataset per month.

Up to 3 requests at once (3 worker threads). Order: Jan 2008 pressure, all single-levels months, then pressure months. Each file is downloaded to <name>.part, verified, then renamed. Resumable: months already verified
in the manifest are skipped. Files: data/raw/era5/era5_single_YYYY_MM.nc, era5_pressure_YYYY_MM.nc, manifest.csv, fetch.log.
The current month is partial (data up to ~6 days ago, ERA5T is preliminary) and is re-downloaded on a later run when more days exist.
Months with preliminary ERA5T data (expver 5) are marked in the manifest; `--refresh-preliminary` re-downloads them (when final).
The CDS key is read by cdsapi from ~/.cdsapirc; this script never prints or copies it. Needs the ERA5 licence accepted on the CDS website.

Progress: data/raw/era5/progress.log. Exit codes: 2 = stopped after repeated failures (do not restart), 3 = some months failed (retry later).
Run as the systemd user service era5.service (see CLAUDE.md).
Usage: python scripts/fetch_era5.py [--from YYYY-MM] [--to YYYY-MM] [--refresh-preliminary] [--estimate]"""
import argparse, calendar, csv, datetime as dt, fcntl, logging, os, shutil, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "era5"
MANIFEST = OUT / "manifest.csv"
AREA = [-20, 110, -50, 160]                      # N, W, S, E (CDS order)
NLAT, NLON = 121, 201
HOURS = [f"{h:02d}:00" for h in range(0, 24, 3)]
LATEST_LAG_DAYS = 6                              # ERA5T is ~5 days behind
FIRST_MONTH = (2008, 1)
MAX_CONSECUTIVE_FAILS = 6                        # likely auth / licence / outage: stop instead of burning through every month
DATASETS = {
    "single": {"name": "reanalysis-era5-single-levels", "vars": ["msl", "tcwv"], "levels": None, "fields": 2,
               "request": {"variable": ["mean_sea_level_pressure", "total_column_water_vapour"]}},
    "pressure": {"name": "reanalysis-era5-pressure-levels", "vars": ["z", "u", "v", "t"], "levels": [500, 700, 850], "fields": 12,
                 "request": {"variable": ["geopotential", "u_component_of_wind", "v_component_of_wind", "temperature"],
                             "pressure_level": ["500", "700", "850"]}},
}
COLS = ["file", "dataset", "month", "size_bytes", "time_steps", "verified", "downloaded_at", "download_seconds",
        "preliminary_expver5", "note"]
log = logging.getLogger("era5")
prog = logging.getLogger("era5.progress")
WORKERS = 3                                      # requests queued at CDS at once


def fname(key, y, m): return f"era5_{key}_{y}_{m:02d}.nc"


def end_day(y, m, today=None):
    """Last day of the month we can ask for (0 = none yet)."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    last = dt.date(y, m, calendar.monthrange(y, m)[1])
    cut = today - dt.timedelta(days=LATEST_LAG_DAYS)
    if cut < dt.date(y, m, 1): return 0
    return min(last, cut).day


def month_list(first, last):
    out, (y, m) = [], first
    while (y, m) <= last:
        out.append((y, m)); y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def parse_ym(s):
    y, m = s.split("-"); return int(y), int(m)


def read_manifest():
    if not MANIFEST.exists(): return {}
    with open(MANIFEST, newline="") as f:
        return {r["file"]: r for r in csv.DictReader(f)}


def write_manifest(rows):
    tmp = MANIFEST.with_suffix(".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader()
        for k in sorted(rows): w.writerow({c: rows[k].get(c, "") for c in COLS})
    os.replace(tmp, MANIFEST)


def verify(path, key, y, m, nd):
    """Return (ok, info dict, problem text). Opens the file, checks variables, levels, grid, area and every time step."""
    import xarray as xr
    spec = DATASETS[key]
    info = {}
    try:
        ds = xr.open_dataset(path, engine="netcdf4", decode_times=True)
    except Exception as e:
        return False, info, f"does not open: {type(e).__name__}"
    try:
        probs = []
        tname = "valid_time" if "valid_time" in ds.coords else "time"
        lat = ds["latitude"].values; lon = ds["longitude"].values
        times = ds[tname].values
        info.update(variables=[v for v in ds.data_vars if v != "expver"], nlat=len(lat), nlon=len(lon),
                    lat=(float(lat.min()), float(lat.max())), lon=(float(lon.min()), float(lon.max())), steps=len(times))
        for v in spec["vars"]:
            if v not in ds.data_vars: probs.append(f"missing variable {v}")
        if spec["levels"]:
            lv = sorted(int(x) for x in ds["pressure_level"].values)
            info["levels"] = lv
            if lv != spec["levels"]: probs.append(f"levels {lv}")
        if (len(lat), len(lon)) != (NLAT, NLON): probs.append(f"grid {len(lat)}x{len(lon)} not {NLAT}x{NLON}")
        if not (abs(lat.max() - AREA[0]) < 1e-3 and abs(lat.min() - AREA[2]) < 1e-3
                and abs(lon.min() - AREA[1]) < 1e-3 and abs(lon.max() - AREA[3]) < 1e-3):
            probs.append("area not fully covered")
        want = np.array([np.datetime64(dt.datetime(y, m, d, h)) for d in range(1, nd + 1) for h in range(0, 24, 3)], "datetime64[ns]")
        got = np.sort(np.unique(times.astype("datetime64[ns]")))
        if len(times) != len(want): probs.append(f"{len(times)} time steps, expected {len(want)}")
        elif not np.array_equal(got, want): probs.append("time steps are not the expected ones")
        # preliminary data (expver 5)
        prelim = False
        if "expver" in ds.variables:
            ev = ds["expver"].values
            labels = {str(x).strip().lstrip("0") or "0" for x in np.atleast_1d(ev)}
            labels = {x.decode().lstrip("0") if isinstance(x, bytes) else x for x in labels}
            prelim = "5" in labels
        info["preliminary"] = prelim
        # data present (not all missing) at first, middle and last step for every variable
        if not probs:
            for v in spec["vars"]:
                da = ds[v]
                if "expver" in da.dims: da = da.max("expver")
                for i in sorted({0, len(times) // 2, len(times) - 1}):
                    if bool(da.isel({tname: i}).isnull().any()): probs.append(f"{v} has missing values at step {i}"); break
        return (not probs), info, "; ".join(probs)
    except Exception as e:
        return False, info, f"verify error: {type(e).__name__}: {e}"
    finally:
        ds.close()


def download(client, key, y, m, nd, target):
    spec = DATASETS[key]
    req = {"product_type": ["reanalysis"], "year": [str(y)], "month": [f"{m:02d}"], "day": [f"{d:02d}" for d in range(1, nd + 1)],
           "time": HOURS, "area": AREA, "data_format": "netcdf", "download_format": "unarchived", **spec["request"]}
    client.retrieve(spec["name"], req).download(str(target))


def per_month_estimate(rows):
    """Bytes for one full month (30.4 days): from verified files if any, else 2 bytes per value (packed)."""
    steps = 30.4 * 8
    tot = 0
    for key, spec in DATASETS.items():
        have = [(int(r["size_bytes"]), int(r["time_steps"])) for r in rows.values()
                if r["dataset"] == key and r["verified"] == "yes" and r["time_steps"] and int(r["time_steps"]) >= 200]
        per_step = np.mean([s / n for s, n in have]) if have else NLAT * NLON * spec["fields"] * 2
        tot += per_step * steps
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="first", default="2008-01"); ap.add_argument("--to", dest="last", default=None)
    ap.add_argument("--refresh-preliminary", action="store_true"); ap.add_argument("--estimate", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(OUT / "fetch.log")])
    logging.getLogger("cdsapi").setLevel(logging.WARNING)
    ph = logging.FileHandler(OUT / "progress.log"); ph.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    prog.addHandler(ph)                       # progress.log: one line per file; fetch.log keeps the detail
    lock = open(OUT / ".lock", "w")
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: sys.exit("another fetch_era5.py is already running")

    today = dt.datetime.now(dt.timezone.utc).date()
    latest = today - dt.timedelta(days=LATEST_LAG_DAYS)
    first = parse_ym(a.first); last = parse_ym(a.last) if a.last else (latest.year, latest.month)
    months = [ym for ym in month_list(first, last) if end_day(*ym) > 0]
    rows = read_manifest()

    def wanted(key, y, m):
        r = rows.get(fname(key, y, m)); nd = end_day(y, m)
        if not r or r["verified"] != "yes" or not (OUT / fname(key, y, m)).exists(): return True
        if int(r["time_steps"]) < nd * 8: return True                       # partial month has grown
        return a.refresh_preliminary and r["preliminary_expver5"] == "yes"
    todo = [(y, m, k) for (y, m) in months for k in DATASETS if wanted(k, y, m)]
    full_equiv = sum(end_day(y, m) / calendar.monthrange(y, m)[1] / 2 for (y, m, k) in todo)   # single+pressure counted as one month
    est = per_month_estimate(rows) * full_equiv
    free = shutil.disk_usage(OUT).free
    log.info("months %d-%02d to %d-%02d (to %s): %d files to download; estimated %.1f GB; free disk %.1f GB",
             *first, *last, latest, len(todo), est / 1e9, free / 1e9)
    if a.estimate: return
    if free < 2 * est:
        sys.exit(f"STOP: free disk {free/1e9:.1f} GB is under twice the estimate ({est/1e9:.1f} GB)")

    import cdsapi
    # order: January 2008 pressure first (the test file), then every single-levels month, then the other pressure months
    todo.sort(key=lambda t: (0 if (t[0], t[1], t[2]) == (2008, 1, "pressure") else 1 if t[2] == "single" else 2, t[0], t[1]))
    state = {"fails": 0, "done": 0, "stop": False, "t0": time.time(), "bytes": 0}
    mlock = threading.Lock()
    tl = threading.local()
    total = len(todo)

    def one(task):
        y, m, key = task
        if state["stop"]: return
        if not hasattr(tl, "client"): tl.client = cdsapi.Client(progress=False)   # key comes from ~/.cdsapirc; never printed
        client = tl.client
        nd = end_day(y, m); name = fname(key, y, m); final = OUT / name; part = OUT / (name + ".part")
        ok = False; problem = ""; info = {}; secs = 0.0
        # a finished file that never got into the manifest (crash between rename and write): verify instead of re-downloading
        if final.exists() and name not in rows:
            ok, info, problem = verify(final, key, y, m, nd)
        for attempt in (1, 2):
            if ok or state["stop"]: break
            part.unlink(missing_ok=True)
            t0 = time.time()
            try:
                download(client, key, y, m, nd, part)
                secs = time.time() - t0
                ok, info, problem = verify(part, key, y, m, nd)
            except Exception as e:
                ok, problem = False, f"download error: {type(e).__name__}: {str(e)[:200]}"
            if ok:
                os.replace(part, final)
            else:
                part.unlink(missing_ok=True)
                log.info("%s attempt %d failed: %s", name, attempt, problem)
        with mlock:
            state["done"] += 1
            if ok:
                state["fails"] = 0; state["bytes"] += final.stat().st_size
                rows[name] = {"file": name, "dataset": DATASETS[key]["name"], "month": f"{y}-{m:02d}", "size_bytes": final.stat().st_size,
                              "time_steps": info["steps"], "verified": "yes", "downloaded_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "download_seconds": round(secs), "preliminary_expver5": "yes" if info["preliminary"] else "no",
                              "note": "partial month" if nd < calendar.monthrange(y, m)[1] else ""}
                msg = (f"[{state['done']}/{total}] {name} ok: {final.stat().st_size/1e6:.1f} MB, {info['steps']} steps, grid {info['nlat']}x{info['nlon']}, "
                       f"vars {','.join(info['variables'])}, prelim={info['preliminary']}, {secs:.0f}s")
                el = time.time() - state["t0"]
                eta = el / state["done"] * (total - state["done"]) / 3600
                prog.info("%s | %.1f GB so far | elapsed %.1f h, ETA ~%.1f h", msg, state["bytes"] / 1e9, el / 3600, eta)
            elif not state["stop"]:
                state["fails"] += 1
                if name not in rows or rows[name]["verified"] != "yes":      # a good older file (partial month) stays as it is
                    final.unlink(missing_ok=True)
                    rows[name] = {"file": name, "dataset": DATASETS[key]["name"], "month": f"{y}-{m:02d}", "size_bytes": "", "time_steps": "",
                                  "verified": "no", "downloaded_at": "", "download_seconds": "", "preliminary_expver5": "", "note": problem[:200]}
                prog.info("[%d/%d] %s FAILED twice, moving on: %s", state["done"], total, name, problem)
                if state["fails"] >= MAX_CONSECUTIVE_FAILS:
                    state["stop"] = True
                    prog.info("STOP: %d failures in a row (key, licence or CDS problem?). Fix and rerun; finished months are skipped.", state["fails"])
            write_manifest(rows)

    prog.info("start: %d files, up to %d requests queued at CDS at once", total, WORKERS)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(one, todo))
    nf = sum(r["verified"] == "no" for r in rows.values())
    prog.info("finished: %d verified, %d failed", sum(r["verified"] == "yes" for r in rows.values()), nf)
    if state["stop"]: sys.exit(2)          # systemd: do not restart (needs a fix)
    if nf: sys.exit(3)                     # systemd: try the failed months again later


if __name__ == "__main__":
    main()
