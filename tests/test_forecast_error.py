"""Run: .venv/bin/python -m unittest tests.test_forecast_error -v   (pure maths, no data files)"""
import sys, unittest
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import forecast_error as FE

class T(unittest.TestCase):
    def test_dew_point(self):
        self.assertAlmostEqual(float(FE.dew_point(20.0, 100.0)), 20.0, 1)
        self.assertAlmostEqual(float(FE.dew_point(25.0, 50.0)), 13.9, 1)

    def test_cloud_base(self):
        self.assertEqual(float(FE.cloud_base_agl(20, 10)), 1250.0)
        self.assertEqual(float(FE.cloud_base_agl(10, 12)), 0.0)          # saturated / dew point above temperature: clipped to 0

    def test_wind_direction_is_where_it_comes_from(self):
        sp, d = FE.uv_to_speed_dir(np.array([0.0, -5.0, 5.0]), np.array([-5.0, 0.0, 0.0]))   # u east, v north
        self.assertTrue(np.allclose(sp, 5)); self.assertTrue(np.allclose(d, [0, 90, 270]))   # southward wind = from N; westward = from E

    def test_circular_error_wraps(self):
        self.assertAlmostEqual(float(FE.circ_diff(350, 10)), -20.0); self.assertAlmostEqual(float(FE.circ_diff(10, 350)), 20.0)

    def test_sector(self):
        self.assertEqual(list(FE.sector_of([0, 350, 44, 46, 315, np.nan])), ["N", "N", "NE", "NE", "NW", None])

    def test_bilinear_recovers_a_plane(self):
        lats = np.array([-37.0, -36.9, -36.8]); lons = np.array([146.0, 146.1, 146.2])
        plane = 2 * lats[:, None] + 3 * lons[None, :]
        field = np.stack([plane, plane + 1])
        out = FE.bilinear(lats, lons, field, -36.87, 146.13)
        self.assertTrue(np.allclose(out, [2 * -36.87 + 3 * 146.13, 2 * -36.87 + 3 * 146.13 + 1]))
        with self.assertRaises(ValueError): FE.bilinear(lats, lons, field, -35.0, 146.1)

    def test_height_correction(self):
        self.assertAlmostEqual(FE.height_correction_c(786, 588), -1.287)      # BARRA terrain lower -> its air is warmer -> subtract
        self.assertAlmostEqual(FE.height_correction_c(313, 549), 1.534)       # BARRA terrain higher -> add
        self.assertEqual(FE.height_correction_c(500, 500), 0.0)

    def test_direction_error_ignores_light_wind(self):
        e = FE.error_columns([10, 10], [350, 350], "dir", bm_speed=[20, 2])
        self.assertAlmostEqual(e[0], 20.0); self.assertTrue(np.isnan(e[1]))

    def test_summarise_sign_and_ratio(self):
        g = pd.DataFrame({"fc": [12.0, 24.0, 36.0], "bm": [10.0, 20.0, 30.0], "date": ["a", "a", "b"]})
        g["err"] = FE.error_columns(g["fc"], g["bm"], "speed"); s = FE.summarise(g)
        self.assertAlmostEqual(s["bias"], 4.0); self.assertAlmostEqual(s["ratio"], 1.2); self.assertEqual(s["n_days"], 2)
        self.assertAlmostEqual(FE.prior_correction("speed", s), 1 / 1.2)      # BARRA / forecast
        self.assertAlmostEqual(FE.prior_correction("add", s), -4.0)
        self.assertIsNone(FE.prior_correction("dir", s))

    def test_compass_and_corrected_rmse(self):
        self.assertEqual(FE.compass_to_deg("NNW"), 337.5); self.assertTrue(np.isnan(FE.compass_to_deg("CALM")))
        truth = np.array([10.0, 20.0, 30.0, 40.0])
        self.assertAlmostEqual(FE.corrected_rmse(truth + 3, truth, "add"), 0.0)           # a steady +3 is fully removable
        self.assertAlmostEqual(FE.corrected_rmse(truth * 0.5, truth, "speed"), 0.0)       # a steady x0.5 is fully removable
        self.assertGreater(FE.corrected_rmse(truth + np.array([5, -5, 5, -5.0]), truth, "add"), 4)

    def test_summarise_empty(self):
        self.assertIsNone(FE.summarise(pd.DataFrame({"fc": [], "bm": [], "err": [], "date": []})))

if __name__ == "__main__": unittest.main()
