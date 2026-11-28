"""ERA5 raw-data collector: downloads and verifies raw monthly netCDF files. Nothing else (no tables, no weather types).

Area N -20, W 110, S -50, E 160 (0.25 deg grid, 121 x 201 points), every 3 h (00..21 UTC), 2008-01 to the latest available day.
  single levels   : msl (mean sea level pressure), tcwv (total column water vapour)
  pressure levels : z 500 hPa; u, v, t 850 hPa; u, v 700 hPa
CDS cannot pick single variable/level pairs, so one pressure-levels request asks for z, u, v, t at 500, 700 and 850 hPa
(12 fields, 6 of them unused extras). That keeps it at one request per dataset per month.

One request at a time (CDS rejects a second queued request, even for another dataset); CDS job ids are kept in jobs.json so a restart re-attaches to a queued job. Order: all single-levels months, then pressure months. Each file is downloaded to <name>.part, verified, then renamed. Resumable: months already verified
in the manifest are skipped. Files: data/raw/era5/era5_single_YYYY_MM.nc, era5_pressure_YYYY_MM.nc, manifest.csv, fetch.log.
The current month is partial (data up to ~6 days ago, ERA5T is preliminary) and is re-downloaded on a later run when more days exist.
Months with preliminary ERA5T data (expver 5) are marked in the manifest; `--refresh-preliminary` re-downloads them (when final).
The CDS key is read by cdsapi from ~/.cdsapirc; this script never prints or copies it. Needs the ERA5 licence accepted on the CDS website.

Progress: data/raw/era5/progress.log. Exit codes: 2 = stopped after repeated failures (do not restart), 3 = some months failed (retry later).
Run as the systemd user service era5.service (see CLAUDE.md).
Usage: python scripts/fetch_era5.py [--from YYYY-MM] [--to YYYY-MM] [--refresh-preliminary] [--estimate]"""
import argparse, calendar, csv, datetime as dt, fcntl, json, logging, os, shutil, sys, threading, time
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
WORKERS = 1                                      # CDS allows one queued request at a time


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


JOBS = OUT / "jobs.json"                         # file name -> CDS job id, so a restart re-attaches to a queued job instead of re-queuing
JOB_LOCK = threading.Lock()
CDS_LOCK = threading.Lock()                      # CDS rejects a second queued request ("request sequentially"): one at a time, any dataset


def jobs_get(name):
    with JOB_LOCK:
        return json.loads(JOBS.read_text()).get(name) if JOBS.exists() else None


def jobs_set(name, job_id):
    with JOB_LOCK:
        d = json.loads(JOBS.read_text()) if JOBS.exists() else {}
        if job_id is None: d.pop(name, None)
        else: d[name] = job_id
        JOBS.write_text(json.dumps(d, indent=1))


GROUP_MAX = 3                                    # pressure months per CDS request (size limit 60000: 1 month ~18000, 3 months ~52000)


def download(client, key, months, target, jobname):
    """Submit (or re-attach to the stored job), wait for it, download to target. months = [(y, m, nd), ...] all in one year.
    Queue-full answers wait and retry."""
    spec = DATASETS[key]
    days = range(1, 32) if len(months) > 1 else range(1, months[0][2] + 1)      # CDS ignores dates that do not exist
    req = {"product_type": ["reanalysis"], "year": [str(months[0][0])], "month": [f"{m:02d}" for _, m, _ in months],
           "day": [f"{d:02d}" for d in days], "time": HOURS, "area": AREA, "data_format": "netcdf", "download_format": "unarchived",
           **spec["request"]}
    c = client.client
    with CDS_LOCK:
        while True:
            remote = None
            jid = jobs_get(jobname)
            if jid:
                try:
                    remote = c.get_remote(jid)
                    if remote.status in ("failed", "rejected", "deleted", "dismissed"): remote = None
                except Exception:
                    remote = None
            if remote is None:
                remote = c.submit(collection_id=spec["name"], request=req)
                jobs_set(jobname, remote.request_id)
            try:
                results = remote.get_results()           # waits in the CDS queue until done
                results.download(str(target))
                jobs_set(jobname, None)
                return
            except Exception as e:
                msg = str(e) + " " + repr(getattr(e, "args", ""))
                if remote.status in ("failed", "rejected", "deleted", "dismissed"): jobs_set(jobname, None)
                if "queued requests" in msg:
                    log.info("%s: CDS queue is full, waiting 5 min", jobname); time.sleep(300); continue
                raise


def split_months(src, months, parts):
    """Cut a multi-month file into one file per month (raw values unchanged, same float32 + zlib). parts: {(y, m): path}."""
    import xarray as xr
    dd = xr.open_dataset(src, engine="netcdf4", decode_times=True)
    tname = "valid_time" if "valid_time" in dd.coords else "time"
    t = dd[tname].values.astype("datetime64[M]"); dd.close()
    raw = xr.open_dataset(src, engine="netcdf4", decode_times=False)
    try:
        for (y, m, _) in months:
            idx = np.where(t == np.datetime64(f"{y}-{m:02d}", "M"))[0]
            if len(idx) == 0: raise ValueError(f"no data for {y}-{m:02d} in the download")
            sub = raw.isel({tname: idx})
            enc = {v: {"zlib": True, "shuffle": True, "complevel": 1} for v in sub.data_vars}
            sub.to_netcdf(parts[(y, m)], engine="netcdf4", encoding=enc)
    finally:
        raw.close()


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
    # tasks: (key, [(y, m, nd), ...]). Single levels: one month per request, all of them first (about 90 s each).
    # Pressure: up to GROUP_MAX full months of the same year per request (fewer queue waits); a partial month goes alone.
    tasks = [("single", [(y, m, end_day(y, m))]) for (y, m, k) in sorted(todo) if k == "single"]
    pm = sorted((y, m) for (y, m, k) in todo if k == "pressure")
    full = [(y, m) for (y, m) in pm if end_day(y, m) == calendar.monthrange(y, m)[1]]
    for (y, m) in pm:
        if (y, m) not in full: tasks.append(("pressure", [(y, m, end_day(y, m))]))
    for y in sorted({y for y, _ in full}):
        ms = [m for (yy, m) in full if yy == y]
        for k in range(0, len(ms), GROUP_MAX):
            tasks.append(("pressure", [(y, m, end_day(y, m)) for m in ms[k:k + GROUP_MAX]]))
    tasks.sort(key=lambda t: (0 if t[0] == "single" else 1, t[1][0][:2]))
    state = {"fails": 0, "done": 0, "stop": False, "t0": time.time(), "bytes": 0}
    mlock = threading.Lock()
    tl = threading.local()
    total = len(todo)

    def record(key, y, m, nd, final, info, secs, ok, problem):
        """Manifest row + progress line for one month file. Caller holds mlock."""
        name = fname(key, y, m)
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

    def fetch(key, months):
        """Download months (one request), split, verify each month. Returns {(y, m): (ok, info, problem)} and seconds."""
        tag = f"{months[0][0]}_{months[0][1]:02d}" + (f"-{months[-1][1]:02d}" if len(months) > 1 else "")
        jobname = f"era5_{key}_{tag}"
        big = OUT / (jobname + ".part")
        parts = {(y, m): OUT / (fname(key, y, m) + ".part") for (y, m, _) in months}
        res = {}; secs = 0.0
        t0 = time.time()
        download(tl.client, key, months, big, jobname)
        secs = time.time() - t0
        if len(months) == 1:
            os.replace(big, parts[months[0][:2]])
        else:
            split_months(big, months, parts); big.unlink(missing_ok=True)
        for (y, m, nd) in months:
            res[(y, m)] = verify(parts[(y, m)], key, y, m, nd)
        return res, secs, parts

    def one(task):
        key, months = task
        if state["stop"]: return
        if not hasattr(tl, "client"): tl.client = cdsapi.Client(progress=False)   # key comes from ~/.cdsapirc; never printed
        # month files that finished but never got into the manifest (crash between rename and write): verify instead of re-downloading
        left = []
        for (y, m, nd) in months:
            final = OUT / fname(key, y, m)
            if final.exists() and fname(key, y, m) not in rows:
                ok, info, problem = verify(final, key, y, m, nd)
                if ok:
                    with mlock: record(key, y, m, nd, final, info, 0.0, True, "")
                    continue
            left.append((y, m, nd))
        months = left
        groups = [months] if months else []
        for attempt in (1, 2):
            if not groups or state["stop"]: break
            next_groups = []
            for grp in groups:
                try:
                    res, secs, parts = fetch(key, grp)
                except Exception as e:
                    res, secs, parts = {}, 0.0, {}
                    problem = f"download error: {type(e).__name__}: {str(e)[:200]}"
                    log.info("%s attempt %d failed: %s", fname(key, grp[0][0], grp[0][1]), attempt, problem)
                    for (y, m, nd) in grp: res[(y, m)] = (False, {}, problem)
                bad = []
                for (y, m, nd) in grp:
                    ok, info, problem = res[(y, m)]; final = OUT / fname(key, y, m); part = parts.get((y, m))
                    if ok:
                        os.replace(part, final)
                        with mlock: record(key, y, m, nd, final, info, secs / len(grp), True, "")
                    else:
                        if part is not None: part.unlink(missing_ok=True)
                        log.info("%s attempt %d failed: %s", fname(key, y, m), attempt, problem)
                        bad.append((y, m, nd, problem))
                if bad:
                    if len(grp) > 1 and attempt == 1:
                        next_groups += [[(y, m, nd)] for (y, m, nd, _) in bad]     # a batched request failed: try its months one by one
                    elif attempt == 1:
                        next_groups.append(grp)
                    else:
                        for (y, m, nd, problem) in bad:
                            with mlock: record(key, y, m, nd, OUT / fname(key, y, m), {}, 0.0, False, problem)
            groups = next_groups
        # months of a batch that failed once and then failed again singly are recorded above; nothing else to do

    prog.info("start: %d files in %d requests, %d request at a time (CDS limit)", total, len(tasks), WORKERS)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(one, tasks))
    nf = sum(r["verified"] == "no" for r in rows.values())
    prog.info("finished: %d verified, %d failed", sum(r["verified"] == "yes" for r in rows.values()), nf)
    if state["stop"]: sys.exit(2)          # systemd: do not restart (needs a fix)
    if nf: sys.exit(3)                     # systemd: try the failed months again later


if __name__ == "__main__":
    main()
