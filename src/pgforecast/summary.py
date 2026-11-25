"""Plain-language text for each day, written from the numbers (no AI involved)."""

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
            "thermals": f"weak thermals (about {n:.1f} m/s)", "cape": f"overdevelopment risk (CAPE {n:.0f})", "rain": f"{n:.1f} mm of rain"}.get(k, r["label"].lower())

def limiters(rules, below=None):
    below = PULLED_BELOW if below is None else below
    return sorted([r for r in rules if r["factor"] is not None and r["factor"] < below], key=lambda r: r["factor"])

def _h(h):
    return "12pm" if h == 12 else f"{h}am" if h < 12 else f"{h - 12}pm"

def best_window(hourly):
    """Longest run of hours near the day's peak XC score, e.g. '12-3pm'. None if the day never gets going."""
    pts = [(x["h"], x["xc"]) for x in hourly if x["xc"] is not None]
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
    if lab == "good":
        return f"{top[0].upper() + top[1:]}{clim}." + (f" Best window {w}." if w else "") + (f" Watch {phrase(lim[0])}." if lim else "")
    if lab == "marginal":
        return (f"Flyable, but {phrase(lim[0])}" + (f" and {phrase(lim[1])}" if len(lim) > 1 else "") + "." if lim else f"Workable, not strong: {top}{clim}.") + (f" Best window {w}." if w else "")
    if lab == "poor":
        return "Unlikely: " + (" and ".join(phrase(r) for r in lim[:2]) if lim else "conditions are weak overall") + "."
    return "No data."

def acro_text(day):
    k, lab, lim = day["key"], label(day["acro"]), limiters(day["acro_rules"])
    calm = f"launch wind {k['wind_kmh']:.0f} km/h, gusts to {k['gust_kmh']:.0f}" if k.get("wind_kmh") is not None and k.get("gust_kmh") is not None else "light wind"
    if lab == "good": return f"Smooth, steady air expected: {calm}" + (f", shear {k['shear_kmh']:.0f} km/h aloft." if k.get("shear_kmh") is not None else ".")
    if lab == "marginal": return f"{calm[0].upper() + calm[1:]}, but " + (phrase(lim[0]) if lim else "some turbulence is possible") + "."
    if lab == "poor": return "Rough or unflyable: " + (" and ".join(phrase(r) for r in lim[:2]) if lim else "conditions are weak overall") + "."
    return "No data."

def pulled_text(day):
    """One line: the rule that holds XC down most, and the one for acro if different."""
    parts = []
    for name, rules in (("XC", day["xc_rules"]), ("acro", day["acro_rules"])):
        lim = limiters(rules)
        if lim: parts.append((name, lim[0]))
    if not parts: return None
    if len(parts) == 2 and parts[0][1]["key"] == parts[1][1]["key"]:
        r = parts[0][1]; return f"{phrase(r)} (-{round(100 * (1 - min(parts[0][1]['factor'], parts[1][1]['factor'])))}%)"
    return " · ".join(f"{n} {phrase(r)} (-{round(100 * (1 - r['factor']))}%)" for n, r in parts)

def headline(day):
    """Short subtitle for a collapsed day row."""
    lim = limiters(day["xc_rules"]) or limiters(day["acro_rules"])
    if lim: return phrase(lim[0])[0].upper() + phrase(lim[0])[1:]
    return agreement(day).capitalize()
