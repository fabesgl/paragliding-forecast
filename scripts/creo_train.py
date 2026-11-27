"""Train the Creo model on every usable day (labelled, complete months, 2022 on) and save it to sites/<site>/creo_model.txt for the daily job (dashboard panel + shadow log). Also works out the rating words' cut points and prints how many past days get each word.
Same features, weights and settings as scripts/creo_test.py (the leave-one-year-out test). The file holds only trees and feature names: no flights, dates or pilots.
Usage: python scripts/creo_train.py        (laptop; needs `pip install -r requirements-creo.txt` and the imported flights). Re-run when more months are imported, then commit the file."""
import hashlib, importlib.util, json, sys
import numpy as np
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import creo
spec = importlib.util.spec_from_file_location("creo_test_script", ROOT / "scripts" / "creo_test.py"); CT = importlib.util.module_from_spec(spec); spec.loader.exec_module(CT)
import lightgbm as lgb

def main():
    df, _ = CT.dataset()
    model = lgb.train(CT.PARAMS, lgb.Dataset(df[CT.FEATURES], df["y"], weight=df["distance_weight"]), num_boost_round=CT.ROUNDS)
    out = ROOT / "sites" / "bright" / "creo_model.txt"; model.save_model(str(out))
    meta = ROOT / "sites" / "bright" / "creo_model.json"           # which days each model version saw, so creo_review.py only scores days it did not train on (dates only, no flights)
    versions = json.loads(meta.read_text())["versions"] if meta.exists() else {}
    res, _ = CT.loyo(df, sorted(df["year"].unique()))                       # out-of-sample (leave-one-year-out) estimates for every past day
    cuts = creo.calibrate_cuts(res["creo_raw"], res["best_km"])               # the estimate at which similar past days' MEDIAN actual best flight reaches 5 / 20 / 50 / 100 km
    res["rating"] = [creo.rating(e, cuts) if x > 0 else "Poor" for e, x in zip(res["creo_raw"], res["xc_pct"])]             # a rules no-go is always Poor
    table = []
    for w in creo.RATINGS:
        g = res[res["rating"] == w]; fl = g[g["flown"]]
        table.append(f"  {w:<6} {len(g):5d} days ({100 * len(g) / len(res):4.1f}%)   median actual best flight {g['best_km'].median() if len(g) else float('nan'):5.1f} km   flown {100 * len(fl) / max(len(g), 1):3.0f}%")
    versions = json.loads(meta.read_text())["versions"] if meta.exists() else {}
    versions[hashlib.sha1(out.read_bytes()).hexdigest()[:8]] = dict(trained_through=df["date"].max(), first_day=df["date"].min(), days=len(df), rating_cuts_km=cuts, rating_bounds_km=[5, 20, 50],
        xc_curve=creo.calibrate_share(res[res["xc_pct"] > 0]["creo_raw"], res[res["xc_pct"] > 0]["best_km"] >= 20),   # share of similar past days with a best flight of 20 km or more
        rating_counts={w: int((res["rating"] == w).sum()) for w in creo.RATINGS}, rating_basis="estimate at which the median actual best flight of past days with a similar leave-one-year-out estimate (101 nearest days, made non-decreasing) reaches 5/20/50/100 km")
    meta.write_text(json.dumps(dict(versions=versions), indent=1) + "\n")
    print("Rating words over the past days (leave-one-year-out estimates):"); print("\n".join(table))
    print(f"Rating cut points (estimate, km): {cuts}")
    print(f"Trained on {len(df)} days ({df['date'].min()} to {df['date'].max()}), {len(CT.FEATURES)} features, {CT.ROUNDS} rounds. Saved {out} ({out.stat().st_size // 1024} KB).")

if __name__ == "__main__": main()
