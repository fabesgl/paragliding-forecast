"""Download Open-Meteo Single Runs API forecasts for Mystic, one request per day (the 00:00 UTC run, 3 days long, GFS + ECMWF 0.25 + ICON), into data/single_runs/<run date>.json (local, git-ignored).
A run made on day R gives the forecast as issued 2 days ahead for R+2 and 1 day ahead for R+1. The API only keeps runs from about April 2026, so runs before that are skipped until the first one that exists.
Resumable (files already saved are skipped). Each request takes ~20-30 s on the server side, so --workers N (default 1, use 3) runs N at a time. Any other error or block stops the run and nothing is retried.
Usage: python scripts/fetch_single_runs.py [--from 2026-04-01] [--to YYYY-MM-DD] [--workers 3]      Generic user-agent, no contact details."""
import datetime as dt, json, sys, time
from pathlib import Path
import requests, yaml
ROOT = Path(__file__).resolve().parent.parent
args = sys.argv[1:]; opt = lambda n, d: args[args.index(n) + 1] if n in args else d
FIRST = dt.date.fromisoformat(opt("--from", "2026-04-01")); LAST = dt.date.fromisoformat(opt("--to", (dt.date.today() - dt.timedelta(days=3)).isoformat()))
cfg = yaml.safe_load((ROOT / "sites" / "bright.yaml").read_text()); p = cfg["points"]["mystic_launch"]
VARS = ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "wind_speed_850hPa", "wind_direction_850hPa", "wind_speed_700hPa", "wind_direction_700hPa", "temperature_2m", "dew_point_2m",
        "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "cape", "boundary_layer_height", "shortwave_radiation", "precipitation"]
MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless"]
OUT = ROOT / "data" / "single_runs"; OUT.mkdir(parents=True, exist_ok=True)


def one(d, stop):
    """Download the run of day d. Returns None on success, or an error text (and sets `stop`) so the whole run stops and nothing is retried."""
    if stop.is_set(): return "skipped"
    try:
        r = requests.get("https://single-runs-api.open-meteo.com/v1/forecast", timeout=150, headers={"User-Agent": "Bright paragliding forecast, personal use"},
                         params=dict(latitude=p["lat"], longitude=p["lon"], hourly=",".join(VARS), models=",".join(MODELS), run=f"{d}T00:00", forecast_days=3, timezone="UTC", wind_speed_unit="kmh"))
        j = r.json() if r.status_code == 200 else None
        if j is None or "hourly" not in j: stop.set(); return f"{d}: HTTP {r.status_code} {r.text[:150]}"
    except (requests.RequestException, ValueError) as e: stop.set(); return f"{d}: {type(e).__name__}"
    (OUT / f"{d}.json").write_text(json.dumps(dict(run=f"{d}T00:00", hourly=j["hourly"]), separators=(",", ":"))); time.sleep(0.4); return None


def main():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    workers = int(opt("--workers", "1")); saved = sorted(x.stem for x in OUT.glob("*.json"))
    d = max(FIRST, dt.date.fromisoformat(saved[0])) if saved else FIRST                    # a resumed run starts at the first run already saved
    while not saved and d <= LAST:                                                         # find the first run that exists (earlier ones are not kept); one request at a time
        try: r = requests.get("https://single-runs-api.open-meteo.com/v1/forecast", timeout=150, headers={"User-Agent": "Bright paragliding forecast, personal use"}, params=dict(latitude=p["lat"], longitude=p["lon"], hourly="wind_speed_850hPa", models="gfs_seamless", run=f"{d}T00:00", forecast_days=1))
        except requests.RequestException as e: sys.exit(f"Stopped at {d}: {type(e).__name__}. No retry.")
        if r.status_code == 200: break
        if not (r.status_code == 400 and "not available" in r.text): sys.exit(f"Stopped at {d}: HTTP {r.status_code} {r.text[:150]}. No retry.")
        d += dt.timedelta(days=1)
    pending = []; x = d
    while x <= LAST:
        if not (OUT / f"{x}.json").exists(): pending.append(x)
        x += dt.timedelta(days=1)
    stop = threading.Event(); errors = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for e in ex.map(lambda x: one(x, stop), pending):
            if e and e != "skipped": errors.append(e)
    files = sorted(x.stem for x in OUT.glob("*.json"))
    print(f"Downloaded {len(pending) - len(errors)} of {len(pending)} pending runs. Saved runs: {len(files)}, first {files[0]}, last {files[-1]}.")
    if errors: sys.exit("Stopped: " + errors[0] + ". Nothing is retried; run again to resume.")


if __name__ == "__main__": main()
