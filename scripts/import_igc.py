"""Import flight tracks (offline only) into data/igc_flights.sqlite. Reads .igc and .gpx files. Re-runnable: nothing is imported twice. Never changes the track files.

  python scripts/import_igc.py igc_files            # igc_files/<pilot>/*.igc|*.gpx for every pilot folder, then prints the summary
  python scripts/import_igc.py igc_files --summary-only
The FOLDER NAME is the pilot (pilot1, pilot2, ...), hashed with the shared salt (store.pilot_hash). Names inside the files are never read or stored.
Per flight: timezone from the launch coordinates (Queensland, Spain... each get their own), local date, launch/landing time, launch site (matched by coordinates to sites/launches.yaml, else unknown@lat,lon), duration, track length, max distance
from launch, max altitude, gain, climb/sink rates, and how finely the track is sampled. Per thermal (circling >= 360 deg): time, position, climb rate, turns, direction.
Flights whose median gap between fixes is over 5 s (or that spend over 20% of the time in gaps over 10 s) are flagged low_rate=1: thermal circles are unreliable there.
Duplicates: the same path is skipped; the same file content for the same pilot (moved/renamed) is skipped; the same flight as both IGC and GPX counts once in igc_flight_unique.
"""
import argparse, collections, sys
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import igc
from pgforecast.chatlog import store

def summary(db, res=None):
    q = lambda s, *a: db.execute(s, a).fetchall()
    U = "igc_flight_unique"
    n = q(f"SELECT COUNT(*) FROM {U}")[0][0]
    print(f"\nflights stored: {q('SELECT COUNT(*) FROM igc_flight')[0][0]} ({n} unique after removing the same flight saved twice) | circling segments: {q('SELECT COUNT(*) FROM igc_thermal')[0][0]}"
          f" (thermal climbs {q(chr(83)+'ELECT COUNT(*) FROM igc_thermal WHERE kind=?', 'climb')[0][0]})")
    print("per pilot (folder): flights / unique / days / date range / formats / low-sample-rate flights")
    for f, c, u, d, lo, hi in q(f"SELECT g.pilot_folder, COUNT(*), (SELECT COUNT(*) FROM {U} x WHERE x.pilot_folder=g.pilot_folder), COUNT(DISTINCT date), MIN(date), MAX(date) FROM igc_flight g GROUP BY 1 ORDER BY 1"):
        fm = ", ".join(f"{k} {v}" for k, v in q("SELECT source_fmt, COUNT(*) FROM igc_flight WHERE pilot_folder=? GROUP BY 1", f))
        lr = q("SELECT COUNT(*) FROM igc_flight WHERE pilot_folder=? AND low_rate=1", f)[0][0]
        md = q("SELECT ROUND(AVG(median_dt_s),1) FROM igc_flight WHERE pilot_folder=?", f)[0][0]
        print(f"  {f:10s} {c:4d} / {u:4d} flights, {d:4d} days, {lo}..{hi}, {fm}; low sample rate: {lr} (mean median-gap {md} s)")
    print("flights per launch site (unique flights, all pilots):")
    for s, c, d in q(f"SELECT launch_site, COUNT(*), COUNT(DISTINCT date) FROM {U} GROUP BY 1 ORDER BY 2 DESC LIMIT 12"): print(f"  {s:28s} {c:4d} flights, {d:4d} days")
    print("Mystic flights per pilot and year (unique flights):")
    for f, y, c, d in q(f"SELECT pilot_folder, substr(date,1,4), COUNT(*), COUNT(DISTINCT date) FROM {U} WHERE launch_site='mystic' GROUP BY 1,2 ORDER BY 1,2"): print(f"  {f} {y}: {c:3d} flights on {d:3d} days")
    print("timezone per flight (how many; method):", ", ".join(f"{z or '?'} {c}" + ("" if m in ("australia", "forced", "override") else " [approximate]") for z, m, c in q("SELECT tz, tz_method, COUNT(*) FROM igc_flight GROUP BY 1,2 ORDER BY 3 DESC")))
    print("thermal reliability: flights usable for thermal work (not low_rate):", q(f"SELECT COUNT(*) FROM {U} WHERE low_rate=0")[0][0], "of", n)
    if res:
        print(f"\nthis run: imported {res['imported']} new ({', '.join(f'{k} {v}' for k, v in res['per_pilot'].items()) or 'none'}), already stored {res['already']}, same content already stored {res['same_content']},"
              f" migrated old rows to folder keys {res.get('migrated', 0)}, back-filled {res.get('backfilled', 0)}; flagged low sample rate this run: {res['low_rate']}")
        rt = res["retime"]
        print(f"timezones: written for {rt['recorded']} flights whose times did not change; {len(rt['changed'])} flights re-timed because their zone differs from the old one"
              + (f"; {len(rt['missing'])} could not be re-timed (file missing)" if rt["missing"] else ""))
        for k, old, new, z in rt["changed"][:8]: print(f"   {k}: {old} -> {new} ({z})")
        if res["ignored"]: print(f"ignored (not .igc/.gpx): {len(res['ignored'])}: " + ", ".join(sorted(collections.Counter(Path(p).suffix.lower() or '(none)' for p in res['ignored']).elements())))
        if res["failed"]:
            print(f"skipped files: {len(res['failed'])}"); c = collections.Counter(r.split("(")[0].strip()[:60] for _, r in res["failed"])
            for k, v in c.most_common(8): print(f"  {v:3d} x {k}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder"); ap.add_argument("--db", default=str(ROOT / "data" / "igc_flights.sqlite")); ap.add_argument("--salt", default=str(ROOT / "data" / "chat_salt"))
    ap.add_argument("--launches", default=str(ROOT / "sites" / "launches.yaml")); ap.add_argument("--tz", default=None, help="force one IANA timezone for every flight (default: worked out per flight from its launch coordinates)")
    ap.add_argument("--summary-only", action="store_true"); ap.add_argument("--failed-list", default=str(ROOT / "data" / "igc_failed.txt"))
    a = ap.parse_args(); db = igc.connect(a.db); res = None
    if not a.summary_only:
        salt_ = store.salt(a.salt); known = yaml.safe_load(open(a.launches))["launches"]
        res = igc.import_root(a.folder, db, known, lambda name: store.pilot_hash(salt_, "igc-folder:" + name), tz=a.tz)
        Path(a.failed_list).write_text("".join(f"{f}\t{r}\n" for f, r in res["failed"]) + "".join(f"{f}\tignored (unsupported type)\n" for f in res["ignored"]))
        print(f"skipped/ignored files listed in {a.failed_list}")
    summary(db, res)
