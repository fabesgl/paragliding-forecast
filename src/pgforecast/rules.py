"""Hand-written rules: turn one model's hourly forecast for one day into XC % and acro %.
Not learned from data, not calibrated. The numbers are an honest first guess for Mystic."""
import math

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None

def _ramp(x, lo, hi):            # 0 at/below lo, 1 at/above hi
    return None if x is None else max(0.0, min(1.0, (x - lo) / (hi - lo)))

def _band(x, a, b, c, d):        # 0 below a, rises to 1 at b, flat to c, falls to 0 at d
    if x is None: return None
    if x <= a or x >= d: return 0.0
    if x < b: return (x - a) / (b - a)
    if x <= c: return 1.0
    return (d - x) / (d - c)

def _in_arc(deg, arc):           # arc = (from, to) clockwise, may wrap through 360
    a, b = arc
    return (a <= deg <= b) if a <= b else (deg >= a or deg <= b)

def _vec_mean_dir(speeds, dirs):
    pairs = [(s, d) for s, d in zip(speeds, dirs) if s is not None and d is not None]
    if not pairs: return None
    u = sum(-s * math.sin(math.radians(d)) for s, d in pairs); v = sum(-s * math.cos(math.radians(d)) for s, d in pairs)
    return (math.degrees(math.atan2(-u, -v))) % 360

COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
def compass(d): return None if d is None else COMPASS[int(((d % 360) + 11.25) // 22.5) % 16]

def _smooth(t):                       # 0 -> 0, 1 -> 1, smooth in between
    t = max(0.0, min(1.0, t)); return t * t * (3 - 2 * t)

def below(x, limit, zero):
    """Rule 'x must be under limit'. About 0.9 at the limit, 1 well under it, falls smoothly to 0 at `zero`."""
    if x is None: return None
    a = limit - 0.25 * (zero - limit)
    return 1 - _smooth((x - a) / (zero - a))

def above(x, thr, zero):
    """Rule 'x must be above thr'. About 0.9 at thr, 1 well above it, falls smoothly to 0 at `zero`."""
    if x is None: return None
    b = thr + 0.25 * (thr - zero)
    return _smooth((x - zero) / (b - zero))

def _hourly(h, name): return h.get(name) or []

def _window_features(h, launch_elev):
    n = len(h["wind_speed_10m"])
    def col(name): 
        c = _hourly(h, name); return c if len(c) == n else [None] * n
    ws, wd, gu = col("wind_speed_10m"), col("wind_direction_10m"), col("wind_gusts_10m")
    w8, d8, w7 = col("wind_speed_850hPa"), col("wind_direction_850hPa"), col("wind_speed_700hPa")
    t, td, blh, sw = col("temperature_2m"), col("dew_point_2m"), col("boundary_layer_height"), col("shortwave_radiation")
    shear, ceil, therm = [], [], []
    for i in range(n):
        if None not in (ws[i], wd[i], w8[i], d8[i]):          # vector wind difference, 10 m vs 850 hPa
            u1, v1 = -ws[i]*math.sin(math.radians(wd[i])), -ws[i]*math.cos(math.radians(wd[i]))
            u2, v2 = -w8[i]*math.sin(math.radians(d8[i])), -w8[i]*math.cos(math.radians(d8[i]))
            shear.append(math.hypot(u2 - u1, v2 - v1))
        cb = 125 * (t[i] - td[i]) if None not in (t[i], td[i]) else None          # cloud base, m above ground
        if cb is not None:
            top = cb if blh[i] is None else min(cb, blh[i])                       # dry thermal top if lower than cloud
            ceil.append(launch_elev + top)
        if sw[i] is not None and (blh[i] is not None or cb is not None):
            zi = blh[i] if blh[i] is not None else min(cb, 2500)
            H = 0.3 * max(sw[i], 0) / 1200.0                                      # sensible heat flux, K m/s (rough: 30% of sunshine)
            therm.append(0.7 * (9.81 / 300.0 * H * max(zi, 0)) ** (1 / 3))        # typical climb ~ 0.7 x Deardorff w*
    return dict(ws=_mean(ws), wd=_vec_mean_dir(ws, wd), gust=max([g for g in gu if g is not None], default=None),
                w850=_mean(w8), d850=_vec_mean_dir(w8, d8), w700=_mean(w7), shear=_mean(shear),
                cb=_mean([125*(a-b) for a, b in zip(t, td) if a is not None and b is not None]),
                ceiling=_mean(ceil), blh=_mean(blh), sw=_mean(sw), therm=_mean(therm),
                cape=_mean(col("cape")), low=_mean(col("cloud_cover_low")),
                rain=(sum(x for x in col("precipitation") if x is not None) if any(x is not None for x in col("precipitation")) else None),
                pairs=[(s_, d_) for s_, d_ in zip(ws, wd) if s_ is not None and d_ is not None])

def score_day(h, launch_arc=(315, 45), bad_arc=(135, 247), launch_elev=797):
    """h: dict of variable -> hourly list for the flying window. Returns XC %, acro %, and the rule factors.
    Every rule gives a factor from 0 to 1; the score is 100 x the LOWEST factor (weakest link: the worst rule sets the score)."""
    f = _window_features(h, launch_elev)
    in_arc = _mean([1.0 if (_in_arc(d, launch_arc) or s < 6) else 0.0 for s, d in f["pairs"]])
    syn_bad = f["w850"] is not None and f["d850"] is not None and _in_arc(f["d850"], bad_arc) and f["w850"] > 25
    direction = 0.0 if syn_bad else (in_arc if in_arc is not None else 1.0)
    dtxt = (f"strong {compass(f['d850'])} wind aloft" if syn_bad else f"wind {compass(f['wd'])} at launch" if f["wd"] is not None else "no data")
    u = lambda x, unit, nd=0: "n/a" if x is None else f"{x:.{nd}f}{unit}"
    shared = [("direction", "Wind direction", (None, "", 0, dtxt), direction)]
    rain = ("rain", "Rain in window", (f["rain"], " mm", 1, " (want none)"), below(f["rain"], 0.2, 2.0))
    xc = shared + [
        ("wind", "Launch wind", (f["ws"], " km/h", 0, " (want <20)"), below(f["ws"], 20, 32)),
        ("gust", "Gusts", (f["gust"], " km/h", 0, " (want <25)"), below(f["gust"], 25, 40)),
        ("w850", "850 hPa wind", (f["w850"], " km/h", 0, " (want <25)"), below(f["w850"], 25, 45)),
        ("ceiling", "Cloud base / thermal top", (f["ceiling"], " m ASL", 0, " (want >2000)"), above(f["ceiling"], 2000, 1300)),
        ("thermals", "Thermal strength (est.)", (f["therm"], " m/s", 1, " (want >1.5)"), above(f["therm"], 1.5, 0.5)),
        ("cape", "CAPE (overdevelopment)", (f["cape"], "", 0, " (want <800)"), below(f["cape"], 800, 1800)), rain]
    acro = shared + [
        ("wind", "Launch wind", (f["ws"], " km/h", 0, " (want <15)"), below(f["ws"], 15, 25)),
        ("gust", "Gusts", (f["gust"], " km/h", 0, " (want <20)"), below(f["gust"], 20, 32)),
        ("w850", "850 hPa wind", (f["w850"], " km/h", 0, " (want <20)"), below(f["w850"], 20, 38)),
        ("shear", "Wind shear 10 m to 850 hPa", (f["shear"], " km/h", 0, " (want <15)"), below(f["shear"], 15, 35)),
        ("cape", "CAPE (overdevelopment)", (f["cape"], "", 0, " (want <800)"), below(f["cape"], 800, 1800)), rain]
    def total(rules):
        got = [r[3] for r in rules if r[3] is not None]
        return None if not got else min(got)
    pct = lambda x: None if x is None else round(100 * max(0, min(1, x)))
    pack = lambda rules: [dict(key=k, label=l, num=v[0], unit=v[1], nd=v[2], want=v[3], factor=None if x is None else round(x, 3)) for k, l, v, x in rules]
    key = dict(wind_kmh=f["ws"], wind_dir=compass(f["wd"]), gust_kmh=f["gust"], wind850_kmh=f["w850"], wind850_dir=compass(f["d850"]),
               wind700_kmh=f["w700"], shear_kmh=f["shear"], ceiling_asl_m=f["ceiling"], cloudbase_agl_m=f["cb"], blh_m=f["blh"],
               thermal_ms=f["therm"], sw_wm2=f["sw"], cape=f["cape"], rain_mm=f["rain"], low_cloud_pct=f["low"])
    return dict(xc=pct(total(xc)), acro=pct(total(acro)), key=key, xc_rules=pack(xc), acro_rules=pack(acro))
