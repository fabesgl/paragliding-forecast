"""Download BOM 'Daily Weather Observations' monthly CSVs (9 am and 3 pm readings plus daily max gust) for a few stations into data/bom_dwo/.
Public pages; polite pace; resumable (finished months are skipped; the current month is always refreshed).
Usage: python scripts/fetch_bom_dwo.py [first_yyyymm] [station_page_id ...]   e.g. 202103 3055 3027 3081"""
import datetime as dt, sys, time, requests
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "bom_dwo"; OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
first = next((a for a in sys.argv[1:] if len(a) == 6), "202103")
ids = [a for a in sys.argv[1:] if len(a) == 4] or ["3055", "3027", "3056", "3081"]   # Hotham, Falls Creek, Hotham Airport, Wangaratta
today = dt.date.today(); y, m = int(first[:4]), int(first[4:])
months = []
while (y, m) <= (today.year, today.month):
    months.append(f"{y}{m:02d}"); m += 1
    if m == 13: y, m = y + 1, 1
for sid in ids:
    missing = 0
    for ym in months:
        f = OUT / f"IDCJDW{sid}.{ym}.csv"
        if f.exists() and ym != months[-1]: continue
        r = requests.get(f"https://www.bom.gov.au/climate/dwo/{ym}/text/IDCJDW{sid}.{ym}.csv", headers=UA, timeout=30)
        if r.status_code == 200: f.write_bytes(r.content); print("ok", sid, ym, len(r.content), flush=True)
        else: missing += 1; print("missing", sid, ym, r.status_code, flush=True)
        time.sleep(0.5)
