"""Creo (first test): pure helpers for a model that predicts a day's best flown distance from the forecast. No files, no network; scripts/creo_test.py does the I/O.
Everything here is forecast-side: what the dashboard could know the day before. The rules' deal-breakers stay hard limits: a no-go day predicts 0."""
import datetime as dt
import math
import numpy as np          # pandas is imported inside the functions that need it: the daily job only needs the stdlib and numpy (installed with lightgbm)

LEVELS = (("wind10", "wind_speed_10m", "wind_direction_10m"), ("wind850", "wind_speed_850hPa", "wind_direction_850hPa"), ("wind700", "wind_speed_700hPa", "wind_direction_700hPa"))
XC_RULES = ("direction", "wind", "gust", "w850", "ceiling", "thermals", "cape", "rain")


def _mean(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return sum(xs) / len(xs) if xs else None


def _uv(speeds, dirs):
    """Mean wind vector (east, north components of where the wind blows to) over the hours that have both speed and direction; None if none."""
    pairs = [(s, d) for s, d in zip(speeds, dirs) if s is not None and d is not None]
    if not pairs: return None
    return (sum(-s * math.sin(math.radians(d)) for s, d in pairs) / len(pairs), sum(-s * math.cos(math.radians(d)) for s, d in pairs) / len(pairs))


def model_features(h, result, launch_elev):
    """One model's flying-window features. h: the window's hourly lists; result: rules.score_day(h, ...)."""
    k = result["key"]; f = {"xc_pct": result["xc"]}
    for name, sv, dv in LEVELS:
        uv = _uv(h.get(sv, []), h.get(dv, [])); f[name + "_speed"] = _mean(h.get(sv, [])); f[name + "_u"], f[name + "_v"] = uv if uv else (None, None)
    f["gust"] = k["gust_kmh"]; f["temp"] = _mean(h["temperature_2m"]); f["dew_point"] = _mean(h["dew_point_2m"])
    f["cloud_base_asl"] = None if k["cloudbase_agl_m"] is None else k["cloudbase_agl_m"] + launch_elev
    f["cape"] = k["cape"]; f["rain"] = k["rain_mm"]
    for c in ("low", "mid", "high"): f[f"cloud_{c}"] = _mean(h.get(f"cloud_cover_{c}", []))
    for r in result["xc_rules"]: f["rule_" + r["key"]] = r["factor"]
    return f


def day_features(per_model):
    """Average several models' features into one row. Wind direction = the vector mean of the models' mean winds, as sin and cos (so 359 and 1 degrees are close)."""
    keys = {k for p in per_model for k in p}; out = {}
    for k in keys:
        if k.endswith(("_u", "_v")): continue
        out[k] = _mean([p.get(k) for p in per_model])
    for name, _, _ in LEVELS:
        u, v = _mean([p.get(name + "_u") for p in per_model]), _mean([p.get(name + "_v") for p in per_model])
        if u is None or v is None or (abs(u) < 1e-9 and abs(v) < 1e-9): out[name + "_sin"] = out[name + "_cos"] = 0.0
        else:
            d = math.atan2(-u, -v); out[name + "_sin"], out[name + "_cos"] = math.sin(d), math.cos(d)       # direction the wind comes FROM
    return out


def hard_limit(pred_km, xc_pct):
    """A rule at 0% (the day's XC % is 0) is a no-go: Creo never overrides it, whatever the model says."""
    pred = np.asarray(pred_km, float).copy(); pred[np.asarray(xc_pct, float) <= 0] = 0.0
    return pred


def matched_bands(score, reference_pct, bands=((0, 20), (20, 40), (40, 60), (60, 80), (80, 100))):
    """Give another score (e.g. Creo's predicted km) the SAME number of days in each band as the reference % has, by rank, so the two are comparable.
    Returns an array of band labels ('0-20' ...). Ties are broken by order (stable)."""
    import pandas as pd
    ref = pd.Series(reference_pct); n = len(ref); order = np.argsort(np.asarray(score, float), kind="stable")
    counts = [int(((ref >= lo) & (ref < hi if hi < 100 else ref <= hi)).sum()) for lo, hi in bands]
    labels = np.empty(n, dtype=object); pos = 0
    for (lo, hi), c in zip(bands, counts):
        labels[order[pos:pos + c]] = f"{lo}-{hi}"; pos += c
    return labels


def spearman_fast(a, b):
    """Rank correlation of two numpy arrays (ties averaged); None if either side never varies."""
    import pandas as pd
    ra, rb = pd.Series(a).rank().to_numpy(), pd.Series(b).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0: return None
    return float(np.corrcoef(ra, rb)[0, 1])


def bootstrap_diff(truth, score_a, score_b, n=1000, seed=1):
    """(difference, low, high): spearman(score_a, truth) - spearman(score_b, truth) and its 95% range over `n` resamples of the days (paired)."""
    truth, a, b = (np.asarray(x, float) for x in (truth, score_a, score_b)); rng = np.random.default_rng(seed); d = []
    for _ in range(n):
        i = rng.integers(0, len(truth), len(truth)); x, y = spearman_fast(a[i], truth[i]), spearman_fast(b[i], truth[i])
        if x is not None and y is not None: d.append(x - y)
    return spearman_fast(a, truth) - spearman_fast(b, truth), float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def feature_row(day_feats, names, target_date):
    """One model input row in the order the trained model wants (`booster.feature_name()`). Missing features are NaN (LightGBM handles them).
    The free-day flag is always 1 (Creo rates the weather, not the crowd); day of year comes from the target date."""
    d = dict(day_feats); d["free"] = 1; d["doy"] = dt.date.fromisoformat(target_date).timetuple().tm_yday
    return [float("nan") if d.get(n) is None else float(d[n]) for n in names]


def predict_day(booster, per_model, target_date, published_xc=None):
    """Shadow prediction for one forecast day. per_model: the models' `model_features` for that day. published_xc: the XC % the dashboard shows (the no-go check).
    Returns dict: creo_raw_km (the model alone), creo_km (after the rules' hard limit), no_go (True when the rules say 0%), xc_feature (the XC % fed to the model)."""
    day = day_features(per_model); xcs = [p["xc_pct"] for p in per_model if p.get("xc_pct") is not None]
    day["xc_pct"] = float(round(sum(xcs) / len(xcs))) if xcs else None
    raw = float(np.expm1(booster.predict(np.array([feature_row(day, booster.feature_name(), target_date)]))[0])); raw = max(raw, 0.0)
    rules_xc = published_xc if published_xc is not None else day["xc_pct"]
    no_go = rules_xc is not None and rules_xc <= 0
    return dict(creo_raw_km=raw, creo_km=0.0 if no_go else raw, no_go=bool(no_go), xc_feature=day["xc_pct"])
