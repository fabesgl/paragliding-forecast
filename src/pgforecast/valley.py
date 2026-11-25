"""Calibrating the valley-wind estimate: how many km/h of afternoon north wind per 1 m/s of estimated thermal strength?
Compares, day by day, a weather station's afternoon north wind with the thermal strength the forecast gave for that day."""
import math

MIN_DAYS = 30

def fit(days):
    """days: list of (thermal_ms, north_kmh). Least-squares line through the origin: north = k * thermal. Returns a result dict."""
    pts = [(t, n) for t, n in days if t is not None and n is not None]
    if not pts: return dict(n_days=0, k=None, r2=None, ready=False)
    sxx = sum(t * t for t, _ in pts); k = sum(t * n for t, n in pts) / sxx if sxx > 0 else None
    my = sum(n for _, n in pts) / len(pts); ss_tot = sum((n - my) ** 2 for _, n in pts)
    ss_res = sum((n - k * t) ** 2 for t, n in pts) if k is not None else None
    r2 = None if ss_tot == 0 or k is None else 1 - ss_res / ss_tot
    bins = {}
    for lo, hi in ((0, 1), (1, 1.5), (1.5, 2), (2, 9)):
        b = [n for t, n in pts if lo <= t < hi]; bins[f"{lo}-{hi if hi < 9 else '+'} m/s"] = dict(days=len(b), mean_north_kmh=round(sum(b) / len(b), 1) if b else None)
    return dict(n_days=len(pts), k=None if k is None else round(k, 2), r2=None if r2 is None else round(r2, 2), by_thermal=bins, ready=len(pts) >= MIN_DAYS)

def north_kmh(speed_kmh, direction_deg):
    """The part of a wind (speed, direction it comes FROM) that blows from the north, never negative."""
    return max(0.0, speed_kmh * math.cos(math.radians(direction_deg)))
