"""Test possible XC rule changes against real flying with the scoreboard data, leave-one-year-out. READ-ONLY: the live rules and the dashboard are never touched;
each change is applied here, to the per-rule factors that rules.score_day returns (the unchanged scoring is reproduced exactly and checked).
Variants (pick with --variants, comma separated; default baseline,west_off,west):
  baseline     the rules as they are NOW in sites/<site>/rules.yaml (the westerly_launch setting was tried 2026-11-27 and reverted, so baseline = no westerly launch)
  west_off     the rules without direction.westerly_launch: the westerly penalty (a W/WNW 10 m wind scores 0 for XC)
  west         XC only: a W/WNW 10 m wind (247-315 deg) counts as launchable at all times (the westerly penalty removed completely)
  west_valley  XC only: W/WNW launchable when the thermic valley wind is established, the same check the rules use for southerlies
               (valley wind = estimated thermals x valley_wind_kmh_per_thermal_ms must beat the launch wind, fading over fade_kmh).
               (Identical to baseline only if the live rules have westerly_launch on.)
  rain         scattered showers (rain in fewer than s window hours; an hour is wet at t mm or more) only lower the score to 1 - wet hours/hours; steady rain keeps today's rule
  west+rain    both of those (west = the full removal)
Leave-one-year-out: only the rain settings (t in 0.1/0.3/1.0 mm, s in 3/4/5 hours) are tuned, on two years and applied to the third. The westerly variants have nothing to
tune (they use the rules' own valley-wind numbers), so they are simply scored year by year. Scores: rank correlation of XC % with best distance (nobody flew = 0 km).
Usage: python scripts/rule_tests.py [--variants a,b,c] [--out name]      Writes data/scoreboard/<name>.md (default rule_tests). Needs data/scoreboard/days.csv (run scripts/scoreboard.py first)."""
import importlib.util, sqlite3, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
args = sys.argv[1:]; sys.argv = sys.argv[:1]                      # scoreboard.py reads sys.argv at import
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("scoreboard_script", ROOT / "scripts" / "scoreboard.py"); M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
from pgforecast import scoreboard as SB
from pgforecast.rules import _window_features, _in_arc, _smooth, _mean

opt = lambda name, default: args[args.index(name) + 1] if name in args else default
VARIANTS = {"baseline": dict(west=None, rain=False), "west_off": dict(west="off", rain=False), "west": dict(west="full", rain=False), "west_valley": dict(west="valley", rain=False),
            "rain": dict(west=None, rain=True), "west+rain": dict(west="full", rain=True)}
CHOSEN = opt("--variants", "baseline,west_off,west").split(","); OUTNAME = opt("--out", "rule_tests")
if any(v not in VARIANTS for v in CHOSEN) or "baseline" not in CHOSEN: sys.exit(f"--variants must include baseline and only use: {', '.join(VARIANTS)}")
R = M.rules; D = R["direction"]; SL = D["southerly_launch"]
WEST_ARC = tuple(D["westerly_launch"]["arc_deg"]) if D.get("westerly_launch") else (247, 315)      # between the southerly arc (ends 247) and the launch arc (starts 315)


def direction_factor(f, west):
    """The direction rule as in rules.score_day (XC). west: None (the live rules), 'off' (no westerly launch), 'full' (westerly always launchable) or
    'valley' (launchable when the valley wind wins, like a southerly)."""
    if west is None: west = "valley" if D.get("westerly_launch") else "off"
    k, fade = SL["valley_wind_kmh_per_thermal_ms"], SL["fade_kmh"]
    valley = lambda s, th: _smooth((k * (th or 0.0) - s + fade) / (2 * fade))
    def hour(s, dd, th):
        if _in_arc(dd, tuple(D["launch_arc_deg"])) or s < D["calm_below_kmh"]: return 1.0
        if west == "full" and _in_arc(dd, WEST_ARC): return 1.0
        if (west == "valley" and _in_arc(dd, WEST_ARC)) or _in_arc(dd, tuple(SL["arc_deg"])): return valley(s, th)
        return 0.0
    in_arc = _mean([hour(s, dd, th) for s, dd, th in f["hours"] if s is not None and dd is not None])
    syn_bad = f["w850"] is not None and f["d850"] is not None and _in_arc(f["d850"], tuple(D["unflyable_arc_deg"])) and f["w850"] > D["unflyable_min_850_kmh"]
    return 0.0 if syn_bad else (in_arc if in_arc is not None else 1.0)


def rec_from_h(h):
    r = M.score_day(h, R, M.LAUNCH); f = _window_features(h, M.LAUNCH, R["estimates"])
    return dict(xc=r["xc"], fac={x["key"]: x["factor"] for x in r["xc_rules"] if x["factor"] is not None}, f=f, precip=[x for x in h["precipitation"] if x is not None], cape=f["cape"])


def variant_pct(rec, west=None, rain=False, t=0.1, s=4):
    fac = dict(rec["fac"])
    if west: fac["direction"] = direction_factor(rec["f"], west)
    if rain and rec["precip"]:
        wet = sum(1 for x in rec["precip"] if x >= t)
        if 0 < wet < s: fac["rain"] = 1 - wet / len(rec["precip"])
    if not west and not (rain and rec["precip"]): return rec["xc"]
    return round(100 * max(0, min(1, min(fac.values()))))


def day_pct(recs, **kw):
    v = [variant_pct(r, **kw) for r in recs]; return round(sum(v) / len(v))


def load_records():
    """Per day: A = list of per-model records (forecast, each model scored then averaged, as the dashboard's 'equal' method); B = one record from BARRA-C2."""
    db = sqlite3.connect(ROOT / "data" / "forecast.sqlite")
    raw = pd.read_sql(f"SELECT model, time_utc, {','.join(M.VARS)} FROM fc WHERE source='hist' AND lead_days=0 AND point_key='mystic_launch' AND model IN ({','.join('?' * len(M.MODELS))})", db, params=M.MODELS)
    raw["time_utc"] = pd.to_datetime(raw["time_utc"]); raw = M.window(raw[raw["time_utc"] >= pd.Timestamp(M.FIRST) - pd.Timedelta(days=1)])
    A = {}
    for date, g in raw.groupby("date"):
        hours = sorted(g["time_utc"].unique()); piv = {m: g[g["model"] == m].set_index("time_utc").reindex(hours) for m in M.MODELS if (g["model"] == m).any()}; recs = []
        for m, p in piv.items():
            if p["wind_speed_10m"].notna().sum() < M.MIN_HOURS: continue
            h = {}
            for v in M.VARS:                      # gaps filled from the other models, as the dashboard does
                own = p[v].tolist(); others = [piv[o][v].tolist() for o in piv if o != m]; col = []
                for i, x in enumerate(own):
                    if pd.isna(x):
                        vals = [o[i] for o in others if not pd.isna(o[i])]; x = sum(vals) / len(vals) if vals else None
                    col.append(None if x is None or pd.isna(x) else float(x))
                h[v] = col
            rr = rec_from_h(h)
            if rr["xc"] is not None: recs.append(rr)
        if recs: A[date] = recs
    B = {}; b = M.barra_mystic()
    if b is not None:
        b = M.window(b[b["time_utc"] >= pd.Timestamp(M.FIRST)])
        for date, g in b.groupby("date"):
            if g["wind_speed_10m"].notna().sum() < M.MIN_HOURS: continue
            rr = rec_from_h({v: [None if pd.isna(x) else float(x) for x in g[v]] for v in M.VARS})
            if rr["xc"] is not None: B[date] = [rr]
    return A, B


GRID = [(t, s) for t in (0.1, 0.3, 1.0) for s in (3, 4, 5)]
def score(df, col): return SB.spearman(df[col], df["best_km"])


def run(src, lab):
    df = lab.merge(pd.DataFrame({"date": list(src)}), on="date"); df["year"] = df["date"].str[:4]; years = sorted(df["year"].unique()); chosen = {}
    for name in CHOSEN:
        kw = VARIANTS[name]
        if not kw["rain"]: df[name] = [day_pct(src[d], **kw) for d in df["date"]]; continue
        col = pd.Series(index=df.index, dtype=float)
        for y in years:                           # tune the rain settings on the other years, apply to this one
            tr = df[df["year"] != y]
            best = max(GRID, key=lambda p: (score(tr.assign(x=[day_pct(src[d], **kw, t=p[0], s=p[1]) for d in tr["date"]]), "x") or -9))
            chosen[(name, y)] = best; te = df[df["year"] == y]; col.loc[te.index] = [day_pct(src[d], **kw, t=best[0], s=best[1]) for d in te["date"]]
        df[name] = col
    return df, chosen


def bootstrap(df, rng):
    """95% range of (score of a variant - score of baseline) over 2000 resamples of the days."""
    df = df.dropna(subset=["best_km"]).reset_index(drop=True); x = df["best_km"].rank().to_numpy(); b0 = df["baseline"].rank().to_numpy(); rows = []
    for v in CHOSEN[1:] if CHOSEN[0] == "baseline" else [c for c in CHOSEN if c != "baseline"]:
        b1 = df[v].rank().to_numpy(); d = []
        for _ in range(2000):
            i = rng.integers(0, len(df), len(df)); d.append(np.corrcoef(b1[i], x[i])[0, 1] - np.corrcoef(b0[i], x[i])[0, 1])
        rows.append((v, np.corrcoef(b1, x)[0, 1] - np.corrcoef(b0, x)[0, 1], np.percentile(d, 2.5), np.percentile(d, 97.5)))
    return rows


def main():
    lab = pd.read_csv(M.OUT / "days.csv")[["date", "flown", "best_km", "n_flights", "source"]]
    A, B = load_records()
    bad = sum(1 for rs in A.values() for r in rs if abs(direction_factor(r["f"], None) - r["fac"].get("direction", 0)) > 0.002)
    mism = sum(1 for rs in A.values() if day_pct(rs, west=None, rain=False) != round(sum(r["xc"] for r in rs) / len(rs)))
    print(f"check: direction recompute mismatches {bad}, baseline mismatches {mism} (both must be 0)")
    if bad or mism: sys.exit("The unchanged scoring is not reproduced exactly; stopping.")
    r2 = lambda v: "n/a" if v is None else f"{v:+.3f}"
    L = [f"# Rule change tests (XC), leave-one-year-out: {', '.join(CHOSEN)}", "",
         f"Made {pd.Timestamp.now():%Y-%m-%d}. Nothing in the live rules or dashboard was changed. Same days and labels as the scoreboard; rank correlation of XC % with best distance (nobody flew = 0 km).",
         "Variants: " + "; ".join(f"**{v}**" for v in CHOSEN) + ". See the docstring of `scripts/rule_tests.py` for what each does. Check passed: the unchanged scoring is reproduced exactly.", ""]
    rng = np.random.default_rng(1); boots = []
    for name, src in (("A: from the forecast", A), ("B: from BARRA-C2 (actual weather)", B)):
        if not src: continue
        df, chosen = run(src, lab); ov = []
        for n in CHOSEN:
            r = dict(version=n, days=len(df), all=score(df, n), flown_only=SB.spearman(df[df.flown][n], df[df.flown]["best_km"]))
            for y in sorted(df["year"].unique()): r[y] = score(df[df["year"] == y], n)
            ov.append(r)
        ov = pd.DataFrame(ov)
        L += [f"## {name}", "", "### Overall score (rank correlation, higher is better)", "", SB.md_table(ov, {c: r2 for c in ov.columns if c not in ("version", "days")}), ""]
        if chosen: L += ["Rain settings chosen (t mm, s hours) per held-out year: " + "; ".join(f"{k[0]} {k[1]}: {v}" for k, v in chosen.items()), ""]
        bands = {n: SB.band_table(df, n) for n in CHOSEN}
        comb = pd.DataFrame({"band": bands["baseline"]["band"], **{n: [f"{r.days} / {r.pct_flown:.0f}% / {'-' if pd.isna(r.median_best_km) else f'{r.median_best_km:.0f}'}" for r in bands[n].itertuples()] for n in CHOSEN}})
        L += ["### Bands: days / % of days flown / median best distance (km, flown days)", "", SB.md_table(comb), ""]
        boots += [(name.split(":")[0], *b) for b in bootstrap(df, rng)]
    L += ["## Is the change bigger than noise? (paired bootstrap over days, 95% range of the score difference from baseline)", "", "| source | variant | difference | 95% range |", "|---|---|---|---|"]
    L += [f"| {s} | {v} | {d:+.3f} | {lo:+.3f} to {hi:+.3f} |" for s, v, d, lo, hi in boots]
    (M.OUT / f"{OUTNAME}.md").write_text("\n".join(L) + "\n"); print("\n".join(L)); print(f"\nWritten: {M.OUT / (OUTNAME + '.md')}")


if __name__ == "__main__": main()
