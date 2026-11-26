"""Run: .venv/bin/python -m unittest tests.test_weighting_review -v"""
import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
import weighting_review as W

def days_of(n, f_equal, f_weighted, obs=lambda i: 10.0 + i):
    """one station, n days, 6 hours each: forecasts as functions of the observation"""
    return {"st": {f"2026-12-{d + 1:02d}": [(f_equal(obs(d * 6 + h)), f_weighted(obs(d * 6 + h)), obs(d * 6 + h)) for h in range(6)] for d in range(n)}}

class T(unittest.TestCase):
    def test_stat_ignores_steady_bias(self):
        self.assertAlmostEqual(W.stat([(o + 3, o) for o in range(10, 20)], "add"), 0.0)
        self.assertAlmostEqual(W.stat([(o * 0.5, o) for o in range(10, 20)], "speed"), 0.0)
        self.assertIsNone(W.stat([(1, 1)], "add"))

    def test_weighted_better_is_helped(self):
        noise = [1, -1, 2, -2, 1, -1]
        by_day = days_of(40, lambda o: o + noise[int(o) % 6] * 3, lambda o: o + noise[int(o) % 6])
        days = sorted(by_day["st"]); imp = W.improvement(by_day, days, "add")
        self.assertGreater(imp, 0.5)
        res = W.review({("temperature_2m", "1-2 days"): by_day})
        self.assertEqual(res["temperature_2m"]["1-2 days"]["verdict"], "helped")

    def test_identical_blends_are_no_clear_difference(self):
        noise = [1, -1, 2, -2, 1, -1]
        by_day = days_of(40, lambda o: o + noise[int(o) % 6], lambda o: o + noise[int(o) % 6])
        self.assertEqual(W.review({("temperature_2m", "1-2 days"): by_day})["temperature_2m"]["1-2 days"]["verdict"], "no clear difference")

    def test_worse_is_hurt_and_few_days_is_not_enough(self):
        noise = [1, -1, 2, -2, 1, -1]
        by_day = days_of(40, lambda o: o + noise[int(o) % 6], lambda o: o + 3 * noise[int(o) % 6])
        self.assertEqual(W.review({("temperature_2m", "1-2 days"): by_day})["temperature_2m"]["1-2 days"]["verdict"], "hurt")
        few = days_of(3, lambda o: o + noise[int(o) % 6], lambda o: o + 3 * noise[int(o) % 6])
        self.assertEqual(W.review({("temperature_2m", "1-2 days"): few})["temperature_2m"]["1-2 days"]["verdict"], "not enough data")

if __name__ == "__main__": unittest.main()
