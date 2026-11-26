"""Timezone of a point from its coordinates, offline. Used to turn UTC track times into the local time at the launch (Queensland has no daylight saving; Spain is 9-10 h behind Victoria).
Australia: explicit state-border rules (the nearest-city shortcut puts Byron Bay in Queensland). Everywhere else: the nearest zone in the system tz database's zone1970.tab
(its representative point per zone), so near a border or on an island the answer can be wrong; that table is the only data used."""
import math, zoneinfo
from pathlib import Path

OVERRIDES_FILE = Path(__file__).resolve().parent.parent.parent / "sites" / "timezones.yaml"
_ZONES = None; _OVR = None


def _load():
    global _ZONES
    if _ZONES is None:
        for d in zoneinfo.TZPATH:
            f = Path(d) / "zone1970.tab"
            if f.is_file(): break
        else: raise FileNotFoundError("zone1970.tab not found in " + ", ".join(zoneinfo.TZPATH) + " (install the system tzdata package)")
        out = []
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line or line[0] == "#": continue
            parts = line.split("\t")
            if len(parts) < 3: continue
            c = parts[1]; i = max(c.rfind("+"), c.rfind("-"))
            def dm(s, deg):                                  # +DDMM / +DDDMM, optionally with seconds
                sign = -1 if s[0] == "-" else 1; s = s[1:]; d = int(s[:deg]); m = int(s[deg:deg + 2]); sec = int(s[deg + 2:deg + 4] or 0)
                return sign * (d + m / 60 + sec / 3600)
            out.append((dm(c[:i], 2), dm(c[i:], 3), parts[2]))
        _ZONES = out
    return _ZONES


def _km(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2); h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def _australia(lat, lon):
    """IANA zone for a point on the Australian mainland or Tasmania, by state border; None if the point is not there."""
    if not (-44 <= lat <= -10 and 112 <= lon <= 154): return None
    if lat < -39.4 and lon > 143.5: return "Australia/Hobart"
    if lon < 129: return "Australia/Eucla" if (lon >= 125.9 and lat < -30.5 and lon < 129) else "Australia/Perth"
    if lon < 138: return "Australia/Darwin" if lat > -26 else "Australia/Adelaide"           # NT north of 26 S, SA south
    if lon < 141: return "Australia/Brisbane" if lat > -26 else "Australia/Adelaide"          # Queensland north of 26 S, SA south
    if 141 <= lon < 142.3 and -32.6 < lat < -29.0: return "Australia/Broken_Hill"            # NSW, keeps South Australian time
    if lon < 149: qld = lat > -29.0
    else: qld = lat > -29.0 + (lon - 149.0) * (0.83 / 4.55)                                    # the border slopes from 29 S at 149 E to 28.17 S at the coast (153.55 E)
    if qld: return "Australia/Brisbane"
    return "Australia/Melbourne" if (lat < -34.0 and lon < 150.0 and _km(lat, lon, -37.8, 145.0) < _km(lat, lon, -33.9, 151.2)) else "Australia/Sydney"   # same rules; named for the nearer city


def _overrides():
    global _OVR
    if _OVR is None:
        _OVR = []
        if OVERRIDES_FILE.is_file():
            import yaml
            _OVR = (yaml.safe_load(OVERRIDES_FILE.read_text()) or {}).get("overrides", [])
    return _OVR


def zone_for(lat, lon):
    """(IANA timezone name, method) for the coordinates. method: 'override' (a box in sites/timezones.yaml), 'australia' (state-border rules, reliable)
    or 'nearest' (approximate: check flights outside Australia)."""
    for o in _overrides():
        if o["lat"][0] <= lat <= o["lat"][1] and o["lon"][0] <= lon <= o["lon"][1]: return o["tz"], "override"
    z = _australia(lat, lon)
    if z: return z, "australia"
    return min(_load(), key=lambda t: _km(lat, lon, t[0], t[1]))[2], "nearest"
