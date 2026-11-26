"""Offline flight-track parser and analyser for IGC and GPX files. Never touches the network.
parse_igc / parse_gpx(text) -> raw track; analyse(track) -> flight summary + thermal circles. Pure functions, no I/O except `import_root`.
Pilot identity = the folder a file sits in (igc_files/<pilot>/...), hashed by the caller. Names inside the files are never read or stored."""
import hashlib, math, re, sqlite3
from . import tzlookup
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

R_EARTH = 6371000.0
TAKEOFF_MS = 3.0      # ground speed (m/s) that counts as flying, not walking
MIN_FLIGHT_S = 120    # under 2 min is a logger test or a drive, not counted
VEHICLE_MS = 15.0     # 75th-percentile ground speed above this (54 km/h) = car, not a glider
TURN_RATE = 4.0       # deg/s (smoothed) that counts as circling: a full circle in under 90 s
MIN_THERMAL_TURN = 360.0
LOW_RATE_S = 5.0       # median gap between fixes above this = too coarse for reliable thermal detection (circles are 20-40 s)
MIN_CLIMB_MS = 0.3    # circling that gains less than this on average is tagged 'circling' (sink, spiral, ridge turn), not 'climb'

SCHEMA = """CREATE TABLE IF NOT EXISTS igc_flight (
  src_file TEXT PRIMARY KEY,                      -- path under the import root, e.g. 'pilot1/2024-03-01_02:15.igc' (folder + file name: the same name in two pilot folders is two flights)
  pilot_hash TEXT, date TEXT, launch_time TEXT, landing_time TEXT, utc_offset TEXT,   -- local date and times (see tz)
  launch_site TEXT, launch_lat REAL, launch_lon REAL, launch_alt_m REAL, land_lat REAL, land_lon REAL,
  duration_min REAL, track_km REAL, max_dist_km REAL, max_alt_m REAL, max_gain_m REAL,
  max_climb_ms REAL, max_sink_ms REAL, n_thermals INTEGER, mean_thermal_climb_ms REAL,
  alt_glitches INTEGER,                           -- fixes whose altitude was off by 40+ m (logger/GPS error); max_alt/gain less reliable when > 0
  imported_utc TEXT,
  pilot_folder TEXT,                              -- 'pilot1', 'pilot2'...: who the flight belongs to (the folder is the identity)
  source_fmt TEXT,                                -- 'igc' or 'gpx'
  n_fixes INTEGER, median_dt_s REAL, gap_frac REAL, -- sampling of the airborne part: median seconds between fixes, share of airborne time inside gaps over 10 s
  low_rate INTEGER,                               -- 1 = median gap over 5 s or over 20% of the time in gaps: thermal circles on this flight are NOT reliable
  file_sha1 TEXT,                                 -- content hash: the same file moved or renamed is not imported twice
  tz TEXT,                                        -- timezone used for the local date/times, worked out from the launch coordinates (tzlookup)
  tz_method TEXT);                                -- 'australia' (state-border rules), 'nearest' (approximate, outside Australia) or 'forced' (--tz given)
CREATE TABLE IF NOT EXISTS igc_thermal (
  src_file TEXT, n INTEGER, local_time TEXT, duration_s REAL, lat REAL, lon REAL, start_alt_m REAL, gain_m REAL, climb_ms REAL, turns REAL, direction TEXT,
  kind TEXT,                                      -- 'climb' (avg >= 0.3 m/s) or 'circling' (turning without lift: spiral, ridge turn)
  PRIMARY KEY (src_file, n));
CREATE INDEX IF NOT EXISTS igc_flight_day ON igc_flight(launch_site, date);"""
NEW_COLS = [("pilot_folder", "TEXT"), ("source_fmt", "TEXT"), ("n_fixes", "INTEGER"), ("median_dt_s", "REAL"), ("gap_frac", "REAL"), ("low_rate", "INTEGER"), ("file_sha1", "TEXT"), ("tz", "TEXT"), ("tz_method", "TEXT")]
# the same flight saved twice (same pilot, local date, launch minute, whole-minute duration) counts once; an IGC beats a GPX copy, then the first file name
UNIQUE_VIEW = """CREATE VIEW igc_flight_unique AS SELECT * FROM
  (SELECT *, ROW_NUMBER() OVER (PARTITION BY COALESCE(pilot_folder, pilot_hash), date, launch_time, CAST(duration_min AS INT) ORDER BY (src_file LIKE '%.gpx'), src_file) AS rn FROM igc_flight) WHERE rn = 1"""


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30); db.executescript(SCHEMA)
    have = {r[1] for r in db.execute("PRAGMA table_info(igc_flight)")}
    for col, typ in NEW_COLS:                                        # databases made before per-pilot folders and GPX existed
        if col not in have: db.execute(f"ALTER TABLE igc_flight ADD COLUMN {col} {typ}")
    db.execute("DROP VIEW IF EXISTS igc_flight_unique"); db.execute(UNIQUE_VIEW); db.commit(); return db


def _deg(dms, hemi, deg_digits):
    v = int(dms[:deg_digits]) + (int(dms[deg_digits:deg_digits + 2]) + int(dms[deg_digits + 2:deg_digits + 5]) / 1000) / 60
    return -v if hemi in "SW" else v


B_REC = re.compile(r"^B(\d{2})(\d{2})(\d{2})(\d{7})([NS])(\d{8})([EW])([AV])(-?\d{4}\d|\d{5})(-?\d{4}\d|\d{5})")


def parse_igc(text):
    """Returns dict(date=datetime.date (UTC date of first fix), fixes=[(t_utc_epoch, lat, lon, pressure_alt, gps_alt)]). Bad B lines are skipped."""
    d0 = None; fixes = []; day = 0; last = None
    for line in text.splitlines():
        if not line: continue
        c = line[0]
        if line.startswith("HFDTE") or line.startswith("HFDTEDATE"):
            m = re.search(r"(\d{2})(\d{2})(\d{2})", line[5:] if not line.startswith("HFDTEDATE") else line[9:])
            if m and d0 is None:
                dd, mo, yy = map(int, m.groups())
                try: d0 = datetime(2000 + yy if yy < 80 else 1900 + yy, mo, dd, tzinfo=timezone.utc)
                except ValueError: pass
        elif c == "B" and d0 is not None:
            m = B_REC.match(line)
            if not m: continue
            hh, mm, ss, la, ns, lo, ew, _, pa, ga = m.groups()
            sec = int(hh) * 3600 + int(mm) * 60 + int(ss)
            if last is not None and sec + day * 86400 < last - 43200: day += 1     # midnight rollover
            t = sec + day * 86400; last = t
            lat = _deg(la, ns, 2); lon = _deg(lo, ew, 3)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0): continue     # (0, 0) = no GPS fix yet
            fixes.append((d0.timestamp() + t, lat, lon, int(pa), int(ga)))
    if d0 is None: raise ValueError("no HFDTE date line")
    if len(fixes) < 10: raise ValueError(f"only {len(fixes)} usable fixes")
    # drop out-of-order and duplicate times
    clean = [fixes[0]]
    for f in fixes[1:]:
        if f[0] > clean[-1][0]: clean.append(f)
    return dict(date=d0, fixes=clean)


TRKPT = re.compile(r"<trkpt\b([^>]*?)(?:/>|>(.*?)</trkpt>)", re.S | re.I)
def _attr(attrs, name):
    m = re.search(rf'\b{name}\s*=\s*["\']([^"\']+)["\']', attrs); return float(m.group(1)) if m else None
def _tag(body, name):
    m = re.search(rf"<{name}>\s*([^<]*?)\s*</{name}>", body or "", re.I); return m.group(1) if m else None


def parse_gpx(text):
    """GPX track points (<trkpt lat lon><ele><time>) with the same result shape as parse_igc. Read with plain pattern matching, not an XML parser, so a hostile file cannot
    expand entities. Points without a time are dropped; route/waypoint-only files (a task, not a track) raise ValueError. Elevation is used as both altitudes (0 if missing)."""
    fixes = []
    for m in TRKPT.finditer(text):
        lat, lon = _attr(m.group(1), "lat"), _attr(m.group(1), "lon"); ts = _tag(m.group(2), "time")
        if lat is None or lon is None or not ts or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0): continue
        try: t = datetime.fromisoformat(ts.replace("Z", "+00:00").replace(" ", "T"))
        except ValueError: continue
        if t.tzinfo is None: t = t.replace(tzinfo=timezone.utc)
        ele = _tag(m.group(2), "ele")
        try: e = int(round(float(ele))) if ele else 0
        except ValueError: e = 0
        fixes.append((t.timestamp(), lat, lon, e, e))
    if len(fixes) < 10: raise ValueError(f"no track: only {len(fixes)} timed track points")
    fixes.sort(key=lambda f: f[0]); clean = [fixes[0]]
    for f in fixes[1:]:
        if f[0] > clean[-1][0]: clean.append(f)
    return dict(date=datetime.fromtimestamp(clean[0][0], timezone.utc), fixes=clean)


def dist_m(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat); dl = math.radians(b_lon - a_lon); dp = p2 - p1
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1, math.sqrt(h)))


def bearing(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat); dl = math.radians(b_lon - a_lon)
    return math.degrees(math.atan2(math.sin(dl) * math.cos(p2), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)))


def _alt(f):  # prefer GPS altitude, fall back to pressure altitude if GPS is zero/missing
    return f[4] if f[4] != 0 else f[3]


def _despike(fx, alts, max_ms=15.0):
    """GPS altitude glitches show up as impossible jumps (a glider cannot climb or sink 15+ m/s for long). Hold the last good altitude until the track agrees again."""
    out = list(alts); good = 0
    for i in range(1, len(alts)):
        dt = max(fx[i][0] - fx[good][0], 1.0)
        if abs(alts[i] - out[good]) / dt > max_ms and dt < 180: out[i] = out[good]
        else: good = i
    return [sorted(out[max(0, i - 2):i + 3])[len(out[max(0, i - 2):i + 3]) // 2] for i in range(len(out))]   # 5-point median removes single-fix spikes


def _smooth(vals, k):
    out = []; n = len(vals)
    for i in range(n):
        lo, hi = max(0, i - k), min(n, i + k + 1); out.append(sum(vals[lo:hi]) / (hi - lo))
    return out


def _airborne_span(fx):
    """(i_start, i_end) of the flight, or None. Moving runs (ground speed >= TAKEOFF_MS, gaps under 2 min merged) are split apart; runs whose median
    speed is car-like (> VEHICLE_MS: the drive up to launch) are dropped; the longest remaining run is the flight."""
    n = len(fx); sp = [0.0] * n
    for i in range(1, n):
        dt = fx[i][0] - fx[i - 1][0]
        sp[i] = dist_m(fx[i - 1][1], fx[i - 1][2], fx[i][1], fx[i][2]) / dt if 0 < dt <= 60 else 0.0
    sm = _smooth(sp, 7)
    runs = []; cur = None
    for i in range(n):
        if sm[i] < TAKEOFF_MS: continue
        if cur and fx[i][0] - fx[cur[1]][0] <= 120: cur[1] = i
        else:
            if cur: runs.append(cur)
            cur = [i, i]
    if cur: runs.append(cur)
    best = None
    for i0, i1 in runs:
        v = sorted(sm[i0:i1 + 1])[(i1 - i0) * 3 // 4]            # 75th percentile ground speed
        if v > VEHICLE_MS: continue
        al = [_alt(f) for f in fx[i0:i1 + 1]]
        if al[-1] - al[0] > 100 and max(al) - al[-1] < 20: continue   # climbs 100+ m and never comes down: the drive up to launch
        d = fx[i1][0] - fx[i0][0]
        if best is None or d > best[0]: best = (d, i0, i1)
    return (best[1], best[2]) if best else None


def analyse(track, tz=None):
    """Summary of one flight plus its thermal circles (list of dicts). Raises ValueError if the file holds no flight.
    tz: IANA name to force, or None = work it out from the launch coordinates (tzlookup), so Queensland, Spain etc. get their own local time."""
    fx = track["fixes"]; span = _airborne_span(fx)
    if not span: raise ValueError("no airborne movement")
    i0, i1 = span; seg = fx[i0:i1 + 1]
    dur = seg[-1][0] - seg[0][0]
    if dur < MIN_FLIGHT_S: raise ValueError(f"flight too short ({dur:.0f} s)")
    tz_method = "forced" if tz else None
    if not tz: tz, tz_method = tzlookup.zone_for(seg[0][1], seg[0][2])
    z = ZoneInfo(tz); t0 = datetime.fromtimestamp(seg[0][0], timezone.utc).astimezone(z); t1 = datetime.fromtimestamp(seg[-1][0], timezone.utc).astimezone(z)
    raw = [_alt(f) for f in seg]; alts = _despike(seg, raw); L0 = seg[0]; glitches = sum(1 for a, b in zip(raw, alts) if abs(a - b) > 40)
    track_m = 0.0; max_d = 0.0
    for a, b in zip(seg, seg[1:]):
        dt = b[0] - a[0]
        if dt <= 60: track_m += dist_m(a[1], a[2], b[1], b[2])
    for f in seg: max_d = max(max_d, dist_m(L0[1], L0[2], f[1], f[2]))
    # vertical speed: average over ~10 s windows (GPS altitude is noisy second to second)
    vs = []
    j = 0
    for i in range(len(seg)):
        while seg[i][0] - seg[j][0] > 10: j += 1
        dt = seg[i][0] - seg[j][0]
        if dt >= 5 and -20 <= (alts[i] - alts[j]) / dt <= 15: vs.append((alts[i] - alts[j]) / dt)   # beyond that is a GPS glitch, not flying
    max_gain = max(a - min(alts[:k + 1]) for k, a in enumerate(alts))
    dts = sorted(b[0] - a[0] for a, b in zip(seg, seg[1:])); med_dt = dts[len(dts) // 2]
    gap_frac = sum(d for d in dts if d > 10) / dur
    thermals = find_thermals(seg, alts, z); climbs = [t for t in thermals if t["kind"] == "climb"]
    mean_clm = sum(t["climb_ms"] for t in climbs) / len(climbs) if climbs else None
    summary = dict(tz=tz, tz_method=tz_method, date=t0.date().isoformat(), launch_time=t0.strftime("%H:%M"), landing_time=t1.strftime("%H:%M"), utc_offset=t0.strftime("%z"),
                   launch_lat=L0[1], launch_lon=L0[2], launch_alt_m=alts[0], land_lat=seg[-1][1], land_lon=seg[-1][2],
                   duration_min=round(dur / 60, 1), track_km=round(track_m / 1000, 2), max_dist_km=round(max_d / 1000, 2),
                   max_alt_m=max(alts), max_gain_m=max_gain, max_climb_ms=round(max(vs), 2) if vs else None, max_sink_ms=round(min(vs), 2) if vs else None,
                   n_thermals=len(climbs), alt_glitches=glitches, n_fixes=len(seg), median_dt_s=round(med_dt, 1), gap_frac=round(gap_frac, 2), low_rate=int(med_dt > LOW_RATE_S or gap_frac > 0.2), mean_thermal_climb_ms=round(mean_clm, 2) if mean_clm is not None else None)
    return summary, thermals


def find_thermals(seg, alts, z):
    """Circling segments: sustained turning in one direction totalling >= 360 degrees. Climb = altitude change / time over the segment."""
    n = len(seg)
    if n < 20: return []
    rate = [0.0] * n; hdg = [None] * n
    for i in range(1, n):
        a, b = seg[i - 1], seg[i]
        if dist_m(a[1], a[2], b[1], b[2]) >= 0.5: hdg[i] = bearing(a[1], a[2], b[1], b[2])
    prev = None; prev_t = None
    for i in range(n):
        if hdg[i] is None: continue
        if prev is not None:
            dh = (hdg[i] - prev + 540) % 360 - 180; dt = seg[i][0] - prev_t
            if 0 < dt <= 10: rate[i] = dh / dt
        prev, prev_t = hdg[i], seg[i][0]
    sm = _smooth(rate, 2)
    out = []; i = 0
    while i < n:
        if abs(sm[i]) < TURN_RATE: i += 1; continue
        sign = 1 if sm[i] > 0 else -1; j = i; gap = 0; k = i; total = 0.0
        while k < n:
            if sm[k] * sign >= TURN_RATE * 0.5: j = k; gap = 0
            else:
                gap += 1
                if gap > 8 or sm[k] * sign < -TURN_RATE: break
            k += 1
        turn = sum(rate[m] * (seg[m][0] - seg[m - 1][0]) for m in range(i + 1, j + 1) if m > 0 and 0 < seg[m][0] - seg[m - 1][0] <= 10) * sign
        if turn >= MIN_THERMAL_TURN and seg[j][0] - seg[i][0] >= 20:
            part = seg[i:j + 1]; dt = seg[j][0] - seg[i][0]
            out.append(dict(local_time=datetime.fromtimestamp(seg[i][0], timezone.utc).astimezone(z).strftime("%H:%M:%S"), duration_s=round(dt),
                            lat=round(sum(p[1] for p in part) / len(part), 5), lon=round(sum(p[2] for p in part) / len(part), 5),
                            start_alt_m=alts[i], gain_m=alts[j] - alts[i], climb_ms=round((alts[j] - alts[i]) / dt, 2), kind="climb" if (alts[j] - alts[i]) / dt >= MIN_CLIMB_MS else "circling",
                            turns=round(turn / 360, 1), direction="right" if sign > 0 else "left"))
        i = max(j + 1, i + 1)
    return out


# ---- launch sites ------------------------------------------------------------------------------------------------------------------------------------
def match_site(lat, lon, known):
    """known: [{id, lat, lon, radius_m}]. Nearest known launch within its radius, else 'unknown@LAT,LON' on a ~1 km grid so repeat launches group together."""
    best = None
    for s in known:
        d = dist_m(lat, lon, s["lat"], s["lon"])
        if d <= s.get("radius_m", 500) and (best is None or d < best[0]): best = (d, s["id"])
    return best[1] if best else f"unknown@{round(lat, 2):.2f},{round(lon, 2):.2f}"


def file_sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def migrate_legacy(db, root, pilot_hasher):
    """Rows from before per-pilot folders have a bare file name as src_file and a hash of the header name. If exactly one pilot folder holds a file of that name,
    re-key the row as '<folder>/<name>' and give it the folder identity. Returns (moved, unresolved names)."""
    root = Path(root); moved = 0; left = []
    for (name,) in db.execute("SELECT src_file FROM igc_flight WHERE src_file NOT LIKE '%/%'").fetchall():
        homes = [d for d in sorted(root.iterdir()) if d.is_dir() and (d / name).is_file()] if root.is_dir() else []
        if len(homes) != 1: left.append(name); continue
        key = f"{homes[0].name}/{name}"
        if db.execute("SELECT 1 FROM igc_flight WHERE src_file=?", (key,)).fetchone(): left.append(name); continue
        db.execute("UPDATE igc_flight SET src_file=?, pilot_folder=?, pilot_hash=?, source_fmt=?, file_sha1=? WHERE src_file=?",
                   (key, homes[0].name, pilot_hasher(homes[0].name), name.rsplit(".", 1)[-1].lower(), file_sha1(homes[0] / name), name))
        db.execute("UPDATE igc_thermal SET src_file=? WHERE src_file=?", (key, name)); moved += 1
    db.commit(); return moved, left


def _backfill(db, root, pilot_hasher):
    """Rows imported before the sample-rate columns existed have no sampling stats or hash: fill the hash (so moved files are recognised) and re-analyse for the rate."""
    rows = db.execute("SELECT src_file FROM igc_flight WHERE file_sha1 IS NULL OR median_dt_s IS NULL").fetchall(); n = 0
    for (key,) in rows:
        p = Path(root) / key
        if not p.is_file(): continue
        try:
            tr = parse_gpx(p.read_text(errors="replace")) if p.suffix.lower() == ".gpx" else parse_igc(p.read_text(errors="replace")); s, _ = analyse(tr)
        except Exception: continue
        db.execute("UPDATE igc_flight SET file_sha1=?, n_fixes=?, median_dt_s=?, gap_frac=?, low_rate=?, pilot_folder=COALESCE(pilot_folder,?), pilot_hash=?, source_fmt=COALESCE(source_fmt,?) WHERE src_file=?",
                   (file_sha1(p), s["n_fixes"], s["median_dt_s"], s["gap_frac"], s["low_rate"], key.split("/")[0], pilot_hasher(key.split("/")[0]), p.suffix.lower().lstrip("."), key)); n += 1
    db.commit(); return n


SUPPORTED = (".igc", ".gpx")


def _save(db, key, folder, fmt, sha, s, ths, known_sites, pilot_hasher, now):
    """Write one analysed flight and its thermals (replacing any earlier rows for the same file)."""
    site = match_site(s["launch_lat"], s["launch_lon"], known_sites)
    cols = dict(src_file=key, pilot_hash=pilot_hasher(folder), date=s["date"], launch_time=s["launch_time"], landing_time=s["landing_time"], utc_offset=s["utc_offset"],
                launch_site=site, launch_lat=s["launch_lat"], launch_lon=s["launch_lon"], launch_alt_m=s["launch_alt_m"], land_lat=s["land_lat"], land_lon=s["land_lon"],
                duration_min=s["duration_min"], track_km=s["track_km"], max_dist_km=s["max_dist_km"], max_alt_m=s["max_alt_m"], max_gain_m=s["max_gain_m"],
                max_climb_ms=s["max_climb_ms"], max_sink_ms=s["max_sink_ms"], n_thermals=s["n_thermals"], mean_thermal_climb_ms=s["mean_thermal_climb_ms"],
                alt_glitches=s["alt_glitches"], imported_utc=now, pilot_folder=folder, source_fmt=fmt, n_fixes=s["n_fixes"],
                median_dt_s=s["median_dt_s"], gap_frac=s["gap_frac"], low_rate=s["low_rate"], file_sha1=sha, tz=s["tz"], tz_method=s["tz_method"])
    db.execute(f"INSERT OR REPLACE INTO igc_flight ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    db.execute("DELETE FROM igc_thermal WHERE src_file=?", (key,))
    for k, t in enumerate(ths, 1):
        db.execute("INSERT INTO igc_thermal VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (key, k, t["local_time"], t["duration_s"], t["lat"], t["lon"], t["start_alt_m"], t["gain_m"], t["climb_ms"], t["turns"], t["direction"], t["kind"]))


def retime(db, root, known_sites, pilot_hasher, tz=None):
    """Make every stored flight's local time agree with the zone worked out from its launch coordinates (or the forced `tz`).
    Flights with no zone yet get it written; if the new zone gives a different UTC offset at that moment, the file is analysed again (date, times, thermal times change).
    Returns dict(recorded=n whose times stayed the same, changed=[(file, old 'date time offset', new 'date time offset', zone)], missing=[files not on disk])."""
    out = dict(recorded=0, changed=[], missing=[]); now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    rows = db.execute("SELECT src_file, launch_lat, launch_lon, date, launch_time, utc_offset, tz, pilot_folder, source_fmt, file_sha1, tz_method FROM igc_flight WHERE launch_lat IS NOT NULL AND launch_lon IS NOT NULL AND utc_offset IS NOT NULL").fetchall()
    for key, la, lo, date, lt, off, old_tz, folder, fmt, sha, old_method in rows:
        want, method = (tz, "forced") if tz else tzlookup.zone_for(la, lo)
        if old_tz == want and old_method == method: continue
        if old_tz == want:                                               # same zone, better label (e.g. now confirmed by timezonefinder)
            db.execute("UPDATE igc_flight SET tz_method=? WHERE src_file=?", (method, key)); out["recorded"] += 1; continue
        sign = 1 if off[0] == "+" else -1; offs = sign * (int(off[1:3]) * 3600 + int(off[3:5]) * 60)
        inst = datetime.fromisoformat(f"{date}T{lt}:00").replace(tzinfo=timezone.utc) - timedelta(seconds=offs)
        if inst.astimezone(ZoneInfo(want)).utcoffset() == timedelta(seconds=offs):
            db.execute("UPDATE igc_flight SET tz=?, tz_method=? WHERE src_file=?", (want, method, key)); out["recorded"] += 1; continue
        p = Path(root) / key
        if not p.is_file(): out["missing"].append(key); continue
        text = p.read_text(errors="replace")
        tr = parse_gpx(text) if p.suffix.lower() == ".gpx" else parse_igc(text); s, ths = analyse(tr, tz)
        _save(db, key, folder or key.split("/")[0], fmt or p.suffix.lower().lstrip("."), sha or file_sha1(p), s, ths, known_sites, pilot_hasher, now)
        out["changed"].append((key, f"{date} {lt} {off}", f"{s['date']} {s['launch_time']} {s['utc_offset']}", want))
    db.commit(); return out


def relabel(db, known_sites):
    """Re-match every stored flight's launch to the current launch list (sites/launches.yaml), so adding or moving a launch updates flights already imported.
    Returns {(old launch, new launch): number of flights} for the ones that changed."""
    changed = {}
    for key, la, lo, old in db.execute("SELECT src_file, launch_lat, launch_lon, launch_site FROM igc_flight WHERE launch_lat IS NOT NULL AND launch_lon IS NOT NULL").fetchall():
        new = match_site(la, lo, known_sites)
        if new != old:
            db.execute("UPDATE igc_flight SET launch_site=? WHERE src_file=?", (new, key)); changed[(old, new)] = changed.get((old, new), 0) + 1
    db.commit(); return changed


def import_root(root, db, known_sites, pilot_hasher, tz=None):
    """Import every .igc/.gpx under root/<pilot>/ . The folder name is the pilot (hashed by `pilot_hasher`); names inside the files are ignored.
    Re-runnable and safe against duplicates: a file already stored (same path) or whose content was already stored for that pilot is skipped.
    tz: None = zone from each flight's launch coordinates. Returns dict(imported, already, same_content, low_rate, failed=[(path, reason)], ignored=[paths of other file types], per_pilot={folder: imported})."""
    root = Path(root); res = dict(imported=0, already=0, same_content=0, low_rate=0, failed=[], ignored=[], per_pilot={})
    mig, _ = migrate_legacy(db, root, pilot_hasher); res["migrated"] = mig; res["backfilled"] = _backfill(db, root, pilot_hasher)
    res["retime"] = retime(db, root, known_sites, pilot_hasher, tz); res["relabeled"] = relabel(db, known_sites)
    done = {r[0] for r in db.execute("SELECT src_file FROM igc_flight")}
    shas = {(r[0], r[1]) for r in db.execute("SELECT pilot_folder, file_sha1 FROM igc_flight WHERE file_sha1 IS NOT NULL")}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    for f in sorted(p for p in root.iterdir() if p.is_file() and not p.name.startswith(".")):
        res["failed"].append((f.name, "not inside a pilot folder (put it in igc_files/<pilot>/)"))
    for pdir in sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")):
        for p in sorted(x for x in pdir.rglob("*") if x.is_file() and not x.name.startswith(".")):
            key = str(p.relative_to(root))
            if p.suffix.lower() not in SUPPORTED: res["ignored"].append(key); continue
            if key in done: res["already"] += 1; continue
            try:
                sha = file_sha1(p)
                if (pdir.name, sha) in shas: res["same_content"] += 1; continue
                text = p.read_text(errors="replace")
                tr = parse_gpx(text) if p.suffix.lower() == ".gpx" else parse_igc(text); s, ths = analyse(tr, tz)
            except Exception as e: res["failed"].append((key, f"{type(e).__name__}: {e}")); continue
            _save(db, key, pdir.name, p.suffix.lower().lstrip("."), sha, s, ths, known_sites, pilot_hasher, now)
            shas.add((pdir.name, sha)); done.add(key); res["imported"] += 1; res["low_rate"] += s["low_rate"]; res["per_pilot"][pdir.name] = res["per_pilot"].get(pdir.name, 0) + 1
            if res["imported"] % 100 == 0: db.commit()
    db.commit(); return res
