"""Import IGC flight tracks (offline only) into data/igc_flights.sqlite. Re-runnable: files already stored are skipped. Never changes the .igc files.

  python scripts/import_igc.py igc_files            # import every *.igc in that folder, then print the summary
  python scripts/import_igc.py igc_files --summary-only
Per flight: local date, launch/landing time, launch site (matched by coordinates to sites/launches.yaml, else unknown@lat,lon), duration, track length,
max distance from launch, max altitude, gain, climb/sink rates. Per thermal (circling >= 360 deg): time, position, climb rate, turns, direction.
The pilot name in the IGC header is hashed with the shared salt (store.pilot_hash); the raw name is never stored or printed.
"""
import argparse, collections, sys
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import igc
from pgforecast.chatlog import store

def summary(db, failed):
    q = lambda s, *a: db.execute(s, a).fetchall()
    n = q("SELECT COUNT(*) FROM igc_flight")[0][0]
    print(f"\nflights stored: {n} | circling segments: {q('SELECT COUNT(*) FROM igc_thermal')[0][0]} (of which thermal climbs {q("SELECT COUNT(*) FROM igc_thermal WHERE kind='climb'")[0][0]}) | files skipped: {len(failed)}")
    lo, hi = q("SELECT MIN(date), MAX(date) FROM igc_flight")[0]; print(f"date range: {lo} .. {hi} | flying days: {q('SELECT COUNT(DISTINCT date) FROM igc_flight')[0][0]}"
          f" | pilots (hashed): {q('SELECT COUNT(DISTINCT pilot_hash) FROM igc_flight')[0][0]}")
    print("flights per launch site:")
    for s, c, d in q("SELECT launch_site, COUNT(*), COUNT(DISTINCT date) FROM igc_flight GROUP BY 1 ORDER BY 2 DESC"): print(f"  {s:28s} {c:4d} flights, {d:4d} days")
    print("Mystic flights per year (local date):")
    for y, c, d, h in q("SELECT substr(date,1,4), COUNT(*), COUNT(DISTINCT date), ROUND(SUM(duration_min)/60,1) FROM igc_flight WHERE launch_site='mystic' GROUP BY 1 ORDER BY 1"): print(f"  {y}: {c:3d} flights on {d:3d} days, {h} h airtime")
    r = q("SELECT ROUND(AVG(duration_min)), ROUND(MAX(duration_min)), ROUND(MAX(max_dist_km),1), MAX(max_alt_m), ROUND(MAX(max_climb_ms),1) FROM igc_flight WHERE launch_site='mystic'")[0]
    print(f"Mystic: mean {r[0]} min, longest {r[1]} min, furthest {r[2]} km from launch, highest {r[3]} m, best 10 s climb {r[4]} m/s")
    print("thermals at Mystic (kind=climb):", q("SELECT COUNT(*), ROUND(AVG(climb_ms),2), ROUND(MAX(climb_ms),1) FROM igc_thermal WHERE kind='climb' AND src_file IN (SELECT src_file FROM igc_flight WHERE launch_site='mystic')")[0], "(count, mean m/s, best m/s)")
    print("flights with GPS altitude glitches (max altitude/gain less reliable):", q("SELECT COUNT(*) FROM igc_flight WHERE alt_glitches>0")[0][0])
    print("circling with no lift (spirals, ridge turns, sink):", q("SELECT COUNT(*) FROM igc_thermal WHERE kind='circling'")[0][0])
    if failed:
        print("skipped files:"); c = collections.Counter(r.split("(")[0].strip() for _, r in failed)
        for k, v in c.most_common(): print(f"  {v:3d} x {k}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder"); ap.add_argument("--db", default=str(ROOT / "data" / "igc_flights.sqlite")); ap.add_argument("--salt", default=str(ROOT / "data" / "chat_salt"))
    ap.add_argument("--launches", default=str(ROOT / "sites" / "launches.yaml")); ap.add_argument("--tz", default="Australia/Melbourne"); ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--failed-list", default=str(ROOT / "data" / "igc_failed.txt"))
    a = ap.parse_args(); db = igc.connect(a.db); failed = []
    if not a.summary_only:
        salt_ = store.salt(a.salt); known = yaml.safe_load(open(a.launches))["launches"]
        n, old, failed = igc.import_dir(a.folder, db, known, lambda name: store.pilot_hash(salt_, name), tz=a.tz)
        print(f"imported {n} new flights ({old} already stored), skipped {len(failed)} files")
        Path(a.failed_list).write_text("".join(f"{f}\t{r}\n" for f, r in failed))
        print(f"skipped files listed in {a.failed_list}")
    summary(db, failed)
