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

def weighted(parts):
    """parts: list of (weight, value or None). Missing values are skipped and weights renormalised."""
    got = [(w, v) for w, v in parts if v is not None]
    return sum(w * v for w, v in got) / sum(w for w, _ in got) if got else None

def score_day(h, launch_arc=(315, 45), bad_arc=(135, 247)):
    """h: dict of variable -> list of hourly values for the flying window (11:00-16:00 local)."""
    ws, wd, gust = _mean(h["wind_speed_10m"]), _vec_mean_dir(h["wind_speed_10m"], h["wind_direction_10m"]), max([g for g in h["wind_gusts_10m"] if g is not None], default=None)
    w850, d850 = _mean(h.get("wind_speed_850hPa", [])), _vec_mean_dir(h.get("wind_speed_850hPa", []), h.get("wind_direction_850hPa", []))
    w700 = _mean(h.get("wind_speed_700hPa", []))
    t, td = _mean(h["temperature_2m"]), _mean(h["dew_point_2m"])
    cloudbase = 125 * (t - td) if t is not None and td is not None else None   # m above ground
    blh, sw = _mean(h.get("boundary_layer_height", [])), _mean(h.get("shortwave_radiation", []))
    cape, low = _mean(h.get("cape", [])), _mean(h.get("cloud_cover_low", []))
    rain = sum(x for x in h["precipitation"] if x is not None) if any(x is not None for x in h["precipitation"]) else None

    # direction: share of window hours with 10 m wind inside the launchable arc
    pairs = [(s, d) for s, d in zip(h["wind_speed_10m"], h["wind_direction_10m"]) if d is not None and s is not None]
    in_arc = _mean([1.0 if (_in_arc(d, launch_arc) or s < 6) else 0.0 for s, d in pairs])
    # strong synoptic wind from S/SE/SW at 850 hPa kills the day
    synoptic_bad = 0.0 if (w850 is not None and d850 is not None and _in_arc(d850, bad_arc) and w850 > 25) else 1.0
    launch = (in_arc if in_arc is not None else 0.5) * synoptic_bad
    comfort = _band(ws, 2, 8, 20, 32)
    gustf = 1 - (_ramp(gust, 30, 50) or 0)
    upper = 1 - (_ramp(w700, 30, 55) or 0) if w700 is not None else None
    base = _ramp(cloudbase, 800, 2200)
    thermal = weighted([(2, _ramp(blh, 600, 2200)), (1, _ramp(sw, 250, 750))])
    sun = None if low is None else 1 - _ramp(low, 30, 90)
    wet = 1.0 if rain is None else 1 - _ramp(rain, 0.3, 3.0)
    storm = 1 - 0.4 * (_ramp(cape, 1000, 2500) or 0)

    xc = weighted([(0.30, thermal), (0.25, base), (0.15, comfort), (0.10, upper), (0.10, sun), (0.10, gustf)])
    xc = None if xc is None else xc * launch * wet * storm
    calm_air = None if thermal is None else 1 - 0.7 * thermal
    smooth = weighted([(0.35, _band(ws, 4, 10, 20, 28)), (0.25, gustf), (0.20, calm_air), (0.10, upper), (0.10, comfort)])
    acro = None if smooth is None else smooth * launch * wet * storm

    why = []
    if synoptic_bad == 0: why.append(f"Strong {compass(d850)} wind aloft ({w850:.0f} km/h at 850 hPa): unflyable")
    elif in_arc is not None and in_arc < 0.5: why.append(f"Wind {compass(wd)} at launch: wrong direction for Mystic")
    elif ws is not None: why.append(f"Launch wind {compass(wd)} {ws:.0f} km/h, gusts to {gust:.0f}" if gust is not None else f"Launch wind {compass(wd)} {ws:.0f} km/h")
    if rain is not None and rain >= 0.3: why.append(f"Rain {rain:.1f} mm in the flying window")
    if cloudbase is not None: why.append(f"Cloud base ~{cloudbase:.0f} m above ground ({'good' if cloudbase > 1700 else 'low' if cloudbase < 1200 else 'ok'})")
    if blh is not None: why.append(f"Boundary layer ~{blh:.0f} m ({'strong' if blh > 1800 else 'weak' if blh < 1000 else 'moderate'} thermals)")
    elif sw is not None: why.append(f"Sunshine {sw:.0f} W/m2 ({'strong' if sw > 600 else 'weak'} heating)")
    if cape is not None and cape > 1500: why.append(f"CAPE {cape:.0f}: overdevelopment risk")
    if gust is not None and gust > 35: why.append(f"Gusty ({gust:.0f} km/h)")
    key = dict(wind_kmh=ws, wind_dir=compass(wd), gust_kmh=gust, wind850_kmh=w850, wind850_dir=compass(d850),
               wind700_kmh=w700, cloudbase_agl_m=cloudbase, blh_m=blh, sw_wm2=sw, cape=cape, rain_mm=rain, low_cloud_pct=low)
    pct = lambda x: None if x is None else round(100 * max(0, min(1, x)))
    return dict(xc=pct(xc), acro=pct(acro), key=key, why=why[:4])
