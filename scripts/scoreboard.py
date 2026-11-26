"""Scoreboard: how well did the current rules (sites/<site>/rules.yaml) predict real flying at Mystic?
For every day with flight data (days with incomplete XContest data are skipped), the XC % is scored twice:
  A = from the archived historical forecasts (what the dashboard would have said; each model scored, then averaged, like the live 'equal' method)
  B = from BARRA-C2 (what the rules say given the weather that actually happened; BARRA ends where data/barra2 ends)
and compared with what pilots did (flown or not, best distance; tandems excluded). A vs B shows whether misses come from the forecast or the rules.
Read-only on all data. Writes data/scoreboard/ (report.md, days.csv; report_d1.md for --lead d1); local only.
Usage: python scripts/scoreboard.py [site_id] [--from 2024-01-01] [--lead hist|d1|d2] [--launch mystic] [--rebuild-barra]
  --lead: which archived forecast: hist (default, short lead, all four models) or d1/d2 (1 or 2 days ahead; GFS, ECMWF, ACCESS-G only).
Needs data/xcontest_flights.sqlite, data/igc_flights.sqlite (optional), data/forecast.sqlite, data/barra2/ (optional), data/chat_labels.sqlite (optional)."""
import datetime as dt, json, sqlite3, sys, yaml
import numpy as np, pandas as pd
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast.rules import score_day
from pgforecast.rules_config import load_rules
from pgforecast import forecast_error as FE, scoreboard as SB

args = sys.argv[1:]
def opt(name, default):
    return args[args.index(name) + 1] if name in args else default
site = next((a for i, a in enumerate(args) if not a.startswith("-") and (i == 0 or args[i - 1] not in ("--from", "--lead", "--launch"))), "bright")
FIRST, LEAD, LAUNCH_ID = opt("--from", "2024-01-01"), opt("--lead", "hist"), opt("--launch", "mystic")
cfg = yaml.safe_load((ROOT / "sites" / f"{site}.yaml").read_text()); rules = load_rules(site)
cal = yaml.safe_load((ROOT / "sites" / site / "calendar.yaml").read_text()) if (ROOT / "sites" / site / "calendar.yaml").exists() else {}
HOLIDAYS = [str(d) for d in cal.get("public_holidays", [])]; EVENTS = cal.get("bright_open", [])
TZ, LAUNCH = cfg["timezone"], cfg["launch_height_m"]; WIN = [int(x[:2]) for x in cfg.get("flying_window_local", ["11:00", "16:00"])]
OUT = ROOT / "data" / "scoreboard"; OUT.mkdir(parents=True, exist_ok=True)
SUFFIX = "" if LEAD == "hist" else f"_{LEAD}"          # report_d1.md etc. so a different forecast lead never overwrites the main report
MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "bom_access_global"]       # the dashboard's models
VARS = ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "wind_speed_850hPa", "wind_direction_850hPa", "wind_speed_700hPa", "temperature_2m",
        "dew_point_2m", "cloud_cover_low", "cape", "boundary_layer_height", "shortwave_radiation", "precipitation"]
MIN_HOURS = 4                                                                         # of the 6 window hours (11:00-16:00)


def xc_summary(h):
    """Rules on one day's window -> (XC %, {rule: factor})."""
    r = score_day(h, rules, LAUNCH)
    return r["xc"], {x["key"]: x["factor"] for x in r["xc_rules"] if x["factor"] is not None}


def window(df):
    """Keep the flying-window hours in local time; adds local `date` and `hour`."""
    loc = df["time_utc"].dt.tz_localize("UTC").dt.tz_convert(TZ)
    df = df[(loc.dt.hour >= WIN[0]) & (loc.dt.hour <= WIN[1])].copy(); df["date"] = loc[df.index].dt.strftime("%Y-%m-%d"); return df


def weakest(factors_list):
    """The rule with the lowest average factor over several scorings (the rule that held the day back)."""
    keys = {k for f in factors_list for k in f}
    avg = {k: np.mean([f[k] for f in factors_list if k in f]) for k in keys}
    return min(avg, key=avg.get) if avg else None


# ---------- A: archived forecasts ----------
def score_forecasts():
    src, lead = ("hist", 0) if LEAD == "hist" else ("prev", int(LEAD[1:]))
    db = sqlite3.connect(ROOT / "data" / "forecast.sqlite", timeout=60)
    df = pd.read_sql(f"SELECT model, time_utc, {','.join(VARS)} FROM fc WHERE source=? AND lead_days=? AND point_key='mystic_launch' AND time_utc>=? AND model IN ({','.join('?' * len(MODELS))})",
                     db, params=[src, lead, FIRST] + MODELS)
    df["time_utc"] = pd.to_datetime(df["time_utc"]); df = window(df); out = {}
    for date, g in df.groupby("date"):
        hours = sorted(g["time_utc"].unique()); piv = {m: g[g["model"] == m].set_index("time_utc").reindex(hours) for m in MODELS if (g["model"] == m).any()}
        per = []
        for m, p in piv.items():
            if p["wind_speed_10m"].notna().sum() < MIN_HOURS: continue
            h = {}
            for v in VARS:                                  # gaps (e.g. no boundary-layer height from ECMWF) are filled from the other models, as the dashboard does
                own = p[v].tolist(); others = [piv[o][v].tolist() for o in piv if o != m]; col = []
                for i, x in enumerate(own):
                    if pd.isna(x):
                        vals = [o[i] for o in others if not pd.isna(o[i])]; x = sum(vals) / len(vals) if vals else None
                    col.append(None if x is None or pd.isna(x) else float(x))
                h[v] = col
            pct, f = xc_summary(h)
            if pct is not None: per.append((pct, f))
        if per: out[date] = dict(xc_a=round(sum(p for p, _ in per) / len(per)), n_models=len(per), limit_a=weakest([f for _, f in per]))
    return pd.DataFrame.from_dict(out, orient="index").rename_axis("date").reset_index()


# ---------- B: BARRA-C2 at Mystic ----------
BARRA_VARS = ["uas", "vas", "wsgs", "ua850", "va850", "ua700", "va700", "tas", "hurs", "zmla", "CAPE", "rsds", "pr"]

def barra_mystic():
    """BARRA-C2 interpolated to Mystic, one row per UTC hour, in the rules' variable names. Cached in data/scoreboard/barra_mystic.pkl (rebuilt when more files arrive)."""
    import xarray as xr
    p = cfg["points"]["mystic_launch"]; folder = ROOT / "data" / "barra2"
    files = sorted(folder.glob("bright_area__*__*.nc")) if folder.exists() else []
    cache, stamp = OUT / "barra_mystic.pkl", OUT / "barra_mystic.stamp"
    if cache.exists() and stamp.exists() and stamp.read_text() == str(len(files)) and "--rebuild-barra" not in args: return pd.read_pickle(cache)
    if not files: return None
    cols = {}
    for v in BARRA_VARS:
        parts = []
        for f in files:
            if f.name.split("__")[1] != v: continue
            with xr.open_dataset(f) as ds:
                da = ds[v].load(); t = pd.to_datetime(da["time"].values)
                if (t.minute == 30).all(): t = t + pd.Timedelta(minutes=30)          # hourly means are stamped mid-hour: label them with the hour they end (like the forecasts)
                parts.append(pd.Series(FE.bilinear(da["lat"].values, da["lon"].values, da.values, p["lat"], p["lon"]), index=t))
        if parts: cols[v] = pd.concat(parts)
    if any(v not in cols for v in BARRA_VARS): print("BARRA-C2 variables missing:", [v for v in BARRA_VARS if v not in cols]); return None
    d = pd.DataFrame(cols).sort_index(); d = d[~d.index.duplicated()]
    out = pd.DataFrame(index=d.index)
    out["wind_speed_10m"], out["wind_direction_10m"] = FE.uv_to_speed_dir(d["uas"], d["vas"]); out["wind_speed_10m"] *= FE.KMH
    out["wind_gusts_10m"] = d["wsgs"] * FE.KMH
    out["wind_speed_850hPa"], out["wind_direction_850hPa"] = FE.uv_to_speed_dir(d["ua850"], d["va850"]); out["wind_speed_850hPa"] *= FE.KMH
    out["wind_speed_700hPa"] = FE.uv_to_speed_dir(d["ua700"], d["va700"])[0] * FE.KMH
    el = json.loads((ROOT / "data" / "forecast_error" / "elevations.json").read_text()) if (ROOT / "data" / "forecast_error" / "elevations.json").exists() else {}
    adj = FE.height_adjustments(cfg["points"], el).get("mystic_launch", 0.0)        # same move to Open-Meteo's elevation as forecast_error.py
    out["temperature_2m"] = d["tas"] - 273.15 + adj
    out["dew_point_2m"] = FE.dew_point(out["temperature_2m"], d["hurs"])
    out["boundary_layer_height"], out["cape"], out["shortwave_radiation"] = d["zmla"], d["CAPE"], d["rsds"]
    out["precipitation"] = d["pr"] * 3600.0                                           # kg m-2 s-1 over an hour -> mm
    out["cloud_cover_low"] = np.nan
    out = out.rename_axis("time_utc").reset_index(); out.to_pickle(cache); stamp.write_text(str(len(files))); return out


def score_barra():
    b = barra_mystic()
    if b is None: return pd.DataFrame(columns=["date", "xc_b", "limit_b"])
    b = window(b[b["time_utc"] >= pd.Timestamp(FIRST)]); out = []
    for date, g in b.groupby("date"):
        if g["wind_speed_10m"].notna().sum() < MIN_HOURS: continue
        pct, f = xc_summary({v: [None if pd.isna(x) else float(x) for x in g[v]] for v in VARS})
        if pct is not None: out.append(dict(date=date, xc_b=pct, limit_b=weakest([f])))
    return pd.DataFrame(out)


# ---------- labels ----------
def labels():
    x = sqlite3.connect(ROOT / "data" / "xcontest_flights.sqlite")
    flights = x.execute("SELECT date, glider_class, distance_km FROM xc_flight WHERE site=? AND date>=?", (LAUNCH_ID, FIRST)).fetchall()
    status = dict(x.execute("SELECT date, status FROM xc_day_status WHERE site=?", (LAUNCH_ID,)).fetchall())
    complete = {m for (m,) in x.execute("SELECT month FROM xc_month_status WHERE site=? AND complete=1", (LAUNCH_ID,))}
    igc = []
    if (ROOT / "data" / "igc_flights.sqlite").exists():
        g = sqlite3.connect(ROOT / "data" / "igc_flights.sqlite")
        igc = [d for (d,) in g.execute("SELECT DISTINCT date FROM igc_flight WHERE launch_site=? AND date>=?", (LAUNCH_ID, FIRST))]
    last = max(status) if status else FIRST
    return SB.build_day_labels(flights, igc, status, complete, FIRST, last, EVENTS, cal.get("comp_distance_weight", SB.COMP_DISTANCE_WEIGHT)), len(status), sum(1 for d in status if d >= FIRST and status[d] == "unknown")


def season(date): return FE.season_of(int(date[5:7]))


def main():
    lab, _, n_unknown = labels()
    print(f"labelled days: {len(lab)} (skipped unknown: {n_unknown})"); a = score_forecasts(); b = score_barra()
    df = lab.merge(a, on="date", how="left").merge(b, on="date", how="left")
    df["season"] = df["date"].map(season); df["free"] = df["date"].map(lambda d: SB.is_free_day(d, HOLIDAYS)); df["bright_open"] = df["date"].map(lambda d: SB.in_events(d, EVENTS))
    df["weekday"] = df["date"].map(lambda d: dt.date.fromisoformat(d).strftime("%a"))
    df.to_csv(OUT / f"days{SUFFIX}.csv", index=False)
    both = df.dropna(subset=["xc_a", "xc_b"]); fmt_r = lambda r: "n/a" if r is None else f"{r:+.2f}"
    f0 = lambda v: f"{v:.0f}"; fp = lambda v: f"{v:.0f}%"
    L = [f"# Scoreboard: {site} XC rules vs real flying", "",
         f"Made {dt.datetime.now():%Y-%m-%d %H:%M}. Rules: `sites/{site}/rules.yaml` as they are now. Forecast archive: `{LEAD}` (each model scored, then averaged; no live bias correction).",
         "Days with incomplete XContest data are skipped, not counted as 'nobody flew'. Tandems are excluded. No pilot names appear here.", ""]
    L += ["## Data", "",
          f"- Days scored with a label: **{len(df)}** ({df['date'].min()} to {df['date'].max()}); {int(df['flown'].sum())} flown, {int((~df['flown']).sum())} not flown; {n_unknown} unknown days skipped.",
          f"- Flown days judged only by an IGC track (distance unknown, left out of distance stats): {int((df['source'] == 'igc').sum())}.",
          f"- A (forecast) available on {int(df['xc_a'].notna().sum())} days (models per day: {df['n_models'].min():.0f} to {df['n_models'].max():.0f}); B (BARRA-C2) on {int(df['xc_b'].notna().sum())} days; both on {len(both)}.",
          f"- Free days (weekend or public holiday): {int(df['free'].sum())}; Bright Open days: {int(df['bright_open'].sum())}. Holiday and Bright Open dates come from `sites/{site}/calendar.yaml`.", ""]
    ra, rb, rba = SB.spearman(df["xc_a"], df["best_km"]), SB.spearman(df["xc_b"], df["best_km"]), SB.spearman(both["xc_a"], both["best_km"])
    ra_f = SB.spearman(df[df["flown"]]["xc_a"], df[df["flown"]]["best_km"]); rb_f = SB.spearman(df[df["flown"]]["xc_b"], df[df["flown"]]["best_km"])
    L += ["## Overall score", "", "Rank correlation between XC % and best distance of the day (nobody flew = 0 km). +1 = perfect order, 0 = no link.", "",
          SB.md_table(pd.DataFrame([
              dict(version="A: forecast", days=int(df["xc_a"].notna().sum()), all_days=fmt_r(ra), flown_days_only=fmt_r(ra_f)),
              dict(version="B: BARRA-C2 (actual weather)", days=int(df["xc_b"].notna().sum()), all_days=fmt_r(rb), flown_days_only=fmt_r(rb_f)),
              dict(version="A on the same days as B", days=len(both), all_days=fmt_r(rba), flown_days_only=fmt_r(SB.spearman(both[both["flown"]]["xc_a"], both[both["flown"]]["best_km"]))),
          ])), "- *all_days*: does a higher % mean someone flew and flew further. *flown_days_only*: given that someone flew, does the % rank the distance.",
          "- A well below B = the forecast is the weak link. B itself low = the rules (or BARRA-C2) are the weak link.", ""]
    for name, col in (("A: from the forecast", "xc_a"), ("B: from BARRA-C2", "xc_b")):
        L += [f"## Bands, {name}", "", SB.md_table(SB.band_table(df, col), {"pct_flown": fp, "median_best_km": f0}), "Median best distance is over days someone flew (km).", ""]
    def groups(col):
        defs = [("all days", df)] + [(f"season: {s}", df[df["season"] == s]) for s in ("summer", "autumn", "winter", "spring")] + [
            ("weekends/holidays", df[df["free"]]), ("weekdays", df[~df["free"]]),
            ("without Bright Open weeks", df[~df["bright_open"]]), ("Bright Open weeks only", df[df["bright_open"]])]
        return pd.DataFrame([SB.group_row(n, g, col) for n, g in defs if len(g)])
    gfmt = {"pct_flown": fp, "low_pct_flown": fp, "high_pct_flown": fp, "spearman": fmt_r}
    for name, col in (("A: forecast", "xc_a"), ("B: BARRA-C2", "xc_b")):
        L += [f"## Splits, {name}", "", SB.md_table(groups(col), gfmt),
              f"*low* = days scored under {SB.LOW_PCT}%, *high* = {SB.HIGH_PCT}% or more. A good rule set has few flown days when low and many when high.", ""]
    fa, sp = SB.misses(df, "xc_a")
    def miss_rows(m):
        m = m.assign(best_km=m["best_km"]); return m[["date", "weekday", "free", "xc_a", "xc_b", "limit_a", "limit_b", "n_flights", "best_km"]].rename(columns={"xc_a": "A%", "xc_b": "B%", "limit_a": "A held back by", "limit_b": "B held back by", "free": "weekend/hol"})
    mfmt = {"A%": f0, "B%": f0, "best_km": f0, "n_flights": f0, "weekend/hol": lambda v: "yes" if v else ""}
    L += [f"## Biggest misses, going by A (forecast)", "", f"### High %, but nobody flew or best flight under {SB.MISS_KM:.0f} km",
          SB.md_table(miss_rows(fa), mfmt), f"### Low % (under {SB.LOW_PCT}), but flown, longest first", SB.md_table(miss_rows(sp), mfmt),
          "*A held back by* / *B held back by* = the rule with the lowest score that day. If A and B disagree, the forecast was wrong; if both agree with a high %, the rules missed something.", ""]
    ok, why = SB.acro_status(*acro_inputs())
    L += ["## Acro", "", ("Acro labels exist but acro scoring is not built yet: " if ok else "Not scored: ") + why + ".", ""]
    (OUT / f"report{SUFFIX}.md").write_text("\n".join(L)); print("\n".join(L)); print(f"\nWritten: {OUT / 'report.md'}, {OUT / 'days.csv'}")


def acro_inputs():
    p = ROOT / "data" / "chat_labels.sqlite"
    if not p.exists(): return [], []
    db = sqlite3.connect(p)
    return [r[1] for r in db.execute("PRAGMA table_info(chat_label)")], db.execute("SELECT DISTINCT kind FROM chat_label").fetchall()


if __name__ == "__main__": main()
