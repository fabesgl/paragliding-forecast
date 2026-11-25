"""Offline parser for saved XContest flight-search pages (xcontest_pages/<site>_<year>/*.html). Never touches the network.
Only what the results table holds is extracted: date, local start time + UTC offset, pilot (hashed), launch, route type, distance, points,
glider model and glider class letter. Airtime and speed are NOT in these pages (only a legend mentions 'speed')."""
import re, html, sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROW = re.compile(r'<tr id="flight-(\d+)"[^>]*>(.*?)</tr>', re.S)
SCHEMA = """CREATE TABLE IF NOT EXISTS xc_flight (
  flight_id INTEGER PRIMARY KEY,           -- XContest FLID; the same flight on overlapping pages/periods is stored once
  site TEXT, date TEXT, start_time TEXT, utc_offset TEXT, launch TEXT, pilot_hash TEXT,
  route_type TEXT, distance_km REAL, points REAL, glider_model TEXT, glider_class TEXT, src_file TEXT,
  airtime_min REAL, speed_kmh REAL,        -- not present in saved pages; kept NULL
  UNIQUE (date, start_time, pilot_hash, launch));
CREATE TABLE IF NOT EXISTS xc_page_total (src_file TEXT PRIMARY KEY, site TEXT, month TEXT, xc_total INTEGER, rows INTEGER);  -- month: UTC month the search covers; xc_total: count XContest printed on that page
-- class T = tandem: stored, but left out of XC grading (top-N, rankings, stats on achievements)
CREATE VIEW IF NOT EXISTS xc_flight_graded AS SELECT * FROM xc_flight WHERE COALESCE(glider_class,'') <> 'T';"""

def _txt(s): return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()

def parse_page(text, site):
    for fid, body in ROW.findall(text):
        m = re.search(r'class="full">(\d\d)\.(\d\d)\.(\d\d) <em>(\d\d:\d\d)</em><span class="XCutcOffset">(UTC[+-][\d:]+)', body)
        if not m: continue
        d, mo, y, tm, off = m.groups()
        pilot = re.search(r'class="plt"[^>]*>(.*?)</a>', body, re.S); launch = re.search(r'class="lau"[^>]*>(.*?)</a>', body, re.S)
        route = re.search(r'class="disc-(\w+)" title="([^"]*)"', body); km = re.search(r'class="km"><strong>([\d.]+)', body)
        pts = re.search(r'class="pts"><strong>([\d.]+)', body); gl = re.search(r'<td class="cat-(\w)"><div title="([^"]*)"', body)
        yield dict(flight_id=int(fid), site=site, date=f"20{y}-{mo}-{d}", start_time=tm, utc_offset=off,
                   launch=_txt(launch.group(1)) if launch else None, pilot=_txt(pilot.group(1)) if pilot else None,
                   route_type=(route.group(2) if route else None), distance_km=float(km.group(1)) if km else None,
                   points=float(pts.group(1)) if pts else None, glider_model=html.unescape(gl.group(2)).strip() if gl else None,
                   glider_class=gl.group(1) if gl else None)

def page_total(text):
    """(month 'YYYY-MM' or None, total flights XContest printed). Only single-month searches give a month."""
    t = re.search(r'<strong[^>]*>(\d+)</strong>\s*flights', text)
    sel = [v for v in re.findall(r'<option value="([^"]*)" selected', text) if re.fullmatch(r"\d{4}-\d\d", v)]
    return (sel[-1] if sel else None), (int(t.group(1)) if t else None)

def utc_month(date, start_time, offset):
    m = re.fullmatch(r"UTC([+-])(\d+):(\d+)", offset or "")
    dt = datetime.fromisoformat(f"{date}T{start_time}")
    if m: dt -= (1 if m.group(1) == "+" else -1) * timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
    return dt.strftime("%Y-%m")

def check_totals(db):
    """Per (site, month): flights saved (by UTC month, as XContest filters) vs the total XContest printed. Returns [(site, month, expected, saved)], short ones flagged by saved<expected."""
    exp = {(s, m): t for s, m, t in db.execute("SELECT site, month, MAX(xc_total) FROM xc_page_total WHERE month IS NOT NULL GROUP BY 1,2")}
    have = {}
    for s, d, tm, off in db.execute("SELECT site, date, start_time, utc_offset FROM xc_flight"):
        k = (s, utc_month(d, tm, off)); have[k] = have.get(k, 0) + 1
    return [(s, m, e, have.get((s, m), 0)) for (s, m), e in sorted(exp.items())]

def refresh_status(db):
    """Rebuilds xc_month_status (complete = saved >= XContest's total) and xc_day_status for every day from a site's first to last saved flight.
    A day with flights is 'flew'. A day with none is 'no_flights' only if every UTC month it touches is complete; otherwise 'unknown'
    (the flight may exist on XContest but not in our saved pages). A month with no saved page at all is unknown too."""
    db.executescript("""CREATE TABLE IF NOT EXISTS xc_month_status (site TEXT, month TEXT, xc_total INTEGER, saved INTEGER, complete INTEGER, PRIMARY KEY (site, month));
                        CREATE TABLE IF NOT EXISTS xc_day_status (site TEXT, date TEXT, status TEXT, n_flights INTEGER, PRIMARY KEY (site, date));
                        DELETE FROM xc_month_status; DELETE FROM xc_day_status;""")
    complete = {}
    for site, month, exp, have in check_totals(db):
        complete[(site, month)] = have >= exp; db.execute("INSERT INTO xc_month_status VALUES (?,?,?,?,?)", (site, month, exp, have, int(have >= exp)))
    for (site,) in db.execute("SELECT DISTINCT site FROM xc_flight").fetchall():
        off = db.execute("SELECT utc_offset FROM xc_flight WHERE site=? GROUP BY utc_offset ORDER BY COUNT(*) DESC LIMIT 1", (site,)).fetchone()[0]
        n = dict(db.execute("SELECT date, COUNT(*) FROM xc_flight WHERE site=? GROUP BY date", (site,)).fetchall())
        lo, hi = datetime.fromisoformat(min(n)), datetime.fromisoformat(max(n)); d = lo
        while d <= hi:
            ds = d.date().isoformat(); k = n.get(ds, 0)
            months = {utc_month(ds, t, off) for t in ("00:00", "23:59")}
            st = "flew" if k else ("no_flights" if all(complete.get((site, m)) for m in months) else "unknown")
            db.execute("INSERT INTO xc_day_status VALUES (?,?,?,?)", (site, ds, st, k)); d += timedelta(days=1)
    db.commit()

def connect(path):
    db = sqlite3.connect(path); db.executescript(SCHEMA); return db

def import_dir(root, db, pilot_hash, out=print):
    """Reads every xcontest_pages/<site>_<year>/ folder. Adds flights not seen before (by FLID, else by date+time+pilot+launch)."""
    stats = dict(files=0, rows=0, added=0, duplicates=0)
    for folder in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        site = folder.name.rsplit("_", 1)[0]
        for f in sorted(folder.glob("*.html")):
            stats["files"] += 1; text = f.read_text(errors="replace"); month, total = page_total(text)
            db.execute("INSERT OR REPLACE INTO xc_page_total VALUES (?,?,?,?,?)", (f"{folder.name}/{f.name}", site, month, total, len(ROW.findall(text))))
            for r in parse_page(text, site):
                stats["rows"] += 1; r["pilot_hash"] = pilot_hash(r.pop("pilot")); r["src_file"] = f"{folder.name}/{f.name}"
                cur = db.execute("""INSERT OR IGNORE INTO xc_flight (flight_id,site,date,start_time,utc_offset,launch,pilot_hash,route_type,distance_km,points,glider_model,glider_class,src_file)
                                    VALUES (:flight_id,:site,:date,:start_time,:utc_offset,:launch,:pilot_hash,:route_type,:distance_km,:points,:glider_model,:glider_class,:src_file)""", r)
                stats["added" if cur.rowcount else "duplicates"] += 1
    refresh_status(db); return stats
