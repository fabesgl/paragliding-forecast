"""Train the Creo model on every usable day (labelled, complete months, 2022 on) and save it to sites/<site>/creo_model.txt for the daily job's shadow mode.
Same features, weights and settings as scripts/creo_test.py (the leave-one-year-out test). The file holds only trees and feature names: no flights, dates or pilots.
Usage: python scripts/creo_train.py        (laptop; needs `pip install -r requirements-creo.txt` and the imported flights). Re-run when more months are imported, then commit the file."""
import hashlib, importlib.util, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("creo_test_script", ROOT / "scripts" / "creo_test.py"); CT = importlib.util.module_from_spec(spec); spec.loader.exec_module(CT)
import lightgbm as lgb

def main():
    df, _ = CT.dataset()
    model = lgb.train(CT.PARAMS, lgb.Dataset(df[CT.FEATURES], df["y"], weight=df["distance_weight"]), num_boost_round=CT.ROUNDS)
    out = ROOT / "sites" / "bright" / "creo_model.txt"; model.save_model(str(out))
    meta = ROOT / "sites" / "bright" / "creo_model.json"           # which days each model version saw, so creo_review.py only scores days it did not train on (dates only, no flights)
    versions = json.loads(meta.read_text())["versions"] if meta.exists() else {}
    versions[hashlib.sha1(out.read_bytes()).hexdigest()[:8]] = dict(trained_through=df["date"].max(), first_day=df["date"].min(), days=len(df))
    meta.write_text(json.dumps(dict(versions=versions), indent=1) + "\n")
    print(f"Trained on {len(df)} days ({df['date'].min()} to {df['date'].max()}), {len(CT.FEATURES)} features, {CT.ROUNDS} rounds. Saved {out} ({out.stat().st_size // 1024} KB).")

if __name__ == "__main__": main()
