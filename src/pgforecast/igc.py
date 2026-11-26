"""Offline IGC flight-track parser and analyser. Never touches the network.
parse_igc(text) -> raw track; analyse(track) -> flight summary + thermal circles. Pure functions, no I/O except `import_dir`.
Pilot names are hashed by the caller; the raw name never leaves this module's return value `header_pilot` (which the importer hashes at once and drops)."""
import math, re, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

R_EARTH = 6371000.0
TAKEOFF_MS = 3.0      # ground speed (m/s) that counts as flying, not walking
MIN_FLIGHT_S = 120    # under 2 min is a logger test or a drive, not counted
VEHICLE_MS = 15.0     # 75th-percentile ground speed above this (54 km/h) = car, not a glider
TURN_RATE = 4.0       # deg/s (smoothed) that counts as circling: a full circle in under 90 s
MIN_THERMAL_TURN = 360.0
MIN_CLIMB_MS = 0.3    # circling that gains less than this on average is tagged 'circling' (sink, spiral, ridge turn), not 'climb'

SCHEMA = """CREATE TABLE IF NOT EXISTS igc_flight (
  src_file TEXT PRIMARY KEY, pilot_hash TEXT, date TEXT, launch_time TEXT, landing_time TEXT, utc_offset TEXT,   -- local (Australia/Melbourne or site tz) date and times
  launch_site TEXT, launch_lat REAL, launch_lon REAL, launch_alt_m REAL, land_lat REAL, land_lon REAL,
  duration_min REAL, track_km REAL, max_dist_km REAL, max_alt_m REAL, max_gain_m REAL,
  max_climb_ms REAL, max_sink_ms REAL, n_thermals INTEGER, mean_thermal_climb_ms REAL, alt_glitches INTEGER, imported_utc TEXT);   -- alt_glitches: fixes whose altitude was off by 40+ m (logger/GPS error); max_alt/gain less reliable when > 0
CREATE TABLE IF NOT EXISTS igc_thermal (
  src_file TEXT, n INTEGER, local_time TEXT, duration_s REAL, lat REAL, lon REAL, start_alt_m REAL, gain_m REAL, climb_ms REAL, turns REAL, direction TEXT, kind TEXT,   -- kind: 'climb' (avg >= 0.3 m/s) or 'circling' (turning without lift)
  PRIMARY KEY (src_file, n));
CREATE INDEX IF NOT EXISTS igc_flight_day ON igc_flight(launch_site, date);
-- the same flight saved twice (identical local date, launch time, duration, track length) counts once: the first file name wins
CREATE VIEW IF NOT EXISTS igc_flight_unique AS SELECT * FROM igc_flight WHERE src_file IN
  (SELECT MIN(src_file) FROM igc_flight GROUP BY date, launch_time, duration_min, track_km);"""


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30); db.executescript(SCHEMA); return db


def _deg(dms, hemi, deg_digits):
    v = int(dms[:deg_digits]) + (int(dms[deg_digits:deg_digits + 2]) + int(dms[deg_digits + 2:deg_digits + 5]) / 1000) / 60
    return -v if hemi in "SW" else v


B_REC = re.compile(r"^B(\d{2})(\d{2})(\d{2})(\d{7})([NS])(\d{8})([EW])([AV])(-?\d{4}\d|\d{5})(-?\d{4}\d|\d{5})")


def parse_igc(text):
    """Returns dict(date=datetime.date (UTC date of first fix), pilot=str, fixes=[(t_utc_epoch, lat, lon, pressure_alt, gps_alt)]). Bad B lines are skipped."""
    d0 = None; pilot = ""; fixes = []; day = 0; last = None
    for line in text.splitlines():
        if not line: continue
        c = line[0]
        if line.startswith("HFDTE") or line.startswith("HFDTEDATE"):
            m = re.search(r"(\d{2})(\d{2})(\d{2})", line[5:] if not line.startswith("HFDTEDATE") else line[9:])
            if m and d0 is None:
                dd, mo, yy = map(int, m.groups())
                try: d0 = datetime(2000 + yy if yy < 80 else 1900 + yy, mo, dd, tzinfo=timezone.utc)
                except ValueError: pass
        elif line.startswith("HFPLT") or line.startswith("HOPLT"):
            pilot = line.split(":", 1)[1].strip() if ":" in line else ""
        elif c == "B" and d0 is not None:
            m = B_REC.match(line)
            if not m: continue
            hh, mm, ss, la, ns, lo, ew, _, pa, ga = m.groups()
            sec = int(hh) * 3600 + int(mm) * 60 + int(ss)
            if last is not None and sec + day * 86400 < last - 43200: day += 1     # midnight rollover
            t = sec + day * 86400; last = t
            lat = _deg(la, ns, 2); lon = _deg(lo, ew, 3)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180): continue
            fixes.append((d0.timestamp() + t, lat, lon, int(pa), int(ga)))
    if d0 is None: raise ValueError("no HFDTE date line")
    if len(fixes) < 10: raise ValueError(f"only {len(fixes)} usable fixes")
    # drop out-of-order and duplicate times
    clean = [fixes[0]]
    for f in fixes[1:]:
        if f[0] > clean[-1][0]: clean.append(f)
    return dict(date=d0, pilot=pilot, fixes=clean)


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


def analyse(track, tz="Australia/Melbourne"):
    """Summary of one flight plus its thermal circles (list of dicts). Raises ValueError if the file holds no flight."""
    fx = track["fixes"]; span = _airborne_span(fx)
    if not span: raise ValueError("no airborne movement")
    i0, i1 = span; seg = fx[i0:i1 + 1]
    dur = seg[-1][0] - seg[0][0]
    if dur < MIN_FLIGHT_S: raise ValueError(f"flight too short ({dur:.0f} s)")
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
    thermals = find_thermals(seg, alts, z); climbs = [t for t in thermals if t["kind"] == "climb"]
    mean_clm = sum(t["climb_ms"] for t in climbs) / len(climbs) if climbs else None
    summary = dict(date=t0.date().isoformat(), launch_time=t0.strftime("%H:%M"), landing_time=t1.strftime("%H:%M"), utc_offset=t0.strftime("%z"),
                   launch_lat=L0[1], launch_lon=L0[2], launch_alt_m=alts[0], land_lat=seg[-1][1], land_lon=seg[-1][2],
                   duration_min=round(dur / 60, 1), track_km=round(track_m / 1000, 2), max_dist_km=round(max_d / 1000, 2),
                   max_alt_m=max(alts), max_gain_m=max_gain, max_climb_ms=round(max(vs), 2) if vs else None, max_sink_ms=round(min(vs), 2) if vs else None,
                   n_thermals=len(climbs), alt_glitches=glitches, mean_thermal_climb_ms=round(mean_clm, 2) if mean_clm is not None else None)
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


def canonical_pilots(names):
    """names: {file: raw header name}. The files come from ONE pilot but the header is typed differently ('Krz', blank, full name...).
    Returns {file: name to hash}: blank or a prefix/extension of the most common longest name maps to that name; anything else is kept as is."""
    from collections import Counter
    norm = {f: n.strip().casefold() for f, n in names.items()}
    cnt = Counter(v for v in norm.values() if v)
    if not cnt: return {f: "" for f in names}
    top = max(cnt, key=lambda v: (len(v.split()), cnt[v]))     # prefer the fullest name
    out = {}
    for f, v in norm.items():
        out[f] = top if (not v or top.startswith(v) or v.startswith(top.split()[0])) else v
    return out


def import_dir(folder, db, known_sites, pilot_hasher, tz="Australia/Melbourne", log=print):
    """Import every *.igc in folder (re-runnable: files already stored are skipped). Returns (n_imported, n_skipped_existing, [(file, reason)])."""
    files = sorted(Path(folder).glob("*.igc")); done = {r[0] for r in db.execute("SELECT src_file FROM igc_flight")}
    parsed = {}; failed = []; names = {}
    for p in files:
        if p.name in done: continue
        try:
            tr = parse_igc(p.read_text(errors="replace")); names[p.name] = tr["pilot"]; parsed[p.name] = tr
        except Exception as e: failed.append((p.name, f"parse: {e}"))
    pil = canonical_pilots(names); now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"); n = 0
    for name, tr in parsed.items():
        try: s, ths = analyse(tr, tz)
        except Exception as e: failed.append((name, f"analyse: {e}")); continue
        site = match_site(s["launch_lat"], s["launch_lon"], known_sites)
        db.execute("""INSERT OR REPLACE INTO igc_flight VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (name, pilot_hasher(pil[name]), s["date"], s["launch_time"], s["landing_time"], s["utc_offset"], site, s["launch_lat"], s["launch_lon"], s["launch_alt_m"],
                    s["land_lat"], s["land_lon"], s["duration_min"], s["track_km"], s["max_dist_km"], s["max_alt_m"], s["max_gain_m"], s["max_climb_ms"], s["max_sink_ms"],
                    s["n_thermals"], s["mean_thermal_climb_ms"], s["alt_glitches"], now))
        db.execute("DELETE FROM igc_thermal WHERE src_file=?", (name,))
        for k, t in enumerate(ths, 1):
            db.execute("INSERT INTO igc_thermal VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (name, k, t["local_time"], t["duration_s"], t["lat"], t["lon"], t["start_alt_m"], t["gain_m"], t["climb_ms"], t["turns"], t["direction"], t["kind"]))
        n += 1
    db.commit()
    return n, len(done), failed
