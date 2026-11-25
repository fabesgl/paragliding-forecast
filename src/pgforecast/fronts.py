"""Spots cold (and warm) fronts passing Bright in the model forecast, and labels each day pre-frontal / front passing / post-frontal.
Information only: it never changes the XC or acro scores. All thresholds are in sites/<site>/rules.yaml under 'fronts'.

How: average the models into one hourly series (pressure, 850 hPa temperature, 850 hPa wind direction), then look for
 - a sharp 850 hPa temperature change over `window_h` hours (cold: a drop; warm: a rise), beyond the usual daily warm-up/cool-down at that hour. Required.
 - the pressure falling then rising around it (a trough passing). Cold fronts only.
 - the 850 hPa wind swinging from one arc to another (cold: NW -> SW/S).
A cold front needs the temperature drop plus at least one of the other two. 'clear' = all three, 'likely' = two."""
import datetime as dt, math

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None

def _dir_mean(pairs):
    """Wind-speed-weighted mean direction of (speed, direction) pairs, or None."""
    u = v = 0.0; n = 0
    for s, d in pairs:
        if s is None or d is None: continue
        u += s * math.sin(math.radians(d)); v += s * math.cos(math.radians(d)); n += 1
    return None if not n or math.hypot(u, v) < 1e-6 else math.degrees(math.atan2(u, v)) % 360

def _in_arc(d, arc):
    return d is not None and ((arc[0] <= d <= arc[1]) if arc[0] <= arc[1] else (d >= arc[0] or d <= arc[1]))

def series(hourly, models):
    """Model-averaged hourly series from one point's hourly data: pressure, 850 hPa temperature, 850 hPa wind direction."""
    n = len(hourly["time"]); g = lambda v, m: hourly.get(f"{v}_{m}") or [None] * n
    return dict(
        p=[_mean([g("pressure_msl", m)[i] for m in models]) for i in range(n)],
        t=[_mean([g("temperature_850hPa", m)[i] for m in models]) for i in range(n)],
        d=[_dir_mean([(g("wind_speed_850hPa", m)[i], g("wind_direction_850hPa", m)[i]) for m in models]) for i in range(n)])

def _at(xs, i): return xs[i] if 0 <= i < len(xs) else None

def _wind_around(s, i, a, b):
    return _dir_mean([(1.0, _at(s["d"], j)) for j in range(i + a, i + b + 1)])

def _pressure_trough(s, i, cfg):
    """Pressure fell by `pressure_fall_hpa` then rose by `pressure_rise_hpa`, with the low within 6 h of the temperature change."""
    near = [(j, s["p"][j]) for j in range(max(0, i - 6), min(len(s["p"]), i + 7)) if s["p"][j] is not None]
    if not near: return False
    m, pm = min(near, key=lambda x: x[1])
    before = [x for x in s["p"][max(0, m - 12):m + 1] if x is not None]; after = [x for x in s["p"][m:m + 13] if x is not None]
    return bool(before and after and max(before) - pm >= cfg["pressure_fall_hpa"] and max(after) - pm >= cfg["pressure_rise_hpa"])

def detect(times, s, cfg):
    """Fronts in the series: [{'kind': 'cold'|'warm', 'time': 'YYYY-MM-DDTHH:00', 'strength': 'clear'|'likely', 'temp_change_c': float, 'signals': [...]}]."""
    w = int(cfg["window_h"]); half = w // 2; n = len(times); out = []
    d_t = [None if _at(s["t"], i - half) is None or _at(s["t"], i + half) is None else s["t"][i + half] - s["t"][i - half] for i in range(n)]
    hr = [int(x[11:13]) for x in times]
    def typical(h):                                  # the usual change at this hour of day over the forecast (the daily warm-up and cool-down), so only unusual changes count
        v = sorted(d_t[i] for i in range(n) if hr[i] == h and d_t[i] is not None); return v[len(v) // 2] if v else 0.0
    norm = {h: typical(h) for h in set(hr)}
    d_raw = d_t; d_t = [None if x is None else x - norm[hr[i]] for i, x in enumerate(d_raw)]
    for kind, sign, need in (("cold", -1, cfg["cold_drop_c"]), ("warm", 1, cfg["warm_rise_c"])):
        i = 0
        while i < n:
            if d_t[i] is not None and sign * d_t[i] >= need:
                j = i
                while j + 1 < n and d_t[j + 1] is not None and sign * d_t[j + 1] >= need: j += 1
                best = max(range(i, j + 1), key=lambda k: sign * d_t[k]); i = j + 1
                before, after = _wind_around(s, best, -9, -3), _wind_around(s, best, 3, 9)
                if kind == "cold":
                    swing = _in_arc(before, cfg["cold_before_arc_deg"]) and _in_arc(after, cfg["cold_after_arc_deg"])
                    other = {"pressure fall then rise": _pressure_trough(s, best, cfg), "wind swing NW to SW/S": swing}
                else:
                    swing = _in_arc(before, cfg["warm_before_arc_deg"]) and _in_arc(after, cfg["warm_after_arc_deg"])
                    pb, pa = _at(s["p"], best - 6), _at(s["p"], best + 3)
                    other = {"pressure falling": pb is not None and pa is not None and pb - pa >= cfg["pressure_fall_hpa"], "wind swing to N/NW": swing}
                sig = [f"850 hPa temperature {'drop' if kind == 'cold' else 'rise'} {abs(d_t[best]):.1f} C in {w} h"] + [k for k, v in other.items() if v]
                if len(sig) >= 2:
                    out.append(dict(kind=kind, time=times[best][:13] + ":00", strength="clear" if len(sig) == 3 else "likely", temp_change_c=round(d_t[best], 1), signals=sig))
            else: i += 1
    return sorted(out, key=lambda f: f["time"])

def _h12(h): return "12pm" if h == 12 else f"{h}am" if h < 12 else f"{h - 12}pm"

def advice(kind, hour):
    """What it means for flying, from the hour the front arrives."""
    if hour >= 15: return "fly early"
    if hour >= 11: return "short window before it"
    return "front arrives before flying hours"

def label_day(fronts, date, cfg):
    """Day label from the nearest front: {'status': 'passing'|'pre'|'post'|None, 'kind', 'time', 'strength', 'text'}."""
    day0 = dt.datetime.fromisoformat(date + "T00:00"); day1 = day0 + dt.timedelta(days=1)
    F = [(dt.datetime.fromisoformat(f["time"]), f) for f in fronts]
    for t, f in F:
        if day0 <= t < day1:
            nm = "Cold front" if f["kind"] == "cold" else "Warm front"
            adv = advice(f["kind"], t.hour); adv = f": {adv}" if f["kind"] == "cold" else ""
            return dict(status="passing", kind=f["kind"], time=f["time"], strength=f["strength"], text=f"{nm} ~{_h12(t.hour)}{adv}")
    prev = [(day0 - t, f, t) for t, f in F if t < day0 and day0 - t <= dt.timedelta(hours=cfg["post_frontal_max_h"])]
    nxt = [(t - day1, f, t) for t, f in F if t >= day1 and t - day1 <= dt.timedelta(hours=cfg["pre_frontal_max_h"])]
    cands = [("post", *max(prev, key=lambda x: x[2]))] if prev else []
    if nxt: cands.append(("pre", *min(nxt, key=lambda x: x[2])))
    if not cands: return dict(status=None, kind=None, time=None, strength=None, text=None)
    status, gap, f, t = min(cands, key=lambda c: c[1])
    nm = "cold front" if f["kind"] == "cold" else "warm front"
    when = f"{t:%A} ~{_h12(t.hour)}"
    return dict(status=status, kind=f["kind"], time=f["time"], strength=f["strength"],
                text=(f"Pre-frontal: {nm} {when}" if status == "pre" else f"Post-frontal: {nm} passed {when}"))
