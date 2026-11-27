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
    names = booster.feature_name(); x = np.array([feature_row(day, names, target_date)])
    raw = float(np.expm1(booster.predict(x)[0])); raw = max(raw, 0.0)
    contrib = dict(zip(names, (float(v) for v in booster.predict(x, pred_contrib=True)[0][:-1])))         # per-feature push on log(1 + km); the last column is the base value
    rules_xc = published_xc if published_xc is not None else day["xc_pct"]
    no_go = rules_xc is not None and rules_xc <= 0
    return dict(creo_raw_km=raw, creo_km=0.0 if no_go else raw, no_go=bool(no_go), xc_feature=day["xc_pct"], contrib=contrib, day=day)


def cluster_bootstrap_diff(groups, truth, score_a, score_b, n=1000, seed=1):
    """Like bootstrap_diff, but resamples whole groups (e.g. dates), so a day that appears more than once (1 and 2 days ahead) is not counted as independent evidence.
    Returns (difference, low, high) of spearman(score_a, truth) - spearman(score_b, truth)."""
    g = np.asarray(groups); truth, a, b = (np.asarray(x, float) for x in (truth, score_a, score_b)); keys = np.unique(g)
    idx = {k: np.flatnonzero(g == k) for k in keys}; rng = np.random.default_rng(seed); d = []
    for _ in range(n):
        pick = np.concatenate([idx[k] for k in rng.choice(keys, len(keys))])
        x, y = spearman_fast(a[pick], truth[pick]), spearman_fast(b[pick], truth[pick])
        if x is not None and y is not None: d.append(x - y)
    return spearman_fast(a, truth) - spearman_fast(b, truth), float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def week_start(date, first):
    """Start (ISO date) of the 7-day block containing `date`, counting blocks from `first`."""
    f = dt.date.fromisoformat(first); d = dt.date.fromisoformat(date)
    return (f + dt.timedelta(days=7 * ((d - f).days // 7))).isoformat()


# ---------------------------------------------------------------- the dashboard panel: a rating word and a short "why"
RATINGS = ("Poor", "Fair", "Good", "Great")


def rating(km, cuts):
    """Poor/Fair/Good/Great from the estimate and three cut points (estimates at which the typical actual best flight reaches the next word's bound; see calibrate_cuts).
    A cut point of None means no estimate ever got there, so that word is never given."""
    return RATINGS[sum(1 for c in cuts if c is not None and km >= c)] if km is not None else None


def _pav(y):
    """Pool-adjacent-violators: the closest non-decreasing curve to y."""
    blocks = []
    for v in y:
        blocks.append([float(v), 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s_, n_ = blocks.pop(); blocks[-1][0] += s_; blocks[-1][1] += n_
    return np.concatenate([np.full(n_, s_ / n_) for s_, n_ in blocks])


def calibrate_cuts(estimates, actual_km, bounds=(5, 20, 50), k=101):
    """For past days with a similar estimate, what was the median actual best flight? Returns the estimate at which that median first reaches each bound, so a day's word follows
    the typical outcome of days like it (Poor under 5 km, Fair 5-20, Good 20-50, Great 50 or more). The median is taken over the `k` days nearest in estimate and made
    non-decreasing; a bound the median never reaches gives None."""
    est, act = np.asarray(estimates, float), np.asarray(actual_km, float); order = np.argsort(est, kind="stable"); e, a = est[order], act[order]; n = len(e); k = min(k, n)
    med = np.array([np.median(a[max(0, min(i - k // 2, n - k)):max(0, min(i - k // 2, n - k)) + k]) for i in range(n)]); iso = _pav(med)
    return [None if not (iso >= b).any() else round(float(e[int(np.argmax(iso >= b))]), 3) for b in bounds]


def calibrate_share(estimates, flags, k=101, points=25):
    """How often did past days with a similar estimate have the outcome `flags` (e.g. a best flight of 20 km or more)? Share among the `k` days nearest in estimate, made
    non-decreasing, sampled at `points` places as [[estimate, share], ...] (stored with the model; `curve_share` reads it)."""
    est, fl = np.asarray(estimates, float), np.asarray(flags, float); order = np.argsort(est, kind="stable"); e, f = est[order], fl[order]; n = len(e); k = min(k, n)
    share = np.array([f[max(0, min(i - k // 2, n - k)):max(0, min(i - k // 2, n - k)) + k].mean() for i in range(n)]); iso = _pav(share)
    idx = np.unique(np.linspace(0, n - 1, points).astype(int)); return [[round(float(e[i]), 3), round(float(iso[i]), 3)] for i in idx]


def curve_share(curve, estimate):
    """Share (0-1) of past days like this with the outcome, from the stored curve."""
    xs, ys = zip(*curve); return float(np.interp(estimate, xs, ys))


# Which features speak for which physical piece, and which pieces make up one FACTOR. A factor gets ONE net effect (all its pieces added up), so it can never be listed as both
# helping and hurting. The sentence describes the piece that pushed the way the factor's net effect went. xc_pct, day of year and the free-day flag are not weather and never appear.
COMPONENTS = {
    "launch_wind": ("wind10_speed", "rule_wind"), "launch_dir": ("wind10_sin", "wind10_cos", "rule_direction"), "gusts": ("gust", "rule_gust"),
    "wind850": ("wind850_speed", "wind850_sin", "wind850_cos", "rule_w850"), "wind700": ("wind700_speed", "wind700_sin", "wind700_cos"),
    "height": ("cloud_base_asl", "rule_ceiling", "dew_point"), "thermals": ("rule_thermals", "temp", "cloud_low", "cloud_mid", "cloud_high"),
    "storm": ("cape", "rule_cape"), "rain": ("rain", "rule_rain"),
}
FACTORS = {"wind at launch": ("launch_wind", "launch_dir", "gusts"), "wind aloft": ("wind850", "wind700"), "height": ("height",), "thermals": ("thermals",), "storm": ("storm",), "rain": ("rain",)}
MIN_REASON = 0.08            # a factor must move the estimate by at least this much net (log scale, about 8% in distance) to be mentioned
_WORD = ["northerly", "north-easterly", "easterly", "south-easterly", "southerly", "south-westerly", "westerly", "north-westerly"]


def _a(word):
    return ("an " if word[0] in "aeiou" else "a ") + word


def _word(sin, cos):
    return None if sin is None or cos is None or (sin == 0 and cos == 0) else _WORD[int(((math.degrees(math.atan2(sin, cos)) % 360) + 22.5) // 45) % 8]


def _phrase(group, good, f, key):
    """Noun phrase for one piece (a COMPONENTS key) with its real value; `good` = it helps. f: the day's features; key: the dashboard day's key numbers."""
    g = lambda n: f.get(n)
    if group == "launch_wind" and g("wind10_speed") is not None: return ("light " if good else "strong ") + f"wind at launch (about {g('wind10_speed'):.0f} km/h)"
    if group == "launch_dir" and _word(g("wind10_sin"), g("wind10_cos")): return f"{_a(_word(g('wind10_sin'), g('wind10_cos')))} wind at launch"
    if group == "gusts" and g("gust") is not None: return ("gentle" if good else "strong") + f" gusts (up to about {g('gust'):.0f} km/h)"
    for grp, spd, sn, cs, where in (("wind850", "wind850_speed", "wind850_sin", "wind850_cos", "aloft"), ("wind700", "wind700_speed", "wind700_sin", "wind700_cos", "higher up")):
        if group == grp and g(spd) is not None:
            w = _word(g(sn), g(cs)); lvl = "1,500 m" if grp == "wind850" else "3,000 m"
            return (f"{_a(w)} wind {where}" if w else f"light wind {where}") + f" ({g(spd):.0f} km/h at {lvl})"
    if group == "height" and g("cloud_base_asl") is not None: return ("a high" if good else "the low") + f" cloud base (about {round(g('cloud_base_asl'), -2):,.0f} m)"
    if group == "thermals":
        t = (key or {}).get("thermal_ms")
        return (("strong" if t is not None and t >= 2.5 else "decent") if good else "weak") + " thermals" + (f" (about {t:.1f} m/s)" if t is not None else "")
    if group == "storm" and g("cape") is not None: return ("some lift from instability" if good else "storm risk") + f" (CAPE {g('cape'):.0f})"
    if group == "rain": return "a dry day" if good else f"rain ({g('rain') or 0:.1f} mm)"
    return None


def wind_at_height(winds, height_m):
    """Wind interpolated to `height_m` from the dashboard's winds-by-height table (rows with height_m and cells of km/h and direction at 11, 13 and 15h). Returns
    {'speed', 'deg', 'height'} (the height is held between the lowest and highest level), or None when the table cannot give it."""
    pts = []
    for row in winds or []:
        cells = [c for c in row.get("cells", []) if c]
        if not row.get("height_m") or not cells: continue
        u = sum(-c["kmh"] * math.sin(math.radians(c["deg"])) for c in cells) / len(cells); v = sum(-c["kmh"] * math.cos(math.radians(c["deg"])) for c in cells) / len(cells)
        pts.append((float(row["height_m"]), u, v))
    if len(pts) < 2 or height_m is None: return None
    pts.sort(); h = min(max(float(height_m), pts[0][0]), pts[-1][0])
    for (h0, u0, v0), (h1, u1, v1) in zip(pts, pts[1:]):
        if h0 <= h <= h1:
            t = 0.0 if h1 == h0 else (h - h0) / (h1 - h0); u, v = u0 + t * (u1 - u0), v0 + t * (v1 - v0)
            return dict(speed=math.hypot(u, v), deg=math.degrees(math.atan2(-u, -v)) % 360, height=h)


def _aloft_phrase(a):
    """The wind aloft at the forecast top of the climbs, with the real height."""
    w = _word(math.sin(math.radians(a["deg"])), math.cos(math.radians(a["deg"]))) if a["speed"] >= 1 else None
    return (f"{_a(w)} wind aloft" if w else "light wind aloft") + f" ({a['speed']:.0f} km/h at about {round(a['height'], -2):,.0f} m)"


def reasons(contrib, f, key=None, n=3, aloft=None):
    """The strongest 2-3 factors behind the estimate, as [(factor, helps?, phrase, net push, plural?)], biggest net effect first. Each factor's pieces are added up into ONE net effect
    (wind aloft = 850 and 700 hPa together; wind at launch = speed, direction and gusts together). Factors whose net effect is under MIN_REASON are left out."""
    comp = {k: sum(contrib.get(c, 0.0) for c in cols) for k, cols in COMPONENTS.items()}; out = []
    for fac, parts in sorted(FACTORS.items(), key=lambda kv: -abs(sum(comp[p] for p in kv[1]))):
        net = sum(comp[p] for p in parts)
        if abs(net) < MIN_REASON: break
        if fac == "wind aloft" and aloft:                                           # described at the top of the climbs; the model itself still sees 850 and 700 hPa
            out.append((fac, net > 0, _aloft_phrase(aloft), net, False))
            if len(out) == n: break
            continue
        for p in sorted(parts, key=lambda p: -abs(comp[p])):                       # describe the biggest piece that pushed the same way as the net effect
            if comp[p] * net > 0 and (ph := _phrase(p, net > 0, f, key)):
                out.append((fac, net > 0, ph, net, p in PLURAL)); break
        if len(out) == n: break
    return out


def _join(xs):
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


PLURAL = ("thermals", "gusts")          # pieces whose phrase is a plural noun ("thermals help", not "thermals helps")


def describe(rs, nogo_lead=None):
    """One or two sentences from `reasons`. nogo_lead: the rules' deal-breaker wording (e.g. 'Rain 3.2 mm in the window'); the text then leads with it."""
    good, bad = [r for r in rs if r[1]], [r for r in rs if not r[1]]
    good_ph, bad_ph = [r[2] for r in good], [r[2] for r in bad]
    verb = lambda items, one, many: many if len(items) > 1 or items[0][4] else one
    limit = "will limit height" if len(bad) == 1 and bad[0][0] == "height" else "will hold it back"
    cap = lambda t: t[0].upper() + t[1:]
    if nogo_lead: return f"{nogo_lead} rules the day out." + (f" Otherwise {_join(good_ph)} would help." if good else "")
    if good and bad: return f"{cap(_join(good_ph))} {verb(good, 'helps', 'help')}, but {_join(bad_ph)} {limit}."
    if good: return f"{cap(_join(good_ph))} {verb(good, 'helps', 'help')}."
    if bad: return f"{cap(_join(bad_ph))} {verb(bad, 'holds', 'hold')} the day back."
    return "Nothing stands out: close to an average day."


# ---------------------------------------------------------------- the panel text: up to four short lines from templates
def _xc_times(share):
    x = round(share * 10)
    return "rarely went XC (20 km+)" if x <= 0 else "nearly always went XC (20 km+)" if x >= 10 else f"went XC (20 km+) about {x} {'time' if x == 1 else 'times'} in 10"


def panel_lines(rating_word, share, window, rs_all, rules_pct, disagree_rs=None, nogo_text=None, climb_top_m=None, climb_ms=None):
    """The dashboard text as a list of lines (about 50 words at most). rs_all: `reasons` for all factors. rules_pct: the rules' XC %. disagree_rs: `reasons` from the weather
    alone (no rule scores), used only when Creo and the rules clearly disagree. nogo_text: the no-go sentence; the panel then is just that."""
    if nogo_text: return [nogo_text]
    out = [f"Similar past days {_xc_times(share)}."]
    best = next((r for r in rs_all if r[1]), None); watch = next((r for r in rs_all if not r[1]), None)
    climbs = (f"climbs to about {round(climb_top_m, -2):,.0f} m" if climb_top_m is not None else "climbs") + (f" at {climb_ms:.1f} m/s" if climb_ms is not None else "")
    if climb_top_m is None and climb_ms is None: climbs = None
    head = f"Best: {window}" if window else "Best: no clear window today"
    line = head + (", " if window else "; ") + climbs if climbs else head
    if best and best[0] != "thermals": line += f", helped by {best[2]}"            # thermals are already said in the climbs
    best_line = line + "."; watch_line = "Watch: " + (watch[2] + "." if watch else "nothing stands out.")
    out += [watch_line, best_line] if rating_word == "Poor" else [best_line, watch_line]          # on a Poor day the drag comes first
    low_rules, high_rules = rules_pct is not None and rules_pct < 35, rules_pct is not None and rules_pct >= 65
    if (high_rules and rating_word == "Poor") or (low_rules and rating_word in ("Good", "Great")):
        up = low_rules; pick = next((r for r in (disagree_rs or []) if r[1] == up), None)
        out.append(f"Creo is more {'upbeat' if up else 'cautious'} than the rules: " + (f"past days with {pick[2]} tended to go {'well' if up else 'badly'}." if pick else "it reads the whole day differently."))
    return out
