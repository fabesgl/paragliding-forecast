"""Download the Mystic weather station's recent history from FreeFlightWx and keep it as 10-minute averages in data/ffwx/mystic.csv (local, git-ignored).
The station's CSV only looks back N hours from now (a date range is ignored), so the script picks the window itself: nothing saved yet -> 720 h (30 days);
newest saved row under 24 h old -> 24 h; otherwise the smallest window that reaches back past the newest saved row (at most 720 h). Windows over 24 h come back
THINNED (about 1 reading per 3-4 minutes instead of every 11 s), so their wind max/min understate gusts. Each row has a `resolution` column, full or thinned
(thinned rows are never replaced by fewer readings, but a later full download of the same 10 minutes does replace them). Training should ignore the wind max/min of thinned rows.
Run it at least every 30 days or readings are lost (it warns if there is a gap).
One request per run. Any error or block stops the run; it never retries. Contact for the user-agent comes from the FFWX_CONTACT environment variable.
Usage: python scripts/fetch_ffwx.py [--hours N]     (--hours overrides the automatic window)"""
import io, os, sys, datetime as dt
import pandas as pd, requests
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import ffwx

URL = "https://www.freeflightwx.com/mystic/table.php"
OUT = ROOT / "data" / "ffwx" / "mystic.csv"
EXPECT = ["Date_Time", "Windspeedmph", "WindspeedmphMax", "WindspeedmphMin", "Winddir", "realQNH", "Tempc", "DewPoint"]


def main():
    old = ffwx.with_resolution(pd.read_csv(OUT)) if OUT.exists() else None
    last = None if old is None or old.empty else pd.Timestamp(old["time_utc"].max()).to_pydatetime()
    now = dt.datetime.now(dt.timezone.utc)
    hours, lost = ffwx.hours_to_request(last, now)
    if "--hours" in sys.argv: hours, lost = int(sys.argv[sys.argv.index("--hours") + 1]), False
    if hours > 24: print(f"Window {hours} h: rows older than about 24 h come back thinned (marked resolution=thinned; their wind max/min understate gusts).")
    if lost: print("WARNING: the newest saved row is more than 720 h old; readings between it and the start of this download are lost.")
    contact = os.environ.get("FFWX_CONTACT", "").strip()
    ua = "Bright paragliding forecast, personal use" + (f", contact {contact}" if contact else "")
    if not contact: print("FFWX_CONTACT is not set: sending the user-agent without contact details.")
    print(f"Requesting the last {hours} h ...", flush=True)
    try:
        r = requests.get(URL, params={"h": hours, "download": "csv"}, headers={"User-Agent": ua}, timeout=300)
    except requests.RequestException as e:
        sys.exit(f"Stopped: {type(e).__name__}. Nothing saved, no retry.")
    if r.status_code != 200 or "csv" not in r.headers.get("content-type", ""):
        sys.exit(f"Stopped: HTTP {r.status_code}, content-type {r.headers.get('content-type')}. Nothing saved, no retry.")
    raw = pd.read_csv(io.StringIO(r.text))
    missing = [c for c in EXPECT if c not in raw.columns]
    if missing or raw.empty: sys.exit(f"Stopped: the CSV has no data or lacks columns {missing}. Nothing saved.")
    new = ffwx.ten_minute(raw)
    print(f"Downloaded {len(raw):,} readings = {len(new):,} ten-minute rows ({(new['resolution'] == 'full').sum():,} full, {(new['resolution'] == 'thinned').sum():,} thinned), {new['time_local'].min()} to {new['time_local'].max()} (local).")
    if old is not None and len(old) and new["time_utc"].min() > old["time_utc"].max() and not lost:
        print(f"WARNING: gap. Saved data ends {old['time_utc'].max()}, this download starts {new['time_utc'].min()}. Those readings are lost.")
    merged = ffwx.merge(old, new); OUT.parent.mkdir(parents=True, exist_ok=True); merged.to_csv(OUT, index=False)
    print(f"Saved {OUT}: {len(merged):,} rows ({len(merged) - (0 if old is None else len(old)):,} new), {merged['time_local'].min()} to {merged['time_local'].max()} (local).")


if __name__ == "__main__": main()
