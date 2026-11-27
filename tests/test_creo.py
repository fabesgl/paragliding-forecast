"""Run: .venv/bin/python -m unittest tests.test_creo -v   (pure maths, no data files)"""
import sys, unittest
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import creo


class T(unittest.TestCase):
    def test_hard_limit_zeroes_no_go_days(self):
        self.assertEqual(creo.hard_limit([30, 12, 5], [0, 40, 100]).tolist(), [0, 12, 5])

    def test_matched_bands_have_the_reference_counts(self):
        ref = [5, 10, 25, 50, 55, 70, 85, 95, 100, 100]
        lab = creo.matched_bands(np.arange(10), ref)
        self.assertEqual([int((lab == b).sum()) for b in ("0-20", "20-40", "40-60", "60-80", "80-100")], [2, 1, 2, 1, 4])
        self.assertEqual(lab[0], "0-20"); self.assertEqual(lab[9], "80-100")             # the lowest scores land in the lowest band

    def test_direction_is_sin_cos_of_the_mean_vector(self):
        f = creo.day_features([{"wind10_u": -5.0, "wind10_v": 0.0, "wind850_u": 0.0, "wind850_v": 0.0, "wind700_u": None, "wind700_v": None}])    # wind blowing west = from the east
        self.assertAlmostEqual(f["wind10_sin"], 1.0); self.assertAlmostEqual(f["wind10_cos"], 0.0, places=6)
        self.assertEqual((f["wind850_sin"], f["wind850_cos"]), (0.0, 0.0))                                  # calm: no direction
        n = creo.model_features({"wind_speed_10m": [10, 10], "wind_direction_10m": [350, 10], "wind_speed_850hPa": [None, None], "wind_direction_850hPa": [None, None], "wind_speed_700hPa": [5, 5],
                                 "wind_direction_700hPa": [90, 90], "temperature_2m": [20, 22], "dew_point_2m": [10, 10]},
                                dict(xc=80, key=dict(gust_kmh=20, cloudbase_agl_m=1500, cape=10, rain_mm=0), xc_rules=[dict(key="rain", factor=1.0)]), 797)
        self.assertEqual(n["cloud_base_asl"], 2297); self.assertEqual(n["temp"], 21); self.assertEqual(n["rule_rain"], 1.0)
        self.assertAlmostEqual(abs(n["wind10_u"]), 0.0, places=6)                                           # 350 and 10 degrees: the east-west parts cancel

    def test_bootstrap_difference_sign(self):
        truth = np.arange(60.0); good, bad = truth + np.random.default_rng(0).normal(0, 3, 60), np.random.default_rng(1).normal(0, 30, 60)
        d, lo, hi = creo.bootstrap_diff(truth, good, bad, n=200); self.assertGreater(d, 0); self.assertGreater(lo, 0)


if __name__ == "__main__": unittest.main()


class Panel(unittest.TestCase):
    F = dict(wind10_speed=8.0, wind10_sin=0.0, wind10_cos=1.0, wind850_speed=14.0, wind850_sin=-1.0, wind850_cos=0.0, wind700_speed=20.0, wind700_sin=0.0, wind700_cos=0.0,
             gust=18.0, cloud_base_asl=1930.0, cape=50.0, rain=0.0)

    def test_rating_words_follow_the_calibrated_cuts(self):
        cuts = [0.2, 1.0, 3.0, 14.0]
        self.assertEqual([creo.rating(k, cuts) for k in (0, 0.5, 2, 5, 20)], ["Poor", "Fair", "Good", "Great", "Epic"]); self.assertEqual(creo.rating(14.0, cuts), "Epic"); self.assertIsNone(creo.rating(None, cuts))
        self.assertEqual(creo.rating(999, [1, 2, 3, None]), "Great")                                 # a word no estimate reaches is never given

    def test_calibrated_cuts_use_the_median_of_similar_days(self):
        est = [1, 2, 3, 4, 5, 6] * 30; act = [0, 0, 10, 30, 60, 200] * 30
        self.assertEqual(creo.calibrate_cuts(est, act, k=11), [3.0, 4.0, 5.0, 6.0])                  # median reaches 5 km at estimate 3, 20 at 4, 50 at 5, 100 at 6
        self.assertEqual(creo.calibrate_cuts(est, [min(a, 40) for a in act], k=11)[2:], [None, None])  # the median never reaches 50 or 100
        self.assertTrue(np.all(np.diff(creo._pav([3, 1, 2, 5, 4])) >= 0))

    def test_a_factor_has_one_net_effect(self):
        c = {"wind850_speed": 0.30, "wind700_speed": -0.12, "cloud_base_asl": -0.3, "xc_pct": 3.0, "doy": 2.0, "free": 1.0, "rule_w850": 0.0, "temp": 0.01}
        rs = creo.reasons(c, self.F); self.assertEqual([r[0] for r in rs], ["height", "wind aloft"])               # xc_pct, doy and free are never reasons
        wind = [r for r in rs if r[0] == "wind aloft"][0]; self.assertTrue(wind[1]); self.assertAlmostEqual(wind[3], 0.18)   # 850 and 700 hPa added up: net helps, mentioned once
        self.assertIn("1,500 m", wind[2])                                                                           # described by the piece that pushed the same way as the net effect
        both = creo.reasons({"wind850_speed": 0.2, "wind700_speed": -0.5}, self.F); self.assertEqual(len(both), 1); self.assertFalse(both[0][1]); self.assertIn("3,000 m", both[0][2])
        self.assertEqual([r[0] for r in creo.reasons({"wind10_speed": 0.2, "gust": -0.2, "wind10_cos": 0.01}, self.F)], [])         # speed, direction and gusts cancel into one small net effect
        self.assertEqual(creo.reasons({"gust": 0.02, "rain": -0.05}, self.F), [])                                   # too small to mention

    def test_the_sentence_has_the_real_values(self):
        t = creo.describe(creo.reasons({"wind850_speed": 0.3, "cloud_base_asl": -0.4}, self.F)); self.assertEqual(t, "A westerly wind aloft (14 km/h at 1,500 m) helps, but the low cloud base (about 1,900 m) will limit height.")
        self.assertEqual(creo.describe(creo.reasons({"gust": -0.3}, self.F)), "Strong gusts (up to about 18 km/h) hold the day back.")
        self.assertEqual(creo.describe(creo.reasons({"cloud_base_asl": 0.3, "wind10_sin": 0.2}, dict(self.F, wind10_sin=1.0, wind10_cos=0.0))), "A high cloud base (about 1,900 m) and an easterly wind at launch help.")
        self.assertEqual(creo.describe(creo.reasons({"rule_thermals": 0.3}, self.F, {"thermal_ms": 1.4})), "Decent thermals (about 1.4 m/s) help.")
        self.assertEqual(creo.describe([]), "Nothing stands out: close to an average day.")

    def test_no_go_leads_with_the_deal_breaker(self):
        t = creo.describe(creo.reasons({"cloud_base_asl": 0.3}, self.F), nogo_lead="Rain 3.2 mm in the window"); self.assertTrue(t.startswith("Rain 3.2 mm in the window rules the day out."))
        self.assertIn("Otherwise a high cloud base", t); self.assertEqual(creo.describe([], nogo_lead="Strong S wind aloft"), "Strong S wind aloft rules the day out.")


class Walk(unittest.TestCase):
    def test_week_blocks_and_cluster_bootstrap(self):
        self.assertEqual(creo.week_start("2025-12-01", "2025-12-01"), "2025-12-01"); self.assertEqual(creo.week_start("2025-12-07", "2025-12-01"), "2025-12-01")
        self.assertEqual(creo.week_start("2025-12-08", "2025-12-01"), "2025-12-08"); self.assertEqual(creo.week_start("2026-01-03", "2025-12-01"), "2025-12-29")
        rng = np.random.default_rng(0); truth = np.tile(np.arange(40.0), 2); good = truth + rng.normal(0, 3, 80); bad = rng.normal(0, 30, 80); days = np.tile(np.arange(40), 2)
        d, lo, hi = creo.cluster_bootstrap_diff(days, truth, good, bad, n=200); self.assertGreater(d, 0); self.assertGreater(lo, 0)


class Shadow(unittest.TestCase):
    def test_predict_day_builds_the_row_and_applies_the_hard_limit(self):
        try: import lightgbm as lgb
        except ImportError: self.skipTest("lightgbm not installed")
        names = ["wind10_speed", "xc_pct", "doy", "free"]; rng = np.random.default_rng(0); X = rng.uniform(0, 30, (200, 4))
        b = lgb.train(dict(objective="regression", num_leaves=4, verbosity=-1, seed=0), lgb.Dataset(X, np.log1p(X[:, 0]), feature_name=names), 20)
        row = creo.feature_row({"wind10_speed": 7.0, "xc_pct": 55.0}, names, "2026-12-01"); self.assertEqual(row[:2], [7.0, 55.0]); self.assertEqual(row[2], 335); self.assertEqual(row[3], 1.0)   # free-day flag always 1
        pm = [{"xc_pct": 60, "wind10_speed": 8.0, "wind10_u": 1.0, "wind10_v": 1.0}, {"xc_pct": 50, "wind10_speed": 6.0, "wind10_u": 1.0, "wind10_v": 1.0}]
        p = creo.predict_day(b, pm, "2026-12-01", published_xc=55); self.assertFalse(p["no_go"]); self.assertEqual(p["creo_km"], p["creo_raw_km"]); self.assertEqual(p["xc_feature"], 55)
        z = creo.predict_day(b, pm, "2026-12-01", published_xc=0); self.assertTrue(z["no_go"]); self.assertEqual(z["creo_km"], 0.0); self.assertGreaterEqual(z["creo_raw_km"], 0.0)
