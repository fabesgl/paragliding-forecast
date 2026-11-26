"""FreeFlightWx (Mystic station) CSV -> 10-minute averages. Pure functions; scripts/fetch_ffwx.py does the download and file handling.
The station's CSV (table.php?h=N&download=csv) lists a reading about every 11 s, newest first, in Victorian LOCAL time with the year included.
Units out: km/h, degrees, deg C, hPa (mean sea-level pressure). Timestamps out: UTC (and local for reading)."""
import datetime as dt
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

MPH = 1.609344
FULL_MAX_GAP_S = 30      # a 10-minute row is "full" when its readings are at most this far apart (median); the station logs every ~11 s, long downloads are thinned to ~1 per 3-4 min
COLUMNS = ["time_utc", "time_local", "resolution", "n_readings", "wind_min_kmh", "wind_avg_kmh", "wind_max_kmh", "wind_dir_deg", "temp_c", "dew_point_c", "pressure_hpa"]


def local_to_utc(local_strings, tz="Australia/Melbourne"):
    """Local clock strings ('YYYY-MM-DD HH:MM:SS') in the file's order, NEWEST FIRST -> UTC datetimes.
    The hour that repeats when daylight saving ends cannot be told apart by the clock alone: walking back from the newest reading, the clock first
    falls through the repeated hour (second pass, standard time), then jumps forward again into the first pass (daylight time)."""
    z = ZoneInfo(tz); out = []; fold = 1; prev = None
    for s in local_strings:
        t = dt.datetime.fromisoformat(s)
        if prev is not None and t > prev + dt.timedelta(minutes=5): fold = 0 if _ambiguous(t, z) else fold   # clock jumped forward going back in time
        elif prev is not None and not _ambiguous(t, z): fold = 1
        prev = t
        out.append(t.replace(tzinfo=z, fold=fold if _ambiguous(t, z) else 0).astimezone(dt.timezone.utc))
    return out


def _ambiguous(t, z):
    return t.replace(tzinfo=z, fold=0).utcoffset() != t.replace(tzinfo=z, fold=1).utcoffset()


def ten_minute(csv_df, tz="Australia/Melbourne"):
    """The raw station table -> one row per 10-minute bin (UTC bins; Melbourne's offset is a whole number of hours so they match local bins).
    Wind min/max = lowest/highest reading in the bin; wind avg = mean; direction = speed-weighted vector mean (plain circular mean when calm)."""
    d = csv_df.copy()
    d["t"] = pd.to_datetime(local_to_utc(d["Date_Time"].tolist(), tz))
    for c in ("Windspeedmph", "WindspeedmphMax", "WindspeedmphMin", "Winddir", "realQNH", "Tempc", "DewPoint"): d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["t"]); d["bin"] = d["t"].dt.floor("10min")
    rad = np.radians(d["Winddir"]); w = d["Windspeedmph"].fillna(0)
    d["u"], d["v"], d["su"], d["sv"] = w * np.sin(rad), w * np.cos(rad), np.sin(rad), np.cos(rad)
    g = d.groupby("bin")
    gap = d.sort_values("t").groupby("bin")["t"].apply(lambda x: x.diff().dt.total_seconds().median())     # NaN for a single reading
    out = pd.DataFrame({
        "n_readings": g.size(), "resolution": np.where(gap.reindex(g.size().index) <= FULL_MAX_GAP_S, "full", "thinned"),
        "wind_min_kmh": g["WindspeedmphMin"].min() * MPH, "wind_avg_kmh": g["Windspeedmph"].mean() * MPH, "wind_max_kmh": g["WindspeedmphMax"].max() * MPH,
        "temp_c": g["Tempc"].mean(), "dew_point_c": g["DewPoint"].mean(), "pressure_hpa": g["realQNH"].mean() / 100.0})
    u, v, su, sv = g["u"].sum(), g["v"].sum(), g["su"].sum(), g["sv"].sum()
    calm = np.hypot(u, v) < 1e-9
    out["wind_dir_deg"] = np.degrees(np.arctan2(np.where(calm, su, u), np.where(calm, sv, v))) % 360
    out = out.round({"wind_min_kmh": 1, "wind_avg_kmh": 1, "wind_max_kmh": 1, "wind_dir_deg": 0, "temp_c": 1, "dew_point_c": 1, "pressure_hpa": 1}).reset_index()
    z = ZoneInfo(tz)
    out["time_utc"] = out["bin"].dt.strftime("%Y-%m-%dT%H:%MZ")
    out["time_local"] = [b.astimezone(z).strftime("%Y-%m-%d %H:%M") for b in out["bin"]]
    return out[COLUMNS]


def with_resolution(df):
    """Saved files from before the resolution column existed: rows with few readings (under 20 per 10 minutes) are thinned."""
    if df is not None and "resolution" not in df.columns: df = df.assign(resolution=np.where(df["n_readings"] >= 20, "full", "thinned"))[COLUMNS]
    return df


def hours_to_request(last_utc, now_utc, max_hours=720):
    """Which h to ask the station for. last_utc: start of the newest saved 10-minute row (None = nothing saved). Under 24 h old -> 24 (full resolution).
    Otherwise the smallest whole number of hours that reaches back past that row, at most max_hours. Returns (hours, gap_lost): gap_lost is True when
    even max_hours does not reach it (readings in between are gone)."""
    if last_utc is None: return max_hours, False
    age_h = (now_utc - last_utc).total_seconds() / 3600
    if age_h < 24: return 24, False
    need = int(age_h) + 2                                  # a little margin so the newest saved row is re-covered
    return min(need, max_hours), need > max_hours


def merge(old, new):
    """Add new 10-minute rows to the saved ones. A bin already saved is kept unless the new copy has more readings (the newest bin is partial when downloaded)."""
    if old is None or old.empty: return new.sort_values("time_utc").reset_index(drop=True)
    both = pd.concat([old, new], ignore_index=True)
    both = both.sort_values(["time_utc", "n_readings"], ascending=[True, False]).drop_duplicates("time_utc", keep="first")
    return both.sort_values("time_utc").reset_index(drop=True)
