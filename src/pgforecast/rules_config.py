"""Loads and checks a site's rule settings (sites/<site>/rules.yaml). Mistakes give a clear message naming the setting, not a crash later."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
COMPASS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

def _num(r, path, f):
    cur = r
    for k in path:
        if not isinstance(cur, dict) or k not in cur: raise ValueError(f"{f}: missing setting '{'.'.join(path)}'")
        cur = cur[k]
    if isinstance(cur, bool) or not isinstance(cur, (int, float)): raise ValueError(f"{f}: '{'.'.join(path)}' must be a number, got {cur!r}")
    return cur

def validate(r, f="rules.yaml"):
    """Raises ValueError if a setting is missing, not a number, or on the wrong side of its limit."""
    for d in ("direction", "shared", "xc", "acro", "smoothing", "estimates", "ratings"):
        if not isinstance(r.get(d), dict): raise ValueError(f"{f}: missing section '{d}'")
    for k in ("launch_arc_deg", "unflyable_arc_deg"):
        a = r["direction"].get(k)
        if not (isinstance(a, list) and len(a) == 2 and all(isinstance(x, (int, float)) and 0 <= x <= 360 for x in a)): raise ValueError(f"{f}: direction.{k} must be [from, to] in degrees 0-360")
    for k in ("calm_below_kmh", "unflyable_min_850_kmh"): _num(r, ("direction", k), f)
    under = [("shared", "rain", "limit_mm", "zero_at_mm"), ("shared", "cape", "limit", "zero_at")] + [
        ("xc", "launch_wind", "limit_kmh", "zero_at_kmh"), ("xc", "gusts", "limit_kmh", "zero_at_kmh"), ("xc", "wind_850", "limit_kmh", "zero_at_kmh"),
        ("acro", "launch_wind", "limit_kmh", "zero_at_kmh"), ("acro", "gusts", "limit_kmh", "zero_at_kmh"), ("acro", "wind_850", "limit_kmh", "zero_at_kmh"),
        ("acro", "shear", "limit_kmh", "zero_at_kmh")]
    for a, b, lim, zero in under:                                       # "stay under": zero point must be higher than the limit
        l, z = _num(r, (a, b, lim), f), _num(r, (a, b, zero), f)
        if not z > l: raise ValueError(f"{f}: {a}.{b}.{zero} ({z}) must be higher than {lim} ({l})")
    for b, lim, zero in (("ceiling", "min_asl_m", "zero_at_asl_m"), ("thermals", "min_ms", "zero_at_ms")):   # "stay over": zero point must be lower
        l, z = _num(r, ("xc", b, lim), f), _num(r, ("xc", b, zero), f)
        if not z < l: raise ValueError(f"{f}: xc.{b}.{zero} ({z}) must be lower than {lim} ({l})")
    a = r["acro"]
    lv = a.get("aloft", {}).get("levels_hpa") if isinstance(a.get("aloft"), dict) else None
    if not (isinstance(lv, list) and lv and all(isinstance(x, int) and 500 <= x <= 1000 for x in lv)): raise ValueError(f"{f}: acro.aloft.levels_hpa must be a list of pressure levels such as [900, 850, 800]")
    n = "acro", "northerly_aloft"
    nl, ns, mp, lo, hi, sh = (_num(r, (*n, k), f) for k in ("limit_kmh", "strong_kmh", "max_penalty", "low_climb_above_launch_m", "high_climb_above_launch_m", "penalty_share_when_high"))
    if not ns > nl: raise ValueError(f"{f}: acro.northerly_aloft.strong_kmh ({ns}) must be higher than limit_kmh ({nl})")
    if not 0 <= mp < 1: raise ValueError(f"{f}: acro.northerly_aloft.max_penalty must be from 0 up to (not including) 1, so it can never reach 0%")
    if not hi > lo: raise ValueError(f"{f}: acro.northerly_aloft.high_climb_above_launch_m ({hi}) must be higher than low_climb_above_launch_m ({lo})")
    if not 0 <= sh <= 1: raise ValueError(f"{f}: acro.northerly_aloft.penalty_share_when_high must be from 0 to 1")
    w = a.get("westerly_aloft", {}).get("weights") if isinstance(a.get("westerly_aloft"), dict) else None
    if not (isinstance(w, dict) and w and all(k in COMPASS16 and isinstance(v, (int, float)) and 0 <= v <= 1 for k, v in w.items())): raise ValueError(f"{f}: acro.westerly_aloft.weights must map compass points (N, NNE, ... NNW) to weights from 0 to 1")
    wl, wz, wf = (_num(r, ("acro", "westerly_aloft", k), f) for k in ("limit_kmh", "zero_at_kmh", "floor"))
    if not wz > wl: raise ValueError(f"{f}: acro.westerly_aloft.zero_at_kmh ({wz}) must be higher than limit_kmh ({wl})")
    tl, tz, tf = (_num(r, ("acro", "strong_thermals", k), f) for k in ("limit_ms", "zero_at_ms", "floor"))
    if not tz > tl: raise ValueError(f"{f}: acro.strong_thermals.zero_at_ms ({tz}) must be higher than limit_ms ({tl})")
    for nm, fl in (("westerly_aloft", wf), ("strong_thermals", tf)):
        if not 0 <= fl <= 1: raise ValueError(f"{f}: acro.{nm}.floor must be from 0 to 1")
    hm, hz = _num(r, ("acro", "height", "min_above_launch_m"), f), _num(r, ("acro", "height", "zero_at_above_launch_m"), f)
    if not hz < hm: raise ValueError(f"{f}: acro.height.zero_at_above_launch_m ({hz}) must be lower than min_above_launch_m ({hm})")
    m = _num(r, ("smoothing", "full_score_margin"), f)
    if not 0 <= m < 1: raise ValueError(f"{f}: smoothing.full_score_margin must be from 0 up to (not including) 1")
    for k in ("cloud_base_m_per_degC", "thermal_heat_fraction", "thermal_climb_fraction", "thermal_depth_cap_m"):
        if _num(r, ("estimates", k), f) <= 0: raise ValueError(f"{f}: estimates.{k} must be above 0")
    g, mg, pd = (_num(r, ("ratings", k), f) for k in ("good_min_pct", "marginal_min_pct", "pulled_down_below"))
    if not 0 < mg < g <= 100: raise ValueError(f"{f}: ratings need 0 < marginal_min_pct < good_min_pct <= 100")
    if not 0 < pd <= 1: raise ValueError(f"{f}: ratings.pulled_down_below must be above 0 and at most 1")
    return r

def load_rules(site, root=None):
    path = Path(root or ROOT) / "sites" / site / "rules.yaml"
    if not path.exists(): raise FileNotFoundError(f"No rule settings for site '{site}': expected {path}")
    return validate(yaml.safe_load(path.read_text()), str(path))

def limits(rules):
    """The hard limits the AI second opinion must respect, read from the same settings the scoring uses."""
    g = lambda d: {"wind": rules[d]["launch_wind"]["limit_kmh"], "gust": rules[d]["gusts"]["limit_kmh"], "w850": rules[d]["wind_850"]["limit_kmh"]}
    return {"xc": g("xc"), "acro": g("acro"), "rain_mm": rules["shared"]["rain"]["limit_mm"]}
