"""Rolling bias corrections: how far each model is off at a BOM station, by model, variable, point and wind direction.

How it learns (all walk-forward, so a correction is always judged on days it has not seen):
  * Each finished day, for every bin, compare the forecast made the evening before with the BOM observations (hours 09-17 local).
  * A bin's correction is a slow exponential average of the daily bias (additive for temperature/dew point/pressure,
    a multiplier for wind and gusts).
  * No correction exists until a bin has MIN_DAYS days of data. From then on, each new day is also scored with the
    correction as it stood *before* that day, and compared with the raw forecast.
  * A correction is only APPLIED when it has been checked on at least MIN_EVAL past days and cut the average error
    by at least MIN_GAIN. If it stops helping it switches itself off again.
"""
import math

MIN_DAYS, MIN_EVAL, MIN_GAIN = 30, 10, 0.02      # days of data to start; past days it must be checked on; smallest error cut (2%)
ALPHA_MIN, DECAY = 0.05, 0.97                    # slowest learning rate (about a 20-day memory); forgetting factor for the error scores
SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
HOURS = range(9, 18)                             # local hours used: 09:00-17:00
# forecast variable -> (BOM column, how the correction works, unit)
VARS = {"temperature_2m": ("air_temp", "add", "°C"), "dew_point_2m": ("dewpt", "add", "°C"), "wind_speed_10m": ("wind_spd_kmh", "mul", "x"),
        "wind_gusts_10m": ("gust_kmh", "mul", "x"), "pressure_msl": ("press_msl", "add", "hPa")}

def sector(deg):
    return None if deg is None else SECTORS[int(((deg % 360) + 22.5) // 45) % 8]

def new_state(): return {"version": 1, "updated_utc": None, "last_day": None, "bins": {}}
def bin_key(model, point, var, sec): return f"{model}|{point}|{var}|{sec}"

def _clamp(kind, x): return max(0.5, min(2.0, x)) if kind == "mul" else x
def _apply_one(kind, corr, x): return x * corr if kind == "mul" else x + corr

def status(b):
    """learning -> validating -> active / off."""
    if b["n_days"] < MIN_DAYS: return "learning"
    if b["eval_days"] < MIN_EVAL: return "validating"
    return "active" if b["mae_corr"] <= b["mae_raw"] * (1 - MIN_GAIN) else "off"

def gain(b):
    return None if not b["eval_days"] or not b["mae_raw"] else 1 - b["mae_corr"] / b["mae_raw"]

def update_day(state, model, point, samples):
    """samples: list of dict(dir=forecast wind direction deg or None, fc={var: forecast}, ob={var: observation}) for the day's hours.
    Updates every bin of this model and point once. Call each day only once (the caller tracks the last processed day)."""
    for var, (_, kind, _) in VARS.items():
        for sec in ["all"] + SECTORS:
            rows = [(s["fc"].get(var), s["ob"].get(var)) for s in samples if (sec == "all" or sector(s["dir"]) == sec)]
            rows = [(f, o) for f, o in rows if f is not None and o is not None]
            if len(rows) < (4 if sec == "all" else 2): continue
            if kind == "mul":
                if sum(f for f, _ in rows) / len(rows) < 3: continue                    # too calm for a ratio to mean anything
                daily = _clamp(kind, sum(o for _, o in rows) / sum(f for f, _ in rows))
            else:
                daily = sum(o - f for f, o in rows) / len(rows)
            b = state["bins"].setdefault(bin_key(model, point, var, sec), dict(kind=kind, corr=None, n_days=0, eval_days=0, w=0.0, mae_raw=0.0, mae_corr=0.0, acc_raw=0.0, acc_corr=0.0))
            if b["n_days"] >= MIN_DAYS and b["corr"] is not None:                       # score the correction as it was BEFORE today
                raw = sum(abs(o - f) for f, o in rows) / len(rows)
                cor = sum(abs(o - _apply_one(kind, b["corr"], f)) for f, o in rows) / len(rows)
                b["w"] = DECAY * b["w"] + 1; b["acc_raw"] = DECAY * b["acc_raw"] + raw; b["acc_corr"] = DECAY * b["acc_corr"] + cor
                b["mae_raw"], b["mae_corr"] = b["acc_raw"] / b["w"], b["acc_corr"] / b["w"]; b["eval_days"] += 1
            a = max(ALPHA_MIN, 1.0 / (b["n_days"] + 1))                                  # plain average at first, then slow
            b["corr"] = daily if b["corr"] is None else b["corr"] + a * (daily - b["corr"])
            b["corr"] = _clamp(kind, b["corr"]); b["n_days"] += 1
    return state

def correction_for(state, model, point, var, direction):
    """The correction to use for one hour: this wind-direction sector if it has proven itself, else (if the sector is still
    being learned) the all-direction one. Returns (kind, value) or None."""
    sec = sector(direction)
    sb = state["bins"].get(bin_key(model, point, var, sec)) if sec else None
    if sb is not None and status(sb) == "active": return sb["kind"], sb["corr"]
    if sb is not None and status(sb) == "off": return None                               # enough data, and it did not help: leave alone
    ab = state["bins"].get(bin_key(model, point, var, "all"))
    if ab is not None and status(ab) == "active": return ab["kind"], ab["corr"]
    return None

def apply_hourly(state, model, point, hourly):
    """Correct one model's hourly series for one point, in place. Returns how many values changed."""
    d = hourly.get(f"wind_direction_10m_{model}") or []
    n = 0
    for var in VARS:
        series = hourly.get(f"{var}_{model}")
        if not series: continue
        for i, x in enumerate(series):
            if x is None: continue
            c = correction_for(state, model, point, var, d[i] if i < len(d) else None)
            if c is None: continue
            series[i] = round(max(0.0, _apply_one(c[0], c[1], x)) if c[0] == "mul" else _apply_one(c[0], c[1], x), 2); n += 1
    t, td = hourly.get(f"temperature_2m_{model}"), hourly.get(f"dew_point_2m_{model}")
    if t and td:
        for i in range(min(len(t), len(td))):
            if t[i] is not None and td[i] is not None and td[i] > t[i]: td[i] = t[i]    # dew point can never exceed the temperature
    return n

def summary(state):
    """Counts for the dashboard header."""
    c = {"learning": 0, "validating": 0, "active": 0, "off": 0}
    for b in state["bins"].values(): c[status(b)] += 1
    return c
