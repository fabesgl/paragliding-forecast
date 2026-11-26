"""Run: .venv/bin/python -m unittest tests.test_scoreboard -v   (pure maths, no data files)"""
import sys, unittest
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import scoreboard as SB


class T(unittest.TestCase):
    def test_band_edges(self):
        self.assertEqual([SB.band_of(x) for x in (0, 19.9, 20, 79, 80, 100)], ["0-20", "0-20", "20-40", "60-80", "80-100", "80-100"])
        self.assertIsNone(SB.band_of(None)); self.assertIsNone(SB.band_of(float("nan")))

    def test_spearman(self):
        self.assertAlmostEqual(SB.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]), 1.0)
        self.assertAlmostEqual(SB.spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]), -1.0)
        self.assertIsNone(SB.spearman([1, 2, 3], [1, 2, 3]))                  # too few days
        self.assertIsNone(SB.spearman([1, 2, 3, 4, 5], [7, 7, 7, 7, 7]))      # one side never varies

    def test_labels_skip_unknown_and_tandems(self):
        flights = [("2024-01-01", "A", 50.0), ("2024-01-01", "B", 10.0), ("2024-01-01", "T", 99.0), ("2024-01-04", "T", 5.0)]
        status = {"2024-01-01": "flew", "2024-01-02": "no_flights", "2024-01-03": "unknown", "2024-01-04": "flew", "2024-01-05": "unknown"}
        lab = SB.build_day_labels(flights, ["2024-01-05"], status, {"2024-01"}, "2024-01-01", "2024-01-05").set_index("date")
        self.assertEqual(lab.loc["2024-01-01", "best_km"], 50.0); self.assertEqual(lab.loc["2024-01-01", "n_flights"], 2)   # tandem not counted
        self.assertEqual(lab.loc["2024-01-01", "median_km"], 30.0)
        self.assertFalse(lab.loc["2024-01-02", "flown"]); self.assertEqual(lab.loc["2024-01-02", "best_km"], 0.0)
        self.assertNotIn("2024-01-03", lab.index)                                  # unknown: skipped
        self.assertFalse(lab.loc["2024-01-04", "flown"])                           # tandem-only day in a complete month
        self.assertTrue(lab.loc["2024-01-05", "flown"]); self.assertTrue(np.isnan(lab.loc["2024-01-05", "best_km"]))    # unknown day rescued by a track
        lab2 = SB.build_day_labels(flights, [], status, set(), "2024-01-04", "2024-01-04")
        self.assertEqual(len(lab2), 0)                                             # tandem-only day in an incomplete month is unknown

    def test_free_days_and_events(self):
        self.assertTrue(SB.is_free_day("2024-02-03", [])); self.assertFalse(SB.is_free_day("2024-02-05", []))     # Sat / Mon
        self.assertTrue(SB.is_free_day("2024-02-05", ["2024-02-05"]))
        ev = [{"start": "2024-02-10", "end": "2024-02-16"}]
        self.assertTrue(SB.in_events("2024-02-16", ev)); self.assertFalse(SB.in_events("2024-02-17", ev))

    def test_band_table_and_misses(self):
        df = pd.DataFrame(dict(date=list("abcdef"), xc=[10, 90, 95, 30, 85, 5], flown=[False, True, False, True, True, True],
                               best_km=[0.0, 60.0, 0.0, 120.0, 10.0, 2.0], free=[False, False, True, False, False, False]))
        bt = SB.band_table(df, "xc").set_index("band")
        self.assertEqual(bt.loc["80-100%", "days"], 3); self.assertAlmostEqual(bt.loc["80-100%", "pct_flown"], 200 / 3)
        self.assertEqual(bt.loc["80-100%", "median_best_km"], 35.0)                # flown days only: 60 and 10
        fa, sp = SB.misses(df, "xc")
        self.assertEqual(fa["date"].tolist()[:2], ["c", "e"])                      # highest % first; 'b' flew 60 km so is no miss
        self.assertEqual(sp["date"].tolist(), ["d"])                               # 30% and 120 km; the 5% day flew only 2 km: not a big flight
    def test_acro_status(self):
        self.assertFalse(SB.acro_status(["flown", "thermals"], [("live",), ("past",)])[0])
        self.assertTrue(SB.acro_status(["flown", "acro_ok"], [("live",)])[0])


if __name__ == "__main__": unittest.main()
