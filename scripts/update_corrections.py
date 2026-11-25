"""Update the rolling bias corrections from finished days: archived forecasts vs BOM observations.
Writes corrections/corrections.json. Run by the GitHub job (local runs write to data/local_out). Safe to re-run: each day is used once.
Usage: python scripts/update_corrections.py [site_id]"""
import csv, datetime as dt, gzip, json, os, sys, yaml
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import corrections as C

def f(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def load_obs(path):
    """Half-hourly BOM rows -> hourly (UTC) values: mean for most variables, max for gusts."""
    hours = {}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            t = dt.datetime.strptime(r["obs_time_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
            h = (t + dt.timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)     # nearest hour
            hours.setdefault(h, []).append(r)
    out = {}
    for h, rs in hours.items():
        o = {}
        for _, (col, kind, _) in C.VARS.items():
            vals = [f(r.get(col)) for r in rs if f(r.get(col)) is not None]
            if vals: o[col] = max(vals) if col == "gust_kmh" else sum(vals) / len(vals)
        out[h] = o
    return out

def load_archive(arch_dir, tz):
    files = []
    for p in sorted(Path(arch_dir).glob("*.json.gz")):
        j = json.load(gzip.open(p, "rt")); t = dt.datetime.fromisoformat(j["fetched_utc"])
        files.append((t, j))
    return files

def run(arch_dir, obs_dir, state_path, cfg, today=None, models=("gfs_seamless", "ecmwf_ifs025", "icon_seamless")):
    tz = ZoneInfo(cfg["timezone"]); today = today or dt.datetime.now(tz).date()
    state = json.loads(Path(state_path).read_text()) if Path(state_path).exists() else C.new_state()
    stations = {k: p["bom_station"] for k, p in cfg["points"].items() if p.get("bom_station")}
    obs = {k: load_obs(Path(obs_dir) / f"{sid}.csv") for k, sid in stations.items() if (Path(obs_dir) / f"{sid}.csv").exists()}
    files = load_archive(arch_dir, tz)
    days = sorted({(t.astimezone(tz).date() + dt.timedelta(days=1)) for t, _ in files})       # a forecast file made on day X covers day X+1
    done = 0
    for D in days:
        if D >= today or (state["last_day"] and D.isoformat() <= state["last_day"]): continue
        prior = [(t, j) for t, j in files if t.astimezone(tz).date() == D - dt.timedelta(days=1)]
        if not prior: continue
        _, j = max(prior, key=lambda x: x[0])                                                  # the last run made the evening before
        for pk, pobs in obs.items():
            if pk not in j["points"]: continue
            hourly = j["response"][j["points"].index(pk)]["hourly"]
            for model in models:
                samples = []
                for i, ts in enumerate(hourly["time"]):
                    lt = dt.datetime.fromisoformat(ts).replace(tzinfo=tz)
                    if lt.date() != D or lt.hour not in C.HOURS: continue
                    o = pobs.get(lt.astimezone(dt.UTC).replace(minute=0, second=0, microsecond=0))
                    if not o: continue
                    fc = {v: (hourly.get(f"{v}_{model}") or [None] * len(hourly["time"]))[i] for v in C.VARS}
                    samples.append(dict(dir=(hourly.get(f"wind_direction_10m_{model}") or [None] * len(hourly["time"]))[i], fc=fc,
                                        ob={v: o.get(col) for v, (col, _, _) in C.VARS.items()}))
                if len(samples) >= 6: C.update_day(state, model, pk, samples)
        state["last_day"] = D.isoformat(); done += 1
    state["updated_utc"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ")
    Path(state_path).parent.mkdir(parents=True, exist_ok=True); Path(state_path).write_text(json.dumps(state, indent=1, sort_keys=True))
    return state, done

if __name__ == "__main__":
    site = sys.argv[1] if len(sys.argv) > 1 else "bright"
    OUT = ROOT if os.environ.get("GITHUB_ACTIONS") else ROOT / "data" / "local_out"
    cfg = yaml.safe_load((ROOT / "sites" / f"{site}.yaml").read_text())
    state, done = run(OUT / "forecast_archive", ROOT / "observations", OUT / "corrections" / "corrections.json", cfg)
    print(f"days added: {done}; bins: {C.summary(state)}")
