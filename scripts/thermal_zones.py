"""East vs west thermals near Mystic launch (one pilot's tracks, default pilot1; low-sample-rate flights left out), from IGC thermal circles, matched to BARRA-C2 weather. Read-only analysis: it never touches the dashboard, forecasts or scores.

  python scripts/thermal_zones.py                 # writes data/thermal_zones/{report.txt, map.html, thermals.csv}
  python scripts/thermal_zones.py --minutes 15 --radius 1500

Facing north from launch, east = right = "Emily", west = left = "Marcus" (Fabian's naming of the two house thermals). The two groups come from k-means (k=2) on
where each climb's circles were (metres east/north of launch). Weather = BARRA-C2 (BOM reanalysis, 4.4 km, hourly, UTC) interpolated to the launch point and the climb's time.
Wind at 'launch' is BARRA's 10 m wind on a 4.4 km grid: it does not see the local slope or valley flow.
"""
import argparse, collections, math, sys, glob
from collections import defaultdict
from pathlib import Path
import numpy as np, pandas as pd, sqlite3, xarray as xr
ROOT = Path(__file__).resolve().parent.parent
LAT0, LON0 = -36.75855, 146.96575                    # Mystic launch (sites/bright.yaml)
VARS = ["uas", "vas", "ua850", "va850", "ta850", "tas", "rsds", "cll", "clm", "clh", "CAPE", "zmla"]
KMH = 3.6
MERGE_M, RING_M = 50.0, 250.0   # ring mode: within 50 m = "merged over launch"; 50-250 m = Emily (east) / Marcus (west)
SECT = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def offsets(lat, lon): return (lon - LON0) * 111195 * math.cos(math.radians(LAT0)), (lat - LAT0) * 111195


def kmeans2(X, seeds=300):
    X = np.asarray(X, float).reshape(len(X), -1)
    rng = np.random.RandomState(0); best = None
    for _ in range(seeds):
        c = X[rng.choice(len(X), 2, replace=False)].copy()
        for _ in range(100):
            lab = np.argmin(((X[:, None, :] - c[None]) ** 2).sum(2), 1)
            new = np.array([X[lab == k].mean(0) if (lab == k).any() else c[k] for k in range(2)])
            if np.allclose(new, c): break
            c = new
        inertia = ((X - c[lab]) ** 2).sum()
        if best is None or inertia < best[0]: best = (inertia, c, lab)
    _, c, lab = best
    if c[0, 0] > c[1, 0]: c = c[::-1]; lab = 1 - lab      # label 0 = west, 1 = east
    return c, lab


def sun_pos(t_utc):
    """(elevation deg, azimuth deg from north) of the sun at Mystic. Standard low-precision formula, good to ~0.5 deg."""
    d = t_utc.timestamp() / 86400 + 2440587.5 - 2451545.0
    L = (280.460 + 0.9856474 * d) % 360; g = math.radians((357.528 + 0.9856003 * d) % 360)
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)); eps = math.radians(23.439 - 4e-7 * d)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)); dec = math.asin(math.sin(eps) * math.sin(lam))
    ha = math.radians((18.697374558 + 24.06570982441908 * d) % 24 * 15 + LON0) - ra; la = math.radians(LAT0)
    el = math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))
    az = (math.degrees(math.atan2(math.sin(ha), math.cos(ha) * math.sin(la) - math.tan(dec) * math.cos(la))) + 180) % 360
    return math.degrees(el), az


def wind_from(u, v): return (math.degrees(math.atan2(-u, -v)) % 360), math.hypot(u, v) * KMH     # meteorological: direction the wind comes FROM


def sector(deg): return SECT[int(((deg + 22.5) % 360) // 45)]


def bilinear(ds, v):
    """Value of variable v at the launch point: weighted mean of the 4 surrounding BARRA grid cells (no scipy needed)."""
    la, lo = ds["lat"].values, ds["lon"].values
    i = int(np.searchsorted(la, LAT0)) - 1; j = int(np.searchsorted(lo, LON0)) - 1
    fy = (LAT0 - la[i]) / (la[i + 1] - la[i]); fx = (LON0 - lo[j]) / (lo[j + 1] - lo[j]); a = ds[v].values
    val = a[:, i, j] * (1 - fy) * (1 - fx) + a[:, i + 1, j] * fy * (1 - fx) + a[:, i, j + 1] * (1 - fy) * fx + a[:, i + 1, j + 1] * fy * fx
    return pd.Series(val, index=pd.to_datetime(ds["time"].values))


def load_weather(months):
    """{month 'YYYYMM': DataFrame indexed by UTC hour} at the launch point (bilinear). One file per variable per month."""
    out = {}
    for ym in sorted(months):
        cols = {}
        for v in VARS:
            f = ROOT / "data" / "barra2" / f"bright_area__{v}__{ym}.nc"
            if not f.exists(): continue
            with xr.open_dataset(f) as ds: cols[v] = bilinear(ds, v)
        if cols: out[ym] = pd.DataFrame(cols)
    return out


def weather_at(frames, t_utc):
    """Linear in time between the two surrounding BARRA records (mean fields are stamped at :30, point fields at :00)."""
    ts = pd.Timestamp(t_utc).tz_localize(None)
    rows = []
    for ym in {ts.strftime("%Y%m"), (ts - pd.Timedelta(hours=2)).strftime("%Y%m"), (ts + pd.Timedelta(hours=2)).strftime("%Y%m")}:
        if ym in frames: rows.append(frames[ym])
    if not rows: return None
    df = pd.concat(rows).sort_index(); df = df[~df.index.duplicated()]
    if ts < df.index[0] or ts > df.index[-1]: return None
    df = df.reindex(df.index.union([ts])).interpolate(method="time", limit_area="inside")     # each variable has its own time stamps (:00 point, :30 hourly mean)
    r = df.loc[ts]
    return r if not r.isna().any() else None


def build(minutes, radius, split="ew", pilot="pilot1"):
    db = sqlite3.connect(ROOT / "data" / "igc_flights.sqlite")
    rows = db.execute("""SELECT t.src_file, f.date, f.launch_time, f.utc_offset, t.local_time, t.lat, t.lon, t.climb_ms, t.gain_m, t.duration_s, t.turns, t.direction
        FROM igc_thermal t JOIN igc_flight_unique f USING(src_file) WHERE f.launch_site='mystic' AND t.kind='climb'
        AND COALESCE(f.low_rate, 0) = 0 AND (? = 'all' OR f.pilot_folder = ?)""", (pilot, pilot)).fetchall()
    recs = []
    for sf, date, lt, off, tm, la, lo, clm, gain, dur, turns, dr in rows:
        e, n = offsets(la, lo); sec = lambda s: int(s[:2]) * 3600 + int(s[3:5]) * 60 + (int(s[6:8]) if len(s) > 5 else 0)
        mins = (sec(tm) - sec(lt)) / 60
        if mins > minutes or math.hypot(e, n) > radius: continue
        sign = 1 if off[0] == "+" else -1; offs = sign * (int(off[1:3]) * 3600 + int(off[3:5]) * 60)
        t_utc = pd.Timestamp(f"{date} {tm}") - pd.Timedelta(seconds=offs)
        recs.append(dict(file=sf, date=date, local_time=tm, hour=int(tm[:2]) + int(tm[3:5]) / 60, month=int(date[5:7]), mins_after_launch=round(mins, 1), east_m=e, north_m=n,
                         climb=clm, gain=gain, dur=dur, turns=turns, turn_dir=dr, t_utc=t_utc.to_pydatetime().replace(tzinfo=__import__("datetime").timezone.utc)))
    df = pd.DataFrame(recs)
    NNW = (-235.0, 368.0)           # the second hotspot ~370 m NNW of launch that the free 2-D clustering found; Fabian: "something else", excluded
    if split == "ring":
        d = np.hypot(df.east_m, df.north_m); ringm = ((d > MERGE_M) & (d <= RING_M)).values
        _, lab_r = kmeans2(df.loc[ringm, ["east_m", "north_m"]].values)
        c = np.array([df.loc[ringm][lab_r == k][["east_m", "north_m"]].mean().values for k in (0, 1)])
        zone = np.full(len(df), "outside", dtype=object)
        zone[(d > RING_M).values & (np.hypot(df.east_m - NNW[0], df.north_m - NNW[1]) <= 200).values] = "nnw"
        zone[(d <= MERGE_M).values] = "merged"; zone[ringm] = np.where(lab_r == 1, "east", "west")
        df["zone"] = zone; df["dist_m"] = d.round(0)
        dd = [np.hypot(df.east_m - c[k, 0], df.north_m - c[k, 1]) for k in (0, 1)]
        df["ambiguous"] = (np.minimum(dd[0], dd[1]) / np.maximum(dd[0], dd[1]) > 0.7) & ringm
    elif split == "ew":      # Fabian's framing: left/right of launch. Cluster on the east offset only, then centre each group in 2D for the map.
        _, lab = kmeans2(df[["east_m"]].values)
        c = np.array([df[lab == k][["east_m", "north_m"]].mean().values for k in (0, 1)])
    else: c, lab = kmeans2(df[["east_m", "north_m"]].values)          # '2d': let the clustering find any two spots
    if split != "ring":
        df["zone"] = np.where(lab == 1, "east", "west")
        d0 = ((df[["east_m", "north_m"]].values - c[0]) ** 2).sum(1) ** .5; d1 = ((df[["east_m", "north_m"]].values - c[1]) ** 2).sum(1) ** .5
        df["ambiguous"] = np.minimum(d0, d1) / np.maximum(d0, d1) > 0.7           # nearly as close to the other centre
    frames = load_weather({t.strftime("%Y%m") for t in df.t_utc})
    wx = []
    for t in df.t_utc:
        r = weather_at(frames, t); wx.append(r)
    ok = [r is not None for r in wx]; df = df[ok].reset_index(drop=True); wx = [r for r in wx if r is not None]
    w = pd.DataFrame(wx).reset_index(drop=True)
    df["sfc_dir"], df["sfc_kmh"] = zip(*[wind_from(u, v) for u, v in zip(w.uas, w.vas)])
    df["w850_dir"], df["w850_kmh"] = zip(*[wind_from(u, v) for u, v in zip(w.ua850, w.va850)])
    df["sfc_u"], df["sfc_v"], df["w850_u"], df["w850_v"] = w.uas * KMH, w.vas * KMH, w.ua850 * KMH, w.va850 * KMH
    df["temp_c"], df["t850_c"] = w.tas - 273.15, w.ta850 - 273.15
    df["rsds"] = w.rsds; df["cloud_pct"] = 100 * (1 - (1 - w.cll / 100) * (1 - w.clm / 100) * (1 - w.clh / 100))
    df["cape"] = w.CAPE; df["bl_m"] = w.zmla
    df["sun_elev"], df["sun_az"] = zip(*[sun_pos(t) for t in df.t_utc])
    return df, c


def day_bootstrap(df, col, n=2000, seed=1, a="east", b="west"):
    """Difference east - west in mean of `col`, with a 95% interval from resampling whole DAYS (thermals on one day are not independent)."""
    rng = np.random.RandomState(seed); days = df.date.unique(); g = {d: x for d, x in df.groupby("date")}
    def diff(sub):
        e, w = sub[sub.zone == a][col], sub[sub.zone == b][col]
        return (e.mean() - w.mean()) if len(e) and len(w) else np.nan
    base = diff(df); boots = []
    for _ in range(n):
        pick = rng.choice(days, len(days)); boots.append(diff(pd.concat([g[d] for d in pick])))
    lo, hi = np.nanpercentile(boots, [2.5, 97.5]); return base, lo, hi


def share_ci(s, n=400, seed=2, a="east"):
    """95% range of the east share, resampling days inside the bin."""
    rng = np.random.RandomState(seed); g = [x.zone.eq(a).values for _, x in s.groupby("date")]
    if len(g) < 3: return None
    v = []
    for _ in range(n):
        pick = rng.randint(0, len(g), len(g)); a = np.concatenate([g[i] for i in pick]); v.append(a.mean())
    return np.percentile(v, [2.5, 97.5]) * 100


def table(df, key, order, label, a="east", b="west"):
    lines = [f"{label:<14}{a:>7}{b:>7}{a + ' %':>9}{'  range':>12}{'  climb ' + a[0].upper():>11}{'  climb ' + b[0].upper():>10}   days"]
    for k in order:
        s = df[df[key] == k]
        if not len(s): continue
        e, w = s[s.zone == a], s[s.zone == b]
        f = lambda x: f"{x.climb.mean():.2f}" if len(x) else "  - "
        thin = "  (thin)" if len(s) < 12 else ""; ci = share_ci(s, a=a); rg = f"{ci[0]:.0f}-{ci[1]:.0f}" if ci is not None else "-"
        lines.append(f"{str(k):<14}{len(e):>7}{len(w):>7}{100 * len(e) / len(s):>8.0f}%{rg:>12}{f(e):>11}{f(w):>10}   {s.date.nunique():>3}{thin}")
    return "\n".join(lines)


def bins(series, edges, names):
    return pd.cut(series, edges, labels=names, right=False)


COL = {"east": "#d4501e", "west": "#1f6fb5", "merged": "#7a3fa0", "nnw": "#999999", "outside": "#cccccc"}
NAME = {"east": "Emily (E)", "west": "Marcus (W)", "merged": "merged over launch", "nnw": "NNW spot (excluded)", "outside": "other"}


def make_map(df, c, path, radius, rings=(500, 1000, 1500), title="", note=""):
    S = 520 / (2 * radius); W = 560; cx = cy = W / 2
    X = lambda e: cx + e * S; Y = lambda n: cy - n * S
    el = []
    for r in rings:
        if r <= radius: el.append(f'<circle cx="{cx}" cy="{cy}" r="{r * S}" fill="none" stroke="#bbb" stroke-dasharray="4 4"/><text x="{cx + r * S * 0.71 + 3}" y="{cy - r * S * 0.71 - 3}" font-size="11" fill="#777">{r:g} m</text>')
    el.append(f'<line x1="{cx}" y1="{cy - radius * S}" x2="{cx}" y2="{cy + radius * S}" stroke="#ddd"/><line x1="{cx - radius * S}" y1="{cy}" x2="{cx + radius * S}" y2="{cy}" stroke="#ddd"/>')
    for _, r in df.iterrows():
        if abs(r.east_m) > radius or abs(r.north_m) > radius: continue
        k = COL[r.zone]; el.append(f'<circle cx="{X(r.east_m):.1f}" cy="{Y(r.north_m):.1f}" r="{2 + min(max(r.climb, 0), 3) * 1.3:.1f}" fill="{k}" fill-opacity="0.45" stroke="{k}" stroke-width="0.5"/>')
    for k, z in enumerate(("west", "east")):
        el.append(f'<circle cx="{X(c[k, 0]):.1f}" cy="{Y(c[k, 1]):.1f}" r="9" fill="none" stroke="#000" stroke-width="2.5"/><text x="{X(c[k, 0]):.1f}" y="{Y(c[k, 1]) - 14:.1f}" text-anchor="middle" font-size="14" font-weight="bold">{NAME[z]}</text>')
    el.append(f'<path d="M{cx} {cy} l-7 -7 l14 0 z" fill="#000"/><text x="{cx + 10}" y="{cy + 16}" font-size="12">launch (Mystic)</text>')
    el.append(f'<text x="{W - 40}" y="26" font-size="16" font-weight="bold">N ↑</text>')
    cnt = df.zone.value_counts(); leg = " ".join(f'<b style="color:{COL[z]}">{NAME[z]} {cnt.get(z, 0)}</b>' for z in ("east", "west", "merged", "nnw") if cnt.get(z, 0))
    html = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mystic thermals</title>
<body style="font-family:system-ui,sans-serif;max-width:620px;margin:1em auto;padding:0 .5em"><h2>{title}</h2>
<p>Each dot is one thermal climb from the IGC tracks (bigger = stronger). Looking north from launch. {leg}. Black rings = group centres.</p>
<svg viewBox="0 0 {W} {W}" width="100%" style="background:#fafaf5;border:1px solid #ccc">{''.join(el)}</svg>
<p style="color:#666;font-size:.85em">No map tiles (nothing is fetched online): distances are metres from the launch point. {note}</p></body>"""
    Path(path).write_text(html)


def ascii_map(df, c, radius=930, cell=60):
    sym = {"east": "E", "west": "W", "merged": "M", "nnw": "N", "outside": "o"}
    n = int(2 * radius / cell); g = [[collections.Counter() for _ in range(n)] for _ in range(n)]
    for _, r in df.iterrows():
        i = int((radius - r.north_m) // cell); j = int((r.east_m + radius) // cell)
        if 0 <= i < n and 0 <= j < n: g[i][j][r.zone] += 1
    out = []
    for i in range(n):
        row = ""
        for j in range(n):
            ct = g[i][j]; ch = sym[ct.most_common(1)[0][0]] if ct else "."
            if abs((j + .5) * cell - radius) < cell / 2 and abs(radius - (i + .5) * cell) < cell / 2: ch = "^" if not ct else ch.lower()
            row += ch
        out.append(row)
    return "\n".join(out)


COND = [("sfc_u", "10 m wind, west-to-east component (km/h, + = from the west)"), ("sfc_v", "10 m wind, south-to-north (km/h, + = from the south)"), ("sfc_kmh", "10 m wind speed (km/h)"),
        ("w850_u", "850 hPa wind, west-to-east (km/h)"), ("w850_v", "850 hPa wind, south-to-north (km/h)"), ("w850_kmh", "850 hPa wind speed (km/h)"),
        ("hour", "hour of day (local)"), ("sun_elev", "sun elevation (deg)"), ("sun_az", "sun azimuth (deg from N)"), ("rsds", "sunshine at ground (W/m2)"), ("cloud_pct", "cloud cover (%)"),
        ("temp_c", "air temp (C)"), ("cape", "CAPE (J/kg)"), ("bl_m", "boundary layer height (m)"), ("month", "month number")]
BINS = [("sfc_sec", SECT, "10 m wind from"), ("sfc_speed", ["<3", "3-5", "5-7", "7+"], "10 m km/h"), ("w850_sec", SECT, "850 hPa from"), ("w850_speed", ["<15", "15-30", "30-45", "45+"], "850 km/h"),
        ("hour_bin", ["<11", "11-12", "12-13", "13-14", "14-15", "15+"], "local hour"), ("season", ["Dec-Feb", "Mar-May", "Jun-Aug", "Sep-Nov"], "season"),
        ("sun_bin", ["<40", "40-50", "50-60", "60+"], "sun elev deg"), ("rsds_bin", ["<400", "400-600", "600-800", "800+"], "sun W/m2")]


def add_bins(df):
    df["sfc_sec"] = [sector(x) for x in df.sfc_dir]; df["w850_sec"] = [sector(x) for x in df.w850_dir]
    df["sfc_speed"] = bins(df.sfc_kmh, [0, 3, 5, 7, 99], ["<3", "3-5", "5-7", "7+"]); df["w850_speed"] = bins(df.w850_kmh, [0, 15, 30, 45, 200], ["<15", "15-30", "30-45", "45+"])
    df["hour_bin"] = bins(df.hour, [0, 11, 12, 13, 14, 15, 24], ["<11", "11-12", "12-13", "13-14", "14-15", "15+"])
    df["sun_bin"] = bins(df.sun_elev, [0, 40, 50, 60, 90], ["<40", "40-50", "50-60", "60+"]); df["rsds_bin"] = bins(df.rsds, [0, 400, 600, 800, 2000], ["<400", "400-600", "600-800", "800+"])
    df["season"] = df.month.map(lambda m: {12: "Dec-Feb", 1: "Dec-Feb", 2: "Dec-Feb", 3: "Mar-May", 4: "Mar-May", 5: "Mar-May", 6: "Jun-Aug", 7: "Jun-Aug", 8: "Jun-Aug"}.get(m, "Sep-Nov"))
    return df


def compare(df, P, a, b, what):
    """Group a minus group b: climb, duration, gain, then every weather variable (day-resampled 95% range), then bin tables."""
    A, B = df[df.zone == a], df[df.zone == b]
    P(f"\n--- {what} ---")
    P(f"n: {a} {len(A)} ({A.date.nunique()} days), {b} {len(B)} ({B.date.nunique()} days);  days with both {len(set(A.date) & set(B.date))}")
    for col, lab, u in [("climb", "mean climb", "m/s"), ("dur", "duration", "s"), ("gain", "height gain", "m"), ("turns", "turns", "")]:
        r, lo, hi = day_bootstrap(df, col, a=a, b=b); P(f"  {lab:<12} {a} {A[col].mean():6.2f} | {b} {B[col].mean():6.2f} | {a} minus {b} {r:+.2f} ({lo:+.2f} to {hi:+.2f}) {u} {'*' if lo > 0 or hi < 0 else ''}")
    P(f"  median climb: {a} {A.climb.median():.2f}, {b} {B.climb.median():.2f}; best: {a} {A.climb.max():.1f}, {b} {B.climb.max():.1f}")
    P(f"\nConditions, {a} minus {b} (95% range from resampling days; * = range excludes zero):")
    for col, lab in COND:
        r, lo, hi = day_bootstrap(df, col, n=1000, a=a, b=b); P(f"  {lab:<62}{r:+8.2f}  ({lo:+.2f} to {hi:+.2f}) {'*' if lo > 0 or hi < 0 else ''}")
    d2 = df[df.zone.isin([a, b])]
    for key, order, lab in BINS: P(f"\n{table(d2, key, order, lab, a=a, b=b)}")


def ring_main(df, c, out, a):
    df = add_bins(df); df.drop(columns=["t_utc"]).round(2).to_csv(out / "thermals.csv", index=False)
    L = []; P = L.append; z = df.zone.value_counts(); n_in = int(z.get("merged", 0) + z.get("east", 0) + z.get("west", 0))
    P(f"Climbs in the first {a.minutes:g} min of Mystic flights within 1500 m of launch: {len(df)} (with BARRA-C2 weather).")
    P(f"  NNW spot (within 200 m of 235 m W / 368 m N, beyond 250 m): {int(z.get('nnw', 0))}  -> excluded;  other climbs beyond 250 m: {int(z.get('outside', 0))}  -> excluded")
    P(f"  Within 250 m of launch: {n_in} on {df[df.zone.isin(['merged', 'east', 'west'])].date.nunique()} days")
    P(f"     merged over launch (within {MERGE_M:g} m): {int(z.get('merged', 0))};  ring {MERGE_M:g}-{RING_M:g} m: {int(z.get('east', 0) + z.get('west', 0))}")
    W_, E_ = c[0], c[1]; sep = math.hypot(*(E_ - W_)); brg = lambda v: (math.degrees(math.atan2(v[0], v[1])) % 360)
    P(f"\nRING, free 2-D clustering into two groups:")
    P(f"  group A (the more westerly centre): n={int(z.get('west', 0))}, centre {W_[0]:+.0f} m E / {W_[1]:+.0f} m N, {math.hypot(*W_):.0f} m from launch, bearing {brg(W_):.0f} deg ({sector(brg(W_))})")
    P(f"  group B (the more easterly centre): n={int(z.get('east', 0))}, centre {E_[0]:+.0f} m E / {E_[1]:+.0f} m N, {math.hypot(*E_):.0f} m from launch, bearing {brg(E_):.0f} deg ({sector(brg(E_))})")
    ang = brg(E_ - W_); P(f"  centres are {sep:.0f} m apart; the line from A to B points {ang:.0f} deg ({sector(ang)}); a clean east-west split would be about 90 deg")
    ew = (W_[0] < 0 < E_[0]) and 55 <= ang <= 125
    P(f"  -> come out as west and east of launch? {'YES' if ew else 'NO / NOT CLEANLY'}  (A west of launch: {W_[0] < 0}; B east of launch: {E_[0] > 0})")
    rg = df[df.zone.isin(['east', 'west'])]; P(f"  ambiguous ring climbs (nearly as close to the other centre): {int(rg.ambiguous.sum())} of {len(rg)}")
    P(f"  separation check, east offset of ring climbs: A mean {rg[rg.zone == 'west'].east_m.mean():+.0f} m (sd {rg[rg.zone == 'west'].east_m.std():.0f}), B mean {rg[rg.zone == 'east'].east_m.mean():+.0f} m (sd {rg[rg.zone == 'east'].east_m.std():.0f})")
    pure = rg.east_m > 0; P(f"  for comparison, plain left/right of launch (east offset sign): {int(pure.sum())} east / {int((~pure).sum())} west; the clustering agrees with that for {100 * ((rg.zone == 'east') == pure).mean():.0f}% of ring climbs")
    for g, nm in (("west", "A"), ("east", "B")):
        n = int((rg.zone == g).sum()); d = rg[rg.zone == g].date.nunique()
        P(f"  group {nm}: {n} climbs on {d} days -> " + ("TOO FEW to say much (under 30)" if n < 30 else "enough only for large effects (30-100)" if n < 100 else "reasonable"))
    make_map(df, c, out / "map.html", 450, rings=(50, 250, 450), title="Mystic: ring climbs (50-250 m) and merged-over-launch (under 50 m)",
             note=f"Two groups in the 50-250 m ring come from 2-D clustering. Centres: A {W_[0]:+.0f} m E {W_[1]:+.0f} m N; B {E_[0]:+.0f} m E {E_[1]:+.0f} m N; {sep:.0f} m apart. Grey = NNW spot, excluded. Rings at 50, 250 and 450 m.")
    compare(df, P, "east", "west", "RING GROUPS (B = east-most centre, A = west-most centre; displayed as 'east'/'west' in the tables)")
    inn = df[df.zone.isin(["merged", "east", "west"])].copy(); inn["zone"] = inn.zone.where(inn.zone == "merged", "ring")
    P(f"\n=== MERGED OVER LAUNCH (within {MERGE_M:g} m) versus the ring ===")
    M, R = inn[inn.zone == "merged"], inn[inn.zone == "ring"]
    fl = lambda x: x.file.nunique()
    P(f"how often: {len(M)} of {len(inn)} climbs within 250 m ({100 * len(M) / len(inn):.0f}%), on {M.date.nunique()} of {inn.date.nunique()} days, in {fl(M)} of {fl(inn)} flights")
    P(f"  (launch position error is of the order of 30-40 m, so the 50 m zone is a fuzzy edge)")
    compare(inn, P, "merged", "ring", "MERGED vs RING (merged %, in the tables = share of within-250 m climbs that were merged)")
    P(f"\nMap of within-250 m climbs plus the NNW spot (20 m cells, north up; E = east group, W = west group, M = merged, N = NNW spot, lowercase = launch cell, ^ = launch):\n" + ascii_map(df[df.zone != "outside"], c, radius=410, cell=20))
    (out / "report.txt").write_text("\n".join(L)); print("\n".join(L)); print(f"\nwritten: {out}/report.txt, map.html, thermals.csv")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=15); ap.add_argument("--radius", type=float, default=1500)
    ap.add_argument("--split", choices=["ew", "2d", "ring"], default="ew", help="ew = east/west by east offset only; 2d = free 2-D clustering; ring = 50 m merged zone + 50-250 m ring split in 2-D, NNW spot excluded")
    ap.add_argument("--pilot", default="pilot1", help="pilot folder in igc_files/ (Emily and Marcus are both in pilot1's tracks), or 'all'")
    a = ap.parse_args()
    out = ROOT / "data" / "thermal_zones" / a.split; out.mkdir(parents=True, exist_ok=True)
    df, c = build(a.minutes, a.radius, a.split, a.pilot)
    df.drop(columns=["t_utc"]).round(2).to_csv(out / "thermals.csv", index=False)
    if a.split == "ring": return ring_main(df, c, out, a)
    make_map(df, c, out / "map.html", a.radius, title=f"Mystic: where the climbs are (first {a.minutes:g} min, within {a.radius / 1000:g} km)", note=f"Centres: west {c[0, 0]:+.0f} m E, {c[0, 1]:+.0f} m N; east {c[1, 0]:+.0f} m E, {c[1, 1]:+.0f} m N.")
    L = []; P = L.append
    e, w = df[df.zone == "east"], df[df.zone == "west"]
    P(f"Climbs within {a.minutes:g} min of launch and {a.radius:g} m: {len(df)} on {df.date.nunique()} days (with BARRA-C2 weather).")
    P(f"WEST (Marcus): n={len(w)}, centre {c[0, 0]:+.0f} m E / {c[0, 1]:+.0f} m N of launch;  EAST (Emily): n={len(e)}, centre {c[1, 0]:+.0f} m E / {c[1, 1]:+.0f} m N")
    P(f"Ambiguous (nearly as close to the other centre): {int(df.ambiguous.sum())} of {len(df)}")
    P(f"Days with both: {len(set(e.date) & set(w.date))}, east only: {len(set(e.date) - set(w.date))}, west only: {len(set(w.date) - set(e.date))}")
    P(f"\nMean climb (m/s): east {e.climb.mean():.2f} (median {e.climb.median():.2f}), west {w.climb.mean():.2f} (median {w.climb.median():.2f}).  Duration: east {e.dur.mean():.0f} s, west {w.dur.mean():.0f} s.  Gain: east {e.gain.mean():.0f} m, west {w.gain.mean():.0f} m.")
    b, lo, hi = day_bootstrap(df, "climb"); P(f"East minus west climb: {b:+.2f} m/s (95% range {lo:+.2f} to {hi:+.2f}, resampling days)")
    P("\nEast minus west difference in conditions (95% range from resampling days; * = range excludes zero):")
    for col, lab in [("sfc_u", "10 m wind, west-to-east component (km/h, + = from the west)"), ("sfc_v", "10 m wind, south-to-north (km/h, + = from the south)"), ("sfc_kmh", "10 m wind speed (km/h)"),
                     ("w850_u", "850 hPa wind, west-to-east (km/h)"), ("w850_v", "850 hPa wind, south-to-north (km/h)"), ("w850_kmh", "850 hPa wind speed (km/h)"),
                     ("hour", "hour of day (local)"), ("sun_elev", "sun elevation (deg)"), ("sun_az", "sun azimuth (deg from N)"), ("rsds", "sunshine at ground (W/m2)"), ("cloud_pct", "cloud cover (%)"),
                     ("temp_c", "10 m-ish air temp (C)"), ("cape", "CAPE (J/kg)"), ("bl_m", "boundary layer height (m)"), ("month", "month number")]:
        b, lo, hi = day_bootstrap(df, col, n=1000); P(f"  {lab:<62}{b:+8.2f}  ({lo:+.2f} to {hi:+.2f}) {'*' if lo > 0 or hi < 0 else ''}")
    df["sfc_sec"] = [sector(x) for x in df.sfc_dir]; df["w850_sec"] = [sector(x) for x in df.w850_dir]
    df["sfc_speed"] = bins(df.sfc_kmh, [0, 3, 5, 7, 99], ["<3", "3-5", "5-7", "7+"]); df["w850_speed"] = bins(df.w850_kmh, [0, 15, 30, 45, 200], ["<15", "15-30", "30-45", "45+"])
    df["hour_bin"] = bins(df.hour, [0, 11, 12, 13, 14, 15, 24], ["<11", "11-12", "12-13", "13-14", "14-15", "15+"])
    df["sun_bin"] = bins(df.sun_elev, [0, 40, 50, 60, 90], ["<40", "40-50", "50-60", "60+"])
    df["rsds_bin"] = bins(df.rsds, [0, 400, 600, 800, 2000], ["<400", "400-600", "600-800", "800+"])
    df["season"] = df.month.map(lambda m: {12: "Dec-Feb", 1: "Dec-Feb", 2: "Dec-Feb", 3: "Mar-May", 4: "Mar-May", 5: "Mar-May", 6: "Jun-Aug", 7: "Jun-Aug", 8: "Jun-Aug"}.get(m, "Sep-Nov"))
    for key, order, lab in [("sfc_sec", SECT, "10 m wind from"), ("sfc_speed", ["<3", "3-5", "5-7", "7+"], "10 m km/h"), ("w850_sec", SECT, "850 hPa from"), ("w850_speed", ["<15", "15-30", "30-45", "45+"], "850 km/h"),
                            ("hour_bin", ["<11", "11-12", "12-13", "13-14", "14-15", "15+"], "local hour"), ("month", list(range(1, 13)), "month"), ("season", ["Dec-Feb", "Mar-May", "Jun-Aug", "Sep-Nov"], "season"),
                            ("sun_bin", ["<40", "40-50", "50-60", "60+"], "sun elev deg"), ("rsds_bin", ["<400", "400-600", "600-800", "800+"], "sun W/m2")]:
        P(f"\n{table(df, key, order, lab)}")
    P("\nMap of climbs (60 m cells, north up, launch in the middle: E = east group, W = west group, + = tie, lowercase = launch cell):\n" + ascii_map(df, c))
    (out / "report.txt").write_text("\n".join(L)); print("\n".join(L)); print(f"\nwritten: {out}/report.txt, map.html, thermals.csv")


if __name__ == "__main__": main()
