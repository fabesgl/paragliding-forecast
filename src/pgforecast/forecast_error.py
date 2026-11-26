"""Forecast error against BARRA-C2: how far each archived forecast model is from the reanalysis at the same hours.

BARRA-C2 (BOM's 4.4 km reanalysis) is used as the stand-in for "what really happened". It is a model too, so these numbers
are "disagreement with BARRA-C2", not error against the real air. Pure functions only; scripts/forecast_error.py does the I/O.

Sign convention everywhere: error = forecast - BARRA-C2 (positive = the forecast is too high).
"""
import numpy as np
import pandas as pd

SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]          # same 45-degree sectors as corrections.py
SEASONS = {12: "summer", 1: "summer", 2: "summer", 3: "autumn", 4: "autumn", 5: "autumn",
           6: "winter", 7: "winter", 8: "winter", 9: "spring", 10: "spring", 11: "spring"}   # southern hemisphere
LOCAL_HOURS = range(11, 17)                                       # 11:00..16:00 Victorian local time, inclusive
KMH = 3.6

# name -> (kind, unit). kind: 'speed' (ratio makes sense), 'add' (plain difference), 'dir' (circular)
VARIABLES = {
    "wind_speed_10m":        ("speed", "km/h"),
    "wind_gusts_10m":        ("speed", "km/h"),
    "wind_speed_850hPa":     ("speed", "km/h"),
    "wind_speed_700hPa":     ("speed", "km/h"),
    "wind_direction_10m":    ("dir", "deg"),
    "wind_direction_850hPa": ("dir", "deg"),
    "wind_direction_700hPa": ("dir", "deg"),
    "temperature_2m":        ("add", "°C"),
    "dew_point_2m":          ("add", "°C"),
    "temperature_850hPa":    ("add", "°C"),
    "temperature_700hPa":    ("add", "°C"),
    "cloud_base_agl":        ("speed", "m"),      # derived: 125 m per degree of temperature - dew point spread
    "boundary_layer_height": ("speed", "m"),
    "cape":                  ("speed", "J/kg"),
}
DIR_MIN_SPEED = 8.0        # km/h: below this in BARRA the wind direction is noise, so direction errors skip those hours


def dew_point(t_c, rh_pct):
    """Magnus formula. t_c in °C, rh_pct 0-100."""
    a, b = 17.625, 243.04
    g = np.log(np.clip(rh_pct, 0.5, 100.0) / 100.0) + a * t_c / (b + t_c)
    return b * g / (a - g)


def cloud_base_agl(t_c, td_c):
    """Cloud base above ground in metres from the temperature/dew-point spread (125 m per degree), 0..6000."""
    return np.clip(125.0 * (np.asarray(t_c) - np.asarray(td_c)), 0.0, 6000.0)


def uv_to_speed_dir(u, v):
    """u (east), v (north) -> speed (same unit) and the direction the wind blows FROM (degrees, 0 = north)."""
    return np.hypot(u, v), (180.0 + np.degrees(np.arctan2(u, v))) % 360.0


def circ_diff(a, b):
    """Smallest signed angle a - b in degrees, in (-180, 180]."""
    d = (np.asarray(a, float) - np.asarray(b, float) + 180.0) % 360.0 - 180.0
    return np.where(d == -180.0, 180.0, d)


def sector_of(deg):
    """8-way sector name for a direction in degrees; None for missing."""
    deg = np.asarray(deg, float)
    idx = (((deg % 360.0) + 22.5) // 45.0).astype("float64")
    out = np.full(deg.shape, None, dtype=object)
    ok = ~np.isnan(deg)
    out[ok] = np.array(SECTORS, dtype=object)[idx[ok].astype(int) % 8]
    return out


def bilinear(lats, lons, field, lat, lon):
    """Bilinear interpolation of field[time, lat, lon] to one point. lats/lons ascending; the point must lie inside the grid."""
    lats, lons = np.asarray(lats), np.asarray(lons)
    if not (lats[0] <= lat <= lats[-1] and lons[0] <= lon <= lons[-1]):
        raise ValueError(f"point {lat},{lon} outside grid")
    i = int(np.clip(np.searchsorted(lats, lat) - 1, 0, len(lats) - 2))
    j = int(np.clip(np.searchsorted(lons, lon) - 1, 0, len(lons) - 2))
    fy = (lat - lats[i]) / (lats[i + 1] - lats[i]); fx = (lon - lons[j]) / (lons[j + 1] - lons[j])
    f = np.asarray(field)
    return (f[:, i, j] * (1 - fy) * (1 - fx) + f[:, i, j + 1] * (1 - fy) * fx
            + f[:, i + 1, j] * fy * (1 - fx) + f[:, i + 1, j + 1] * fy * fx)


def add_derived(df):
    """Add the derived columns (cloud base) to a table that has temperature_2m and dew_point_2m."""
    df["cloud_base_agl"] = cloud_base_agl(df["temperature_2m"], df["dew_point_2m"])
    df.loc[df["temperature_2m"].isna() | df["dew_point_2m"].isna(), "cloud_base_agl"] = np.nan
    return df


LAPSE_C_PER_KM = 6.5       # standard temperature drop with height, used to move BARRA-C2's 2 m temperature to the forecast's elevation


def height_correction_c(forecast_elev_m, barra_elev_m, lapse=LAPSE_C_PER_KM):
    """Degrees to ADD to BARRA-C2's 2 m temperature so it applies at the forecast's elevation.
    Open-Meteo downscales its temperatures to the point's real elevation; BARRA-C2's smoothed 4.4 km terrain is often lower (warmer)."""
    return -lapse / 1000.0 * (forecast_elev_m - barra_elev_m)


def season_of(month):
    return SEASONS[int(month)]


def error_columns(fc, bm, kind, bm_speed=None):
    """Error series for one variable. For directions, hours where BARRA's wind is below DIR_MIN_SPEED are dropped (NaN)."""
    fc, bm = np.asarray(fc, float), np.asarray(bm, float)
    if kind == "dir":
        err = circ_diff(fc, bm)
        if bm_speed is not None: err = np.where(np.asarray(bm_speed) >= DIR_MIN_SPEED, err, np.nan)
        return err
    return fc - bm


def summarise(g, kind="speed"):
    """g: DataFrame with columns fc, bm, err, date. Returns one dict of error statistics (None when empty)."""
    g = g.dropna(subset=["fc", "bm", "err"])
    n = len(g)
    if n == 0: return None
    e = g["err"].to_numpy(float); f = g["fc"].to_numpy(float); b = g["bm"].to_numpy(float)
    sd_f, sd_b = f.std(), b.std()
    r = float(np.corrcoef(f, b)[0, 1]) if n > 2 and sd_f > 0 and sd_b > 0 else None
    mb = float(b.mean())
    return dict(n=int(n), n_days=int(g["date"].nunique()), bias=float(e.mean()), median_err=float(np.median(e)),
                sd=float(e.std()), mae=float(np.abs(e).mean()), rmse=float(np.sqrt((e ** 2).mean())),
                p10=float(np.percentile(e, 10)), p90=float(np.percentile(e, 90)),
                fc_mean=float(f.mean()), bm_mean=mb, ratio=(float(f.mean() / mb) if (kind == "speed" and mb > 0) else None), r=r)


def split_groups(df):
    """Yield (split_type, split_value, sub-frame) for the breakdowns we report: overall, season, wind-direction sector."""
    yield "all", "all", df
    for s, sub in df.groupby("season"): yield "season", s, sub
    for s, sub in df.dropna(subset=["sector"]).groupby("sector"): yield "sector", s, sub


def prior_correction(kind, stats):
    """Starting correction in the same form corrections.py uses: a multiplier (BARRA / forecast) for speed-like
    variables, an additive amount (BARRA - forecast) for temperatures. None for directions or if too little data."""
    if kind == "dir" or stats is None: return None
    if kind == "speed":
        return None if not stats["fc_mean"] or stats["fc_mean"] <= 0 else stats["bm_mean"] / stats["fc_mean"]
    return -stats["bias"]


COMPASS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass_to_deg(name):
    """'NNW' -> 337.5; 'CALM' or anything unknown -> NaN."""
    try: return COMPASS16.index(str(name).strip().upper()) * 22.5
    except ValueError: return float("nan")


def corrected_rmse(fc, truth, kind):
    """Error that is left after the best simple bias correction: the spread of (forecast - truth) for 'add' variables, or the RMSE of
    truth - k*forecast with the best multiplier k for 'speed' variables. This is what a correction layer cannot remove, so it is the fair
    score for ranking models (a model with a steady bias is not 'worse' than one without)."""
    f, t = np.asarray(fc, float), np.asarray(truth, float)
    ok = ~(np.isnan(f) | np.isnan(t)); f, t = f[ok], t[ok]
    if len(f) < 3: return float("nan")
    if kind == "add": return float(np.std(f - t))
    den = float((f * f).sum())
    if den <= 0: return float("nan")
    k = float((f * t).sum() / den)
    return float(np.sqrt(np.mean((t - k * f) ** 2)))
