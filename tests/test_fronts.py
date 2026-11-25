import unittest, datetime as dt
from pgforecast import fronts as F
from pgforecast.rules_config import load_rules
CFG = load_rules("bright")["fronts"]

def synthetic(front_hour=40, n=72):
    """Three days of hourly data: daily temperature cycle, NW wind and falling pressure, then a cold front at front_hour (pressure low, wind to SW, temperature -6 C)."""
    t0 = dt.datetime(2026, 11, 25); times = [(t0 + dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(n)]
    p, T, d = [], [], []
    for i in range(n):
        post = i >= front_hour
        p.append(1010 - 0.2 * min(i, front_hour) + (0.5 * (i - front_hour) if post else 0))
        T.append(12 + 3 * ((i % 24) in range(10, 18)) - (6 if post else 0)); d.append(300 if not post else 225)
    return times, dict(p=p, t=T, d=d)

class FrontTests(unittest.TestCase):
    def test_cold_front_found_with_all_signals(self):
        times, s = synthetic(); f = F.detect(times, s, CFG)
        self.assertEqual(len(f), 1); self.assertEqual(f[0]["kind"], "cold"); self.assertEqual(f[0]["strength"], "clear")
        self.assertAlmostEqual(dt.datetime.fromisoformat(f[0]["time"]).hour + 24 * dt.datetime.fromisoformat(f[0]["time"]).day, 40 + 24 * 25, delta=3)
    def test_daily_cycle_alone_is_not_a_front(self):
        times, s = synthetic(front_hour=10 ** 6); self.assertEqual(F.detect(times, s, CFG), [])
    def test_day_labels(self):
        times, s = synthetic(); f = F.detect(times, s, CFG)           # front on 26 Nov afternoon-ish
        day = f[0]["time"][:10]; self.assertEqual(F.label_day(f, day, CFG)["status"], "passing")
        self.assertIn("Cold front ~", F.label_day(f, day, CFG)["text"])
        self.assertEqual(F.label_day(f, "2026-11-25", CFG)["status"], "pre"); self.assertEqual(F.label_day(f, "2026-11-27", CFG)["status"], "post")
        self.assertIsNone(F.label_day([], day, CFG)["status"])
    def test_fly_early_only_for_afternoon_fronts(self):
        self.assertEqual(F.advice("cold", 16), "fly early"); self.assertNotEqual(F.advice("cold", 9), "fly early")
if __name__ == "__main__": unittest.main()
