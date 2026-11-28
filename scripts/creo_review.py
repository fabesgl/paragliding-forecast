"""Review Creo's shadow-mode predictions against the flights you have imported (laptop). Read-only.
The daily job logs, for every forecast day, the rules' XC % and Creo's predicted best distance in predictions/creo_log.csv (pull first so you have the newest log).
This scores both against what really happened with the scoreboard's method: rank correlation with the day's best distance (nobody flew = 0 km), the same days for both,
a resampled 95% range of the difference, and bands. One row per forecast lead (lead 1 = made the day before, the dashboard's "tomorrow"); for each day and lead the LATEST log row counts.
Only days whose flights are imported, in months with complete XContest data, with a known distance, are scored.
Usage: python scripts/creo_review.py [--log predictions/creo_log.csv]       Writes data/creo/review.md"""
import importlib.util, json, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
args = sys.argv[1:]
LOG = Path(args[args.index("--log") + 1]) if "--log" in args else ROOT / "predictions" / "creo_log.csv"
if not LOG.exists(): sys.exit(f"No Creo log at {LOG}. The daily job writes it; run `git pull` first (it appears after the first job run with Creo shadow mode on).")
log = pd.read_csv(LOG)
log = log[log["lead_days"] >= 1].reset_index(drop=True)      # lead 0 rows can be made during or after the flying window: only forecasts made the day before or earlier count
if log.empty: sys.exit("The Creo log is empty.")
sys.argv = ["scoreboard", "--from", log["target_date"].min()]       # the scoreboard script reads its start date from the command line
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("scoreboard_script", ROOT / "scripts" / "scoreboard.py"); M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
from pgforecast import creo, scoreboard as SB

MIN_DAYS_RANGE, MIN_DAYS_BANDS = 30, 50
r3 = lambda v: "n/a" if v is None else f"{v:+.3f}"
pct = lambda v: "-" if pd.isna(v) else f"{v:.0f}%"
f0 = lambda v: "-" if pd.isna(v) else f"{v:.0f}"


def main():
    lab, _, _ = M.labels()
    lab = lab[lab["month_complete"] & lab["best_km"].notna()][["date", "flown", "best_km"]] if len(lab) else pd.DataFrame(columns=["date", "flown", "best_km"])
    latest = log.sort_values("made_at_utc").groupby(["target_date", "lead_days"], as_index=False).last()
    meta = ROOT / "sites" / "bright" / "creo_model.json"; versions = json.loads(meta.read_text())["versions"] if meta.exists() else {}
    seen = latest.copy(); latest = latest[~latest.apply(lambda r: str(r["model"]) in versions and r["target_date"] <= versions[str(r["model"])]["trained_through"], axis=1)]
    df = latest.merge(lab, left_on="target_date", right_on="date", how="inner")
    L = ["# Creo shadow-mode review", "", f"Made {pd.Timestamp.now():%Y-%m-%d}. Log: {len(log)} rows, {log['target_date'].nunique()} forecast days ({log['target_date'].min()} to {log['target_date'].max()}), model version(s) {', '.join(sorted(log['model'].astype(str).unique()))}.",
         f"Left out because the model had trained on those days: {len(seen) - len(latest)} log rows (cut-offs in `sites/bright/creo_model.json`).",
         f"Days with imported, complete-month flights and a known distance: {df['target_date'].nunique()}. Newer days are scored once their flights are imported (`scripts/import_xcontest.py`, `scripts/import_igc.py`).", ""]
    if df.empty:
        L += ["No logged day has imported flights yet, so there is nothing to score."]; out = "\n".join(L)
    else:
        rows = []
        for lead, g in df.groupby("lead_days"):
            g = g.dropna(subset=["rules_xc_pct", "creo_km"]); n = len(g)
            r, c = creo.spearman_fast(g["rules_xc_pct"], g["best_km"]) if n >= 5 else None, creo.spearman_fast(g["creo_km"], g["best_km"]) if n >= 5 else None
            rng = "too few days"
            if n >= MIN_DAYS_RANGE and r is not None and c is not None: d, lo, hi = creo.bootstrap_diff(g["best_km"], g["creo_km"], g["rules_xc_pct"]); rng = f"{lo:+.3f} to {hi:+.3f}"
            rows.append(dict(lead_days=int(lead), days=n, rules=r3(r), creo=r3(c), difference=r3(None if r is None or c is None else c - r), range_95=rng))
        L += ["## Rank correlation with the day's best distance (higher is better), by forecast lead", "", SB.md_table(pd.DataFrame(rows)),
              f"Difference = Creo minus rules. The 95% range needs at least {MIN_DAYS_RANGE} days. Only days after each model version's training data are scored. Creo was trained on short-lead forecasts, so lead 3-5 rows may be weaker than lead 1.", ""]
        g1 = df[df["lead_days"] == 1].dropna(subset=["rules_xc_pct", "creo_km"])
        if len(g1) >= MIN_DAYS_BANDS:
            g1 = g1.assign(x=g1["rules_xc_pct"], flown=g1["flown"].astype(bool)); rb = SB.band_table(g1, "x")
            cb = creo.matched_bands(g1["creo_km"].to_numpy(), g1["rules_xc_pct"].to_numpy()); cr = []
            for b in ("0-20", "20-40", "40-60", "60-80", "80-100"):
                s = g1[cb == b]; fl = s[s["flown"]]
                cr.append(dict(band=b + "%", days=len(s), pct_flown=100 * len(fl) / len(s) if len(s) else np.nan, median_best_km=float(fl["best_km"].median()) if len(fl) else np.nan))
            fm = {"pct_flown": pct, "median_best_km": f0}
            L += ["## Bands, lead 1 (made the day before)", "", "### Rules (XC %)", "", SB.md_table(rb, fm), "", "### Creo (same number of days per band as the rules, by predicted distance)", "", SB.md_table(pd.DataFrame(cr), fm), ""]
        else: L += [f"Bands need at least {MIN_DAYS_BANDS} scored days at lead 1 (have {len(g1)})."]
        out = "\n".join(L)
    (ROOT / "data" / "creo").mkdir(parents=True, exist_ok=True); (ROOT / "data" / "creo" / "review.md").write_text(out + "\n"); print(out); print("\nWritten: data/creo/review.md")


if __name__ == "__main__": main()
