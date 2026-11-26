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

def below(x, limit, zero, margin=0.25):
    """Rule 'x must be under limit'. About 0.9 at the limit, 1 well under it, falls smoothly to 0 at `zero`."""
    if x is None: return None
    a = limit - margin * (zero - limit)
    return 1 - _smooth((x - a) / (zero - a))

def above(x, thr, zero, margin=0.25):
    """Rule 'x must be above thr'. About 0.9 at thr, 1 well above it, falls smoothly to 0 at `zero`."""
    if x is None: return None
    b = thr + margin * (thr - zero)
    return _smooth((x - zero) / (b - zero))

COMPASS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

def west_weight(deg, weights):
    """How much a wind FROM `deg` counts as 'Buffalo turbulence' (0-1). The 16 compass points carry the configured weights (others 0);
    in between, the weight blends smoothly, so there is no cliff edge."""
    pts = [float(weights.get(c, 0.0)) for c in COMPASS16]; x = (deg % 360) / 22.5; i = int(x) % 16; f = x - int(x)
    return pts[i] * (1 - f) + pts[(i + 1) % 16] * f

def north_factor(n_kmh, gain_m, c):
    """Mild penalty for wind from the north at acro height (it drifts you back towards the hill). c = acro.northerly_aloft settings.
    Never below 1 - max_penalty. Bigger when climbs are low, smaller when there is plenty of working height."""
    if n_kmh is None: return None
    strength = _smooth((n_kmh - c["limit_kmh"]) / (c["strong_kmh"] - c["limit_kmh"]))
    share = 1.0 if gain_m is None else 1 - (1 - c["penalty_share_when_high"]) * _smooth((gain_m - c["low_climb_above_launch_m"]) / (c["high_climb_above_launch_m"] - c["low_climb_above_launch_m"]))
    return 1 - c["max_penalty"] * strength * share

def _aloft(h, levels, weights):
    """Wind at acro height from the configured pressure levels: per hour, the average wind vector (for the northerly part) and the worst
    weighted westerly level. Launch (10 m) wind is NOT used. Returns window averages, or Nones when no level has data."""
    n = len(h["wind_speed_10m"]); norths, wests, us, vs = [], [], [], []
    for i in range(n):
        lv = []
        for L in levels:
            sp, dr = _hourly(h, f"wind_speed_{L}hPa"), _hourly(h, f"wind_direction_{L}hPa")
            if len(sp) == n and len(dr) == n and sp[i] is not None and dr[i] is not None: lv.append((sp[i], dr[i]))
        if not lv: continue
        u = sum(-s_ * math.sin(math.radians(d_)) for s_, d_ in lv) / len(lv); v = sum(-s_ * math.cos(math.radians(d_)) for s_, d_ in lv) / len(lv)
        us.append(u); vs.append(v); norths.append(max(0.0, -v)); wests.append(max(s_ * west_weight(d_, weights) for s_, d_ in lv))
    if not us: return dict(north=None, west=None, u=None, v=None, speed=None)
    u, v = _mean(us), _mean(vs)
    return dict(north=_mean(norths), west=_mean(wests), u=u, v=v, speed=math.hypot(u, v))

def _hourly(h, name): return h.get(name) or []

def rain_factor(x, limit, zero, margin):
    """Rain rule: exactly no rain scores 100%. The score eases down to the usual value at `limit` (about 90%), then follows the normal fade to 0 at `zero`."""
    if x is None: return None
    if x >= limit: return below(x, limit, zero, margin)
    at_limit = below(limit, limit, zero, margin)
    return 1 - (1 - at_limit) * max(0.0, x) / limit

def _window_features(h, launch_elev, est):
    """est = the 'estimates' section of the site's rules.yaml."""
    cbm, heat, climb, cap = est["cloud_base_m_per_degC"], est["thermal_heat_fraction"], est["thermal_climb_fraction"], est["thermal_depth_cap_m"]
    n = len(h["wind_speed_10m"])
    def col(name): 
        c = _hourly(h, name); return c if len(c) == n else [None] * n
    ws, wd, gu = col("wind_speed_10m"), col("wind_direction_10m"), col("wind_gusts_10m")
    w8, d8, w7 = col("wind_speed_850hPa"), col("wind_direction_850hPa"), col("wind_speed_700hPa")
    t, td, blh, sw = col("temperature_2m"), col("dew_point_2m"), col("boundary_layer_height"), col("shortwave_radiation")
    shear, ceil, therm, therm_h = [], [], [], [None] * n
    for i in range(n):
        if None not in (ws[i], wd[i], w8[i], d8[i]):          # vector wind difference, 10 m vs 850 hPa
            u1, v1 = -ws[i]*math.sin(math.radians(wd[i])), -ws[i]*math.cos(math.radians(wd[i]))
            u2, v2 = -w8[i]*math.sin(math.radians(d8[i])), -w8[i]*math.cos(math.radians(d8[i]))
            shear.append(math.hypot(u2 - u1, v2 - v1))
        cb = cbm * (t[i] - td[i]) if None not in (t[i], td[i]) else None          # cloud base, m above ground
        if cb is not None:
            top = cb if blh[i] is None else min(cb, blh[i])                       # dry thermal top if lower than cloud
            ceil.append(launch_elev + top)
        if sw[i] is not None and (blh[i] is not None or cb is not None):
            zi = blh[i] if blh[i] is not None else min(cb, cap)
            H = heat * max(sw[i], 0) / 1200.0                                     # sensible heat flux, K m/s (rough: a share of the sunshine)
            therm.append(climb * (9.81 / 300.0 * H * max(zi, 0)) ** (1 / 3)); therm_h[i] = therm[-1]      # typical climb ~ a share of Deardorff w*
    return dict(ws=_mean(ws), wd=_vec_mean_dir(ws, wd), gust=max([g for g in gu if g is not None], default=None),
                w850=_mean(w8), d850=_vec_mean_dir(w8, d8), w700=_mean(w7), shear=_mean(shear),
                cb=_mean([cbm*(a-b) for a, b in zip(t, td) if a is not None and b is not None]),
                ceiling=_mean(ceil), blh=_mean(blh), sw=_mean(sw), therm=_mean(therm),
                cape=_mean(col("cape")), low=_mean(col("cloud_cover_low")),
                rain=(sum(x for x in col("precipitation") if x is not None) if any(x is not None for x in col("precipitation")) else None),
                pairs=[(s_, d_) for s_, d_ in zip(ws, wd) if s_ is not None and d_ is not None],
                hours=list(zip(ws, wd, therm_h)))

def acro_window_score(acro_pct, key, rules):
    """One hour's acro score for the 'Acro window' strip only: the normal hourly acro score, capped by the thermal-strength and climb-height checks (rules.yaml acro_window)."""
    if acro_pct is None: return None
    w, mg = rules["acro_window"], rules["smoothing"]["full_score_margin"]
    th, cl = w["thermals"], w["climb"]
    fs = [x for x in (above(key.get("thermal_ms"), th["min_ms"], th["zero_at_ms"], mg),
                      above(key.get("climb_gain_m"), cl["min_above_launch_m"], cl["zero_at_above_launch_m"], mg)) if x is not None]
    return acro_pct if not fs else round(min(acro_pct, 100 * max(0, min(1, min(fs)))))

def score_day(h, rules, launch_elev):
    """h: dict of variable -> hourly list for the flying window. rules: the site's settings (rules_config.load_rules).
    Returns XC %, acro %, and the rule factors.
    Every rule gives a factor from 0 to 1; the score is 100 x the LOWEST factor (weakest link: the worst rule sets the score)."""
    d, sh, xr, ar, mg = rules["direction"], rules["shared"], rules["xc"], rules["acro"], rules["smoothing"]["full_score_margin"]
    f = _window_features(h, launch_elev, rules["estimates"])
    sl = d["southerly_launch"]; k_valley, fade = sl["valley_wind_kmh_per_thermal_ms"], sl["fade_kmh"]
    wl = d.get("westerly_launch")                                    # optional, XC only: a westerly at launch is OK when the valley wind beats it, like a southerly
    syn_bad = f["w850"] is not None and f["d850"] is not None and _in_arc(f["d850"], tuple(d["unflyable_arc_deg"])) and f["w850"] > d["unflyable_min_850_kmh"]
    def direction_rule(westerly):
        """(factor, text). 100% when the wind is in the launch arc or near calm. A southerly (and, if `westerly`, a W/WNW wind) is OK when the (estimated)
        thermic valley wind beats it, fading over +/- fade km/h. Else 0%. A strong southerly aloft stays a no-go whatever the valley wind does."""
        valley_ok = False
        def hour_direction(s_, dd, th):
            nonlocal valley_ok
            if _in_arc(dd, tuple(d["launch_arc_deg"])) or s_ < d["calm_below_kmh"]: return 1.0
            if _in_arc(dd, tuple(sl["arc_deg"])) or (westerly and wl and _in_arc(dd, tuple(wl["arc_deg"]))):
                x = _smooth((k_valley * (th or 0.0) - s_ + fade) / (2 * fade)); valley_ok |= x > 0.5; return x
            return 0.0
        in_arc = _mean([hour_direction(s_, dd, th) for s_, dd, th in f["hours"] if s_ is not None and dd is not None])
        value = 0.0 if syn_bad else (in_arc if in_arc is not None else 1.0)
        text = (f"strong {compass(f['d850'])} wind aloft" if syn_bad else f"wind {compass(f['wd'])} at launch, valley wind should win" if valley_ok and f["wd"] is not None
                else f"wind {compass(f['wd'])} at launch" if f["wd"] is not None else "no data")
        return value, text
    dir_xc, txt_xc = direction_rule(True); dir_acro, txt_acro = direction_rule(False)
    g = lambda x: f"{x:g}"                                           # 20 -> '20', 1.5 -> '1.5'
    under = lambda x, v: below(x, v[0], v[1], mg)
    direction_row = lambda v, t: ("direction", "Wind direction", (None, "", 0, t), v)
    rain = ("rain", "Rain in window", (f["rain"], " mm", 1, " (want none)"), rain_factor(f["rain"], sh["rain"]["limit_mm"], sh["rain"]["zero_at_mm"], mg))
    cape = ("cape", "CAPE (overdevelopment)", (f["cape"], "", 0, f" (want <{g(sh['cape']['limit'])})"), under(f["cape"], (sh["cape"]["limit"], sh["cape"]["zero_at"])))
    def kmh(key, label, val, sec, name):
        c = sec[name]; return (key, label, (val, " km/h", 0, f" (want <{g(c['limit_kmh'])})"), under(val, (c["limit_kmh"], c["zero_at_kmh"])))
    ce, th = xr["ceiling"], xr["thermals"]
    xc = [direction_row(dir_xc, txt_xc)] + [
        kmh("wind", "Launch wind", f["ws"], xr, "launch_wind"), kmh("gust", "Gusts", f["gust"], xr, "gusts"), kmh("w850", "850 hPa wind", f["w850"], xr, "wind_850"),
        ("ceiling", "Cloud base / thermal top", (f["ceiling"], " m ASL", 0, f" (want >{g(ce['min_asl_m'])})"), above(f["ceiling"], ce["min_asl_m"], ce["zero_at_asl_m"], mg)),
        ("thermals", "Thermal strength (est.)", (f["therm"], " m/s", 1, f" (want >{g(th['min_ms'])})"), above(f["therm"], th["min_ms"], th["zero_at_ms"], mg)),
        cape, rain]
    an, aw, ast, ah = ar["northerly_aloft"], ar["westerly_aloft"], ar["strong_thermals"], ar["height"]
    al = _aloft(h, ar["aloft"]["levels_hpa"], aw["weights"]); gain = None if f["ceiling"] is None else f["ceiling"] - launch_elev
    soft = lambda x, lim, zero, fl: None if x is None else max(fl, below(x, lim, zero, mg))
    acro = [direction_row(dir_acro, txt_acro)] + [
        kmh("wind", "Launch wind", f["ws"], ar, "launch_wind"), kmh("gust", "Gusts", f["gust"], ar, "gusts"), kmh("w850", "850 hPa wind", f["w850"], ar, "wind_850"),
        kmh("shear", "Wind shear 10 m to 850 hPa", f["shear"], ar, "shear"),
        ("north_aloft", "Northerly wind aloft", (al["north"], " km/h", 0, f" (mild penalty above {g(an['limit_kmh'])})"), north_factor(al["north"], gain, an)),
        ("west_aloft", "Westerly wind aloft (Buffalo)", (al["west"], " km/h", 0, f" (want <{g(aw['limit_kmh'])})"), soft(al["west"], aw["limit_kmh"], aw["zero_at_kmh"], aw["floor"])),
        ("thermals_strong", "Very strong thermals (est.)", (f["therm"], " m/s", 1, f" (want <{g(ast['limit_ms'])})"), soft(f["therm"], ast["limit_ms"], ast["zero_at_ms"], ast["floor"])),
        ("height", "Climb height above launch", (gain, " m", 0, f" (want >{g(ah['min_above_launch_m'])})"), above(gain, ah["min_above_launch_m"], ah["zero_at_above_launch_m"], mg)),
        cape, rain]
    def total(rs):
        got = [r[3] for r in rs if r[3] is not None]
        return None if not got else min(got)
    pct = lambda x: None if x is None else round(100 * max(0, min(1, x)))
    pack = lambda rs: [dict(key=k, label=l, num=v[0], unit=v[1], nd=v[2], want=v[3], factor=None if x is None else round(x, 3)) for k, l, v, x in rs]
    key = dict(wind_kmh=f["ws"], wind_dir=compass(f["wd"]), gust_kmh=f["gust"], wind850_kmh=f["w850"], wind850_dir=compass(f["d850"]),
               wind700_kmh=f["w700"], shear_kmh=f["shear"], ceiling_asl_m=f["ceiling"], cloudbase_agl_m=f["cb"], blh_m=f["blh"],
               thermal_ms=f["therm"], sw_wm2=f["sw"], cape=f["cape"], rain_mm=f["rain"], low_cloud_pct=f["low"],
               aloft_kmh=al["speed"], aloft_u=al["u"], aloft_v=al["v"], north_aloft_kmh=al["north"], west_aloft_kmh=al["west"], climb_gain_m=gain)
    return dict(xc=pct(total(xc)), acro=pct(total(acro)), key=key, xc_rules=pack(xc), acro_rules=pack(acro))
