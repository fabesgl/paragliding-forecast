"""Report on the saved BOM observations (observations/<station>.csv): rows, first/last reading, and gaps. BOM only keeps ~72 hours, so a gap
longer than that can never be recovered; the job runs this after collecting and prints a warning (it never fails the run).
Usage: python scripts/check_observations.py [site_id] [--max-gap-hours 3]"""
import csv, datetime as dt, sys, yaml
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
site = next((a for a in sys.argv[1:] if not a.startswith("-")), "bright")
max_gap = dt.timedelta(hours=float(sys.argv[sys.argv.index("--max-gap-hours") + 1]) if "--max-gap-hours" in sys.argv else 3)
SPARSE = dt.timedelta(hours=20)                 # a station that only reports about once a day (Benalla) is flagged as sparse, not as full of gaps


def gaps(times, limit):
    return [(a, b) for a, b in zip(times, times[1:]) if b - a > limit]


def check(path, now=None):
    now = now or dt.datetime.now(dt.UTC).replace(tzinfo=None)
    ts = sorted(dt.datetime.strptime(r["obs_time_utc"], "%Y-%m-%dT%H:%M:%SZ") for r in csv.DictReader(open(path)))
    if not ts: return dict(rows=0)
    steps = sorted(b - a for a, b in zip(ts, ts[1:]))
    sparse = bool(steps) and steps[len(steps) // 2] >= SPARSE
    return dict(rows=len(ts), first=ts[0], last=ts[-1], sparse=sparse, gaps=[] if sparse else gaps(ts, max_gap), age=now - ts[-1])


if __name__ == "__main__":
    stations = yaml.safe_load((ROOT / "sites" / f"{site}.yaml").read_text())["bom_stations"]; bad = 0
    for sid, name in stations.items():
        f = ROOT / "observations" / f"{sid}.csv"
        if not f.exists(): print(f"MISSING {sid} {name}: no file"); bad += 1; continue
        r = check(f)
        if not r["rows"]: print(f"EMPTY   {sid} {name}"); bad += 1; continue
        note = "sparse feed (about one reading a day)" if r["sparse"] else (f"{len(r['gaps'])} gap(s) over {max_gap}: " + ", ".join(f"{a:%m-%d %H:%M}->{b:%m-%d %H:%M}" for a, b in r["gaps"][:3]) if r["gaps"] else "no gaps")
        stale = r["age"] > dt.timedelta(hours=12) and not r["sparse"]
        bad += bool(r["gaps"]) or stale
        print(f"{'WARN' if r['gaps'] or stale else 'ok  '}    {sid} {name}: {r['rows']} rows, {r['first']:%Y-%m-%d} to {r['last']:%Y-%m-%d %H:%M}Z, {note}" + (f"; LATEST READING IS {r['age']} OLD" if stale else ""))
    print("observations:", "all stations complete" if not bad else f"{bad} station(s) need a look")
