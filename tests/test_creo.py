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
