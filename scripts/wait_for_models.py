"""Wait (up to 75 min) until Open-Meteo has the newest ECMWF, GFS and ICON runs, and record which runs they are.
Open-Meteo publishes each model's latest run in https://api.open-meteo.com/data/<model>/static/meta.json.
Typical delay from run start to availability (from the model-update data, 2026-11-25): ECMWF ~7.3 h, GFS ~8.3 h, ICON ~3.5 h.
Only waits when run by the schedule; manual runs just record what is there. Never fails the job."""
import csv, datetime as dt, json, os, time, requests
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT if os.environ.get("GITHUB_ACTIONS") else ROOT / "data" / "local_out"
MODELS = {"ecmwf_ifs025": 7.5, "ncep_gfs025": 8.5, "dwd_icon": 3.75}   # typical hours until the run is available, plus a little margin
MAX_WAIT_S = 75 * 60
def meta(m):
    r = requests.get(f"https://api.open-meteo.com/data/{m}/static/meta.json", timeout=30); r.raise_for_status()
    j = r.json(); f = lambda t: dt.datetime.fromtimestamp(t, dt.UTC)
    return f(j["last_run_initialisation_time"]), f(j["last_run_availability_time"])
def expected_init(m, now):
    for back in range(0, 48, 6):          # newest 00/06/12/18Z run that should be out by now
        t = (now - dt.timedelta(hours=back)).replace(minute=0, second=0, microsecond=0)
        t = t.replace(hour=t.hour // 6 * 6)
        if t + dt.timedelta(hours=MODELS[m]) <= now: return t
start = dt.datetime.now(dt.UTC); wait = os.environ.get("GITHUB_EVENT_NAME") == "schedule"
state = {}
while True:
    now = dt.datetime.now(dt.UTC); pending = []
    for m in MODELS:
        try: init, avail = meta(m)
        except Exception as e: print(m, "meta error", type(e).__name__); pending.append(m); continue
        exp = expected_init(m, now); state[m] = (init, avail, exp)
        if init < exp: pending.append(m)
    waited = (now - start).total_seconds()
    if not pending or not wait or waited > MAX_WAIT_S: break
    print(f"waiting for {pending} ({waited/60:.0f} min so far)", flush=True); time.sleep(300)
rows = []
for m, (init, avail, exp) in state.items():
    ok = init >= exp
    rows.append([now.strftime("%Y-%m-%dT%H:%MZ"), m, init.strftime("%Y-%m-%dT%H:%MZ"), avail.strftime("%Y-%m-%dT%H:%MZ"),
                 f"{(avail - init).total_seconds()/3600:.2f}", f"{(now - start).total_seconds()/60:.0f}", "newest" if ok else "OLDER-THAN-EXPECTED"])
    print(rows[-1])
d = OUT / "model_runs"; d.mkdir(parents=True, exist_ok=True)
new = not (d / "log.csv").exists()
with (d / "log.csv").open("a", newline="") as f:
    w = csv.writer(f)
    if new: w.writerow(["checked_utc", "model", "run_init_utc", "available_utc", "delay_h", "waited_min", "status"])
    w.writerows(rows)
(d / "latest.json").write_text(json.dumps({r[1]: {"run_init_utc": r[2], "available_utc": r[3], "status": r[6]} for r in rows}))
