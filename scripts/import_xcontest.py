"""Import saved XContest flight-search pages (offline only) into data/xcontest_flights.sqlite. Re-runnable: only new flights are added.

  python scripts/import_xcontest.py            # reads xcontest_pages/*/ (every folder, e.g. mystic_2026, mystic_2025)
  python scripts/import_xcontest.py --summary  # also print the summary
"""
import argparse, sys, collections
from datetime import datetime, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import xcontest
from pgforecast.chatlog import store

def warn_short(db):
    """Print every site/month whose saved flights (by UTC month) differ from XContest's printed total. Returns the number of short months."""
    bad = 0
    for site, month, exp, have in xcontest.check_totals(db):
        if not xcontest.is_complete(have, exp): bad += 1; print(f"  INCOMPLETE {site} {month}: saved {have} of {exp} - short by {exp - have}")
        elif have < exp: print(f"  note    {site} {month}: saved {have} of {exp} - short by {exp - have}, counted as complete (tolerance {xcontest.MONTH_TOLERANCE})")
        elif have > exp: print(f"  note    {site} {month}: saved {have}, XContest says {exp} (+{have - exp}; saved pages may come from a later search)")
    print("all months complete" if not bad else f"{bad} month(s) still short"); return bad

def summary(db, week=None):
    q = lambda s, *a: db.execute(s, a).fetchall()
    exp = {(s, m): e for s, m, e, _ in xcontest.check_totals(db)}
    have = collections.Counter(); tand = collections.Counter()
    for s, d, tm, off, cl in q("SELECT site, date, start_time, utc_offset, glider_class FROM xc_flight"):
        m = xcontest.utc_month(d, tm, off); have[(s, m)] += 1; tand[(s, m)] += cl == "T"
    print("flights per month (UTC month, as XContest counts) saved / XContest total:")
    for (s, m), n in sorted(have.items()): print(f"  {s} {m}  {n:4d} / {exp.get((s, m), '?')}   (incl. {tand[(s, m)]} tandem)")
    inc = q("SELECT site, month, xc_total, saved FROM xc_month_status WHERE complete=0 ORDER BY 1,2")
    print("incomplete months:", ", ".join(f"{s} {m} ({h}/{e})" for s, m, e, h in inc) or "none")
    print("days:", ", ".join(f"{st} {n}" for st, n in q("SELECT status, COUNT(*) FROM xc_day_status GROUP BY 1")), "(unknown = month incomplete, so no flights saved does NOT mean nobody flew)")
    print("distinct pilots:", q("SELECT COUNT(DISTINCT pilot_hash) FROM xc_flight")[0][0], "| flying days:", q("SELECT COUNT(DISTINCT date) FROM xc_flight")[0][0],
          "| graded (non-tandem) flights:", q("SELECT COUNT(*) FROM xc_flight_graded")[0][0])
    for cls, n in q("SELECT COALESCE(glider_class,'?'), COUNT(*) FROM xc_flight GROUP BY 1"): print(f"  class {cls}: {n}")
    days = [d for (d,) in q("SELECT DISTINCT date FROM xc_flight_graded ORDER BY date")]
    if week: days = [d for d in days if week <= d < (datetime.fromisoformat(week) + timedelta(days=7)).date().isoformat()]
    for d in days[:7] if not week else days:
        print(d, "top 3 by distance (tandem excluded):")
        for km, pts, rt, gl, cl, ph, la in q("SELECT distance_km,points,route_type,glider_model,glider_class,pilot_hash,launch FROM xc_flight_graded WHERE date=? ORDER BY distance_km DESC LIMIT 3", d):
            print(f"   {km:6.1f} km {pts:6.1f} p  {rt:13s} {cl}/{gl}  pilot {ph[:6]}  {la}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pages", default=str(ROOT / "xcontest_pages")); ap.add_argument("--db", default=str(ROOT / "data" / "xcontest_flights.sqlite"))
    ap.add_argument("--salt", default=str(ROOT / "data" / "chat_salt")); ap.add_argument("--summary", action="store_true"); ap.add_argument("--week", help="YYYY-MM-DD start of the sample week")
    a = ap.parse_args(); salt_ = store.salt(a.salt); db = xcontest.connect(a.db)
    print(xcontest.import_dir(a.pages, db, lambda n: store.pilot_hash(salt_, n)))
    warn_short(db)
    if a.summary or a.week: summary(db, a.week)
