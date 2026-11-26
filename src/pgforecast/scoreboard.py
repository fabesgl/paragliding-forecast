"""Scoreboard maths: did the rules' XC % match real flying? Pure functions (no files, no network); scripts/scoreboard.py does the I/O.
A day's label = was it flown, how many flights, best and median distance. A day's score = the rules' XC % (from the forecast, or from BARRA-C2).
Tandem flights never count. Unknown days (saved XContest flights incomplete) are skipped, not treated as 'nobody flew'."""
import datetime as dt
import numpy as np
import pandas as pd

BANDS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]       # the last band includes 100
MISS_KM = 20.0           # a "short" flight for the false-alarm list: best distance under this
LOW_PCT, HIGH_PCT = 40, 60


def band_of(pct):
    """'0-20' ... '80-100' for a % (None/NaN -> None). 20 belongs to the band above, 100 to the top band."""
    if pct is None or pd.isna(pct): return None
    for lo, hi in BANDS:
        if pct < hi or hi == 100: return f"{lo}-{hi}"


def spearman(x, y):
    """Rank correlation (-1..1) between two series, using only rows where both exist; None if fewer than 5 rows or one side never varies."""
    d = pd.DataFrame({"x": pd.to_numeric(x, errors="coerce"), "y": pd.to_numeric(y, errors="coerce")}).dropna()
    if len(d) < 5 or d["x"].nunique() < 2 or d["y"].nunique() < 2: return None
    return float(d["x"].rank().corr(d["y"].rank()))


def build_day_labels(xc_flights, igc_dates, day_status, complete_months, first, last):
    """One row per day from first to last that can be judged.
    xc_flights: rows (date, glider_class, distance_km). igc_dates: dates with a saved track from Mystic. day_status: {date: 'flew'|'no_flights'|'unknown'}
    (XContest day status). complete_months: {'YYYY-MM'} where every XContest flight is saved.
    Returns DataFrame: date, flown, n_flights, best_km, median_km, source ('xcontest' | 'igc' (a track proves a flight, distance unknown) | 'none'), skipped dates are left out."""
    by = {}
    for d, cls, km in xc_flights:
        if cls == "T": continue                                              # tandems never count
        by.setdefault(d, []).append(float(km) if km is not None else np.nan)
    igc = set(igc_dates); rows = []
    d = dt.date.fromisoformat(first)
    while d <= dt.date.fromisoformat(last):
        s = d.isoformat(); kms = [k for k in by.get(s, []) if not np.isnan(k)]; n = len(by.get(s, []))
        st = day_status.get(s)
        if n:
            rows.append(dict(date=s, flown=True, n_flights=n, best_km=max(kms) if kms else np.nan, median_km=float(np.median(kms)) if kms else np.nan, source="xcontest"))
        elif s in igc:
            rows.append(dict(date=s, flown=True, n_flights=np.nan, best_km=np.nan, median_km=np.nan, source="igc"))
        elif st == "no_flights" or (st == "flew" and s[:7] in complete_months):     # 'flew' with no solo flight = tandem-only day
            rows.append(dict(date=s, flown=False, n_flights=0, best_km=0.0, median_km=0.0, source="none"))
        d += dt.timedelta(days=1)                                                  # anything else is unknown: skipped
    return pd.DataFrame(rows)


def is_free_day(date, holidays):
    """Saturday, Sunday or a listed public holiday (date strings)."""
    return dt.date.fromisoformat(date).weekday() >= 5 or date in set(holidays)


def in_events(date, events):
    """True if the date falls in any {start, end} window (inclusive); dates may be strings or date objects."""
    d = dt.date.fromisoformat(str(date))
    return any(dt.date.fromisoformat(str(e["start"])) <= d <= dt.date.fromisoformat(str(e["end"])) for e in events)


def _km(x): return "" if pd.isna(x) else f"{x:.0f}"


def band_table(df, col):
    """One row per XC band for the score column `col`: days, % of days flown, median best distance on days flown (km)."""
    d = df.dropna(subset=[col]).copy(); d["band"] = d[col].map(band_of); rows = []
    for lo, hi in BANDS:
        g = d[d["band"] == f"{lo}-{hi}"]; fl = g[g["flown"]]
        rows.append(dict(band=f"{lo}-{hi}%", days=len(g), pct_flown=(100 * len(fl) / len(g)) if len(g) else np.nan,
                         median_best_km=float(fl["best_km"].median()) if fl["best_km"].notna().any() else np.nan))
    return pd.DataFrame(rows)


def group_row(name, g, col):
    """Summary of one group of days (a season, weekdays, ...): days, % flown, rank correlation, % flown on low-% and on high-% days."""
    g = g.dropna(subset=[col]); low, high = g[g[col] < LOW_PCT], g[g[col] >= HIGH_PCT]
    pf = lambda x: (100 * x["flown"].mean()) if len(x) else np.nan
    return dict(group=name, days=len(g), pct_flown=pf(g), spearman=spearman(g[col], g["best_km"]),
                low_days=len(low), low_pct_flown=pf(low), high_days=len(high), high_pct_flown=pf(high))


def misses(df, col, n=10):
    """(false_alarms, surprises). False alarm: a high % but nobody flew, or the best flight was under MISS_KM; ranked by % (highest first), weekends/holidays
    first among ties (people were free and still did not fly), then the shortest flight. Surprise: a % under LOW_PCT but a best flight of MISS_KM or more, longest first."""
    d = df.dropna(subset=[col]).copy(); d["_free"] = d["free"].astype(int)
    fa = d[(~d["flown"]) | (d["best_km"] < MISS_KM)].sort_values([col, "_free", "best_km"], ascending=[False, False, True]).head(n)
    sp = d[(d[col] < LOW_PCT) & (d["best_km"] >= MISS_KM)].sort_values("best_km", ascending=False).head(n)
    return fa.drop(columns="_free"), sp.drop(columns="_free")


def md_table(df, fmt=None):
    """DataFrame -> GitHub markdown table. fmt: {column: callable}. NaN/None show as '-'."""
    fmt = fmt or {}
    def cell(c, v):
        if v is None or (isinstance(v, float) and np.isnan(v)): return "-"
        return fmt[c](v) if c in fmt else str(v)
    head = "| " + " | ".join(df.columns) + " |\n|" + "|".join("---" for _ in df.columns) + "|\n"
    return head + "".join("| " + " | ".join(cell(c, v) for c, v in zip(df.columns, r)) + " |\n" for r in df.itertuples(index=False))


def acro_status(columns, rows):
    """Can acro be scored? Only if the chat labels carry an acro field/kind with data. columns: chat_label column names; rows: [(kind,)] of its kind values.
    Returns (can_score, reason)."""
    acro_cols = [c for c in columns if "acro" in c.lower()]
    acro_kinds = [k for (k,) in rows if k and "acro" in str(k).lower()]
    if acro_cols or acro_kinds: return True, f"chat labels have acro data ({', '.join(acro_cols) or 'kind'})"
    return False, "chat labels have no acro field (they record flown / wind / turbulence / thermals / rating, not acro days), so acro cannot be scored yet"
