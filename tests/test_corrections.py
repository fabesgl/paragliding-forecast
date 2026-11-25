"""Run: .venv/bin/python -m unittest tests.test_corrections -v   (synthetic forecasts and observations)"""
import csv, datetime as dt, gzip, json, random, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
from pgforecast import corrections as C
import update_corrections as U

def day_samples(rng, bias_by_sector, dirs=("N", "S"), noise=0.8):
    """9 hours: truth is random; the forecast = truth - bias for the hour's wind-direction sector."""
    out = []
    for h in range(9, 18):
        deg = {"N": 0, "S": 180, "NE": 45}[rng.choice(dirs)]
        truth = 15 + rng.gauss(0, 3); b = bias_by_sector.get(C.sector(deg), 0.0)
        out.append(dict(dir=deg, fc={"temperature_2m": truth - b + rng.gauss(0, noise)}, ob={"temperature_2m": truth}))
    return out

class T(unittest.TestCase):
    def run_days(self, n, bias, seed=1, dirs=("N", "S")):
        rng = random.Random(seed); st = C.new_state()
        for _ in range(n): C.update_day(st, "gfs", "pt", day_samples(rng, bias, dirs))
        return st

    def test_nothing_before_30_days_and_validation_period(self):
        st = self.run_days(29, {"N": 1.5, "S": 1.5, "NE": 1.5})
        b = st["bins"]["gfs|pt|temperature_2m|all"]; self.assertEqual(C.status(b), "learning")
        self.assertIsNone(C.correction_for(st, "gfs", "pt", "temperature_2m", 0))
        st = self.run_days(35, {"N": 1.5, "S": 1.5})
        self.assertEqual(C.status(st["bins"]["gfs|pt|temperature_2m|all"]), "validating")       # 30 days of data, only 5 days checked
        self.assertIsNone(C.correction_for(st, "gfs", "pt", "temperature_2m", 0))

    def test_real_bias_becomes_active_and_improves(self):
        st = self.run_days(60, {"N": 1.5, "S": 1.5}); b = st["bins"]["gfs|pt|temperature_2m|all"]
        self.assertEqual(C.status(b), "active"); self.assertGreater(C.gain(b), 0.3); self.assertAlmostEqual(b["corr"], 1.5, delta=0.3)
        h = {"temperature_2m_gfs": [10.0, 20.0], "wind_direction_10m_gfs": [0, 180]}
        C.apply_hourly(st, "gfs", "pt", h); self.assertAlmostEqual(h["temperature_2m_gfs"][0], 11.5, delta=0.3)

    def test_noise_only_is_not_applied(self):
        for seed in range(5):
            st = self.run_days(90, {}, seed=seed); b = st["bins"]["gfs|pt|temperature_2m|all"]
            self.assertNotEqual(C.status(b), "active", (seed, C.gain(b)))
            h = {"temperature_2m_gfs": [10.0], "wind_direction_10m_gfs": [0]}; C.apply_hourly(st, "gfs", "pt", h); self.assertEqual(h["temperature_2m_gfs"], [10.0])

    def test_direction_specific_bias(self):
        st = self.run_days(120, {"N": 2.0}, seed=3)                                            # biased only when the wind is northerly
        h = {"temperature_2m_gfs": [10.0, 10.0], "wind_direction_10m_gfs": [0, 180]}; C.apply_hourly(st, "gfs", "pt", h)
        self.assertAlmostEqual(h["temperature_2m_gfs"][0], 12.0, delta=0.5)                    # northerly hour corrected
        self.assertAlmostEqual(h["temperature_2m_gfs"][1], 10.0, delta=0.01)                   # southerly hour left alone: its sector proved no gain

    def test_bias_that_disappears_switches_off(self):
        rng = random.Random(7); st = C.new_state()
        for _ in range(60): C.update_day(st, "gfs", "pt", day_samples(rng, {"N": 1.5, "S": 1.5}))
        self.assertEqual(C.status(st["bins"]["gfs|pt|temperature_2m|all"]), "active")
        for _ in range(150): C.update_day(st, "gfs", "pt", day_samples(rng, {}))              # model got fixed: the old correction now hurts
        self.assertNotEqual(C.status(st["bins"]["gfs|pt|temperature_2m|all"]), "active")

    def test_wind_is_a_multiplier_and_dew_point_capped(self):
        rng = random.Random(2); st = C.new_state()
        for _ in range(60):
            s = []
            for h in range(9, 18):
                truth = 20 + rng.gauss(0, 4); s.append(dict(dir=0, fc={"wind_speed_10m": truth * 0.7 + rng.gauss(0, 1)}, ob={"wind_speed_10m": truth}))
            C.update_day(st, "gfs", "pt", s)
        self.assertEqual(C.status(st["bins"]["gfs|pt|wind_speed_10m|all"]), "active")
        h = {"wind_speed_10m_gfs": [10.0], "wind_direction_10m_gfs": [0], "temperature_2m_gfs": [10.0], "dew_point_2m_gfs": [12.0]}
        C.apply_hourly(st, "gfs", "pt", h); self.assertAlmostEqual(h["wind_speed_10m_gfs"][0], 14.3, delta=1.0); self.assertEqual(h["dew_point_2m_gfs"][0], 10.0)

    def test_files_pipeline_is_idempotent_and_ignores_unfinished_days(self):
        d = Path(tempfile.mkdtemp()); (d / "arch").mkdir(); (d / "obs").mkdir(); rng = random.Random(5)
        tz = dt.timezone(dt.timedelta(hours=11)); import zoneinfo; tzm = zoneinfo.ZoneInfo("Australia/Melbourne")
        cfg = {"timezone": "Australia/Melbourne", "points": {"falls_creek": {"bom_station": 1}, "mystic_launch": {}}}
        start = dt.date(2026, 6, 1); rows = []
        for k in range(45):
            D = start + dt.timedelta(days=k); times, fc_t = [], []
            for h in range(24):
                lt = dt.datetime(D.year, D.month, D.day, h, tzinfo=tzm); times.append(lt.strftime("%Y-%m-%dT%H:%M"))
                truth = 12 + rng.gauss(0, 2); fc_t.append(truth - 1.0)                          # forecast 1 degree too cold
                u = lt.astimezone(dt.UTC)
                for m in (0, 30): rows.append([(u + dt.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:00Z"), truth])
            made = dt.datetime(D.year, D.month, D.day, 19, 45, tzinfo=tzm) - dt.timedelta(days=1)       # evening run the day before
            hourly = {"time": times, "temperature_2m_gfs": fc_t, "wind_direction_10m_gfs": [0] * 24}
            j = {"fetched_utc": made.astimezone(dt.UTC).isoformat(), "points": ["falls_creek", "mystic_launch"], "response": [{"hourly": hourly}, {"hourly": hourly}]}
            with gzip.open(d / "arch" / f"{made.astimezone(dt.UTC):%Y-%m-%dT%H%MZ}.json.gz", "wt") as fh: json.dump(j, fh)
        with open(d / "obs" / "1.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["obs_time_utc", "air_temp"]); w.writerows(sorted(rows))
        today = start + dt.timedelta(days=44)
        st1, n1 = U.run(d / "arch", d / "obs", d / "c.json", cfg, today=today, models=("gfs",))
        self.assertEqual(n1, 44); self.assertTrue(all("mystic_launch" not in k for k in st1["bins"]))     # no station -> never corrected
        st2, n2 = U.run(d / "arch", d / "obs", d / "c.json", cfg, today=today, models=("gfs",))
        self.assertEqual(n2, 0); self.assertEqual(st1["bins"], st2["bins"])                                # nothing counted twice
        b = st1["bins"]["gfs|falls_creek|temperature_2m|all"]
        self.assertEqual(b["n_days"], 44); self.assertAlmostEqual(b["corr"], 1.0, delta=0.4); self.assertEqual(C.status(b), "active")

if __name__ == "__main__": unittest.main()
