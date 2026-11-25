"""Plain-language text for each day, written from the numbers (no AI involved)."""
import math

GOOD_MIN, MARGINAL_MIN, PULLED_BELOW = 65, 35, 0.9     # defaults; replaced from the site's rules.yaml by configure()

def configure(rules):
    """Take the Good/Marginal/Poor cut-offs and the 'pulled down' threshold from the site's rule settings."""
    global GOOD_MIN, MARGINAL_MIN, PULLED_BELOW
    r = rules["ratings"]; GOOD_MIN, MARGINAL_MIN, PULLED_BELOW = r["good_min_pct"], r["marginal_min_pct"], r["pulled_down_below"]

def label(p):
    return "n/a" if p is None else "good" if p >= GOOD_MIN else "marginal" if p >= MARGINAL_MIN else "poor"

def agreement(day):
    lo, hi = day["xc_range"]
    if lo is None or len(day["models"]) < 2: return "one model"
    return "models agree" if hi - lo <= 25 else "models differ" if hi - lo <= 50 else "models disagree"

def phrase(r):
    n, k = r["num"], r["key"]
    if k == "direction": return r["want"]
    if n is None: return r["label"].lower()
    return {"wind": f"launch wind {n:.0f} km/h", "gust": f"gusts near {n:.0f} km/h", "w850": f"{n:.0f} km/h wind at 850 hPa",
            "shear": f"{n:.0f} km/h of wind shear up to 850 hPa", "ceiling": f"thermals topping out near {round(n, -2):,.0f} m",
            "thermals": f"weak thermals (about {n:.1f} m/s)", "cape": f"overdevelopment risk (CAPE {n:.0f})", "rain": f"{n:.1f} mm of rain",
            "north_aloft": f"northerly wind aloft ({n:.0f} km/h)", "west_aloft": f"westerly wind aloft ({n:.0f} km/h, rough off Buffalo)",
            "thermals_strong": f"very strong thermals (about {n:.1f} m/s)", "height": f"climbs reaching about {n:,.0f} m above launch"}.get(k, r["label"].lower())

def limiters(rules, below=None):
    below = PULLED_BELOW if below is None else below
    return sorted([r for r in rules if r["factor"] is not None and r["factor"] < below], key=lambda r: r["factor"])

NOGO_BELOW = 15       # a score under this is a no-go day: the summary leads with the deal-breaker and skips flying tips

def nogo(p):
    return p is not None and p < NOGO_BELOW

def dealbreaker(rules):
    """The rule that sinks the day (heavy rain always leads, as the clearest reason; otherwise the lowest score), worded to lead a sentence; None if nothing is limiting."""
    lim = sorted(limiters(rules), key=lambda r: (not (r["key"] == "rain" and r["factor"] < 0.5), r["factor"]))
    if not lim: return None, []
    r = lim[0]
    head = f"Rain {r['num']:.1f} mm in the window" if r["key"] == "rain" and r["num"] is not None else phrase(r)[0].upper() + phrase(r)[1:]
    return head, lim[1:]

def nogo_text(rules):
    head, rest = dealbreaker(rules)
    if head is None: return "Conditions are weak overall."
    also = [phrase(r) for r in rest if r["key"] != "rain"][:1]
    return head + (f"; also {also[0]}" if also else "") + "."

def _h(h):
    return "12pm" if h == 12 else f"{h}am" if h < 12 else f"{h - 12}pm"

def best_window(hourly, key="xc"):
    """Longest run of hours near the day's peak score (XC or acro), e.g. '12-3pm'. None if the day never gets going."""
    pts = [(x["h"], x[key]) for x in hourly if x.get(key) is not None]
    if not pts: return None
    peak = max(s for _, s in pts)
    if peak < 35: return None
    thr = max(35, 0.75 * peak); runs, cur = [], []
    for h, s in pts:
        if s >= thr: cur.append(h)
        else:
            if cur: runs.append(cur)
            cur = []
    if cur: runs.append(cur)
    best = max(runs, key=lambda r: (len(r), -r[0])); sa, sb = _h(best[0]), _h(best[-1] + 1)
    return f"{sa[:-2] if sa[-2:] == sb[-2:] else sa}\u2013{sb}"

def xc_text(day):
    k, lab, lim = day["key"], label(day["xc"]), limiters(day["xc_rules"])
    w = best_window(day["hourly"])
    top = f"thermals reaching about {round(k['ceiling_asl_m'], -2):,.0f} m" if k.get("ceiling_asl_m") else "usable thermals"
    clim = f" at about {k['thermal_ms']:.1f} m/s" if k.get("thermal_ms") else ""
    if nogo(day["xc"]): return nogo_text(day["xc_rules"])
    if lab == "good":
        return f"{top[0].upper() + top[1:]}{clim}." + (f" Best window {w}." if w else "") + (f" Watch {phrase(lim[0])}." if lim else "")
    if lab == "marginal":
        return (f"Flyable, but {phrase(lim[0])}" + (f" and {phrase(lim[1])}" if len(lim) > 1 else "") + "." if lim else f"Workable, not strong: {top}{clim}.") + (f" Best window {w}." if w else "")
    if lab == "poor":
        return "Unlikely: " + (" and ".join(phrase(r) for r in lim[:2]) if lim else "conditions are weak overall") + "."
    return "No data."

_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

def aloft_phrase(day):
    """The wind at acro height (about 1,000-2,000 m) in words: where it pushes you relative to the acro box (north side of Mystic)."""
    k = day["key"]; u, v = k.get("aloft_u"), k.get("aloft_v")
    if u is None or v is None: return None
    spd = math.hypot(u, v); rules = {r["key"]: r for r in day["acro_rules"]}
    d = _COMPASS[int((math.degrees(math.atan2(-u, -v)) % 360 + 11.25) // 22.5) % 16] if spd >= 1 else None
    w, n = rules.get("west_aloft"), rules.get("north_aloft")
    if w and w["factor"] is not None and w["factor"] < PULLED_BELOW: return f"{d or 'W'} aloft, rough off Buffalo"
    if n and n["factor"] is not None and n["factor"] < 0.97: return f"{d or 'N'} aloft: train higher, you'll drift towards the hill"
    if spd < 3: return "Nil wind aloft, stays over the box"
    if d in ("SE", "SSE", "S", "SSW", "SW") and spd <= 10: return f"Light {d} aloft, good for the box"
    return f"{d} aloft at {spd:.0f} km/h"

def acro_text(day):
    if nogo(day["acro"]): return nogo_text(day["acro_rules"])
    k, lab = day["key"], label(day["acro"]); aloft = aloft_phrase(day)
    lim = [r for r in limiters(day["acro_rules"]) if not (aloft and r["key"] in ("west_aloft", "north_aloft"))]      # already said in the aloft phrase
    head = (aloft[0].upper() + aloft[1:] + ". ") if aloft else ""
    gust_limits = bool(lim) and lim[0]["key"] == "gust"          # the limiter phrase already states the gust speed
    calm = (f"launch wind {k['wind_kmh']:.0f} km/h" + ("" if gust_limits else f", gusts to {k['gust_kmh']:.0f}")) if k.get("wind_kmh") is not None and k.get("gust_kmh") is not None else "light wind"
    w = best_window(day["hourly"], "acro"); win = f" Best acro window {w}." if w else ""
    if lab == "good": return head + f"Smooth, steady air expected: {calm}" + (f", shear {k['shear_kmh']:.0f} km/h aloft." if k.get("shear_kmh") is not None else ".") + win
    if lab == "marginal": return head + f"{calm[0].upper() + calm[1:]}, but " + (phrase(lim[0]) if lim else "some turbulence is possible") + "." + win
    if lab == "poor": return head + "Rough or unflyable: " + (" and ".join(phrase(r) for r in lim[:2]) if lim else "conditions are weak overall") + "."
    return "No data."

def pulled_text(day):
    """One line: the rule that holds XC down most, and the one for acro if different."""
    parts = []
    for name, rules in (("XC", day["xc_rules"]), ("acro", day["acro_rules"])):
        lim = limiters(rules)
        if lim: parts.append((name, lim[0]))
    rain = next((r for r in limiters(day["acro_rules"]) if r["key"] == "rain"), None)          # rain is a rule for both; show it when it is a factor even if something else is worse
    if rain and not any(r["key"] == "rain" for _, r in parts): parts.append(("rain", rain))
    if not parts: return None
    if rain and rain["factor"] < 0.5: parts.sort(key=lambda p: p[0] != "rain")          # heavy rain goes first, same as in the summary line
    if len(parts) == 2 and parts[1][0] != "rain" and parts[0][1]["key"] == parts[1][1]["key"]:
        r = parts[0][1]; return f"{phrase(r)} (-{round(100 * (1 - min(parts[0][1]['factor'], parts[1][1]['factor'])))}%)"
    return " · ".join(f"{phrase(r) if n == 'rain' else n + ' ' + phrase(r)} (-{round(100 * (1 - r['factor']))}%)" for n, r in parts)

def headline(day):
    """Short subtitle for a collapsed day row."""
    lim = limiters(day["xc_rules"]) or limiters(day["acro_rules"])
    if lim: return phrase(lim[0])[0].upper() + phrase(lim[0])[1:]
    return agreement(day).capitalize()
